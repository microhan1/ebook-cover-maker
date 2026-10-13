"""Saving: never overwrite, never leave junk, survive failures and races."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import helpers  # noqa: F401  (must come first)
from PIL import Image

import presets
import render
from helpers import ROOT, sha
from render import Options


class SaveBasics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = render.render(Options(title="제목", author="저자", publisher="유페이퍼")).image

    def tearDown(self):
        self.tmp.cleanup()

    def p(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def names(self):
        return sorted(os.listdir(self.tmp.name))

    def test_numbering_skips_files_and_folders(self):
        base = self.p("cover.jpg")
        got = [os.path.basename(render.save_cover(self.img, base, "jpg")) for _ in range(3)]
        os.mkdir(self.p("cover(4).jpg"))
        got.append(os.path.basename(render.save_cover(self.img, base, "jpg")))
        self.assertEqual(got, ["cover.jpg", "cover(2).jpg", "cover(3).jpg", "cover(5).jpg"])

    def test_existing_file_content_is_never_touched(self):
        p = self.p("cover.jpg")
        open(p, "wb").write(b"precious")
        out = render.save_cover(self.img, p, "jpg")
        self.assertEqual(open(p, "rb").read(), b"precious")
        self.assertNotEqual(out, p)

    def test_extensions_follow_the_format(self):
        cases = [("cover", "jpg", "cover.jpg"), ("cover", "png", "cover.png"), ("cover.JPG", "jpg", "cover.JPG"),
                 ("cover.jpeg", "jpg", "cover.jpeg"), ("cover.png", "jpg", "cover.jpg"),
                 ("cover.jpg", "png", "cover.png"), ("my.cover", "jpg", "my.cover.jpg"),
                 ("a.b.c", "png", "a.b.c.png"), ("cover.PNG", "png", "cover.PNG")]
        for i, (name, fmt, want) in enumerate(cases):
            d = self.p(f"c{i}")
            out = render.save_cover(self.img, os.path.join(d, name), fmt)
            self.assertEqual(os.path.basename(out), want, (name, fmt))
            with Image.open(out) as im:
                self.assertEqual(im.format, "PNG" if fmt == "png" else "JPEG")

    def test_creates_missing_folders_and_handles_unusual_names(self):
        out = render.save_cover(self.img, self.p("새 폴더", "하위 #1 & (2)", "표지 100%.jpg"), "jpg")
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(self.names(), ["새 폴더"])

    def test_relative_path_uses_the_working_directory(self):
        old = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            out = render.save_cover(self.img, "rel.jpg", "jpg")
        finally:
            os.chdir(old)
        self.assertTrue(os.path.isfile(self.p("rel.jpg")))
        self.assertTrue(os.path.samefile(out, self.p("rel.jpg")))

    def test_output_files_are_valid_and_up_to_spec(self):
        j = render.save_cover(self.img, self.p("a.jpg"), "jpg")
        pn = render.save_cover(self.img, self.p("a.png"), "png")
        with Image.open(j) as im:
            self.assertEqual((im.format, im.mode, im.size), ("JPEG", "RGB", (800, 1200)))
            self.assertEqual(im.quantization[0][0], 3, "JPEG quality should be 90")     # q90 luma DC step
            self.assertNotIn("progressive", im.info)
            self.assertFalse(im.info.get("exif"))
        with Image.open(pn) as im:
            self.assertEqual((im.format, im.mode, im.size), ("PNG", "RGB", (800, 1200)))
            self.assertEqual(list(im.getdata())[:200], list(self.img.getdata())[:200])    # lossless
        self.assertLess(os.path.getsize(j), 400_000)

    def test_upaper_preset_width_is_inside_600_1000(self):
        w, h = presets.SIZE_PRESETS[presets.DEFAULT_PRESET]
        self.assertTrue(600 <= w <= 1000)
        self.assertEqual(h * 2, w * 3)                                       # 2:3 portrait


class SaveFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = render.render(Options(title="t", author="a", publisher="p")).image

    def tearDown(self):
        self.tmp.cleanup()

    def leftovers(self):
        return [n for n in os.listdir(self.tmp.name)]

    def test_failure_while_writing_leaves_nothing(self):
        real_fdopen = os.fdopen

        def boom(fd, *a, **k):
            f = real_fdopen(fd, *a, **k)
            f.write = mock.Mock(side_effect=OSError(28, "No space left on device"))
            return f

        with mock.patch("os.fdopen", boom):
            with self.assertRaises(OSError):
                render.save_cover(self.img, os.path.join(self.tmp.name, "c.jpg"), "jpg")
        self.assertEqual(self.leftovers(), [])

    def test_failure_while_moving_leaves_nothing(self):
        with mock.patch("os.rename", side_effect=OSError(5, "denied")), \
                mock.patch("os.link", side_effect=OSError(5, "denied")):
            with self.assertRaises(OSError):
                render.save_cover(self.img, os.path.join(self.tmp.name, "c.jpg"), "jpg")
        self.assertEqual(self.leftovers(), [])

    def test_encoding_failure_creates_no_file(self):
        with mock.patch.object(render, "to_bytes", side_effect=ValueError("encoder broke")):
            with self.assertRaises(ValueError):
                render.save_cover(self.img, os.path.join(self.tmp.name, "c.jpg"), "jpg")
        self.assertEqual(self.leftovers(), [])

    def test_unwritable_location_raises_oserror(self):
        blocker = os.path.join(self.tmp.name, "file")
        open(blocker, "w").write("x")
        with self.assertRaises(OSError):
            render.save_cover(self.img, os.path.join(blocker, "sub", "c.jpg"), "jpg")     # parent is a file

    def test_keyboard_interrupt_cleans_up(self):
        with mock.patch("os.rename", side_effect=KeyboardInterrupt), mock.patch("os.link", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                render.save_cover(self.img, os.path.join(self.tmp.name, "c.jpg"), "jpg")
        self.assertEqual(self.leftovers(), [])


class SaveRaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.img = render.render(Options(title="t", author="a", publisher="p")).image

    def tearDown(self):
        self.tmp.cleanup()

    def test_sixteen_threads_one_name(self):
        base = os.path.join(self.tmp.name, "cover.jpg")
        paths, errors = [], []

        def work():
            try:
                paths.append(render.save_cover(self.img, base, "jpg"))
            except Exception as exc:  # noqa
                errors.append(repr(exc))

        ths = [threading.Thread(target=work) for _ in range(16)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(paths)), 16)
        self.assertEqual(len(os.listdir(self.tmp.name)), 16)
        for p in paths:
            with Image.open(p) as im:
                im.load()                                  # every file is a whole, valid JPEG

    def test_separate_processes_one_name(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import render; from render import Options\n"
            "img = render.render(Options(title='t', author='a', publisher='p')).image\n"
            "print(render.save_cover(img, sys.argv[2], 'jpg'))\n"
        )
        base = os.path.join(self.tmp.name, "cover.jpg")
        procs = [subprocess.Popen([sys.executable, "-c", code, ROOT, base], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(6)]
        outs = [p.communicate(timeout=120) for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), [o[1][-300:] for o in outs])
        paths = [o[0].strip() for o in outs]
        self.assertEqual(len(set(paths)), 6, paths)
        self.assertEqual(len(os.listdir(self.tmp.name)), 6)


if __name__ == "__main__":
    unittest.main()
