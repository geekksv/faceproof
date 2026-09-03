"""Fast face check on an input image, before committing to a full run.

A full scan publishes images, drives a browser and downloads dozens of
candidates -- several minutes. Detection takes a second. Failing here instead
means a bad input is rejected immediately rather than after the slow part.

Prints a single machine-readable line for the demo script to parse.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root,
# so `pipeline` would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    if len(sys.argv) < 2:
        print("USAGE")
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print("MISSING")
        return 2

    from pipeline.face import FaceEncoder

    try:
        faces = FaceEncoder.detect(path)
    except Exception as e:  # unreadable file, unsupported format, ...
        print(f"ERROR {type(e).__name__}: {e}")
        return 2

    if not faces:
        print("NO_FACE")
        return 1

    f = faces[0]
    x1, y1, x2, y2 = f.bbox
    print(f"OK {len(faces)} {x2 - x1}x{y2 - y1} {f.det_score:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
