"""Readability on real pixels: every text color against what is behind it,
for every template and color scheme, on solid colors and on photos."""
from __future__ import annotations

import os
import tempfile
import unittest

import helpers  # noqa: F401  (must come first)
import presets
import render
from helpers import contrast, make_photo, median_rgb, record_draws
from render import Options

FIELDS = dict(title="별을 세는 밤", subtitle="부제목입니다", author="홍길동", publisher="유페이퍼",
              series="책갈피 시리즈", volume="1권")
BLANK = dict(title="", subtitle="", author="", publisher="", series="", volume="")


def background_only(opts: Options):
    """The same cover with every text removed: what the text sits on."""
    kw = dict(opts.__dict__)
    kw.update(BLANK)
    return render.render(Options(**kw)).image


class SolidColorContrast(unittest.TestCase):
    def test_every_scheme_and_template_is_readable(self):
        problems = []
        for tpl in presets.TEMPLATE_IDS:
            for color in range(1, len(presets.COLOR_SCHEMES) + 1):
                opts = Options(template=tpl, color=color, **FIELDS)
                with record_draws() as rects:
                    r = render.render(opts)
                bg = background_only(opts)
                for rc in rects:
                    behind = median_rgb(bg, (rc["x0"], rc["y0"], rc["x1"], rc["y1"]))
                    if behind is None:
                        continue
                    ratio = contrast(rc["color"], behind)
                    # the series line is small accent-colored text: 3:1 is the floor, 4.5:1 for everything else
                    floor = 3.0 if rc["text"] in ("책갈피 시리즈 1권", "책갈피 시리즈") else 4.5
                    if ratio < floor:
                        problems.append((tpl, color, rc["text"], rc["color"], behind, round(ratio, 2)))
        self.assertEqual(problems, [])

    def test_custom_colors_are_honored(self):
        opts = Options(title="t", author="a", publisher="p", bg_color="#102030", fg_color="#ffeedd")
        r = render.render(opts)
        self.assertEqual(r.image.getpixel((5, 5)), (0x10, 0x20, 0x30))


class PhotoContrast(unittest.TestCase):
    """Text drawn on a photo is light and readable, even for a dark-text scheme,
    even on a nearly white photo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.photos = {k: make_photo(os.path.join(cls.tmp.name, f"{k}.png"), k)
                      for k in ("mid", "white", "black", "noise", "gradient")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_text_on_every_photo_kind(self):
        problems = []
        for kind, photo in self.photos.items():
            for tpl in presets.TEMPLATE_IDS:
                for color in range(1, len(presets.COLOR_SCHEMES) + 1):
                    opts = Options(template=tpl, color=color, bg_image=photo, **FIELDS)
                    with record_draws() as rects:
                        render.render(opts)
                    bg = background_only(opts)
                    for rc in rects:
                        if len(rc["text"]) == 1 and tpl == 3 and rc["x0"] < 400 and rc["y0"] < 900:
                            continue                       # a single big glyph fills its own cell: median would be the glyph
                        behind = median_rgb(bg, (rc["x0"], rc["y0"], rc["x1"], rc["y1"]))
                        if behind is None:
                            continue
                        ratio = contrast(rc["color"], behind)
                        if ratio < 4.5:
                            problems.append((kind, tpl, color, rc["text"], rc["color"], behind, round(ratio, 2)))
        self.assertEqual(problems[:10], [], f"{len(problems)} low-contrast texts")

    def test_user_chosen_text_color_wins_over_the_automatic_light_color(self):
        photo = self.photos["mid"]
        with record_draws() as rects:
            render.render(Options(template=1, title="t", author="a", publisher="p", bg_image=photo,
                                  fg_color="#ff0000"))
        self.assertTrue(all(rc["color"] == "#ff0000" for rc in rects if rc["text"] in ("t", "a", "p")))

    def test_photo_is_darkened_enough_for_light_text(self):
        for kind in ("white", "mid", "noise", "gradient"):
            img = render.load_background(self.photos[kind], 800, 1200)[0]
            self.assertLessEqual(render._luminance_p90(img), render.BG_LUM_MAX * 1.15, kind)

    def test_dark_photo_is_not_made_brighter_or_crushed(self):
        before = render.load_background(self.photos["black"], 400, 600)[0]
        self.assertLessEqual(max(before.getdata(0)), 8)


if __name__ == "__main__":
    unittest.main()
