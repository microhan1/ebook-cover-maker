# Chaekgalpi Tools – Ebook Cover Maker

[한국어](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

Type a title, an author and a publisher name and get an ebook cover image that passes self-publishing platform checks (600–1000 px wide, publisher name at the bottom, as required by upaper.net). It is a compliance cover generator, not a design tool. No server, no install, the original EPUB and background image are never modified (the new-version check is the one exception, and it can be turned off).

![Ebook Cover Maker window: details, design, preview, put it into an EPUB](docs/app.png)

![The four templates](docs/templates.png)

## Download

- **Executable**: grab `ebook-cover-maker.exe` from [Releases](https://github.com/microhan1/ebook-cover-maker/releases) and double-click it. Nothing to install. The exe is unsigned; if SmartScreen warns, choose "More info → Run anyway".
- **Run from source**:

```bash
pip install -r requirements.txt
python main.py
```

## Usage

1. Enter the title, author and publisher. While the publisher is empty the Save button is disabled and the reason is shown.
2. Pick a size preset, a template and a color scheme. The preview follows every change.
3. Press **Save** to write `cover.jpg` (`cover(2).jpg` if it exists). Press **Insert into EPUB** to get `<name>_cover.epub`.

Command line:

```bash
python main.py --title "Title" --author "Author" --publisher "Publisher" --preset upaper --template 1 --color 3 -o cover.jpg
python main.py --title "Title" --author "Author" --publisher "Publisher" --bg photo.jpg --template 4 --epub book.epub -y
```

`python main.py --help` prints the options in your OS language (한국어 · English · 中文 · 日本語).

## Upaper cover rules at a glance

| Item | Rule | This tool |
|---|---|---|
| Size | 600–1000 px wide (700×1000 recommended) | default preset 800×1200 (2:3) |
| Publisher | printed at the bottom of the cover | always inside the bottom 10%, verified on save |
| File format | JPG recommended, RGB | JPG quality 90 (PNG optional), RGB |
| Inside the EPUB | first page, one cover only | cover meta + first spine item; an SVG wrapper is replaced by a plain img |

## What it does not do

- No free-form editing (dragging text, layers). It is not Canva.
- No illustration or photo generation.
- No print covers (spine, back, bleed).
- No extra fonts. Only the three bundled ones (body, title, serif).
- It never downloads or replaces a newer version by itself.

## Checking for a new version

To see whether a newer version exists, the app sends only the tool's name and its version to the Chaekgalpi server (chaekgalpi.co.kr) when it starts. No files are sent, it checks at most once a day, and you can turn it off in the settings: untick "Check for updates" at the top right of the window. If a newer version exists, one line appears at the bottom of the window, and **Update** opens the Chaekgalpi tools page in your browser.

![New-version notice: one line at the bottom of the window](docs/update_notice.png)

## Fonts

The bundled fonts are Noto Sans CJK and Noto Serif CJK (SIL Open Font License 1.1). The license text is in `fonts/LICENSE-Sans.txt` and `fonts/LICENSE-Serif.txt`.

To keep the exe small (about 34 MB) it carries subsets, not the whole fonts: all 11,172 Hangul syllables, Latin, digits, symbols, kana, and the hanja/kanji/hanzi of KS X 1001, JIS X 0208 and GB 2312 plus the 5,401 common Big5 Traditional Chinese characters, with the Japanese and Simplified Chinese glyph shapes unchanged. A very rare character outside that range (for example 鱻) triggers the warning "Characters missing from the bundled fonts show as boxes". `tools/subset_fonts.py` makes the subsets from the originals in `fonts/`, and `tools/verify_fonts.py` checks that they draw exactly like the originals.

## Series

- Chaekgalpi Tools: [Scan PDF Cleanup](https://github.com/microhan1/scan-pdf-cleanup) · [Scan PDF Split](https://github.com/microhan1/scan-pdf-split) · [Trim PDF](https://github.com/microhan1/TrimPDF) · Ebook Renamer (coming soon)
- Browser version, no install: [Spread Split](https://microhan1.github.io/spread-split/)
- [Chaekgalpi Library](https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=github&utm_medium=referral&utm_campaign=tool_cta&utm_content=ebookcover) — a web service for keeping track of the books you read and your reading notes (tools page in English; the reading-log service itself is in Korean).

## License

MIT. See [LICENSE](LICENSE). Fonts are OFL (`fonts/`).

Third-party licenses are in [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
