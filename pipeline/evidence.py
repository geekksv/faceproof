"""Build and hash the evidence bundle that gets anchored on-chain.

The bundle is the pipeline's sworn statement: which image was scanned, which
engine was asked, which post came back, and what similarity our own model
measured. Hashing is done over a *canonical* serialisation -- sorted keys,
no insignificant whitespace, UTF-8 -- so that the same evidence always
produces the same digest on any machine, and any edit produces a different
one. That determinism is what makes later re-verification meaningful.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_utils import keccak

SCHEMA_VERSION = "faceproof/v1"


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, compact, UTF-8, no NaN."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def evidence_hash(bundle: dict) -> str:
    """keccak256 of the canonical bundle, as a 0x-prefixed hex string.

    keccak256 (not SHA-256) because that is what the EVM speaks natively --
    a Solidity verifier can recompute this from calldata.
    """
    return "0x" + keccak(canonical_json(bundle)).hex().removeprefix("0x")


def build_bundle(
    *,
    query_image_path: str | Path,
    query_image_sha256: str,
    face_bbox: tuple[int, int, int, int],
    face_det_score: float,
    face_crop_sha256: str,
    hosted_crop_url: str | None,
    engine: str,
    queries: list[dict],
    candidates_seen: int,
    social_candidates: int,
    match: dict,
    threshold: float,
    model: str = "insightface/buffalo_l (SCRFD + ArcFace w600k_r50)",
    searched_at: str | None = None,
) -> dict:
    """Assemble the exact structure that gets hashed and anchored.

    Everything here is either a hash, a public URL, or a number a third party
    can reproduce. No embeddings and no raw image bytes go on-chain.
    """
    return {
        "schema": SCHEMA_VERSION,
        "searched_at": searched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "face_scan": {
            "source_filename": Path(query_image_path).name,
            "image_sha256": query_image_sha256,
            "crop_sha256": face_crop_sha256,
            "hosted_crop_url": hosted_crop_url,
            "bbox": list(face_bbox),
            "detection_score": round(float(face_det_score), 6),
            "model": model,
        },
        "search": {
            "engine": engine,
            # Every distinct query put to the engine, so an auditor can see
            # exactly what was asked -- the face crop, the full photo, or both.
            "queries": sorted(queries, key=lambda q: (q.get("kind", ""), q.get("url", ""))),
            "candidates_seen": candidates_seen,
            "social_candidates": social_candidates,
        },
        "match": {
            "post_url": match["page_url"],
            "platform": match.get("platform"),
            "host": match.get("host"),
            "title": match.get("title", ""),
            "matched_image_url": match["matched_image_url"],
            "matched_image_sha256": match["image_sha256"],
            "similarity": round(float(match["similarity"]), 6),
            "threshold": float(threshold),
            "faces_in_matched_image": int(match.get("faces_in_image", 0)),
        },
    }


def write_bundle(bundle: dict, path: str | Path) -> Path:
    """Persist the bundle in its canonical byte form.

    Written as canonical bytes rather than pretty JSON on purpose: the file on
    disk hashes to the anchored digest exactly as it sits, so `verify` never
    depends on re-serialising it the same way.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(bundle))
    return path


def read_bundle(path: str | Path) -> dict:
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def similarity_to_bps(similarity: float) -> int:
    """Cosine similarity -> basis points, clamped to the contract's uint32."""
    return max(0, min(10_000, int(round(float(similarity) * 10_000))))
