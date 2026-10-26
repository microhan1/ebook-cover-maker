"""Compare what the font subsets draw with what the original collections draw.

    python tools/verify_fonts.py             # every character the subsets cover, 3 fonts x 3 faces x 2 sizes
    python tools/verify_fonts.py --quick     # every 7th character, one size

For each character of each font and face, the glyph bitmap and the advance width from the subset files
(render.split_runs / render.load_font) are compared with the ones from the original collection in
fonts/. Prints how many characters differ; exit code 1 if any does.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from PIL import ImageFont  # noqa: E402

import presets  # noqa: E402
import render  # noqa: E402
import subset_fonts as sf  # noqa: E402


def covered(key: str) -> list[int]:
    """Code points the subsets of this font hold (the kr file's cmap)."""
    path, ttc_index = render.font_layers(key, presets.BASE_FACE)[-1]
    return sorted(render._cmap(path, ttc_index))


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    quick = "--quick" in argv
    sizes = (40,) if quick else (28, 96)
    bad_total = 0
    for key in presets.FONT_KEYS:
        ttc = os.path.join(sf.SRC, sf.COLLECTIONS[key])
        cps = covered(key)
        if quick:
            cps = cps[::7]
        for index in presets.FACE_NAMES:
            bad: list[int] = []
            for size in sizes:
                orig = ImageFont.truetype(ttc, size, index=index)
                for cp in cps:
                    ch = chr(cp)
                    runs, missing = render.split_runs(ch, key, index)
                    (_, k, layer), = runs
                    f = render.load_font(k, index, size, layer)
                    if missing or k != key:
                        bad.append(cp)
                        continue
                    if f.getlength(ch) != orig.getlength(ch):
                        bad.append(cp)
                        continue
                    a, b = f.getmask(ch, anchor="ls"), orig.getmask(ch, anchor="ls")
                    if a.size != b.size or bytes(a) != bytes(b):
                        bad.append(cp)
            bad = sorted(set(bad))
            bad_total += len(bad)
            layers = len(render.font_layers(key, index))
            print(f"{key:10s} {presets.FACE_NAMES[index]}  chars {len(cps):6d}  layers {layers}  differ {len(bad)}"
                  + (f"  e.g. {' '.join(chr(c) for c in bad[:12])}" if bad else ""))
    print("all identical" if not bad_total else f"{bad_total} differences")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
