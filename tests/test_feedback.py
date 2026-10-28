"""The "send feedback" link: the address it builds, its texts, and that building it touches no network.
The button itself (one browser call, failures, language switch) is tested on the real window in test_gui.py."""
from __future__ import annotations

import json
import os
import re
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

    def test_unknown_language_gives_the_english_page(self):
        """Series rule of 2026-10-27: only a code whose first part is ko gets the Korean page."""
        for lang in ("fr", "de-DE", "zh-TW", "kor", "korean", "english", "e n", "x-ko", "-ko", "_ko"):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), EN, repr(lang))
        for lang in ("ko-KR?x", "ko-", "ko_"):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), KO, repr(lang))

    def test_missing_language_follows_the_default_screen_language(self):
        self.assertEqual(i18n.DEFAULT_LANG, "en")
        for lang in (None, "", "   ", "\t"):
            self.assertEqual(uc.feedback_url("ebookcover", "0.2.1", lang), EN, repr(lang))

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


class LanguageRule(NoNetwork):
    """One rule for every page this program opens (2026-10-27, the same across the series):
    a code whose first part is ko -> the Korean page, every other code -> the English page,
    no code -> this tool's default screen language. is_korean() decides for both the
    feedback link and the [Update] button of the new-version notice."""

    KOREAN = ("ko", "ko-KR", "ko_KR", "KO", "Ko", "Ko-kr", "KO-KR", " ko ", "ko\t", "ko-Kore-KR")
    ENGLISH = ("en", "ja", "zh", "zh-CN", "zh_CN", "zh-TW", "zh-Hant", "fr", "de", "EN", "en-US", "en_GB",
               "ja-JP", "pt-BR", "xx", "x", "kor", "korean", "eko", "?", "123", "한국어", "日本語")
    UPD_KO = "https://chaekgalpi.co.kr/tools/ebookcover?utm_source=app&utm_medium=update"
    UPD_EN = "https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=app&utm_medium=update"

    def page(self, url: str) -> str:
        return urllib.parse.urlsplit(url).path

    def test_korean_codes(self):
        for lang in self.KOREAN:
            with self.subTest(lang=lang):
                self.assertTrue(uc.is_korean(lang))
                self.assertEqual(self.page(uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)), "/tools/ebookcover")

    def test_every_other_code(self):
        for lang in self.ENGLISH:
            with self.subTest(lang=lang):
                self.assertFalse(uc.is_korean(lang))
                self.assertEqual(self.page(uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)), "/en/tools/ebookcover")

    def test_no_code_follows_the_default_screen_language(self):
        self.assertEqual(i18n.DEFAULT_LANG, "en")              # this tool opens in English when it cannot tell
        for lang in (None, "", " ", "   ", "\t\n"):
            with self.subTest(lang=lang):
                self.assertFalse(uc.is_korean(lang))
                self.assertEqual(self.page(uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)), "/en/tools/ebookcover")
                with mock.patch.object(i18n, "DEFAULT_LANG", "ko"):    # and the rule really reads the default
                    self.assertTrue(uc.is_korean(lang))
                    self.assertEqual(self.page(uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)), "/tools/ebookcover")

    def test_every_address_has_the_same_shape(self):
        for lang in (*self.KOREAN, *self.ENGLISH, None, ""):
            for version in (APP_VERSION, "", None):
                url = uc.feedback_url(UPDATE_SLUG, version, lang)
                with self.subTest(lang=lang, version=version):
                    expected = "https://chaekgalpi.co.kr/" + ("" if uc.is_korean(lang) else "en/") + \
                               "tools/ebookcover?utm_source=app&utm_medium=feedback" + \
                               (f"&v={version}" if version else "") + "#feedback"
                    self.assertEqual(url, expected)

    def test_the_update_page_uses_the_same_rule(self):
        both = uc.UpdateInfo("9.9.9", APP_VERSION, self.UPD_KO, self.UPD_EN)
        korean_only = uc.UpdateInfo("9.9.9", APP_VERSION, self.UPD_KO)
        for lang in (*self.KOREAN, *self.ENGLISH, None, "", "  "):
            with self.subTest(lang=lang):
                expected = self.UPD_KO if uc.is_korean(lang) else self.UPD_EN
                self.assertEqual(uc.link_for(both, lang), expected)
                self.assertEqual(uc.link_for(korean_only, lang), self.UPD_KO)   # no English page offered
                feedback_korean = self.page(uc.feedback_url(UPDATE_SLUG, APP_VERSION, lang)) == "/tools/ebookcover"
                self.assertEqual(uc.link_for(both, lang) == self.UPD_KO, feedback_korean)

    def test_the_program_languages(self):
        self.assertEqual([uc.is_korean(c) for c in i18n.LANGS], [c == "ko" for c in i18n.LANGS])

    def test_one_rule_in_the_code(self):
        """Both pages go through is_korean(); no second list of languages is left behind."""
        with open(os.path.join(helpers.ROOT, "update_check.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("FEEDBACK_EN_LANGS", source)
        self.assertNotIn('lang != "ko"', source)
        self.assertEqual(source.count("is_korean(lang)"), 2)


class Readmes(unittest.TestCase):
    """d. The feedback link of each README: the Korean page in Korean, the English page in
    every other language (the site has no Japanese or Chinese page), always written as
    [text](address) so no bracket or particle after it becomes part of the address."""

    EXPECTED = {"README.md": "https://chaekgalpi.co.kr/tools/ebookcover#feedback",
                "README.en.md": "https://chaekgalpi.co.kr/en/tools/ebookcover#feedback",
                "README.ja.md": "https://chaekgalpi.co.kr/en/tools/ebookcover#feedback",
                "README.zh-CN.md": "https://chaekgalpi.co.kr/en/tools/ebookcover#feedback"}

    def read(self, name: str) -> str:
        with open(os.path.join(helpers.ROOT, name), encoding="utf-8") as f:
            return f.read()

    def test_each_readme_links_to_its_page(self):
        for name, expected in self.EXPECTED.items():
            with self.subTest(readme=name):
                text = self.read(name)
                links = re.findall(r"https://chaekgalpi\.co\.kr/\S*?#feedback", text)
                self.assertTrue(links, "no feedback link")
                self.assertEqual(set(links), {expected})

    def test_every_feedback_address_is_a_markdown_link(self):
        for name in self.EXPECTED:
            with self.subTest(readme=name):
                text = self.read(name)
                for m in re.finditer(r"https://chaekgalpi\.co\.kr/[^\s]*", text):
                    self.assertEqual(text[m.start() - 2:m.start()], "](", m.group(0))
                    self.assertRegex(m.group(0),
                                     r"^https://chaekgalpi\.co\.kr/[A-Za-z0-9/_.-]*(\?[A-Za-z0-9_=&.-]*)?(#feedback)?\)",
                                     "something follows the address inside the link")


class Texts(unittest.TestCase):
    BUTTON = {"ko": "의견 보내기", "en": "Send feedback", "ja": "ご意見を送る", "zh-CN": "发送反馈"}
    TIP = {"ko": "안 되는 것·불편한 것·바라는 기능을 알려 주세요(브라우저가 열립니다)",
           "en": "Tell us what's broken or missing (opens your browser)",
           "ja": "不具合・ご要望をお知らせください（ブラウザが開きます）",
           "zh-CN": "告诉我们问题或建议（将打开浏览器）"}

    def test_the_note_text_in_every_language(self):
        tips = [lang_file(lang)["tip_feedback"] for lang in i18n.LANGS]
        for lang, tip in zip(i18n.LANGS, tips):
            self.assertEqual(tip, self.TIP[lang], lang)
            self.assertTrue(tip.strip(), lang)
            self.assertEqual(tip, tip.strip(), lang)
        self.assertEqual(len(set(tips)), len(i18n.LANGS), "two languages share one note")

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
