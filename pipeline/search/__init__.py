"""Face -> web search backends.

Three engines, all genuinely queried at run time. Two are free; the third
is a paid true-face-recognition service.

  yandex     Reverse *image* search (CBIR). Finds pages containing a
             visually matching image. Free. The default.
  bing       Visual search. Resolves the face to a named entity and returns
             pages about that person. Free. Broader, less precise.
  facecheck  True face search: finds a person even in photos that appear
             nowhere else. Paid (~$0.50/search), requires an API token.

The distinction between the first two and the third matters. Yandex and Bing
answer "have I seen this IMAGE before?", so they find a private individual
only if one of their photos has been reposted somewhere. FaceCheck answers
"have I seen this FACE before?" against a face-indexed crawl of social media,
so it finds people whose photos exist in exactly one place.

In practice: the free engines handle public figures well and ordinary people
poorly. There is no free face-search API that returns source URLs -- every
such service gives away the match and sells the URL.

No engine's verdict is trusted. Everything any of them returns is re-checked
against our own ArcFace embeddings in `pipeline.verify`.
"""

from .base import SOCIAL_DOMAINS, Candidate, SearchBackend, classify_domain, dedupe

__all__ = [
    "Candidate",
    "SearchBackend",
    "classify_domain",
    "dedupe",
    "SOCIAL_DOMAINS",
    "get_backend",
    "BACKENDS",
]

BACKENDS = ("yandex", "bing", "both", "facecheck")


def get_backend(name: str, **kw):
    """Resolve a backend by name.

    Imported lazily so a missing optional dependency (Playwright, say) only
    breaks the backend that actually needs it.
    """
    name = name.lower()
    if name == "yandex":
        from .yandex import YandexBackend

        return YandexBackend(**kw)
    if name == "bing":
        from .bing import BingBackend

        return BingBackend(**kw)
    if name == "facecheck":
        from .facecheck import FaceCheckBackend

        # The browser backends take `headless`; FaceCheck is pure HTTP.
        return FaceCheckBackend(**{k: v for k, v in kw.items() if k != "headless"})
    if name == "both":
        return MultiBackend(**kw)
    raise ValueError(f"unknown backend {name!r}; choose from {', '.join(BACKENDS)}")


class MultiBackend:
    """Run every engine and merge their candidates.

    Used when one engine alone finds nothing verifiable -- more independent
    sources means a better chance of landing a post we can actually confirm.
    """

    name = "yandex+bing"

    def __init__(self, **kw):
        self.kw = kw
        self.last_hosted_url = None
        self.entity_name = None

    def search_by_url(self, image_url: str, limit: int = 60) -> list[Candidate]:
        merged: list[Candidate] = []
        errors = []
        for engine in ("yandex", "bing"):
            b = get_backend(engine, **self.kw)
            try:
                merged.extend(b.search_by_url(image_url, limit=limit))
            except Exception as e:  # one engine failing must not sink the run
                errors.append(f"{engine}: {type(e).__name__}: {str(e)[:120]}")
            self.entity_name = self.entity_name or getattr(b, "entity_name", None)
        if not merged and errors:
            raise RuntimeError("all engines failed:\n  " + "\n  ".join(errors))
        self.last_errors = errors
        return dedupe(merged)

    def search(self, face_crop, limit: int = 60) -> list[Candidate]:
        from ..imagehost import publish

        hosted = publish(face_crop)
        self.last_hosted_url = hosted.url
        return self.search_by_url(hosted.url, limit=limit)
