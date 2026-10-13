"""Put a cover image into an EPUB: replace cover.jpg, register it in the OPF
(<meta name="cover">, cover-image property), and make the cover page the
first spine item. The original EPUB is never touched; the result is written
as <name>_cover.epub next to it.

The OPF is edited with targeted string replacements rather than re-serialized
from a DOM, so everything we do not touch stays byte-for-byte identical
(namespace prefixes, comments, ordering).
"""
from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from render import unique_path

CONTAINER = "META-INF/container.xml"
ENCRYPTION = "META-INF/encryption.xml"
RIGHTS = "META-INF/rights.xml"
OPF_NS = "http://www.idpf.org/2007/opf"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
FONT_OBFUSCATION = ("http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/svg+xml", "image/webp")
XHTML_TYPE = "application/xhtml+xml"
MEDIA_FOR = {"jpg": "image/jpeg", "png": "image/png"}
EXT_FOR = {"image/jpeg": ".jpg", "image/png": ".png"}


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


# ---------------------------------------------------------------- reading
def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r'''([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')''', tag):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def _find_element(text: str, tag: str, attr: str | None = None, value: str | None = None,
                  start: int = 0) -> tuple[int, int, str] | None:
    """Span of the first <tag ...> element (self-closing or with its end tag,
    assuming it has no nested <tag>) whose attribute matches. Returns
    (start, end, start_tag_text)."""
    for m in re.finditer(rf"<{tag}\b[^>]*>", text[start:]):
        s = start + m.start()
        head = m.group(0)
        if attr is not None:
            if _attrs(head).get(attr) != value:
                continue
        if head.endswith("/>"):
            return s, start + m.end(), head
        close = re.compile(rf"</{tag}\s*>").search(text, start + m.end())
        end = close.end() if close else start + m.end()
        return s, end, head
    return None


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


def _resolve(opf_path: str, href: str) -> str:
    href = href.split("#", 1)[0]
    return posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))


def _image_refs(xhtml: bytes) -> list[str]:
    text = xhtml.decode("utf-8", "replace")
    refs = re.findall(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', text, flags=re.I)
    refs += re.findall(r'<image\b[^>]*\b(?:xlink:)?href\s*=\s*["\']([^"\']+)["\']', text, flags=re.I)
    return refs


def inspect_epub(path: str) -> EpubInfo:
    """Read the OPF and find the current cover image and cover page."""
    with _open_zip(path) as zf:
        opf_path = _opf_path(zf)
        drm = _is_drm(zf)
        try:
            root = ET.fromstring(zf.read(opf_path))
        except ET.ParseError as exc:
            raise NotEpub(opf_path) from exc
        version = root.get("version", "2.0")
        language = "en"
        for el in root.iter("{http://purl.org/dc/elements/1.1/}language"):
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
            if doc_path in zf.namelist():
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
        cover_doc = guide_doc
        if cover_doc is None:
            cover_doc = _doc_showing(zf, opf_path, items, spine, cover_image)
        if cover_doc is None and spine:
            first = items[spine[0]]
            if "cover" in (first.id + " " + first.href).lower():
                cover_doc = first
    return EpubInfo(path=path, opf_path=opf_path, version=version, drm=drm,
                    cover_image=cover_image, cover_doc=cover_doc, items=items, spine=spine, language=language)


def _doc_showing(zf: zipfile.ZipFile, opf_path: str, items: dict[str, ManifestItem],
                 spine: list[str], image: ManifestItem | None) -> ManifestItem | None:
    for doc in _docs_showing(zf, opf_path, items, spine, image):
        return doc
    return None


def _docs_showing(zf: zipfile.ZipFile, opf_path: str, items: dict[str, ManifestItem],
                  spine: list[str], image: ManifestItem | None) -> list[ManifestItem]:
    if image is None:
        return []
    target = _resolve(opf_path, image.href)
    found = []
    order = spine + [i for i in items if i not in spine]
    for idref in order:
        it = items[idref]
        if it.media_type != XHTML_TYPE:
            continue
        doc_path = _resolve(opf_path, it.href)
        if doc_path not in zf.namelist():
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


def _cover_xhtml(img_rel: str, epub3: bool, language: str = "en") -> bytes:
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
        f'<body{body_attr}>\n<div><img src="{img_rel}" alt="Cover"/></div>\n</body>\n</html>\n'
    ).encode("utf-8")


def _replace_span(text: str, span: tuple[int, int, str] | None, new: str) -> str:
    if span is None:
        return text
    return text[:span[0]] + new + text[span[1]:]


def _remove_all(text: str, tag: str, attr: str, value: str) -> str:
    while True:
        span = _find_element(text, tag, attr, value)
        if span is None:
            return text
        text = _replace_span(text, span, "")


def _insert_before_close(text: str, tag: str, new: str) -> str:
    m = re.search(rf"</{tag}\s*>", text)
    if m is None:
        return text
    return text[:m.start()] + new + text[m.start():]


def _insert_after_open(text: str, tag: str, new: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>", text)
    if m is None:
        return text
    return text[:m.end()] + new + text[m.end():]


def _strip_cover_image_property(text: str) -> str:
    def fix(m: re.Match) -> str:
        head = m.group(0)
        attrs = _attrs(head)
        props = attrs.get("properties", "").split()
        if "cover-image" not in props:
            return head
        props = [p for p in props if p != "cover-image"]
        if props:
            return re.sub(r'\bproperties\s*=\s*(["\'])[^"\']*\1', f'properties="{" ".join(props)}"', head)
        return re.sub(r'\s+properties\s*=\s*(["\'])[^"\']*\1', "", head)

    return re.sub(r"<item\b[^>]*>", fix, text)


def insert_cover(epub_path: str, cover_bytes: bytes, fmt: str = "jpg",
                 output_path: str | None = None) -> InsertResult:
    """Write a copy of the EPUB with ``cover_bytes`` as its cover."""
    info = inspect_epub(epub_path)
    if info.drm:
        raise DRMProtected(epub_path)
    media = MEDIA_FOR.get(fmt, "image/jpeg")
    ext = EXT_FOR[media]
    with _open_zip(epub_path) as zin:
        names = set(zin.namelist())
        opf_text = zin.read(info.opf_path).decode("utf-8")
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
            img_href = posixpath.relpath(img_path, opf_dir) if opf_dir else img_path
            img_id = img.id
            replace_item = True
        else:
            # no cover yet, or the old file is still shown elsewhere: add a new one
            img_path = _unique_name(names, opf_dir, "cover", ext)
            img_href = posixpath.relpath(img_path, opf_dir) if opf_dir else img_path
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
            opf_text = _insert_before_close(opf_text, "manifest", "    " + new_item + "\n")
        items[img_id] = ManifestItem(img_id, img_href, media)
        new_files[img_path] = cover_bytes

        # ---- <meta name="cover">
        opf_text = _remove_all(opf_text, "meta", "name", "cover")
        opf_text = _insert_before_close(opf_text, "metadata", f'    <meta name="cover" content="{img_id}"/>\n')

        # ---- cover page
        if info.cover_doc is not None:
            doc = info.cover_doc
            doc_path = _resolve(info.opf_path, doc.href)
            doc_id = doc.id
        else:
            doc_path = _unique_name(names, opf_dir, "cover", ".xhtml")
            doc_href = posixpath.relpath(doc_path, opf_dir) if opf_dir else doc_path
            doc_id = _unique_id(items, "cover-page")
            opf_text = _insert_before_close(
                opf_text, "manifest", f'    <item id="{doc_id}" href="{doc_href}" media-type="{XHTML_TYPE}"/>\n')
            items[doc_id] = ManifestItem(doc_id, doc_href, XHTML_TYPE)
        img_rel = posixpath.relpath(img_path, posixpath.dirname(doc_path) or ".")
        new_files[doc_path] = _cover_xhtml(img_rel, info.epub3, info.language)
        opf_text = _remove_all(opf_text, "itemref", "idref", doc_id)
        opf_text = _insert_after_open(opf_text, "spine", f'\n    <itemref idref="{doc_id}"/>')

        # ---- guide (EPUB 2, or when the file already has one)
        has_guide = re.search(r"<guide\b", opf_text) is not None
        if has_guide or not info.epub3:
            doc_href_from_opf = posixpath.relpath(doc_path, opf_dir) if opf_dir else doc_path
            ref = f'<reference type="cover" title="Cover" href="{doc_href_from_opf}"/>'
            if has_guide:
                opf_text = _remove_all(opf_text, "reference", "type", "cover")
                opf_text = _insert_before_close(opf_text, "guide", "    " + ref + "\n")
            else:
                opf_text = _insert_before_close(opf_text, "package", "  <guide>\n    " + ref + "\n  </guide>\n")
        new_files[info.opf_path] = opf_text.encode("utf-8")

        dups = [d.href for d in others]

        # ---- write
        out = output_path or output_path_for(epub_path)
        out = unique_path(out)
        tmp = out + ".part"
        with zipfile.ZipFile(tmp, "w") as zout:
            zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for zi in zin.infolist():
                if zi.filename == "mimetype" or zi.filename in removed or zi.filename in new_files:
                    continue
                if zi.is_dir():
                    continue
                zout.writestr(zi, zin.read(zi.filename), compress_type=zi.compress_type)
            for name, data in new_files.items():
                zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, out)
    return InsertResult(output_path=out, replaced=info.cover_image is not None, duplicates=dups)
