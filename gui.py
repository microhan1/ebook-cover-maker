"""tkinter GUI for ebook-cover-maker: a form on the left, a live preview on
the right, save and EPUB-insert at the bottom."""
from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

import epub_insert
import i18n
import presets
import render
from i18n import t
from render import Options

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_DND = False

PREVIEW_DEBOUNCE_MS = 150          # the preview must follow a change within 0.3 s (render ~10 ms + display)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")
OPTION_KEYS = ("publisher", "author", "series", "preset", "width", "height", "template", "color",
               "bg_color", "fg_color", "font", "fmt")


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
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        self.root.geometry("1120x780")
        self.root.minsize(980, 700)

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

        self._build()
        self._apply_texts()
        for var in (self.var_title, self.var_subtitle, self.var_author, self.var_publisher, self.var_series,
                    self.var_volume, self.var_width, self.var_height, self.var_bg_color, self.var_fg_color,
                    self.var_bg_image, self.var_fmt):
            var.trace_add("write", lambda *_: self._on_change())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_gate()
        self.root.after(50, self._refresh_preview)

    # ------------------------------------------------------------ building
    def _reg(self, widget: tk.Misc, key: str, attr: str = "text") -> tk.Misc:
        self._texts.append((widget, key, attr))
        return widget

    def _combo(self, parent: tk.Misc, keys: list[str], index: int, command, width: int = 26) -> ttk.Combobox:
        cmb = ttk.Combobox(parent, state="readonly", width=width, values=[t(k) for k in keys])
        cmb.current(index)
        cmb.bind("<<ComboboxSelected>>", lambda e: command())
        self._combos.append((cmb, keys))
        return cmb

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        # ---- header
        head = ttk.Frame(root, padding=(12, 10, 12, 4))
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        head.columnconfigure(0, weight=1)
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self._reg(ttk.Label(head, font=(family, 15, "bold")), "app_title").grid(row=0, column=0, sticky="w")
        self._reg(ttk.Label(head), "lbl_language").grid(row=0, column=1, padx=(0, 6))
        self.cmb_lang = ttk.Combobox(head, state="readonly", width=10, textvariable=self.var_lang,
                                     values=[i18n.LANG_NAMES[c] for c in i18n.LANGS])
        self.cmb_lang.grid(row=0, column=2)
        self.cmb_lang.bind("<<ComboboxSelected>>", self._on_lang)

        # ---- form
        form = ttk.Frame(root, padding=(12, 4))
        form.grid(row=1, column=0, sticky="nsw")
        form.columnconfigure(1, weight=1)
        r = 0
        self.entries: dict[str, ttk.Entry] = {}
        for key, var in (("field_title", self.var_title), ("field_subtitle", self.var_subtitle),
                         ("field_author", self.var_author), ("field_publisher", self.var_publisher),
                         ("field_series", self.var_series), ("field_volume", self.var_volume)):
            self._reg(ttk.Label(form), key).grid(row=r, column=0, sticky="w", pady=2)
            ent = ttk.Entry(form, textvariable=var, width=34)
            ent.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
            self.entries[key] = ent
            r += 1
        self.lbl_pub_warn = ttk.Label(form, foreground="#c0392b", wraplength=360)
        self.lbl_pub_warn.grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1

        ttk.Separator(form).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
        r += 1
        self._reg(ttk.Label(form), "preset_label").grid(row=r, column=0, sticky="w", pady=2)
        self.cmb_preset = self._combo(form, [f"preset_{k}" for k in presets.PRESET_KEYS],
                                      presets.PRESET_KEYS.index(self._preset), self._on_preset)
        self.cmb_preset.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
        r += 1
        size = ttk.Frame(form)
        size.grid(row=r, column=1, columnspan=3, sticky="w")
        self._reg(ttk.Label(size), "lbl_width").pack(side="left")
        self.spn_width = ttk.Spinbox(size, from_=presets.MIN_SIDE, to=presets.MAX_SIDE, width=6,
                                     textvariable=self.var_width)
        self.spn_width.pack(side="left", padx=(4, 10))
        self._reg(ttk.Label(size), "lbl_height").pack(side="left")
        self.spn_height = ttk.Spinbox(size, from_=presets.MIN_SIDE, to=presets.MAX_SIDE, width=6,
                                      textvariable=self.var_height)
        self.spn_height.pack(side="left", padx=4)
        r += 1
        self._reg(ttk.Label(form), "template_label").grid(row=r, column=0, sticky="w", pady=2)
        self.cmb_template = self._combo(form, [f"template_{i}" for i in presets.TEMPLATE_IDS],
                                        presets.TEMPLATE_IDS.index(self._template), self._on_change)
        self.cmb_template.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
        r += 1
        self._reg(ttk.Label(form), "color_label").grid(row=r, column=0, sticky="w", pady=2)
        self.cmb_color = self._combo(form, [f"color_{i + 1}" for i in range(len(presets.COLOR_SCHEMES))],
                                     self._color - 1, self._on_change)
        self.cmb_color.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
        r += 1
        colors = ttk.Frame(form)
        colors.grid(row=r, column=1, columnspan=3, sticky="w")
        self.btn_bg_color = self._reg(ttk.Button(colors, command=lambda: self._pick_color(self.var_bg_color, "dlg_color_bg")),
                                      "btn_bg_color")
        self.btn_bg_color.pack(side="left")
        self.btn_fg_color = self._reg(ttk.Button(colors, command=lambda: self._pick_color(self.var_fg_color, "dlg_color_fg")),
                                      "btn_fg_color")
        self.btn_fg_color.pack(side="left", padx=4)
        self.btn_color_reset = self._reg(ttk.Button(colors, command=self._reset_colors), "btn_color_reset")
        self.btn_color_reset.pack(side="left")
        r += 1
        self._reg(ttk.Label(form), "font_label").grid(row=r, column=0, sticky="w", pady=2)
        self.cmb_font = self._combo(form, [f"font_{k}" for k in presets.FONT_KEYS],
                                    presets.FONT_KEYS.index(self._font), self._on_change)
        self.cmb_font.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
        r += 1
        self._reg(ttk.Label(form), "bg_image").grid(row=r, column=0, sticky="w", pady=2)
        self.ent_bg = ttk.Entry(form, textvariable=self.var_bg_image, width=22, state="readonly")
        self.ent_bg.grid(row=r, column=1, sticky="ew", pady=2)
        self.btn_bg_browse = self._reg(ttk.Button(form, command=self._browse_bg, width=6), "btn_browse")
        self.btn_bg_browse.grid(row=r, column=2, padx=(4, 0))
        self.btn_bg_clear = self._reg(ttk.Button(form, command=lambda: self.var_bg_image.set(""), width=6), "btn_clear")
        self.btn_bg_clear.grid(row=r, column=3, padx=(4, 0))
        r += 1
        self._reg(ttk.Label(form), "lbl_format").grid(row=r, column=0, sticky="w", pady=2)
        fmts = ttk.Frame(form)
        fmts.grid(row=r, column=1, columnspan=3, sticky="w")
        self.rad_jpg = self._reg(ttk.Radiobutton(fmts, variable=self.var_fmt, value="jpg"), "format_jpg")
        self.rad_jpg.pack(side="left")
        self.rad_png = self._reg(ttk.Radiobutton(fmts, variable=self.var_fmt, value="png"), "format_png")
        self.rad_png.pack(side="left", padx=8)
        r += 1
        ttk.Separator(form).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
        r += 1
        self._reg(ttk.Label(form), "field_epub").grid(row=r, column=0, sticky="w", pady=2)
        self.ent_epub = ttk.Entry(form, textvariable=self.var_epub, width=22, state="readonly")
        self.ent_epub.grid(row=r, column=1, sticky="ew", pady=2)
        self.btn_epub_browse = self._reg(ttk.Button(form, command=self._browse_epub, width=6), "btn_browse")
        self.btn_epub_browse.grid(row=r, column=2, padx=(4, 0))
        self.btn_epub_clear = self._reg(ttk.Button(form, command=lambda: self.var_epub.set(""), width=6), "btn_clear")
        self.btn_epub_clear.grid(row=r, column=3, padx=(4, 0))
        r += 1
        self.lbl_drop = ttk.Label(form, foreground="#666", wraplength=360, justify="left")
        self._reg(self.lbl_drop, "drop_hint")
        self.lbl_drop.grid(row=r, column=0, columnspan=4, sticky="w", pady=(8, 0))

        # ---- preview
        prev = ttk.Frame(root, padding=(4, 4, 12, 4))
        prev.grid(row=1, column=1, sticky="nsew")
        prev.columnconfigure(0, weight=1)
        prev.rowconfigure(0, weight=1)
        self.lbl_preview = ttk.Label(prev, anchor="center", relief="groove", background="#d9d9d9")
        self.lbl_preview.grid(row=0, column=0, sticky="nsew")
        self.lbl_preview.bind("<Configure>", lambda e: self._show_preview())

        # ---- warnings + log
        self.lbl_warn = ttk.Label(root, foreground="#b9770e", wraplength=1000, justify="left")
        self.lbl_warn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12)
        logf = self._reg(ttk.LabelFrame(root, padding=4), "lbl_log")
        logf.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        logf.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(logf, height=3, state="disabled", wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="ew")
        lsb = ttk.Scrollbar(logf, command=self.txt_log.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=lsb.set)

        # ---- bottom
        bot = ttk.Frame(root, padding=(12, 4, 12, 10))
        bot.grid(row=4, column=0, columnspan=2, sticky="ew")
        bot.columnconfigure(0, weight=1)
        self.lbl_status = ttk.Label(bot, anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="w")
        self.btn_open = self._reg(ttk.Button(bot, command=self._open_result, state="disabled"), "btn_open_result")
        self.btn_open.grid(row=0, column=1, padx=(0, 6))
        self.btn_epub = self._reg(ttk.Button(bot, command=self._insert_epub), "btn_insert_epub")
        self.btn_epub.grid(row=0, column=2, padx=(0, 6))
        self.btn_save = self._reg(ttk.Button(bot, command=self._save), "btn_save")
        self.btn_save.grid(row=0, column=3)

        if _HAS_DND:
            for w in (root, self.lbl_preview, self.lbl_drop):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        self._on_preset()

    def _apply_texts(self) -> None:
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
        self.lbl_warn.configure(text="\n".join(t(k, **kw) for k, kw in self._last_warnings))

    def _show_preview(self) -> None:
        if self._last_result is None:
            return
        img = self._last_result.image
        w = max(120, self.lbl_preview.winfo_width() - 8)
        h = max(160, self.lbl_preview.winfo_height() - 8)
        scale = min(w / img.width, h / img.height, 1.0)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        thumb = img.resize(size, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(thumb)
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

    # ------------------------------------------------------------ closing
    def _on_close(self) -> None:
        self._closing = True
        self.preview_gen += 1
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
