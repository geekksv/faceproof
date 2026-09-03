"""Bing Visual Search backend.

Bing behaves differently from Yandex: instead of a "pages containing this
image" list, its visual search resolves the face to an *entity* and redirects
to a normal web search for that name. That gives us two things Yandex does
not -- a human-readable identity label, and a broad set of pages about the
person -- at the cost of no direct image-to-page mapping.

Both are still funnelled through `pipeline.verify`, so the identity label is
never trusted on its own: a page only becomes a match if our own embedder
confirms the face.
"""

from __future__ import annotations

import base64
import contextlib
import re
import time
import urllib.parse
from pathlib import Path

from .base import Candidate, dedupe
from ._browser import browser_page, save_debug

VISUAL = (
    "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP"
    "&sbisrc=UrlPaste&q=imgurl:"
)

_SKIP_HOSTS = re.compile(r"bing\.com|microsoft|msn\.com|live\.com|go\.microsoft", re.I)


def _unwrap(href: str) -> str | None:
    """Resolve a Bing /ck/a? redirect to the destination it hides.

    Bing routes every organic result through a click tracker whose `u`
    parameter is the real URL, base64url-encoded behind an "a1" marker.
    Without decoding this, every result looks like it points at bing.com.
    """
    try:
        u = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("u", [""])[0]
    except Exception:
        return None
    if not u.startswith("a1"):
        return href if href.startswith("http") and not _SKIP_HOSTS.search(href) else None
    payload = u[2:]
    payload += "=" * (-len(payload) % 4)  # restore stripped base64 padding
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    except Exception:
        return None
    return decoded if decoded.startswith("http") else None


class BingBackend:
    """Visual search via Bing; also exposes the entity name Bing inferred."""

    name = "bing"

    def __init__(self, headless: bool | None = None, settle_s: float = 6.0):
        self.headless = headless
        self.settle_s = settle_s
        self.entity_name: str | None = None

    def _entity_from_url(self, url: str) -> str | None:
        """Bing redirects to /search?q=<name> once it recognises the face."""
        with contextlib.suppress(Exception):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [""])[0]
            q = q.strip()
            # Reject the passthrough case where q is still our image URL.
            if q and not q.lower().startswith(("imgurl:", "http")):
                return q
        return None

    def search_by_url(self, image_url: str, limit: int = 30) -> list[Candidate]:
        target = VISUAL + urllib.parse.quote(image_url, safe="")
        with browser_page(headless=self.headless) as page:
            page.goto(target, wait_until="domcontentloaded")
            time.sleep(self.settle_s)

            self.entity_name = self._entity_from_url(page.url)

            rows = page.eval_on_selector_all(
                "li.b_algo h2 a, li.b_algo a.tilk, #b_results li a[href*='/ck/a']",
                """els => els.map(e => ({
                    href: e.href,
                    text: (e.innerText || '').trim().slice(0, 160)
                }))""",
            )
            save_debug(page, "bing_results")

        out: list[Candidate] = []
        for i, r in enumerate(rows):
            dest = _unwrap(r["href"])
            if not dest or _SKIP_HOSTS.search(dest):
                continue
            out.append(
                Candidate(
                    page_url=dest,
                    title=r["text"][:200],
                    engine=self.name,
                    engine_rank=i + 1,
                )
            )
            if len(out) >= limit * 3:  # dedupe will thin this out
                break
        return dedupe(out)[:limit]

    def search(self, face_crop: Path, limit: int = 30) -> list[Candidate]:
        from ..imagehost import publish

        hosted = publish(face_crop)
        self.last_hosted_url = hosted.url
        return self.search_by_url(hosted.url, limit=limit)
