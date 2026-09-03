"""Face -> web search backends.

Two engines, both free and both genuinely queried at run time:

  yandex  Reverse *image* search (CBIR). Returns pages that contain a
          visually matching image. The primary backend -- best face recall.
  bing    Visual search. Resolves the face to a named entity and returns
          pages about that person. Broader, less precise; a good fallback
          and a useful cross-check when Yandex is rate-limited.

Neither engine's verdict is trusted. Everything they return is re-checked
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

BACKENDS = ("yandex", "bing", "both")


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
