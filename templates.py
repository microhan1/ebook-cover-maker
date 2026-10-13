"""The four text-only cover layouts.

Each layout takes a render.Canvas and draws onto it. Positions are fractions
of the cover width/height so every size preset looks the same. Every layout
ends with ``cv.publisher(...)``, which places the publisher inside the
bottom 10% no matter what else happened.
"""
from __future__ import annotations

TITLE_START, TITLE_MIN = 0.12, 0.045      # title font size, as a fraction of width
SUB_START, SUB_MIN = 0.05, 0.03
AUTHOR_START, AUTHOR_MIN = 0.045, 0.028
SERIES_START, SERIES_MIN = 0.036, 0.024


def _series_line(cv) -> str:
    o = cv.opts
    return " ".join(p for p in (o.series, o.volume) if p)


def layout_center(cv) -> None:
    """1. Solid background, everything centered."""
    o, w, h = cv.opts, cv.w, cv.h
    fg = cv.fg
    cv.block(_series_line(cv), "sans", (cv.px(0.10), cv.py(0.08), w - cv.px(0.10), cv.py(0.14)),
             cv.px(SERIES_START), cv.px(SERIES_MIN), 1, cv.accent)
    title_box = (cv.px(0.08), cv.py(0.20), w - cv.px(0.08), cv.py(0.58))
    tb = cv.block(o.title, o.font, title_box, cv.px(TITLE_START), cv.px(TITLE_MIN), 4, fg,
                  valign="middle", is_title=True)
    y = (tb[3] if tb else cv.py(0.40)) + cv.py(0.025)
    sb = cv.block(o.subtitle, "sans", (cv.px(0.12), y, w - cv.px(0.12), min(y + cv.py(0.16), cv.py(0.72))),
                  cv.px(SUB_START), cv.px(SUB_MIN), 2, fg)
    y = (sb[3] if sb else y) + cv.py(0.03)
    rule_y = max(y, cv.py(0.70))
    cv.rect((w // 2 - cv.px(0.06), rule_y, w // 2 + cv.px(0.06), rule_y + max(2, cv.py(0.003))), cv.accent)
    cv.block(o.author, "sans", (cv.px(0.12), rule_y + cv.py(0.03), w - cv.px(0.12), cv.py(0.86)),
             cv.px(AUTHOR_START), cv.px(AUTHOR_MIN), 2, fg)
    cv.publisher(fg)


def layout_bands(cv) -> None:
    """2. Accent band on top (title) and bottom (publisher)."""
    o, w, h = cv.opts, cv.w, cv.h
    cv.rect((0, 0, w, cv.py(0.36)), cv.accent)
    cv.rect((0, cv.py(0.88), w, h), cv.accent)
    on_band = _contrast(cv.accent) if cv.has_image else cv.bg
    cv.block(o.title, o.font, (cv.px(0.08), cv.py(0.05), w - cv.px(0.08), cv.py(0.31)),
             cv.px(TITLE_START), cv.px(TITLE_MIN), 4, on_band, valign="middle", is_title=True,
             shadow=False)
    cv.block(_series_line(cv), "sans", (cv.px(0.10), cv.py(0.40), w - cv.px(0.10), cv.py(0.46)),
             cv.px(SERIES_START), cv.px(SERIES_MIN), 1, cv.fg)
    sb = cv.block(o.subtitle, "sans", (cv.px(0.12), cv.py(0.48), w - cv.px(0.12), cv.py(0.66)),
                  cv.px(SUB_START), cv.px(SUB_MIN), 3, cv.fg, valign="middle")
    cv.block(o.author, "sans", (cv.px(0.12), cv.py(0.70), w - cv.px(0.12), cv.py(0.85)),
             cv.px(AUTHOR_START), cv.px(AUTHOR_MIN), 2, cv.fg, valign="middle")
    cv.publisher(on_band, shadow=False)


def layout_vertical(cv) -> None:
    """3. Vertical title down the left side, the rest aligned right."""
    o, w, h = cv.opts, cv.w, cv.h
    fg = cv.fg
    cv.rect((cv.px(0.07), cv.py(0.07), cv.px(0.07) + max(3, cv.px(0.008)), cv.py(0.70)), cv.accent)
    right = cv.vertical(o.title, o.font, cv.px(0.11), cv.py(0.08), cv.py(0.62),
                        cv.px(0.11), cv.px(TITLE_MIN), 3, fg, is_title=True)
    left = max(right + cv.px(0.04), cv.px(0.40))
    cv.block(_series_line(cv), "sans", (left, cv.py(0.08), w - cv.px(0.08), cv.py(0.13)),
             cv.px(SERIES_START), cv.px(SERIES_MIN), 1, cv.accent, align="right")
    cv.block(o.subtitle, "sans", (cv.px(0.40), cv.py(0.72), w - cv.px(0.08), cv.py(0.80)),
             cv.px(SUB_START), cv.px(SUB_MIN), 2, fg, align="right")
    cv.block(o.author, "sans", (cv.px(0.40), cv.py(0.81), w - cv.px(0.08), cv.py(0.87)),
             cv.px(AUTHOR_START), cv.px(AUTHOR_MIN), 1, fg, align="right", valign="bottom")
    cv.publisher(fg)


def layout_box(cv) -> None:
    """4. Translucent box over the (darkened) background image."""
    o, w, h = cv.opts, cv.w, cv.h
    fg = cv.fg
    box = (cv.px(0.08), cv.py(0.27), w - cv.px(0.08), cv.py(0.73))
    if cv.has_image:
        cv.rect(box, cv.bg, 200)
    else:
        cv.rect(box, _mix(cv.bg, fg, 0.06))          # a faint panel so the box still reads on a solid color
    b = max(2, cv.px(0.004))
    cv.rect((box[0], box[1], box[2], box[1] + b), cv.accent)
    cv.rect((box[0], box[3] - b, box[2], box[3]), cv.accent)
    # the series line sits on the photo itself, which is darkened: keep it light
    cv.block(_series_line(cv), "sans", (cv.px(0.10), cv.py(0.18), w - cv.px(0.10), cv.py(0.24)),
             cv.px(SERIES_START), cv.px(SERIES_MIN), 1, "#ffffff" if cv.has_image else fg)
    pad = cv.px(0.05)
    # text sits on the box, so no shadow is needed even over a photo
    tb = cv.block(o.title, o.font, (box[0] + pad, box[1] + cv.py(0.03), box[2] - pad, cv.py(0.56)),
                  cv.px(TITLE_START), cv.px(TITLE_MIN), 4, fg, valign="middle", is_title=True,
                  shadow=False)
    y = (tb[3] if tb else cv.py(0.50)) + cv.py(0.02)
    cv.block(o.subtitle, "sans", (box[0] + pad, y, box[2] - pad, cv.py(0.66)),
             cv.px(SUB_START), cv.px(SUB_MIN), 2, fg, shadow=False)
    cv.block(o.author, "sans", (box[0] + pad, cv.py(0.66), box[2] - pad, box[3] - cv.py(0.02)),
             cv.px(AUTHOR_START), cv.px(AUTHOR_MIN), 1, fg, valign="bottom", shadow=False)
    cv.publisher(fg, strip=cv.bg, strip_alpha=170 if cv.has_image else 255, shadow=False)


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(round(x + (y - x) * t)):02x}" for x, y in zip(ca, cb))


def _contrast(hexcolor: str) -> str:
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return "#000000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 else "#ffffff"


LAYOUTS = {1: layout_center, 2: layout_bands, 3: layout_vertical, 4: layout_box}
