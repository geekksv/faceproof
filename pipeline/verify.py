"""Independently verify that a search hit really shows the query face.

This is the step that makes the pipeline honest. A search engine's own
ranking is a black box, and a hardcoded URL would sail straight through it.
So we ignore the engine's opinion entirely: we fetch the image the engine
pointed at, run OUR detector and OUR embedder over it, and accept the
candidate only if the ArcFace cosine similarity clears a threshold. Anything
below is discarded, however highly the engine ranked it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .face import FaceEncoder, cosine_similarity, sha256_bytes
from .search.base import Candidate

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# ArcFace (w600k_r50) cosine similarity. Same-person pairs typically land
# above ~0.5; unrelated faces sit near 0. 0.45 keeps recall without letting
# look-alikes through, and the actual score is always recorded in evidence.
DEFAULT_THRESHOLD = 0.45

MAX_BYTES = 12 * 1024 * 1024


@dataclass
class VerifiedMatch:
    """A candidate whose face we re-checked ourselves."""

    candidate: Candidate
    similarity: float
    matched_image_url: str
    image_sha256: str
    image_bytes: bytes = field(repr=False, default=b"")
    faces_in_image: int = 0
    local_path: Path | None = None

    @property
    def passed(self) -> bool:
        return self.similarity >= self._threshold

    _threshold: float = DEFAULT_THRESHOLD

    def to_dict(self) -> dict:
        return {
            **self.candidate.to_dict(),
            "similarity": round(self.similarity, 6),
            "matched_image_url": self.matched_image_url,
            "image_sha256": self.image_sha256,
            "faces_in_image": self.faces_in_image,
            "threshold": self._threshold,
            "passed": self.passed,
        }


def _fetch(url: str, referer: str | None = None) -> bytes | None:
    """Download bytes, refusing anything that is not a reasonable image."""
    headers = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    try:
        r = requests.get(url, headers=headers, timeout=30, stream=True)
        if not r.ok:
            return None
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image"):
            return None
        data = r.raw.read(MAX_BYTES + 1, decode_content=True)
        return data if 0 < len(data) <= MAX_BYTES else None
    except Exception:
        return None


def _og_image(page_url: str) -> str | None:
    """Pull the og:image off a post page when the engine gave us no image."""
    try:
        r = requests.get(
            page_url,
            headers={"User-Agent": UA, "Accept": "text/html,*/*"},
            timeout=25,
        )
        if not r.ok or "html" not in r.headers.get("content-type", ""):
            return None
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']'
            r'[^>]+content=["\']([^"\']+)["\']',
            r.text,
            re.I,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
            r'(?:property|name)=["\'](?:og:image|twitter:image)["\']',
            r.text,
            re.I,
        )
        return m.group(1) if m else None
    except Exception:
        return None


def verify_candidate(
    candidate: Candidate,
    query_embedding,
    threshold: float = DEFAULT_THRESHOLD,
    save_dir: str | Path | None = None,
) -> VerifiedMatch | None:
    """Re-derive the similarity between the query face and a candidate.

    Returns None when nothing usable could be fetched or no face was found --
    which is a normal outcome for a lot of search hits.
    """
    fetched: tuple[str, bytes] | None = None

    # An engine that handed back the thumbnail inline saves us a round trip,
    # and works on hosts that refuse to serve images to scripts.
    if candidate.image_bytes:
        fetched = (candidate.image_url or f"{candidate.engine}:inline", candidate.image_bytes)

    if fetched is None:
        for url in (u for u in (candidate.image_url,) if u):
            data = _fetch(url, referer=candidate.page_url)
            if data:
                fetched = (url, data)
                break

    if fetched is None:
        og = _og_image(candidate.page_url)
        if og:
            data = _fetch(og, referer=candidate.page_url)
            if data:
                fetched = (og, data)

    if fetched is None:
        return None

    url, data = fetched
    try:
        faces = FaceEncoder.detect(data)
    except Exception:
        return None
    if not faces:
        return None

    # A post can contain several people; the match is the best face in it.
    best = max(faces, key=lambda f: cosine_similarity(query_embedding, f.embedding))
    sim = cosine_similarity(query_embedding, best.embedding)

    local_path = None
    if save_dir:
        d = Path(save_dir)
        d.mkdir(parents=True, exist_ok=True)
        local_path = d / f"match_{sha256_bytes(data)[:16]}.jpg"
        local_path.write_bytes(data)

    return VerifiedMatch(
        candidate=candidate,
        similarity=sim,
        matched_image_url=url,
        image_sha256=sha256_bytes(data),
        image_bytes=data,
        faces_in_image=len(faces),
        local_path=local_path,
        _threshold=threshold,
    )


def verify_all(
    candidates: list[Candidate],
    query_embedding,
    threshold: float = DEFAULT_THRESHOLD,
    social_only: bool = True,
    max_checks: int = 25,
    save_dir: str | Path | None = None,
    on_result=None,
) -> list[VerifiedMatch]:
    """Verify candidates in engine-rank order; return passes, best first."""
    pool = [c for c in candidates if c.is_social] if social_only else list(candidates)
    pool.sort(key=lambda c: c.engine_rank)

    passed: list[VerifiedMatch] = []
    for c in pool[:max_checks]:
        m = verify_candidate(c, query_embedding, threshold, save_dir)
        if on_result:
            on_result(c, m)
        if m and m.passed:
            passed.append(m)
    passed.sort(key=lambda m: m.similarity, reverse=True)
    return passed
