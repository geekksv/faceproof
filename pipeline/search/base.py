"""Shared types for face->web search backends.

Every backend answers the same question: *given a cropped face, which public
web pages show this person?* Backends differ only in how they ask the web.
They deliberately do NOT decide whether a hit is a real match -- that is
`pipeline.verify`'s job, using our own embeddings. A backend that returned a
hardcoded URL would simply fail verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Domains we treat as "social media" for the task's matching-post requirement.
SOCIAL_DOMAINS = {
    "instagram.com": "Instagram",
    "x.com": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "tiktok.com": "TikTok",
    "linkedin.com": "LinkedIn",
    "reddit.com": "Reddit",
    "bsky.app": "Bluesky",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "vk.com": "VK",
    "ok.ru": "Odnoklassniki",
    "pinterest.com": "Pinterest",
    "youtube.com": "YouTube",
    "tumblr.com": "Tumblr",
    "weibo.com": "Weibo",
    "mastodon.social": "Mastodon",
    "flickr.com": "Flickr",
    "imdb.com": "IMDb",
}


def classify_domain(url: str) -> tuple[str | None, str]:
    """Return (platform_name_or_None, registrable_host) for a URL."""
    m = re.match(r"https?://([^/]+)", url or "", re.I)
    host = (m.group(1) if m else "").lower().removeprefix("www.")
    for domain, platform in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return platform, host
    return None, host


@dataclass
class Candidate:
    """One search hit, before we have verified the face actually matches."""

    page_url: str                 # the post / page the engine found
    image_url: str | None = None  # direct link to the matched image, if known
    title: str = ""
    engine: str = ""
    engine_rank: int = 0
    engine_score: float | None = None  # only some engines report one

    @property
    def platform(self) -> str | None:
        return classify_domain(self.page_url)[0]

    @property
    def host(self) -> str:
        return classify_domain(self.page_url)[1]

    @property
    def is_social(self) -> bool:
        return self.platform is not None

    def to_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "image_url": self.image_url,
            "title": self.title,
            "engine": self.engine,
            "engine_rank": self.engine_rank,
            "engine_score": self.engine_score,
            "platform": self.platform,
            "host": self.host,
        }


class SearchBackend(Protocol):
    """Interface every search engine adapter implements."""

    name: str

    def search(self, face_crop: Path, limit: int = 30) -> list[Candidate]:
        """Search the web for `face_crop` and return unverified candidates."""
        ...


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Drop repeat page URLs, keeping the best-ranked sighting of each."""
    seen: dict[str, Candidate] = {}
    for c in candidates:
        key = c.page_url.split("?")[0].rstrip("/").lower()
        if key not in seen:
            seen[key] = c
    return list(seen.values())
