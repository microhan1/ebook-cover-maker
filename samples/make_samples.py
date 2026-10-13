"""Make the README image (docs/templates.png): the four templates in a 2x2
grid, plus a generated gradient "photo" for template 4. Nothing copyrighted.

    python samples/make_samples.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

import render  # noqa: E402
from render import Options  # noqa: E402

TEXT = dict(title="별을 세는 밤에 우리가 잊어버린 이야기", subtitle="첫 책을 내는 작가를 위한 안내",
            author="홍길동", publisher="유페이퍼", series="책갈피 시리즈", volume="1권")
COLORS = {1: 1, 2: 3, 3: 8, 4: 2}
THUMB = (400, 600)
GAP = 20


def gradient_photo(path: str, w: int = 1200, h: int = 1800) -> None:
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        k = y / h
        d.line([(0, y), (w, y)], fill=(int(30 + 120 * k), int(60 + 90 * (1 - k)), int(120 + 100 * k)))
    for i in range(0, w, 80):                       # a few soft diagonal stripes
        d.line([(i, 0), (i + h // 3, h)], fill=(255, 255, 255), width=6)
    img.save(path, quality=90)


def main() -> None:
    samples = os.path.join(ROOT, "samples")
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    photo = os.path.join(samples, "sample_bg.jpg")
    gradient_photo(photo)
    sheet = Image.new("RGB", (GAP * 3 + THUMB[0] * 2, GAP * 3 + THUMB[1] * 2), "#ffffff")
    for template in (1, 2, 3, 4):
        opts = Options(template=template, color=COLORS[template], **TEXT)
        if template == 4:
            opts.bg_image = photo
        result = render.render(opts)
        assert render.publisher_in_zone(result)
        result.image.save(os.path.join(samples, f"template_{template}.jpg"), quality=90)
        col, row = (template - 1) % 2, (template - 1) // 2
        sheet.paste(result.image.resize(THUMB, Image.Resampling.LANCZOS),
                    (GAP + col * (THUMB[0] + GAP), GAP + row * (THUMB[1] + GAP)))
    out = os.path.join(docs, "templates.png")
    sheet.save(out, optimize=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
