# しおりツール – 電子書籍表紙メーカー

[한국어](README.md) · [English](README.en.md) · [中文](README.zh-CN.md)

タイトル・著者・出版社名を入力するだけで、セルフ出版プラットフォームの審査規格（幅 600〜1000px、下部に出版社名。upaper.net の要件）に合う電子書籍の表紙画像を作ります。デザインツールではなく、規格を通すための表紙ジェネレーターです。サーバー不要、インストール不要、元の EPUB と背景画像は一切変更しません（新バージョンの確認のみ例外で、オフにできます）。

![電子書籍表紙メーカーの画面：入力、デザイン、プレビュー、EPUBに入れる](docs/app.png)

![4つのテンプレート](docs/templates.png)

## ダウンロード

- **実行ファイル**: [Releases](https://github.com/microhan1/ebook-cover-maker/releases) から `ebook-cover-maker.exe` を取得してダブルクリック。インストールは不要です。未署名のため SmartScreen の警告が出たら「詳細情報 → 実行」を選んでください。
- **ソースから実行**:

```bash
pip install -r requirements.txt
python main.py
```

## 使い方

1. タイトル・著者・出版社名を入力します。出版社名が空のあいだは保存ボタンが無効になり、理由が表示されます。
2. サイズ規格・テンプレート・配色を選びます。プレビューがすぐに更新されます。
3. **保存**を押すと `cover.jpg` ができます（既にあれば `cover(2).jpg`）。**EPUBに挿入**を押すと `<元の名前>_cover.epub` が新しく作られます。

コマンドラインでも使えます。

```bash
python main.py --title "タイトル" --author "著者" --publisher "出版社" --preset upaper --template 1 --color 3 -o cover.jpg
python main.py --title "タイトル" --author "著者" --publisher "出版社" --bg photo.jpg --template 4 --epub book.epub -y
```

`python main.py --help` は OS の言語（한국어 · English · 中文 · 日本語）でオプションを表示します。

## Upaper 表紙規格のまとめ

| 項目 | 規格 | このツール |
|---|---|---|
| サイズ | 幅 600〜1000px（推奨 700×1000） | 既定プリセット 800×1200（2:3） |
| 出版社名 | 表紙の下部に表記 | 常に下部 10% の領域内に描画し、保存時に確認 |
| ファイル形式 | JPG 推奨、RGB | JPG 品質 90（PNG も選択可）、RGB |
| EPUB 内の位置 | 最初のページ、表紙は1つだけ | cover メタ登録 + spine の先頭、SVG ラッパーは img に置き換え |

## しないこと

- 自由配置の編集（文字のドラッグ、レイヤー）はありません。Canva ではありません。
- イラストや写真の生成はしません。
- 紙の本の表紙（背表紙・裏表紙・トンボ）は作りません。
- フォントの追加はできません。内蔵の3種（本文体・見出し体・明朝体）のみです。
- 新しいバージョンを自動でダウンロードしたり入れ替えたりしません。

## 新しいバージョンの確認

起動時に新しいバージョンがあるか確認するため、ツール名とバージョンだけをしおりサーバー（chaekgalpi.co.kr）に送ります。ファイルは送らず、確認は1日1回だけで、設定でオフにできます。ウィンドウ右上の「新しいバージョンを確認」のチェックを外してください。新しいバージョンがあると、ウィンドウの下に1行で知らせ、「更新」を押すとしおりツールのページがブラウザで開きます。

![新バージョンのお知らせ：ウィンドウ下の1行](docs/update_notice.png)

## ご意見

不具合・使いにくい点・ほしい機能は、ウィンドウ右上の「ご意見を送る」または[しおりツールのページ](https://chaekgalpi.co.kr/en/tools/ebookcover#feedback)からログインなしで送れます。[GitHub Issues](https://github.com/microhan1/ebook-cover-maker/issues) も引き続き受け付けています。「ご意見を送る」はそのページをブラウザで開くだけで、プログラム自身は何も送信しません。

## フォント

内蔵フォントは Noto Sans CJK と Noto Serif CJK（SIL Open Font License 1.1）です。ライセンス原文は `fonts/LICENSE-Sans.txt`、`fonts/LICENSE-Serif.txt` にあります。

実行ファイルを小さくするため（約34MB）、フォント全体ではなくサブセットを入れています。ハングル音節11,172字すべて、ラテン文字・数字・記号・かな、KS X 1001・JIS X 0208・GB 2312 の漢字と Big5 の常用繁体字（5,401字）が入っており、日本語・簡体字中国語の字形もそのままです。この範囲外のごくまれな文字（例：鱻）には「内蔵フォントにない文字は□で表示されます」という警告が出ます。サブセットは `tools/subset_fonts.py` で `fonts/` の原本から作り、`tools/verify_fonts.py` で原本と同じ描画になることを確認します。

## シリーズ

- しおりツール: [スキャンPDF補正](https://github.com/microhan1/scan-pdf-cleanup) · [見開き分割](https://github.com/microhan1/scan-pdf-split) · [余白カット](https://github.com/microhan1/TrimPDF) · 電子書籍ファイル名整理（近日公開）
- インストール不要のブラウザ版: [見開き分割](https://microhan1.github.io/spread-split/)
- [しおりライブラリ](https://chaekgalpi.co.kr/en/tools/ebookcover?utm_source=github&utm_medium=referral&utm_campaign=tool_cta&utm_content=ebookcover) — 読んだ本と読書メモを記録するウェブサービス。

## ライセンス

MIT。[LICENSE](LICENSE) を参照。フォントは OFL（`fonts/`）。

サードパーティのライセンスは [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) にあります。
