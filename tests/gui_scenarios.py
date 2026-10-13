"""Scenarios that need a brand-new window. Tk is only reliable with one window
per process, so test_gui.py runs each of these in its own process:

    python gui_scenarios.py <name> '<json arguments>'

A scenario prints OK and exits 0, or raises (exit code 1 with a traceback).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
import zipfile
from unittest import mock

import helpers  # noqa: F401  (redirects the settings file to a temp folder)
import epub_insert
import fixtures as fx
import i18n
import presets
import render
from gui_support import alive, pump, put, sleep_pump

import gui

ERRORS: list = []


def new_app():
    threading.excepthook = lambda args: ERRORS.append(("thread", args.exc_type, args.exc_value))
    i18n.set_lang("ko", persist=False)
    app = gui.App()
    app.root.withdraw()
    app.root.report_callback_exception = lambda *a: ERRORS.append(("tk", a[1], a[2]))
    for name in ("showerror", "showinfo", "showwarning", "askyesno"):
        setattr(gui.messagebox, name, lambda *a, **k: True)
    return app


def write_settings(content: str) -> None:
    if os.path.exists(i18n.SETTINGS_PATH):
        os.remove(i18n.SETTINGS_PATH)
    put(i18n.SETTINGS_PATH, content)


def close_and_wait(app, timeout: float = 6.0) -> None:
    app._on_close()
    assert pump(app, lambda: not alive(app), timeout), "the window did not close"


def settings_variant(content: str) -> None:
    write_settings(content)
    app = new_app()
    o = app.options()
    assert o.preset in presets.PRESET_KEYS, o
    assert o.template in presets.TEMPLATE_IDS, o
    assert presets.MIN_SIDE <= o.width <= presets.MAX_SIDE and presets.MIN_SIDE <= o.height <= presets.MAX_SIDE, o
    sleep_pump(app, 0.4)                                      # let the first preview run
    close_and_wait(app)
    assert not ERRORS, ERRORS


def saved_choices() -> None:
    write_settings(json.dumps({"options": {"template": 3, "color": 5, "preset": "square", "font": "serif", "fmt": "png",
                                           "publisher": "내 출판사", "author": "내 이름"}}))
    app = new_app()
    assert app.cmb_template.current() == 2
    assert app.cmb_color.current() == 4
    assert presets.PRESET_KEYS[app.cmb_preset.current()] == "square"
    assert presets.FONT_KEYS[app.cmb_font.current()] == "serif"
    assert app.var_fmt.get() == "png"
    assert (app.var_publisher.get(), app.var_author.get()) == ("내 출판사", "내 이름")
    assert app.var_title.get() == "", "per-book fields must not be remembered"
    close_and_wait(app)
    assert not ERRORS, ERRORS


def saved_custom_size() -> None:
    write_settings(json.dumps({"options": {"preset": "custom", "width": 640, "height": 960}}))
    app = new_app()
    assert (app.var_width.get(), app.var_height.get()) == ("640", "960")
    assert not app.spn_width.instate(["disabled"])
    close_and_wait(app)
    assert not ERRORS, ERRORS


def closing_writes_settings() -> None:
    write_settings("{}")
    app = new_app()
    app.var_title.set("비밀 제목")
    app.var_publisher.set("저장될 출판사")
    app.cmb_template.current(1)
    close_and_wait(app)
    saved = json.load(open(i18n.SETTINGS_PATH, encoding="utf-8"))
    assert saved["options"]["publisher"] == "저장될 출판사", saved
    assert saved["options"]["template"] == 2, saved
    assert "비밀 제목" not in json.dumps(saved, ensure_ascii=False), "book text leaked into settings.json"
    assert not ERRORS, ERRORS


def close_during_render() -> None:
    write_settings("{}")
    app = new_app()
    real = render.render

    def slow(opts):
        time.sleep(0.6)
        return real(opts)

    with mock.patch.object(render, "render", slow):
        app.var_publisher.set("p")
        app.var_title.set("x")
        sleep_pump(app, 0.3)                                  # the slow render is in flight
        close_and_wait(app)
        time.sleep(0.9)                                       # the thread finishes against a dead window
    assert not ERRORS, ERRORS


def close_during_insert() -> None:
    write_settings("{}")
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "g.epub")
    fx.epub2_no_cover(src)
    app = new_app()
    app.var_publisher.set("p")
    app.var_epub.set(src)
    real = epub_insert.insert_cover

    def slow(*a, **k):
        time.sleep(1.0)
        return real(*a, **k)

    with mock.patch.object(epub_insert, "insert_cover", slow):
        app._insert_epub()
        sleep_pump(app, 0.2)
        app._on_close()
        sleep_pump(app, 0.4)
        assert alive(app), "the window closed while the EPUB was still being written"
        assert pump(app, lambda: not alive(app), 6), "the window never closed"
    out = os.path.join(tmp, "g_cover.epub")
    assert os.path.isfile(out), "the insert was cut off"
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
    assert [n for n in os.listdir(tmp) if n.endswith(".part")] == [], "a .part file was left behind"
    assert not ERRORS, ERRORS


if __name__ == "__main__":
    name = sys.argv[1]
    kwargs = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    globals()[name](**kwargs)
    print("OK")
