"""The built exe (dist/ebook-cover-maker.exe). Skipped when it has not been built.

Each test copies the exe into a fresh folder (with Korean letters and spaces
in its name), runs it from a different working directory, finds its window by
title, takes a picture of the window with PrintWindow, and closes it with
WM_CLOSE. Nothing touches the real settings of the person running the tests.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import helpers  # noqa: F401  (must come first)
from PIL import Image

import presets

EXE = os.path.join(helpers.ROOT, "dist", "ebook-cover-maker.exe")
TITLES = set()
for _lang in ("ko", "en", "zh-CN", "ja"):
    with open(os.path.join(helpers.ROOT, "lang", f"{_lang}.json"), encoding="utf-8") as _f:
        TITLES.add(json.load(_f)["app_title"])

if sys.platform != "win32" or not os.path.isfile(EXE):
    raise unittest.SkipTest("the exe is not built (run build.bat) or this is not Windows")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def find_window(titles, timeout: float = 45.0):
    found = {}

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value in titles:
                    found["hwnd"], found["title"] = hwnd, buf.value
        return True

    t0 = time.time()
    while time.time() - t0 < timeout and "hwnd" not in found:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        time.sleep(0.3)
    return found.get("hwnd"), found.get("title"), time.time() - t0


def capture(hwnd) -> Image.Image:
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)                       # PW_RENDERFULLCONTENT

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


LIVE = os.environ.get("ECM_LIVE_UPDATE_CHECK") == "1"     # the one real request, for a release check only


class ProxyTrap:
    """A local HTTPS proxy that answers nobody. The exe's urllib honours HTTPS_PROXY, so every
    request it makes arrives here as "CONNECT host:443" and is recorded, then the connection is
    closed: the exe sees "offline" and the real server is never reached."""

    def __init__(self):
        import socket
        import threading

        self.seen: list[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(2)
                try:
                    first = conn.recv(1024).split(b"\r\n", 1)[0].decode("latin-1")
                except OSError:
                    first = "?"
                self.seen.append(first)

    def env(self) -> dict:
        proxy = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ, HTTPS_PROXY=proxy, HTTP_PROXY=proxy, https_proxy=proxy, http_proxy=proxy)
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
        return env

    def close(self):
        self.running = False
        self.sock.close()


class ExeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "한글 폴더 with space")
        os.makedirs(self.home)
        self.exe = os.path.join(self.home, "ebook-cover-maker.exe")
        shutil.copy2(EXE, self.exe)
        self.cwd = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(self.cwd)
        self.settings = os.path.join(self.home, "settings.json")
        self.procs: list[subprocess.Popen] = []
        # the exe asks the server for a new version 1.5 s after it opens: every launch goes through
        # the proxy trap, so no test reaches the real server (the LIVE release check excepted)
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"update_check": False}, f)
        self.trap = ProxyTrap()
        self.addCleanup(self.trap.close)

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
        time.sleep(0.3)
        self._tmp.cleanup()

    def launch(self, *args, live: bool = False):
        p = subprocess.Popen([self.exe, *args], cwd=self.cwd, env=None if live else self.trap.env())
        self.procs.append(p)
        hwnd, title, waited = find_window(TITLES)
        self.assertIsNotNone(hwnd, "the exe never showed its window")
        return p, hwnd, title, waited

    def close(self, proc, hwnd):
        user32.PostMessageW(hwnd, 0x0010, 0, 0)            # WM_CLOSE
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("the exe did not exit after WM_CLOSE")
        return proc.returncode


class Launch(ExeCase):
    def test_starts_shows_a_translated_title_and_saves_settings_beside_itself(self):
        proc, hwnd, title, waited = self.launch()
        self.assertIn(title, TITLES)
        self.assertLess(waited, 30)
        time.sleep(1.0)
        self.assertEqual(self.close(proc, hwnd), 0)
        self.assertTrue(os.path.isfile(self.settings), "settings.json was not written next to the exe")
        saved = json.load(open(self.settings, encoding="utf-8"))
        self.assertIn("options", saved)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "settings.json")), "settings went to the working directory")

    def test_bundled_fonts_draw_the_preview(self):
        """Settings supply a publisher and author, so the preview must show real text
        in the bundled Noto font; a missing font would leave the cover blank."""
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"lang": "ko", "update_check": False,
                       "options": {"publisher": "유페이퍼 출판", "author": "홍길동", "template": 1, "color": 1}},
                      f, ensure_ascii=False)
        proc, hwnd, title, _ = self.launch()
        self.assertEqual(title, json.load(open(os.path.join(helpers.ROOT, "lang", "ko.json"), encoding="utf-8"))["app_title"])
        time.sleep(2.5)                                    # first preview
        shot = capture(hwnd)
        shot.save(os.path.join(self._tmp.name, "exe_window.png"))
        self.close(proc, hwnd)
        # the cover is the big flat #1f1f1f rectangle; find it and look for light text inside it
        w, h = shot.size
        px = shot.load()
        xs, ys = [], []
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if px[x, y] == (0x1f, 0x1f, 0x1f):
                    xs.append(x)
                    ys.append(y)
        self.assertGreater(len(xs), 5000, "no cover preview was drawn in the window")
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        self.assertGreater((y1 - y0), 200)
        text_pixels = 0
        for y in range(int(y1 - (y1 - y0) * 0.13), y1):          # the bottom band holds the publisher
            for x in range(x0, x1):
                r, g, b = px[x, y]
                if r > 150 and g > 150 and b > 140:
                    text_pixels += 1
        self.assertGreater(text_pixels, 60, "the publisher name was not drawn: the font probably is not bundled")

    def test_arguments_open_the_window_instead_of_running_a_hidden_cli(self):
        """A windowed exe has no console, so command line flags must not run silently."""
        out = os.path.join(self.cwd, "out.jpg")
        proc, hwnd, _, _ = self.launch("--title", "t", "--author", "a", "--publisher", "p", "-o", out)
        time.sleep(1.0)
        self.close(proc, hwnd)
        self.assertFalse(os.path.exists(out))

    def test_dropped_files_as_arguments(self):
        img = os.path.join(self.cwd, "사진 1.png")
        Image.new("RGB", (900, 1300), (40, 90, 160)).save(img)
        epub = os.path.join(self.cwd, "책 (final).epub")
        import fixtures as fx
        fx.epub2_no_cover(epub)
        proc, hwnd, _, _ = self.launch(img, epub)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), "the exe exited on its own")
        self.assertEqual(self.close(proc, hwnd), 0)

    def test_damaged_settings_are_survived_and_repaired(self):
        """A damaged file means default settings, so the check is on: it goes into the trap, not to the server."""
        for content in ("{not json", '{"options": {"template": "x", "width": "big"}}', "\x00\x01"):
            with open(self.settings, "w", encoding="utf-8") as f:
                f.write(content)
            proc, hwnd, _, _ = self.launch()
            time.sleep(0.8)
            self.assertEqual(self.close(proc, hwnd), 0, content)
            json.load(open(self.settings, encoding="utf-8"))          # now valid JSON again

    def test_second_launch_reads_what_the_first_saved(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"lang": "ja", "update_check": False}, f)
        proc, hwnd, title, _ = self.launch()
        self.assertEqual(title, json.load(open(os.path.join(helpers.ROOT, "lang", "ja.json"), encoding="utf-8"))["app_title"])
        self.close(proc, hwnd)

    def test_the_exe_tries_once_and_an_unreachable_server_is_silent(self):
        """Check on (the default): exactly one attempt, to the tools site only, after about 1.5 s.
        It lands in the trap, so the exe sees "offline": no dialog, no crash, no day recorded."""
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"lang": "ko"}, f)
        proc, hwnd, _, _ = self.launch()
        deadline = time.time() + 15
        while time.time() < deadline and not self.trap.seen:
            time.sleep(0.2)
        time.sleep(3)                                                     # a second attempt would show up by now
        self.assertIsNone(proc.poll(), "the exe exited on its own")
        self.assertEqual(self.close(proc, hwnd), 0)
        self.assertEqual(self.trap.seen, ["CONNECT chaekgalpi.co.kr:443 HTTP/1.1"])
        saved = json.load(open(self.settings, encoding="utf-8"))
        self.assertEqual(saved.get("update_check_last", ""), "", "an unanswered check must not use up the day")

    @unittest.skipUnless(LIVE, "release check only: set ECM_LIVE_UPDATE_CHECK=1 to send the one real request")
    def test_new_version_check_records_the_day_in_settings_beside_the_exe(self):
        """The real exe, the real server: one read-only request with name, version and OS.
        Run once per release (ECM_LIVE_UPDATE_CHECK=1), never in ordinary test runs."""
        import datetime

        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"lang": "ko"}, f)                                  # update_check is on by default
        proc, hwnd, _, _ = self.launch(live=True)
        deadline = time.time() + 25
        recorded = ""
        while time.time() < deadline and not recorded:
            time.sleep(0.5)
            try:
                recorded = json.load(open(self.settings, encoding="utf-8")).get("update_check_last", "")
            except (OSError, ValueError):
                recorded = ""
        shot = capture(hwnd)
        shot.save(os.path.join(self._tmp.name, "exe_update_check.png"))
        self.close(proc, hwnd)
        self.assertEqual(recorded, datetime.date.today().isoformat(), "update_check_last was not written after the answer")
        saved = json.load(open(self.settings, encoding="utf-8"))
        self.assertIs(saved["update_check"], True)

    def test_switched_off_the_exe_never_records_a_check(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"lang": "ko", "update_check": False}, f)
        proc, hwnd, _, _ = self.launch()
        time.sleep(5)                                                     # well past the 1.5 s delay
        self.close(proc, hwnd)
        saved = json.load(open(self.settings, encoding="utf-8"))
        self.assertEqual(saved.get("update_check_last", ""), "")
        self.assertIs(saved["update_check"], False)
        self.assertEqual(self.trap.seen, [], "switched off, yet it tried to connect")

    def test_bundle_contains_every_resource(self):
        from PyInstaller.archive.readers import CArchiveReader

        names = set(CArchiveReader(EXE).toc.keys())
        for lang in ("ko", "en", "zh-CN", "ja"):
            self.assertIn(f"lang\\{lang}.json", names)
        for stem in presets.FONT_STEMS.values():              # the font subsets, LZMA-packed (tools/subset_fonts.py)
            for face in presets.FACE_NAMES.values():
                self.assertIn(f"fonts\\{stem}-{face}.otf.xz", names)
        for lic in ("LICENSE-Sans.txt", "LICENSE-Serif.txt"):
            self.assertIn(f"fonts\\{lic}", names)
        self.assertFalse([n for n in names if n.endswith(".ttc")], "the full collections must not be in the exe")
        self.assertFalse([n for n in names if n.lower().startswith(("numpy", "lxml", "scipy"))])
        self.assertLess(os.path.getsize(EXE), 40 * 1000 * 1000, "the exe grew past 40 MB again")
        self.assertTrue(any("tkdnd" in n.lower() for n in names))
        self.assertTrue(any("fontTools" in n for n in names))


if __name__ == "__main__":
    unittest.main()
