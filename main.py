"""Entry point for ebook-cover-maker.

    python main.py                                   -> GUI
    python main.py --title T --author A --publisher P -o cover.jpg   -> CLI

Dropping an image or an EPUB onto the exe (or passing them as positional
arguments) opens the GUI with that file loaded.
"""
from __future__ import annotations

import argparse
import os
import sys

import epub_insert
import i18n
import presets
import render
from i18n import t

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def _preselect_lang(argv: list[str]) -> str | None:
    """--lang has to be known before the parser is built, so help is translated."""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def _localize_argparse() -> None:
    """argparse's own labels go through gettext; route them to lang files."""
    table = {
        "usage: ": t("cli_usage"),
        "positional arguments": t("cli_positional"),
        "options": t("cli_options"),
        "show this help message and exit": t("cli_help"),
    }
    argparse._ = lambda s: table.get(s, s)  # type: ignore[attr-defined]


def build_parser() -> argparse.ArgumentParser:
    _localize_argparse()
    p = argparse.ArgumentParser(prog="ebook-cover-maker", description=t("cli_desc"))
    p.add_argument("inputs", nargs="*", help=t("cli_inputs"))
    # --- TOOL OPTIONS ---------------------------------------------------
    p.add_argument("--title", default="", help=t("cli_title"))
    p.add_argument("--subtitle", default="", help=t("cli_subtitle"))
    p.add_argument("--author", default="", help=t("cli_author"))
    p.add_argument("--publisher", default="", help=t("cli_publisher"))
    p.add_argument("--series", default="", help=t("cli_series"))
    p.add_argument("--volume", default="", help=t("cli_volume"))
    p.add_argument("--preset", choices=presets.PRESET_KEYS, default=presets.DEFAULT_PRESET, help=t("cli_preset"))
    p.add_argument("--width", type=int, default=800, help=t("cli_width"))
    p.add_argument("--height", type=int, default=1200, help=t("cli_height"))
    p.add_argument("--template", type=int, choices=presets.TEMPLATE_IDS, default=presets.DEFAULT_TEMPLATE,
                   help=t("cli_template"))
    p.add_argument("--color", type=int, choices=range(1, len(presets.COLOR_SCHEMES) + 1),
                   default=presets.DEFAULT_COLOR, help=t("cli_color"))
    p.add_argument("--bg-color", default="", help=t("cli_bg_color"))
    p.add_argument("--fg-color", default="", help=t("cli_fg_color"))
    p.add_argument("--font", choices=presets.FONT_KEYS, default=presets.DEFAULT_FONT, help=t("cli_font"))
    p.add_argument("--bg", default="", help=t("cli_bg"))
    p.add_argument("--epub", default="", help=t("cli_epub"))
    p.add_argument("--format", choices=presets.FORMATS, default=None, help=t("cli_format"))
    p.add_argument("-o", "--output", default="cover.jpg", help=t("cli_output"))
    # --- END TOOL OPTIONS -----------------------------------------------
    p.add_argument("-y", "--yes", action="store_true", help=t("cli_yes"))
    p.add_argument("--lang", choices=i18n.LANGS, help=t("cli_lang"))
    p.add_argument("--gui", action="store_true", help=t("cli_gui"))
    return p


def options_from_args(args: argparse.Namespace) -> render.Options:
    fmt = args.format
    if fmt is None:
        fmt = "png" if args.output.lower().endswith(".png") else "jpg"
    return render.Options(
        title=args.title, subtitle=args.subtitle, author=args.author, publisher=args.publisher,
        series=args.series, volume=args.volume, preset=args.preset, width=args.width, height=args.height,
        template=args.template, color=args.color, bg_color=args.bg_color, fg_color=args.fg_color,
        bg_image=args.bg, font=args.font, fmt=fmt,
    ).validated()


def print_warnings(warnings: list[tuple[str, dict]]) -> None:
    for key, kwargs in warnings:
        print(t("cli_warning", text=t(key, **kwargs)), file=sys.stderr)


def run_cli(args: argparse.Namespace) -> int:
    missing = [name for name, val in (("title", args.title), ("author", args.author),
                                      ("publisher", args.publisher)) if not val.strip()]
    if missing:
        print(t("cli_missing_required", names=", ".join("--" + m for m in missing)), file=sys.stderr)
        if not args.publisher.strip():
            print(t("warn_publisher_empty"), file=sys.stderr)
        return 2
    opts = options_from_args(args)
    if opts.bg_image and not os.path.isfile(opts.bg_image):
        print(t("err_bg_open_failed", name=opts.bg_image), file=sys.stderr)
        return 1
    try:
        result = render.render(opts)
    except render.FontMissing as exc:
        print(t("err_font_missing", name=str(exc)), file=sys.stderr)
        return 1
    except render.BackgroundOpenError as exc:
        print(t("err_bg_open_failed", name=str(exc)), file=sys.stderr)
        return 1
    print_warnings(result.warnings)
    if not render.publisher_in_zone(result):
        return 1
    try:
        path = render.save_cover(result.image, args.output, opts.fmt)
    except OSError as exc:
        print(t("err_save_failed", error=exc), file=sys.stderr)
        return 1
    print(t("msg_saved", path=path))
    if not args.epub:
        return 0
    try:
        info = epub_insert.inspect_epub(args.epub)
    except epub_insert.NotEpub:
        print(t("err_epub_open_failed", name=args.epub), file=sys.stderr)
        return 1
    if info.drm:
        print(t("err_epub_drm", name=os.path.basename(args.epub)), file=sys.stderr)
        return 1
    if info.has_cover and not args.yes:
        if not sys.stdin.isatty():
            print(t("warn_epub_replace"), file=sys.stderr)
            return 1
        answer = input(t("warn_epub_replace") + t("cli_confirm_hint")).strip().lower()
        if answer not in ("y", "yes"):
            return 0                     # the user chose to keep the old cover; the image was still saved
    try:
        ins = epub_insert.insert_cover(args.epub, render.to_bytes(result.image, opts.fmt), opts.fmt)
    except (epub_insert.EpubError, OSError, ValueError) as exc:
        print(t("err_epub_failed", error=exc), file=sys.stderr)
        return 1
    print(t("msg_epub_done", path=ins.output_path))
    if ins.duplicates:
        print(t("cli_warning", text=t("warn_epub_dup", names=", ".join(ins.duplicates))), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows consoles default to cp949; Chinese/Japanese text would crash print().
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    i18n.init(_preselect_lang(argv))
    args = build_parser().parse_args(argv)
    if args.lang:
        i18n.set_lang(args.lang, persist=False)
    # A windowed exe has no console, so files dropped on it open the GUI with
    # those files loaded instead of running the CLI into nowhere.
    headless = getattr(sys, "frozen", False) and sys.stdout is None
    cli_fields = any((args.title, args.author, args.publisher))
    if args.gui or headless or not cli_fields:
        import gui

        bg = next((p for p in args.inputs if p.lower().endswith(IMAGE_EXTS)), args.bg or None)
        epub = next((p for p in args.inputs if p.lower().endswith(".epub")), args.epub or None)
        gui.launch(bg_image=bg, epub=epub)
        return 0
    try:
        return run_cli(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
