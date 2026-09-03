"""Generate a self-contained HTML evidence report.

The terminal proves a match with numbers. That asks a reader to take the
pipeline's word for the one claim they most want to check themselves: that
these two photographs show the same person. This report puts the faces side
by side and shows every candidate that was rejected, with its score.

Everything is inlined as base64 -- no server, no network, no external assets.
The file opens with a double-click and still works a year from now, which is
the same property the on-chain anchor is going for.
"""

from __future__ import annotations

import base64
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

THUMB_PX = 150


def _data_uri(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _thumb(data: bytes, px: int = THUMB_PX) -> str | None:
    """Downscale to a thumbnail so a report with 30 candidates stays small."""
    try:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = px / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        return _data_uri(buf.tobytes()) if ok else None
    except Exception:
        return None


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


CSS = """
:root{
  --bg:#0f1115; --card:#171a21; --line:#262b36; --text:#e6e9ef; --dim:#98a1b3;
  --pass:#3ddc84; --fail:#ff6b6b; --warn:#ffc857; --accent:#5aa9ff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 72px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
  margin:38px 0 14px;font-weight:600}
.sub{color:var(--dim);font-size:14px;margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
.verdict{display:flex;align-items:center;gap:14px;font-size:19px;font-weight:650;
  border-radius:12px;padding:18px 22px;margin-bottom:8px}
.verdict.ok{background:rgba(61,220,132,.10);border:1px solid rgba(61,220,132,.35);color:var(--pass)}
.verdict.no{background:rgba(255,107,107,.10);border:1px solid rgba(255,107,107,.35);color:var(--fail)}
.faces{display:grid;grid-template-columns:1fr auto 1fr;gap:22px;align-items:center}
.face{text-align:center}
.face img{width:100%;max-width:260px;border-radius:12px;border:1px solid var(--line);display:block;margin:0 auto}
.face .lbl{color:var(--dim);font-size:13px;margin-top:10px}
.face .lbl a{color:var(--accent);text-decoration:none;word-break:break-all}
.score{text-align:center;min-width:150px}
.score .n{font-size:44px;font-weight:700;letter-spacing:-.03em;line-height:1}
.score .n.ok{color:var(--pass)} .score .n.no{color:var(--fail)}
.score .c{color:var(--dim);font-size:13px;margin-top:6px}
.bar{height:7px;background:#0b0d11;border-radius:99px;margin-top:14px;position:relative;overflow:hidden}
.bar .fill{height:100%;border-radius:99px}
.bar .mark{position:absolute;top:-4px;width:2px;height:15px;background:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:14px;table-layout:fixed}
th{text-align:left;color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.06em;padding:0 10px 10px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:none}
col.c-th{width:66px} col.c-pl{width:118px} col.c-sc{width:104px} col.c-vd{width:98px}
td.url{max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td img{width:46px;height:46px;object-fit:cover;border-radius:7px;border:1px solid var(--line);display:block}
.tag{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}
.tag.pass{background:rgba(61,220,132,.13);color:var(--pass)}
.tag.fail{background:rgba(255,107,107,.13);color:var(--fail)}
.tag.none{background:rgba(152,161,179,.13);color:var(--dim)}
.num{font-variant-numeric:tabular-nums;font-weight:600}
a.u{color:var(--accent);text-decoration:none} a.u:hover{text-decoration:underline}
.kv{display:grid;grid-template-columns:190px 1fr;gap:9px 18px;font-size:14px}
.kv .k{color:var(--dim)}
.mono{font-family:ui-monospace,"Cascadia Code",Consolas,monospace;font-size:12.5px;word-break:break-all}
.note{color:var(--dim);font-size:13.5px;margin-top:12px;line-height:1.6}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.faces,.grid2{grid-template-columns:1fr}.score{margin:12px 0}}
"""


def _verdict_block(best, threshold: float) -> str:
    if best is None:
        return (
            '<div class="verdict no">NO MATCH &mdash; nothing cleared the '
            f"{threshold} similarity threshold</div>"
        )
    return (
        '<div class="verdict ok">MATCH VERIFIED &mdash; '
        f"{_esc(best.candidate.platform or best.candidate.host)} "
        f"at {best.similarity:.4f} similarity</div>"
    )


def _faces_block(crop_bytes: bytes | None, best, threshold: float) -> str:
    if best is None or not crop_bytes:
        return ""
    left = _data_uri(crop_bytes)
    right = _thumb(best.image_bytes, 520) if best.image_bytes else None
    if not right:
        return ""

    # Scale 0..1 rather than the full -1..+1 cosine range. Negative
    # similarities never occur between two real faces, so the wider scale would
    # only push every score toward the right and flatter a marginal match.
    pct = max(0.0, min(1.0, best.similarity)) * 100
    colour = "var(--pass)" if best.passed else "var(--fail)"
    cls = "ok" if best.passed else "no"
    mark = max(0.0, min(1.0, threshold)) * 100

    return f"""
<div class="card">
  <div class="faces">
    <div class="face">
      <img src="{left}" alt="input face">
      <div class="lbl">Face scanned from your input image</div>
    </div>
    <div class="score">
      <div class="n {cls}">{best.similarity:.3f}</div>
      <div class="c">cosine similarity<br>threshold {threshold}</div>
    </div>
    <div class="face">
      <img src="{right}" alt="matched image">
      <div class="lbl">Found on
        <a href="{_esc(best.candidate.page_url)}" target="_blank" rel="noopener">
        {_esc(best.candidate.platform or best.candidate.host)}</a></div>
    </div>
  </div>
  <div class="bar">
    <div class="fill" style="width:{pct:.1f}%;background:{colour}"></div>
    <div class="mark" style="left:{mark:.1f}%"></div>
  </div>
  <div class="note">The bar runs 0 to 1; the amber marker is the {threshold} acceptance
  threshold. Both faces were embedded by the same local ArcFace model &mdash; the search
  engine's own ranking was not used.</div>
</div>"""


def _candidates_table(results: list[tuple]) -> str:
    if not results:
        return '<div class="card"><div class="note">No candidates were checked.</div></div>'

    rows = []
    for cand, match in results:
        if match is None:
            thumb, tag, score = "", '<span class="tag none">no face</span>', "&mdash;"
        else:
            t = _thumb(match.image_bytes, 92) if match.image_bytes else None
            thumb = f'<img src="{t}" alt="">' if t else ""
            if match.passed:
                tag = '<span class="tag pass">PASS</span>'
            else:
                tag = '<span class="tag fail">rejected</span>'
            score = f'<span class="num">{match.similarity:+.4f}</span>'

        rows.append(
            f"<tr><td>{thumb}</td>"
            f"<td>{_esc(cand.platform or cand.host)}</td>"
            f"<td>{score}</td><td>{tag}</td>"
            f'<td class="url"><a class="u" href="{_esc(cand.page_url)}" target="_blank" '
            f'rel="noopener" title="{_esc(cand.page_url)}">{_esc(cand.page_url)}</a></td></tr>'
        )

    checked = len(results)
    passed = sum(1 for _, m in results if m and m.passed)
    return f"""
<div class="card">
  <table>
    <colgroup><col class="c-th"><col class="c-pl"><col class="c-sc"><col class="c-vd"><col></colgroup>
    <thead><tr><th></th><th>Platform</th><th>Similarity</th><th>Verdict</th><th>Page</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</div>
<div class="note">{checked} candidate(s) independently re-checked, {passed} accepted.
Rejections are shown deliberately: a search engine returning the wrong person is
normal, and catching it is the point of this stage.</div>"""


def build_report(
    *,
    out_path: str | Path,
    image_path: str | Path,
    crop_path: str | Path,
    face,
    queries: list[dict],
    engine: str,
    results: list[tuple],
    best,
    threshold: float,
    bundle: dict,
    evidence_hash: str,
    receipt: dict | None,
    candidates_seen: int,
    social_candidates: int,
) -> Path:
    """Write a single self-contained HTML file summarising one scan."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    crop_bytes = Path(crop_path).read_bytes() if Path(crop_path).exists() else None
    input_thumb = _thumb(Path(image_path).read_bytes(), 300) if Path(image_path).exists() else None

    q_rows = "".join(
        f'<div class="k">{_esc(q["kind"])}</div>'
        f'<div class="mono"><a class="u" href="{_esc(q["url"])}" target="_blank" '
        f'rel="noopener">{_esc(q["url"])}</a></div>'
        for q in queries
    )

    if receipt:
        explorer = receipt.get("explorer_url")
        link = (
            f'<div class="k">explorer</div><div><a class="u" href="{_esc(explorer)}" '
            f'target="_blank" rel="noopener">{_esc(explorer)}</a></div>'
            if explorer
            else '<div class="k">explorer</div><div class="mono">local chain &mdash; no public explorer</div>'
        )
        chain = f"""
<div class="card"><div class="kv">
  <div class="k">network</div><div class="mono">{_esc(receipt.get('network'))}</div>
  <div class="k">contract</div><div class="mono">{_esc(receipt.get('contract_address'))}</div>
  <div class="k">transaction</div><div class="mono">{_esc(receipt.get('tx_hash'))}</div>
  <div class="k">block</div><div class="mono">{_esc(receipt.get('block_number'))}</div>
  <div class="k">gas used</div><div class="mono">{_esc(receipt.get('gas_used'))}</div>
  {link}
</div></div>"""
    else:
        chain = ('<div class="card"><div class="note">Not anchored on a blockchain '
                 '(<span class="mono">--no-chain</span>).</div></div>')

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FaceProof evidence report</title><style>{CSS}</style></head>
<body><div class="wrap">

<h1>FaceProof evidence report</h1>
<div class="sub">Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
&middot; engine <b>{_esc(engine)}</b>
&middot; {candidates_seen} pages found, {social_candidates} on social platforms</div>

{_verdict_block(best, threshold)}
{_faces_block(crop_bytes, best, threshold)}

<h2>Every candidate we checked</h2>
{_candidates_table(results)}

<h2>The face that was scanned</h2>
<div class="card"><div class="grid2">
  <div>{f'<img src="{input_thumb}" style="width:100%;max-width:300px;border-radius:10px;border:1px solid var(--line)">' if input_thumb else ''}</div>
  <div class="kv">
    <div class="k">source file</div><div class="mono">{_esc(Path(image_path).name)}</div>
    <div class="k">sha256</div><div class="mono">{_esc(bundle['face_scan']['image_sha256'])}</div>
    <div class="k">bounding box</div><div class="mono">{_esc(face.bbox)}</div>
    <div class="k">detector confidence</div><div class="mono">{face.det_score:.4f}</div>
    <div class="k">model</div><div class="mono">{_esc(bundle['face_scan']['model'])}</div>
  </div>
</div></div>

<h2>What was searched</h2>
<div class="card"><div class="kv">{q_rows}</div>
<div class="note">Two query images are used because they fail in opposite cases: the
face crop finds the same face in other photographs, the full image finds that exact
photograph reposted elsewhere.</div></div>

<h2>Blockchain anchor</h2>
{chain}

<h2>Evidence digest</h2>
<div class="card">
  <div class="kv">
    <div class="k">keccak256</div><div class="mono">{_esc(evidence_hash)}</div>
  </div>
  <div class="note">This digest is computed over the canonical evidence JSON &mdash;
  sorted keys, no insignificant whitespace &mdash; so the same facts always produce the
  same fingerprint, and changing a single character produces a different one. That is
  what makes the on-chain record tamper-evident: re-hash the file, compare to the chain.</div>
</div>

</div></body></html>"""

    out_path.write_text(doc, encoding="utf-8")
    return out_path
