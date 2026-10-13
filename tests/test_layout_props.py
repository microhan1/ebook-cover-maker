"""Property-style layout tests: seeded random titles, every template and size.

What must always hold, whatever the text:
  * wrapping loses and duplicates no characters
  * nothing is drawn outside the cover (unless the title warning says it cannot fit)
  * text lines never overlap each other, and nothing but the publisher enters
    the bottom 10% zone
  * the publisher name is the only thing that changes in the bottom 10% zone
    when it is added, checked on real pixels
"""
from __future__ import annotations

import random
import unittest

import helpers  # noqa: F401  (must come first)
from PIL import ImageChops

import presets
import render
from helpers import make_photo, overlaps, record_draws
from render import Options

SCRIPTS = {
    "ko": "가나다라마바사아자차카타파하별빛달님꽃잎",
    "ja": "あいうえおかきくけこ夜星物語覚書愛",
    "zh": "数星星的夜晚与我们遗忘的十二个故事",
    "en": "The quick brown fox jumps over the lazy dog",
    "mixed": "Python 입문 第3版 & 実践ガイド (2026)",
}


def random_text(rng: random.Random, lo: int, hi: int) -> str:
    n = rng.randint(lo, hi)
    pool = SCRIPTS[rng.choice(list(SCRIPTS))]
    out = []
    for _ in range(n):
        out.append(rng.choice(pool))
        if rng.random() < 0.08:
            out.append(" ")
    return "".join(out).strip() or "x"


class WrapProperties(unittest.TestCase):
    def test_wrap_preserves_characters_and_respects_width(self):
        rng = random.Random(20260922)
        measure = lambda s: sum(18 if ord(c) > 0x2E80 else 9 for c in s)  # noqa: E731
        for _ in range(400):
            text = random_text(rng, 1, 90)
            if rng.random() < 0.2:
                text = text[: len(text) // 2] + "\n" + text[len(text) // 2:]
            width = rng.choice([40, 90, 200, 333])
            lines = render.wrap(text, measure, width)
            self.assertEqual("".join(lines).replace(" ", ""), text.replace("\n", "").replace(" ", ""), text)
            for ln in lines:
                self.assertTrue(measure(ln) <= width or len(ln) == 1, (text, ln, width))
                self.assertEqual(ln, ln.strip(), (text, ln))

    def test_wrap_edge_inputs(self):
        measure = lambda s: len(s) * 10  # noqa: E731
        self.assertEqual(render.wrap("", measure, 100), [""])
        self.assertEqual(render.wrap("   ", measure, 100), [""])
        self.assertEqual(render.wrap("\n\n", measure, 100), [""])
        self.assertEqual(render.wrap("a" * 50, measure, 5), ["a"] * 50)             # box narrower than a glyph
        self.assertEqual(render.wrap("a b", measure, 0), ["a", "b"])
        self.assertEqual(render.wrap("a\r\nb", measure, 100), ["a", "b"])


class LayoutProperties(unittest.TestCase):
    def _check(self, opts: Options, label: str):
        with record_draws() as rects:
            r = render.render(opts)
        rects = [rc for rc in rects if rc["text"].strip()]          # spaces are gaps, not ink
        w, h = r.image.size
        zone_top = int(h * (1 - presets.PUBLISHER_ZONE))
        warned = {k for k, _ in r.warnings}
        title_ok = "warn_title_long" not in warned
        if not title_ok:
            return r
        pub = " ".join(opts.publisher.split())             # the wrapper collapses runs of spaces
        for rc in rects:
            self.assertGreaterEqual(rc["x0"], -1, (label, rc))
            self.assertLessEqual(rc["x1"], w + 1, (label, rc))
            self.assertGreaterEqual(rc["y0"], -1, (label, rc))
            self.assertLessEqual(rc["y1"], h + 1, (label, rc))
            is_pub = rc["text"] and rc["text"] in pub and rc["y0"] >= zone_top - 2
            if not is_pub:
                self.assertLessEqual(rc["y1"], zone_top + 1, (label, "text enters the publisher zone", rc))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(overlaps(a, b), (label, a["text"], b["text"], a, b))
        return r

    def test_random_covers_stay_inside_and_never_collide(self):
        rng = random.Random(7)
        cases = 0
        for _ in range(160):
            tpl = rng.choice(presets.TEMPLATE_IDS)
            preset = rng.choice(["upaper", "general", "square"])
            opts = Options(
                title=random_text(rng, 1, 40),
                subtitle=random_text(rng, 0, 26) if rng.random() < 0.7 else "",
                author=random_text(rng, 1, 14),
                publisher=random_text(rng, 2, 14),
                series=random_text(rng, 1, 10) if rng.random() < 0.4 else "",
                volume=str(rng.randint(1, 20)) if rng.random() < 0.3 else "",
                template=tpl, preset=preset, color=rng.randint(1, 8),
                font=rng.choice(presets.FONT_KEYS))
            self._check(opts, f"{tpl}/{preset}/{opts.title!r}")
            cases += 1
        self.assertEqual(cases, 160)

    def test_long_subtitle_author_series_do_not_run_into_other_text(self):
        """Over-long optional fields must be cut (with a warning), not spill onto the publisher."""
        for tpl in presets.TEMPLATE_IDS:
            for field in ("subtitle", "author", "series"):
                opts = Options(title="제목", author="저자", publisher="유페이퍼", template=tpl)
                setattr(opts, field, "아주 긴 문장 " * 40)
                r = self._check(opts, f"{tpl}/{field}")
                keys = {k for k, _ in r.warnings}
                self.assertIn(f"warn_{field}_long", keys, (tpl, field, keys))

    def test_nothing_changes_above_the_zone_when_publisher_is_added(self):
        """Pixel level proof of "publisher stays in the bottom 10%"."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(os.path.join(tmp, "p.png"), "gradient")
            for tpl in presets.TEMPLATE_IDS:
                for preset in ("upaper", "general", "square"):
                    for color in range(1, 9):
                        for bg in ("", photo):
                            kw = dict(title="별을 세는 밤에 우리가 잊은 이야기", subtitle="부제", author="저자",
                                      series="시리즈", template=tpl, preset=preset, color=color, bg_image=bg)
                            with_pub = render.render(Options(publisher="유페이퍼 출판", **kw)).image
                            without = render.render(Options(publisher="", **kw)).image
                            diff = ImageChops.difference(with_pub, without).getbbox()
                            label = (tpl, preset, color, bool(bg))
                            self.assertIsNotNone(diff, ("publisher invisible", label))
                            self.assertGreaterEqual(diff[1], int(with_pub.height * 0.9), ("publisher leaks", label, diff))


class VerticalTemplate(unittest.TestCase):
    def test_long_unbroken_word_stays_inside_the_title_area(self):
        for word in ("Supercalifragilisticexpialidocious", "Pneumonoultramicroscopicsilicovolcanoconiosis",
                     "A" * 200, "https://example.com/a/very/long/path/that/never/breaks"):
            with record_draws() as rects:
                r = render.render(Options(title=word, author="a", publisher="p", template=3))
            h = r.image.height
            for rc in rects:
                if rc["rotated"] or rc["y0"] < h * 0.7:
                    self.assertLessEqual(rc["y1"], h * 0.71, (word[:20], rc))
                    self.assertGreaterEqual(rc["x0"], -1)
                    self.assertLessEqual(rc["x1"], r.image.width + 1)

    def test_vertical_title_keeps_every_character(self):
        title = "별을 세는 밤에 우리가 잊어버린 이야기"
        with record_draws() as rects:
            render.render(Options(title=title, author="a", publisher="p", template=3))
        drawn = "".join(rc["text"] for rc in rects if rc["y0"] < 1200 * 0.71 and rc["x0"] < 400)
        self.assertEqual(sorted(drawn.replace(" ", "")), sorted(title.replace(" ", "")))


if __name__ == "__main__":
    unittest.main()
