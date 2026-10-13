"""EPUB insertion: original untouched, cover registered, first in spine, valid.

Set EPUBCHECK_JAR=<path to epubcheck.jar> to also validate every output.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import epub_insert  # noqa: E402
import fixtures  # noqa: E402
import render  # noqa: E402

EPUBCHECK = os.environ.get("EPUBCHECK_JAR", "")


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class EpubInsertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = fixtures.build_all(cls.tmp.name)
        cls.cover = render.to_bytes(render.render(render.Options(title="t", author="a", publisher="p")).image, "jpg")
        cls.cover_png = render.to_bytes(render.render(render.Options(title="t", author="a", publisher="p")).image, "png")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _check(self, out: str) -> epub_insert.EpubInfo:
        with zipfile.ZipFile(out) as z:
            infos = z.infolist()
            self.assertEqual(infos[0].filename, "mimetype")
            self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            info = epub_insert.inspect_epub(out)
            self.assertIsNotNone(info.cover_image)
            self.assertIsNotNone(info.cover_doc)
            self.assertEqual(info.spine[0], info.cover_doc.id)
            img_path = epub_insert._resolve(info.opf_path, info.cover_image.href)
            self.assertEqual(z.read(img_path), self.cover)
            doc = z.read(epub_insert._resolve(info.opf_path, info.cover_doc.href)).decode("utf-8")
            self.assertIn("<img", doc)
            self.assertNotIn("<svg", doc)
            opf = z.read(info.opf_path).decode("utf-8")
            self.assertEqual(len(re.findall(r'<meta\b[^>]*name="cover"', opf)), 1)
            if info.epub3:
                self.assertEqual(len(re.findall(r'properties="[^"]*\bcover-image\b', opf)), 1)
            else:
                self.assertIn('<reference type="cover"', opf)
            # every manifest item resolves to a file
            for it in info.items.values():
                self.assertIn(epub_insert._resolve(info.opf_path, it.href), z.namelist(), it.href)
        if EPUBCHECK:
            r = subprocess.run(["java", "-jar", EPUBCHECK, out], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            problems = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith(("ERROR", "FATAL"))]
            self.assertEqual(problems, [], out)
        return info

    def test_epub2_existing_cover_replaced(self):
        src = self.paths["epub2_cover.epub"]
        before = sha(src)
        res = epub_insert.insert_cover(src, self.cover, "jpg")
        self.assertEqual(sha(src), before)
        self.assertTrue(res.replaced)
        self.assertTrue(res.output_path.endswith("epub2_cover_cover.epub"))
        info = self._check(res.output_path)
        self.assertEqual(info.cover_image.href, "images/cover.jpg")       # same file, overwritten

    def test_epub2_without_cover_gets_one_first(self):
        src = self.paths["epub2_nocover.epub"]
        res = epub_insert.insert_cover(src, self.cover, "jpg")
        self.assertFalse(res.replaced)
        info = self._check(res.output_path)
        self.assertEqual(info.spine, ["cover-page", "ch1"])

    def test_png_cover_late_in_spine_moves_first_and_keeps_shared_file(self):
        src = self.paths["epub2_png_late.epub"]
        res = epub_insert.insert_cover(src, self.cover, "jpg")
        self.assertEqual(res.duplicates, ["about.xhtml"])
        info = self._check(res.output_path)
        self.assertEqual(info.spine[0], "titlepage")
        with zipfile.ZipFile(res.output_path) as z:
            self.assertIn("OEBPS/cover.png", z.namelist())            # still used by about.xhtml

    def test_epub3_svg_wrapper_replaced_with_img(self):
        src = self.paths["epub3_svg.epub"]
        res = epub_insert.insert_cover(src, self.cover, "jpg")
        self.assertTrue(res.replaced)
        info = self._check(res.output_path)
        self.assertTrue(info.epub3)

    def test_epub3_without_cover(self):
        res = epub_insert.insert_cover(self.paths["epub3_nocover.epub"], self.cover, "jpg")
        self._check(res.output_path)

    def test_drm_is_refused(self):
        src = self.paths["epub2_drm.epub"]
        self.assertTrue(epub_insert.inspect_epub(src).drm)
        with self.assertRaises(epub_insert.DRMProtected):
            epub_insert.insert_cover(src, self.cover, "jpg")
        self.assertFalse(os.path.exists(src.replace(".epub", "_cover.epub")))

    def test_font_obfuscation_is_not_drm(self):
        self.assertFalse(epub_insert.inspect_epub(self.paths["epub3_fontobf.epub"]).drm)

    def test_output_is_numbered_when_taken(self):
        sub = os.path.join(self.tmp.name, "numbered")
        os.makedirs(sub, exist_ok=True)
        src = os.path.join(sub, "book.epub")
        fixtures.epub2_with_cover(src)
        first = epub_insert.insert_cover(src, self.cover, "jpg").output_path
        self.assertEqual(os.path.basename(first), "book_cover.epub")
        os.mkdir(os.path.join(sub, "book_cover(2).epub"))          # a folder squatting on the name
        second = epub_insert.insert_cover(src, self.cover, "jpg").output_path
        self.assertEqual(os.path.basename(second), "book_cover(3).epub")

    def test_not_an_epub(self):
        bad = os.path.join(self.tmp.name, "bad.epub")
        open(bad, "wb").write(b"hello")
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(bad)
        with zipfile.ZipFile(os.path.join(self.tmp.name, "zip.epub"), "w") as z:
            z.writestr("a.txt", "x")
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(os.path.join(self.tmp.name, "zip.epub"))


if __name__ == "__main__":
    unittest.main()
