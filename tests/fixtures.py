"""Build small EPUB fixtures for the tests (written to a temp folder, never committed)."""
from __future__ import annotations

import io
import os
import zipfile

from PIL import Image

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

XHTML2 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ko"><head><title>{title}</title></head>
<body>{body}</body></html>"""

XHTML3 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ko"><head><title>{title}</title></head>
<body>{body}</body></html>"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="urn:uuid:11111111-2222-3333-4444-555555555555"/><meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>Test</text></docTitle>
<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Chapter 1</text></navLabel><content src="ch1.xhtml"/></navPoint></navMap>
</ncx>"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Nav</title></head>
<body><nav epub:type="toc"><ol><li><a href="ch1.xhtml">Chapter 1</a></li></ol></nav></body></html>"""


def jpeg(w: int = 700, h: int = 1000, color: str = "#336699") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


def png(w: int = 700, h: int = 1000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "#996633").save(buf, format="PNG")
    return buf.getvalue()


def _opf2(items: str, spine: str, meta: str = "", guide: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="uid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>테스트 도서</dc:title>
    <dc:creator opf:role="aut">홍길동</dc:creator>
    <dc:language>ko</dc:language>
    <dc:publisher>유페이퍼</dc:publisher>
{meta}  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{items}  </manifest>
  <spine toc="ncx">
{spine}  </spine>
{guide}</package>"""


def _opf3(items: str, spine: str, meta: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>テスト</dc:title>
    <dc:creator>山田太郎</dc:creator>
    <dc:language>ja</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
{meta}  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{items}  </manifest>
  <spine>
{spine}  </spine>
</package>"""


def write_epub(path: str, files: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


def epub2_with_cover(path: str) -> None:
    """EPUB 2 with cover.jpg, a cover page, meta cover and guide."""
    items = ('    <item id="cover-img" href="images/cover.jpg" media-type="image/jpeg"/>\n'
             '    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>\n'
             '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n')
    spine = '    <itemref idref="cover"/>\n    <itemref idref="ch1"/>\n'
    meta = '    <meta name="cover" content="cover-img"/>\n'
    guide = '  <guide>\n    <reference type="cover" title="Cover" href="cover.xhtml"/>\n  </guide>\n'
    write_epub(path, {
        "OEBPS/content.opf": _opf2(items, spine, meta, guide),
        "OEBPS/toc.ncx": NCX,
        "OEBPS/images/cover.jpg": jpeg(),
        "OEBPS/cover.xhtml": XHTML2.format(title="Cover", body='<div><img src="images/cover.jpg" alt="cover"/></div>'),
        "OEBPS/ch1.xhtml": XHTML2.format(title="Chapter 1", body="<p>본문</p>"),
    })


def epub2_no_cover(path: str) -> None:
    items = '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    spine = '    <itemref idref="ch1"/>\n'
    write_epub(path, {
        "OEBPS/content.opf": _opf2(items, spine),
        "OEBPS/toc.ncx": NCX,
        "OEBPS/ch1.xhtml": XHTML2.format(title="Chapter 1", body="<p>본문</p>"),
    })


def epub2_png_cover_after_ch1(path: str) -> None:
    """PNG cover, cover page NOT first in spine, no guide, another doc also showing the image."""
    items = ('    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
             '    <item id="cov" href="cover.png" media-type="image/png"/>\n'
             '    <item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>\n'
             '    <item id="about" href="about.xhtml" media-type="application/xhtml+xml"/>\n')
    spine = '    <itemref idref="ch1"/>\n    <itemref idref="titlepage"/>\n    <itemref idref="about"/>\n'
    meta = '    <meta name="cover" content="cov"/>\n'
    write_epub(path, {
        "OEBPS/content.opf": _opf2(items, spine, meta),
        "OEBPS/toc.ncx": NCX,
        "OEBPS/cover.png": png(),
        "OEBPS/titlepage.xhtml": XHTML2.format(title="Cover", body='<div><img src="cover.png" alt="cover"/></div>'),
        "OEBPS/about.xhtml": XHTML2.format(title="About", body='<p>Also shows</p><p><img src="cover.png" alt="x"/></p>'),
        "OEBPS/ch1.xhtml": XHTML2.format(title="Chapter 1", body="<p>본문</p>"),
    })


def epub3_svg_cover(path: str) -> None:
    """EPUB 3 whose cover page wraps the image in an SVG element."""
    items = ('    <item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>\n'
             '    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>\n'
             '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n')
    spine = '    <itemref idref="cover"/>\n    <itemref idref="ch1"/>\n'
    body = ('<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'viewBox="0 0 700 1000" preserveAspectRatio="xMidYMid meet">'
            '<image width="700" height="1000" xlink:href="cover.jpg"/></svg></div>')
    write_epub(path, {
        "OEBPS/content.opf": _opf3(items, spine),
        "OEBPS/nav.xhtml": NAV,
        "OEBPS/cover.jpg": jpeg(),
        "OEBPS/cover.xhtml": XHTML3.format(title="Cover", body=body),
        "OEBPS/ch1.xhtml": XHTML3.format(title="Chapter 1", body="<p>本文</p>"),
    })


def epub3_no_cover(path: str) -> None:
    items = '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    spine = '    <itemref idref="ch1"/>\n'
    write_epub(path, {
        "OEBPS/content.opf": _opf3(items, spine),
        "OEBPS/nav.xhtml": NAV,
        "OEBPS/ch1.xhtml": XHTML3.format(title="Chapter 1", body="<p>本文</p>"),
    })


def epub2_drm(path: str) -> None:
    items = '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    spine = '    <itemref idref="ch1"/>\n'
    enc = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
<enc:CipherData><enc:CipherReference URI="OEBPS/ch1.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>"""
    write_epub(path, {
        "META-INF/encryption.xml": enc,
        "OEBPS/content.opf": _opf2(items, spine),
        "OEBPS/toc.ncx": NCX,
        "OEBPS/ch1.xhtml": XHTML2.format(title="Chapter 1", body="<p>x</p>"),
    })


def epub3_font_obfuscation_only(path: str) -> None:
    """encryption.xml that only obfuscates a font is not DRM."""
    items = '    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    spine = '    <itemref idref="ch1"/>\n'
    enc = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
<enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
<enc:CipherData><enc:CipherReference URI="OEBPS/fonts/a.otf"/></enc:CipherData></enc:EncryptedData></encryption>"""
    write_epub(path, {
        "META-INF/encryption.xml": enc,
        "OEBPS/content.opf": _opf3(items, spine),
        "OEBPS/nav.xhtml": NAV,
        "OEBPS/ch1.xhtml": XHTML3.format(title="Chapter 1", body="<p>x</p>"),
    })


ALL = {
    "epub2_cover.epub": epub2_with_cover,
    "epub2_nocover.epub": epub2_no_cover,
    "epub2_png_late.epub": epub2_png_cover_after_ch1,
    "epub3_svg.epub": epub3_svg_cover,
    "epub3_nocover.epub": epub3_no_cover,
    "epub2_drm.epub": epub2_drm,
    "epub3_fontobf.epub": epub3_font_obfuscation_only,
}


def build_all(folder: str) -> dict[str, str]:
    os.makedirs(folder, exist_ok=True)
    out = {}
    for name, fn in ALL.items():
        p = os.path.join(folder, name)
        fn(p)
        out[name] = p
    return out
