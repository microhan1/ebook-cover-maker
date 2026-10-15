"""Shared test helpers. Import this module FIRST in every test file.

It points the settings file at a temp folder so no test ever touches a real
settings.json, and puts the repository root on sys.path.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import i18n  # noqa: E402

_SETTINGS_DIR = tempfile.mkdtemp(prefix="ecm_settings_")
i18n.SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "settings.json")

import unittest  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

import update_check  # noqa: E402

# ------------------------------------------------------------------ never reach the real server
# The window asks the 책갈피 server for a new version on its own 1.5 s after it opens, and the
# settings file is isolated per test, so the once-a-day guard is always open. Three layers:
#   1. update_check.fetch (what check_async calls when no fetcher is given) -> _offline
#   2. the real fetch's default opener and urllib.request.urlopen itself  -> _guard_urlopen
#   3. every test that made layer 2 record something fails (see _guarded_run)
# Layer 1 calls are expected (every window start makes one) and are only counted.
# Tests of the request itself use REAL_FETCH with an explicit fake opener.
REAL_FETCH = update_check.fetch
REAL_URLOPEN = urllib.request.urlopen
NETWORK_CALLS: list = []          # (test, slug, version): stopped at layer 1, by design
BLOCKED: list = []                # (test, url): reached layer 2 - a test bug, the test fails
_CURRENT = ["(no test)"]


def _offline(slug, version, **kwargs):
    NETWORK_CALLS.append((_CURRENT[0], slug, version))
    return None


def _guard_urlopen(req, *args, **kwargs):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if "chaekgalpi.co.kr" in url:
        BLOCKED.append((_CURRENT[0], url))
        raise urllib.error.URLError("blocked in tests: the real 책갈피 server must not be called")
    return REAL_URLOPEN(req, *args, **kwargs)


update_check.fetch = _offline
urllib.request.urlopen = _guard_urlopen
if REAL_FETCH.__kwdefaults__ and "opener" in REAL_FETCH.__kwdefaults__:
    REAL_FETCH.__kwdefaults__["opener"] = _guard_urlopen

_orig_run = unittest.TestCase.run


def _guarded_run(self, result=None):
    _CURRENT[0] = self.id()
    before = len(BLOCKED)
    try:
        return _orig_run(self, result)
    finally:
        new = BLOCKED[before:]
        if new and result is not None:
            try:
                raise AssertionError(f"this test tried to reach the real 책갈피 server: {[u for _, u in new]}")
            except AssertionError:
                result.addFailure(self, sys.exc_info())
        _CURRENT[0] = "(no test)"


unittest.TestCase.run = _guarded_run

from PIL import Image, ImageStat  # noqa: E402

import render  # noqa: E402

EPUBCHECK = os.environ.get("EPUBCHECK_JAR", "")


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ------------------------------------------------------------------ colors
def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def hex_rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def contrast(a, b) -> float:
    """WCAG contrast ratio between two colors (hex strings or RGB tuples)."""
    a = hex_rgb(a) if isinstance(a, str) else a
    b = hex_rgb(b) if isinstance(b, str) else b
    la, lb = rel_lum(a), rel_lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def median_rgb(img: Image.Image, box) -> tuple[int, int, int] | None:
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return tuple(int(v) for v in ImageStat.Stat(img.crop((x0, y0, x1, y1)).convert("RGB")).median)  # type: ignore[return-value]


# ------------------------------------------------------------------ draw recorder
@contextlib.contextmanager
def record_draws():
    """Record every text draw as (text, x0, y0, x1, y1, color, size) in cover
    pixels, *before* clipping to the canvas, so overflow is visible."""
    rects: list[dict] = []
    orig_draw = render.Text.draw
    orig_rot = render.Canvas._paste_rotated
    inside_rotation = [False]

    def spy_draw(self, draw, x, y_top, s, fill, shadow=None):
        if not inside_rotation[0]:                       # the rotated helper draws onto a scratch image first
            w = self.width(s)
            rects.append(dict(text=s, x0=x, y0=y_top, x1=x + w, y1=y_top + self.size, color=fill,
                              size=self.size, rotated=False))
        return orig_draw(self, draw, x, y_top, s, fill, shadow)

    def spy_rot(self, s, tx, cx, y, color):
        w = tx.width(s)
        rects.append(dict(text=s, x0=cx - tx.size / 2, y0=y, x1=cx + tx.size / 2, y1=y + w, color=color,
                          size=tx.size, rotated=True))
        inside_rotation[0] = True
        try:
            return orig_rot(self, s, tx, cx, y, color)
        finally:
            inside_rotation[0] = False

    render.Text.draw = spy_draw
    render.Canvas._paste_rotated = spy_rot
    try:
        yield rects
    finally:
        render.Text.draw = orig_draw
        render.Canvas._paste_rotated = orig_rot


def overlaps(a: dict, b: dict, shrink: float = 0.12) -> bool:
    """Do two text rectangles intersect, after trimming each by ``shrink`` of
    its height on every side (line spacing and glyph side bearings)?"""
    def box(r):
        m = r["size"] * shrink
        return r["x0"] + m, r["y0"] + m, r["x1"] - m, r["y1"] - m
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


# ------------------------------------------------------------------ images
def make_photo(path: str, kind: str = "mid", size: tuple[int, int] = (1200, 1800)) -> str:
    w, h = size
    if kind == "mid":
        img = Image.new("RGB", size, (128, 128, 128))
    elif kind == "white":
        img = Image.new("RGB", size, (252, 252, 252))
    elif kind == "black":
        img = Image.new("RGB", size, (3, 3, 3))
    elif kind == "noise":
        img = Image.effect_noise(size, 80).convert("RGB")
    elif kind == "gradient":
        base = Image.linear_gradient("L").resize(size)
        img = Image.merge("RGB", (base, base.transpose(Image.Transpose.FLIP_TOP_BOTTOM), base))
    else:
        raise ValueError(kind)
    img.save(path)
    return path


# ------------------------------------------------------------------ cli
def run_cli(*args: str, stdin: str | None = None, timeout: int = 120, cwd: str | None = None):
    """Run ``python main.py`` and return (returncode, stdout, stderr)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input=stdin, timeout=timeout, cwd=cwd or ROOT, env=env)
    return r.returncode, r.stdout, r.stderr


def epubcheck_errors(path: str) -> list[str]:
    """ERROR/FATAL lines epubcheck reports ([] when the jar is not configured)."""
    if not EPUBCHECK:
        return []
    r = subprocess.run(["java", "-jar", EPUBCHECK, "--quiet", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith(("ERROR", "FATAL"))]
