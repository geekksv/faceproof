"""FaceCheck.ID backend -- true face-recognition search.

This is a genuinely different kind of engine from Yandex, and the difference
matters more than it first appears:

  Yandex     "have I seen this IMAGE before?"  -- finds copies of a picture
  FaceCheck  "have I seen this FACE before?"   -- finds the person

Yandex only finds a private individual if one of their photos has been
reposted somewhere. FaceCheck indexed social profiles *by face*, so it finds
people whose photos exist in exactly one place. That is why Yandex handles
celebrities well and ordinary people badly.

Cost: 3 credits (~$0.50) per search, and FaceCheck moved to cryptocurrency-only
payment in late 2024. Without a token this backend raises a clear error rather
than silently returning nothing -- the free `demo` mode scans only 100,000
faces and returns results the docs themselves describe as not meaningful, so
it is exposed only behind an explicit flag.

API: https://facecheck.id/Face-Search/API
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import requests

from .base import Candidate, dedupe

BASE = "https://facecheck.id/api"
UPLOAD = f"{BASE}/upload_pic"
SEARCH = f"{BASE}/search"

# The search is asynchronous: upload, then poll until progress reaches 100.
POLL_INTERVAL_S = 2.0
MAX_POLLS = 90


class FaceCheckError(RuntimeError):
    pass


class FaceCheckBackend:
    """Face search via the FaceCheck.ID REST API."""

    name = "facecheck"

    def __init__(
        self,
        token: str | None = None,
        demo: bool | None = None,
        min_score: int = 0,
        headless: bool | None = None,  # accepted for interface parity; unused
    ):
        self.token = token or os.environ.get("FACECHECK_API_TOKEN")
        if demo is None:
            demo = os.environ.get("FACECHECK_DEMO", "0") == "1"
        self.demo = demo
        self.min_score = min_score

        if not self.token:
            raise FaceCheckError(
                "No FaceCheck.ID API token.\n"
                "  Set FACECHECK_API_TOKEN in .env (get one at "
                "https://facecheck.id/Face-Search/API).\n"
                "  A search costs 3 credits. To search for free instead, use "
                "--backend yandex."
            )

    # -- http ---------------------------------------------------------

    @property
    def _headers(self) -> dict:
        return {"accept": "application/json", "Authorization": self.token}

    def _upload(self, data: bytes, filename: str) -> str:
        r = requests.post(
            UPLOAD,
            headers=self._headers,
            files={"images": (filename, data)},
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise FaceCheckError(f"upload rejected: {body['error']} ({body.get('code')})")
        id_search = body.get("id_search")
        if not id_search:
            raise FaceCheckError(f"upload returned no id_search: {body}")
        return id_search

    def _poll(self, id_search: str) -> list[dict]:
        payload = {
            "id_search": id_search,
            "with_progress": True,
            "status_only": False,
            "demo": self.demo,
        }
        for _ in range(MAX_POLLS):
            r = requests.post(SEARCH, headers=self._headers, json=payload, timeout=120)
            r.raise_for_status()
            body = r.json()

            if body.get("error"):
                raise FaceCheckError(
                    f"search failed: {body['error']} ({body.get('code')})"
                )

            output = body.get("output")
            if output:
                return output.get("items", []) or []

            time.sleep(POLL_INTERVAL_S)

        raise FaceCheckError(
            f"search did not complete after {int(MAX_POLLS * POLL_INTERVAL_S)}s"
        )

    # -- public -------------------------------------------------------

    def _to_candidates(self, items: list[dict], limit: int) -> list[Candidate]:
        out: list[Candidate] = []
        for i, item in enumerate(items):
            url = item.get("url")
            if not url:
                continue
            score = item.get("score")
            if score is not None and score < self.min_score:
                continue

            # FaceCheck returns the matched thumbnail inline as a data URI.
            # Keeping the bytes lets verification run even when the source
            # host blocks scripted image fetches.
            thumb = None
            raw = item.get("base64") or ""
            if "," in raw:
                raw = raw.split(",", 1)[1]
            if raw:
                try:
                    thumb = base64.b64decode(raw)
                except Exception:
                    thumb = None

            out.append(
                Candidate(
                    page_url=url,
                    image_url=None,
                    title=(item.get("guid") or "")[:200],
                    engine=self.name,
                    engine_rank=i + 1,
                    engine_score=float(score) if score is not None else None,
                    image_bytes=thumb,
                )
            )
            if len(out) >= limit:
                break
        return dedupe(out)

    def search(self, face_crop: Path, limit: int = 60) -> list[Candidate]:
        """Search by uploading a local image. No public hosting required --
        unlike the browser backends, nothing about the face is published."""
        path = Path(face_crop)
        items = self._poll(self._upload(path.read_bytes(), path.name))
        return self._to_candidates(items, limit)

    def search_by_url(self, image_url: str, limit: int = 60) -> list[Candidate]:
        """Interface parity with the browser backends: fetch, then upload."""
        r = requests.get(image_url, timeout=60)
        r.raise_for_status()
        name = image_url.rsplit("/", 1)[-1].split("?")[0] or "face.jpg"
        items = self._poll(self._upload(r.content, name))
        return self._to_candidates(items, limit)
