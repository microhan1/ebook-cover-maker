"""Draw the same covers with the original font collections and with the subsets, and compare the pixels.

    python tools/compare_covers.py              # both, then the comparison table
    python tools/compare_covers.py --keep DIR   # also keep the pictures in DIR/ttc and DIR/subset

4 templates x 3 fonts x the text sets below, 800x1200. A set that reaches into characters the subsets do
not hold shows up as a changed warning (warn_font_missing) and a changed picture; that is listed too.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SETS = {
    "ko": ("책갈피 툴로 만든 전자책 표지", "한글 제목과 부제목입니다", "박문한", "유페이퍼"),
    "en": ("The Quick Brown Fox Jumps Over the Lazy Dog", "A Novel in Three Parts", "Jane Austen", "Penguin Books"),
    "ja": ("吾輩は猫である", "夏目漱石の長編小説", "夏目漱石", "新潮社"),
    "zh-CN": ("红楼梦", "中国古典文学四大名著", "曹雪芹", "人民文学出版社"),
    "zh-TW": ("紅樓夢", "中國古典文學四大名著", "曹雪芹", "人民文學出版社"),
    "ko-rare": ("똠방각하 뷁 쀍 꿿 뷁뷁", "쌻 쒦 퀁 똠", "뷁뷁", "똠방각하"),
    "ko-hanja": ("三國志 演義", "桃園結義", "羅貫中", "民音社"),
    "symbols": ("한글 · 제목 — “인용” ‘작은’ …", "①②③ ♥ ★ → ※", "A·B—C", "(주)책갈피"),
    "mixed": ("Hello 世界 こんにちは 안녕", "English 日本語 中文 한국어", "Müller Ñandú", "Éditions"),
    "rare-han": ("鱻 鬱 𠀀 𪚥 龘 麤", "龘龘 鱻", "𠀀", "𪚥"),
}


def render_all(out_dir: str) -> None:
    sys.path.insert(0, ROOT)
    import render

    os.makedirs(out_dir, exist_ok=True)
    info = {}
    for tpl in (1, 2, 3, 4):
        for font in ("sans", "sans_bold", "serif"):
            for name, (title, sub, author, pub) in SETS.items():
                o = render.Options(title=title, subtitle=sub, author=author, publisher=pub,
                                   template=tpl, font=font)
                r = render.render(o)
                key = f"t{tpl}.{font}.{name}"
                r.image.save(os.path.join(out_dir, key + ".png"))
                info[key] = {"warnings": [[k, v] for k, v in r.warnings],
                             "sha": hashlib.sha256(r.image.tobytes()).hexdigest()}
    with open(os.path.join(out_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if argv and argv[0] == "--render":
        render_all(argv[1])
        return 0
    keep = argv[argv.index("--keep") + 1] if "--keep" in argv else None
    base = keep or tempfile.mkdtemp(prefix="ecm_cmp_")
    dirs = {"ttc": os.path.join(base, "ttc"), "subset": os.path.join(base, "subset")}
    for mode, d in dirs.items():
        env = dict(os.environ, ECM_FONT_SOURCE="ttc" if mode == "ttc" else "subset")
        subprocess.run([sys.executable, __file__, "--render", d], check=True, env=env)
    a = json.load(open(os.path.join(dirs["ttc"], "info.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(dirs["subset"], "info.json"), encoding="utf-8"))
    from PIL import Image, ImageChops

    rows = {}
    for key in a:
        same = a[key]["sha"] == b[key]["sha"]
        wa = {k: v for k, v in a[key]["warnings"]}
        wb = {k: v for k, v in b[key]["warnings"]}
        diff = ""
        if not same:
            box = ImageChops.difference(Image.open(os.path.join(dirs["ttc"], key + ".png")),
                                        Image.open(os.path.join(dirs["subset"], key + ".png"))).getbbox()
            diff = str(box)
        rows[key] = (same, wa.get("warn_font_missing", {}).get("chars", ""),
                     wb.get("warn_font_missing", {}).get("chars", ""), wa == wb, diff)
    per_set: dict[str, list] = {}
    for key, r in rows.items():
        per_set.setdefault(key.split(".", 2)[2], []).append(r)
    print(f"{'set':10s} {'covers':>6s} {'identical':>9s} {'warn same':>9s}  missing chars before -> after")
    for name, rs in per_set.items():
        before = "".join(sorted(set("".join(r[1] for r in rs))))
        after = "".join(sorted(set("".join(r[2] for r in rs))))
        print(f"{name:10s} {len(rs):6d} {sum(r[0] for r in rs):9d} {sum(r[3] for r in rs):9d}  "
              f"'{before}' -> '{after}'")
    total = len(rows)
    same = sum(r[0] for r in rows.values())
    print(f"total {total} covers, {same} pixel-identical, {total - same} differ")
    print("pictures in", base)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
