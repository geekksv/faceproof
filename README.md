# FaceProof — Face Identification with Blockchain-Verified Evidence

A pipeline that takes a face scan, finds that person on live social media through a
real reverse-image search, verifies the match with its own face-recognition model,
and anchors the finding on a blockchain as a tamper-evident record.

```
  face scan  ──▶  reverse image search  ──▶  independent  ──▶  evidence  ──▶  on-chain
  (InsightFace)   (Yandex CBIR / Bing)       verification      bundle         anchor
   detect +        real live query,           our own          canonical      keccak256
   512-d ArcFace   no hardcoded results       ArcFace re-check  JSON          in a contract
```

Built for **HH Goa 2026 Shortlisting Task 3**.

---

## What it actually does

Run one command:

```bash
python -m pipeline.cli scan samples/virat_kohli.jpg --network localhost
```

and the pipeline:

1. **Scans the face** — SCRFD detects every face in the input, picks the largest,
   and encodes it as a 512-dimension ArcFace embedding. Runs locally on CPU.
2. **Searches the live web** — publishes the face crop to a temporary URL, then
   queries Yandex's reverse-image (CBIR) index for pages containing that face.
   This is a genuine network call to a live engine; results differ run to run.
3. **Verifies the match itself** — downloads the image from each candidate post,
   re-runs its *own* detector and embedder over it, and computes cosine similarity
   against the query face. Anything below the threshold is discarded no matter how
   highly the search engine ranked it.
4. **Builds an evidence bundle** — a canonical JSON document recording what was
   scanned, which engine was asked, what came back, and the similarity measured.
5. **Anchors it on-chain** — `keccak256` of that bundle goes into a Solidity
   registry contract, together with the post URL, platform and similarity score.

Then, at any point afterwards:

```bash
python -m pipeline.cli verify out/evidence.json --network localhost
```

recomputes the digest from the file on disk and checks it against the chain.

### Real output

```
Step 3/5 · Independent face verification
      re-checking each hit with our own embedder — the engine's ranking is not trusted
      PASS sim=+0.8199  Pinterest   https://www.pinterest.com/pin/1101271533748...
      PASS sim=+0.9774  YouTube     https://www.youtube.com/@varioustips8692
      PASS sim=+0.9865  YouTube     https://www.youtube.com/shorts/B38jsTSNXYI
        ·    no usable face   YouTube     https://www.youtube.com/shorts/reR05rPo2Ho
      PASS sim=+0.9947  YouTube     https://www.youtube.com/shorts/t7K38auPbPI
  OK  11 post(s) independently verified as the same person

Step 5/5 · Blockchain anchor
  OK  anchored in block 3 (gas 213,454)
      tx 0x0674e8d41aeddb0a01263ab8d50e371efe7902b745b08e45f196a39bb419f2ae
```

---

## Why the search result can be trusted

The task's hard requirement is that the search is genuine, not a pre-picked URL.
Three things make that checkable rather than a claim:

**The engine's verdict is never used.** Yandex says "these pages contain a similar
image." The pipeline ignores that judgement entirely, re-downloads the image, and
decides for itself. A hardcoded URL would have to survive our own face matcher.

**The threshold has enormous margin.** Measured on the bundled samples:

| pair | cosine similarity |
|---|---|
| Kohli ↔ Dhoni | +0.017 |
| Kohli ↔ Pichai | +0.069 |
| Dhoni ↔ Pichai | +0.022 |
| **acceptance threshold** | **0.45** |
| Kohli ↔ verified YouTube posts | +0.93 to +0.99 |

Unrelated faces land near zero; true matches land near one. Nothing sits near the
line. `tests/test_pipeline.py::test_different_people_score_far_below_threshold`
enforces this — if it ever failed, every "verified match" would be meaningless.

**Failures are visible.** The run prints every candidate it checked, including the
ones that failed and the ones with no usable face. There is no silent filtering.

---

## Blockchain design

`contracts/FaceProofRegistry.sol` stores, per finding:

| field | why |
|---|---|
| `evidenceHash` | `keccak256` of the canonical evidence JSON — the anchor itself |
| `faceHash` | `sha256` of the input image bytes |
| `submitter` | `msg.sender`, recorded by the chain, not claimed by the caller |
| `timestamp` | block time — proves *when* the evidence existed |
| `similarityBps` | the measured similarity, in basis points |
| `postUrl`, `platform` | public pointers so an auditor can re-fetch the material |

**No photograph and no biometric template ever goes on-chain.** Only a hash plus
public pointers. That keeps the record cheap, keeps personal data off a permanent
public ledger, and still makes any later edit detectable.

Anchoring the same hash twice **reverts** (`AlreadyAnchored`). Records are write-once
by construction — immutability is the entire point, so the contract refuses to let
an existing finding be quietly overwritten.

### Which blockchain

Both, selected with `--network`:

- **`localhost`** — a Hardhat node on your machine. Free, instant, deterministic,
  works offline. The default.
- **Public testnets** — `sepolia`, `amoy` (Polygon), `baseSepolia`. Real public
  chains with a block explorer anyone can independently check. Testnet coins are
  free from a faucet; **no real money is involved**.

The contract, the pipeline and the verification path are identical on both.

### Verifying a record independently

The point of anchoring is that someone else can check it without trusting you:

1. Take `out/evidence.json`.
2. Recompute `keccak256` over its bytes (they are stored in canonical form — sorted
   keys, no insignificant whitespace — so the file hashes as it sits).
3. Call `getProof(hash)` on the contract.
4. Open the `postUrl` from the record and confirm the face is who it claims.

Step 3 can be done from Etherscan's "Read Contract" tab on a testnet deployment —
no code, no trust in this repo.

---

## Setup

Requires **Python 3.11+** (developed on 3.14) and **Node 18+**.

```bash
git clone <your-repo-url>
cd hhg

# Python side
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux
.venv\Scripts\python.exe -m playwright install chromium

# Node / contract side
npm install
npx hardhat compile
```

The InsightFace `buffalo_l` model pack (~280 MB) downloads automatically on first run.

### Run it

```bash
# terminal 1 — local blockchain, leave running
npx hardhat node

# terminal 2
npx hardhat run scripts/deploy.js --network localhost
python -m pipeline.cli scan samples/virat_kohli.jpg --network localhost
python -m pipeline.cli verify out/evidence.json --network localhost
python -m pipeline.cli tamper out/evidence.json --network localhost
```

### Deploying to a public testnet

```bash
cp .env.example .env      # add a funded testnet PRIVATE_KEY (throwaway wallet)
npx hardhat run scripts/deploy.js --network sepolia
python -m pipeline.cli scan samples/virat_kohli.jpg --network sepolia
```

Free testnet ETH: the Google Cloud Web3 faucet for Sepolia, or the Polygon faucet
for Amoy. If a faucet gates on a mainnet balance, use Amoy or Base Sepolia.

---

## Commands

| command | what it does |
|---|---|
| `scan <image>` | the full pipeline: detect → search → verify → anchor |
| `verify [bundle]` | recompute the digest and check it against the chain |
| `tamper [bundle]` | edit one character and show verification fail |
| `info` | chain, contract and signer status |

Useful flags on `scan`:

| flag | effect |
|---|---|
| `--backend yandex\|bing\|both` | which engine to query (default `yandex`) |
| `--threshold 0.45` | minimum cosine similarity to accept a match |
| `--any-domain` | verify every result page, not just social platforms |
| `--image-url URL` | search an already-public image; uploads nothing |
| `--no-chain` | run the search half only |
| `--headless` | hide the browser window |
| `--network` | `localhost`, `sepolia`, `amoy`, `baseSepolia` |

---

## Tests

```bash
npx hardhat test                                  # 7 contract tests
.venv\Scripts\python.exe -m pytest tests/ -q      # 23 pipeline tests
```

The Python suite covers face matching (including the negative control), canonical
hashing determinism, tamper detection, and domain classification. The contract
suite covers anchoring, write-once enforcement, and the unknown-hash revert.

The live search backends are deliberately **not** unit-tested: they call
third-party engines, so asserting on their output would test Yandex rather than
this project, and would be flaky by construction.

---

## How the search works, and why it is built this way

Reverse **face** search has no free API. FaceCheck.ID has the cleanest one but
charges ~$0.30 per search, and its free `demo` mode scans only 100 000 faces and
returns meaningless results. PimEyes has no API at all. So this project drives the
free engines directly:

- **Yandex** (primary) — the strongest freely scriptable engine for faces. Its
  browser upload widget silently drops scripted `set_input_files` calls, but the
  `?url=` CBIR entry point works reliably. `&cbir_page=sites` is the key detail:
  without it Yandex renders ~4 teaser results, with it the full list of ~60.
- **Bing** (fallback) — resolves a face to a *named entity* and returns pages about
  that person. Broader and less precise, but a useful cross-check, and it recovers
  an identity label Yandex never provides. Its organic links are wrapped in
  `/ck/a?` click-trackers whose real destination is base64url-encoded in the `u`
  parameter; `pipeline/search/bing.py` decodes them.

Because the URL entry point needs a publicly fetchable image, `pipeline/imagehost.py`
uploads the crop to an anonymous host (catbox.moe, with uguu and tmpfiles as
fallbacks) and confirms it is readable before searching.

---

## Privacy

This is face-search tooling, and it is worth being blunt about what that means.

- **The face crop is uploaded to a third-party anonymous host** so the search engine
  can fetch it. That is a real disclosure. It is fine for the public-figure demo;
  think before pointing it at a private person's photo. `--image-url` skips the
  upload entirely when the image is already public.
- **Nothing biometric goes on-chain.** Embeddings and images stay local; only hashes
  and public URLs are anchored.
- **A blockchain record is permanent.** Anchoring a URL about a person cannot be
  undone. That is the point of the tool, and a reason to be deliberate about what
  you anchor.
- The bundled samples are public figures, from freely-licensed Wikimedia Commons
  photographs.

---

## Known limitations

- **Search results are not reproducible.** Yandex's index changes, so two runs of
  the same image can return different posts. This is inherent to searching the
  live web, and it is why evidence is anchored per-run rather than treated as a
  stable identifier.
- **Instagram, Facebook and X block scraping.** They appear in results, but their
  images usually cannot be downloaded, so they rarely survive verification.
  YouTube, Pinterest and Reddit serve images openly and verify reliably.
- **Yandex can serve a CAPTCHA** from datacenter or heavily-used IPs. Re-run
  without `--headless` and solve it in the visible window, or use `--backend bing`.
- **One face per scan.** The largest detected face is used; group photos need the
  subject to be the most prominent person.
- **Similarity is not identity.** A high score means the same face, not a verified
  legal identity. Identical twins and some heavy edits can fool any face embedder.
- **The evidence bundle references remote URLs.** If a post is deleted, the on-chain
  hash still proves what was found and when, but the material itself may be gone.
  Archiving the matched image bytes would fix this; only its `sha256` is recorded today.
- **Anonymous image hosts expire.** `hosted_crop_url` in the bundle may 404 later.
  The hash of the crop is recorded, so the evidence stays checkable if you keep the file.
- **Testnets are not permanent.** Public testnets get deprecated. For a record meant
  to last, mainnet or a dedicated chain would be the real answer.

---

## Project layout

```
pipeline/
  face.py            SCRFD detection + ArcFace embedding
  imagehost.py       publish a crop so search engines can fetch it
  search/
    base.py          Candidate type, social-domain classification
    yandex.py        reverse-image search (primary)
    bing.py          visual search + entity resolution (fallback)
    _browser.py      shared Playwright setup
  verify.py          independent re-verification of every candidate
  evidence.py        canonical JSON + keccak256 hashing
  chain.py           web3 client: anchor and read back
  cli.py             the four commands
contracts/
  FaceProofRegistry.sol
scripts/deploy.js
test/                contract tests (Hardhat/Mocha)
tests/               pipeline tests (pytest)
samples/             public-figure images from Wikimedia Commons
```

## License

MIT
