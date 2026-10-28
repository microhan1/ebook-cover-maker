"""The real Tk window, driven from Python (never by fake mouse events).

Dialogs are replaced by stubs so nothing waits for a person. The settings
file is redirected to a temp folder by helpers.py. Every Tk or thread
exception raised during a test fails that test.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import socket
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import subprocess
import sys
import unittest
import urllib.request
import webbrowser
from unittest import mock

import helpers  # noqa: F401  (must come first)
from gui_support import alive, pump, put, sleep_pump
import epub_insert
import fixtures as fx
import i18n
import presets
import render
import update_check
from helpers import make_photo, sha
from version import APP_VERSION, UPDATE_SLUG
from PIL import Image

try:
    import gui
except Exception as exc:  # pragma: no cover
    raise unittest.SkipTest(f"gui cannot be imported: {exc}")

LANGS = i18n.LANGS


def lang_text(lang: str, key: str, **kw) -> str:
    with open(os.path.join(helpers.ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)[key].format(**kw)


class FakeEvent:
    def __init__(self, data):
        self.data = data


ERRORS: list = []
PREVIEW_DONE = [0]
APPLIED_GEN = [0]            # the newest render that was actually shown
APP: "gui.App | None" = None
APP_STARTED = [0.0]          # time.time() when the module's window was made
_ORIG_HOOK = threading.excepthook


def setUpModule():
    """One window for every test in this module: Tk is only reliable with one
    window per process. Scenarios that need a fresh window run in their own
    process (see FreshWindow)."""
    global APP
    threading.excepthook = lambda args: ERRORS.append(("thread", args.exc_type, args.exc_value))
    i18n.set_lang("ko", persist=False)
    GuiCase.reset_settings()
    try:
        APP_STARTED[0] = time.time()
        APP = gui.App()
    except tk.TclError as exc:
        raise unittest.SkipTest(f"no display: {exc}")
    APP.root.report_callback_exception = lambda *a: ERRORS.append(("tk", a[1], a[2]))
    APP.root.withdraw()
    original = APP._preview_done

    def counted(gen, result, error):
        applied = gen == APP.preview_gen and not APP._closing             # a stale render must not count
        original(gen, result, error)
        if applied:
            PREVIEW_DONE[0] += 1
            APPLIED_GEN[0] = max(APPLIED_GEN[0], gen)

    APP._preview_done = counted


def wait_preview(app, timeout: float = 4.0) -> bool:
    """Wait for the preview of the *current* options: first for the debounced
    refresh to start, then for that very render to be shown. (An older render
    that was already in flight must not count.)"""
    ok = pump(app, lambda: app._preview_job is None, timeout)
    gen = app.preview_gen
    return ok and pump(app, lambda: APPLIED_GEN[0] >= gen, timeout)


def tearDownModule():
    threading.excepthook = _ORIG_HOOK
    try:
        if APP is not None and alive(APP):
            APP.root.destroy()
    except tk.TclError:
        pass
    GuiCase.reset_settings()


class GuiCase(unittest.TestCase):
    """Shares the module's window; state is reset before every test."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.tmp = cls.tmpdir.name
        cls.app = APP

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    @staticmethod
    def reset_settings(content: str | None = None):
        if os.path.exists(i18n.SETTINGS_PATH):
            os.remove(i18n.SETTINGS_PATH)
        if content is not None:
            put(i18n.SETTINGS_PATH, content)

    def setUp(self):
        ERRORS.clear()
        self.dialogs: list[tuple[str, tuple]] = []
        patches = {
            "showerror": lambda *a, **k: self.dialogs.append(("error", a[1:])),
            "showinfo": lambda *a, **k: self.dialogs.append(("info", a[1:])),
            "showwarning": lambda *a, **k: self.dialogs.append(("warning", a[1:])),
            "askyesno": lambda *a, **k: (self.dialogs.append(("yesno", a[1:])) or self.answer_yes),
        }
        self.answer_yes = True
        for name, fn in patches.items():
            p = mock.patch.object(gui.messagebox, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.save_answer = ""
        self.save_calls: list[dict] = []
        self.open_answer = ""
        p1 = mock.patch.object(gui.filedialog, "asksaveasfilename", self._ask_save)
        p2 = mock.patch.object(gui.filedialog, "askopenfilename", lambda *a, **k: self.open_answer)
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.reset_app()

    def tearDown(self):
        sleep_pump(self.app, 0.05)
        self.assertEqual(ERRORS, [], "an exception happened inside Tk or a thread")

    def _ask_save(self, *a, **k):
        self.save_calls.append(k)
        return self.save_answer

    def reset_app(self):
        a = self.app
        a._running = False
        a._closing = False
        i18n.set_lang("ko", persist=False)
        a.cmb_lang.current(0)
        a.var_lang.set(i18n.LANG_NAMES["ko"])
        for var in (a.var_title, a.var_subtitle, a.var_author, a.var_publisher, a.var_series, a.var_volume,
                    a.var_bg_color, a.var_fg_color, a.var_bg_image, a.var_epub):
            var.set("")
        a.var_width.set("800")
        a.var_height.set("1200")
        a.var_fmt.set("jpg")
        a.cmb_preset.current(0)
        a.cmb_template.current(0)
        a.cmb_color.current(0)
        a.cmb_font.current(presets.FONT_KEYS.index(presets.DEFAULT_FONT))
        a._result_path = None
        a._on_preset()
        a._apply_texts()
        a.txt_log.configure(state="normal")
        a.txt_log.delete("1.0", "end")
        a.txt_log.configure(state="disabled")
        self.settle()

    def settle(self, timeout: float = 4.0):
        """Wait until the preview for the current options has been shown."""
        a = self.app
        a._schedule_preview()
        self.assertTrue(wait_preview(a, timeout), "the preview never finished")

    def log_text(self) -> str:
        return self.app.txt_log.get("1.0", "end")


# ---------------------------------------------------------------------------
class Gate(GuiCase):
    def test_publisher_gate_in_every_language(self):
        a = self.app
        for i, lang in enumerate(LANGS):
            a.cmb_lang.current(i)
            a._on_lang()
            for empty in ("", "   ", "\t", "\u3000"):
                a.var_publisher.set(empty)
                a._update_gate()
                self.assertTrue(a.btn_save.instate(["disabled"]), (lang, repr(empty)))
                self.assertTrue(a.btn_epub.instate(["disabled"]), (lang, repr(empty)))
                self.assertEqual(a.lbl_pub_warn.cget("text"), lang_text(lang, "warn_publisher_empty"))
            a.var_publisher.set("유페이퍼")
            a._update_gate()
            self.assertFalse(a.btn_save.instate(["disabled"]), lang)
            self.assertFalse(a.btn_epub.instate(["disabled"]), lang)
            self.assertEqual(a.lbl_pub_warn.cget("text"), "")

    def test_save_and_epub_do_nothing_while_the_gate_is_closed(self):
        a = self.app
        a.var_publisher.set("")
        a.var_epub.set(os.path.join(self.tmp, "x.epub"))
        a._save()
        a._insert_epub()
        self.assertEqual((self.save_calls, self.dialogs), ([], []))

    def test_running_locks_everything_and_unlocking_restores_the_gate(self):
        a = self.app
        a.var_publisher.set("유페이퍼")
        a._set_running(True)
        for w in (a.btn_save, a.btn_epub, a.btn_bg_browse, a.btn_epub_browse, a.btn_bg_clear, a.btn_epub_clear,
                  a.btn_bg_color, a.btn_fg_color, a.btn_color_reset):
            self.assertTrue(w.instate(["disabled"]))
        self.assertEqual(str(a.cmb_lang.cget("state")), "disabled")
        a._set_running(False)
        self.assertFalse(a.btn_save.instate(["disabled"]))
        self.assertEqual(str(a.cmb_lang.cget("state")), "readonly")
        a.var_publisher.set("")
        a._set_running(True)
        a._set_running(False)
        self.assertTrue(a.btn_save.instate(["disabled"]), "unlocking must not bypass the publisher gate")


# ---------------------------------------------------------------------------
class Languages(GuiCase):
    def test_selections_survive_every_language_switch(self):
        a = self.app
        a.cmb_preset.current(2)
        a.cmb_template.current(3)
        a.cmb_color.current(5)
        a.cmb_font.current(2)
        a._on_preset()
        before = a.options()
        for i, lang in enumerate(list(LANGS) * 2):
            a.cmb_lang.current(LANGS.index(lang))
            a._on_lang()
            a.root.update()
            after = a.options()
            self.assertEqual((after.preset, after.template, after.color, after.font),
                             (before.preset, before.template, before.color, before.font), lang)
            self.assertEqual(a.cmb_template.get(), lang_text(lang, "template_4"))
            self.assertEqual(a.cmb_color.get(), lang_text(lang, "color_6"))
            self.assertEqual(a.cmb_preset.get(), lang_text(lang, "preset_square"))
            self.assertEqual(a.cmb_font.get(), lang_text(lang, "font_serif"))

    def test_every_registered_text_is_in_the_chosen_language(self):
        a = self.app
        for i, lang in enumerate(LANGS):
            a.cmb_lang.current(i)
            a._on_lang()
            self.assertEqual(a.root.title(), lang_text(lang, "app_title"))
            for widget, key, attr in a._texts:
                self.assertEqual(str(widget.cget(attr)), lang_text(lang, key), (lang, key))
            self.assertEqual(i18n.current_lang(), lang)

    def test_warnings_and_status_are_relabelled_on_switch(self):
        a = self.app
        a.var_title.set("가" * 300)
        a.var_subtitle.set("부제 " * 100)
        self.settle()
        for i, lang in enumerate(LANGS):
            a.cmb_lang.current(i)
            a._on_lang()
            shown = a.lbl_warn.cget("text")
            self.assertIn(lang_text(lang, "warn_title_long"), shown, lang)
            self.assertIn(lang_text(lang, "warn_subtitle_long"), shown, lang)
        self.assertIn("×", a.lbl_status.cget("text"))

    def test_no_text_is_cut_off_in_any_language_or_window_size(self):
        self.maxDiff = None
        a = self.app
        a.root.deiconify()
        problems = []
        for size in ("1120x780", "980x700"):
            a.root.geometry(size)
            for i, lang in enumerate(LANGS):
                a.cmb_lang.current(i)
                a._on_lang()
                a.var_publisher.set("")                       # also shows the warning label
                a.var_title.set("가" * 300)
                self.settle()                                  # the warning lines appear only after the render
                a.root.update()
                for widget, key, attr in a._texts:
                    if not widget.winfo_ismapped():
                        continue                               # the folded-away notice buttons have no size
                    if widget.winfo_width() < widget.winfo_reqwidth() - 1:
                        problems.append((size, lang, key, widget.winfo_width(), widget.winfo_reqwidth()))
                for cmb, keys in a._combos:
                    font = tkfont.Font(font=cmb.cget("font"))
                    room = cmb.winfo_width() - 28                 # the drop-down arrow and padding
                    for k in keys:
                        if font.measure(i18n.t(k)) > room:
                            problems.append((size, lang, k, "combobox", font.measure(i18n.t(k)), room))
                for lbl in (a.lbl_pub_warn, a.lbl_warn, a.lbl_drop, a.lbl_status):
                    if not lbl.winfo_ismapped():
                        continue                               # a folded-away label has no position
                    right = lbl.winfo_rootx() + lbl.winfo_reqwidth()
                    if lbl.winfo_reqwidth() > a.root.winfo_width():
                        problems.append((size, lang, "label wider than the window", str(lbl)))
                    if lbl.winfo_rootx() - a.root.winfo_rootx() + lbl.winfo_width() > a.root.winfo_width() + 2:
                        problems.append((size, lang, "label outside the window", str(lbl)))
                for w in [a.txt_log, a.btn_save, a.btn_epub, a.lbl_status, a.cmb_font, a.lbl_drop, *a.entries.values()]:
                    bottom = w.winfo_rooty() - a.root.winfo_rooty() + w.winfo_height()
                    right = w.winfo_rootx() - a.root.winfo_rootx() + w.winfo_width()
                    if bottom > a.root.winfo_height() + 2 or right > a.root.winfo_width() + 2:
                        problems.append((size, lang, "widget sticks out of the window", str(w), bottom, right))
        a.root.withdraw()
        self.assertEqual(problems, [])

    def test_language_choice_is_saved_and_cover_text_is_unaffected(self):
        a = self.app
        a.var_title.set("별 星 star")
        a.var_publisher.set("유페이퍼")
        self.settle()
        shots = []
        for i, lang in enumerate(LANGS):
            a.cmb_lang.current(i)
            a._on_lang()
            shots.append(render.to_bytes(render.render(a.options()).image, "png"))
        self.assertEqual(len(set(shots)), 1)
        self.assertEqual(json.load(open(i18n.SETTINGS_PATH, encoding="utf-8"))["lang"], "ja")


# ---------------------------------------------------------------------------
class Options(GuiCase):
    def test_form_maps_to_options(self):
        a = self.app
        a.var_title.set("  제목  ")
        a.var_subtitle.set("부제")
        a.var_author.set("저자")
        a.var_publisher.set("출판사")
        a.var_series.set("시리즈")
        a.var_volume.set("2권")
        a.cmb_preset.current(1)
        a.cmb_template.current(2)
        a.cmb_color.current(4)
        a.cmb_font.current(0)
        a.var_fmt.set("png")
        a.var_bg_color.set("#102030")
        o = a.options()
        self.assertEqual((o.title, o.subtitle, o.author, o.publisher, o.series, o.volume),
                         ("제목", "부제", "저자", "출판사", "시리즈", "2권"))
        self.assertEqual((o.preset, o.width, o.height, o.template, o.color, o.font, o.fmt, o.bg_color),
                         ("general", 1600, 2400, 3, 5, "sans", "png", "#102030"))

    def test_size_boxes_only_work_for_custom(self):
        a = self.app
        self.assertTrue(a.spn_width.instate(["disabled"]))
        a.cmb_preset.current(3)
        a._on_preset()
        self.assertFalse(a.spn_width.instate(["disabled"]))
        for text, want in (("abc", 800), ("", 800), ("  640 ", 640), ("-5", presets.MIN_SIDE), ("99999", presets.MAX_SIDE),
                           ("800.5", 800), ("１２００", 1200), ("0x400", 800), ("1e3", 800)):
            a.var_width.set(text)
            self.assertEqual(a.options().width, want, repr(text))
        a.cmb_preset.current(0)
        a._on_preset()
        a.var_width.set("999")
        self.assertEqual(a.options().width, 800, "the boxes must be ignored outside custom")

    def test_color_buttons(self):
        a = self.app
        with mock.patch.object(gui.colorchooser, "askcolor", return_value=((1, 2, 3), "#010203")):
            a.btn_bg_color.invoke()
        self.assertEqual(a.var_bg_color.get(), "#010203")
        with mock.patch.object(gui.colorchooser, "askcolor", return_value=(None, None)):
            a.btn_fg_color.invoke()
        self.assertEqual(a.var_fg_color.get(), "")                # cancel changes nothing
        a.var_fg_color.set("#ffffff")
        a.btn_color_reset.invoke()
        self.assertEqual((a.var_bg_color.get(), a.var_fg_color.get()), ("", ""))

    def test_custom_colors_reach_the_cover(self):
        a = self.app
        a.var_title.set("t")
        a.var_publisher.set("p")
        a.var_bg_color.set("#102030")
        self.settle()
        self.assertEqual(a._last_result.image.getpixel((2, 2)), (0x10, 0x20, 0x30))


# ---------------------------------------------------------------------------
class Preview(GuiCase):
    def test_preview_follows_a_change_within_300_ms(self):
        a = self.app
        a.var_publisher.set("유페이퍼")
        a.var_title.set("준비")
        self.settle()
        times = []
        for i in range(16):
            t0 = time.perf_counter()
            if i % 4 == 0:
                a.cmb_template.current(i // 4 % 4)
                a._on_change()
            else:
                a.var_title.set(f"별을 세는 밤 {i} 우리가 잊어버린 이야기")
            ok = wait_preview(a, 3)
            self.assertTrue(ok)
            times.append(time.perf_counter() - t0)
        times.sort()
        median = times[len(times) // 2]
        self.assertLess(median, 0.30, f"median {median * 1000:.0f} ms, worst {times[-1] * 1000:.0f} ms")
        self.assertLess(times[-1], 0.60, f"worst {times[-1] * 1000:.0f} ms")

    def test_preview_with_a_large_photo_stays_fast_after_the_first_time(self):
        a = self.app
        photo = os.path.join(self.tmp, "big.jpg")
        Image.effect_noise((6000, 4000), 60).convert("RGB").save(photo, quality=85)
        a.var_publisher.set("p")
        a.var_bg_image.set(photo)
        self.settle(timeout=10)
        times = []
        for i in range(6):
            t0 = time.perf_counter()
            a.var_title.set(f"t{i}")
            wait_preview(a, 3)
            times.append(time.perf_counter() - t0)
        self.assertLess(sorted(times)[len(times) // 2], 0.30, [round(t, 2) for t in times])

    def test_a_slow_old_render_never_overwrites_a_newer_one(self):
        a = self.app
        a.var_publisher.set("p")
        real = render.render
        delays = {"AAA": 0.9, "BBB": 0.0}

        def slow(opts):
            result = real(opts)
            time.sleep(delays.get(opts.title, 0))
            return result

        with mock.patch.object(render, "render", slow):
            a.var_title.set("AAA")
            sleep_pump(a, 0.35)                                   # AAA's render has started and is slow
            a.var_title.set("BBB")
            wait_preview(a, 3)
            sleep_pump(a, 1.2)                                    # long enough for AAA to finish
        expect = render.to_bytes(real(a.options()).image, "png")
        self.assertEqual(render.to_bytes(a._last_result.image, "png"), expect, "an outdated render replaced the new one")
        self.assertEqual(a.options().title, "BBB")

    def test_fast_typing_renders_only_what_is_needed(self):
        a = self.app
        a.var_publisher.set("p")
        calls = []
        real = render.render

        def counting(opts):
            calls.append(opts.title)
            return real(opts)

        with mock.patch.object(render, "render", counting):
            for i in range(40):
                a.var_title.set("글" * (i % 10 + 1))
                sleep_pump(a, 0.02)
            wait_preview(a, 3)
            sleep_pump(a, 0.3)
        self.assertLessEqual(len(calls), 10, f"{len(calls)} renders for 40 keystrokes")
        self.assertEqual(calls[-1], "글" * 10)

    def test_broken_background_is_reported_and_recovers(self):
        a = self.app
        a.var_publisher.set("p")
        bad = os.path.join(self.tmp, "junk.png")
        put(bad, b"not an image")
        a.var_bg_image.set(bad)
        self.settle()
        self.assertIn(lang_text("ko", "err_bg_open_failed", name=bad), a.lbl_warn.cget("text"))
        a.btn_bg_clear.invoke()
        self.settle()
        self.assertEqual(a.lbl_warn.cget("text"), "")
        self.assertIsNotNone(a._last_result)

    def test_text_that_needs_warnings_shows_them(self):
        a = self.app
        a.var_publisher.set("p")
        a.var_title.set("고양이 \U0001f431")
        self.settle()
        self.assertIn("\U0001f431", a.lbl_warn.cget("text"))
        a.cmb_preset.current(3)
        a._on_preset()
        a.var_width.set("4000")
        a.var_height.set("300")
        self.settle()
        self.assertIn(lang_text("ko", "warn_publisher_small", size=int(a._last_result.publisher_box[3] - a._last_result.publisher_box[1])).split("(")[0],
                      a.lbl_warn.cget("text"))

# ---------------------------------------------------------------------------
class SaveFlow(GuiCase):
    def prepare(self):
        a = self.app
        a.var_title.set("별을 세는 밤")
        a.var_author.set("저자")
        a.var_publisher.set("유페이퍼")
        self.settle()

    def test_save_jpg(self):
        a = self.app
        self.prepare()
        target = os.path.join(self.tmp, "out", "내 표지.jpg")
        self.save_answer = target
        a._save()
        self.assertTrue(os.path.isfile(target))
        with Image.open(target) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (800, 1200)))
        self.assertEqual(a._result_path, target)
        self.assertFalse(a.btn_open.instate(["disabled"]))
        self.assertIn(lang_text("ko", "msg_saved", path=target), self.log_text())
        self.assertEqual(self.save_calls[0]["initialfile"], "cover.jpg")
        self.assertEqual(json.load(open(i18n.SETTINGS_PATH, encoding="utf-8"))["last_dir"], os.path.dirname(target))

    def test_save_png_defaults_and_numbering(self):
        a = self.app
        self.prepare()
        a.var_fmt.set("png")
        target = os.path.join(self.tmp, "same")
        self.save_answer = target
        a._save()
        a._save()
        self.assertEqual(self.save_calls[0]["initialfile"], "cover.png")
        self.assertTrue(os.path.isfile(target + ".png"))
        self.assertTrue(os.path.isfile(target + "(2).png"))

    def test_cancelled_dialog_creates_nothing(self):
        a = self.app
        self.prepare()
        self.save_answer = ""
        a._save()
        self.assertIsNone(a._result_path)
        self.assertEqual(self.dialogs, [])

    def test_unwritable_target_shows_an_error_and_keeps_going(self):
        a = self.app
        self.prepare()
        blocker = os.path.join(self.tmp, "blocker")
        put(blocker, "x")
        self.save_answer = os.path.join(blocker, "sub", "c.jpg")
        a._save()
        self.assertEqual([d[0] for d in self.dialogs], ["error"])
        self.assertIn("Save failed".split()[0] if False else lang_text("ko", "err_save_failed", error="").split(":")[0], self.dialogs[0][1][0])
        self.assertIsNone(a._result_path)
        self.assertFalse(a.btn_save.instate(["disabled"]))

    def test_save_blocked_when_the_publisher_is_outside_the_zone(self):
        a = self.app
        self.prepare()
        self.save_answer = os.path.join(self.tmp, "blocked.jpg")
        with mock.patch.object(render, "publisher_in_zone", return_value=False):
            a._save()
        self.assertFalse(os.path.exists(self.save_answer), "a cover whose publisher is outside the zone was saved")
        self.assertEqual([d[0] for d in self.dialogs], ["error"])
        self.assertIn(lang_text("ko", "warn_publisher_zone"), self.dialogs[0][1][0])

    def test_tiny_publisher_is_a_warning_not_a_block(self):
        a = self.app
        self.prepare()
        a.cmb_preset.current(3)
        a._on_preset()
        a.var_width.set("300")
        a.var_height.set("300")
        self.settle()
        self.assertIn(lang_text("ko", "warn_publisher_small", size=0).split("(")[0], a.lbl_warn.cget("text"))
        self.save_answer = os.path.join(self.tmp, "small.jpg")
        a._save()
        self.assertTrue(os.path.isfile(self.save_answer))

    def test_open_result_uses_explorer(self):
        a = self.app
        self.prepare()
        target = os.path.join(self.tmp, "o.jpg")
        self.save_answer = target
        a._save()
        with mock.patch.object(gui.subprocess, "Popen") as popen:
            a.btn_open.invoke()
        self.assertEqual(popen.call_args[0][0][:2], ["explorer", "/select,"])
        self.assertEqual(popen.call_args[0][0][2], os.path.normpath(target))


# ---------------------------------------------------------------------------
class EpubFlow(GuiCase):
    def make(self, name, builder):
        p = os.path.join(self.tmp, name)
        builder(p)
        return p

    def ready(self, epub):
        a = self.app
        a.var_title.set("표지 제목")
        a.var_publisher.set("유페이퍼")
        a.var_epub.set(epub)
        self.settle()

    def wait_idle(self):
        self.assertTrue(pump(self.app, lambda: not self.app._running, 15))

    def test_insert_into_epub_with_cover_asks_then_creates(self):
        src = self.make("a.epub", fx.epub2_with_cover)
        before = sha(src)
        self.ready(src)
        self.app._insert_epub()
        self.wait_idle()
        out = os.path.join(self.tmp, "a_cover.epub")
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(sha(src), before)
        kinds = [d[0] for d in self.dialogs]
        self.assertEqual(kinds, ["yesno", "info"])
        self.assertIn(lang_text("ko", "warn_epub_replace"), self.dialogs[0][1][0])
        self.assertEqual(self.app._result_path, out)
        self.assertFalse(self.app.btn_save.instate(["disabled"]))

    def test_declining_the_replace_question_does_nothing(self):
        src = self.make("b.epub", fx.epub2_with_cover)
        self.ready(src)
        self.answer_yes = False
        self.app._insert_epub()
        self.assertFalse(self.app._running)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b_cover.epub")))

    def test_epub_without_cover_needs_no_question(self):
        src = self.make("c.epub", fx.epub3_no_cover)
        self.ready(src)
        self.app._insert_epub()
        self.wait_idle()
        self.assertEqual([d[0] for d in self.dialogs], ["info"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "c_cover.epub")))

    def test_drm_and_non_epub_and_corrupt_files(self):
        drm = self.make("d.epub", fx.epub2_drm)
        junk = os.path.join(self.tmp, "j.epub")
        put(junk, b"junk")
        for path, key, kw in ((drm, "err_epub_drm", {"name": "d.epub"}), (junk, "err_epub_open_failed", {"name": "j.epub"})):
            self.dialogs.clear()
            self.ready(path)
            self.app._insert_epub()
            self.assertEqual([d[0] for d in self.dialogs], ["error"], path)
            self.assertIn(lang_text("ko", key, **kw), self.dialogs[0][1][0])
            self.assertFalse(self.app._running)

    def test_failure_inside_the_worker_restores_the_window(self):
        src = self.make("e.epub", fx.epub2_no_cover)
        self.ready(src)
        with mock.patch.object(epub_insert, "insert_cover", side_effect=epub_insert.EpubError("boom")):
            self.app._insert_epub()
            self.wait_idle()
        self.assertEqual([d[0] for d in self.dialogs], ["error"])
        self.assertIn("boom", self.dialogs[0][1][0])
        self.assertFalse(self.app.btn_save.instate(["disabled"]))
        self.assertEqual(str(self.app.cmb_lang.cget("state")), "readonly")

    def test_no_epub_chosen_opens_the_browser_and_cancel_stops(self):
        a = self.app
        a.var_publisher.set("p")
        a.var_epub.set("")
        self.open_answer = ""
        a._insert_epub()
        self.assertEqual(self.dialogs, [])
        self.assertFalse(a._running)

    def test_duplicate_cover_pages_are_reported_in_the_log(self):
        src = self.make("f.epub", fx.epub2_png_cover_after_ch1)
        self.ready(src)
        self.app._insert_epub()
        self.wait_idle()
        self.assertIn("about.xhtml", self.log_text())

# ---------------------------------------------------------------------------
class Drops(GuiCase):
    def test_drop_formats(self):
        a = self.app
        d = os.path.join(self.tmp, "폴더 with space")
        os.makedirs(d)
        img = os.path.join(d, "사진 1.PNG")
        Image.new("RGB", (50, 50)).save(img)
        epub = os.path.join(d, "책 (final).EPUB")
        fx.epub2_no_cover(epub)
        a._on_drop(FakeEvent("{%s} {%s}" % (img.replace("\\", "/"), epub.replace("\\", "/"))))
        self.assertEqual(os.path.normpath(a.var_bg_image.get()), img)
        self.assertEqual(os.path.normpath(a.var_epub.get()), epub)

    def test_drop_rejects_what_it_cannot_use(self):
        a = self.app
        text = os.path.join(self.tmp, "a.txt")
        put(text, "x")
        a._on_drop(FakeEvent("{%s} {%s} {%s}" % (text.replace("\\", "/"), self.tmp.replace("\\", "/"),
                                                   (self.tmp + "/missing.png").replace("\\", "/"))))
        self.assertEqual((a.var_bg_image.get(), a.var_epub.get()), ("", ""))
        self.assertEqual(self.log_text().count(lang_text("ko", "err_open_failed", name="").split(":")[0]), 3)

    def test_drops_are_ignored_while_working(self):
        a = self.app
        img = os.path.join(self.tmp, "i.png")
        Image.new("RGB", (50, 50)).save(img)
        a._set_running(True)
        a._on_drop(FakeEvent("{%s}" % img.replace("\\", "/")))
        a._set_running(False)
        self.assertEqual(a.var_bg_image.get(), "")

    def test_browse_buttons(self):
        a = self.app
        img = os.path.join(self.tmp, "b.png")
        Image.new("RGB", (50, 50)).save(img)
        self.open_answer = img
        a.btn_bg_browse.invoke()
        self.assertEqual(a.var_bg_image.get(), img)
        self.open_answer = ""
        a.btn_bg_browse.invoke()
        self.assertEqual(a.var_bg_image.get(), img)               # cancel keeps the old choice
        a.btn_bg_clear.invoke()
        self.assertEqual(a.var_bg_image.get(), "")


# ---------------------------------------------------------------------------
class UpdateNotice(GuiCase):
    """The one-line "new version" notice: never a dialog, never moves the cards."""

    URL = "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=update"

    def setUp(self):
        super().setUp()
        a = self.app
        a._hide_update_notice()
        a.upd = update_check.UpdatePrefs()
        a.var_update_check.set(True)
        self.reset_settings()

    def info(self, latest="0.3.0", url=None):
        return update_check.UpdateInfo(latest, APP_VERSION, url or self.URL)

    def saved(self) -> dict:
        with open(i18n.SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_checkbox_is_next_to_the_language_choice_and_on_by_default(self):
        a = self.app
        self.assertTrue(a.var_update_check.get())
        self.assertEqual(a.chk_update.master, a.cmb_lang.master)
        for lang in i18n.LANGS:
            a.cmb_lang.current(i18n.LANGS.index(lang))
            a._on_lang()
            self.assertEqual(str(a.chk_update.cget("text")), lang_text(lang, "opt_update_check"))

    def test_notice_text_and_buttons_in_every_language(self):
        a = self.app
        a._on_update_result(self.info(), True)
        self.assertTrue(a.frm_update.winfo_manager())
        for lang in i18n.LANGS:
            a.cmb_lang.current(i18n.LANGS.index(lang))
            a._on_lang()
            self.assertEqual(str(a.lbl_update.cget("text")), lang_text(lang, "update_available", latest="0.3.0", current=APP_VERSION))
            self.assertEqual(str(a.btn_update_open.cget("text")), lang_text(lang, "btn_update"))
            self.assertEqual(str(a.btn_update_later.cget("text")), lang_text(lang, "btn_later"))
        self.assertEqual(self.dialogs, [], "the notice must never be a dialog")

    def test_the_four_exact_sentences(self):
        a = self.app
        a._on_update_result(self.info("0.2.0"), True)
        want = {"ko": "새 버전이 나왔습니다: 0.2.0 (현재 %s)" % APP_VERSION,
                "en": "Version 0.2.0 is available (current %s)" % APP_VERSION,
                "ja": "新しいバージョン 0.2.0 があります（現在 %s）" % APP_VERSION,
                "zh-CN": "有新版本 0.2.0（当前 %s）" % APP_VERSION}
        for lang, sentence in want.items():
            a.cmb_lang.current(i18n.LANGS.index(lang))
            a._on_lang()
            self.assertEqual(str(a.lbl_update.cget("text")), sentence)

    def test_the_day_is_recorded_only_when_the_server_answered(self):
        a = self.app
        a._on_update_result(None, False)                      # offline
        self.assertEqual(a.upd.update_check_last, "")
        self.assertFalse(a.frm_update.winfo_manager())
        a._on_update_result(None, True)                       # answered: nothing newer
        self.assertEqual(a.upd.update_check_last, update_check.today())
        self.assertEqual(self.saved()["update_check_last"], update_check.today())
        self.assertFalse(a.frm_update.winfo_manager())

    def test_switched_off_shows_nothing_and_toggling_off_hides_and_saves(self):
        a = self.app
        a.var_update_check.set(False)
        a._on_update_result(self.info(), True)
        self.assertFalse(a.frm_update.winfo_manager())
        a.var_update_check.set(True)
        a._on_update_result(self.info(), True)
        self.assertTrue(a.frm_update.winfo_manager())
        a.var_update_check.set(False)
        a.chk_update.invoke() if False else a._on_update_toggle()
        self.assertFalse(a.frm_update.winfo_manager())
        self.assertIs(self.saved()["update_check"], False)
        a.var_update_check.set(True)
        a._on_update_toggle()
        self.assertIs(self.saved()["update_check"], True)

    def test_later_remembers_that_version_only(self):
        a = self.app
        a._on_update_result(self.info("0.3.0"), True)
        a.btn_update_later.invoke()
        self.assertFalse(a.frm_update.winfo_manager())
        self.assertEqual(self.saved()["update_check_skipped"], "0.3.0")
        self.assertIsNone(update_check.interpret({"updateAvailable": True, "latest": "0.3.0", "url": self.URL}, APP_VERSION,
                                                 a.upd.update_check_skipped))
        self.assertIsNotNone(update_check.interpret({"updateAvailable": True, "latest": "0.3.1", "url": self.URL}, APP_VERSION,
                                                    a.upd.update_check_skipped))

    def test_update_opens_the_tools_page_and_nothing_else(self):
        a = self.app
        a._on_update_result(self.info(), True)
        with mock.patch.object(gui.webbrowser, "open") as opener:
            a.btn_update_open.invoke()
        opener.assert_called_once_with(self.URL)
        self.assertFalse(a.frm_update.winfo_manager())
        a._update_info = self.info(url="https://evil.example/x")        # even if a bad address got this far
        with mock.patch.object(gui.webbrowser, "open") as opener:
            a._update_open()
        opener.assert_not_called()

    def test_update_opens_the_page_for_the_language_on_screen(self):
        a = self.app
        en = "https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=app&utm_medium=update"
        for lang, want in (("ko", self.URL), ("en", en), ("ja", en), ("zh-CN", en)):
            with self.subTest(lang=lang):
                a.cmb_lang.current(i18n.LANGS.index(lang))
                a._on_lang()
                a._on_update_result(update_check.UpdateInfo("0.3.0", APP_VERSION, self.URL, en), True)
                with mock.patch.object(gui.webbrowser, "open") as opener:
                    a.btn_update_open.invoke()
                opener.assert_called_once_with(want)
        a.cmb_lang.current(i18n.LANGS.index("en"))
        a._on_lang()
        a._on_update_result(update_check.UpdateInfo("0.3.0", APP_VERSION, self.URL), True)   # server gave no English page
        with mock.patch.object(gui.webbrowser, "open") as opener:
            a.btn_update_open.invoke()
        opener.assert_called_once_with(self.URL)

    def test_a_result_that_arrives_while_closing_is_ignored(self):
        a = self.app
        a._closing = True
        a._on_update_result(self.info(), True)
        a._closing = False
        self.assertFalse(a.frm_update.winfo_manager())
        self.assertFalse(os.path.exists(i18n.SETTINGS_PATH))

    def test_showing_the_notice_does_not_move_anything(self):
        """The window grows by exactly the height of the notice, so the two columns of cards stay put."""
        a = self.app
        a.root.deiconify()
        a.root.geometry(f"{a.px(1120)}x{a.px(800)}")
        a.root.update()
        a.root.update_idletasks()

        def layout():
            a.root.update()
            r = a.root
            widgets = [a.cmb_lang, a.chk_update, a.btn_save, a.btn_epub, a.txt_log, a.preview_holder, a.lbl_status,
                       a.cmb_preset, a.cmb_font, a.btn_bg_browse, a.ent_epub, *a.entries.values()]
            return [(w.winfo_rootx() - r.winfo_rootx(), w.winfo_rooty() - r.winfo_rooty(), w.winfo_width(), w.winfo_height())
                    for w in widgets], r.winfo_width(), r.winfo_height()

        try:
            before, w0, h0 = layout()
            a._on_update_result(self.info(), True)
            after, w1, h1 = layout()
            self.assertEqual(after, before, "a card moved or resized when the notice appeared")
            self.assertEqual(w1, w0)
            self.assertGreater(h1, h0)
            self.assertEqual(h1 - h0, a._notice_extra)
            self.assertLess(a.frm_update.winfo_rooty() - a.root.winfo_rooty() + a.frm_update.winfo_height(), h1 + 1,
                            "the notice is cut off at the bottom")
            a._hide_update_notice()
            back, w2, h2 = layout()
            self.assertEqual((back, w2, h2), (before, w0, h0), "the window did not return to its size")
            self.assertEqual(a._notice_extra, 0)
        finally:
            a.root.withdraw()

    def test_a_second_result_does_not_grow_the_window_again(self):
        a = self.app
        a.root.deiconify()
        a.root.update()
        try:
            a._on_update_result(self.info("0.3.0"), True)
            a.root.update()
            h = a.root.winfo_height()
            extra = a._notice_extra
            a._on_update_result(self.info("0.4.0"), True)
            a.root.update()
            self.assertEqual((a.root.winfo_height(), a._notice_extra), (h, extra))
            self.assertIn("0.4.0", str(a.lbl_update.cget("text")))
        finally:
            a._hide_update_notice()
            a.root.withdraw()

    def test_the_notice_fits_in_every_language(self):
        a = self.app
        a.root.deiconify()
        problems = []
        try:
            a._on_update_result(self.info("0.10.12"), True)
            for lang in i18n.LANGS:
                a.cmb_lang.current(i18n.LANGS.index(lang))
                a._on_lang()
                a.root.update()
                for w in (a.lbl_update, a.btn_update_open, a.btn_update_later):
                    if w.winfo_width() < w.winfo_reqwidth() - 1:
                        problems.append((lang, str(w), w.winfo_width(), w.winfo_reqwidth()))
                right = a.btn_update_open.winfo_rootx() - a.root.winfo_rootx() + a.btn_update_open.winfo_width()
                if right > a.root.winfo_width():
                    problems.append((lang, "buttons outside the window"))
        finally:
            a._hide_update_notice()
            a.root.withdraw()
        self.assertEqual(problems, [])


# ---------------------------------------------------------------------------
class FeedbackCase(GuiCase):
    """「의견 보내기」 opens the feedback box of the tools page in the browser and does nothing else.
    webbrowser.open is always faked; no socket, no urlopen, no update check may happen."""

    KO = f"https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=feedback&v={APP_VERSION}#feedback"
    EN = f"https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=app&utm_medium=feedback&v={APP_VERSION}#feedback"

    def setUp(self):
        super().setUp()
        self.net: list = []
        self.browser: list = []
        real_connect = socket.socket.connect

        def connect(sock, address):
            self.net.append(("connect", address))
            return real_connect(sock, address)

        patches = [(socket.socket, "connect", connect),
                   (socket, "create_connection", lambda *a, **k: self.net.append(("create_connection", a))),
                   (urllib.request, "urlopen", lambda *a, **k: self.net.append(("urlopen", a))),
                   (update_check, "check_async", lambda *a, **k: self.net.append(("check_async", a))),
                   (gui.webbrowser, "open", lambda url, *a, **k: self.browser.append(url) or True)]
        for target, name, fn in patches:
            p = mock.patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)
        # the window's own start-up check (stopped by helpers.py) must not be counted against the link
        rest = APP_STARTED[0] + update_check.DELAY_MS / 1000 + 1.0 - time.time()
        if rest > 0:
            sleep_pump(self.app, rest)
        self.fetches = len(helpers.NETWORK_CALLS)
        a = self.app
        a._hide_update_notice()
        a.upd = update_check.UpdatePrefs()
        a.var_update_check.set(True)
        self.reset_settings()

    def tearDown(self):
        a = self.app
        a.tip_feedback.hide()
        a.btn_feedback.state(["!focus", "!active", "!pressed"])
        super().tearDown()
        self.assertEqual(self.net, [], "the feedback link touched the network")
        self.assertEqual(len(helpers.NETWORK_CALLS), self.fetches, "the feedback link started an update check")

    def switch(self, lang: str) -> None:
        a = self.app
        a.cmb_lang.current(i18n.LANGS.index(lang))
        a._on_lang()

    def state(self) -> dict:
        a = self.app
        return {"options": a.options(), "lang": i18n.current_lang(), "combo": a.cmb_lang.get(),
                "update_check": a.var_update_check.get(), "upd": dataclasses.asdict(a.upd),
                "notice": (a._update_info, bool(a.frm_update.winfo_manager())), "running": a._running,
                "closing": a._closing, "result": a._result_path, "status": str(a.lbl_status.cget("text")),
                "log": self.log_text(), "title": a.root.title(), "settings": os.path.exists(i18n.SETTINGS_PATH),
                "buttons": [str(w.cget("state")) for w in (a.btn_save, a.btn_epub, a.btn_feedback, a.cmb_lang)],
                "alive": alive(a)}


class FeedbackLink(FeedbackCase):
    def test_sits_in_the_header_before_the_update_checkbox(self):
        a = self.app
        self.assertEqual(a.btn_feedback.master, a.chk_update.master)
        self.assertEqual(a.btn_feedback.master, a.cmb_lang.master)
        order = a.btn_feedback.master.pack_slaves()
        self.assertLess(order.index(a.btn_feedback), order.index(a.chk_update))
        self.assertEqual(str(a.btn_feedback.cget("style")), "Link.TButton")

    def test_one_click_opens_the_page_once(self):
        self.app.btn_feedback.invoke()
        self.assertEqual(self.browser, [self.KO])
        self.assertEqual(self.dialogs, [])

    def test_each_screen_language_opens_its_page(self):
        for lang in i18n.LANGS:
            self.browser.clear()
            self.switch(lang)
            self.app.btn_feedback.invoke()
            self.assertEqual(self.browser, [self.KO if lang == "ko" else self.EN], lang)
            self.assertEqual(self.browser, [update_check.feedback_url(UPDATE_SLUG, APP_VERSION, lang)])
        self.assertEqual(self.dialogs, [])

    def test_switching_language_changes_the_next_click(self):
        a = self.app
        for lang, expected in (("ko", self.KO), ("en", self.EN), ("ko", self.KO), ("ja", self.EN),
                               ("zh-CN", self.EN), ("ko", self.KO)):
            self.switch(lang)
            a.btn_feedback.invoke()
            self.assertEqual(self.browser[-1], expected, lang)
        self.assertEqual(len(self.browser), 6)

    def test_the_label_follows_the_language(self):
        for lang in i18n.LANGS:
            self.switch(lang)
            self.assertEqual(str(self.app.btn_feedback.cget("text")), lang_text(lang, "btn_feedback"))
        self.switch("ko")
        self.assertEqual(str(self.app.btn_feedback.cget("text")), "의견 보내기")

    def test_no_browser_shows_the_address_and_changes_nothing(self):
        a = self.app
        a.var_title.set("제목")
        a.var_publisher.set("출판사")
        self.settle()

        def refuse(url, *x, **k):
            self.browser.append(url)
            return False

        def raise_error(exc):
            def fn(url, *x, **k):
                self.browser.append(url)
                raise exc
            return fn

        failures = {"False": refuse, "None": lambda url, *x, **k: self.browser.append(url),
                    "webbrowser.Error": raise_error(webbrowser.Error("could not locate runnable browser")),
                    "OSError": raise_error(OSError(5, "Access is denied")),
                    "FileNotFoundError": raise_error(FileNotFoundError("no shell"))}
        for lang in i18n.LANGS:
            self.switch(lang)
            url = update_check.feedback_url(UPDATE_SLUG, APP_VERSION, lang)
            for name, fn in failures.items():
                with self.subTest(lang=lang, failure=name):
                    self.dialogs.clear()
                    self.browser.clear()
                    before = self.state()
                    with mock.patch.object(gui.webbrowser, "open", fn):
                        a.btn_feedback.invoke()
                    self.assertEqual(self.browser, [url], "exactly one try")
                    self.assertEqual(self.dialogs, [("warning", (lang_text(lang, "feedback_failed", url=url),))])
                    self.assertIn(url, self.dialogs[0][1][0])
                    self.assertEqual(self.state(), before)
        a.btn_feedback.invoke()                                         # and it works again afterwards
        self.assertEqual(self.browser[-1], self.EN)

    def test_success_shows_nothing_and_changes_nothing(self):
        before = self.state()
        self.app.btn_feedback.invoke()
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.state(), before)

    def test_leaves_the_update_check_alone(self):
        a = self.app
        info = update_check.UpdateInfo("0.9.0", APP_VERSION, "https://chaekgalpi.co.kr/tools/ebookcover")
        a._on_update_result(info, True)
        self.assertTrue(a.frm_update.winfo_manager())
        prefs = dataclasses.asdict(a.upd)
        saved = open(i18n.SETTINGS_PATH, encoding="utf-8").read()
        for checked in (True, False):
            a.var_update_check.set(checked)
            a.btn_feedback.invoke()
            self.assertTrue(a.frm_update.winfo_manager(), "the notice was hidden")
            self.assertIs(a._update_info, info)
            self.assertEqual(dataclasses.asdict(a.upd), prefs)
            self.assertEqual(open(i18n.SETTINGS_PATH, encoding="utf-8").read(), saved, "settings were written")
        self.assertEqual(self.browser, [self.KO, self.KO])
        a.var_update_check.set(True)
        with mock.patch.object(gui.webbrowser, "open") as opener:       # [Update] still opens its own page
            a.btn_update_open.invoke()
        opener.assert_called_once_with(info.url)

    def test_works_while_a_save_is_running(self):
        a = self.app
        a._set_running(True)
        try:
            a.btn_feedback.invoke()
        finally:
            a._set_running(False)
        self.assertEqual(self.browser, [self.KO])

    def test_the_address_comes_from_constants_only(self):
        """A settings file that tries to change the address changes nothing."""
        put(i18n.SETTINGS_PATH, json.dumps({"feedback_url": "https://evil.example/", "url": "https://evil.example/",
                                            "slug": "other", "lang": "en"}))
        self.app.btn_feedback.invoke()
        self.assertEqual(self.browser, [self.KO])


# ---------------------------------------------------------------------------
# Keys and the note, series follow-up of 2026-10-27. A generated key event only reaches a
# widget when the window has the system's keyboard focus, and taking that focus would steal
# it from whoever is typing. So the tests run the very script Tk binds to a key (what Tk runs
# when the key arrives) and follow the Tab order with tk_focusNext / tk_focusPrev, which is
# what Tk's own <Tab> and <Shift-Tab> bindings use. Mouse in/out are Tk events on the widget.
REAL_TIP_DELAY = gui.Tooltip.DELAY_MS


def press(widget, sequence: str) -> str:
    """Run the script bound to `sequence` on this widget as Tk would; "break" when it stops
    the class bindings that come after it."""
    script = widget.bind(sequence)
    assert script, f"nothing bound to {sequence}"
    fields = dict.fromkeys("#bhkstwxyEXYDKNAT", "0")
    fields["W"] = str(widget)
    script = re.sub(r"%([#a-zA-Z])", lambda m: fields.get(m.group(1), "??"), script)
    code = widget.tk.eval(f"catch {{{script}}} ::press_result")      # 3 is Tcl's code for break
    assert code in ("0", "3"), widget.tk.eval("set ::press_result")
    return "break" if code == "3" else ""


class FeedbackKeyboard(FeedbackCase):
    """a. Tab reaches the link, Enter and Space open the page exactly once, the focus shows."""

    def setUp(self):
        super().setUp()
        self.app.root.deiconify()                     # ttk skips widgets that are not on screen in the Tab order
        self.app.root.update()

    def tearDown(self):
        super().tearDown()
        self.app.root.withdraw()

    def test_the_link_is_a_tab_stop(self):
        link = self.app.btn_feedback
        self.assertEqual(str(link.cget("takefocus")), "ttk::takefocus")
        self.assertEqual(str(link.tk.call("ttk::takefocus", str(link))), "1")

    def test_tab_goes_from_the_widget_before_to_the_link_and_shift_tab_back(self):
        a = self.app
        link = a.btn_feedback
        before = link.tk_focusPrev()
        self.assertIsNotNone(before)
        self.assertIsNot(before, link)
        self.assertIs(before.tk_focusNext(), link, "Tab from the widget before does not land on the link")
        self.assertIs(link.tk_focusNext(), a.chk_update, "Tab from the link does not go to the checkbox")
        self.assertIs(a.chk_update.tk_focusPrev(), link, "Shift+Tab from the checkbox does not come back")
        self.assertIs(link.tk_focusPrev(), before)

    def test_the_whole_tab_cycle_passes_the_link_once_both_ways(self):
        link = self.app.btn_feedback
        forward, w = [], link
        for _ in range(300):
            w = w.tk_focusNext()
            forward.append(w)
            if w is link:
                break
        self.assertIs(forward[-1], link, "Tab never came back to the link")
        self.assertEqual(forward.count(link), 1)
        backward, w = [], link
        for _ in range(300):
            w = w.tk_focusPrev()
            backward.append(w)
            if w is link:
                break
        self.assertEqual(backward[:-1], list(reversed(forward[:-1])), "Shift+Tab is not Tab backwards")
        self.assertIn(self.app.chk_update, forward)
        self.assertIn(self.app.cmb_lang, forward)

    def test_enter_and_space_each_open_the_page_exactly_once(self):
        link = self.app.btn_feedback
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            with self.subTest(key=key):
                self.browser.clear()
                self.assertEqual(press(link, key), "break", "the class binding would run as well")
                sleep_pump(self.app, 0.25)            # ttk's own Space handler would invoke a moment later
                self.assertEqual(self.browser, [self.KO])
        self.assertEqual(self.dialogs, [])

    def test_the_link_comes_first_in_its_bind_tags(self):
        link = self.app.btn_feedback
        self.assertEqual(link.bindtags()[0], str(link))

    def test_keys_and_mouse_together(self):
        a = self.app
        link = a.btn_feedback
        for key in ("<Return>", "<space>", "<KP_Enter>"):
            press(link, key)
        link.invoke()
        sleep_pump(a, 0.25)
        self.assertEqual(self.browser, [self.KO] * 4)

    def test_keys_use_the_language_on_screen(self):
        for lang in i18n.LANGS:
            for key in ("<Return>", "<space>"):
                with self.subTest(lang=lang, key=key):
                    self.browser.clear()
                    self.switch(lang)
                    press(self.app.btn_feedback, key)
                    self.assertEqual(self.browser, [update_check.feedback_url(UPDATE_SLUG, APP_VERSION, lang)])

    def test_keys_with_no_browser_show_the_address_once(self):
        link = self.app.btn_feedback
        for key in ("<Return>", "<space>"):
            with self.subTest(key=key):
                self.dialogs.clear()
                with mock.patch.object(gui.webbrowser, "open", lambda url, *x, **k: self.browser.append(url) or False):
                    press(link, key)
                self.assertEqual(len(self.dialogs), 1)
                self.assertIn(self.KO, self.dialogs[0][1][0])

    def test_focus_is_visible_and_does_not_change_the_size(self):
        a = self.app
        link = a.btn_feedback
        style = gui.ttk.Style(a.root)
        a.root.update()
        size = (link.winfo_reqwidth(), link.winfo_reqheight())
        shown = (link.winfo_width(), link.winfo_height())
        head = (a.chk_update.winfo_x(), a.cmb_lang.winfo_x())
        self.assertEqual(style.lookup("Link.TButton", "background", ["!focus"]), gui.BG)
        self.assertEqual(style.lookup("Link.TButton", "background", ["focus"]), gui.LINK_FOCUS_BG)
        self.assertNotEqual(gui.LINK_FOCUS_BG, gui.BG)
        self.assertEqual(style.lookup("Link.TButton", "background", ["focus", "active"]), gui.LINK_FOCUS_BG)
        self.assertEqual(style.lookup("Link.TButton", "foreground", ["focus"]), gui.ACCENT_DARK)
        self.assertIn("Button.focus", str(style.layout("Link.TButton")), "the theme's focus ring is gone")
        for on in (True, False, True, False):
            link.state(["focus"] if on else ["!focus"])
            a.root.update()
            self.assertEqual(link.instate(["focus"]), on)
            self.assertEqual((link.winfo_reqwidth(), link.winfo_reqheight()), size)
            self.assertEqual((link.winfo_width(), link.winfo_height()), shown)
            self.assertEqual((a.chk_update.winfo_x(), a.cmb_lang.winfo_x()), head, "the header moved")
        self.assertEqual(self.browser, [], "the focus alone opened the page")

    def test_other_keys_do_nothing(self):
        link = self.app.btn_feedback
        keys = sorted(s for s in link.bind() if s.startswith("<Key"))
        self.assertEqual(keys, ["<Key-KP_Enter>", "<Key-Return>", "<Key-space>"])
        for seq in ("<Key-a>", "<Key-1>", "<Escape>", "<Up>", "<Down>", "<Delete>", "<BackSpace>", "<F1>", "<Tab>"):
            with self.subTest(key=seq):
                self.assertEqual(link.bind(seq), "")
                self.assertEqual(link.bind_class("TButton", seq), "")
        self.assertEqual(self.browser, [])


class FeedbackTooltip(FeedbackCase):
    """b. The small note while the mouse is over the link."""

    def setUp(self):
        super().setUp()
        a = self.app
        self.geometry = a.root.geometry()
        p = mock.patch.object(gui.Tooltip, "DELAY_MS", 40)
        p.start()
        self.addCleanup(p.stop)
        a.root.deiconify()
        a.root.update()

    def tearDown(self):
        super().tearDown()
        a = self.app
        a.root.geometry(self.geometry)
        a.root.withdraw()

    def tip(self):
        return self.app.tip_feedback

    def shown(self) -> bool:
        tip = self.tip()
        # mapped is not enough: the label inside can still be 1 pixel wide for a moment
        return (tip.tip is not None and bool(tip.tip.winfo_ismapped()) and tip.tip.winfo_width() > 1
                and tip.label.winfo_width() > 1)

    def hover(self) -> None:
        self.app.btn_feedback.event_generate("<Enter>", x=2, y=2)
        self.assertTrue(pump(self.app, self.shown, 3.0), "the note did not appear")

    def leave(self) -> None:
        self.app.btn_feedback.event_generate("<Leave>", x=-5, y=-5)
        self.app.root.update()

    def text(self) -> str:
        return str(self.tip().label.cget("text"))

    def test_the_delay_is_about_half_a_second(self):
        self.assertGreaterEqual(REAL_TIP_DELAY, 400)
        self.assertLessEqual(REAL_TIP_DELAY, 500)

    def test_appears_on_hover_and_goes_on_leave(self):
        self.hover()
        self.assertEqual(self.text(), lang_text("ko", "tip_feedback"))
        self.assertEqual(self.tip().tip.wm_overrideredirect(), True)
        self.leave()
        self.assertIsNone(self.tip().tip)
        self.assertEqual(self.browser, [])

    def test_waits_before_showing(self):
        with mock.patch.object(gui.Tooltip, "DELAY_MS", REAL_TIP_DELAY):
            t0 = time.time()
            self.app.btn_feedback.event_generate("<Enter>", x=2, y=2)
            sleep_pump(self.app, 0.25)
            self.assertIsNone(self.tip().tip, "the note came before the delay")
            self.assertTrue(pump(self.app, self.shown, 3.0))
            self.assertGreaterEqual(time.time() - t0, REAL_TIP_DELAY / 1000 - 0.02)

    def test_leaving_before_the_delay_shows_nothing(self):
        with mock.patch.object(gui.Tooltip, "DELAY_MS", REAL_TIP_DELAY):
            for stay in (0.0, 0.1, 0.3):
                with self.subTest(stay=stay):
                    self.app.btn_feedback.event_generate("<Enter>", x=2, y=2)
                    sleep_pump(self.app, stay)
                    self.leave()
                    sleep_pump(self.app, REAL_TIP_DELAY / 1000 + 0.3)
                    self.assertIsNone(self.tip().tip)
                    self.assertIsNone(self.tip()._job)

    def test_a_click_hides_it_and_opens_the_page_once(self):
        a = self.app
        self.hover()
        a.btn_feedback.event_generate("<ButtonPress-1>", x=2, y=2)
        a.root.update()
        self.assertIsNone(self.tip().tip, "the note stayed after the click")
        a.btn_feedback.event_generate("<ButtonRelease-1>", x=2, y=2)
        sleep_pump(a, 0.2)
        self.assertEqual(self.browser, [self.KO])
        self.assertIsNone(self.tip().tip, "the note came back over the browser")

    def test_a_key_hides_it_and_opens_the_page_once(self):
        self.hover()
        press(self.app.btn_feedback, "<Return>")
        self.assertIsNone(self.tip().tip)
        self.assertEqual(self.browser, [self.KO])

    def test_the_four_texts(self):
        texts = {}
        for lang in i18n.LANGS:
            self.switch(lang)
            self.hover()
            texts[lang] = self.text()
            self.assertEqual(texts[lang], lang_text(lang, "tip_feedback"), lang)
            self.assertTrue(texts[lang].strip(), lang)
            self.leave()
        self.assertEqual(len(set(texts.values())), 4, texts)

    def test_switching_language_while_it_shows_changes_the_words(self):
        a = self.app
        self.hover()
        for lang in ("en", "ja", "zh-CN", "ko", "en"):
            self.switch(lang)
            a.root.update()
            self.assertTrue(self.shown(), f"the note went away on switching to {lang}")
            self.assertEqual(self.text(), lang_text(lang, "tip_feedback"))
            self.assertInside(lang, "switch")

    def assertInside(self, lang: str, where: str, in_window: bool = True) -> None:
        a = self.app
        tip, link = self.tip().tip, a.btn_feedback
        tip.update_idletasks()
        a.root.update()
        x, y, w, h = tip.winfo_rootx(), tip.winfo_rooty(), tip.winfo_width(), tip.winfo_height()
        left, top, right, bottom = gui._monitor_area(a.root)
        msg = f"{lang} {where}: note at {x},{y} {w}x{h}, screen {left},{top}-{right},{bottom}, " \
              f"window {a.root.winfo_rootx()}+{a.root.winfo_width()}"
        self.assertGreaterEqual(x, left, msg)
        self.assertLessEqual(x + w, right, msg)
        self.assertGreaterEqual(y, top, msg)
        self.assertLessEqual(y + h, bottom, msg)
        if in_window:
            self.assertGreaterEqual(x, a.root.winfo_rootx(), msg)
            self.assertLessEqual(x + w, a.root.winfo_rootx() + a.root.winfo_width(), msg)
        self.assertGreaterEqual(y, link.winfo_rooty() + link.winfo_height(), msg + " (not under the link)")
        self.assertLessEqual(w, a.px(320) + 40, msg + " (not wrapped)")

    def test_inside_the_window_and_the_screen_at_both_screen_edges(self):
        a = self.app
        mw, mh = a._min_size
        left, top, right, bottom = gui._monitor_area(a.root)
        self.assertGreater(right - left, mw)
        a.root.geometry(f"{mw}x{mh}+{left}+{top}")
        a.root.update()
        frame = a.root.winfo_rootx() - a.root.winfo_x()          # the window border left of the inside
        places = {"left edge": (left, True), "right edge": (right - mw - 2 * frame, True),
                  "half off the right edge": (right - mw // 2, False), "half off the left edge": (left - mw // 2, False)}
        for lang in i18n.LANGS:
            self.switch(lang)
            for where, (x, in_window) in places.items():
                with self.subTest(lang=lang, where=where):
                    a.root.geometry(f"{mw}x{mh}+{x}+{top}")
                    sleep_pump(a, 0.05)
                    self.assertEqual(a.root.winfo_width(), mw)
                    self.assertEqual(a.root.winfo_x(), x, "the window is not where the test put it")
                    self.hover()
                    self.assertInside(lang, where, in_window)
                    self.leave()
        self.assertEqual(self.browser, [])

    def test_the_screen_area_is_the_one_the_window_is_on(self):
        a = self.app
        left, top, right, bottom = gui._monitor_area(a.root)
        self.assertGreater(right, left)
        self.assertGreater(bottom, top)
        cx = a.root.winfo_rootx() + a.root.winfo_width() // 2
        self.assertTrue(left <= cx <= right)
        with mock.patch.object(gui.sys, "platform", "linux"):     # without Windows: the whole screen
            self.assertEqual(gui._monitor_area(a.root), (0, 0, a.root.winfo_screenwidth(), a.root.winfo_screenheight()))


# ---------------------------------------------------------------------------
class FreshWindow(unittest.TestCase):
    """Tk is only reliable with one window per process, so every scenario that
    needs a brand-new window runs in its own process (gui_scenarios.py)."""

    def scenario(self, name: str, **kwargs):
        script = os.path.join(helpers.TESTS, "gui_scenarios.py")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, script, name, json.dumps(kwargs)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
        out = (r.stdout + r.stderr)
        if "no display" in out or "TclError: no display" in out:
            self.skipTest("no display")
        self.assertEqual(r.returncode, 0, out[-1500:])
        self.assertIn("OK", r.stdout)

    def test_garbage_settings_never_stop_the_window_from_opening(self):
        contents = [
            "{not json", "[]", "null", '"text"', "",
            '{"options": "x"}', '{"options": []}',
            '{"options": {"template": "x", "width": "big", "color": 99, "preset": [], "font": 7, "fmt": {}, "bg_color": 5}}',
            '{"options": {"template": 9, "width": -1, "height": 99999999, "color": -3, "preset": "nope"}}',
            '{"options": {"publisher": 123, "author": null, "series": ["x"]}}',
            '{"options": {"template": true, "color": false, "width": 800.5}}',
            '{"last_dir": 5}', '{"last_dir": "Z:\\\\does\\\\not\\\\exist"}', '{"lang": 5, "options": {"fmt": "gif"}}',
            "\x00\x01\x02", '{"options": {"preset": "custom", "width": 10, "height": 20}}',
        ]
        for content in contents:
            with self.subTest(content=content[:40]):
                self.scenario("settings_variant", content=content)

    def test_saved_choices_come_back(self):
        self.scenario("saved_choices")

    def test_saved_custom_size_enables_the_size_boxes(self):
        self.scenario("saved_custom_size")

    def test_closing_writes_settings_but_never_book_text(self):
        self.scenario("closing_writes_settings")

    def test_update_check_asks_once_with_only_name_version_and_os(self):
        self.scenario("update_flow", mode="asks_once")

    def test_update_check_switched_off_never_asks(self):
        self.scenario("update_flow", mode="off")

    def test_update_check_already_done_today_does_not_ask(self):
        self.scenario("update_flow", mode="already_today")

    def test_update_check_offline_does_not_use_up_the_day(self):
        self.scenario("update_flow", mode="offline")

    def test_update_check_skipped_version_shows_no_notice(self):
        self.scenario("update_flow", mode="skipped")

    def test_closing_while_the_update_request_is_out_survives_three_times(self):
        """A crash here (Tcl_AsyncDelete) kills the whole process, so each round is its own process."""
        for round_ in range(3):
            with self.subTest(round=round_):
                self.scenario("update_flow", mode="close_during_request")

    def test_update_check_never_blocks_or_shows_errors_when_the_server_is_slow(self):
        self.scenario("update_flow", mode="slow")

    def test_closing_while_a_render_is_running_is_clean(self):
        self.scenario("close_during_render")

    def test_closing_during_an_insert_lets_it_finish(self):
        self.scenario("close_during_insert")


if __name__ == "__main__":
    unittest.main()
