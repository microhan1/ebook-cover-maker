"""The font subsets: coverage, layering, packed files, and pixels equal to the original collections.

    python -m unittest tests.test_fonts -v

The pixel comparison needs fontbuild/ (python tools/subset_fonts.py) and fonts/*.ttc, and is skipped
without them. The quick sample here is every 11th character; python tools/verify_fonts.py checks all.
"""
from __future__ import annotations

import lzma
import os
import shutil
import sys
import tempfile
import unittest

import helpers  # noqa: F401  (first: settings in a temp folder, no network)

from PIL import ImageFont

import presets
import render

ROOT = helpers.ROOT
TTC_OK = all(os.path.isfile(os.path.join(ROOT, "fonts", f)) for f in presets.FONT_FILES.values())
SUBSET_OK = all(
    os.path.isfile(os.path.join(ROOT, "fontbuild", f"{presets.FONT_STEMS[k]}-{n}.otf"))
    for k in presets.FONT_KEYS for n in presets.FACE_NAMES.values()
)


def hangul_syllables() -> str:
    return "".join(chr(c) for c in range(0xAC00, 0xD7A4))


class Coverage(unittest.TestCase):
    def test_fonts_available(self):
        self.assertEqual(render.fonts_available(), [])

    def test_all_hangul_syllables_in_every_font_and_face(self):
        text = hangul_syllables()
        for key in presets.FONT_KEYS:
            for index in presets.FACE_NAMES:
                runs, missing = render.split_runs(text, key, index)
                self.assertEqual(missing, "", f"{key} face {index}")
                self.assertTrue(all(k == key for _, k, _ in runs), f"{key} face {index} fell back to another font")

    def test_everyday_text_in_every_language(self):
        samples = ["책갈피 · 전자책 — “표지” ‘만들기’", "The Quick Brown Fox, 123 & 456 (2026)",
                   "吾輩は猫である ひらがな カタカナ 夏目漱石", "红楼梦 曹雪芹 人民文学出版社",
                   "三國志 演義 桃園結義", "Müller Ñandú Éditions Ωmega Привет"]
        for text in samples:
            for key in presets.FONT_KEYS:
                for index in presets.FACE_NAMES:
                    _, missing = render.split_runs(text, key, index)
                    self.assertEqual(missing, "", f"{text!r} {key} {index}")

    def test_characters_in_no_font_are_reported_not_drawn_silently(self):
        _, missing = render.split_runs("가\U00020000나\U0001f431다", "sans", 1)
        self.assertEqual(missing, "\U00020000\U0001f431")
        r = render.render(render.Options(title="\U00020000 ok", author="a", publisher="p"))
        self.assertIn("warn_font_missing", dict(r.warnings))

    def test_spaces_never_use_notdef(self):
        for index in presets.FACE_NAMES:
            runs, missing = render.split_runs("A B 한 글", "sans", index)
            self.assertEqual(missing, "")
            tx = render.Text("sans", index, 40)
            ref = render.load_font("sans", presets.BASE_FACE, 40)
            self.assertAlmostEqual(tx.width("A B"), ref.getlength("A B"), places=3)

    def test_layers(self):
        layers = render.font_layers("sans", 0)
        if SUBSET_OK and os.environ.get("ECM_FONT_SOURCE") != "ttc":
            self.assertEqual(len(layers), 2)         # JP differences, then the KR file
            self.assertEqual(len(render.font_layers("sans", 1)), 1)
        else:
            self.assertEqual(len(layers), 1)


@unittest.skipUnless(TTC_OK and SUBSET_OK, "needs fonts/*.ttc and fontbuild/ (python tools/subset_fonts.py)")
class SameAsOriginal(unittest.TestCase):
    SIZES = (30, 90)

    def test_every_11th_character_pixel_identical(self):
        for key in presets.FONT_KEYS:
            ttc = os.path.join(ROOT, "fonts", presets.FONT_FILES[key])
            path, ttc_index = render.font_layers(key, presets.BASE_FACE)[-1]
            cps = sorted(render._cmap(path, ttc_index))[::11]
            self.assertGreater(len(cps), 1500)
            for index in presets.FACE_NAMES:
                for size in self.SIZES:
                    orig = ImageFont.truetype(ttc, size, index=index)
                    bad = []
                    for cp in cps:
                        ch = chr(cp)
                        (run,), missing = render.split_runs(ch, key, index)
                        f = render.load_font(run[1], index, size, run[2])
                        a, b = f.getmask(ch, anchor="ls"), orig.getmask(ch, anchor="ls")
                        if missing or f.getlength(ch) != orig.getlength(ch) or a.size != b.size or bytes(a) != bytes(b):
                            bad.append(ch)
                    self.assertEqual(bad, [], f"{key} face {index} size {size}")

    def test_jp_and_sc_use_their_own_glyphs_where_they_differ(self):
        jp_path, jp_n = render.font_layers("sans", 0)[0]
        sc_path, sc_n = render.font_layers("sans", 2)[0]
        jp_only = sorted(c for c in render._cmap(jp_path, jp_n) if c >= 0x4E00)
        sc_only = sorted(c for c in render._cmap(sc_path, sc_n) if c >= 0x4E00)
        self.assertGreater(len(jp_only), 200)
        self.assertGreater(len(sc_only), 2000)
        ch = chr(sc_only[len(sc_only) // 2])
        self.assertEqual(render._locate("sans", 2, ch), 0)          # SC face: its own file
        self.assertEqual(render._locate("sans", 1, ch), 0)          # KR face has a single file
        shapes = {bytes(render.load_font("sans", 2, 120, 0).getmask(ch)),
                  bytes(render.load_font("sans", 1, 120, 0).getmask(ch))}
        self.assertEqual(len(shapes), 2)                            # and the shapes differ
        self.assertEqual(render._locate("sans", 0, "あ"), 1)        # kana: same glyph, comes from the KR file
        self.assertEqual(render._locate("sans", 0, chr(jp_only[0])), 0)

    def test_metrics_equal_original(self):
        for key in presets.FONT_KEYS:
            ttc = os.path.join(ROOT, "fonts", presets.FONT_FILES[key])
            for index in presets.FACE_NAMES:
                self.assertEqual(render.Text(key, index, 100).font.getmetrics(),
                                 ImageFont.truetype(ttc, 100, index=index).getmetrics())


class Packed(unittest.TestCase):
    """The exe carries <name>.otf.xz and unpacks it the first time a font is needed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ecm_fonts_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._saved = (render.FONT_DIRS, dict(render._layers_cache), dict(render._font_cache))
        self.addCleanup(self._restore)

    def _restore(self):
        render.FONT_DIRS = self._saved[0]
        render._layers_cache.clear()
        render._layers_cache.update(self._saved[1])
        render._font_cache.clear()
        render._font_cache.update(self._saved[2])

    @unittest.skipUnless(SUBSET_OK, "needs fontbuild/")
    def test_unpack_from_xz(self):
        src = os.path.join(ROOT, "fontbuild")
        folder = os.path.join(self.tmp, "fonts")
        os.makedirs(folder)
        for key in presets.FONT_KEYS:
            for name in presets.FACE_NAMES.values():
                fn = f"{presets.FONT_STEMS[key]}-{name}.otf"
                with open(os.path.join(src, fn), "rb") as f, lzma.open(os.path.join(folder, fn + ".xz"), "wb") as g:
                    g.write(f.read())
        render.FONT_DIRS = (folder,)
        render._layers_cache.clear()
        render._font_cache.clear()
        os.environ.pop("ECM_FONT_SOURCE", None)
        self.assertEqual(render.fonts_available(), [])
        layers = render.font_layers("serif", 0)
        self.assertEqual(len(layers), 2)
        for path, _ in layers:
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(path.endswith(".xz"))
        runs, missing = render.split_runs("책갈피 直 あ", "serif", 0)
        self.assertEqual(missing, "")
        tx = render.Text("serif", 0, 50)
        self.assertGreater(tx.width("책갈피 直 あ"), 100)

    def test_damaged_xz_is_a_missing_font_not_a_crash(self):
        folder = os.path.join(self.tmp, "fonts")
        os.makedirs(folder)
        for key in presets.FONT_KEYS:
            for name in presets.FACE_NAMES.values():
                with open(os.path.join(folder, f"{presets.FONT_STEMS[key]}-{name}.otf.xz"), "wb") as f:
                    f.write(b"not an xz file")
        render.FONT_DIRS = (folder,)
        render._layers_cache.clear()
        render._font_cache.clear()
        saved_dir = render.FONT_DIR
        render.FONT_DIR = os.path.join(self.tmp, "nowhere")
        self.addCleanup(setattr, render, "FONT_DIR", saved_dir)
        self.assertEqual(sorted(render.fonts_available()), sorted(presets.FONT_KEYS))
        with self.assertRaises(render.FontMissing):
            render.Text("sans", 1, 20)


if __name__ == "__main__":
    unittest.main()
