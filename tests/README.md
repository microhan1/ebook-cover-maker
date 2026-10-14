# Tests

Standard library `unittest`, no extra packages.

```bash
python -m unittest discover -s tests              # everything (about 4 minutes)
python -m unittest discover -s tests -p "test_cli.py"
```

| File | What it proves |
|---|---|
| `test_render.py` | basic layout, publisher zone, saving, render speed |
| `test_layout_props.py` | seeded random titles in every template and size: nothing leaves the cover, text never overlaps, only the publisher touches the bottom 10% (checked on real pixels) |
| `test_visual.py` | WCAG contrast of every text against what is behind it, for all templates and color schemes, on solid colors and on photos |
| `test_text_input.py` | NFD Hangul, control characters, emoji, Arabic, very long text, determinism |
| `test_background.py` | every image format and mode, EXIF rotation, 16-bit PNG, broken files, decompression bombs, the cache |
| `test_save.py` | never overwriting, failure cleanup, 16 threads and 6 processes saving one name |
| `test_epub.py`, `test_epub_matrix.py` | EPUB insertion into many shapes of EPUB (percent-encoded paths, OPF encodings, title pages that must not be overwritten, damaged zips, races) |
| `test_cli.py` | exit codes, messages in all four languages, bad arguments, EPUB flows |
| `test_i18n.py` | language files, the arguments the code passes to `t()`, language choice order |
| `test_update_check.py` | the new-version check: once a day, off switch, what is sent, silent failures, only tools-site links (the network is always faked) |
| `test_gui.py` | the real Tk window: gate, language switch, truncation, preview speed and ordering, save and EPUB flows, drops |
| `test_exe.py` | the built exe (skipped unless `dist/ebook-cover-maker.exe` exists); one test asks the real server once, read-only, and is skipped when offline |

## Options

* `EPUBCHECK_JAR=<path to epubcheck.jar>` also validates every EPUB the tests produce
  (needs Java; adds about three minutes). Only errors that the source EPUB did not already have count.
* `test_exe.py` runs the real exe, opens its window and takes a picture of it with PrintWindow.
  It never sends mouse or keyboard input.

## Notes

* `helpers.py` redirects the settings file to a temp folder. No test touches a real `settings.json`.
* Tk is only reliable with one window per process. `test_gui.py` shares one window and runs scenarios that
  need a fresh window (damaged settings, closing during work) in their own process through `gui_scenarios.py`.
* Every CLI test passes `--lang`, and none of them calls `main.py` without title, author and publisher
  because that opens the GUI.
