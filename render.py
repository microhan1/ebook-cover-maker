"""Cover rendering for ebook-cover-maker: fonts, wrapping, auto-size, saving.

Everything the GUI and the CLI share lives here. ``render(opts)`` returns a
Pillow image plus warnings; ``save_cover`` writes it without ever
overwriting an existing file.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import presets
import templates
from i18n import resource_dir

FONT_DIR = os.path.join(resource_dir(), "fonts")
LINE_SPACING = 1.25            # line height as a multiple of the font size
SHRINK_STEP = 0.92             # auto-size shrinks by this factor per attempt
DARKEN = 0.45                  # how much a background photo is darkened (0..1)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# One token per CJK/Hangul/kana character, whitespace runs, or other runs.
_CJK = (
    "\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff"
    "\uac00-\ud7af\uf900-\ufaff\U00020000-\U0002ebef\u3000-\u303f\uff00-\uffef"
)
TOKEN_RE = re.compile(rf"\s+|[{_CJK}]|[^\s{_CJK}]+")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ebef]")
_CJK_CHAR_RE = re.compile(rf"[{_CJK}]")


class RenderError(Exception):
    """Something needed for rendering is missing (fonts, background image)."""


class FontMissing(RenderError):
    pass


class BackgroundOpenError(RenderError):
    pass


# ---------------------------------------------------------------- options
@dataclass
class Options:
    title: str = ""
    subtitle: str = ""
    author: str = ""
    publisher: str = ""
    series: str = ""
    volume: str = ""
    preset: str = presets.DEFAULT_PRESET
    width: int = 800
    height: int = 1200
    template: int = presets.DEFAULT_TEMPLATE
    color: int = presets.DEFAULT_COLOR
    bg_color: str = ""            # "#rrggbb" overrides the scheme, "" keeps it
    fg_color: str = ""
    bg_image: str = ""
    font: str = presets.DEFAULT_FONT
    fmt: str = presets.DEFAULT_FORMAT

    def validated(self) -> "Options":
        def s(v) -> str:
            return v.strip() if isinstance(v, str) else ""

        def hexcolor(v) -> str:
            return v.strip().lower() if isinstance(v, str) and HEX_RE.match(v.strip()) else ""

        preset = self.preset if self.preset in presets.SIZE_PRESETS else presets.DEFAULT_PRESET
        size = presets.SIZE_PRESETS[preset]
        if size is None:
            width = _clamp_int(self.width, presets.MIN_SIDE, presets.MAX_SIDE, 800)
            height = _clamp_int(self.height, presets.MIN_SIDE, presets.MAX_SIDE, 1200)
        else:
            width, height = size
        try:
            template = int(self.template)
        except (TypeError, ValueError):
            template = presets.DEFAULT_TEMPLATE
        if template not in presets.TEMPLATE_IDS:
            template = presets.DEFAULT_TEMPLATE
        color = _clamp_int(self.color, 1, len(presets.COLOR_SCHEMES), presets.DEFAULT_COLOR)
        return Options(
            title=s(self.title), subtitle=s(self.subtitle), author=s(self.author),
            publisher=s(self.publisher), series=s(self.series), volume=s(self.volume),
            preset=preset, width=width, height=height, template=template, color=color,
            bg_color=hexcolor(self.bg_color), fg_color=hexcolor(self.fg_color),
            bg_image=s(self.bg_image),
            font=self.font if self.font in presets.FONT_FILES else presets.DEFAULT_FONT,
            fmt=self.fmt if self.fmt in presets.FORMATS else presets.DEFAULT_FORMAT,
        )

    def colors(self) -> tuple[str, str, str]:
        bg, fg, accent = presets.COLOR_SCHEMES[self.color - 1]
        return (self.bg_color or bg, self.fg_color or fg, accent)


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@dataclass
class RenderResult:
    image: Image.Image
    warnings: list[tuple[str, dict]] = field(default_factory=list)
    publisher_box: tuple[int, int, int, int] | None = None
    title_size: int = 0


# ---------------------------------------------------------------- fonts
_font_cache: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}
_cmap_cache: dict[tuple[str, int], set[int]] = {}


def font_path(key: str) -> str:
    return os.path.join(FONT_DIR, presets.FONT_FILES[key])


def fonts_available() -> list[str]:
    """Font keys whose file is missing from fonts/ (empty list = all present)."""
    return [k for k in presets.FONT_KEYS if not os.path.isfile(font_path(k))]


def face_index(text: str) -> int:
    """Pick the regional face of the Noto CJK collection from the script used.
    Every face covers all CJK characters; only glyph shapes differ."""
    if _KANA_RE.search(text):
        return 0
    if _HANGUL_RE.search(text):
        return 1
    if _HAN_RE.search(text):
        return 2
    return 1


def load_font(key: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    size = max(4, int(size))
    ck = (key, index, size)
    f = _font_cache.get(ck)
    if f is None:
        path = font_path(key)
        if not os.path.isfile(path):
            raise FontMissing(path)
        try:
            f = ImageFont.truetype(path, size, index=index)
        except OSError as exc:
            raise FontMissing(path) from exc
        if len(_font_cache) > 256:
            _font_cache.clear()
        _font_cache[ck] = f
    return f


def _cmap(key: str, index: int) -> set[int]:
    ck = (key, index)
    cm = _cmap_cache.get(ck)
    if cm is None:
        try:
            from fontTools.ttLib import TTFont

            tt = TTFont(font_path(key), fontNumber=index, lazy=True)
            cm = set((tt["cmap"].getBestCmap() or {}).keys())
            tt.close()
        except Exception:
            cm = set()
        _cmap_cache[ck] = cm
    return cm


def _covers(key: str, index: int, ch: str) -> bool:
    cm = _cmap(key, index)
    return not cm or ord(ch) in cm    # unknown coverage: assume yes, FreeType shows .notdef


def split_runs(text: str, key: str, index: int) -> tuple[list[tuple[str, str]], str]:
    """Break text into (run, font_key) pieces so characters the chosen font
    lacks are drawn with another bundled font. Returns the runs and the
    characters no bundled font has (they render as boxes)."""
    runs: list[tuple[str, str]] = []
    missing: list[str] = []
    for ch in text:
        use = key
        if not ch.isspace() and not _covers(key, index, ch):
            for alt in presets.FONT_KEYS:
                if alt != key and _covers(alt, index, ch) and _cmap(alt, index):
                    use = alt
                    break
            else:
                if ch not in missing:
                    missing.append(ch)
        if runs and runs[-1][1] == use:
            runs[-1] = (runs[-1][0] + ch, use)
        else:
            runs.append((ch, use))
    return runs, "".join(missing)


class Text:
    """A font choice (key + regional face + size) with measuring and drawing."""

    def __init__(self, key: str, index: int, size: int) -> None:
        self.key, self.index, self.size = key, index, max(4, int(size))
        self.font = load_font(key, index, self.size)
        self.ascent, self.descent = self.font.getmetrics()
        self.line_height = int(round(self.size * LINE_SPACING))

    def resized(self, size: int) -> "Text":
        return Text(self.key, self.index, size)

    def width(self, s: str) -> float:
        runs, _ = split_runs(s, self.key, self.index)
        return sum(load_font(k, self.index, self.size).getlength(r) for r, k in runs)

    def draw(self, draw: ImageDraw.ImageDraw, x: float, y_top: float, s: str, fill: str,
             shadow: str | None = None) -> None:
        """Draw one line with its top-left at (x, y_top)."""
        runs, _ = split_runs(s, self.key, self.index)
        base = y_top + self.ascent
        for r, k in runs:
            f = load_font(k, self.index, self.size)
            if shadow:
                off = max(1, self.size // 28)
                draw.text((x + off, base + off), r, font=f, fill=shadow, anchor="ls")
            draw.text((x, base), r, font=f, fill=fill, anchor="ls")
            x += f.getlength(r)


# ---------------------------------------------------------------- wrapping
def wrap(text: str, measure, max_width: float) -> list[str]:
    """Greedy word wrap. CJK characters break anywhere, Latin words at spaces,
    and a single word wider than the box is split between characters."""
    lines: list[str] = []
    for para in text.replace("\r", "").split("\n"):
        cur = ""
        for tok in TOKEN_RE.findall(para):
            if tok.isspace():
                if cur:
                    cur += " "
                continue
            cand = cur + tok
            if measure(cand) <= max_width:
                cur = cand
                continue
            if cur.strip():
                lines.append(cur.rstrip())
                cur = ""
            if measure(tok) <= max_width or len(tok) == 1:
                cur = tok
                continue
            for ch in tok:                          # oversized single token
                if cur and measure(cur + ch) > max_width:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur.rstrip())
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines


def fit_block(text: str, key: str, index: int, max_w: float, max_h: float, start: int,
              min_size: int, max_lines: int) -> tuple[Text, list[str], bool]:
    """Shrink from ``start`` until the wrapped text fits the box. Returns the
    Text (font at the final size), the lines, and whether it had to stop at
    ``min_size`` without fitting."""
    size = max(int(start), int(min_size))
    while True:
        tx, lines, fits = _try_size(text, key, index, size, max_w, max_h, max_lines)
        if fits:
            return _avoid_orphan(text, key, index, size, min_size, max_w, max_h, max_lines, tx, lines)
        if size <= min_size:
            return tx, lines, True
        size = max(int(min_size), int(size * SHRINK_STEP))


def _try_size(text: str, key: str, index: int, size: int, max_w: float, max_h: float,
              max_lines: int) -> tuple[Text, list[str], bool]:
    tx = Text(key, index, size)
    lines = wrap(text, tx.width, max_w)
    fits = len(lines) <= max_lines and len(lines) * tx.line_height <= max_h \
        and all(tx.width(ln) <= max_w for ln in lines)
    return tx, lines, fits


def _avoid_orphan(text, key, index, size, min_size, max_w, max_h, max_lines, tx, lines):
    """A last line of one or two characters looks broken. If shrinking a
    little removes a line, take the smaller size."""
    if len(lines) < 2 or len(lines[-1].strip()) > 2:
        return tx, lines, False
    for _ in range(3):
        size = max(int(min_size), int(size * SHRINK_STEP))
        tx2, lines2, fits = _try_size(text, key, index, size, max_w, max_h, max_lines)
        if fits and len(lines2) < len(lines):
            return tx2, lines2, False
        if size <= min_size:
            break
    return tx, lines, False


# ---------------------------------------------------------------- canvas
class Canvas:
    """What a template gets: the image, the options, colors, and text helpers
    that record warnings. Coordinates given to helpers are absolute pixels."""

    def __init__(self, image: Image.Image, opts: Options) -> None:
        self.image = image
        self.opts = opts
        self.w, self.h = image.size
        self.bg, self.fg, self.accent = opts.colors()
        self.has_image = False
        self.warnings: list[tuple[str, dict]] = []
        self.publisher_box: tuple[int, int, int, int] | None = None
        self.title_size = 0
        self.draw = ImageDraw.Draw(image)
        self._missing: list[str] = []

    # sizes as fractions of the width keep every preset looking the same
    def px(self, frac: float) -> int:
        return max(1, int(round(self.w * frac)))

    def py(self, frac: float) -> int:
        return max(1, int(round(self.h * frac)))

    def shadow(self, wanted: bool = True) -> str | None:
        if not self.has_image or not wanted:
            return None
        return "#000000" if _luminance(self.fg) > 0.5 else "#ffffff"

    def rect(self, box: tuple[int, int, int, int], color: str, alpha: int = 255) -> None:
        if alpha >= 255:
            self.draw.rectangle(box, fill=color)
            return
        overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(box, fill=_rgba(color, alpha))
        self.image.alpha_composite(overlay)
        self.draw = ImageDraw.Draw(self.image)

    def _note_missing(self, chars: str) -> None:
        for ch in chars:
            if ch not in self._missing:
                self._missing.append(ch)

    def block(self, text: str, key: str, box: tuple[int, int, int, int], start: int,
              min_size: int, max_lines: int, color: str, align: str = "center",
              valign: str = "top", is_title: bool = False,
              shadow: bool = True) -> tuple[int, int, int, int] | None:
        """Wrap and auto-size ``text`` into ``box``. Returns the drawn bbox."""
        if not text:
            return None
        x0, y0, x1, y1 = box
        index = face_index(text)
        tx, lines, too_small = fit_block(text, key, index, x1 - x0, y1 - y0, start, min_size, max_lines)
        if is_title:
            self.title_size = tx.size
            if too_small:
                self.warnings.append(("warn_title_long", {}))
        _, missing = split_runs(text, key, index)
        self._note_missing(missing)
        total_h = len(lines) * tx.line_height
        if valign == "middle":
            y = y0 + max(0, (y1 - y0 - total_h) // 2)
        elif valign == "bottom":
            y = y1 - total_h
        else:
            y = y0
        top = y
        widest = 0.0
        for ln in lines:
            wdt = tx.width(ln)
            widest = max(widest, wdt)
            if align == "center":
                x = x0 + (x1 - x0 - wdt) / 2
            elif align == "right":
                x = x1 - wdt
            else:
                x = x0
            tx.draw(self.draw, x, y, ln, color, self.shadow(shadow))
            y += tx.line_height
        if align == "center":
            bx0 = int(x0 + (x1 - x0 - widest) / 2)
        elif align == "right":
            bx0 = int(x1 - widest)
        else:
            bx0 = x0
        return (bx0, top, int(bx0 + widest), y)

    def vertical(self, text: str, key: str, x0: int, y0: int, max_h: int, start: int,
                 min_size: int, max_cols: int, color: str, is_title: bool = False) -> int:
        """Draw ``text`` top-to-bottom in columns starting at x0 (columns run
        right-to-left, the traditional order). Latin runs are rotated 90\u00b0.
        Returns the right edge of the drawn area."""
        if not text:
            return x0
        index = face_index(text)
        size = max(int(start), int(min_size))
        while True:
            tx = Text(key, index, size)
            cols, cell = _vertical_layout(text, tx, max_h)
            if len(cols) <= max_cols or size <= min_size:
                break
            size = max(int(min_size), int(size * SHRINK_STEP))
        if is_title:
            self.title_size = size
            if len(cols) > max_cols:
                self.warnings.append(("warn_title_long", {}))
        _, missing = split_runs(text, key, index)
        self._note_missing(missing)
        colw = int(size * 1.2)
        n = len(cols)
        for ci, col in enumerate(cols):
            cx = x0 + (n - 1 - ci) * colw + colw // 2
            y = y0
            for item, length in col:
                if _CJK_CHAR_RE.match(item) or len(item) == 1:
                    tx.draw(self.draw, cx - tx.width(item) / 2, y, item, color, self.shadow())
                else:
                    self._paste_rotated(item, tx, cx, y, color)
                y += length
        return x0 + n * colw

    def _paste_rotated(self, s: str, tx: Text, cx: int, y: int, color: str) -> None:
        wdt = int(tx.width(s)) + 4
        tmp = Image.new("RGBA", (wdt, tx.line_height + 4), (0, 0, 0, 0))
        tx.draw(ImageDraw.Draw(tmp), 2, 2, s, color, self.shadow())
        rot = tmp.rotate(-90, expand=True)
        self.image.alpha_composite(rot, (int(cx - rot.width / 2), int(y)))
        self.draw = ImageDraw.Draw(self.image)

    def publisher(self, color: str, strip: str | None = None, strip_alpha: int = 255,
                  shadow: bool = True) -> None:
        """The publisher name, always inside the bottom 10% of the cover."""
        name = self.opts.publisher
        zone_top = int(self.h * (1 - presets.PUBLISHER_ZONE))
        if strip:
            self.rect((0, zone_top, self.w, self.h), strip, strip_alpha)
        if not name:
            return
        pad = self.py(0.02)
        box = (self.px(0.08), zone_top + pad, self.w - self.px(0.08), self.h - pad)
        bbox = self.block(name, "sans", box, self.py(0.03), self.py(0.012), 1, color,
                          align="center", valign="middle", shadow=shadow)
        self.publisher_box = bbox

    def publisher_check(self) -> None:
        """Never trust the layout: verify the drawn publisher sits in the zone."""
        if not self.opts.publisher:
            return
        box = self.publisher_box
        zone_top = int(self.h * (1 - presets.PUBLISHER_ZONE))
        if box is None or box[1] < zone_top or box[3] > self.h:
            self.warnings.append(("warn_publisher_zone", {}))

    def finish(self) -> None:
        if self._missing:
            self.warnings.append(("warn_font_missing", {"chars": "".join(self._missing)}))


def _vertical_layout(text: str, tx: Text, max_h: int) -> tuple[list[list[tuple[str, int]]], int]:
    """Split text into columns of (item, length). CJK characters take one
    cell each; a Latin run takes its rotated width."""
    cell = int(tx.size * 1.12)
    items: list[tuple[str, int]] = []
    for tok in TOKEN_RE.findall(text.replace("\n", " ")):
        if tok.isspace():
            items.append((" ", cell // 2))
        elif _CJK_CHAR_RE.match(tok):
            items.append((tok, cell))
        else:
            items.append((tok, int(tx.width(tok)) + cell // 4))
    cols: list[list[tuple[str, int]]] = [[]]
    used = 0
    for item, length in items:
        if used + length > max_h and cols[-1]:
            cols.append([])
            used = 0
            if item == " ":
                continue
        cols[-1].append((item, length))
        used += length
    return cols, cell


def _luminance(hexcolor: str) -> float:
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgba(hexcolor: str, alpha: int) -> tuple[int, int, int, int]:
    return (int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16), alpha)


# ---------------------------------------------------------------- background
def load_background(path: str, w: int, h: int) -> tuple[Image.Image, list[tuple[str, dict]]]:
    """Crop the user's photo to the cover size (centered) and darken it so
    text stays readable. Upscaled photos are blurred and reported."""
    warnings: list[tuple[str, dict]] = []
    try:
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            img = img.convert("RGB")
    except Exception as exc:
        raise BackgroundOpenError(path) from exc
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    img = ImageOps.fit(img, (w, h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if scale > 1.0:
        img = img.filter(ImageFilter.GaussianBlur(radius=min(12.0, 1.0 + scale)))
        warnings.append(("warn_bg_low_res", {"width": sw, "height": sh}))
    dark = Image.new("RGB", (w, h), "#000000")
    img = Image.blend(img, dark, DARKEN)
    return img, warnings


# ---------------------------------------------------------------- render
def render(opts: Options) -> RenderResult:
    opts = opts.validated()
    w, h = opts.width, opts.height
    bg, fg, accent = opts.colors()
    warnings: list[tuple[str, dict]] = []
    if opts.bg_image:
        base, warnings = load_background(opts.bg_image, w, h)
        image = base.convert("RGBA")
    else:
        image = Image.new("RGBA", (w, h), bg)
    cv = Canvas(image, opts)
    cv.has_image = bool(opts.bg_image)
    cv.warnings.extend(warnings)
    templates.LAYOUTS[opts.template](cv)
    cv.publisher_check()
    cv.finish()
    return RenderResult(image=image.convert("RGB"), warnings=cv.warnings,
                        publisher_box=cv.publisher_box, title_size=cv.title_size)


def publisher_in_zone(result: RenderResult) -> bool:
    return not any(k == "warn_publisher_zone" for k, _ in result.warnings)


# ---------------------------------------------------------------- saving
def unique_path(path: str) -> str:
    """cover.jpg -> cover(2).jpg -> cover(3).jpg while the name is taken (by
    anything, folders included)."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while True:
        cand = f"{base}({n}){ext}"
        if not os.path.lexists(cand):
            return cand
        n += 1


def normalize_output(path: str, fmt: str) -> str:
    """Make the extension match the format ("cover" -> "cover.jpg")."""
    base, ext = os.path.splitext(path)
    want = ".png" if fmt == "png" else ".jpg"
    if ext.lower() in (".jpg", ".jpeg") and fmt == "jpg":
        return path
    if ext.lower() == ".png" and fmt == "png":
        return path
    return path + want if ext.lower() not in (".jpg", ".jpeg", ".png") else base + want


def to_bytes(image: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "png":
        image.convert("RGB").save(buf, format="PNG", optimize=True)
    else:
        image.convert("RGB").save(buf, format="JPEG", quality=presets.JPEG_QUALITY, optimize=True,
                                  subsampling=0)
    return buf.getvalue()


def save_cover(image: Image.Image, path: str, fmt: str) -> str:
    """Write the cover next to a temp file then rename, so a crash never
    leaves a half-written file. Returns the final path (numbered if taken)."""
    path = unique_path(normalize_output(os.path.abspath(path), fmt))
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(to_bytes(image, fmt))
    os.replace(tmp, path)
    return path
