# 书签工具 – 电子书封面制作

[한국어](README.md) · [English](README.en.md) · [日本語](README.ja.md)

只需输入书名、作者、出版社名，即可生成符合自出版平台审核规格（宽 600~1000px，底部出版社名，upaper.net 要求）的电子书封面图片。这不是设计工具，而是为了通过审核的封面生成器。无服务器，无需安装，绝不修改原 EPUB 和背景图片（仅“检查新版本”除外，可关闭）。

![电子书封面制作窗口：输入、设计、预览、放入EPUB](docs/app.png)

![四种模板](docs/templates.png)

## 下载

- **可执行文件**：在 [Releases](https://github.com/microhan1/ebook-cover-maker/releases) 下载 `ebook-cover-maker.exe`，双击即可运行，无需安装。未签名，若 SmartScreen 提示，请选择"更多信息 → 仍要运行"。
- **从源码运行**：

```bash
pip install -r requirements.txt
python main.py
```

## 使用方法

1. 输入书名、作者、出版社名。出版社名为空时保存按钮会锁定并显示原因。
2. 选择尺寸规格、模板和配色。预览会立即更新。
3. 按 **保存** 生成 `cover.jpg`（已存在时为 `cover(2).jpg`）。按 **插入EPUB** 生成 `<原名>_cover.epub`。

命令行：

```bash
python main.py --title "书名" --author "作者" --publisher "出版社" --preset upaper --template 1 --color 3 -o cover.jpg
python main.py --title "书名" --author "作者" --publisher "出版社" --bg photo.jpg --template 4 --epub book.epub -y
```

`python main.py --help` 以系统语言（한국어 · English · 中文 · 日本語）显示选项。

## Upaper 封面规格摘要

| 项目 | 规格 | 本工具 |
|---|---|---|
| 尺寸 | 宽 600~1000px（推荐 700×1000） | 默认预设 800×1200（2:3） |
| 出版社名 | 标注在封面底部 | 始终位于底部 10% 区域内，保存时校验 |
| 文件格式 | 推荐 JPG，RGB | JPG 质量 90（可选 PNG），RGB |
| EPUB 内位置 | 第一页，仅一个封面 | 注册 cover 元数据 + spine 首项，SVG 包装替换为 img |

## 不做的事

- 没有自由排版编辑（拖动文字、图层）。这不是 Canva。
- 不生成插画或照片。
- 不制作纸质书封面（书脊、封底、出血线）。
- 不支持添加字体。仅使用内置的 3 种字体（正文体、标题体、宋体）。
- 不会自动下载或替换新版本。

## 检查新版本

启动时，为了检查是否有新版本，程序只会把工具名称和版本号发送到书签服务器（chaekgalpi.co.kr）。不会发送任何文件，每天最多检查一次，并且可以在设置中关闭：取消窗口右上角的“检查新版本”勾选即可。有新版本时，窗口下方会出现一行提示，点击“更新”会在浏览器中打开书签工具页面。

![新版本提示：窗口下方的一行](docs/update_notice.png)

## 反馈

遇到问题、不方便之处或想要的功能，可以点击窗口右上角的“发送反馈”，或在[书签工具页面](https://chaekgalpi.co.kr/en/tools/ebookcover#feedback)免登录提交。也继续接受 [GitHub Issues](https://github.com/microhan1/ebook-cover-maker/issues)。“发送反馈”只会在浏览器中打开该页面，程序本身不发送任何内容。

## 字体

内置字体为 Noto Sans CJK 与 Noto Serif CJK（SIL Open Font License 1.1）。许可证原文见 `fonts/LICENSE-Sans.txt`、`fonts/LICENSE-Serif.txt`。

为了让可执行文件更小（约 34MB），程序内置的是字体子集而不是完整字体：全部 11,172 个韩文音节、拉丁字母、数字、符号、假名，以及 KS X 1001、JIS X 0208、GB 2312 的汉字和 Big5 常用繁体字（5,401 个），日文与简体中文的字形保持不变。超出此范围的极罕见字符（例如 鱻）会出现“内置字体中没有的字符将显示为□”的提示。子集由 `tools/subset_fonts.py` 从 `fonts/` 中的原字体生成，`tools/verify_fonts.py` 用来确认它们与原字体的绘制结果完全一致。

## 系列

- 书签工具：[扫描PDF清晰化](https://github.com/microhan1/scan-pdf-cleanup) · [双页拆分](https://github.com/microhan1/scan-pdf-split) · [裁边](https://github.com/microhan1/TrimPDF) · 电子书文件名整理（即将公开）
- 无需安装的网页版：[跨页拆分](https://microhan1.github.io/spread-split/)
- [书签库](https://chaekgalpi.co.kr/tools/ebookcover?utm_source=github&utm_medium=referral&utm_campaign=tool_cta&utm_content=ebookcover) — 记录读过的书和读书笔记的网络服务。

## 许可证

MIT，见 [LICENSE](LICENSE)。字体为 OFL（`fonts/`）。

第三方许可证见 [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt)。
