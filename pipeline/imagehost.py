"""Publish a face crop to a temporary public URL.

Reverse-image engines accept an image either by browser upload or by URL.
Their upload widgets are heavily bot-protected, but the `?url=` entry point
is stable and scriptable -- so we put the crop somewhere publicly fetchable
first and hand the engine that link.

PRIVACY: this uploads the cropped face to a third-party anonymous host. That
is fine for the public-figure demo, and it is why `--no-upload` exists: if the
input image already lives at a public URL, pass `--image-url` and nothing new
is published. See the README's Privacy section.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
TIMEOUT = 60


@dataclass
class HostedImage:
    url: str
    host: str


class UploadFailed(RuntimeError):
    pass


def _catbox(path: Path) -> str:
    with open(path, "rb") as fh:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": fh},
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise UploadFailed(f"catbox returned: {url[:120]}")
    return url


def _uguu(path: Path) -> str:
    with open(path, "rb") as fh:
        r = requests.post(
            "https://uguu.se/upload?output=text",
            files={"files[]": fh},
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise UploadFailed(f"uguu returned: {url[:120]}")
    return url


def _tmpfiles(path: Path) -> str:
    with open(path, "rb") as fh:
        r = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": fh},
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
    r.raise_for_status()
    url = json.loads(r.text)["data"]["url"]
    # The page URL renders HTML; /dl/ serves the raw bytes engines need.
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


_HOSTS = [("catbox", _catbox), ("uguu", _uguu), ("tmpfiles", _tmpfiles)]


def publish(path: str | Path) -> HostedImage:
    """Upload `path` to the first anonymous host that accepts it."""
    path = Path(path)
    errors = []
    for name, fn in _HOSTS:
        try:
            url = fn(path)
        except Exception as e:  # noqa: BLE001 - try every host before giving up
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
            continue
        if _reachable(url):
            return HostedImage(url=url, host=name)
        errors.append(f"{name}: uploaded but URL not publicly readable")
    raise UploadFailed("all image hosts failed:\n  " + "\n  ".join(errors))


def _reachable(url: str) -> bool:
    """Confirm the engine will actually be able to fetch what we uploaded."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30, stream=True)
        ok = r.ok and r.headers.get("content-type", "").startswith("image")
        r.close()
        return ok
    except Exception:
        return False
