"""Put a cover image into an EPUB: replace cover.jpg, register it in the OPF
(<meta name="cover">, cover-image property), and make the cover page the
first spine item. The original EPUB is never touched; the result is written
as <name>_cover.epub next to it.

The OPF is edited with targeted string replacements rather than re-serialized
from a DOM, so everything we do not touch stays byte-for-byte identical
(namespace prefixes, comments, ordering). Because a text edit can silently
miss, the edited OPF is parsed again and checked before anything is written.

Rules that protect the book:
  * a page is only rewritten as the cover page when it is a real cover page
    (one image and almost no text); a chapter or title page that merely shows
    the cover image is left alone and reported as a duplicate
  * hrefs are percent-decoded before they are matched against zip entries
  * nothing is ever overwritten: the output gets a numbered name if taken
"""
from __future__ import annotations

import os
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field

from render import move_no_overwrite, new_temp_file, unique_path

CONTAINER = "META-INF/container.xml"
ENCRYPTION = "META-INF/encryption.xml"
RIGHTS = "META-INF/rights.xml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
FONT_OBFUSCATION = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/svg+xml", "image/webp")
XHTML_TYPE = "application/xhtml+xml"
MEDIA_FOR = {"jpg": "image/jpeg", "png": "image/png"}
EXT_FOR = {"image/jpeg": ".jpg", "image/png": ".png"}
COVER_PAGE_MAX_TEXT = 40        # a cover page has at most this many visible characters
ZIP_ERRORS = (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError)


class EpubError(Exception):
    pass


class NotEpub(EpubError):
    pass


class DRMProtected(EpubError):
    pass


@dataclass
class ManifestItem:
    id: str
    href: str
    media_type: str
    properties: str = ""


@dataclass
class EpubInfo:
    path: str
    opf_path: str
    version: str
    drm: bool
    cover_image: ManifestItem | None
    cover_doc: ManifestItem | None
    items: dict[str, ManifestItem] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)
    language: str = "en"

    @property
    def has_cover(self) -> bool:
        return self.cover_image is not None

    @property
    def epub3(self) -> bool:
        return self.version.startswith("3")


@dataclass
class InsertResult:
    output_path: str
    replaced: bool
    duplicates: list[str]       # other documents that also show the cover image


# ---------------------------------------------------------------- paths
def _resolve(base_path: str, href: str) -> str:
    """Zip entry that ``href`` (relative to the file ``base_path``) names.
    hrefs are URLs: "my%20cover.jpg" is the entry "my cover.jpg"."""
    href = href.split("#", 1)[0].split("?", 1)[0]
    href = urllib.parse.unquote(href)
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), href))


def _href_for(from_path: str, target: str) -> str:
    """href (percent-encoded) that reaches zip entry ``target`` from the file ``from_path``."""
    folder = posixpath.dirname(from_path) or "."
    return urllib.parse.quote(posixpath.relpath(target, folder), safe="/")


# ---------------------------------------------------------------- text helpers
def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r'''([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')''', tag):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def _find_element(text: str, tag: str, attr: str | None = None, value: str | None = None,
                  start: int = 0, ci: bool = False) -> tuple[int, int, str] | None:
    """Span of the first <tag ...> element (self-closing or with its end tag,
    assuming it has no nested <tag>) whose attribute matches. Returns
    (start, end, start_tag_text)."""
    for m in re.finditer(rf"<{tag}\b[^>]*>", text[start:]):
        s = start + m.start()
        head = m.group(0)
        if attr is not None:
            got = _attrs(head).get(attr)
            if got is None or (got.lower() != value.lower() if ci else got != value):
                continue
        if head.endswith("/>"):
            return s, start + m.end(), head
        close = re.compile(rf"</{tag}\s*>").search(text, start + m.end())
        end = close.end() if close else start + m.end()
        return s, end, head
    return None


def _replace_span(text: str, span: tuple[int, int, str] | None, new: str) -> str:
    if span is None:
        return text
    return text[:span[0]] + new + text[span[1]:]


def _remove_span(text: str, span: tuple[int, int, str]) -> str:
    """Delete an element; when it sits alone on its line, delete the whole line."""
    s, e = span[0], span[1]
    line_start = text.rfind("\n", 0, s) + 1
    tail = re.match(r"[ \t]*(\r?\n|$)", text[e:])
    if text[line_start:s].strip() == "" and tail:
        return text[:line_start] + text[e + tail.end():]
    return text[:s] + text[e:]


def _remove_all(text: str, tag: str, attr: str, value: str, ci: bool = False) -> str:
    while True:
        span = _find_element(text, tag, attr, value, ci=ci)
        if span is None:
            return text
        text = _remove_span(text, span)


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _insert_before_close(text: str, tag: str, line: str, indent: str = "    ") -> str:
    """Add ``line`` as its own line just above </tag>, keeping </tag>'s indentation."""
    m = re.search(rf"</{tag}\s*>", text)
    if m is None:
        return text
    nl = _newline(text)
    line_start = text.rfind("\n", 0, m.start()) + 1
    at = line_start if text[line_start:m.start()].strip() == "" else m.start()
    prefix = "" if at == line_start else nl
    return text[:at] + prefix + indent + line + nl + text[at:]


def _open_tag(text: str, tag: str) -> re.Match | None:
    """The first <tag ...> start tag that is not self-closing (<tag/>)."""
    for m in re.finditer(rf"<{tag}\b[^>]*>", text):
        if not m.group(0).endswith("/>"):
            return m
    return None


def _insert_after_open(text: str, tag: str, line: str, indent: str = "    ") -> str:
    m = _open_tag(text, tag)
    if m is None:
        return text
    nl = _newline(text)
    return text[:m.end()] + nl + indent + line + text[m.end():]


def _strip_cover_image_property(text: str) -> str:
    def fix(m: re.Match) -> str:
        head = m.group(0)
        props = _attrs(head).get("properties", "").split()
        if "cover-image" not in props:
            return head
        props = [p for p in props if p != "cover-image"]
        if props:
            return re.sub(r'\bproperties\s*=\s*(["\'])[^"\']*\1', f'properties="{" ".join(props)}"', head)
        return re.sub(r'\s+properties\s*=\s*(["\'])[^"\']*\1', "", head)

    return re.sub(r"<item\b[^>]*>", fix, text)


def _decode_xml(data: bytes) -> tuple[str, str, bytes]:
    """(text, encoding, bom) of an XML file, honoring a BOM or the XML declaration."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8", b"\xef\xbb\xbf"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16", b""
    m = re.match(rb'\s*<\?xml[^>]*\bencoding\s*=\s*["\']([\w.:-]+)["\']', data)
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return data.decode(enc), enc, b""
    except LookupError as exc:
        raise EpubError(f"unknown encoding {enc}") from exc


# ---------------------------------------------------------------- reading
def _open_zip(path: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise NotEpub(path) from exc
    if CONTAINER not in zf.namelist():
        zf.close()
        raise NotEpub(path)
    return zf


def _opf_path(zf: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(zf.read(CONTAINER))
    except ET.ParseError as exc:
        raise NotEpub(CONTAINER) from exc
    for rf in root.iter(f"{{{CONTAINER_NS}}}rootfile"):
        p = rf.get("full-path")
        if p and p in zf.namelist():
            return p
    raise NotEpub(CONTAINER)


def _is_drm(zf: zipfile.ZipFile) -> bool:
    names = set(zf.namelist())
    if RIGHTS in names:
        return True
    if ENCRYPTION not in names:
        return False
    try:
        root = ET.fromstring(zf.read(ENCRYPTION))
    except ET.ParseError:
        return True
    for el in root.iter():
        if el.tag.endswith("}EncryptionMethod") or el.tag == "EncryptionMethod":
            if el.get("Algorithm") not in FONT_OBFUSCATION:
                return True
    return False


def _image_refs(xhtml: bytes) -> list[str]:
    text = xhtml.decode("utf-8", "replace")
    refs = re.findall(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', text, flags=re.I)
    refs += re.findall(r'<image\b[^>]*\b(?:xlink:)?href\s*=\s*["\']([^"\']+)["\']', text, flags=re.I)
    return refs


def _is_cover_page(xhtml: bytes) -> bool:
    """A real cover page: at most one image and almost no text. Anything with
    more (a chapter, a title page with author and publisher lines) is book
    content and must not be rewritten."""
    text = xhtml.decode("utf-8", "replace")
    m = re.search(r"<body\b.*?</body\s*>", text, flags=re.S | re.I)
    body = m.group(0) if m else text
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"<(script|style)\b.*?</\1\s*>", "", body, flags=re.S | re.I)
    visible = re.sub(r"<[^>]*>", "", body)
    visible = re.sub(r"&#?\w+;", " ", visible)
    visible = "".join(visible.split())
    return len(visible) <= COVER_PAGE_MAX_TEXT and len(_image_refs(xhtml)) <= 1


def inspect_epub(path: str) -> EpubInfo:
    """Read the OPF and find the current cover image and cover page."""
    try:
        return _inspect(path)
    except ZIP_ERRORS as exc:
        raise NotEpub(path) from exc


def _inspect(path: str) -> EpubInfo:
    with _open_zip(path) as zf:
        opf_path = _opf_path(zf)
        drm = _is_drm(zf)
        try:
            root = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as exc:
            raise NotEpub(opf_path) from exc
        names = set(zf.namelist())
        version = root.get("version", "2.0")
        language = "en"
        for el in root.iter(f"{{{DC_NS}}}language"):
            if el.text and el.text.strip():
                language = el.text.strip()
                break
        items: dict[str, ManifestItem] = {}
        for it in root.iter(f"{{{OPF_NS}}}item"):
            if it.get("id") and it.get("href"):
                items[it.get("id")] = ManifestItem(it.get("id"), it.get("href"),
                                                   it.get("media-type", ""), it.get("properties", ""))
        spine = [ref.get("idref") for ref in root.iter(f"{{{OPF_NS}}}itemref") if ref.get("idref") in items]
        cover_id = None
        for meta in root.iter(f"{{{OPF_NS}}}meta"):
            if meta.get("name") == "cover":
                cover_id = meta.get("content")
        cover_image = None
        if cover_id and cover_id in items and items[cover_id].media_type in IMAGE_TYPES:
            cover_image = items[cover_id]
        if cover_image is None:
            for it in items.values():
                if "cover-image" in it.properties.split() and it.media_type in IMAGE_TYPES:
                    cover_image = it
                    break
        guide_doc = None
        for ref in root.iter(f"{{{OPF_NS}}}reference"):
            if (ref.get("type") or "").lower() == "cover" and ref.get("href"):
                target = _resolve(opf_path, ref.get("href"))
                for it in items.values():
                    if _resolve(opf_path, it.href) == target and it.media_type == XHTML_TYPE:
                        guide_doc = it
                        break
        if cover_image is None and guide_doc is not None:
            doc_path = _resolve(opf_path, guide_doc.href)
            if doc_path in names:
                for src in _image_refs(zf.read(doc_path)):
                    target = _resolve(doc_path, src)
                    for it in items.values():
                        if _resolve(opf_path, it.href) == target and it.media_type in IMAGE_TYPES:
                            cover_image = it
                            break
                    if cover_image:
                        break
        if cover_image is None:
            for it in items.values():
                if it.media_type in IMAGE_TYPES and "cover" in (it.id + " " + it.href).lower():
                    cover_image = it
                    break

        def reusable(it: ManifestItem | None) -> ManifestItem | None:
            """Only a genuine cover page may be rewritten."""
            if it is None or it.media_type != XHTML_TYPE:
                return None
            p = _resolve(opf_path, it.href)
            return it if p in names and _is_cover_page(zf.read(p)) else None

        cover_doc = reusable(guide_doc)
        if cover_doc is None:
            for d in _docs_showing(zf, opf_path, items, spine, cover_image):
                cover_doc = reusable(d)
                if cover_doc is not None:
                    break
        if cover_doc is None and spine:
            first = items[spine[0]]
            if "cover" in (first.id + " " + first.href).lower():
                cover_doc = reusable(first)
    return EpubInfo(path=path, opf_path=opf_path, version=version, drm=drm,
                    cover_image=cover_image, cover_doc=cover_doc, items=items, spine=spine, language=language)


def _docs_showing(zf: zipfile.ZipFile, opf_path: str, items: dict[str, ManifestItem],
                  spine: list[str], image: ManifestItem | None) -> list[ManifestItem]:
    if image is None:
        return []
    target = _resolve(opf_path, image.href)
    names = set(zf.namelist())
    found = []
    order = spine + [i for i in items if i not in spine]
    for idref in order:
        it = items[idref]
        if it.media_type != XHTML_TYPE:
            continue
        doc_path = _resolve(opf_path, it.href)
        if doc_path not in names:
            continue
        if any(_resolve(doc_path, src) == target for src in _image_refs(zf.read(doc_path))):
            found.append(it)
    return found


# ---------------------------------------------------------------- writing
def output_path_for(epub_path: str) -> str:
    base, _ = os.path.splitext(epub_path)
    return unique_path(base + "_cover.epub")


def _unique_id(items: dict[str, ManifestItem], base: str) -> str:
    if base not in items:
        return base
    n = 2
    while f"{base}{n}" in items:
        n += 1
    return f"{base}{n}"


def _unique_name(names: set[str], folder: str, base: str, ext: str) -> str:
    cand = posixpath.join(folder, base + ext) if folder else base + ext
    n = 2
    while cand in names:
        cand = posixpath.join(folder, f"{base}{n}{ext}") if folder else f"{base}{n}{ext}"
        n += 1
    return cand


def _cover_xhtml(img_href: str, epub3: bool, language: str = "en") -> bytes:
    doctype = ("<!DOCTYPE html>" if epub3 else
               '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')
    lang = re.sub(r"[^A-Za-z0-9-]", "", language) or "en"
    # XHTML 1.1 allows only xml:lang; HTML5-flavoured EPUB 3 also takes lang
    ns = f' xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}"' if epub3 \
        else f' xml:lang="{lang}"'
    body_attr = ' epub:type="cover"' if epub3 else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n' + doctype + "\n"
        f'<html xmlns="http://www.w3.org/1999/xhtml"{ns}>\n'
        "<head>\n<title>Cover</title>\n"
        '<style type="text/css">\nbody { margin: 0; padding: 0; text-align: center; }\n'
        "img { max-width: 100%; max-height: 100%; }\n</style>\n</head>\n"
        f'<body{body_attr}>\n<div><img src="{img_href}" alt="Cover"/></div>\n</body>\n</html>\n'
    ).encode("utf-8")


def _verify_opf(opf_bytes: bytes, img_id: str, img_href: str, doc_id: str) -> None:
    """The text edits above can silently miss on an unusual OPF (for example
    one written with a namespace prefix). Check the result before writing it."""
    try:
        root = ET.fromstring(opf_bytes)
    except ET.ParseError as exc:
        raise EpubError(f"the edited OPF is not well-formed: {exc}") from exc
    items = {it.get("id"): it for it in root.iter(f"{{{OPF_NS}}}item")}
    meta_ok = any(m.get("name") == "cover" and m.get("content") == img_id for m in root.iter(f"{{{OPF_NS}}}meta"))
    refs = [r.get("idref") for r in root.iter(f"{{{OPF_NS}}}itemref")]
    ok = (img_id in items and items[img_id].get("href") == img_href and doc_id in items and meta_ok
          and bool(refs) and refs[0] == doc_id and refs.count(doc_id) == 1)
    if not ok:
        raise EpubError("unsupported OPF structure (the cover could not be registered safely)")


def insert_cover(epub_path: str, cover_bytes: bytes, fmt: str = "jpg",
                 output_path: str | None = None) -> InsertResult:
    """Write a copy of the EPUB with ``cover_bytes`` as its cover."""
    try:
        return _insert_cover(epub_path, cover_bytes, fmt, output_path)
    except ZIP_ERRORS as exc:
        raise EpubError(f"damaged or unsupported EPUB: {exc}") from exc
    except UnicodeError as exc:
        raise EpubError(f"cannot read the OPF text: {exc}") from exc


def _insert_cover(epub_path: str, cover_bytes: bytes, fmt: str, output_path: str | None) -> InsertResult:
    info = inspect_epub(epub_path)
    if info.drm:
        raise DRMProtected(epub_path)
    media = MEDIA_FOR.get(fmt, "image/jpeg")
    ext = EXT_FOR[media]
    with _open_zip(epub_path) as zin:
        names = set(zin.namelist())
        opf_text, opf_enc, opf_bom = _decode_xml(zin.read(info.opf_path))
        nl = _newline(opf_text)
        opf_dir = posixpath.dirname(info.opf_path)
        items = dict(info.items)
        removed: set[str] = set()
        new_files: dict[str, bytes] = {}

        # ---- other documents that also show the current cover image
        others = [d for d in _docs_showing(zin, info.opf_path, info.items, info.spine, info.cover_image)
                  if info.cover_doc is None or d.id != info.cover_doc.id]

        # ---- image
        if info.epub3:
            opf_text = _strip_cover_image_property(opf_text)
        img = info.cover_image
        if img is not None and img.media_type == media:
            # same format: overwrite the file in place, keep id and href
            img_path, img_href, img_id = _resolve(info.opf_path, img.href), img.href, img.id
            replace_item = True
        elif img is not None and not others:
            # format changes and nothing else uses the old file: swap it out
            old_path = _resolve(info.opf_path, img.href)
            removed.add(old_path)
            folder = posixpath.dirname(old_path)
            stem = posixpath.splitext(posixpath.basename(old_path))[0] or "cover"
            img_path = _unique_name(names - removed, folder, stem, ext)
            img_href = _href_for(info.opf_path, img_path)
            img_id = img.id
            replace_item = True
        else:
            # no cover yet, or the old file is still shown elsewhere: add a new one
            img_path = _unique_name(names, opf_dir, "cover", ext)
            img_href = _href_for(info.opf_path, img_path)
            img_id = _unique_id(items, "cover-image")
            replace_item = False
        new_item = f'<item id="{img_id}" href="{img_href}" media-type="{media}"'
        if info.epub3:
            new_item += ' properties="cover-image"'
        new_item += "/>"
        span = _find_element(opf_text, "item", "id", img_id) if replace_item else None
        if span:
            opf_text = _replace_span(opf_text, span, new_item)
        else:
            opf_text = _insert_before_close(opf_text, "manifest", new_item)
        items[img_id] = ManifestItem(img_id, img_href, media)
        new_files[img_path] = cover_bytes

        # ---- <meta name="cover">
        opf_text = _remove_all(opf_text, "meta", "name", "cover")
        opf_text = _insert_before_close(opf_text, "metadata", f'<meta name="cover" content="{img_id}"/>')

        # ---- cover page
        if info.cover_doc is not None:
            doc_path = _resolve(info.opf_path, info.cover_doc.href)
            doc_id = info.cover_doc.id
        else:
            doc_path = _unique_name(names, opf_dir, "cover", ".xhtml")
            doc_href = _href_for(info.opf_path, doc_path)
            doc_id = _unique_id(items, "cover-page")
            opf_text = _insert_before_close(
                opf_text, "manifest", f'<item id="{doc_id}" href="{doc_href}" media-type="{XHTML_TYPE}"/>')
            items[doc_id] = ManifestItem(doc_id, doc_href, XHTML_TYPE)
        new_files[doc_path] = _cover_xhtml(_href_for(doc_path, img_path), info.epub3, info.language)
        opf_text = _remove_all(opf_text, "itemref", "idref", doc_id)
        opf_text = _insert_after_open(opf_text, "spine", f'<itemref idref="{doc_id}"/>')

        # ---- guide (EPUB 2, or when the file already has one)
        empty_guide = re.search(r"<guide\s*/>", opf_text)
        has_guide = empty_guide is not None or _open_tag(opf_text, "guide") is not None
        if has_guide or not info.epub3:
            ref = f'<reference type="cover" title="Cover" href="{_href_for(info.opf_path, doc_path)}"/>'
            if empty_guide:
                opf_text = opf_text[:empty_guide.start()] + f"<guide>{nl}    {ref}{nl}  </guide>" + opf_text[empty_guide.end():]
            elif has_guide:
                opf_text = _remove_all(opf_text, "reference", "type", "cover", ci=True)
                opf_text = _insert_before_close(opf_text, "guide", ref)
            else:
                opf_text = _insert_before_close(opf_text, "package", f"<guide>{nl}    {ref}{nl}  </guide>", indent="  ")
        opf_body = opf_text.encode(opf_enc)
        _verify_opf(opf_body, img_id, img_href, doc_id)
        new_files[info.opf_path] = opf_bom + opf_body

        dups = [d.href for d in others]

        # ---- write
        out = os.path.abspath(output_path or output_path_for(epub_path))
        folder = os.path.dirname(out)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = new_temp_file(folder)
        try:
            with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w") as zout:
                zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip",
                              compress_type=zipfile.ZIP_STORED)
                for zi in zin.infolist():
                    if zi.filename == "mimetype" or zi.filename in removed or zi.filename in new_files:
                        continue
                    if zi.is_dir():
                        continue
                    copy = zipfile.ZipInfo(zi.filename, zi.date_time)      # a fresh header: keeps time and
                    copy.compress_type = zi.compress_type                  # method, drops stale extra fields
                    copy.external_attr = zi.external_attr
                    zout.writestr(copy, zin.read(zi.filename))
                for name, data in new_files.items():
                    zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            out = move_no_overwrite(tmp, out)      # never replaces an existing file
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return InsertResult(output_path=out, replaced=info.cover_image is not None, duplicates=dups)
