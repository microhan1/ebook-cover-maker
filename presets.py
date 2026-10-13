"""Size presets, color schemes and bundled fonts for ebook-cover-maker.

Display names for all of these live in lang/*.json (preset_*, color_*,
font_*, template_*); this module only holds the values.
"""
from __future__ import annotations

# (width, height) in pixels. "custom" takes width/height from Options.
SIZE_PRESETS: dict[str, tuple[int, int] | None] = {
    "upaper": (800, 1200),      # upaper.net accepts 600-1000 px wide; 2:3 portrait
    "general": (1600, 2400),
    "square": (1200, 1200),
    "custom": None,
}
PRESET_KEYS = tuple(SIZE_PRESETS)
DEFAULT_PRESET = "upaper"
MIN_SIDE, MAX_SIDE = 300, 4000

# (background, text, accent). Accent is used for bands and boxes.
COLOR_SCHEMES: list[tuple[str, str, str]] = [
    ("#1f1f1f", "#f5f2ea", "#c9a961"),   # 1 charcoal / ivory / gold
    ("#f7f3ea", "#222222", "#b23a48"),   # 2 ivory / ink / crimson
    ("#0f2b46", "#ffffff", "#e0a458"),   # 3 navy / white / amber
    ("#2f4f3e", "#f1f4ec", "#d4c48f"),   # 4 forest / mist / straw
    ("#7a1f2b", "#fbeee6", "#f2c14e"),   # 5 wine / cream / yellow
    ("#e9e4d8", "#3b2f2f", "#8c6f4e"),   # 6 sand / cocoa / walnut
    ("#ffffff", "#111111", "#1a73e8"),   # 7 white / black / blue
    ("#000000", "#ffffff", "#ff5a5f"),   # 8 black / white / coral
]
DEFAULT_COLOR = 1

TEMPLATE_IDS = (1, 2, 3, 4)
DEFAULT_TEMPLATE = 1

# key -> file in fonts/. All three are Noto CJK OTCs (SIL Open Font License).
# Face index inside the collection: 0 = JP, 1 = KR, 2 = SC, 3 = TC, 4 = HK.
FONT_FILES: dict[str, str] = {
    "sans": "NotoSansCJK-Regular.ttc",
    "sans_bold": "NotoSansCJK-Bold.ttc",
    "serif": "NotoSerifCJK-Regular.ttc",
}
FONT_KEYS = tuple(FONT_FILES)
DEFAULT_FONT = "sans_bold"

FORMATS = ("jpg", "png")
DEFAULT_FORMAT = "jpg"
JPEG_QUALITY = 90

# The publisher name must sit inside the bottom 10% of the cover.
PUBLISHER_ZONE = 0.10
