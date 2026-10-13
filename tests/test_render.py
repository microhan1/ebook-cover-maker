"""Rendering rules: wrapping, publisher zone, warnings, saving, speed.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import presets  # noqa: E402
import render  # noqa: E402
from render import Options  # noqa: E402

KO20 = "별을 세는 밤에 우리가 잊은 이야기들"          # 20 characters incl. spaces
JA20 = "夜に星を数える十二の物語についての覚え書き"
ZH20 = "数星星的夜晚与我们遗忘的十二个故事集"
EN = "The Night We Counted Stars and the Twelve Stories We Forgot"


class WrapTests(unittest.TestCase):
    def test_cjk_breaks_anywhere_latin_at_spaces(self):
        measure = lambda s: len(s) * 10  # noqa: E731
        self.assertEqual(render.wrap("가나다라마", measure, 30), ["가나다", "라마"])
        self.assertEqual(render.wrap("ab cd ef", measure, 50), ["ab cd", "ef"])
        self.assertEqual(render.wrap("abcdefgh", measure, 30), ["abc", "def", "gh"])   # oversized word
        self.assertEqual(render.wrap("a\nb", measure, 100), ["a", "b"])

    def test_face_index_by_script(self):
        self.assertEqual(render.face_index("한글"), 1)
        self.assertEqual(render.face_index("かな漢字"), 0)
        self.assertEqual(render.face_index("汉字"), 2)
        self.assertEqual(render.face_index("Latin"), 1)


class LayoutTests(unittest.TestCase):
    def _render(self, **kw) -> render.RenderResult:
        return render.render(Options(**kw))

    def test_twenty_char_titles_are_not_shrunk_to_minimum(self):
        for title in (KO20, JA20, ZH20, EN):
            for template in presets.TEMPLATE_IDS:
                for preset in ("upaper", "general", "square"):
                    r = self._render(title=title, author="a", publisher="p", template=template, preset=preset)
                    keys = [k for k, _ in r.warnings]
                    self.assertNotIn("warn_title_long", keys, (title, template, preset))
                    self.assertEqual(r.image.size, presets.SIZE_PRESETS[preset])

    def test_publisher_always_in_bottom_zone(self):
        for template in presets.TEMPLATE_IDS:
            for preset in ("upaper", "general", "square"):
                for publisher in ("유페이퍼", "A Very Long Publisher Name For Testing Purposes Inc.", "出版社"):
                    r = self._render(title=KO20, author="a", publisher=publisher, template=template, preset=preset)
                    self.assertTrue(render.publisher_in_zone(r), (template, preset, publisher))
                    x0, y0, x1, y1 = r.publisher_box
                    h = r.image.height
                    self.assertGreaterEqual(y0, int(h * (1 - presets.PUBLISHER_ZONE)))
                    self.assertLessEqual(y1, h)

    def test_custom_size_is_clamped(self):
        r = self._render(title="t", author="a", publisher="p", preset="custom", width=50, height=99999)
        self.assertEqual(r.image.size, (presets.MIN_SIDE, presets.MAX_SIDE))

    def test_very_long_title_warns_but_renders(self):
        r = self._render(title="가" * 200, author="a", publisher="p")
        self.assertIn("warn_title_long", [k for k, _ in r.warnings])
        r = self._render(title="가" * 200, author="a", publisher="p", template=3)
        self.assertIn("warn_title_long", [k for k, _ in r.warnings])

    def test_missing_glyph_warns(self):
        r = self._render(title="고양이 \U0001f431", author="a", publisher="p")
        warn = dict(r.warnings)
        self.assertIn("warn_font_missing", warn)
        self.assertEqual(warn["warn_font_missing"]["chars"], "\U0001f431")

    def test_empty_optional_fields(self):
        r = self._render(title="t", author="", publisher="p", subtitle="", series="", volume="")
        self.assertEqual(r.image.size, (800, 1200))

    def test_invalid_options_fall_back(self):
        o = Options(preset="nope", template=9, color=0, font="x", fmt="gif", bg_color="red", width="a").validated()
        self.assertEqual((o.preset, o.template, o.color, o.font, o.fmt, o.bg_color), ("upaper", 1, 1, "sans_bold", "jpg", ""))


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _img(self, name: str, size: tuple[int, int]) -> str:
        p = os.path.join(self.tmp.name, name)
        Image.new("RGB", size, "#3366aa").save(p)
        return p

    def test_low_res_background_warns_and_original_untouched(self):
        p = self._img("small.png", (100, 150))
        before = hashlib.sha256(open(p, "rb").read()).hexdigest()
        r = render.render(Options(title="t", author="a", publisher="p", bg_image=p, template=4))
        self.assertIn("warn_bg_low_res", [k for k, _ in r.warnings])
        self.assertEqual(r.image.size, (800, 1200))
        self.assertEqual(before, hashlib.sha256(open(p, "rb").read()).hexdigest())

    def test_wide_background_is_cropped_not_squashed(self):
        p = self._img("wide.jpg", (3000, 2000))
        r = render.render(Options(title="t", author="a", publisher="p", bg_image=p))
        self.assertNotIn("warn_bg_low_res", [k for k, _ in r.warnings])
        self.assertEqual(r.image.size, (800, 1200))

    def test_unreadable_background_raises(self):
        p = os.path.join(self.tmp.name, "bad.jpg")
        open(p, "wb").write(b"not an image")
        with self.assertRaises(render.BackgroundOpenError):
            render.render(Options(title="t", author="a", publisher="p", bg_image=p))


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = render.render(Options(title="t", author="a", publisher="p")).image

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbering_never_overwrites(self):
        base = os.path.join(self.tmp.name, "cover.jpg")
        p1 = render.save_cover(self.img, base, "jpg")
        p2 = render.save_cover(self.img, base, "jpg")
        os.mkdir(os.path.join(self.tmp.name, "cover(3).jpg"))          # a folder squatting on the name
        p3 = render.save_cover(self.img, base, "jpg")
        self.assertEqual([os.path.basename(p) for p in (p1, p2, p3)], ["cover.jpg", "cover(2).jpg", "cover(4).jpg"])
        self.assertFalse(any(n.endswith(".part") for n in os.listdir(self.tmp.name)))

    def test_extension_follows_format(self):
        p = render.save_cover(self.img, os.path.join(self.tmp.name, "cover"), "png")
        self.assertTrue(p.endswith("cover.png"))
        with Image.open(p) as im:
            self.assertEqual(im.format, "PNG")
        p = render.save_cover(self.img, os.path.join(self.tmp.name, "x.png"), "jpg")
        self.assertTrue(p.endswith("x.jpg"))
        with Image.open(p) as im:
            self.assertEqual((im.format, im.mode), ("JPEG", "RGB"))


class SpeedTests(unittest.TestCase):
    def test_render_under_300ms_after_warmup(self):
        opts = Options(title=KO20, subtitle="부제", author="저자", publisher="유페이퍼", template=2)
        render.render(opts)
        t0 = time.perf_counter()
        for i in range(10):
            render.render(Options(**{**opts.__dict__, "title": KO20 + str(i), "template": i % 4 + 1}))
        avg = (time.perf_counter() - t0) / 10
        self.assertLess(avg, 0.3, f"average render {avg * 1000:.0f} ms")


if __name__ == "__main__":
    unittest.main()
