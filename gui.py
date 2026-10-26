"""tkinter GUI for ebook-cover-maker: a form on the left, a live preview on
the right, save and EPUB-insert at the bottom."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

import epub_insert
import i18n
import presets
import render
import update_check
from i18n import t
from render import Options
from version import APP_VERSION, UPDATE_SLUG

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_DND = False

BG = "#f3f4f6"
CARD = "#ffffff"
BORDER = "#d1d5db"
TEXT = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
NOTICE = "#d97706"
NOTICE_DARK = "#b45309"          # warning text: NOTICE itself is too pale for small text on white
PREVIEW_BG = "#e5e7eb"
UPDATE_BG = "#eff6ff"
UPDATE_BORDER = "#bfdbfe"
UI_FONTS = {
    "ko": ("Malgun Gothic",),
    "ja": ("Yu Gothic UI", "Meiryo UI", "MS UI Gothic"),
    "zh-CN": ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei"),
    "en": ("Segoe UI",),
}
PREVIEW_DEBOUNCE_MS = 150          # the preview must follow a change within 0.3 s (render ~10 ms + display)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")
OPTION_KEYS = ("publisher", "author", "series", "preset", "width", "height", "template", "color",
               "bg_color", "fg_color", "font", "fmt")


def _enable_dpi_awareness() -> None:
    """Crisp text on high-DPI screens instead of a bitmap-stretched window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _saved_options(settings: dict) -> Options:
    """Options from settings.json, trusting nothing about its types. The file
    sits beside the exe where anyone can edit it; one wrong value must not
    stop the window from opening."""
    raw = settings.get("options")
    raw = raw if isinstance(raw, dict) else {}
    d = Options()
    kw = {}
    for key in OPTION_KEYS:
        val = raw.get(key)
        default = getattr(d, key)
        if isinstance(default, bool) or not isinstance(val, type(default)):
            val = default
        kw[key] = val
    return Options(**kw).validated()


class App:
    def __init__(self, bg_image: str | None = None, epub: str | None = None) -> None:
        _enable_dpi_awareness()
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        self.scale = max(1.0, min(3.0, self.root.winfo_fpixels("1i") / 96.0))
        self.root.geometry(f"{self.px(1120)}x{self.px(800)}")
        self._min_size = (self.px(1000), self.px(720))
        self.root.minsize(*self._min_size)

        self.worker: threading.Thread | None = None
        self.preview_gen = 0
        self._preview_job: str | None = None
        self._texts: list[tuple[tk.Misc, str, str]] = []
        self._combos: list[tuple[ttk.Combobox, list[str]]] = []
        self._status_key = ""
        self._status_kwargs: dict = {}
        self._running = False
        self._closing = False
        self._last_result: render.RenderResult | None = None
        self._last_warnings: list[tuple[str, dict]] = []
        self._photo: ImageTk.PhotoImage | None = None
        self._result_path: str | None = None
        self._last_dir = ""

        settings = i18n.load_settings()
        saved = _saved_options(settings)
        last_dir = settings.get("last_dir")
        self._last_dir = last_dir if isinstance(last_dir, str) and os.path.isdir(last_dir) else ""
        self.upd = update_check.load_prefs(settings)
        self.var_update_check = tk.BooleanVar(value=self.upd.update_check)
        self._update_info: update_check.UpdateInfo | None = None
        self._notice_extra = 0                         # how much taller the window is made while the notice shows
        self.var_title = tk.StringVar()
        self.var_subtitle = tk.StringVar()
        self.var_author = tk.StringVar(value=saved.author)
        self.var_publisher = tk.StringVar(value=saved.publisher)
        self.var_series = tk.StringVar(value=saved.series)
        self.var_volume = tk.StringVar()
        self.var_width = tk.StringVar(value=str(saved.width))
        self.var_height = tk.StringVar(value=str(saved.height))
        self.var_bg_color = tk.StringVar(value=saved.bg_color)
        self.var_fg_color = tk.StringVar(value=saved.fg_color)
        self.var_bg_image = tk.StringVar(value=bg_image or "")
        self.var_epub = tk.StringVar(value=epub or "")
        self.var_fmt = tk.StringVar(value=saved.fmt)
        self.var_lang = tk.StringVar(value=i18n.LANG_NAMES[i18n.current_lang()])
        self._preset = saved.preset
        self._template = saved.template
        self._color = saved.color
        self._font = saved.font

        self._style()
        self._build()
        self._apply_texts()
        for var in (self.var_title, self.var_subtitle, self.var_author, self.var_publisher, self.var_series,
                    self.var_volume, self.var_width, self.var_height, self.var_bg_color, self.var_fg_color,
                    self.var_bg_image, self.var_fmt):
            var.trace_add("write", lambda *_: self._on_change())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_gate()
        self._preview_job = self.root.after(50, self._refresh_preview)
        # one quiet check after the window is up; never blocks, never shows an error
        update_check.check_async(self.root, UPDATE_SLUG, APP_VERSION, self.upd, self._on_update_result)

    # ------------------------------------------------------------ building
    def px(self, n: int) -> int:
        return int(round(n * self.scale))

    def _reg(self, widget: tk.Misc, key: str, attr: str = "text") -> tk.Misc:
        self._texts.append((widget, key, attr))
        return widget

    def _combo(self, parent: tk.Misc, keys: list[str], index: int, command, width: int = 20) -> ttk.Combobox:
        cmb = ttk.Combobox(parent, state="readonly", width=width, values=[t(k) for k in keys])
        cmb.current(index)
        cmb.bind("<<ComboboxSelected>>", lambda e: command())
        self._combos.append((cmb, keys))
        return cmb

    def _apply_fonts(self) -> None:
        """Japanese and Chinese text in the default font falls back to a spaced-out
        bitmap-like face; use the system UI font of the chosen language instead."""
        try:
            available = set(tkfont.families(self.root))
        except tk.TclError:
            return
        family = next((f for f in UI_FONTS.get(i18n.current_lang(), ()) if f in available), None)
        if family is None:
            return
        for f in (self.font_body, self.font_bold, self.font_title, self.font_small):
            f.configure(family=family)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=family)
            except tk.TclError:
                pass

    def _style(self) -> None:
        """The shared Chaekgalpi look: clam theme, light gray page, white cards, one blue main button."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = tkfont.nametofont("TkDefaultFont")
        self.font_body = base.copy()
        self.font_body.configure(size=10)
        self.font_bold = base.copy()
        self.font_bold.configure(size=10, weight="bold")
        self.font_title = base.copy()
        self.font_title.configure(size=16, weight="bold")
        self.font_small = base.copy()
        self.font_small.configure(size=9)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=10)
            except tk.TclError:
                pass
        self.root.configure(background=BG)
        pad = (self.px(10), self.px(5))
        style.configure(".", background=BG, foreground=TEXT, font=self.font_body, bordercolor=BORDER,
                        lightcolor=BG, darkcolor=BG, troughcolor="#e5e7eb", focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TLabel", background=BG)
        style.configure("Card.TLabel", background=CARD)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=self.font_title)
        style.configure("Section.TLabel", background=CARD, font=self.font_bold)
        style.configure("Small.TLabel", font=self.font_small, foreground=MUTED)
        style.configure("CardSmall.TLabel", background=CARD, font=self.font_small, foreground=MUTED)
        style.configure("Warn.TLabel", background=CARD, font=self.font_small, foreground=NOTICE_DARK)
        style.configure("TSeparator", background=BORDER)
        style.configure("TButton", padding=pad, background=CARD, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
        style.map("TButton", background=[("active", "#eef2ff"), ("disabled", "#f3f4f6")],
                  foreground=[("disabled", "#9ca3af")])
        style.configure("Small.TButton", font=self.font_small, padding=(self.px(8), self.px(3)))
        style.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                        lightcolor=ACCENT, darkcolor=ACCENT, font=self.font_bold,
                        padding=(self.px(22), self.px(8)))
        style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#93c5fd")],
                  foreground=[("disabled", "white")], bordercolor=[("disabled", "#93c5fd")])
        style.configure("TCheckbutton", background=BG, indicatorsize=self.px(14))
        style.map("TCheckbutton", background=[("active", BG)])
        self.font_link = self.font_body.copy()
        self.font_link.configure(underline=True)
        style.configure("Link.TButton", background=BG, foreground=ACCENT, font=self.font_link, borderwidth=0,
                        relief="flat", padding=(self.px(2), self.px(2)), lightcolor=BG, darkcolor=BG, bordercolor=BG)
        style.map("Link.TButton", background=[("active", BG)], foreground=[("active", ACCENT_DARK)])
        style.configure("AccentSmall.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                        lightcolor=ACCENT, darkcolor=ACCENT, font=self.font_bold, padding=(self.px(12), self.px(3)))
        style.map("AccentSmall.TButton", background=[("active", ACCENT_DARK)])
        style.configure("Card.TRadiobutton", background=CARD, indicatorsize=self.px(13))
        style.map("Card.TRadiobutton", background=[("active", CARD)])
        for widget in ("TCombobox", "TSpinbox"):
            style.configure(widget, fieldbackground=CARD, background=CARD, arrowcolor=MUTED, bordercolor=BORDER,
                            lightcolor=CARD, darkcolor=CARD, padding=(self.px(6), self.px(3)))
        style.map("TCombobox", fieldbackground=[("readonly", CARD), ("disabled", "#f3f4f6")],
                  background=[("readonly", CARD)], foreground=[("readonly", TEXT), ("disabled", "#9ca3af")],
                  selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])
        style.map("TSpinbox", fieldbackground=[("disabled", "#f3f4f6")], foreground=[("disabled", "#9ca3af")],
                  bordercolor=[("focus", ACCENT)])
        style.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                        padding=(self.px(6), self.px(4)))
        style.map("TEntry", bordercolor=[("focus", ACCENT)], fieldbackground=[("readonly", "#f9fafb")])
        style.configure("Vertical.TScrollbar", background="#e5e7eb", troughcolor=BG, bordercolor=BG)

    def _card(self, parent: tk.Misc, title_key: str) -> ttk.Frame:
        """A white card with a section title and a hairline under it. Content starts at row 2."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(self.px(16), self.px(12), self.px(16), self.px(14)))
        card.columnconfigure(1, weight=1)
        self._reg(ttk.Label(card, style="Section.TLabel"), title_key).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Separator(card).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(self.px(6), self.px(8)))
        return card

    def _field(self, card: ttk.Frame, row: int, key: str, widget: tk.Misc, pady: int = 3) -> None:
        self._reg(ttk.Label(card, style="Card.TLabel"), key).grid(
            row=row, column=0, sticky="w", padx=(0, self.px(12)), pady=self.px(pady))
        widget.grid(row=row, column=1, sticky="ew", pady=self.px(pady))

    def _autowrap(self, label: tk.Misc, container: tk.Misc, margin: int) -> None:
        """Keep a label's wrapping in step with the width of the box that holds it."""
        container.bind("<Configure>", lambda e: label.configure(wraplength=max(120, e.width - self.px(margin))), add="+")

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main = ttk.Frame(root, padding=(self.px(20), self.px(16), self.px(20), self.px(16)))
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(1, weight=1)

        # ---- header: title, one-line promise, language
        head = ttk.Frame(main)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, self.px(14)))
        head.columnconfigure(0, weight=1)
        self._reg(ttk.Label(head, style="Title.TLabel"), "app_title").grid(row=0, column=0, sticky="w")
        self._reg(ttk.Label(head, style="Muted.TLabel"), "app_tagline").grid(row=1, column=0, sticky="w")
        lang = ttk.Frame(head)
        lang.grid(row=0, column=1, rowspan=2, sticky="e")
        self.btn_feedback = self._reg(ttk.Button(lang, style="Link.TButton", cursor="hand2",
                                                 command=self._send_feedback), "btn_feedback")
        self.btn_feedback.pack(side="left", padx=(0, self.px(14)))
        self.chk_update = self._reg(ttk.Checkbutton(lang, variable=self.var_update_check,
                                                    command=self._on_update_toggle), "opt_update_check")
        self.chk_update.pack(side="left", padx=(0, self.px(18)))
        self._reg(ttk.Label(lang, style="Muted.TLabel"), "lbl_language").pack(side="left", padx=(0, self.px(8)))
        self.cmb_lang = ttk.Combobox(lang, state="readonly", width=10, textvariable=self.var_lang,
                                     values=[i18n.LANG_NAMES[c] for c in i18n.LANGS])
        self.cmb_lang.pack(side="left")
        self.cmb_lang.bind("<<ComboboxSelected>>", self._on_lang)

        # ---- left column: 1) what goes on the cover, 2) how it looks
        left = ttk.Frame(main)
        left.grid(row=1, column=0, sticky="ns", padx=(0, self.px(16)))
        inp = self._card(left, "step_input")
        inp.grid(row=0, column=0, sticky="ew")
        inp.columnconfigure(1, minsize=self.px(250))
        self.entries: dict[str, ttk.Entry] = {}
        rows = (("field_title", self.var_title), ("field_subtitle", self.var_subtitle),
                ("field_author", self.var_author), ("field_series", self.var_series),
                ("field_volume", self.var_volume), ("field_publisher", self.var_publisher))
        r = 2
        for key, var in rows:
            ent = ttk.Entry(inp, textvariable=var)
            self._field(inp, r, key, ent)
            self.entries[key] = ent
            r += 1
        self.lbl_pub_warn = ttk.Label(inp, style="Warn.TLabel", wraplength=self.px(400))
        self.lbl_pub_warn.grid(row=r, column=0, columnspan=2, sticky="w")

        des = self._card(left, "step_design")
        des.grid(row=1, column=0, sticky="ew", pady=(self.px(12), 0))
        des.columnconfigure(1, minsize=self.px(250))
        r = 2
        self.cmb_preset = self._combo(des, [f"preset_{k}" for k in presets.PRESET_KEYS],
                                      presets.PRESET_KEYS.index(self._preset), self._on_preset)
        self._field(des, r, "preset_label", self.cmb_preset)
        r += 1
        size = ttk.Frame(des, style="Card.TFrame")
        size.configure(relief="flat", borderwidth=0)
        self._reg(ttk.Label(size, style="CardSmall.TLabel"), "lbl_width").pack(side="left")
        self.spn_width = ttk.Spinbox(size, from_=presets.MIN_SIDE, to=presets.MAX_SIDE, width=6,
                                     textvariable=self.var_width)
        self.spn_width.pack(side="left", padx=(self.px(6), self.px(14)))
        self._reg(ttk.Label(size, style="CardSmall.TLabel"), "lbl_height").pack(side="left")
        self.spn_height = ttk.Spinbox(size, from_=presets.MIN_SIDE, to=presets.MAX_SIDE, width=6,
                                      textvariable=self.var_height)
        self.spn_height.pack(side="left", padx=(self.px(6), 0))
        size.grid(row=r, column=1, sticky="w", pady=(0, self.px(3)))
        r += 1
        self.cmb_template = self._combo(des, [f"template_{i}" for i in presets.TEMPLATE_IDS],
                                        presets.TEMPLATE_IDS.index(self._template), self._on_change)
        self._field(des, r, "template_label", self.cmb_template)
        r += 1
        self.cmb_color = self._combo(des, [f"color_{i + 1}" for i in range(len(presets.COLOR_SCHEMES))],
                                     self._color - 1, self._on_change)
        self._field(des, r, "color_label", self.cmb_color)
        r += 1
        colors = ttk.Frame(des, style="Card.TFrame")
        colors.configure(relief="flat", borderwidth=0)
        self.btn_bg_color = self._reg(ttk.Button(colors, style="Small.TButton",
                                                 command=lambda: self._pick_color(self.var_bg_color, "dlg_color_bg")),
                                      "btn_bg_color")
        self.btn_bg_color.pack(side="left")
        self.btn_fg_color = self._reg(ttk.Button(colors, style="Small.TButton",
                                                 command=lambda: self._pick_color(self.var_fg_color, "dlg_color_fg")),
                                      "btn_fg_color")
        self.btn_fg_color.pack(side="left", padx=self.px(6))
        self.btn_color_reset = self._reg(ttk.Button(colors, style="Small.TButton", command=self._reset_colors),
                                         "btn_color_reset")
        self.btn_color_reset.pack(side="left")
        colors.grid(row=r, column=1, sticky="w", pady=(0, self.px(3)))
        r += 1
        self.cmb_font = self._combo(des, [f"font_{k}" for k in presets.FONT_KEYS],
                                    presets.FONT_KEYS.index(self._font), self._on_change)
        self._field(des, r, "font_label", self.cmb_font)
        r += 1
        bg = ttk.Frame(des, style="Card.TFrame")
        bg.configure(relief="flat", borderwidth=0)
        bg.columnconfigure(0, weight=1)
        self.ent_bg = ttk.Entry(bg, textvariable=self.var_bg_image, state="readonly")
        self.ent_bg.grid(row=0, column=0, sticky="ew")
        self.btn_bg_browse = self._reg(ttk.Button(bg, style="Small.TButton", command=self._browse_bg), "btn_browse")
        self.btn_bg_browse.grid(row=0, column=1, padx=(self.px(6), 0))
        self.btn_bg_clear = self._reg(ttk.Button(bg, style="Small.TButton",
                                                 command=lambda: self.var_bg_image.set("")), "btn_clear")
        self.btn_bg_clear.grid(row=0, column=2, padx=(self.px(6), 0))
        self._field(des, r, "bg_image", bg)
        r += 1
        fmts = ttk.Frame(des, style="Card.TFrame")
        fmts.configure(relief="flat", borderwidth=0)
        self.rad_jpg = self._reg(ttk.Radiobutton(fmts, style="Card.TRadiobutton", variable=self.var_fmt, value="jpg"),
                                 "format_jpg")
        self.rad_jpg.pack(side="left")
        self.rad_png = self._reg(ttk.Radiobutton(fmts, style="Card.TRadiobutton", variable=self.var_fmt, value="png"),
                                 "format_png")
        self.rad_png.pack(side="left", padx=self.px(14))
        self._field(des, r, "lbl_format", fmts)

        # ---- right column: 3) the big preview, then EPUB, then the actions
        right = ttk.Frame(main)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        pcard = ttk.Frame(right, style="Card.TFrame", padding=(self.px(16), self.px(12), self.px(16), self.px(12)))
        pcard.grid(row=0, column=0, sticky="nsew")
        pcard.columnconfigure(0, weight=1)
        pcard.rowconfigure(2, weight=1)
        self._reg(ttk.Label(pcard, style="Section.TLabel"), "step_preview").grid(row=0, column=0, sticky="w")
        ttk.Separator(pcard).grid(row=1, column=0, sticky="ew", pady=(self.px(6), self.px(10)))
        self.preview_holder = tk.Frame(pcard, background=PREVIEW_BG, width=self.px(260), height=self.px(320))
        self.preview_holder.grid(row=2, column=0, sticky="nsew")
        self.lbl_preview = tk.Label(self.preview_holder, background=PREVIEW_BG, borderwidth=0)
        self.lbl_preview.place(relx=0.5, rely=0.5, anchor="center")     # placed, so the image never resizes the window
        self.preview_holder.bind("<Configure>", lambda e: self._show_preview())
        self.lbl_warn = ttk.Label(pcard, style="Warn.TLabel", justify="left")
        self.lbl_warn.grid(row=3, column=0, sticky="ew", pady=(self.px(8), 0))
        self.lbl_warn.grid_remove()
        self._autowrap(self.lbl_warn, pcard, 34)
        self.lbl_status = ttk.Label(pcard, style="CardSmall.TLabel", anchor="w")
        self.lbl_status.grid(row=4, column=0, sticky="ew", pady=(self.px(4), 0))

        ecard = self._card(right, "step_epub")
        ecard.grid(row=1, column=0, sticky="ew", pady=(self.px(12), 0))
        epub = ttk.Frame(ecard, style="Card.TFrame")
        epub.configure(relief="flat", borderwidth=0)
        epub.columnconfigure(0, weight=1)
        self.ent_epub = ttk.Entry(epub, textvariable=self.var_epub, state="readonly")
        self.ent_epub.grid(row=0, column=0, sticky="ew")
        self.btn_epub_browse = self._reg(ttk.Button(epub, style="Small.TButton", command=self._browse_epub),
                                         "btn_browse")
        self.btn_epub_browse.grid(row=0, column=1, padx=(self.px(6), 0))
        self.btn_epub_clear = self._reg(ttk.Button(epub, style="Small.TButton",
                                                   command=lambda: self.var_epub.set("")), "btn_clear")
        self.btn_epub_clear.grid(row=0, column=2, padx=(self.px(6), 0))
        self._field(ecard, 2, "field_epub", epub)
        self.lbl_drop = ttk.Label(ecard, style="CardSmall.TLabel", justify="left")
        self._reg(self.lbl_drop, "drop_hint")
        self.lbl_drop.grid(row=3, column=0, columnspan=2, sticky="w", pady=(self.px(4), 0))
        self._autowrap(self.lbl_drop, ecard, 34)

        bar = ttk.Frame(right)
        bar.grid(row=2, column=0, sticky="ew", pady=(self.px(12), 0))
        bar.columnconfigure(1, weight=1)
        self.btn_open = self._reg(ttk.Button(bar, style="Small.TButton", command=self._open_result, state="disabled"),
                                  "btn_open_result")
        self.btn_open.grid(row=0, column=0, sticky="w")
        self.btn_epub = self._reg(ttk.Button(bar, command=self._insert_epub), "btn_insert_epub")
        self.btn_epub.grid(row=0, column=2, padx=(0, self.px(8)))
        self.btn_save = self._reg(ttk.Button(bar, style="Accent.TButton", command=self._save), "btn_save")
        self.btn_save.grid(row=0, column=3)

        logbox = ttk.Frame(right)
        logbox.grid(row=3, column=0, sticky="ew", pady=(self.px(10), 0))
        logbox.columnconfigure(0, weight=1)
        self._reg(ttk.Label(logbox, style="Small.TLabel"), "lbl_log").grid(row=0, column=0, sticky="w")
        self.txt_log = tk.Text(logbox, height=3, state="disabled", wrap="none", background=CARD, foreground=TEXT,
                               relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
                               highlightcolor=BORDER, font=self.font_small, padx=self.px(6), pady=self.px(4))
        self.txt_log.grid(row=1, column=0, sticky="ew")

        self.frm_update = tk.Frame(root, background=UPDATE_BG, highlightthickness=1, highlightbackground=UPDATE_BORDER)
        self.lbl_update = tk.Label(self.frm_update, background=UPDATE_BG, foreground=TEXT, font=self.font_body, anchor="w")
        self.lbl_update.pack(side="left", fill="x", expand=True, padx=(self.px(12), self.px(8)), pady=self.px(6))
        self.btn_update_later = self._reg(ttk.Button(self.frm_update, style="Small.TButton", command=self._update_later),
                                          "btn_later")
        self.btn_update_later.pack(side="right", padx=(self.px(6), self.px(10)), pady=self.px(5))
        self.btn_update_open = self._reg(ttk.Button(self.frm_update, style="AccentSmall.TButton",
                                                    command=self._update_open), "btn_update")
        self.btn_update_open.pack(side="right", pady=self.px(5))

        if _HAS_DND:
            for w in (root, self.lbl_preview, self.preview_holder, self.lbl_drop):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        self._on_preset()

    def _apply_texts(self) -> None:
        self._apply_fonts()
        self.root.title(t("app_title"))
        for widget, key, attr in self._texts:
            try:
                widget.configure(**{attr: t(key)})
            except tk.TclError:
                pass
        for cmb, keys in self._combos:
            idx = cmb.current()
            cmb.configure(values=[t(k) for k in keys])
            cmb.current(max(0, idx))
        self._show_warnings()
        self._update_gate()
        if self._update_info is not None:                # the notice follows the language too
            self.lbl_update.configure(text=t("update_available", latest=self._update_info.latest,
                                             current=self._update_info.current))
        if self._status_key:
            self._set_status(self._status_key, **self._status_kwargs)

    # ------------------------------------------------------------ helpers
    def _set_status(self, key: str, **kwargs) -> None:
        self._status_key, self._status_kwargs = key, kwargs
        self.lbl_status.configure(text=t(key, **kwargs) if key else "")

    def log(self, key: str, **kwargs) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", t(key, **kwargs) + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def options(self) -> Options:
        return Options(
            title=self.var_title.get(), subtitle=self.var_subtitle.get(), author=self.var_author.get(),
            publisher=self.var_publisher.get(), series=self.var_series.get(), volume=self.var_volume.get(),
            preset=presets.PRESET_KEYS[max(0, self.cmb_preset.current())],
            width=_to_int(self.var_width.get(), 800), height=_to_int(self.var_height.get(), 1200),
            template=presets.TEMPLATE_IDS[max(0, self.cmb_template.current())],
            color=max(0, self.cmb_color.current()) + 1,
            bg_color=self.var_bg_color.get(), fg_color=self.var_fg_color.get(),
            bg_image=self.var_bg_image.get(), font=presets.FONT_KEYS[max(0, self.cmb_font.current())],
            fmt=self.var_fmt.get(),
        ).validated()

    def _save_settings(self) -> None:
        settings = i18n.load_settings()
        o = self.options()
        settings["options"] = {k: getattr(o, k) for k in OPTION_KEYS}
        settings["last_dir"] = self._last_dir
        self.upd.update_check = bool(self.var_update_check.get())
        update_check.store_prefs(settings, self.upd)
        i18n.save_settings(settings)

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for w in (self.btn_save, self.btn_epub, self.btn_epub_browse, self.btn_epub_clear, self.btn_bg_browse,
                  self.btn_bg_clear, self.btn_bg_color, self.btn_fg_color, self.btn_color_reset):
            w.configure(state=state)
        self.cmb_lang.configure(state="disabled" if running else "readonly")
        if not running:
            self._update_gate()

    def _update_gate(self) -> None:
        """The save buttons stay off while the publisher name is empty, and
        the label says why: a missing publisher fails the platform review."""
        empty = not self.var_publisher.get().strip()
        self.lbl_pub_warn.configure(text=t("warn_publisher_empty") if empty else "")
        if not self._running:
            state = "disabled" if empty else "normal"
            self.btn_save.configure(state=state)
            self.btn_epub.configure(state=state)
        self.btn_open.configure(state="normal" if self._result_path else "disabled")

    def _on_lang(self, _event=None) -> None:
        index = self.cmb_lang.current()
        if index >= 0:
            i18n.set_lang(i18n.LANGS[index])
        self._apply_texts()

    def _on_preset(self) -> None:
        custom = presets.PRESET_KEYS[max(0, self.cmb_preset.current())] == "custom"
        state = "normal" if custom else "disabled"
        self.spn_width.configure(state=state)
        self.spn_height.configure(state=state)
        self._on_change()

    def _on_change(self) -> None:
        self._update_gate()
        self._schedule_preview()

    def _pick_color(self, var: tk.StringVar, title_key: str) -> None:
        initial = var.get() or (self.options().colors()[0] if var is self.var_bg_color else self.options().colors()[1])
        _, hexcolor = colorchooser.askcolor(color=initial, title=t(title_key), parent=self.root)
        if hexcolor:
            var.set(hexcolor)

    def _reset_colors(self) -> None:
        self.var_bg_color.set("")
        self.var_fg_color.set("")

    def _browse_bg(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[(t("file_dialog_image"), "*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff")],
            initialdir=self._last_dir or None, parent=self.root)
        if path:
            self._remember_dir(path)
            self.var_bg_image.set(path)

    def _browse_epub(self) -> None:
        path = filedialog.askopenfilename(filetypes=[(t("file_dialog_epub"), "*.epub *.EPUB")],
                                          initialdir=self._last_dir or None, parent=self.root)
        if path:
            self._remember_dir(path)
            self.var_epub.set(path)

    def _remember_dir(self, path: str) -> None:
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            self._last_dir = folder

    def _on_drop(self, event) -> None:
        if self._running:
            return
        for p in self.root.tk.splitlist(event.data):
            if not os.path.isfile(p):
                self.log("err_open_failed", name=p)
                continue
            low = p.lower()
            if low.endswith(".epub"):
                self.var_epub.set(p)
            elif low.endswith(IMAGE_EXTS):
                self.var_bg_image.set(p)
            else:
                self.log("err_open_failed", name=p)
            self._remember_dir(p)

    # ------------------------------------------------------------ preview
    def _schedule_preview(self) -> None:
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(PREVIEW_DEBOUNCE_MS, self._refresh_preview)

    def _refresh_preview(self) -> None:
        self._preview_job = None
        self.preview_gen += 1
        gen = self.preview_gen
        opts = self.options()

        def work() -> None:
            try:
                result = render.render(opts)
                error = None
            except render.FontMissing as exc:
                result, error = None, ("err_font_missing", {"name": str(exc)})
            except render.BackgroundOpenError as exc:
                result, error = None, ("err_bg_open_failed", {"name": str(exc)})
            except Exception as exc:  # never let the preview thread die silently
                result, error = None, ("err_render_failed", {"error": str(exc)})
            if gen == self.preview_gen and not self._closing:
                try:
                    self.root.after(0, self._preview_done, gen, result, error)
                except (RuntimeError, tk.TclError):
                    pass                         # the window closed while we were rendering

        threading.Thread(target=work, daemon=True).start()

    def _preview_done(self, gen: int, result: render.RenderResult | None, error) -> None:
        if gen != self.preview_gen or self._closing:
            return
        if result is None:
            self._last_result = None
            self._last_warnings = [error] if error else []
            self._show_warnings()
            return
        self._last_result = result
        self._last_warnings = list(result.warnings)
        self._show_warnings()
        self._set_status("status_info", width=result.image.width, height=result.image.height,
                         size=result.title_size)
        self._show_preview()

    def _show_warnings(self) -> None:
        text = "\n".join(t(k, **kw) for k, kw in self._last_warnings)
        self.lbl_warn.configure(text=text)
        if text:
            self.lbl_warn.grid()
        else:
            self.lbl_warn.grid_remove()

    def _show_preview(self) -> None:
        if self._last_result is None:
            return
        img = self._last_result.image
        w = max(120, self.preview_holder.winfo_width() - self.px(24))
        h = max(160, self.preview_holder.winfo_height() - self.px(24))
        scale = min(w / img.width, h / img.height, 1.0)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        thumb = ImageOps.expand(img.resize(size, Image.Resampling.LANCZOS), border=1, fill="#cbd5e1")
        self._photo = ImageTk.PhotoImage(thumb, master=self.root)      # belongs to this window, not the default root
        self.lbl_preview.configure(image=self._photo)

    # ------------------------------------------------------------ save
    def _render_now(self) -> render.RenderResult | None:
        try:
            result = render.render(self.options())
        except render.FontMissing as exc:
            messagebox.showerror(t("dlg_error"), t("err_font_missing", name=str(exc)), parent=self.root)
            return None
        except render.BackgroundOpenError as exc:
            messagebox.showerror(t("dlg_error"), t("err_bg_open_failed", name=str(exc)), parent=self.root)
            return None
        if not render.publisher_in_zone(result):
            messagebox.showerror(t("dlg_error"), t("warn_publisher_zone"), parent=self.root)
            return None
        return result

    def _save(self) -> None:
        if self._running or not self.var_publisher.get().strip():
            return
        opts = self.options()
        ext = ".png" if opts.fmt == "png" else ".jpg"
        path = filedialog.asksaveasfilename(
            initialfile="cover" + ext, defaultextension=ext, initialdir=self._last_dir or None,
            filetypes=[(t("file_dialog_jpg"), "*.jpg"), (t("file_dialog_png"), "*.png")],
            confirmoverwrite=False, parent=self.root)
        if not path:
            return
        self._remember_dir(path)
        result = self._render_now()
        if result is None:
            return
        try:
            final = render.save_cover(result.image, path, opts.fmt)
        except OSError as exc:
            messagebox.showerror(t("dlg_error"), t("err_save_failed", error=exc), parent=self.root)
            return
        self._result_path = final
        self._remember_dir(final)                       # the folder exists now, even if it was new
        self._save_settings()
        self.log("msg_saved", path=final)
        self._set_status("msg_saved", path=final)
        self._update_gate()

    def _insert_epub(self) -> None:
        if self._running or not self.var_publisher.get().strip():
            return
        epub = self.var_epub.get()
        if not epub:
            self._browse_epub()
            epub = self.var_epub.get()
            if not epub:
                return
        try:
            info = epub_insert.inspect_epub(epub)
        except epub_insert.NotEpub:
            messagebox.showerror(t("dlg_error"), t("err_epub_open_failed", name=os.path.basename(epub)), parent=self.root)
            return
        if info.drm:
            messagebox.showerror(t("dlg_error"), t("err_epub_drm", name=os.path.basename(epub)), parent=self.root)
            return
        if info.has_cover and not messagebox.askyesno(t("dlg_confirm"), t("warn_epub_replace"), parent=self.root):
            return
        result = self._render_now()
        if result is None:
            return
        opts = self.options()
        data = render.to_bytes(result.image, opts.fmt)
        self._set_running(True)
        self._set_status("status_inserting", name=os.path.basename(epub))

        def work() -> None:
            try:
                ins = epub_insert.insert_cover(epub, data, opts.fmt)
                self.root.after(0, self._insert_done, ins, None)
            except Exception as exc:
                self.root.after(0, self._insert_done, None, exc)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _insert_done(self, ins: epub_insert.InsertResult | None, exc: Exception | None) -> None:
        if self._closing:
            return
        self._set_running(False)
        if ins is None:
            messagebox.showerror(t("dlg_error"), t("err_epub_failed", error=exc), parent=self.root)
            self._set_status("status_ready")
            return
        self._result_path = ins.output_path
        self._save_settings()
        self.log("msg_epub_done", path=ins.output_path)
        if ins.duplicates:
            self.log("warn_epub_dup", names=", ".join(ins.duplicates))
        self._set_status("msg_epub_done", path=ins.output_path)
        self._update_gate()
        messagebox.showinfo(t("dlg_confirm"), t("msg_epub_done", path=ins.output_path), parent=self.root)

    def _open_result(self) -> None:
        if self._result_path and os.path.exists(self._result_path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self._result_path)])

    # ------------------------------------------------------------ new version notice
    def _on_update_result(self, info: update_check.UpdateInfo | None, answered: bool) -> None:
        """Tk thread. The day is recorded only when the server answered, so an offline
        start asks again next time."""
        if self._closing:
            return
        if answered:
            self.upd.update_check_last = update_check.today()
            self._save_settings()
        if info is not None and self.var_update_check.get():
            self._update_info = info
            self._show_update_notice()

    def _resize_for_notice(self, dy: int) -> None:
        """The notice is one line under the two columns. Making the window exactly that much
        taller (and its minimum too) keeps every card where it was, instead of squeezing them."""
        self._notice_extra = max(0, self._notice_extra + dy)
        self.root.minsize(self._min_size[0], self._min_size[1] + self._notice_extra)
        try:
            if self.root.state() != "normal":
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
            limit = max(h, self.root.winfo_screenheight() - self.px(80))
            self.root.geometry(f"{w}x{min(h + dy, limit)}")
        except tk.TclError:
            pass

    def _show_update_notice(self) -> None:
        info = self._update_info
        if info is None:
            self._hide_update_notice()
            return
        self.lbl_update.configure(text=t("update_available", latest=info.latest, current=info.current))
        if self.frm_update.winfo_manager():
            return
        self.frm_update.grid(row=1, column=0, sticky="ew", padx=self.px(20), pady=(0, self.px(14)))
        self.root.update_idletasks()
        self._resize_for_notice(self.frm_update.winfo_reqheight() + self.px(14))

    def _hide_update_notice(self) -> None:
        self._update_info = None
        if self.frm_update.winfo_manager():
            self.frm_update.grid_remove()
            self._resize_for_notice(-self._notice_extra)

    def _update_open(self) -> None:
        """The tools page, never a download: the site counts and serves the download."""
        info = self._update_info
        if info is not None:
            url = update_check.link_for(info, i18n.current_lang())    # the language on screen at the click
            if url.startswith(update_check.SITE):
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        self._hide_update_notice()

    def _send_feedback(self) -> None:
        """The feedback box on the tools page, in the browser. Nothing is sent from here;
        when no browser opens, the address is shown so it can be copied (Ctrl+C copies the box)."""
        url = update_check.feedback_url(UPDATE_SLUG, APP_VERSION, i18n.current_lang())   # the language at the click
        try:
            opened = webbrowser.open(url)
        except Exception:                               # webbrowser.Error, OSError, ...
            opened = False
        if not opened:
            messagebox.showwarning(t("app_title"), t("feedback_failed", url=url), parent=self.root)

    def _update_later(self) -> None:
        if self._update_info is not None:
            self.upd.update_check_skipped = self._update_info.latest      # a newer one is shown again
            self._save_settings()
        self._hide_update_notice()

    def _on_update_toggle(self) -> None:
        self._save_settings()
        if not self.var_update_check.get():
            self._hide_update_notice()

    # ------------------------------------------------------------ closing
    def _on_close(self) -> None:
        self._closing = True
        self.preview_gen += 1
        if self._preview_job:                           # a pending preview must not fire into a closed window
            self.root.after_cancel(self._preview_job)
            self._preview_job = None
        self._save_settings()
        self._finish_close()

    def _finish_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.root.after(100, self._finish_close)   # never kill a half-written EPUB
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def launch(bg_image: str | None = None, epub: str | None = None) -> None:
    App(bg_image=bg_image, epub=epub).run()
