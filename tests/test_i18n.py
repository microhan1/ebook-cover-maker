"""Language files and the language chooser."""
from __future__ import annotations

import ast
import json
import os
import re
import unittest
from unittest import mock

import helpers  # noqa: F401  (must come first)
import i18n
import presets

LANG_DIR = os.path.join(helpers.ROOT, "lang")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
KEEP_WHITESPACE = {"cli_usage", "cli_confirm_hint"}          # these end with a space on purpose


def load(lang: str) -> dict:
    with open(os.path.join(LANG_DIR, f"{lang}.json"), "rb") as f:
        raw = f.read()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{lang}.json has a BOM"
    return json.loads(raw.decode("utf-8"))


DATA = {lang: load(lang) for lang in i18n.LANGS}


def code_files():
    for name in os.listdir(helpers.ROOT):
        if name.endswith(".py"):
            yield os.path.join(helpers.ROOT, name)


class LanguageFiles(unittest.TestCase):
    def test_same_keys_everywhere(self):
        base = set(DATA["en"])
        for lang in i18n.LANGS:
            self.assertEqual(set(DATA[lang]) ^ base, set(), lang)

    def test_values_are_clean_strings(self):
        for lang, d in DATA.items():
            for key, val in d.items():
                self.assertIsInstance(val, str, (lang, key))
                self.assertTrue(val.strip(), (lang, key, "empty"))
                if key not in KEEP_WHITESPACE:
                    self.assertEqual(val, val.strip(), (lang, key, "stray whitespace"))
                self.assertNotRegex(val, r"[\x00-\x08\x0b-\x1f\ufffd]", (lang, key))
                self.assertTrue(re.fullmatch(r"[a-z][a-z0-9_]*", key), key)

    def test_placeholders_match_across_languages(self):
        for key in DATA["en"]:
            sets = {lang: sorted(PLACEHOLDER.findall(DATA[lang][key])) for lang in i18n.LANGS}
            self.assertEqual(len({tuple(v) for v in sets.values()}), 1, (key, sets))

    def test_every_text_formats_with_its_placeholders(self):
        for lang, d in DATA.items():
            for key, val in d.items():
                kwargs = {name: "X" for name in PLACEHOLDER.findall(val)}
                val.format(**kwargs)

    def test_no_untranslated_copies(self):
        """A non-English language must not just repeat the English sentence."""
        same = [k for k in DATA["en"] if len(DATA["en"][k]) > 25 and any(DATA[l][k] == DATA["en"][k] for l in ("ko", "ja", "zh-CN"))]
        self.assertEqual(same, [])

    def test_ui_texts_do_not_contain_the_other_scripts(self):
        han_kana = re.compile(r"[\u3040-\u30ff]")
        hangul = re.compile(r"[\uac00-\ud7af]")
        for key, val in DATA["zh-CN"].items():
            self.assertFalse(hangul.search(val) or han_kana.search(val), ("zh-CN", key, val))
        for key, val in DATA["ja"].items():
            self.assertFalse(hangul.search(val), ("ja", key, val))
        for key, val in DATA["ko"].items():
            self.assertFalse(han_kana.search(val), ("ko", key, val))
        for key, val in DATA["en"].items():
            self.assertFalse(hangul.search(val) or han_kana.search(val) or re.search(r"[\u4e00-\u9fff]", val), ("en", key, val))


class CodeMatchesFiles(unittest.TestCase):
    """What the code passes to t() must be exactly what each text expects."""

    def collect(self):
        calls = []
        for path in code_files():
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                    if name == "t" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        kws = {k.arg for k in node.keywords if k.arg}
                        has_star = any(k.arg is None for k in node.keywords)
                        calls.append((os.path.basename(path), node.lineno, node.args[0].value, kws, has_star))
                elif isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant) \
                        and isinstance(node.elts[0].value, str) and node.elts[0].value.startswith(("warn_", "err_")) \
                        and isinstance(node.elts[1], ast.Dict):
                    kws = {k.value for k in node.elts[1].keys if isinstance(k, ast.Constant)}
                    calls.append((os.path.basename(path), node.lineno, node.elts[0].value, kws, False))
        return calls

    def test_every_literal_key_exists_and_gets_exactly_its_placeholders(self):
        calls = self.collect()
        self.assertGreater(len(calls), 60, "the scan found suspiciously few calls")
        for fname, line, key, kws, star in calls:
            self.assertIn(key, DATA["en"], (fname, line, key))
            want = set(PLACEHOLDER.findall(DATA["en"][key]))
            if star:
                self.assertTrue(kws <= want, (fname, line, key, kws, want))
            else:
                self.assertEqual(kws, want, (fname, line, key, "wrong keyword arguments"))

    def test_warning_keys_used_by_templates_exist(self):
        src = open(os.path.join(helpers.ROOT, "templates.py"), encoding="utf-8").read()
        for key in set(re.findall(r'cut_warning="(\w+)"', src)):
            self.assertIn(key, DATA["en"], key)
        render_src = open(os.path.join(helpers.ROOT, "render.py"), encoding="utf-8").read()
        for key in set(re.findall(r'"(warn_\w+)"', render_src)):
            self.assertIn(key, DATA["en"], key)

    def test_names_built_from_presets_exist(self):
        for k in presets.PRESET_KEYS:
            self.assertIn(f"preset_{k}", DATA["en"])
        for i in presets.TEMPLATE_IDS:
            self.assertIn(f"template_{i}", DATA["en"])
        for i in range(1, len(presets.COLOR_SCHEMES) + 1):
            self.assertIn(f"color_{i}", DATA["en"])
        for k in presets.FONT_KEYS:
            self.assertIn(f"font_{k}", DATA["en"])
        for f in presets.FORMATS:
            self.assertIn(f"format_{f}", DATA["en"])

    def test_no_non_ascii_text_literals_in_code(self):
        allowed = set(i18n.LANG_NAMES.values())
        bad = []
        for path in code_files():
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"[^\x00-\x7f]", node.value):
                    if node.value not in allowed and not re.fullmatch(r"[\u0000-\uffff\U00010000-\U0010ffff\-\[\]\\\^\{\}]*", node.value):
                        bad.append((os.path.basename(path), node.lineno, node.value[:30]))
                    elif node.value not in allowed and not re.search(r"\\u|[\u2026\u00b0]", ""):
                        pass
        # symbols used for drawing (an ellipsis, a degree sign) and regex ranges are fine; words are not
        words = [b for b in bad if re.search(r"[\uac00-\ud7af\u3040-\u30ff\u4e00-\u9fff]{2}", b[2])]
        self.assertEqual(words, [])


class Chooser(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: i18n.set_lang("ko", persist=False))
        self.settings = i18n.SETTINGS_PATH
        if os.path.exists(self.settings):
            os.remove(self.settings)

    def write(self, content):
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def test_order_explicit_then_settings_then_os_then_english(self):
        with mock.patch.object(i18n, "detect_os_lang", return_value="zh-CN"):
            self.write({"lang": "ja"})
            self.assertEqual(i18n.init("ko"), "ko")                    # explicit wins
            self.assertEqual(i18n.init(None), "ja")                    # then the saved choice
            os.remove(self.settings)
            self.assertEqual(i18n.init(None), "zh-CN")                 # then the OS
            self.assertEqual(i18n.init("xx"), "zh-CN")                 # an unknown explicit value is ignored
        with mock.patch.object(i18n, "detect_os_lang", return_value=i18n.DEFAULT_LANG):
            self.assertEqual(i18n.init(None), "en")

    def test_damaged_settings_never_break_startup(self):
        with mock.patch.object(i18n, "detect_os_lang", return_value="ja"):
            for content in ('{"lang": 5}', '{"lang": null}', '{"lang": ["ko"]}', "{not json", "[]", "null", '"ko"',
                            "", "\x00\x01", '{"lang": "KO"}', '{"lang": "ko "}'):
                self.write(content)
                self.assertIn(i18n.init(None), i18n.LANGS, content)
            self.write({"lang": {"a": 1}})
            self.assertEqual(i18n.init(None), "ja")

    def test_settings_file_edge_cases(self):
        self.write("{}")
        self.assertEqual(i18n.load_settings(), {})
        with open(self.settings, "wb") as f:
            f.write(b"\xff\xfe\x00bad bytes")
        self.assertEqual(i18n.load_settings(), {})                    # not UTF-8
        os.remove(self.settings)
        self.assertEqual(i18n.load_settings(), {})                    # missing

    def test_persist_writes_and_unwritable_settings_are_ignored(self):
        i18n.set_lang("ja", persist=True)
        self.assertEqual(json.load(open(self.settings, encoding="utf-8"))["lang"], "ja")
        with mock.patch.object(i18n, "SETTINGS_PATH", os.path.join(self.settings, "nope", "s.json")):
            i18n.set_lang("en", persist=True)                         # the folder does not exist: silently ignored
        self.assertEqual(i18n.current_lang(), "en")

    def test_os_language_detection(self):
        table = {0x0412: "ko", 0x0411: "ja", 0x0804: "zh-CN", 0x0404: "zh-CN", 0x0C04: "zh-CN", 0x0409: "en", 0x0809: "en"}
        for lcid, want in table.items():
            fake = mock.Mock()
            fake.kernel32.GetUserDefaultUILanguage.return_value = lcid
            with mock.patch("ctypes.windll", fake, create=True), mock.patch.object(i18n.sys, "platform", "win32"):
                self.assertEqual(i18n.detect_os_lang(), want, hex(lcid))

    def test_unsupported_os_language_gives_english(self):
        for lcid in (0x040C, 0x0407, 0x0419, 0x0C0A):                 # French, German, Russian, Spanish
            fake = mock.Mock()
            fake.kernel32.GetUserDefaultUILanguage.return_value = lcid
            env = {k: "" for k in ("LC_ALL", "LC_MESSAGES", "LANG")}
            with mock.patch("ctypes.windll", fake, create=True), mock.patch.object(i18n.sys, "platform", "win32"), \
                    mock.patch.dict(os.environ, env), mock.patch("locale.getlocale", return_value=(None, None)):
                self.assertEqual(i18n.detect_os_lang(), "en", hex(lcid))

    def test_lookup_fallbacks(self):
        i18n.set_lang("ko", persist=False)
        self.assertEqual(i18n.t("btn_save"), DATA["ko"]["btn_save"])
        self.assertEqual(i18n.t("no_such_key"), "no_such_key")                      # unknown: the key itself
        self.assertEqual(i18n.t("msg_saved"), DATA["ko"]["msg_saved"])               # missing args: raw text, no exception
        self.assertEqual(i18n.t("msg_saved", path="{x}"), DATA["ko"]["msg_saved"].replace("{path}", "{x}"))
        self.assertEqual(i18n.t("msg_saved", path="a", extra="b"), DATA["ko"]["msg_saved"].replace("{path}", "a"))
        with mock.patch.dict(i18n._inst._strings, {"btn_save": ""}):                 # an empty text falls back to English
            self.assertEqual(i18n.t("btn_save"), DATA["en"]["btn_save"])

    def test_switching_is_immediate_and_complete(self):
        for lang in i18n.LANGS:
            i18n.set_lang(lang, persist=False)
            self.assertEqual(i18n.current_lang(), lang)
            for key in DATA["en"]:
                kwargs = {n: "V" for n in PLACEHOLDER.findall(DATA[lang][key])}
                self.assertEqual(i18n.t(key, **kwargs), DATA[lang][key].format(**kwargs))


if __name__ == "__main__":
    unittest.main()
