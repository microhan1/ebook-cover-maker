"""EPUB insertion against many shapes of EPUB.

Every case checks the same invariants (see ``check``): the source is
untouched, the result is a well-formed EPUB whose cover is first, registered
once, and shows our image; nothing else in the book changed.
Set EPUBCHECK_JAR to also validate each result with epubcheck.
"""
from __future__ import annotations

import os
import posixpath
import re
import tempfile
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

import helpers  # noqa: F401  (must come first)
import epub_insert
import fixtures as fx
import render
from helpers import epubcheck_errors, sha
from render import Options

OPF_NS = "{http://www.idpf.org/2007/opf}"
COVER = render.to_bytes(render.render(Options(title="표지", author="저자", publisher="유페이퍼")).image, "jpg")
COVER_PNG = render.to_bytes(render.render(Options(title="표지", author="저자", publisher="유페이퍼")).image, "png")


def build(path: str, *, version: int = 2, opf: str = "OEBPS/content.opf", items: str = "", spine: str = "",
          meta: str = "", guide: str = "", files: dict | None = None, bom: bool = False, crlf: bool = False,
          encoding: str = "utf-8", chapter: bool = True, extra_pkg: str = "") -> str:
    """Write a small EPUB. Paths in ``files`` are relative to the OPF folder
    unless they start with '/'. The nav/ncx and a chapter are added for you."""
    d = posixpath.dirname(opf)
    full = lambda n: n[1:] if n.startswith("/") else posixpath.normpath(posixpath.join(d, n))  # noqa: E731
    data: dict[str, bytes | str] = {}
    for name, content in (files or {}).items():
        data[full(name)] = content
    if chapter and full("ch1.xhtml") not in data:
        data[full("ch1.xhtml")] = (fx.XHTML3 if version == 3 else fx.XHTML2).format(title="Ch1", body="<p>본문 text</p>")
    if version == 3:
        data[full("nav.xhtml")] = fx.NAV
        base_items = '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        spine_open = "<spine>"
    else:
        data[full("toc.ncx")] = fx.NCX
        base_items = '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
        spine_open = '<spine toc="ncx">'
    if chapter:
        base_items += '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    if not spine and chapter:
        spine = '    <itemref idref="ch1"/>\n'
    decl = f'<?xml version="1.0" encoding="{encoding}"?>\n'
    text = (decl + f'<package version="{version}.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid"{extra_pkg}>\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            '    <dc:identifier id="uid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>\n'
            '    <dc:title>Test é</dc:title>\n    <dc:language>ko</dc:language>\n'
            + ('    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n' if version == 3 else "")
            + meta + "  </metadata>\n  <manifest>\n" + base_items + items + "  </manifest>\n  " + spine_open + "\n"
            + spine + "  </spine>\n" + guide + "</package>\n")
    if crlf:
        text = text.replace("\n", "\r\n")
    raw = text.encode(encoding)
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    data[opf] = raw
    container = fx.CONTAINER.replace("OEBPS/content.opf", opf)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        for n, c in data.items():
            zf.writestr(n, c, compress_type=zipfile.ZIP_DEFLATED)
    return path


def error_codes(lines: list[str]) -> set[str]:
    return {m.group(1) for ln in lines if (m := re.match(r"(?:ERROR|FATAL)\(([\w-]+)\)", ln))}


def doc(title: str, body: str, version: int = 2) -> str:
    return (fx.XHTML3 if version == 3 else fx.XHTML2).format(title=title, body=body)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = cls._tmp.name

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def new(self, name: str, **kw) -> str:
        sub = os.path.join(self.tmp, name)
        os.makedirs(sub, exist_ok=True)
        return build(os.path.join(sub, "book.epub"), **kw)

    # ------------------------------------------------------------------
    def insert(self, src: str, data: bytes = COVER, fmt: str = "jpg") -> epub_insert.InsertResult:
        before = (sha(src), os.stat(src).st_mtime_ns)
        res = epub_insert.insert_cover(src, data, fmt)
        self.assertEqual((sha(src), os.stat(src).st_mtime_ns), before, "the source EPUB was modified")
        return res

    def check(self, src: str, res: epub_insert.InsertResult, data: bytes = COVER, expect_changed: set[str] | None = None):
        out = res.output_path
        self.assertTrue(os.path.isfile(out))
        self.assertEqual([n for n in os.listdir(os.path.dirname(out)) if n.endswith(".part")], [])
        with zipfile.ZipFile(out) as z, zipfile.ZipFile(src) as zs:
            infos = z.infolist()
            self.assertEqual(infos[0].filename, "mimetype")
            self.assertEqual(infos[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read("mimetype"), b"application/epub+zip")
            names = z.namelist()
            self.assertEqual(len(names), len(set(names)), "duplicate zip entries")
            info = epub_insert.inspect_epub(out)
            self.assertIsNotNone(info.cover_image, "no cover image registered")
            self.assertIsNotNone(info.cover_doc, "no cover page found")
            self.assertEqual(info.spine[0], info.cover_doc.id, "the cover is not first in the spine")
            img_entry = epub_insert._resolve(info.opf_path, info.cover_image.href)
            self.assertIn(img_entry, names, "the cover image href does not point at a file")
            self.assertEqual(z.read(img_entry), data, "the cover image is not ours")
            opf = z.read(info.opf_path)
            root = ET.fromstring(opf)                                        # well-formed
            text = opf.decode("utf-8", "replace")
            self.assertEqual(len(re.findall(r"<(?:\w+:)?meta\b[^>]*\bname\s*=\s*[\"']cover[\"']", text)), 1, "meta cover count")
            ids = [e.get("id") for e in root.iter(f"{OPF_NS}item")]
            self.assertEqual(len(ids), len(set(ids)), "duplicate manifest ids")
            for it in root.iter(f"{OPF_NS}item"):
                self.assertIn(epub_insert._resolve(info.opf_path, it.get("href")), names, f"dangling manifest href {it.get('href')}")
            for ref in root.iter(f"{OPF_NS}itemref"):
                self.assertIn(ref.get("idref"), ids, "spine idref not in manifest")
            spine_ids = [r.get("idref") for r in root.iter(f"{OPF_NS}itemref")]
            self.assertEqual(spine_ids.count(info.cover_doc.id), 1, "cover page listed twice in the spine")
            if info.epub3:
                props = sum(1 for it in root.iter(f"{OPF_NS}item") if "cover-image" in (it.get("properties") or "").split())
                self.assertEqual(props, 1, "cover-image property count")
            else:
                self.assertEqual(len(re.findall(r"<(?:\w+:)?reference\b[^>]*type\s*=\s*[\"']cover[\"']", text)), 1, "guide cover count")
            page = z.read(epub_insert._resolve(info.opf_path, info.cover_doc.href)).decode("utf-8")
            self.assertEqual(len(re.findall(r"<img\b", page)), 1)
            self.assertNotIn("<svg", page)
            m = re.search(r'<img\b[^>]*\bsrc\s*=\s*"([^"]+)"', page)
            self.assertEqual(epub_insert._resolve(epub_insert._resolve(info.opf_path, info.cover_doc.href), m.group(1)), img_entry)
            ET.fromstring(page.encode("utf-8"))                              # the page is well-formed XML
            # nothing else changed
            allowed = {info.opf_path, img_entry, epub_insert._resolve(info.opf_path, info.cover_doc.href)}
            allowed |= expect_changed or set()
            for n in zs.namelist():
                if n.endswith("/") or n == "mimetype" or n in allowed:
                    continue
                if n not in names:
                    continue                                                  # an unshared old cover image may be gone
                self.assertEqual(z.read(n), zs.read(n), f"{n} changed")
        errs = epubcheck_errors(out)
        if errs:                                  # only errors the source did not already have count against us
            new = error_codes(errs) - error_codes(epubcheck_errors(src))
            self.assertEqual(new, set(), errs[:3])
        return info


class Shapes(Base):
    def test_opf_in_the_root_folder(self):
        src = self.new("root", opf="content.opf",
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"cover.jpg": fx.jpeg()})
        res = self.insert(src)
        self.assertTrue(res.replaced)
        self.check(src, res)

    def test_opf_in_a_deep_folder_with_parent_hrefs(self):
        src = self.new("deep", opf="a/b/content.opf",
                       items='    <item id="ci" href="../img/cover.jpg" media-type="image/jpeg"/>\n'
                             '    <item id="cp" href="../text/cover.xhtml" media-type="application/xhtml+xml"/>\n',
                       spine='    <itemref idref="cp"/>\n    <itemref idref="ch1"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n',
                       files={"../img/cover.jpg": fx.jpeg(),
                              "../text/cover.xhtml": doc("c", '<div><img src="../img/cover.jpg" alt=""/></div>')})
        res = self.insert(src)
        info = self.check(src, res)
        self.assertTrue(res.replaced)
        self.assertEqual(info.cover_image.href, "../img/cover.jpg")

    def test_percent_encoded_and_unicode_hrefs(self):
        for label, href, entry in (("space", "images/my%20cover.jpg", "images/my cover.jpg"),
                                   ("korean", "images/%ED%91%9C%EC%A7%80.jpg", "images/표지.jpg"),
                                   ("raw korean", "images/표지2.jpg", "images/표지2.jpg"),
                                   ("plus-and-hash", "images/a%23b%26c.jpg", "images/a#b&c.jpg")):
            with self.subTest(label):
                src = self.new("pct_" + label,
                               items=f'    <item id="ci" href="{href}" media-type="image/jpeg"/>\n'
                                     '    <item id="cp" href="cover.xhtml" media-type="application/xhtml+xml"/>\n',
                               spine='    <itemref idref="cp"/>\n    <itemref idref="ch1"/>\n',
                               meta='    <meta name="cover" content="ci"/>\n',
                               files={entry: fx.jpeg(),
                                      "cover.xhtml": doc("c", f'<div><img src="{href}" alt=""/></div>')})
                res = self.insert(src)
                self.assertTrue(res.replaced, "an existing cover was not recognised")
                with zipfile.ZipFile(res.output_path) as z:
                    self.assertEqual(z.read("OEBPS/" + entry), COVER, "the real cover file was not replaced")
                    stray = [n for n in z.namelist() if "%" in n]
                    self.assertEqual(stray, [], "a literal %-named file was added next to the real one")
                self.check(src, res)

    def test_quote_styles_and_attribute_order(self):
        src = self.new("quotes",
                       items="    <item href='cover.jpg' id='ci' media-type='image/jpeg'/>\n",
                       meta="    <meta content='ci' name='cover'/>\n", files={"cover.jpg": fx.jpeg()})
        res = self.insert(src)
        self.assertTrue(res.replaced)
        self.check(src, res)

    def test_meta_cover_points_to_a_missing_id_and_guide_names_the_page(self):
        src = self.new("dangling",
                       items='    <item id="cp" href="cover.xhtml" media-type="application/xhtml+xml"/>\n'
                             '    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       spine='    <itemref idref="cp"/>\n    <itemref idref="ch1"/>\n',
                       meta='    <meta name="cover" content="ghost"/>\n',
                       guide='  <guide>\n    <reference type="cover" title="c" href="cover.xhtml"/>\n  </guide>\n',
                       files={"cover.jpg": fx.jpeg(), "cover.xhtml": doc("c", '<div><img src="cover.jpg" alt=""/></div>')})
        self.check(src, self.insert(src))

    def test_two_items_claim_cover_image_in_epub3(self):
        src = self.new("two_props", version=3,
                       items='    <item id="a" href="a.jpg" media-type="image/jpeg" properties="cover-image"/>\n'
                             '    <item id="b" href="b.jpg" media-type="image/jpeg" properties="cover-image"/>\n',
                       files={"a.jpg": fx.jpeg(), "b.jpg": fx.jpeg()})
        self.check(src, self.insert(src))

    def test_cover_page_listed_twice_and_linear_no(self):
        src = self.new("twice",
                       items='    <item id="cp" href="cover.xhtml" media-type="application/xhtml+xml"/>\n'
                             '    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       spine='    <itemref idref="ch1"/>\n    <itemref idref="cp" linear="no"/>\n    <itemref idref="cp"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n',
                       files={"cover.jpg": fx.jpeg(), "cover.xhtml": doc("c", '<div><img src="cover.jpg" alt=""/></div>')})
        self.check(src, self.insert(src))

    def test_bom_and_crlf_in_the_opf(self):
        src = self.new("bomcrlf", bom=True, crlf=True,
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"cover.jpg": fx.jpeg()})
        res = self.insert(src)
        self.check(src, res)
        with zipfile.ZipFile(res.output_path) as z:
            self.assertTrue(z.read("OEBPS/content.opf").startswith(b"\xef\xbb\xbf"), "the BOM was dropped")

    def test_opf_declared_as_latin1_keeps_its_characters(self):
        src = self.new("latin1", encoding="iso-8859-1",
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"cover.jpg": fx.jpeg()})
        res = self.insert(src)
        with zipfile.ZipFile(res.output_path) as z:
            self.assertIn("é".encode("iso-8859-1"), z.read("OEBPS/content.opf"))
        self.check(src, res)

    def test_empty_self_closing_guide(self):
        src = self.new("guide_empty", guide="  <guide/>\n")
        self.check(src, self.insert(src))

    def test_manifest_id_collisions(self):
        src = self.new("idclash",
                       items='    <item id="cover-image" href="notes.xhtml" media-type="application/xhtml+xml"/>\n'
                             '    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>\n',
                       spine='    <itemref idref="ch1"/>\n',
                       files={"notes.xhtml": doc("n", "<p>x</p>"), "cover.xhtml": doc("keep", "<p>an unrelated page</p>")})
        res = self.insert(src)
        info = self.check(src, res)
        with zipfile.ZipFile(res.output_path) as z:
            self.assertIn("an unrelated page", z.read("OEBPS/cover.xhtml").decode("utf-8"), "an unrelated file was overwritten")
        self.assertNotEqual(info.cover_doc.href, "cover.xhtml")

class ProtectContent(Base):
    """The cover page is rewritten, so it must never be a page with real content."""

    def test_chapter_that_merely_contains_the_cover_image_is_not_overwritten(self):
        body = "<h1>제1장</h1><p>" + "아주 중요한 본문. " * 30 + '</p><p><img src="cover.jpg" alt=""/></p>'
        src = self.new("chapter_with_image",
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n'
                             '    <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>\n',
                       spine='    <itemref idref="first"/>\n    <itemref idref="ch1"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n',
                       files={"cover.jpg": fx.jpeg(), "first.xhtml": doc("first", body)})
        res = self.insert(src)
        with zipfile.ZipFile(res.output_path) as z:
            self.assertIn("아주 중요한 본문", z.read("OEBPS/first.xhtml").decode("utf-8"), "book text was destroyed")
        info = self.check(src, res, expect_changed=set())
        self.assertNotEqual(info.cover_doc.href, "first.xhtml")
        self.assertIn("first.xhtml", res.duplicates)                  # and the user is told it still shows the cover

    def test_guide_cover_that_is_a_title_page_with_text_is_kept(self):
        body = ("<h1>책 제목</h1><h2>부제</h2><p>저자 홍길동</p><p>출판사 유페이퍼 2026</p>"
                "<p>이 책은 별을 세는 밤에 우리가 잊어버린 열두 가지 이야기를 담았습니다. 많은 사랑 부탁드립니다.</p>"
                '<img src="cover.jpg" alt=""/>')
        src = self.new("titlepage_text",
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n'
                             '    <item id="tp" href="title.xhtml" media-type="application/xhtml+xml"/>\n',
                       spine='    <itemref idref="tp"/>\n    <itemref idref="ch1"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n',
                       guide='  <guide>\n    <reference type="cover" title="c" href="title.xhtml"/>\n  </guide>\n',
                       files={"cover.jpg": fx.jpeg(), "title.xhtml": doc("t", body)})
        res = self.insert(src)
        with zipfile.ZipFile(res.output_path) as z:
            kept = z.read("OEBPS/title.xhtml").decode("utf-8")
        self.assertIn("저자 홍길동", kept)

    def test_pure_image_cover_page_is_reused(self):
        src = self.new("pure",
                       items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n'
                             '    <item id="cp" href="cover.xhtml" media-type="application/xhtml+xml"/>\n',
                       spine='    <itemref idref="cp"/>\n    <itemref idref="ch1"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n',
                       files={"cover.jpg": fx.jpeg(), "cover.xhtml": doc("c", '<div><img src="cover.jpg" alt=""/></div>')})
        res = self.insert(src)
        info = self.check(src, res)
        self.assertEqual(info.cover_doc.href, "cover.xhtml")           # no extra page added
        self.assertEqual(res.duplicates, [])


class Formats(Base):
    def test_jpeg_cover_replaced_by_png_and_back(self):
        src = self.new("j2p", items='    <item id="ci" href="images/c%20over.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"images/c over.jpg": fx.jpeg()})
        res = self.insert(src, COVER_PNG, "png")
        info = self.check(src, res, data=COVER_PNG)
        self.assertEqual(info.cover_image.media_type, "image/png")
        with zipfile.ZipFile(res.output_path) as z:
            self.assertNotIn("OEBPS/images/c over.jpg", z.namelist(), "the replaced jpeg is still inside")
        # and again: png -> jpg
        res2 = epub_insert.insert_cover(res.output_path, COVER, "jpg")
        info2 = self.check(res.output_path, res2)
        self.assertEqual(info2.cover_image.media_type, "image/jpeg")

    def test_second_insertion_changes_nothing_structurally(self):
        src = self.new("idem", version=3, items='    <item id="ci" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>\n',
                       files={"cover.jpg": fx.jpeg()})
        first = self.insert(src)
        info1 = self.check(src, first)
        second = epub_insert.insert_cover(first.output_path, COVER, "jpg")
        info2 = self.check(first.output_path, second)
        self.assertEqual((info1.cover_image.href, info1.cover_doc.href), (info2.cover_image.href, info2.cover_doc.href))
        with zipfile.ZipFile(first.output_path) as a, zipfile.ZipFile(second.output_path) as b:
            self.assertEqual(sorted(a.namelist()), sorted(b.namelist()))
            self.assertEqual(a.read("OEBPS/content.opf"), b.read("OEBPS/content.opf"))


class Hostile(Base):
    def test_corrupted_entry_is_an_epub_error_and_leaves_nothing(self):
        src = self.new("corrupt", items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"cover.jpg": fx.jpeg(), "big.bin": os.urandom(5000)})
        raw = bytearray(open(src, "rb").read())
        at = raw.find(b"big.bin") + 60
        raw[at:at + 40] = b"\x00" * 40                                   # damage the stored data of one entry
        open(src, "wb").write(raw)
        before = sorted(os.listdir(os.path.dirname(src)))
        with self.assertRaises(epub_insert.EpubError):
            epub_insert.insert_cover(src, COVER, "jpg")
        self.assertEqual(sorted(os.listdir(os.path.dirname(src))), before, "a partial output was left behind")

    def test_encrypted_flag_entry_is_an_epub_error(self):
        src = self.new("encflag", files={"x.bin": b"data"})
        with zipfile.ZipFile(src) as z:
            infos = {i.filename: i for i in z.infolist()}
        raw = bytearray(open(src, "rb").read())
        # set the "encrypted" bit in the central directory and local header of x.bin
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            pos = 0
            while True:
                pos = raw.find(sig, pos)
                if pos < 0:
                    break
                name_len_off = pos + (26 if sig == b"PK\x03\x04" else 28)
                n = int.from_bytes(raw[name_len_off:name_len_off + 2], "little")
                name_at = pos + (30 if sig == b"PK\x03\x04" else 46)
                if bytes(raw[name_at:name_at + n]) == b"OEBPS/x.bin":
                    raw[pos + off] |= 1
                pos += 4
        open(src, "wb").write(raw)
        with self.assertRaises(epub_insert.EpubError):
            epub_insert.insert_cover(src, COVER, "jpg")
        self.assertEqual([n for n in os.listdir(os.path.dirname(src)) if n != "book.epub"], [])

    def test_not_zip_text_empty_and_directory(self):
        d = os.path.join(self.tmp, "notzip")
        os.makedirs(d)
        cases = {"text.epub": b"hello", "empty.epub": b"", "html.epub": b"<html></html>"}
        for name, data in cases.items():
            p = os.path.join(d, name)
            open(p, "wb").write(data)
            with self.assertRaises(epub_insert.NotEpub, msg=name):
                epub_insert.inspect_epub(p)
            with self.assertRaises(epub_insert.EpubError, msg=name):
                epub_insert.insert_cover(p, COVER, "jpg")
        sub = os.path.join(d, "folder.epub")
        os.mkdir(sub)
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(sub)
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(os.path.join(d, "missing.epub"))

    def test_container_points_nowhere_or_opf_is_broken(self):
        d = os.path.join(self.tmp, "broken")
        os.makedirs(d)
        p = os.path.join(d, "a.epub")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", fx.CONTAINER)               # OEBPS/content.opf is missing
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(p)
        q = os.path.join(d, "b.epub")
        with zipfile.ZipFile(q, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", fx.CONTAINER)
            z.writestr("OEBPS/content.opf", "<package><manifest>")            # not well-formed
        with self.assertRaises(epub_insert.NotEpub):
            epub_insert.inspect_epub(q)

    def test_unwritable_output_leaves_nothing(self):
        src = self.new("nowrite")
        with mock.patch.object(epub_insert, "new_temp_file", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(OSError):
                epub_insert.insert_cover(src, COVER, "jpg")
        self.assertEqual(os.listdir(os.path.dirname(src)), ["book.epub"])

    def test_output_never_overwrites_and_races_get_distinct_names(self):
        src = self.new("race")
        import threading

        outs, errs = [], []

        def work():
            try:
                outs.append(epub_insert.insert_cover(src, COVER, "jpg").output_path)
            except Exception as exc:  # noqa
                errs.append(repr(exc))

        ths = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(errs, [])
        self.assertEqual(len(set(outs)), 8)
        for o in outs:
            with zipfile.ZipFile(o) as z:
                self.assertIsNone(z.testzip())

    def test_prefixed_opf_is_refused_or_handled_but_never_corrupted(self):
        d = os.path.join(self.tmp, "prefixed")
        os.makedirs(d)
        p = os.path.join(d, "book.epub")
        opf = ('<?xml version="1.0"?>\n<opf:package version="2.0" xmlns:opf="http://www.idpf.org/2007/opf" unique-identifier="u">\n'
               '<opf:metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="u">x</dc:identifier>'
               '<dc:title>t</dc:title><dc:language>en</dc:language></opf:metadata>\n<opf:manifest>\n'
               '<opf:item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n</opf:manifest>\n'
               '<opf:spine><opf:itemref idref="ch1"/></opf:spine>\n</opf:package>\n')
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", fx.CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/ch1.xhtml", doc("c", "<p>x</p>"))
        try:
            res = epub_insert.insert_cover(p, COVER, "jpg")
        except epub_insert.EpubError:
            return                                                            # a clean refusal is fine
        self.check(p, res)                                                    # otherwise it must be a correct result


class Preservation(Base):
    def test_every_other_entry_is_byte_identical_and_keeps_its_metadata(self):
        files = {"cover.jpg": fx.jpeg(), "style.css": "body{}", "fonts/a.otf": os.urandom(3000),
                 "images/a.png": fx.png(), "text/한글 장.xhtml": doc("k", "<p>한글</p>")}
        src = self.new("preserve", items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n'
                                         '    <item id="css" href="style.css" media-type="text/css"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files=files)
        res = self.insert(src)
        self.check(src, res)
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(res.output_path) as b:
            for i in a.infolist():
                if i.filename in ("OEBPS/content.opf", "OEBPS/cover.jpg", "mimetype"):
                    continue
                j = b.getinfo(i.filename)
                self.assertEqual((j.date_time, j.compress_type), (i.date_time, i.compress_type), i.filename)

    def test_large_epub_is_processed_quickly(self):
        files = {"cover.jpg": fx.jpeg()}
        for i in range(30):
            files[f"images/p{i}.bin"] = os.urandom(1_500_000)               # 45 MB, incompressible
        src = self.new("large", items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files=files)
        t0 = time.perf_counter()
        res = self.insert(src)
        dt = time.perf_counter() - t0
        self.assertLess(dt, 20, f"{dt:.1f}s for a 45 MB epub")
        with zipfile.ZipFile(res.output_path) as z:
            self.assertEqual(len([n for n in z.namelist() if n.startswith("OEBPS/images/")]), 30)
            self.assertIsNone(z.testzip())

    def test_zip_comment_and_directory_entries_do_not_break_it(self):
        src = self.new("dirs", items='    <item id="ci" href="cover.jpg" media-type="image/jpeg"/>\n',
                       meta='    <meta name="cover" content="ci"/>\n', files={"cover.jpg": fx.jpeg()})
        with zipfile.ZipFile(src, "a") as z:
            z.writestr("OEBPS/images/", b"")
            z.comment = b"made by hand"
        self.check(src, self.insert(src))


if __name__ == "__main__":
    unittest.main()
