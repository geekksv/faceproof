"""Face detection + embedding.

Wraps InsightFace's `buffalo_l` pack (SCRFD detector + ArcFace w600k_r50
recogniser). Everything runs locally on CPU via ONNX Runtime -- no API keys,
no per-call cost, and the same 512-d embedding space is used for both the
query face and every candidate face pulled off the web, so similarity scores
are directly comparable.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# scikit-image deprecation raised from inside InsightFace's alignment code.
# Nothing we can act on from here, and it fires once per detected face.
warnings.filterwarnings(
    "ignore", category=FutureWarning, module="insightface.utils.face_align"
)

# InsightFace chatters on stdout at import/prepare time; keep the CLI readable.
_MODEL_PACK = os.environ.get("FACE_MODEL_PACK", "buffalo_l")
_DET_SIZE = (640, 640)


@dataclass
class Face:
    """One detected face and its normalised ArcFace embedding."""

    bbox: tuple[int, int, int, int]          # x1, y1, x2, y2
    det_score: float
    embedding: np.ndarray = field(repr=False)  # L2-normalised, 512-d
    landmarks: np.ndarray | None = field(default=None, repr=False)

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)


class NoFaceFound(Exception):
    """Raised when an image the pipeline required a face in has none."""


@contextlib.contextmanager
def _quiet():
    """Silence the C-level model-loading banner without losing real errors."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield


class FaceEncoder:
    """Lazily-initialised singleton around InsightFace's FaceAnalysis app."""

    _app = None

    @classmethod
    def _get_app(cls):
        if cls._app is None:
            from insightface.app import FaceAnalysis

            with _quiet():
                app = FaceAnalysis(
                    name=_MODEL_PACK,
                    providers=["CPUExecutionProvider"],
                    allowed_modules=["detection", "recognition"],
                )
                app.prepare(ctx_id=-1, det_size=_DET_SIZE)
            cls._app = app
        return cls._app

    # -- loading -----------------------------------------------------------

    @staticmethod
    def load_image(source: str | Path | bytes) -> np.ndarray:
        """Read an image path or raw bytes into a BGR numpy array."""
        if isinstance(source, (bytes, bytearray)):
            arr = np.frombuffer(source, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"image not found: {path}")
            # np.fromfile handles non-ASCII Windows paths that cv2.imread chokes on
            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("could not decode image (unsupported or corrupt format)")
        return img

    # -- core --------------------------------------------------------------

    @classmethod
    def _detect_array(cls, img: np.ndarray, offset: int = 0) -> list[Face]:
        """Run the detector over one array, shifting boxes back by `offset`."""
        faces = []
        for f in cls._get_app().get(img):
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            x1, y1, x2, y2 = (int(v) - offset for v in f.bbox)
            faces.append(
                Face(
                    bbox=(x1, y1, x2, y2),
                    det_score=float(f.det_score),
                    embedding=emb,
                    landmarks=getattr(f, "kps", None),
                )
            )
        faces.sort(key=lambda f: f.area, reverse=True)
        return faces

    @classmethod
    def detect(cls, source: str | Path | bytes) -> list[Face]:
        """Detect every face in an image, largest first.

        Falls back to a padded retry when the first pass finds nothing. SCRFD
        misses faces that fill the entire frame -- its anchor scales top out
        below a face that large -- and adding a border makes the face
        proportionally smaller without altering a single pixel of it.

        This is not an edge case: social media profile pictures are almost
        always extreme close-up crops, so without the retry the pipeline
        silently fails on exactly the images it most needs to check.
        """
        img = cls.load_image(source)
        faces = cls._detect_array(img)
        if faces:
            return faces

        pad = max(img.shape[0], img.shape[1]) // 2
        padded = cv2.copyMakeBorder(
            img, pad, pad, pad, pad, cv2.BORDER_REPLICATE
        )
        return cls._detect_array(padded, offset=pad)

    @classmethod
    def primary_face(cls, source: str | Path | bytes) -> Face:
        """The largest face in an image -- the subject of a face scan."""
        faces = cls.detect(source)
        if not faces:
            raise NoFaceFound("no face detected in the input image")
        return faces[0]

    # -- helpers -----------------------------------------------------------

    @classmethod
    def crop(
        cls,
        source: str | Path | bytes,
        face: Face,
        out_path: str | Path,
        margin: float = 0.35,
    ) -> Path:
        """Write a padded crop of `face` to disk; this is what gets searched.

        Reverse-image engines match a tight face crop far better than a full
        scene, because the background stops dominating the visual hash.
        """
        img = cls.load_image(source)
        h, w = img.shape[:2]
        x1, y1, x2, y2 = face.bbox
        pad_x, pad_y = int((x2 - x1) * margin), int((y2 - y1) * margin)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)

        crop = img[y1:y2, x1:x2]
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            raise ValueError("failed to encode face crop")
        buf.tofile(str(out_path))
        return out_path


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two ArcFace embeddings, in [-1, 1].

    Both vectors are already L2-normalised by InsightFace, so this is just a
    dot product -- but we re-normalise defensively in case a caller passes a
    raw embedding.
    """
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a / na, b / nb))


def sha256_file(path: str | Path) -> str:
    """SHA-256 of a file's bytes -- used to pin the exact image we scanned."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
