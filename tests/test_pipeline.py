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


# ------------------------------------------------------- inline image bytes


def test_candidate_reports_inline_image():
    """FaceCheck hands back thumbnails inline; verification uses them directly
    instead of re-fetching from a host that may block scripts."""
    with_bytes = Candidate(page_url="https://x.com/a", image_bytes=b"\xff\xd8\xff")
    without = Candidate(page_url="https://x.com/b")
    assert with_bytes.to_dict()["image_inline"] is True
    assert without.to_dict()["image_inline"] is False


def test_verify_prefers_inline_bytes_over_network(kohli):
    """A candidate carrying real image bytes must verify without any network
    access -- this is what makes scrape-blocked platforms checkable."""
    from pipeline.verify import verify_candidate

    c = Candidate(
        page_url="https://example.com/post",
        engine="test",
        image_bytes=KOHLI.read_bytes(),
    )
    m = verify_candidate(c, kohli.embedding)
    assert m is not None
    assert m.passed
    assert m.similarity > 0.9


def test_verify_rejects_a_different_person_even_when_handed_the_bytes(kohli):
    """The threshold is enforced regardless of what the engine claims."""
    from pipeline.verify import verify_candidate

    c = Candidate(page_url="https://example.com/p", engine="test",
                  image_bytes=DHONI.read_bytes())
    m = verify_candidate(c, kohli.embedding)
    assert m is not None
    assert not m.passed
    assert m.similarity < 0.30


# ------------------------------------------------------------ backends


def test_facecheck_refuses_to_run_without_a_token(monkeypatch):
    """A paid backend must fail loudly, not silently return nothing."""
    from pipeline.search.facecheck import FaceCheckBackend, FaceCheckError

    monkeypatch.delenv("FACECHECK_API_TOKEN", raising=False)
    with pytest.raises(FaceCheckError, match="token"):
        FaceCheckBackend()


def test_unknown_backend_names_the_valid_options():
    from pipeline.search import get_backend

    with pytest.raises(ValueError, match="yandex"):
        get_backend("nope")


def test_evidence_records_every_query_put_to_the_engine():
    """An auditor must be able to see whether we searched the crop, the full
    photo, or both -- so the queries list is part of the hashed evidence."""
    from pipeline.evidence import build_bundle

    queries = [
        {"kind": "full_image", "url": "https://h/2.jpg"},
        {"kind": "face_crop", "url": "https://h/1.jpg"},
    ]
    b = build_bundle(
        query_image_path="a.jpg", query_image_sha256="ab", face_bbox=(0, 0, 1, 1),
        face_det_score=0.9, face_crop_sha256="cd", hosted_crop_url="https://h/1.jpg",
        engine="yandex", queries=queries, candidates_seen=10, social_candidates=3,
        match={"page_url": "https://x.com/a", "matched_image_url": "https://i/1.jpg",
               "image_sha256": "ef", "similarity": 0.99, "platform": "X (Twitter)"},
        threshold=0.45,
    )
    kinds = [q["kind"] for q in b["search"]["queries"]]
    assert kinds == ["face_crop", "full_image"], "queries must be canonically ordered"


# ------------------------------------------------- extreme close-up faces

RAHUL = SAMPLES / "kl_rahul.jpg"


def _fill_frame_crop(path, tmp_path):
    """Crop an image down to just the face, so it fills the whole frame --
    the shape of a typical social media profile picture."""
    import cv2

    f = FaceEncoder.primary_face(path)
    img = FaceEncoder.load_image(path)
    x1, y1, x2, y2 = f.bbox
    out = tmp_path / "closeup.jpg"
    cv2.imwrite(str(out), img[max(0, y1):y2, max(0, x1):x2])
    return out


@pytest.mark.parametrize("src", [KOHLI, RAHUL])
def test_detects_a_face_that_fills_the_entire_frame(tmp_path, src):
    """Regression: SCRFD's anchors top out below a face this large and the
    first detection pass returns nothing. Profile pictures are nearly always
    this shape, so failing here silently loses the social matches that matter
    most. `detect` retries on a padded copy.
    """
    closeup = _fill_frame_crop(src, tmp_path)
    faces = FaceEncoder.detect(closeup)
    assert faces, "a face filling the frame must still be detected"


def test_padded_retry_keeps_bboxes_in_image_coordinates(tmp_path):
    """The retry pads the image, so boxes must be shifted back or every
    downstream crop would be offset."""
    closeup = _fill_frame_crop(KOHLI, tmp_path)
    img = FaceEncoder.load_image(closeup)
    h, w = img.shape[:2]
    face = FaceEncoder.primary_face(closeup)
    x1, y1, x2, y2 = face.bbox
    assert x2 > x1 and y2 > y1
    # Generous bounds: the box may extend slightly past the crop edge, but it
    # must not be sitting out in padding space.
    assert -w < x1 < w and -h < y1 < h
    assert 0 < x2 <= 2 * w and 0 < y2 <= 2 * h


def test_closeup_still_matches_the_same_person(tmp_path):
    """Padding must not distort the embedding."""
    face = FaceEncoder.primary_face(KOHLI)
    closeup = _fill_frame_crop(KOHLI, tmp_path)
    reembedded = FaceEncoder.primary_face(closeup)
    assert cosine_similarity(face.embedding, reembedded.embedding) > 0.85


def test_closeup_of_one_person_still_rejects_another(tmp_path):
    """The padded retry must not weaken discrimination."""
    kohli_face = FaceEncoder.primary_face(KOHLI)
    dhoni_closeup = _fill_frame_crop(DHONI, tmp_path)
    other = FaceEncoder.primary_face(dhoni_closeup)
    assert cosine_similarity(kohli_face.embedding, other.embedding) < 0.30


# ------------------------------------------------------------- report


def test_report_renders_a_self_contained_file(tmp_path, kohli):
    """The report must open with no server and no network: every image inlined."""
    from pipeline.report import build_report
    from pipeline.verify import verify_candidate

    c = Candidate(page_url="https://x.com/someone", engine="test",
                  image_bytes=KOHLI.read_bytes(), title="t")
    m = verify_candidate(c, kohli.embedding)
    crop = FaceEncoder.crop(KOHLI, kohli, tmp_path / "crop.jpg")

    bundle = {
        "face_scan": {"image_sha256": "ab" * 32,
                      "model": "insightface/buffalo_l"},
        "match": {"post_url": c.page_url},
    }
    out = build_report(
        out_path=tmp_path / "r.html", image_path=KOHLI, crop_path=crop, face=kohli,
        queries=[{"kind": "face_crop", "url": "https://h/1.jpg"}], engine="yandex",
        results=[(c, m)], best=m, threshold=0.45, bundle=bundle,
        evidence_hash="0x" + "cd" * 32,
        receipt={"network": "localhost", "contract_address": "0xabc",
                 "tx_hash": "0xdef", "block_number": 3, "gas_used": 1000,
                 "explorer_url": None},
        candidates_seen=10, social_candidates=2,
    )
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "MATCH VERIFIED" in text
    assert "data:image/jpeg;base64," in text
    # No external assets: nothing may be fetched when the file is opened.
    for attr in ('src="http', 'href="http://cdn', "<script"):
        assert attr not in text, f"report must not reference {attr}"


def test_report_handles_a_run_with_no_match(tmp_path, kohli):
    """A failed scan still deserves a readable report."""
    from pipeline.report import build_report

    crop = FaceEncoder.crop(KOHLI, kohli, tmp_path / "crop.jpg")
    out = build_report(
        out_path=tmp_path / "r.html", image_path=KOHLI, crop_path=crop, face=kohli,
        queries=[], engine="yandex", results=[], best=None, threshold=0.45,
        bundle={"face_scan": {"image_sha256": "ab", "model": "m"}, "match": {}},
        evidence_hash="0x00", receipt=None, candidates_seen=0, social_candidates=0,
    )
    text = out.read_text(encoding="utf-8")
    assert "NO MATCH" in text
    assert "--no-chain" in text


def test_report_escapes_untrusted_page_titles(tmp_path, kohli):
    """Titles and URLs come from search engines; they must not inject markup."""
    from pipeline.report import build_report

    evil = 'https://x.com/a"><script>alert(1)</script>'
    c = Candidate(page_url=evil, engine="test", title="<img onerror=alert(1)>")
    crop = FaceEncoder.crop(KOHLI, kohli, tmp_path / "crop.jpg")
    out = build_report(
        out_path=tmp_path / "r.html", image_path=KOHLI, crop_path=crop, face=kohli,
        queries=[], engine="yandex", results=[(c, None)], best=None, threshold=0.45,
        bundle={"face_scan": {"image_sha256": "ab", "model": "m"}, "match": {}},
        evidence_hash="0x00", receipt=None, candidates_seen=1, social_candidates=1,
    )
    text = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text or "&quot;" in text
