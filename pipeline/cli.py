"""FaceProof CLI -- face scan to blockchain anchor, end to end.

Commands
    scan     run the whole pipeline on an image and anchor the result
    verify   re-check a saved evidence bundle against the chain
    tamper   demonstrate that edited evidence fails verification
    info     show chain / contract status
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

warnings.filterwarnings("ignore")

# Pin a comfortable width: URLs and 66-char hashes wrap into an unreadable
# mess in a default 80-column Windows console, and this output is meant to be
# screen-recorded.
console = Console(width=max(110, Console().width))


def _step(n: int, total: int, title: str) -> None:
    console.rule(f"[bold cyan]Step {n}/{total} · {title}", align="left")


def _ok(msg: str) -> None:
    console.print(f"  [green]OK[/green]  {msg}")


def _info(msg: str) -> None:
    console.print(f"      [dim]{msg}[/dim]")


def _fail(msg: str) -> None:
    console.print(f"  [red]FAIL[/red]  {msg}")


# ---------------------------------------------------------------- scan


def cmd_scan(args: argparse.Namespace) -> int:
    from .chain import ChainClient, ChainError
    from .evidence import build_bundle, evidence_hash, similarity_to_bps, write_bundle
    from .face import FaceEncoder, NoFaceFound, sha256_file
    from .imagehost import publish
    from .search import dedupe as search_dedupe, get_backend
    from .verify import verify_all

    image = Path(args.image)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    total = 5 if not args.no_chain else 4

    console.print(
        Panel.fit(
            f"[bold]FaceProof[/bold] — face identification with on-chain verification\n"
            f"[dim]input   [/dim] {image}\n"
            f"[dim]engine  [/dim] {args.backend}\n"
            f"[dim]network [/dim] {'(skipped)' if args.no_chain else args.network}",
            border_style="cyan",
        )
    )

    # -- 1. face scan --------------------------------------------------
    _step(1, total, "Face scan")
    try:
        faces = FaceEncoder.detect(image)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))
        return 2
    if not faces:
        _fail("no face detected in the input image")
        return 2

    face = faces[0]
    _ok(f"{len(faces)} face(s) detected; using the largest")
    _info(f"bbox={face.bbox}  detector confidence={face.det_score:.4f}")
    _info(f"embedding: 512-d ArcFace (buffalo_l / w600k_r50)")

    crop_path = FaceEncoder.crop(image, face, outdir / "query_face.jpg")
    image_sha = sha256_file(image)
    crop_sha = sha256_file(crop_path)
    _ok(f"face crop written to {crop_path}")
    _info(f"input sha256 {image_sha[:32]}...")

    # -- 2. web / social search ----------------------------------------
    _step(2, total, "Web & social media search")

    try:
        backend = get_backend(args.backend, headless=args.headless)
    except Exception as e:
        _fail(str(e))
        return 3

    # Two different questions, and they fail in opposite cases:
    #   the face crop  -> best for finding the same FACE in other photos
    #   the full photo -> best for finding the exact IMAGE reposted elsewhere
    # A private individual is usually only findable by the second, because
    # nothing of theirs has been cropped and reposted. Searching both roughly
    # doubles recall on ordinary people at the cost of one extra query.
    queries: list[dict] = []
    if args.image_url:
        queries.append({"kind": "supplied_url", "url": args.image_url})
        _ok("using the public image URL you supplied (nothing uploaded)")
    else:
        try:
            queries.append({"kind": "face_crop", "url": publish(crop_path).url})
            if not args.crop_only:
                queries.append({"kind": "full_image", "url": publish(image).url})
        except Exception as e:
            _fail(f"could not publish the image for search: {e}")
            return 3
        _ok(f"published {len(queries)} query image(s) so the engine can fetch them")

    for q in queries:
        _info(f"{q['kind']:<13} {q['url']}")

    candidates = []
    errors = []
    for q in queries:
        console.print(f"      [dim]querying {args.backend} with the {q['kind']}…[/dim]")
        try:
            found = backend.search_by_url(q["url"], limit=args.limit)
        except Exception as e:
            errors.append(f"{q['kind']}: {type(e).__name__}: {e}")
            _info(f"[yellow]{q['kind']} query failed: {type(e).__name__}[/yellow]")
            continue
        _info(f"{q['kind']:<13} returned {len(found)} pages")
        candidates.extend(found)

    if not candidates:
        _fail("the search returned nothing.\n      " + "\n      ".join(errors))
        return 3

    candidates = search_dedupe(candidates)
    social = [c for c in candidates if c.is_social]
    _ok(f"{len(candidates)} unique pages found, {len(social)} on social platforms")
    if getattr(backend, "entity_name", None):
        _info(f"engine identified the face as: {backend.entity_name}")
    for c in social[:8]:
        _info(f"[{c.engine_rank:>2}] {c.platform:<11} {c.page_url[:70]}")

    # -- 3. independent verification -----------------------------------
    _step(3, total, "Independent face verification")
    console.print(
        "      [dim]re-checking each hit with our own embedder — the engine's "
        "ranking is not trusted[/dim]"
    )

    def on_result(c, m):
        if m is None:
            _info(f"  ·    no usable face   {c.platform or c.host:<11} {c.page_url[:52]}")
        else:
            tag = "[green]PASS[/green]" if m.passed else "[yellow]fail[/yellow]"
            console.print(
                f"      {tag} sim={m.similarity:+.4f}  "
                f"{(c.platform or c.host):<11} {c.page_url[:52]}"
            )

    matches = verify_all(
        candidates,
        face.embedding,
        threshold=args.threshold,
        social_only=not args.any_domain,
        max_checks=args.max_checks,
        save_dir=outdir / "matches",
        on_result=on_result,
    )

    if not matches:
        _fail(
            f"no candidate cleared the {args.threshold} similarity threshold. "
            "Try --backend both, --any-domain, or a clearer input photo."
        )
        return 4

    best = matches[0]
    _ok(f"{len(matches)} post(s) independently verified as the same person")
    console.print(
        Panel.fit(
            f"[bold]{best.candidate.platform}[/bold]  "
            f"[green]similarity {best.similarity:.4f}[/green] "
            f"(threshold {args.threshold})\n{best.candidate.page_url}",
            title="Best match",
            border_style="green",
        )
    )

    # -- 4. evidence bundle --------------------------------------------
    _step(4, total, "Evidence bundle")
    bundle = build_bundle(
        query_image_path=image,
        query_image_sha256=image_sha,
        face_bbox=face.bbox,
        face_det_score=face.det_score,
        face_crop_sha256=crop_sha,
        hosted_crop_url=queries[0]["url"],
        engine=getattr(backend, "name", args.backend),
        queries=queries,
        candidates_seen=len(candidates),
        social_candidates=len(social),
        match=best.to_dict(),
        threshold=args.threshold,
    )
    ehash = evidence_hash(bundle)
    bundle_path = write_bundle(bundle, outdir / "evidence.json")
    _ok(f"canonical bundle written to {bundle_path}")
    _info(f"keccak256 = {ehash}")

    if args.no_chain:
        console.print("\n[yellow]--no-chain set: skipping the anchor step.[/yellow]")
        return 0

    # -- 5. anchor on chain --------------------------------------------
    _step(5, total, "Blockchain anchor")
    try:
        client = ChainClient(args.network)
    except ChainError as e:
        _fail(str(e))
        return 5

    _info(f"network {args.network} via {client.rpc_url}")
    _info(f"contract {client.address}")

    try:
        receipt = client.anchor(
            evidence_hash=ehash,
            face_sha256=image_sha,
            similarity_bps=similarity_to_bps(best.similarity),
            post_url=best.candidate.page_url,
            platform=best.candidate.platform or "",
        )
    except ChainError as e:
        _fail(str(e))
        return 5

    _ok(f"anchored in block {receipt.block_number} (gas {receipt.gas_used:,})")
    _info(f"tx {receipt.tx_hash}")
    if receipt.explorer_url:
        console.print(f"      [link]{receipt.explorer_url}[/link]")

    (outdir / "receipt.json").write_text(
        json.dumps(receipt.to_dict(), indent=2), encoding="utf-8"
    )
    _ok(f"receipt written to {outdir / 'receipt.json'}")

    console.print(
        Panel.fit(
            f"Evidence hash [bold]{ehash}[/bold]\n"
            f"is now permanently recorded on [bold]{args.network}[/bold].\n\n"
            f"Re-verify at any time:\n"
            f"  [cyan]python -m pipeline.cli verify {outdir / 'evidence.json'} "
            f"--network {args.network}[/cyan]",
            title="[green]Pipeline complete[/green]",
            border_style="green",
        )
    )
    return 0


# -------------------------------------------------------------- verify


def cmd_verify(args: argparse.Namespace) -> int:
    from .chain import ChainClient, ChainError
    from .evidence import canonical_json, evidence_hash, read_bundle

    path = Path(args.bundle)
    if not path.exists():
        _fail(f"no such bundle: {path}")
        return 2

    bundle = read_bundle(path)
    ehash = evidence_hash(bundle)

    console.print(
        Panel.fit(
            f"[dim]bundle [/dim] {path}\n"
            f"[dim]bytes  [/dim] {len(canonical_json(bundle)):,}\n"
            f"[dim]digest [/dim] {ehash}",
            title="Recomputed from the file on disk",
            border_style="cyan",
        )
    )

    try:
        client = ChainClient(args.network)
    except ChainError as e:
        _fail(str(e))
        return 5

    proof = client.get_proof(ehash)
    if proof is None:
        console.print(
            Panel.fit(
                "[bold red]VERIFICATION FAILED[/bold red]\n\n"
                "This digest is not on the chain. Either the evidence file was "
                "modified after anchoring, or it was never anchored on this network.",
                border_style="red",
            )
        )
        return 1

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[dim]network[/dim]", args.network)
    t.add_row("[dim]contract[/dim]", client.address)
    t.add_row("[dim]submitter[/dim]", proof.submitter)
    t.add_row("[dim]anchored at[/dim]", f"unix {proof.timestamp}")
    t.add_row("[dim]post URL[/dim]", proof.post_url)
    t.add_row("[dim]platform[/dim]", proof.platform)
    t.add_row("[dim]similarity[/dim]", f"{proof.similarity_bps / 10_000:.4f}")

    console.print(
        Panel(
            t,
            title="[green]VERIFIED — evidence matches the on-chain record[/green]",
            border_style="green",
        )
    )

    # Cross-check the fields the contract stores against the local bundle.
    local_url = bundle["match"]["post_url"]
    if local_url != proof.post_url:
        console.print(
            f"[yellow]note:[/yellow] on-chain post URL differs from the bundle "
            f"({proof.post_url} vs {local_url})"
        )
    return 0


# -------------------------------------------------------------- tamper


def cmd_tamper(args: argparse.Namespace) -> int:
    """Show that a single edited character breaks verification."""
    from .chain import ChainClient, ChainError
    from .evidence import evidence_hash, read_bundle

    path = Path(args.bundle)
    if not path.exists():
        _fail(f"no such bundle: {path}")
        return 2

    bundle = read_bundle(path)
    original = evidence_hash(bundle)

    tampered = json.loads(json.dumps(bundle))
    old_url = tampered["match"]["post_url"]
    tampered["match"]["post_url"] = old_url + "X"
    new_hash = evidence_hash(tampered)

    console.print(
        Panel.fit(
            f"[dim]original post_url [/dim] {old_url}\n"
            f"[dim]tampered post_url [/dim] {old_url}[red]X[/red]\n\n"
            f"[dim]original digest   [/dim] [green]{original}[/green]\n"
            f"[dim]tampered digest   [/dim] [red]{new_hash}[/red]",
            title="One character changed",
            border_style="yellow",
        )
    )

    try:
        client = ChainClient(args.network)
    except ChainError as e:
        _fail(str(e))
        return 5

    console.print(f"  original on chain : {'[green]YES[/green]' if client.is_anchored(original) else '[red]NO[/red]'}")
    console.print(f"  tampered on chain : {'[green]YES[/green]' if client.is_anchored(new_hash) else '[red]NO[/red]'}")
    console.print(
        "\n[bold]The tampered bundle cannot be passed off as the anchored one:[/bold] "
        "its digest simply is not in the registry."
    )
    return 0


# ---------------------------------------------------------------- info


def cmd_info(args: argparse.Namespace) -> int:
    from .chain import ChainClient, ChainError

    try:
        # Tolerate a missing deployment: this command is how you check whether
        # a faucet has funded you, which necessarily happens before deploying.
        client = ChainClient(args.network, require_deployment=False)
    except ChainError as e:
        _fail(str(e))
        return 5

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[dim]network[/dim]", args.network)
    t.add_row("[dim]rpc[/dim]", client.rpc_url)
    t.add_row("[dim]chain id[/dim]", str(client.w3.eth.chain_id))
    t.add_row("[dim]block[/dim]", str(client.w3.eth.block_number))

    if client.address:
        t.add_row("[dim]contract[/dim]", client.address)
        t.add_row("[dim]proofs anchored[/dim]", str(client.total()))
    else:
        t.add_row("[dim]contract[/dim]", "[yellow]not deployed on this network yet[/yellow]")

    if client.account:
        bal = client.signer_balance_eth()
        t.add_row("[dim]signer[/dim]", client.account.address)
        colour = "green" if bal > 0 else "red"
        t.add_row("[dim]balance[/dim]", f"[{colour}]{bal} ETH[/{colour}]")
        if bal == 0 and args.network != "localhost":
            t.add_row("", "[yellow]fund this address from a faucet before deploying[/yellow]")
    else:
        t.add_row("[dim]signer[/dim]", "[yellow]none — set PRIVATE_KEY in .env[/yellow]")

    if client.explorer_address_url():
        t.add_row("[dim]explorer[/dim]", client.explorer_address_url())

    console.print(Panel(t, title="FaceProofRegistry", border_style="cyan"))
    return 0


# ----------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.cli",
        description="Face identification with blockchain-verified evidence.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="run the full pipeline on an image")
    s.add_argument("image", help="path to the input face image")
    s.add_argument("--backend", default="yandex",
                   choices=["yandex", "bing", "both", "facecheck"],
                   help="yandex/bing/both are free image search; facecheck is "
                        "paid true face search and needs FACECHECK_API_TOKEN")
    s.add_argument("--network", default="localhost")
    s.add_argument("--out", default="out", help="output directory")
    s.add_argument("--threshold", type=float, default=0.45,
                   help="minimum ArcFace cosine similarity to accept (default 0.45)")
    s.add_argument("--limit", type=int, default=60, help="max search results to pull")
    s.add_argument("--max-checks", type=int, default=25,
                   help="max candidates to download and face-check")
    s.add_argument("--any-domain", action="store_true",
                   help="verify all result pages, not just social platforms")
    s.add_argument("--image-url",
                   help="use this already-public image URL instead of uploading anything")
    s.add_argument("--crop-only", action="store_true",
                   help="search only the face crop, not the full photo (faster, "
                        "but much worse recall on non-celebrities)")
    s.add_argument("--no-chain", action="store_true", help="stop before anchoring")
    s.add_argument("--headless", action="store_true", default=None,
                   help="run the browser headless (default: visible)")
    s.set_defaults(func=cmd_scan)

    v = sub.add_parser("verify", help="re-verify an evidence bundle against the chain")
    v.add_argument("bundle", nargs="?", default="out/evidence.json")
    v.add_argument("--network", default="localhost")
    v.set_defaults(func=cmd_verify)

    t = sub.add_parser("tamper", help="prove that edited evidence fails verification")
    t.add_argument("bundle", nargs="?", default="out/evidence.json")
    t.add_argument("--network", default="localhost")
    t.set_defaults(func=cmd_tamper)

    i = sub.add_parser("info", help="show chain and contract status")
    i.add_argument("--network", default="localhost")
    i.set_defaults(func=cmd_info)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252 and choke on non-ASCII post titles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv()

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
