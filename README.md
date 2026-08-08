# TransVideo

**一句话介绍**：丢给它一个视频（本地文件 / 抖音 / TikTok / YouTube / B站链接），它自动完成 **听写 → 翻译 → AI 配音 → 烧录字幕**，输出一个配好音、带字幕的新视频。

比如你下载了一个英文教程视频，运行一条命令，几分钟后就能得到一个**中文配音 + 中文字幕**的版本——配音甚至可以用 IndexTTS 2 克隆原视频说话人的声音和情绪，听起来就像原作者本人在说中文。

---

## 目录

- [效果流程](#效果流程)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [第一次使用：三分钟上手](#第一次使用三分钟上手)
- [交互界面详解](#交互界面详解)
- [命令行用法](#命令行用法)
- [引擎怎么选](#引擎怎么选)
- [IndexTTS 2 克隆配音](#indextts-2-克隆配音)
- [输出产物说明](#输出产物说明)
- [支持的平台与语言](#支持的平台与语言)
- [常见问题 FAQ](#常见问题-faq)

## 效果流程

```
输入视频 ──► 提取音频 ──► Whisper 听写原文 ──► AI 翻译 ──► 生成字幕
                                                    │
                                                    ▼
              输出视频 ◄── 合成（字幕+新音轨）◄── 对齐混音 ◄── AI 配音
```

- 配音时长自动与原片段对齐（超长的自动变速，不会音画错位）
- 语音识别断句太碎时自动按标点合并，翻译和配音都更连贯
- 每一步的中间结果都会保存，中断后可以从断点继续

## 功能特性

| 能力 | 说明 |
|------|------|
| 多平台 | 本地视频文件、抖音分享链接、TikTok、YouTube、B站等 |
| 多语言 | 中 ⇄ 英互译为主，支持日 / 韩 / 法 / 德 / 西 / 俄等 |
| 语音识别 | OpenAI Whisper API（云端快）或 faster-whisper（本地 GPU 免费） |
| 翻译 | GPT 兼容接口 / Ollama 本地大模型 / Google / MyMemory，失败自动降级 |
| 配音 | edge-tts（免费）/ Azure TTS / **IndexTTS 2（克隆原声+保留情感）** |
| 字幕 | 黑底白字 YouTube 风格，支持单语 / 双语，字号边距可调 |
| 音轨 | 可保留原声作背景（音量可调），也可完全替换 |
| GPU 加速 | ASR、IndexTTS、视频编码（NVENC）全部支持 GPU |

**没有 API Key 也能用**：自动降级到全免费方案（faster-whisper 本地识别 + Google/MyMemory 翻译 + edge-tts 配音）。

## 快速开始

### 方式一：Windows 一键启动（推荐）

```bash
git clone https://github.com/Zoe-cmd/TransVideo.git
cd TransVideo
```

然后**双击 `run.bat`**。首次运行会自动：

1. 创建 `.venv` 虚拟环境（不污染系统 Python）
2. 安装全部依赖（pip 进度实时可见）
3. 生成 `.env` 配置文件
4. 进入交互式命令行界面

以后每次双击 `run.bat` 直接进入界面，无需任何操作。

### 方式二：手动安装

```bash
git clone https://github.com/Zoe-cmd/TransVideo.git
cd TransVideo
pip install -r requirements.txt
python cli.py
```

### 前置要求

- **Python 3.10+**（已加入 PATH）
- **ffmpeg**：把它放进 PATH，或者在 `.env` 里指定完整路径：
  ```env
  FFMPEG_PATH=D:\Program Files\ffmpeg\bin\ffmpeg.exe
  FFPROBE_PATH=D:\Program Files\ffmpeg\bin\ffprobe.exe
  ```
- **NVIDIA GPU（可选但强烈推荐）**：没有 GPU 也能跑，只是 ASR 和 IndexTTS 会慢很多

## 第一次使用：三分钟上手

**第 1 步：启动**

双击 `run.bat`（或 `python cli.py`），看到主菜单。

**第 2 步：配置（可选）**

主菜单选「修改配置」：

- 有 OpenAI 兼容 API Key → 填入 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`，翻译质量最好
- 没有 Key → 什么都不用填，自动用免费方案
- 在国内下载 YouTube / HuggingFace 模型 → 填 `NETWORK_PROXY`（如 `http://127.0.0.1:7897`）

**第 3 步：跑一个视频**

主菜单选「📁 翻译本地视频文件」，输入视频路径和目标语言（如 `zh`），剩下的交给它：

```
步骤 1/7: 获取视频
步骤 2/7: 提取音频
步骤 3/7: 语音识别 (ASR)      ← 首次使用自动下载 whisper 模型（有进度条）
步骤 4/7: 翻译
步骤 5/7: 生成字幕
步骤 6/7: TTS 配音
步骤 7/7: 混合音频 + 合成视频
```

**第 4 步：拿结果**

完成后在 `output\日期时间\` 目录下找到：`视频_zh.mp4`（成品）、字幕文件、原文/译文案。

> 想克隆原声配音？见下文 [IndexTTS 2 克隆配音](#indextts-2-克隆配音)。

## 交互界面详解

主菜单各选项：

| 菜单项 | 作用 |
|--------|------|
| 🌐 翻译抖音 / TikTok 视频 | 粘贴分享链接（支持抖音口令），自动解析下载 |
| ▶️ 翻译 YouTube / B站等流媒体视频 | 粘贴链接（国内需先在配置里设代理） |
| 📁 翻译本地视频文件 | 选择电脑上的视频文件开始处理 |
| 📝 仅生成字幕（不配音） | 只走 听写→翻译→烧字幕，不做配音 |
| ⚙️ 修改配置 | 所有参数的可视化配置，见下表 |
| 🧹 清理缓存 | 删除 `.work` 下的中间产物（释放磁盘） |
| 🚪 退出 | — |

「⚙️ 修改配置」里的子菜单：

| 配置项 | 说明 |
|--------|------|
| 更改 ASR 引擎 / Whisper 模型 | 切换 whisper-api / faster-whisper；选模型版本后**立即提示下载**，进度条实时可见 |
| 更改翻译引擎 / Ollama 模型 | 切换 openai / ollama / google / mymemory |
| 更改 TTS 引擎 / 音色 | 切换 edge / azure / index、选各语言音色、调语速音量、**安装/检测 IndexTTS 环境** |
| 更改字幕设置 | 单语/双语、字号、字体、边距、换行宽度、颜色 |
| OpenAI 配置 | API Key、Base URL、翻译模型及备选模型链、Whisper API 模型 |
| 设置网络代理 | YouTube / HuggingFace 下载必需 |
| 设置 TikTok cookies 浏览器 | 借用浏览器 cookies 绕过反爬 |
| 查看完整配置文件 | 打开 `.env` 内容 |

所有修改自动保存到 `.env`，下次启动继续生效。

## 命令行用法

熟悉之后可以跳过界面直接用命令：

```bash
# 本地视频 → 中文
python cli.py video input.mp4 -t zh

# 抖音链接 → 英文
python cli.py douyin "https://v.douyin.com/xxxxx/" -t en

# YouTube → 中文（需代理）
python cli.py youtube "https://youtube.com/watch?v=xxx" -t zh

# IndexTTS 2 克隆配音
python cli.py video input.mp4 -t zh --tts-engine index

# 只要字幕，不要配音
python cli.py video input.mp4 -t en --subtitle-only

# 双语字幕（原文+译文）
python cli.py video input.mp4 -t en --subtitle-style dual

# 保留原声当背景（音量默认 15%）
python cli.py video input.mp4 -t zh --keep-original-audio

# 断点续跑（复用已完成的 ASR/翻译结果）
python cli.py video input.mp4 -t zh --skip-download --skip-asr --skip-translate
```

常用参数：

| 参数 | 说明 |
|------|------|
| `-t, --target-lang` | 目标语言：`zh` `en` `ja` `ko` `fr` `de` `es` `ru` 等 |
| `-s, --source-lang` | 源语言（默认 `auto` 自动检测） |
| `--asr-engine` | `whisper-api` / `faster-whisper` |
| `--translate-engine` | `openai` / `ollama` / `google` / `mymemory` |
| `--tts-engine` | `edge` / `azure` / `index` |
| `--faster-whisper-model` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `--subtitle-style` | `single`（仅译文）/ `dual`（双语） |
| `--subtitle-only` | 只烧字幕不配音 |
| `--keep-original-audio` | 保留原声作背景 |

## 引擎怎么选

### ASR（语音识别）

| 引擎 | 优点 | 缺点 | 适合 |
|------|------|------|------|
| whisper-api | 快、不占本地资源 | 要 Key、按量计费 | 有 Key 且追求速度 |
| faster-whisper | 免费、离线、GPU 加速 | 首次要下载模型 | 大多数人 |

faster-whisper 模型版本选择（配置菜单里选完即下载）：

| 模型 | 大小 | 精度 | 建议 |
|------|------|------|------|
| tiny | 39MB | 最低 | 快速预览 |
| base | 142MB | 一般 | 入门 |
| small | 466MB | 较好 | 平衡之选 |
| medium | 1.5GB | 高 | 建议 GPU |
| large-v3 | 3GB | 最高 | 强烈建议 GPU |

### 翻译

| 引擎 | 说明 |
|------|------|
| openai | 任何 OpenAI 兼容接口（官方 / 中转站都行），支持备选模型链自动切换 |
| ollama | 本地大模型（如 qwen2.5），免费离线，需先装 Ollama |
| google | 免费，无需配置，质量一般 |
| mymemory | 免费兜底，有每日额度限制 |

翻译失败会自动沿引擎链降级，不会中断流程。

### TTS（配音）

| 引擎 | 声音 | 成本 | 特点 |
|------|------|------|------|
| edge | 微软在线多音色 | 免费 | 开箱即用，稳定 |
| azure | 微软官方音色 | 需 Key | 质量更好更稳 |
| index | **克隆原视频说话人** | 免费（吃 GPU） | 保留情感和语气，最像原片 |

## IndexTTS 2 克隆配音

这是体验最好的配音方式：逐片段提取原视频中说话人的声音作为参考，生成的配音**音色像原作者本人、情绪语气也和原片段一致**，而不是千篇一律的播音腔。

### 安装（一键）

交互界面：「修改配置 → TTS 配音配置 → 安装 / 检测 IndexTTS 环境」，或在切换 TTS 引擎到 `index` 时按提示自动安装。也可以手动跑：

```bash
python scripts/setup_indextts.py
```

脚本自动完成（已装的部分自动跳过，可反复运行）：

1. 克隆 index-tts 官方仓库
2. 用 [uv](https://docs.astral.sh/uv/) 创建独立环境并安装依赖（含 CUDA 版 PyTorch，约 3.2GB）
3. 下载 IndexTTS-2 模型权重（约 5.5GB）

**下载慢 / 失败不用慌**：所有下载都有自动回退——优先用你配置的代理，不行换直连，再不行自动切国内镜像（阿里云 / ModelScope / hf-mirror），全都不行会明确提示你配置代理。

### 要求

- NVIDIA GPU，建议 ≥8GB 显存（默认开 FP16 省显存）
- 首次安装约 8GB 下载量，之后完全离线可用
- 主程序和 index-tts 是**两个独立 Python 环境**，互不污染

### 使用

```bash
python cli.py video input.mp4 -t zh --tts-engine index
```

或在交互界面把 TTS 引擎切到 `index`（会自动保存到 `.env`）。

进阶配置（`.env`）：

```env
INDEX_TTS_REF_AUDIO=        # 指定一段 3-15 秒清晰人声做固定音色参考；留空=逐片段克隆原声（推荐）
INDEX_TTS_USE_FP16=true     # 半精度，省显存更快
```

### 中文路径说明

项目放在中文目录下也能正常用——安装脚本检测到非 ASCII 路径时，会自动把 index-tts 环境建到 `%LOCALAPPDATA%\TransVideo\` 下的纯 ASCII 目录并做路径映射，无需你移动项目。

## 输出产物说明

每次运行在 `output\YYYYMMDD_HHMMSS\` 下生成一个独立目录：

| 文件 | 内容 |
|------|------|
| `视频名_语言.mp4` | **成品视频**（配音 + 烧录字幕） |
| `视频名_语言.ass` / `.srt` | 字幕文件（可导入剪辑软件） |
| `视频名_原文.txt` | 识别出的原文（带时间戳） |
| `视频名_译文_语言.txt` | 翻译结果（带时间戳） |
| `视频名_文案.md` | 完整文案（视频信息 + 原文 + 译文） |
| `视频名_segments.json` | 全部片段数据（时间戳/原文/译文） |

中间产物在 `.work\` 下，可用主菜单「🧹 清理缓存」一键释放磁盘。

## 支持的平台与语言

**视频来源**：本地文件 / 抖音（含口令）/ TikTok / YouTube / B站及大部分 yt-dlp 支持的站点。

**目标语言**：`zh` 中文、`en` 英语、`ja` 日语、`ko` 韩语、`fr` 法语、`de` 德语、`es` 西语、`ru` 俄语等（edge-tts 覆盖的语言均可配音）。

## 常见问题 FAQ

**Q: 完全没有任何 API Key，能用吗？**
A: 能。默认就是免费方案：faster-whisper 本地识别 + Google/MyMemory 翻译 + edge-tts 配音。填了 OpenAI 兼容 Key 后翻译质量会更好。

**Q: 模型下载慢、卡住、失败？**
A: 所有下载（whisper 模型 / IndexTTS 依赖 / IndexTTS 权重）都内置回退链：代理 → 官方直连 → 国内镜像。建议在 `.env` 配置 `NETWORK_PROXY` 后重试；下载任务断点续传，重跑不会从零开始。

**Q: IndexTTS 2 安装失败怎么办？**
A: 直接重跑 `python scripts/setup_indextts.py`。它是幂等的，已完成的步骤自动跳过，失败的部分自动换下载源重试。

**Q: IndexTTS 2 跑起来很慢 / 爆显存？**
A: 确认 `INDEX_TTS_USE_FP16=true`；16GB 显存的 4060 Ti 合成一条 5 秒配音约 3-7 秒。显存不足可关闭其他占显存的程序。

**Q: run.bat 双击闪退？**
A: 请用仓库里的原版。如果改过一次：批处理文件**不能含中文**（除非存成 GBK 编码）且**必须是 CRLF 换行**，否则 cmd 解析会直接退出。

**Q: 界面中文乱码？**
A: 不影响功能。`run.bat` 已设置 UTF-8；手动运行时建议用 Windows Terminal 或在 cmd 先执行 `chcp 65001`。

**Q: 抖音/TikTok 解析失败？**
A: TikTok 反爬严格：先在浏览器登录 tiktok.com，然后在 `.env` 设置 `TIKTOK_COOKIES_BROWSER=edge`（或 chrome/firefox），程序会自动借用浏览器 cookies。

**Q: YouTube 下载失败？**
A: 国内需要代理：`.env` 里设置 `NETWORK_PROXY=http://127.0.0.1:端口`。

**Q: 想用第三方 OpenAI 中转站？**
A: `.env` 里改 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，`TRANSLATE_MODEL` 填中转站支持的模型名，还可以用 `TRANSLATE_MODEL_FALLBACKS` 配一串备选模型自动切换。

**Q: 项目目录结构是怎样的？**

```
TransVideo/
├── cli.py                    # CLI 入口 + 交互式配置
├── pipeline.py               # 流水线编排
├── config.py                 # 配置管理（.env 读写）
├── index_tts_worker.py       # IndexTTS 2 子进程桥接
├── scripts/setup_indextts.py # IndexTTS 2 一键安装脚本
├── run.bat                   # Windows 一键启动
├── modules/                  # 下载/ASR/翻译/TTS/字幕/合成各模块
├── index-tts/                # IndexTTS 2 仓库（自动安装，不随项目分发）
├── .models/                  # ASR 模型（自动下载）
├── .work/                    # 中间文件（可清理）
└── output/                   # 输出成品
```

## License

MIT
