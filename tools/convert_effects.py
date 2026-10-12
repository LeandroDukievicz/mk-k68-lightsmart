#!/usr/bin/env python3
"""Turn a folder of wide, short GIFs into the effects the app ships.

The vendor's library serves its designs as GIFs ten or more pixels per
LED -- 700x70, 1300x100, 1563x123. This scales them to the strip's 65x5
and writes each one as the bytes the keyboard is sent, gzipped, which is
about a hundredth of the GIF.

Run it when the folder changes; the result is committed:

    tools/convert_effects.py ~/Projetos/efeitos-kt68

Needs Qt, which the app depends on anyway.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mk_k68_lightsmart import image                      # noqa: E402

#: Frames to aim for. The keyboard takes 15 ms a block, so a 390-frame
#: animation is a ninety-second upload nobody waits through.
TARGET_FRAMES = 120

#: Titles for the files, since a filename is not a label.
TITLES = {
    "aquarium": "Aquarium",
    "cian-matrix": "Cyan code",
    "emoji": "Emoji",
    "flowers": "Flowers",
    "gameover": "Game over",
    "gamer": "Gamer",
    "invaders": "Invaders",
    "matrix-plus": "Code plus",
    "nyancat": "Nyan cat",
    "pacman": "Pac-chase",
    "purple": "Purple drift",
    "robot": "Robot",
    "snake-game": "Snake",
    "sound": "Sound bars",
    "space": "Space",
    "tetris-mount": "Tetris wall",
    "vertical-matrix": "Vertical code",
    "locomotive": "Locomotive",
    "sunset": "Sunset",
    ">>>": "Arrows",
}


def convert(source: Path, out_dir: Path, target: int) -> dict:
    peek = image.load(str(source), limit=1)
    stride = max(1, -(-peek.available // target))
    loaded = image.load(str(source), stride=stride)

    key = source.stem.replace(">", "arrow") or source.stem
    key = "arrows" if key.startswith("arrow") else key
    raw = b"".join(frame.encode(255) for frame in loaded.frames)
    path = out_dir / f"{key}.strip.gz"
    path.write_bytes(gzip.compress(raw, 9))

    return {
        "key": key,
        "title": TITLES.get(source.stem, source.stem.replace("-", " ").title()),
        "file": path.name,
        "frames": len(loaded.frames),
        "source_frames": peek.available,
        "source_size": list(peek.size),
        "stride": stride,
        "delay_ms": loaded.delay_ms,
        "bytes": path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="folder of GIFs")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "mk_k68_lightsmart" / "effects")
    parser.add_argument("--target-frames", type=int, default=TARGET_FRAMES)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    entries = []
    for source in sorted(args.source.glob("*.gif")):
        entry = convert(source, args.out, args.target_frames)
        entries.append(entry)
        print(f"{entry['key']:<16} {entry['frames']:>4} frames "
              f"(of {entry['source_frames']}, every {entry['stride']})  "
              f"{entry['bytes'] / 1024:>6.0f} KB")

    index = {"effects": sorted(entries, key=lambda e: e["title"])}
    (args.out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    total = sum(e["bytes"] for e in entries)
    print(f"\n{len(entries)} effects, {total / 1024:.0f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
