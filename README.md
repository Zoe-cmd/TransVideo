# TransVideo

视频翻译配音工具 —— 将任意语言的视频翻译为目标语言，并添加 AI 配音和字幕。

## 功能

- **多平台**：本地视频 / 抖音 / TikTok / YouTube / B站
- **多语言**：中 ⇄ 英，支持日/韩/法/德/西/俄等
- **语音识别**：OpenAI Whisper API（云端）或 faster-whisper（本地 GPU，模型按需下载）
- **翻译**：GPT / Ollama 本地模型 / Google / MyMemory，自动降级
- **配音**：
  - **edge-tts**（免费，多语言多音色，开箱即用）
  - **Azure TTS**（需 Key，质量更好）
  - **IndexTTS 2**（本地 GPU，零样本克隆原声 + 保留源音频情感）
- **字幕**：黑底白字 YouTube 风格，ASS 格式，支持双语
- **智能断句**：ASR 过度切分时自动按句末标点合并，保证配音连贯
- **断点续跑**：中间结果自动保存

## 快速开始

### Windows 一键启动（推荐）

```bash
git clone <repo-url> TransVideo
cd TransVideo
# 双击 run.bat，或：
run.bat
```

`run.bat` 首次运行会自动创建 `.venv` 虚拟环境并安装依赖（进度实时可见），之后直接进入交互式命令行。无需手动配置 Python 环境。

### 手动安装

```bash
git clone <repo-url> TransVideo
cd TransVideo
pip install -r requirements.txt
python cli.py
```

需要 ffmpeg 在 PATH 中，或在 `.env` 中指定路径（`FFMPEG_PATH` / `FFPROBE_PATH`）。

## 配置

首次运行自动生成 `.env` 配置文件。所有配置项均可在交互模式的「修改配置」中可视化修改，无需手动编辑文件。

```bash
cp .env.example .env   # 或手动创建
```

### 引擎选择

| 组件 | 有 API Key | 无 API Key |
|------|-----------|------------|
| ASR | Whisper API（快） | faster-whisper（本地，模型首次自动下载） |
| 翻译 | GPT 兼容接口 | Ollama 本地 / Google 免费 |
| TTS | edge-tts（免费）/ Azure / IndexTTS 2 | edge-tts（免费）/ IndexTTS 2 |

### faster-whisper 模型

在 CLI 配置菜单中选择版本，**选择后立即提示下载**（进度条实时可见），模型保存到 `.models/`：

| 模型 | 大小 | 精度 | 建议 |
|------|------|------|------|
| tiny | 39MB | 最低 | 快速预览 |
| base | 142MB | 一般 | 入门推荐 |
| small | 466MB | 较好 | 平衡选择 |
| medium | 1.5GB | 高 | 需 GPU |
| large-v3 | 3GB | 最高 | 强烈建议 GPU |

### IndexTTS 2（本地 GPU 配音，克隆原声）

设置 `TTS_ENGINE=index` 即可使用 [IndexTTS 2](https://github.com/index-tts/index-tts)：零样本克隆视频中说话人的音色，并保留源音频的情感。

**自动安装（推荐）**：交互模式「修改配置 → TTS 配音配置 → 安装 / 检测 IndexTTS 环境」一键安装；切换到 index 引擎时也会自动检测并提示。也可手动运行：

```bash
python scripts/setup_indextts.py
```

脚本自动完成（幂等，可重复运行，进度实时显示）：

1. 克隆 index-tts 仓库到 `index-tts/`
2. `uv sync` 安装依赖（独立虚拟环境，Python 3.11 + CUDA torch，与主程序完全隔离）
3. 下载 IndexTTS-2 模型权重（约 5.5GB）

**下载源自动回退**：默认使用 `.env` 中 `NETWORK_PROXY` 配置的代理；无代理或失败时自动切换国内镜像（阿里云 PyPI / pytorch-wheels、ModelScope、hf-mirror）；全部失败则提示配置代理。

**环境要求**：

- NVIDIA GPU（建议 ≥8GB 显存，默认开启 FP16）
- [uv](https://docs.astral.sh/uv/)（缺失时脚本自动安装）
- Windows 上跳过 DeepSpeed / flash-attn（官方也不支持），不影响使用

**工作原理**：

- 主程序通过子进程（`index_tts_worker.py`）调用 index-tts 独立虚拟环境，批量合成所有片段，模型只加载一次
- 默认逐片段用原声切片做音色 + 情感参考（`spk_audio_prompt` + `emo_audio_prompt`）；也可用 `INDEX_TTS_REF_AUDIO` 指定固定参考音频
- **中文路径兼容**：项目路径含中文等非 ASCII 字符时，虚拟环境自动建到外部纯 ASCII 目录（`%LOCALAPPDATA%\TransVideo\indextts-venv`），模型目录自动创建 ASCII junction——规避 sentencepiece/kaldifst 等 C++ 扩展的中文路径崩溃

## 使用

```bash
# 交互模式（推荐）
python cli.py

# 翻译本地视频
python cli.py video input.mp4 -t en

# 翻译抖音视频
python cli.py douyin "https://v.douyin.com/xxxxx/" -t en

# 翻译 YouTube 视频（需代理）
python cli.py youtube "https://youtube.com/watch?v=xxx" -t zh

# 使用 IndexTTS 2 配音（克隆原声）
python cli.py video input.mp4 -t zh --tts-engine index

# 仅生成字幕
python cli.py video input.mp4 -t en --subtitle-only

# 双语字幕
python cli.py video input.mp4 -t en --subtitle-style dual

# 保留原音频
python cli.py video input.mp4 -t en --keep-original-audio

# 查看配置
python cli.py config
```

## .env 配置项

```env
# 引擎
ASR_ENGINE=faster-whisper         # whisper-api / faster-whisper
TRANSLATE_ENGINE=openai           # openai / ollama / google / mymemory
TTS_ENGINE=edge                   # edge / azure / index

# OpenAI
OPENAI_API_KEY=                   # 你的 API Key
OPENAI_BASE_URL=https://api.openai.com/v1
TRANSLATE_MODEL=gpt-4o-mini
TRANSLATE_MODEL_FALLBACKS=gpt-4o-mini,gpt-3.5-turbo,gpt-4o

# faster-whisper 模型
FASTER_WHISPER_MODEL=base         # tiny/base/small/medium/large-v3

# TTS 音色
TTS_VOICE_ZH=zh-CN-YunxiNeural
TTS_VOICE_EN=en-US-GuyNeural

# IndexTTS 2（仅 TTS_ENGINE=index 时需要）
INDEX_TTS_MODEL_DIR=index-tts/checkpoints
INDEX_TTS_REF_AUDIO=              # 固定参考音频（留空则逐片段克隆原声）
INDEX_TTS_USE_FP16=true
INDEX_TTS_USE_DEEPSPEED=false
INDEX_TTS_USE_ACCEL=false

# 字幕（黑底白字）
SUBTITLE_STYLE=single             # single / dual
SUBTITLE_FONTSIZE=28
SUBTITLE_MARGIN_V=40

# 网络
NETWORK_PROXY=                    # http://127.0.0.1:7890，下载模型/依赖时默认使用
TIKTOK_COOKIES_BROWSER=           # chrome/edge/firefox
```

完整配置项见 `.env.example`。

## 项目结构

```
TransVideo/
├── cli.py                    # CLI 入口 + 交互式配置
├── pipeline.py               # 流水线编排
├── config.py                 # 配置管理（.env 读写）
├── index_tts_worker.py       # IndexTTS 2 子进程桥接（在 index-tts venv 中运行）
├── scripts/
│   └── setup_indextts.py     # IndexTTS 2 环境自动安装（带回退链）
├── .env                      # 配置文件（自动生成，gitignore）
├── .env.example              # 配置模板
├── run.bat                   # Windows 一键启动（自动建 venv + 装依赖）
├── requirements.txt
├── modules/
│   ├── douyin_parser.py      # 抖音解析
│   ├── youtube_parser.py     # YouTube/TikTok 解析
│   ├── transcriber.py        # ASR（Whisper API / faster-whisper，下载带回退链）
│   ├── translator.py         # 翻译（GPT/Google/MyMemory）
│   ├── ollama_translator.py  # 翻译（Ollama）
│   ├── tts_engine.py         # TTS（edge-tts / Azure / IndexTTS 2）
│   ├── indextts_setup.py     # IndexTTS 环境状态检查 / 路径规划 / 安装入口
│   ├── subtitle.py           # 字幕生成（ASS/SRT）
│   ├── segment_merger.py     # 智能断句合并
│   ├── video_composer.py     # 视频合成
│   └── dependency_check.py   # 依赖检查
├── index-tts/                # IndexTTS 2 仓库（gitignore，自动安装）
├── .models/                  # ASR 模型（gitignore）
├── .work/                    # 中间文件（gitignore）
└── output/                   # 输出（gitignore）
```

## 常见问题

**Q: 没有 API Key 能用吗？**
A: 可以。ASR 用 faster-whisper 本地，翻译用 Ollama 或 Google，配音用 edge-tts。

**Q: 模型/依赖下载很慢或失败？**
A: 所有下载都内置回退链：优先用 `.env` 中 `NETWORK_PROXY` 配置的代理 → 直连官方源 → 国内镜像（阿里云 / ModelScope / hf-mirror）。建议配置代理后重试。

**Q: faster-whisper 很慢？**
A: CPU 上较慢，建议用 Whisper API 或装 CUDA。medium/large-v3 强烈建议 GPU。

**Q: IndexTTS 2 安装失败？**
A: 重跑 `python scripts/setup_indextts.py`（幂等，已完成的步骤自动跳过）。torch 下载约 3.2GB、模型权重约 5.5GB，请保持网络畅通。

**Q: 项目放在中文路径下 IndexTTS 报错？**
A: 已内置兼容：虚拟环境和模型访问路径会自动转为纯 ASCII（venv 外置 + junction）。无需移动项目目录。

**Q: run.bat 闪退？**
A: 批处理文件必须保持纯英文内容 + CRLF 换行。如自行修改过请检查这两点。

**Q: 抖音/TikTok 下载失败？**
A: 在 `.env` 中设置 `TIKTOK_COOKIES_BROWSER` 为你登录的浏览器。

**Q: YouTube 下载失败？**
A: 需要代理，在 `.env` 中设置 `NETWORK_PROXY`。

**Q: 如何使用第三方 OpenAI 兼容 API？**
A: 在 `.env` 中修改 `OPENAI_BASE_URL` 和 `TRANSLATE_MODEL`。

## License

MIT
