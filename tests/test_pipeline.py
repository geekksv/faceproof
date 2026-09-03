"""Tests for the offline half of the pipeline.

The search backends are deliberately not tested here: they hit live third-party
engines, so asserting on their output would make the suite flaky and would
test Yandex rather than this project. What *is* tested is everything that
determines whether a result can be trusted -- face matching, canonical hashing,
and tamper detection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.evidence import (
    canonical_json,
    evidence_hash,
    read_bundle,
    similarity_to_bps,
    write_bundle,
)
from pipeline.face import FaceEncoder, NoFaceFound, cosine_similarity, sha256_file
from pipeline.search.base import Candidate, classify_domain, dedupe

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
KOHLI = SAMPLES / "virat_kohli.jpg"
DHONI = SAMPLES / "ms_dhoni.jpg"
PICHAI = SAMPLES / "sundar_pichai.jpg"


# ------------------------------------------------------------------ faces


@pytest.fixture(scope="module")
def kohli():
    return FaceEncoder.primary_face(KOHLI)


def test_detects_a_face(kohli):
    assert kohli.det_score > 0.5
    assert kohli.embedding.shape == (512,)
    assert kohli.area > 0


def test_embedding_is_l2_normalised(kohli):
    import numpy as np

    assert np.isclose(np.linalg.norm(kohli.embedding), 1.0, atol=1e-4)


def test_same_face_survives_cropping(tmp_path, kohli):
    """A crop of a face must still embed as that face -- this is what the
    pipeline relies on when it searches with a crop and matches full images."""
    crop = FaceEncoder.crop(KOHLI, kohli, tmp_path / "c.jpg")
    recropped = FaceEncoder.primary_face(crop)
    assert cosine_similarity(kohli.embedding, recropped.embedding) > 0.9


@pytest.mark.parametrize("other_path", [DHONI, PICHAI])
def test_different_people_score_far_below_threshold(kohli, other_path):
    """The negative control.

    If this ever failed, every 'verified match' in the pipeline would be
    meaningless -- so it is the single most important assertion here.
    """
    other = FaceEncoder.primary_face(other_path)
    sim = cosine_similarity(kohli.embedding, other.embedding)
    assert sim < 0.30, f"unrelated faces scored {sim:.4f}"


def test_no_face_raises(tmp_path):
    import numpy as np
    import cv2

    blank = tmp_path / "blank.jpg"
    cv2.imwrite(str(blank), np.full((300, 300, 3), 200, dtype=np.uint8))
    with pytest.raises(NoFaceFound):
        FaceEncoder.primary_face(blank)


def test_sha256_is_stable():
    assert sha256_file(KOHLI) == sha256_file(KOHLI)
    assert sha256_file(KOHLI) != sha256_file(DHONI)


# --------------------------------------------------------------- evidence


def test_canonical_json_is_key_order_independent():
    a = {"b": 2, "a": 1, "n": {"z": 1, "y": 2}}
    b = {"n": {"y": 2, "z": 1}, "a": 1, "b": 2}
    assert canonical_json(a) == canonical_json(b)
    assert evidence_hash(a) == evidence_hash(b)


def test_evidence_hash_is_keccak_shaped():
    h = evidence_hash({"x": 1})
    assert h.startswith("0x") and len(h) == 66


def test_one_byte_change_changes_the_hash():
    base = {"match": {"post_url": "https://x.com/a", "similarity": 0.99}}
    edited = {"match": {"post_url": "https://x.com/b", "similarity": 0.99}}
    assert evidence_hash(base) != evidence_hash(edited)


def test_bundle_roundtrips_through_disk(tmp_path):
    """The file on disk must hash to the anchored digest exactly as written."""
    bundle = {"schema": "faceproof/v1", "match": {"post_url": "https://x.com/a"}}
    p = write_bundle(bundle, tmp_path / "e.json")
    assert evidence_hash(read_bundle(p)) == evidence_hash(bundle)
    assert p.read_bytes() == canonical_json(bundle)


@pytest.mark.parametrize(
    "sim,bps", [(0.0, 0), (1.0, 10_000), (0.9947, 9947), (-0.5, 0), (2.0, 10_000)]
)
def test_similarity_to_bps_clamps(sim, bps):
    assert similarity_to_bps(sim) == bps


# ----------------------------------------------------------------- search


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://www.instagram.com/p/abc/", "Instagram"),
        ("https://x.com/user/status/1", "X (Twitter)"),
        ("https://gr.pinterest.com/pin/123/", "Pinterest"),
        ("https://www.youtube.com/shorts/abc", "YouTube"),
        ("https://example.com/news", None),
    ],
)
def test_domain_classification(url, platform):
    assert classify_domain(url)[0] == platform


def test_subdomain_is_not_confused_with_a_lookalike_domain():
    assert classify_domain("https://notinstagram.com/x")[0] is None
    assert classify_domain("https://www.instagram.com.evil.net/x")[0] is None


def test_dedupe_keeps_best_ranked_sighting():
    c = [
        Candidate(page_url="https://x.com/a", engine_rank=1),
        Candidate(page_url="https://x.com/a/", engine_rank=5),
        Candidate(page_url="https://x.com/b", engine_rank=2),
    ]
    out = dedupe(c)
    assert len(out) == 2
    assert out[0].engine_rank == 1
