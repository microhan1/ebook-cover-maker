"""Command line: exit codes, messages in every language, bad input.

Every call passes --lang, so the user's settings.json can never influence a
test, and none of them is allowed to print a Python traceback.
NOTE: main.py with no title/author/publisher opens the GUI, so no test calls it that way.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

import helpers  # noqa: F401  (must come first)
import fixtures as fx
import i18n
from helpers import make_photo, run_cli, sha
from PIL import Image

BASE = ["--title", "제목", "--author", "저자", "--publisher", "유페이퍼"]


def lang_text(lang: str, key: str, **kw) -> str:
    with open(os.path.join(helpers.ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)[key].format(**kw)


class CliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def run_ok(self, *args, **kw):
        code, out, err = run_cli(*args, **kw)
        self.assertNotIn("Traceback", err, err[-600:])
        return code, out, err


class Success(CliBase):
    def test_minimal_run_in_every_language(self):
        for lang in i18n.LANGS:
            out_file = self.p(f"{lang}.jpg")
            code, out, err = self.run_ok("--lang", lang, *BASE, "-o", out_file)
            self.assertEqual(code, 0, (lang, err))
            self.assertEqual(out.strip(), lang_text(lang, "msg_saved", path=out_file).strip())
            with Image.open(out_file) as im:
                self.assertEqual((im.format, im.size, im.mode), ("JPEG", (800, 1200), "RGB"))

    def test_all_options_together(self):
        photo = make_photo(self.p("bg.jpg"), "gradient", (1000, 1500))
        code, out, err = self.run_ok("--lang", "en", "--title", "별을 세는 밤", "--subtitle", "부제", "--author", "홍길동",
                                     "--publisher", "유페이퍼", "--series", "시리즈", "--volume", "3권",
                                     "--preset", "general", "--template", "4", "--color", "6", "--font", "serif",
                                     "--bg", photo, "--format", "png", "-o", self.p("all.png"))
        self.assertEqual(code, 0, err)
        with Image.open(self.p("all.png")) as im:
            self.assertEqual((im.format, im.size), ("PNG", (1600, 2400)))

    def test_each_preset_and_template(self):
        for preset, size in (("upaper", (800, 1200)), ("general", (1600, 2400)), ("square", (1200, 1200))):
            for tpl in ("1", "2", "3", "4"):
                code, _, err = self.run_ok("--lang", "en", *BASE, "--preset", preset, "--template", tpl,
                                           "-o", self.p(f"{preset}{tpl}.jpg"))
                self.assertEqual(code, 0, err)
                with Image.open(self.p(f"{preset}{tpl}.jpg")) as im:
                    self.assertEqual(im.size, size)

    def test_custom_size_is_clamped_into_range(self):
        code, _, _ = self.run_ok("--lang", "en", *BASE, "--preset", "custom", "--width", "5", "--height", "99999",
                                 "-o", self.p("c.jpg"))
        self.assertEqual(code, 0)
        with Image.open(self.p("c.jpg")) as im:
            self.assertEqual(im.size, (300, 4000))

    def test_output_naming(self):
        out = self.p("deep", "er", "cover.jpg")
        self.assertEqual(self.run_ok("--lang", "en", *BASE, "-o", out)[0], 0)
        self.assertTrue(os.path.isfile(out))
        first = sha(out)
        self.assertEqual(self.run_ok("--lang", "en", *BASE, "--template", "2", "-o", out)[0], 0)
        self.assertEqual(sha(out), first, "an existing file was overwritten")
        self.assertTrue(os.path.isfile(self.p("deep", "er", "cover(2).jpg")))
        self.assertEqual(self.run_ok("--lang", "en", *BASE, "--format", "png", "-o", self.p("x.jpg"))[0], 0)
        self.assertTrue(os.path.isfile(self.p("x.png")))

    def test_output_that_is_a_folder_gets_cover_inside(self):
        os.mkdir(self.p("realdir"))
        self.assertEqual(self.run_ok("--lang", "en", *BASE, "-o", self.p("realdir"))[0], 0)
        self.assertTrue(os.path.isfile(self.p("realdir", "cover.jpg")))
        self.assertFalse(os.path.exists(self.p("realdir.jpg")))
        self.assertEqual(self.run_ok("--lang", "en", *BASE, "--format", "png", "-o", self.p("newdir") + os.sep)[0], 0)
        self.assertTrue(os.path.isfile(self.p("newdir", "cover.png")))

    def test_unicode_everywhere(self):
        out = self.p("한글 폴더", "表紙 #1 & 2.jpg")
        for lang, title in (("ko", "한국어 제목"), ("ja", "日本語のタイトル"), ("zh-CN", "中文标题")):
            code, stdout, err = self.run_ok("--lang", lang, "--title", title, "--author", title, "--publisher", title,
                                            "--subtitle", title, "-o", out)
            self.assertEqual(code, 0, err)
            self.assertIn("表紙 #1 & 2", stdout)
        self.assertTrue(os.path.isfile(out))

    def test_decomposed_hangul_argument(self):
        import unicodedata

        a = self.p("a.png")
        b = self.p("b.png")
        t = "한글 표지"
        self.run_ok("--lang", "en", "--title", t, "--author", "a", "--publisher", "p", "-o", a)
        self.run_ok("--lang", "en", "--title", unicodedata.normalize("NFD", t), "--author", "a", "--publisher", "p", "-o", b)
        self.assertEqual(Image.open(a).tobytes(), Image.open(b).tobytes())

    def test_overlong_fields_warn_but_succeed(self):
        code, out, err = self.run_ok("--lang", "en", "--title", "긴 제목 " * 400, "--author", "저자", "--publisher", "p",
                                     "--subtitle", "부제 " * 200, "-o", self.p("long.jpg"))
        self.assertEqual(code, 0)
        self.assertIn(lang_text("en", "warn_title_long"), err)
        self.assertIn(lang_text("en", "warn_subtitle_long"), err)
        self.assertTrue(err.startswith("warning:"))

    def test_warnings_are_translated(self):
        for lang in i18n.LANGS:
            _, _, err = self.run_ok("--lang", lang, "--title", "가" * 500, "--author", "a", "--publisher", "p",
                                    "-o", self.p(f"w{lang}.jpg"))
            self.assertIn(lang_text(lang, "warn_title_long"), err, lang)

    def test_four_processes_writing_the_same_name(self):
        import subprocess
        import sys

        out = self.p("same.jpg")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        procs = [subprocess.Popen([sys.executable, os.path.join(helpers.ROOT, "main.py"), "--lang", "en", *BASE,
                                   "--template", str(i % 4 + 1), "-o", out], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env) for i in range(4)]
        outs = [p.communicate(timeout=120) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), [o[1][-300:] for o in outs])
        self.assertEqual(len([n for n in os.listdir(self.tmp) if n.endswith(".jpg")]), 4)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".part")], [])


class BadArguments(CliBase):
    def test_missing_required_fields(self):
        cases = [(["--title", "t", "--author", "a"], ["--publisher"]),
                 (["--title", "t", "--publisher", "p"], ["--author"]),
                 (["--author", "a", "--publisher", "p"], ["--title"]),
                 (["--title", "t", "--author", "a", "--publisher", "   "], ["--publisher"]),
                 (["--title", " ", "--author", "a", "--publisher", "p"], ["--title"])]
        for args, missing in cases:
            code, _, err = self.run_ok("--lang", "en", *args, "-o", self.p("m.jpg"))
            self.assertEqual(code, 2, (args, err))
            for m in missing:
                self.assertIn(m, err)
            self.assertFalse(os.path.exists(self.p("m.jpg")))

    def test_publisher_missing_explains_why_in_every_language(self):
        for lang in i18n.LANGS:
            code, _, err = self.run_ok("--lang", lang, "--title", "t", "--author", "a")
            self.assertEqual(code, 2)
            self.assertIn(lang_text(lang, "warn_publisher_empty"), err)

    def test_invalid_choices_exit_2_without_output(self):
        for bad in (["--template", "9"], ["--template", "0"], ["--template", "x"], ["--color", "0"], ["--color", "9"],
                    ["--preset", "bogus"], ["--font", "comic"], ["--format", "gif"], ["--lang", "xx"],
                    ["--width", "abc"], ["--height", "1.5"], ["-o"], ["--bogus-flag"]):
            code, _, err = self.run_ok(*BASE, *bad, "--lang", "en") if "--lang" not in bad else self.run_ok(*BASE, *bad)
            self.assertEqual(code, 2, (bad, err[-300:]))

    def test_invalid_colors_are_rejected_not_ignored(self):
        for flag in ("--bg-color", "--fg-color"):
            for bad in ("red", "#12", "#12345g", "rgb(1,2,3)", "##112233"):
                code, _, err = self.run_ok("--lang", "en", *BASE, flag, bad, "-o", self.p("c.jpg"))
                self.assertEqual(code, 2, (flag, bad, err))
                self.assertIn(flag, err)
                self.assertFalse(os.path.exists(self.p("c.jpg")))

    def test_valid_color_spellings(self):
        for i, good in enumerate(("#1f1f1f", "1F1F1F", "#abc", "  #ffffff  ")):
            out = self.p(f"ok{i}.png")
            code, _, err = self.run_ok("--lang", "en", *BASE, "--bg-color", good, "--format", "png", "-o", out)
            self.assertEqual(code, 0, (good, err))
        with Image.open(self.p("ok2.png")) as im:
            self.assertEqual(im.getpixel((3, 3)), (0xaa, 0xbb, 0xcc))

    def test_background_problems_exit_1(self):
        junk = self.p("junk.jpg")
        open(junk, "wb").write(b"not an image")
        for bg in (self.p("missing.jpg"), junk, self.tmp):
            code, _, err = self.run_ok("--lang", "en", *BASE, "--bg", bg, "-o", self.p("b.jpg"))
            self.assertEqual(code, 1, (bg, err))
            self.assertIn(lang_text("en", "err_bg_open_failed", name=bg), err)
            self.assertFalse(os.path.exists(self.p("b.jpg")))

    def test_unwritable_output_exit_1(self):
        blocker = self.p("blocker")
        open(blocker, "w").write("x")
        code, _, err = self.run_ok("--lang", "en", *BASE, "-o", os.path.join(blocker, "sub", "c.jpg"))
        self.assertEqual(code, 1)
        self.assertIn("Save failed", err)

    def test_help_is_fully_translated(self):
        for lang in i18n.LANGS:
            code, out, err = self.run_ok("--lang", lang, "--help")
            self.assertEqual(code, 0)
            self.assertIn(lang_text(lang, "cli_usage"), out)
            self.assertIn(lang_text(lang, "cli_desc")[:12], out.replace("\n", " ").replace("  ", " "))
            if lang != "en":
                for english in ("show this help message and exit", "positional arguments"):
                    self.assertNotIn(english, out, (lang, english))

    def test_weird_argument_shapes_never_crash(self):
        weird = [
            ["--title=", "--author=a", "--publisher=p"],
            ["--title", "", "--author", "", "--publisher", ""],
            ["--title", "t\nu", "--author", "a\tb", "--publisher", "p\x07"],
            ["--title", "t", "--title", "second", "--author", "a", "--publisher", "p"],
            ["--title", "x" * 20000, "--author", "a", "--publisher", "p"],         # near the Windows command line limit
            ["--title", "‮txet", "--author", "a", "--publisher", "p"],
            ["--title", "t", "--author", "a", "--publisher", "p", "--series", "s", "--volume", "9" * 500],
            ["--title", "t", "--author", "a", "--publisher", "p", "--preset", "custom", "--width", "-5", "--height", "0"],
            ["--title", "t", "--author", "a", "--publisher", "p", "--width", "999999999999999999999"],
        ]
        for i, args in enumerate(weird):
            code, _, err = self.run_ok("--lang", "en", *args, "-o", self.p(f"w{i}.jpg"))
            self.assertIn(code, (0, 1, 2), (args[:4], code, err[-300:]))


class EpubFlow(CliBase):
    def make(self, name, fn):
        p = self.p(name)
        fn(p)
        return p

    def test_insert_into_epub_without_cover_and_numbering(self):
        src = self.make("book.epub", fx.epub2_no_cover)
        before = sha(src)
        code, out, err = self.run_ok("--lang", "en", *BASE, "--epub", src, "-o", self.p("c.jpg"))
        self.assertEqual(code, 0, err)
        self.assertEqual(sha(src), before)
        self.assertIn(lang_text("en", "msg_epub_done", path=self.p("book_cover.epub")), out)
        code, out, err = self.run_ok("--lang", "en", *BASE, "--epub", src, "-o", self.p("c.jpg"))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self.p("book_cover(2).epub")))

    def test_existing_cover_needs_yes_when_not_interactive(self):
        src = self.make("has.epub", fx.epub2_with_cover)
        code, out, err = self.run_ok("--lang", "en", *BASE, "--epub", src, "-o", self.p("c.jpg"), stdin="")
        self.assertEqual(code, 1)
        self.assertIn(lang_text("en", "warn_epub_replace"), err)
        self.assertFalse(os.path.exists(self.p("has_cover.epub")))
        code, out, err = self.run_ok("--lang", "en", *BASE, "--epub", src, "-y", "-o", self.p("c2.jpg"))
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(self.p("has_cover.epub")))

    def test_drm_and_non_epub_are_refused(self):
        drm = self.make("drm.epub", fx.epub2_drm)
        code, _, err = self.run_ok("--lang", "en", *BASE, "--epub", drm, "-y", "-o", self.p("c.jpg"))
        self.assertEqual(code, 1)
        self.assertIn(lang_text("en", "err_epub_drm", name="drm.epub"), err)
        notepub = self.p("x.epub")
        open(notepub, "wb").write(b"hello")
        for target in (notepub, self.p("missing.epub")):
            code, _, err = self.run_ok("--lang", "en", *BASE, "--epub", target, "-y", "-o", self.p("c.jpg"))
            self.assertEqual(code, 1, err)
            self.assertIn(lang_text("en", "err_epub_open_failed", name=target), err)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith("_cover.epub")], [])

    def test_png_into_epub(self):
        src = self.make("p.epub", fx.epub3_svg_cover)
        code, _, err = self.run_ok("--lang", "en", *BASE, "--epub", src, "--format", "png", "-y", "-o", self.p("c.png"))
        self.assertEqual(code, 0, err)
        import zipfile
        with zipfile.ZipFile(self.p("p_cover.epub")) as z:
            self.assertTrue(any(n.endswith(".png") for n in z.namelist()))
            self.assertIsNone(z.testzip())


if __name__ == "__main__":
    unittest.main()
