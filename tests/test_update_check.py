"""Update check: once a day, off switch, silent failures, what is sent, when a notice shows.
The network is always faked: these tests never reach the real server."""
from __future__ import annotations

import io
import json
import socket
import threading
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import helpers  # noqa: F401  (must come first; it also blocks the real network)
import update_check as uc
from version import APP_VERSION, UPDATE_SLUG

DAY = "2026-10-13"
OK = {"slug": "ebookcover", "latest": "0.3.0", "tag": "v0.3.0", "client": "0.1.1", "updateAvailable": True,
      "url": "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=update"}


class FakeRoot:
    """after() runs the callback at once; the background thread is run inline (see InlineThread)."""

    def __init__(self):
        self.delays = []

    def after(self, ms, fn):
        self.delays.append(ms)
        fn()


class InlineThread:
    def __init__(self, target, name=None, daemon=None):
        self.target = target

    def start(self):
        self.target()


def counting(answer):
    calls = []

    def fetcher(slug, version):
        calls.append((slug, version))
        return answer

    return fetcher, calls


class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(uc.threading, "Thread", InlineThread)
        p.start()
        self.addCleanup(p.stop)

    def run_start(self, prefs, fetcher, day=DAY):
        """One app start; the caller records the day only when answered (as the window does)."""
        results = []

        def on_result(info, answered):
            results.append(info)
            if answered:
                prefs.update_check_last = day

        root = FakeRoot()
        scheduled = uc.check_async(root, UPDATE_SLUG, "0.1.1", prefs, on_result, fetcher=fetcher, day=day)
        return scheduled, results, root


class HowOften(Base):
    def test_same_day_twice_asks_once(self):
        p = uc.UpdatePrefs()
        fetcher, calls = counting(OK)
        self.run_start(p, fetcher)
        self.run_start(p, fetcher)
        self.assertEqual(len(calls), 1)
        self.run_start(p, fetcher, day="2026-10-14")
        self.assertEqual(len(calls), 2)

    def test_switched_off_never_asks(self):
        p = uc.UpdatePrefs(update_check=False)
        fetcher, calls = counting(OK)
        scheduled, results, root = self.run_start(p, fetcher)
        self.assertEqual((scheduled, calls, results, root.delays), (False, [], [], []))

    def test_offline_does_not_use_up_the_day(self):
        p = uc.UpdatePrefs()
        fetcher, calls = counting(None)
        self.run_start(p, fetcher)
        self.run_start(p, fetcher)
        self.assertEqual((len(calls), p.update_check_last), (2, ""))

    def test_waits_one_and_a_half_seconds_first(self):
        _, _, root = self.run_start(uc.UpdatePrefs(), counting(OK)[0])
        self.assertEqual(root.delays[0], 1500)

    def test_the_window_closing_meanwhile_is_harmless(self):
        class DeadRoot(FakeRoot):
            def after(self, ms, fn):
                if ms == 0:
                    raise RuntimeError("main thread is not in main loop")
                super().after(ms, fn)

        fetcher, calls = counting(OK)
        uc.check_async(DeadRoot(), UPDATE_SLUG, "0.1.1", uc.UpdatePrefs(), lambda *a: None, fetcher=fetcher, day=DAY)
        self.assertEqual(len(calls), 1)

    def test_a_root_that_cannot_schedule_means_no_check(self):
        class Broken:
            def after(self, ms, fn):
                raise RuntimeError("no loop")

        self.assertFalse(uc.check_async(Broken(), UPDATE_SLUG, "0.1.1", uc.UpdatePrefs(), lambda *a: None, day=DAY))


class ThreadHandOff(unittest.TestCase):
    """The worker thread must never hold a Tk object or a callback. When it did, a window
    closed while the request was out left the thread with the last reference to the App,
    its Tk variables were freed off the main thread and Tcl aborted (Tcl_AsyncDelete)."""

    def test_the_thread_target_holds_neither_root_nor_callback(self):
        started = []

        class Recorder:
            def __init__(self, target, name=None, daemon=None):
                started.append(target)

            def start(self):
                pass

        class Root:
            def __init__(self):
                self.pending = []

            def after(self, ms, fn):
                self.pending.append((ms, fn))

        root = Root()

        def on_result(info, answered):
            pass

        with mock.patch.object(uc.threading, "Thread", Recorder):
            uc.check_async(root, UPDATE_SLUG, "0.1.1", uc.UpdatePrefs(), on_result, fetcher=lambda s, v: None, day=DAY)
            root.pending[0][1]()                                  # the 1.5 s timer fires: start() runs
        self.assertEqual(len(started), 1)
        target = started[0]
        held = [c.cell_contents for c in (target.__closure__ or ())]
        held += list(target.__defaults__ or ()) + list((target.__kwdefaults__ or {}).values())
        for obj in held:
            self.assertIsNot(obj, root, "the worker holds the Tk root")
            self.assertIsNot(obj, on_result, "the worker holds the result callback")
            self.assertFalse(hasattr(obj, "after"), f"the worker holds something Tk-like: {obj!r}")

    def test_only_the_calling_thread_ever_touches_root(self):
        main = threading.get_ident()
        touched_from = []
        results = []
        gate = threading.Event()

        class Root:
            def __init__(self):
                self.pending = []

            def after(self, ms, fn):
                touched_from.append(threading.get_ident())
                self.pending.append((ms, fn))

        def slow(slug, version):
            gate.wait(5)
            return OK

        root = Root()
        uc.check_async(root, UPDATE_SLUG, "0.1.1", uc.UpdatePrefs(), lambda i, a: results.append((i, a)),
                       fetcher=slow, day=DAY)
        root.pending.pop(0)[1]()                                  # start: the thread begins, the answer is not there yet
        self.assertEqual(results, [])
        ms, poll = root.pending.pop(0)
        self.assertEqual(ms, uc.POLL_MS)
        poll()                                                    # still waiting: it must ask to be called again
        self.assertEqual(results, [])
        self.assertEqual(len(root.pending), 1)
        gate.set()
        for _ in range(100):
            threading.Event().wait(0.02)
            if "data" in (root.pending[-1][1].__closure__ and
                          [c.cell_contents for c in root.pending[-1][1].__closure__ if isinstance(c.cell_contents, dict)][0]):
                break
        root.pending.pop(0)[1]()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].latest, "0.3.0")
        self.assertTrue(results[0][1])
        self.assertEqual(set(touched_from), {main}, "root.after was called from the worker thread")


class WhatIsSent(unittest.TestCase):
    def test_request_carries_only_slug_version_and_os(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["timeout"] = req.full_url, timeout
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            return FakeResp(json.dumps(OK).encode())

        self.assertEqual(helpers.REAL_FETCH(UPDATE_SLUG, "0.1.1", opener=opener), OK)
        url = urllib.parse.urlsplit(seen["url"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "chaekgalpi.co.kr", "/api/tools/ebookcover/latest"))
        self.assertEqual(urllib.parse.parse_qs(url.query), {"v": ["0.1.1"], "os": ["win"]})
        self.assertEqual(seen["timeout"], 4.0)
        self.assertEqual(seen["headers"]["user-agent"], "chaekgalpi-tool/ebookcover/0.1.1")
        self.assertLessEqual(set(seen["headers"]), {"user-agent", "accept"})        # nothing else about the person or machine

    def test_this_build_identifies_itself_correctly(self):
        self.assertEqual(UPDATE_SLUG, "ebookcover")
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")

    def test_failures_are_silent(self):
        def make(behaviour):
            def opener(req, timeout):
                if behaviour == "urlerror":
                    raise urllib.error.URLError("offline")
                if behaviour == "timeout":
                    raise socket.timeout("slow")
                if behaviour == "http500":
                    raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
                if behaviour == "http400":
                    raise urllib.error.HTTPError(req.full_url, 400, "bad v", {}, None)
                if behaviour == "oserror":
                    raise OSError("reset")
                if behaviour == "badjson":
                    return FakeResp(b"<html>not json")
                if behaviour == "list":
                    return FakeResp(b"[1, 2]")
                if behaviour == "binary":
                    return FakeResp(b"\xff\xfe\x00")
                if behaviour == "empty":
                    return FakeResp(b"")
                return FakeResp(json.dumps(OK).encode(), status=500)
            return opener

        for b in ("urlerror", "timeout", "http500", "http400", "badjson", "list", "status500", "oserror", "binary", "empty"):
            with self.subTest(b):
                self.assertIsNone(helpers.REAL_FETCH(UPDATE_SLUG, "0.1.1", opener=make(b)))

    def test_a_huge_answer_is_not_read_whole(self):
        resp = FakeResp(b"{" + b" " * (10 * 1024 * 1024) + b"}")
        self.assertIsNone(helpers.REAL_FETCH(UPDATE_SLUG, "0.1.1", opener=lambda req, timeout: resp))
        self.assertLessEqual(resp.tell(), uc.MAX_BYTES)


class WhenANoticeShows(Base):
    def test_no_notice_unless_server_says_true(self):
        for flag in (False, None, "true", "yes", 1, 0, [], {}, "True", "1"):
            with self.subTest(flag=flag):
                self.assertIsNone(uc.interpret(dict(OK, updateAvailable=flag), "0.1.1"))
        self.assertIsNone(uc.interpret({k: v for k, v in OK.items() if k != "updateAvailable"}, "0.1.1"))
        for junk in (None, [], "text", 5):
            self.assertIsNone(uc.interpret(junk, "0.1.1"))

    def test_notice_once_when_newer(self):
        p = uc.UpdatePrefs()
        fetcher, _ = counting(OK)
        _, results, _ = self.run_start(p, fetcher)
        self.assertEqual(results, [uc.UpdateInfo("0.3.0", "0.1.1", OK["url"])])

    def test_no_notice_when_the_server_says_false(self):
        _, results, _ = self.run_start(uc.UpdatePrefs(), counting(dict(OK, updateAvailable=False))[0])
        self.assertEqual(results, [None])

    def test_later_hides_that_version_but_not_a_newer_one(self):
        self.assertIsNone(uc.interpret(OK, "0.1.1", "0.3.0"))
        self.assertEqual(uc.interpret(dict(OK, latest="0.3.1"), "0.1.1", "0.3.0").latest, "0.3.1")
        p = uc.UpdatePrefs(update_check_skipped="0.3.0")
        _, results, _ = self.run_start(p, counting(OK)[0])
        self.assertEqual(results, [None], "a version the person said 'later' to must not come back")

    def test_only_pages_of_the_tools_site_are_opened(self):
        for url in ("https://github.com/microhan1/ebook-cover-maker/releases", "http://chaekgalpi.co.kr/tools/x",
                    "https://chaekgalpi.co.kr.evil.example/", "https://evil.example/https://chaekgalpi.co.kr/",
                    "javascript:alert(1)", "file:///C:/Windows/System32/calc.exe", "//chaekgalpi.co.kr/", "", None, 5, ["x"]):
            with self.subTest(url=url):
                self.assertIsNone(uc.interpret(dict(OK, url=url), "0.1.1"))
        self.assertIsNotNone(uc.interpret(dict(OK, url="https://chaekgalpi.co.kr/tools/ebookcover"), "0.1.1"))

    def test_odd_versions_show_nothing(self):
        for latest in ("", "   ", None, 3, "x" * 40, ["0.3.0"], {"v": 1}):
            with self.subTest(latest=latest):
                self.assertIsNone(uc.interpret(dict(OK, latest=latest), "0.1.1"))
        self.assertEqual(uc.interpret(dict(OK, latest="  0.3.0 "), "0.1.1").latest, "0.3.0")


class Settings(unittest.TestCase):
    def test_settings_are_type_checked(self):
        p = uc.load_prefs({"update_check": "no", "update_check_last": 20261013, "update_check_skipped": ["0.3.0"]})
        self.assertEqual((p.update_check, p.update_check_last, p.update_check_skipped), (True, "", ""))
        for junk in (None, [], "x", 5):
            self.assertEqual(uc.load_prefs(junk), uc.UpdatePrefs())
        p = uc.load_prefs({"update_check": False, "update_check_last": "2026-10-13", "update_check_skipped": "0.3.0"})
        self.assertEqual((p.update_check, p.update_check_last, p.update_check_skipped), (False, "2026-10-13", "0.3.0"))
        self.assertTrue(uc.load_prefs({"update_check": 0}).update_check, "0 is not a boolean")
        self.assertEqual(uc.load_prefs({"update_check_last": "x" * 50}).update_check_last, "")

    def test_store_keeps_other_settings(self):
        d = {"lang": "ja", "options": {"template": 2}}
        uc.store_prefs(d, uc.UpdatePrefs(False, "2026-10-13", "0.3.0"))
        self.assertEqual(d, {"lang": "ja", "options": {"template": 2}, "update_check": False,
                             "update_check_last": "2026-10-13", "update_check_skipped": "0.3.0"})
        self.assertEqual(uc.load_prefs(d), uc.UpdatePrefs(False, "2026-10-13", "0.3.0"))

    def test_today_is_an_iso_date(self):
        self.assertRegex(uc.today(), r"^\d{4}-\d{2}-\d{2}$")


class NoRealNetworkInTests(unittest.TestCase):
    def test_the_real_fetch_is_blocked_by_the_test_helpers(self):
        self.assertIsNone(uc.fetch(UPDATE_SLUG, "9.9.9"))
        self.assertIn((UPDATE_SLUG, "9.9.9"), helpers.NETWORK_CALLS)


if __name__ == "__main__":
    unittest.main()
