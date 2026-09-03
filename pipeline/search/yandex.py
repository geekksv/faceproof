"""Yandex Images reverse-image search backend.

Yandex is the strongest freely-scriptable reverse-image engine for *faces*:
its CBIR index reliably surfaces the same person across social profiles, and
it renders a plain "Sites containing this image" list that survives scraping.

There is no free API. Yandex's browser upload widget is bot-protected and
silently drops scripted `set_input_files` calls, but the `?url=` CBIR entry
point works reliably -- so `pipeline.imagehost` publishes the crop first and
we hand Yandex that link. `&cbir_page=sites` is the key: without it Yandex
renders ~4 teaser results, with it the full list (~60).
"""

from __future__ import annotations

import contextlib
import re
import time
import urllib.parse
from pathlib import Path

from .base import Candidate, dedupe
from ._browser import browser_page, save_debug

SEARCH = "https://yandex.com/images/search"

# Pull page URL, direct image URL, title and domain out of one result card.
_EXTRACT_JS = """els => els.map(e => {
    const info = e.querySelector('.CbirSites-ItemInfo') || e;
    const pageA = info.querySelector("a[href^='http']");
    const thumbA = e.querySelector("a.CbirSites-ItemThumb") ||
                   e.querySelector(".CbirSites-ItemThumb a") ||
                   e.querySelector("a[href^='http']");
    const el = s => e.querySelector(s);
    return {
        page_url:  pageA  ? pageA.href  : null,
        image_url: thumbA ? thumbA.href : null,
        title:  (el('.CbirSites-ItemTitle')       || {}).innerText || '',
        domain: (el('.CbirSites-ItemDomain')      || {}).innerText || '',
        desc:   (el('.CbirSites-ItemDescription') || {}).innerText || ''
    };
})"""


def _clean(url: str | None) -> str | None:
    """Strip Yandex's referral params so evidence URLs are canonical."""
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query)
        if not k.startswith("utm_")
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


class YandexBackend:
    """Reverse-image search via Yandex CBIR."""

    name = "yandex"

    def __init__(self, headless: bool | None = None, settle_s: float = 5.0):
        self.headless = headless
        self.settle_s = settle_s

    def search_by_url(self, image_url: str, limit: int = 60) -> list[Candidate]:
        """Run CBIR against a publicly reachable image URL."""
        target = (
            f"{SEARCH}?rpt=imageview&cbir_page=sites"
            f"&url={urllib.parse.quote(image_url, safe='')}"
        )
        with browser_page(headless=self.headless) as page:
            page.goto(target, wait_until="domcontentloaded")
            time.sleep(self.settle_s)

            if re.search(r"captcha|showcaptcha", page.url, re.I):
                save_debug(page, "yandex_captcha")
                raise RuntimeError(
                    "Yandex served a CAPTCHA. Re-run with HEADLESS=0 to solve it in "
                    "the visible window, or use --backend bing."
                )

            # The sites list lazy-loads; scroll until the count stops growing.
            last = -1
            for _ in range(8):
                n = page.locator(".CbirSites-Item").count()
                if n == last:
                    break
                last = n
                page.mouse.wheel(0, 5000)
                time.sleep(1.0)

            raw = page.eval_on_selector_all(".CbirSites-Item", _EXTRACT_JS)
            save_debug(page, "yandex_results")

        out: list[Candidate] = []
        for i, r in enumerate(raw):
            page_url = _clean(r.get("page_url"))
            if not page_url:
                continue
            title = (r.get("title") or r.get("desc") or "").strip().replace("\n", " ")
            out.append(
                Candidate(
                    page_url=page_url,
                    image_url=_clean(r.get("image_url")),
                    title=title[:200],
                    engine=self.name,
                    engine_rank=i + 1,
                )
            )
            if len(out) >= limit:
                break
        return dedupe(out)

    def search(self, face_crop: Path, limit: int = 60) -> list[Candidate]:
        """Publish the crop, then CBIR it. Used when no public URL exists."""
        from ..imagehost import publish

        hosted = publish(face_crop)
        self.last_hosted_url = hosted.url
        return self.search_by_url(hosted.url, limit=limit)
