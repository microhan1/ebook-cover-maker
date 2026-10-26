"""Cut the three bundled Noto CJK collections down to what a cover can use.

The collections in fonts/ (Sans Regular, Sans Bold, Serif Regular) hold five regional faces each and
about 44,800 characters per face, 66 MB in all. A cover only ever picks the JP, KR and SC faces
(render.face_index) and needs far fewer characters, so this script writes small OpenType files into
fontbuild/ and, packed with LZMA, into fontbuild/pack/. build.bat bundles the packed ones instead of the
collections.

    python tools/subset_fonts.py            # everything, nine files in parallel (about 4 minutes)
    python tools/subset_fonts.py --list     # the character sets
    python tools/subset_fonts.py sans-kr    # one file
    python tools/subset_fonts.py --pack     # only (re)pack fontbuild/*.otf into fontbuild/pack/

Per font (sans, sans-bold, serif) three files:

    <font>-kr.otf   Latin, symbols, kana, all 11,172 Hangul syllables and the jamo, and the Han
                    characters below, drawn with the KR glyphs
    <font>-jp.otf   only the characters whose JP glyph differs from the KR one (the outlines are compared)
    <font>-sc.otf   only the characters whose SC glyph differs from the KR one

A JP or SC cover takes a character from its own file when it has one and from the KR file otherwise, which
is the glyph the original collection holds for that face too, so everything the files cover is drawn pixel
for pixel like the original (tools/verify_fonts.py checks that on real pixels).

Han characters kept: every hanja of KS X 1001 (4,888), every kanji of JIS X 0208 (levels 1 and 2, 6,355),
every hanzi of GB 2312 (6,763) and the 5,401 common characters of Big5 (level 1, Traditional Chinese).
Hinting stays, the layout tables (GSUB, GPOS, vertical metrics) go: Pillow lays text out with FreeType
alone here. The names are untouched; the fonts have no Reserved Font Name and the license text ships in
the exe.
"""
from __future__ import annotations

import lzma
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "fonts")
OUT = os.path.join(ROOT, "fontbuild")
PACK = os.path.join(OUT, "pack")

# bundled font key -> collection file (same as presets.FONT_FILES)
COLLECTIONS = {
    "sans": "NotoSansCJK-Regular.ttc",
    "sans_bold": "NotoSansCJK-Bold.ttc",
    "serif": "NotoSerifCJK-Regular.ttc",
}
# face name -> index in the collection (same as presets.FACE_NAMES)
FACES = {"jp": 0, "kr": 1, "sc": 2}
LICENSES = ("LICENSE-Sans.txt", "LICENSE-Serif.txt")

LATIN_AND_SYMBOLS = [
    (0x20, 0x7E), (0xA0, 0x24F), (0x250, 0x2FF), (0x370, 0x3FF), (0x400, 0x4FF),
    (0x2000, 0x206F), (0x2070, 0x209F), (0x20A0, 0x20CF), (0x2100, 0x218F), (0x2190, 0x21FF),
    (0x2200, 0x22FF), (0x2300, 0x23FF), (0x2460, 0x24FF), (0x2500, 0x25FF), (0x2600, 0x26FF),
    (0x2700, 0x27BF), (0x2E80, 0x2FDF), (0x3000, 0x30FF), (0x3100, 0x312F), (0x31F0, 0x31FF),
    (0x3200, 0x33FF), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF),
]
HANGUL = [(0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7A3)]


def _range_set(ranges) -> set[int]:
    out: set[int] = set()
    for lo, hi in ranges:
        out.update(range(lo, hi + 1))
    return out


def _han_of(codec: str, rows) -> set[int]:
    """Han characters (U+3400 and up) of a legacy double-byte charset; rows = (lead, first, last trail)."""
    out: set[int] = set()
    for lead, lo, hi in rows:
        for trail in range(lo, hi + 1):
            try:
                s = bytes([lead, trail]).decode(codec)
            except UnicodeDecodeError:
                continue
            if len(s) == 1 and ord(s) >= 0x3400:
                out.add(ord(s))
    return out


def han_sets() -> dict[str, set[int]]:
    ks = _han_of("euc_kr", [(a, 0xA1, 0xFE) for a in range(0xA1, 0xFE)])
    jis = _han_of("shift_jis", [(a, 0x40, 0xFC) for a in range(0x81, 0xEB)])
    gb = _han_of("gb2312", [(a, 0xA1, 0xFE) for a in range(0xA1, 0xF8)])
    big5 = _han_of("big5", [(a, 0x40, 0xFE) for a in range(0xA4, 0xC6)] + [(0xC6, 0x40, 0x7E)])
    return {"KS X 1001": ks, "JIS X 0208": jis, "GB 2312": gb, "Big5 level 1": big5}


def charset() -> set[int]:
    """Everything the kr file holds; the jp and sc files hold the part of it whose glyph differs."""
    out = _range_set(LATIN_AND_SYMBOLS) | _range_set(HANGUL)
    for s in han_sets().values():
        out |= s
    return out


def out_name(key: str, face: str) -> str:
    return f"{key.replace('_', '-')}-{face}.otf"


def _open(key: str, face: str):
    from fontTools.ttLib import TTFont

    return TTFont(os.path.join(SRC, COLLECTIONS[key]), fontNumber=FACES[face], lazy=True)


def _signatures(font, cps) -> dict[int, tuple]:
    from fontTools.pens.recordingPen import RecordingPen

    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    sig = {}
    for cp in cps:
        name = cmap.get(cp)
        if name is None:
            continue
        g = gs[name]
        pen = RecordingPen()
        g.draw(pen)
        sig[cp] = (g.width, tuple(pen.value))
    return sig


def wanted(key: str, face: str) -> set[int]:
    """Code points the file for (key, face) must hold."""
    allc = charset()
    if face == "kr":
        cmap = _open(key, "kr").getBestCmap()
        return {c for c in allc if c in cmap}
    kr, own = _signatures(_open(key, "kr"), allc), _signatures(_open(key, face), allc)
    return {c for c, s in own.items() if kr.get(c) != s}


def build_one(key: str, face: str) -> str:
    from fontTools import subset

    cps = wanted(key, face)
    font = _open(key, face)
    opts = subset.Options()                   # hinting stays (pixel-identical output)
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = False
    opts.layout_features = []                 # no GSUB/GPOS: Pillow's basic layout does not read them
    opts.drop_tables = list(opts.drop_tables) + ["vhea", "vmtx", "VORG", "BASE", "GDEF", "GSUB", "GPOS"]
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=sorted(cps))
    sub.subset(font)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, out_name(key, face))
    tmp = path + ".tmp"
    font.save(tmp)
    os.replace(tmp, path)
    return path


def pack() -> None:
    """LZMA-pack fontbuild/*.otf for the exe (render unpacks a file the first time it needs it)."""
    os.makedirs(PACK, exist_ok=True)
    filters = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 64 << 20, "lc": 4, "lp": 0, "pb": 0}]
    for key in COLLECTIONS:
        for face in FACES:
            name = out_name(key, face)
            with open(os.path.join(OUT, name), "rb") as f:
                data = f.read()
            with open(os.path.join(PACK, name + ".xz"), "wb") as f:
                f.write(lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, filters=filters))
    for lic in LICENSES:
        shutil.copyfile(os.path.join(SRC, lic), os.path.join(PACK, lic))


def report() -> None:
    raw = packed = 0
    for key in COLLECTIONS:
        for face in FACES:
            name = out_name(key, face)
            r = os.path.getsize(os.path.join(OUT, name)) if os.path.isfile(os.path.join(OUT, name)) else 0
            p = os.path.join(PACK, name + ".xz")
            c = os.path.getsize(p) if os.path.isfile(p) else 0
            raw += r
            packed += c
            print(f"{name:18s} {r / 1e6:6.2f} MB   packed {c / 1e6:6.2f} MB")
    print(f"{'total':18s} {raw / 1e6:6.2f} MB   packed {packed / 1e6:6.2f} MB")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--list" in argv:
        for name, s in han_sets().items():
            print(f"{name:20s} {len(s):6d}")
        print(f"{'kr file, requested':20s} {len(charset()):6d}")
        return 0
    if "--pack" in argv:
        pack()
        report()
        return 0
    jobs = [(k, f) for k in COLLECTIONS for f in FACES]
    if argv:
        jobs = [(k, f) for k, f in jobs if out_name(k, f)[:-4] in argv]
    if len(jobs) > 1:
        procs = [(j, subprocess.Popen([sys.executable, __file__, out_name(*j)[:-4]])) for j in jobs]
        bad = [j for j, p in procs if p.wait() != 0]
        if bad:
            print("failed:", bad)
            return 1
        pack()
        report()
        return 0
    for key, face in jobs:
        build_one(key, face)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
