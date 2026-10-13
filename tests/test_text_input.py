"""Hostile and unusual text: it must render, never crash, and look right."""
from __future__ import annotations

import time
import unicodedata
import unittest

import helpers  # noqa: F401  (must come first)
from PIL import ImageChops

import render
from render import Options


def img(**kw):
    base = dict(title="제목", author="저자", publisher="유페이퍼")
    base.update(kw)
    return render.render(Options(**base))


class Normalization(unittest.TestCase):
    def test_decomposed_hangul_renders_like_composed(self):
        """Text from macOS (NFD) must not show up as separate jamo."""
        nfc = "한글 표지 만들기"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd)
        diff = ImageChops.difference(img(title=nfc).image, img(title=nfd).image).getbbox()
        self.assertIsNone(diff)

    def test_japanese_dakuten_composed_equals_decomposed(self):
        nfc = "ガギグゲゴ"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertIsNone(ImageChops.difference(img(title=nfc).image, img(title=nfd).image).getbbox())

    def test_control_characters_do_not_draw_boxes(self):
        clean = img(title="AB").image
        for dirty in ("A\x00B", "A\x01\x02B", "A\x7fB", "A\x1bB", "A﻿B"):
            self.assertIsNone(ImageChops.difference(clean, img(title=dirty).image).getbbox(), repr(dirty))

    def test_tabs_and_odd_whitespace_become_single_spaces(self):
        a = img(title="A B").image
        for ws in ("A\tB", "A B", "A　B", "A \t B"):
            self.assertIsNone(ImageChops.difference(a, img(title=ws).image).getbbox(), repr(ws))

    def test_lone_surrogates_do_not_crash(self):
        for t in ("\ud800", "a\udfffb", "\ud83d"):
            r = img(title=t, subtitle=t, author=t, publisher="유페이퍼")
            self.assertEqual(r.image.size, (800, 1200))


class Unusual(unittest.TestCase):
    CASES = {
        "emoji": "고양이 \U0001f431 와 함께",
        "zwj emoji": "\U0001f468‍\U0001f469‍\U0001f467",
        "arabic": "مرحبا بالعالم",
        "hebrew": "שלום עולם",
        "thai": "สวัสดีชาวโลก",
        "combining": "ééé",
        "vs16": "❤️",
        "half/fullwidth": "ＡＢＣ ｱｲｳ １２３",
        "hanja in korean": "三國志 삼국지",
        "kanji only": "吾輩は猫である",
        "newlines": "첫 줄\n둘째 줄\n\n넷째 줄",
        "only spaces": "   ",
        "single char": "책",
        "punctuation": "…!?—「」『』《》“”‘’",
        "rtl mark": "abc‏def",
        "huge digits": "9" * 400,
    }

    def test_all_render(self):
        for name, text in self.CASES.items():
            for tpl in (1, 2, 3, 4):
                r = img(title=text, subtitle=text, author=text, publisher="유페이퍼", template=tpl)
                self.assertEqual(r.image.size, (800, 1200), (name, tpl))
                self.assertTrue(render.publisher_in_zone(r), (name, tpl))

    def test_missing_glyphs_are_reported_not_hidden(self):
        for name in ("emoji", "arabic", "hebrew", "thai"):
            keys = {k for k, _ in img(title=self.CASES[name]).warnings}
            self.assertIn("warn_font_missing", keys, name)

    def test_supported_scripts_have_no_font_warning(self):
        for name in ("hanja in korean", "kanji only", "half/fullwidth", "punctuation", "newlines", "combining"):
            keys = {k for k, _ in img(title=self.CASES[name]).warnings}
            self.assertNotIn("warn_font_missing", keys, name)

    def test_blank_title_still_makes_a_cover(self):
        r = img(title="", subtitle="", author="")
        self.assertEqual(r.image.size, (800, 1200))


class Performance(unittest.TestCase):
    def test_long_inputs_stay_fast(self):
        for n in (500, 5000, 20000):
            t0 = time.perf_counter()
            r = img(title="긴 제목 " * n, subtitle="부제 " * n, author="저자 " * n, publisher="출판사 " * (n // 50))
            dt = time.perf_counter() - t0
            self.assertLess(dt, 5.0, f"{n} repeats took {dt:.1f}s")
            self.assertEqual(r.image.size, (800, 1200))

    def test_render_is_deterministic(self):
        a = render.to_bytes(img(title="결정성 시험", subtitle="same").image, "png")
        b = render.to_bytes(img(title="결정성 시험", subtitle="same").image, "png")
        self.assertEqual(a, b)

    def test_cover_text_does_not_depend_on_ui_language(self):
        import i18n

        outs = []
        for lang in i18n.LANGS:
            i18n.set_lang(lang, persist=False)
            outs.append(render.to_bytes(img(title="별 星 star", subtitle="s", series="x", volume="1").image, "png"))
        i18n.set_lang("ko", persist=False)
        self.assertEqual(len(set(outs)), 1)


if __name__ == "__main__":
    unittest.main()
