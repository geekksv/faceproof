"""Playwright helper shared by the reverse-image-search backends.

Reverse image engines have no free API, so we drive their real upload UI.
A visible (non-headless) browser is the default because headless Chrome is
fingerprinted and blocked far more aggressively -- and because the whole
point of the demo recording is to *show* the search happening.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    """Playwright or its browser binaries are not installed."""


@contextlib.contextmanager
def browser_page(headless: bool | None = None, timeout_ms: int = 45_000):
    """Yield a Playwright page with anti-bot-friendly defaults."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise BrowserUnavailable("playwright is not installed") from e

    if headless is None:
        headless = os.environ.get("HEADLESS", "0") == "1"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
        except Exception as e:
            raise BrowserUnavailable(
                "Chromium is not installed for Playwright. Run:\n"
                r"  .venv\Scripts\python.exe -m playwright install chromium"
            ) from e

        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        # Hide the most obvious automation tell before any page script runs.
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = ctx.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                ctx.close()
            with contextlib.suppress(Exception):
                browser.close()


def save_debug(page, tag: str, out_dir: str | Path = "out/debug") -> None:
    """Screenshot the current page so failures are diagnosable after the run."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        page.screenshot(path=str(d / f"{tag}.png"), full_page=False)
