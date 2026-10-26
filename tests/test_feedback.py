"""The "send feedback" link: the address it builds, its texts, and that building it touches no network.
The button itself (one browser call, failures, language switch) is tested on the real window in test_gui.py."""
from __future__ import annotations

import json
import os
import socket
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import helpers  # noqa: F401  (must come first; it also blocks the real network)
import i18n
import update_check as uc
from helpers import run_cli
from version import APP_VERSION, UPDATE_SLUG

KO = "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"
EN = "https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"
LANG_DIR = os.path.join(helpers.ROOT, "lang")


def lang_file(lang: str) -> dict:
    with open(os.path.join(LANG_DIR, f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


class NoNetwork(unittest.TestCase):
    """Every test here also proves that no socket was opened and urlopen was never called."""

    def setUp(self):
        self.net: list = []
        real_connect = socket.socket.connect

        def connect(sock, address):
            self.net.append(("connect", address))
            return real_connect(sock, address)

        for target, name, fn in ((socket.socket, "connect", connect),
                                 (socket, "create_connection", lambda *a, **k: self.net.append(("create_connection", a))),
                                 (urllib.request, "urlopen", lambda *a, **k: self.net.append(("urlopen", a)))):
            p = mock.patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.fetches = len(helpers.NETWORK_CALLS)

    def tearDown(self):
        self.assertEqual(self.net, [], "building the feedback address touched the network")
        self.assertEqual(len(helpers.NETWORK_CALLS), self.fetches)


class Address(NoNetwork):
    def test_the_four_screen_languages(self):
        self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", "ko"), KO)
        self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", "en"), EN)
        self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", "ja"), EN)          # no Japanese page
        self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", "zh-CN"), EN)       # no Chinese page

    def test_every_language_the_program_offers_gets_a_page(self):
        for lang in i18n.LANGS:
            expected = KO if lang == "ko" else EN
            self.assertEqual(uc.feedback_url(UPDATE_SLUG, "0.2.1", lang), expected, lang)

    def test_unknown_or_missing_language_gives_the_korean_page(self):
        for lang in (None, "", "   ", "fr", "de-DE", "zh-TW", "kor", "english", "e n", "ko-KR?x"):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), KO, repr(lang))

    def test_language_codes_are_matched_regardless_of_case(self):
        for lang in ("EN", "En", " en ", "JA", "Ja", "zh-cn", "ZH-CN", "Zh-Cn", "zh_CN", "zh_cn"):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), EN, lang)
        for lang in ("KO", "Ko", " ko "):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), KO, lang)

    def test_shape_of_every_address(self):
        for lang in (*i18n.LANGS, None, "xx", "EN"):
            for version in ("0.2.1", "v0.2.1", "", None, "a b&c#d"):
                url = uc.feedback_url(UPDATE_SLUG, version, lang)
                with self.subTest(lang=lang, version=version):
                    self.assertTrue(url.startswith("https://chaekgalpi.co.kr/"), url)
                    self.assertTrue(url.startswith(uc.SITE), url)
                    self.assertTrue(url.endswith("#feedback"), url)
                    self.assertEqual(url.count("#"), 1, url)
                    parts = urllib.parse.urlsplit(url)
                    self.assertEqual((parts.scheme, parts.netloc, parts.fragment), ("https", "chaekgalpi.co.kr", "feedback"))
                    self.assertIn(parts.path, ("/tools/ebookcover", "/en/tools/ebookcover"))
                    q = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
                    self.assertEqual(q["utm_source"], ["app"])
                    self.assertEqual(q["utm_medium"], ["feedback"])
                    self.assertIn("utm_source=app", url)
                    self.assertIn("utm_medium=feedback", url)

    def test_this_build_points_at_its_own_page(self):
        self.assertEqual(UPDATE_SLUG, "ebookcover")
        url = uc.feedback_url(UPDATE_SLUG, APP_VERSION, "ko")
        self.assertEqual(url, "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=feedback"
                              f"&v={APP_VERSION}#feedback")

    def test_the_same_input_always_gives_the_same_address(self):
        self.assertEqual({uc.feedback_url(UPDATE_SLUG, "0.2.1", "en") for _ in range(5)}, {EN})


class Version(NoNetwork):
    def v(self, url: str):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
        return q.get("v")

    def test_plain_version(self):
        self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", "ko"), KO)

    def test_a_leading_v_is_kept(self):
        url = uc.feedback_url("ebookcover", "v0.2.1", "ko")
        self.assertEqual(url, KO.replace("v=0.2.1", "v=v0.2.1"))
        self.assertEqual(self.v(url), ["v0.2.1"])

    def test_plus_in_a_semver_build_is_not_read_back_as_a_space(self):
        url = uc.feedback_url("ebookcover", "1.0.0-beta+3", "en")
        self.assertIn("v=1.0.0-beta%2B3#feedback", url)
        self.assertEqual(self.v(url), ["1.0.0-beta+3"])

    def test_empty_version_leaves_v_out(self):
        bare = "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=feedback#feedback"
        for version in ("", None, " ", "\t"):
            url = uc.feedback_url("ebookcover", version, "ko")
            self.assertEqual(url, bare, repr(version))
            self.assertIsNone(self.v(url))
            self.assertNotIn("v=", url.replace("utm_", ""))

    def test_odd_versions_are_encoded_and_read_back_unchanged(self):
        odd = ["0.2.1 beta", " 0.2.1", "a&b=c", "x#y", "버전 1", "０.２", "1.0?x=1", "../../admin", "%41%",
               "a+b", "tab\there", "semi;colon", "quote\"'<>", "slash/back\\", "emoji 📘", "=", "&", "#", "?"]
        for version in odd:
            for lang in ("ko", "en"):
                url = uc.feedback_url("ebookcover", version, lang)
                with self.subTest(version=version, lang=lang):
                    parts = urllib.parse.urlsplit(url)
                    self.assertEqual(parts.netloc, "chaekgalpi.co.kr")
                    self.assertIn(parts.path, ("/tools/ebookcover", "/en/tools/ebookcover"))
                    self.assertEqual(parts.fragment, "feedback")
                    self.assertTrue(url.endswith("#feedback"))
                    self.assertEqual(url.count("#"), 1)
                    q = urllib.parse.parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
                    self.assertEqual(q, {"utm_source": ["app"], "utm_medium": ["feedback"], "v": [version]})
                    self.assertFalse(any(c in url for c in " \t\"<>\\"), url)
                    url.encode("ascii")                                    # nothing raw left in the address


class Texts(unittest.TestCase):
    BUTTON = {"ko": "의견 보내기", "en": "Send feedback", "ja": "ご意見を送る", "zh-CN": "发送反馈"}

    def test_the_button_text_in_every_language(self):
        for lang in i18n.LANGS:
            self.assertEqual(lang_file(lang)["btn_feedback"], self.BUTTON[lang], lang)

    def test_the_failure_notice_exists_everywhere_and_carries_the_address(self):
        for lang in i18n.LANGS:
            text = lang_file(lang)["feedback_failed"]
            self.assertTrue(text.strip(), lang)
            self.assertIn("{url}", text, lang)
            self.assertIn("Ctrl+C", text, lang)
            url = uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)
            self.assertIn(url, text.format(url=url))

    def test_t_follows_the_language(self):
        try:
            for lang in i18n.LANGS:
                i18n.set_lang(lang, persist=False)
                self.assertEqual(i18n.t("btn_feedback"), self.BUTTON[lang])
                self.assertIn("https://x/", i18n.t("feedback_failed", url="https://x/"))
        finally:
            i18n.set_lang("ko", persist=False)


class Cli(unittest.TestCase):
    """The command line does not change: --help still works, --version is still not an option."""

    def test_help_still_works_and_does_not_mention_feedback(self):
        for lang in i18n.LANGS:
            code, out, err = run_cli("--lang", lang, "--help")
            self.assertEqual(code, 0, err)
            self.assertIn("--title", out)
            self.assertNotIn(lang_file(lang)["btn_feedback"], out)

    def test_version_flag_is_unchanged(self):
        code, out, err = run_cli("--lang", "en", "--version", timeout=60)
        self.assertEqual(code, 2)
        self.assertIn("--version", err)


if __name__ == "__main__":
    unittest.main()
