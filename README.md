# TransVideo

视频翻译配音工具 —— 将任意语言的视频翻译为目标语言，并添加 AI 配音和字幕。

## 功能

- **多平台**：本地视频 / 抖音 / TikTok / YouTube / B站
- **多语言**：中 ⇄ 英，支持日/韩/法/德/西/俄等
- **语音识别**：OpenAI Whisper API（云端）或 faster-whisper（本地，模型按需下载）
- **翻译**：GPT / Ollama 本地模型 / Google / MyMemory，自动降级
- **配音**：edge-tts 免费（多语言多音色）或 Azure TTS
- **字幕**：黑底白字 YouTube 风格，ASS 格式，支持双语
- **智能断句**：ASR 过度切分时自动按句末标点合并，保证配音连贯
- **断点续跑**：中间结果自动保存

## 安装

```bash
git clone <repo-url> TransVideo
cd TransVideo
pip install -r requirements.txt
```

需要 ffmpeg 在 PATH 中，或在 `.env` 中指定路径。

## 配置

首次运行 `python cli.py` 会自动生成 `.env` 配置文件。也可手动创建：

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
```

所有配置项均可在交互模式的「修改配置」中可视化修改，无需手动编辑文件。

### 引擎选择

| 组件 | 有 API Key | 无 API Key |
|------|-----------|------------|
| ASR | Whisper API（快） | faster-whisper（本地，模型首次自动下载） |
| 翻译 | GPT-4o-mini | Ollama 本地 / Google 免费 |
| TTS | edge-tts（免费） | edge-tts（免费） |

### faster-whisper 模型

在 CLI 配置菜单中选择版本，首次使用时自动下载到 `.models/`：

| 模型 | 大小 | 精度 | 建议 |
|------|------|------|------|
| tiny | 39MB | 最低 | 快速预览 |
| base | 142MB | 一般 | 入门推荐 |
| small | 466MB | 较好 | 平衡选择 |
| medium | 1.5GB | 高 | 需 GPU |
| large-v3 | 3GB | 最高 | 强烈建议 GPU |

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
ASR_ENGINE=faster-whisper        # whisper-api / faster-whisper
TRANSLATE_ENGINE=openai           # openai / ollama / google / mymemory
TTS_ENGINE=edge                   # edge / azure

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

# 字幕（黑底白字）
SUBTITLE_STYLE=single             # single / dual
SUBTITLE_FONTSIZE=28
SUBTITLE_MARGIN_V=40

# 网络
NETWORK_PROXY=                    # http://127.0.0.1:7890
TIKTOK_COOKIES_BROWSER=           # chrome/edge/firefox
```

完整配置项见 `.env.example`。

## 项目结构

```
TransVideo/
├── cli.py                    # CLI 入口 + 交互式配置
├── pipeline.py               # 流水线编排
├── config.py                 # 配置管理（.env 读写）
├── .env                      # 配置文件（自动生成，gitignore）
├── .env.example              # 配置模板
├── run.bat                   # Windows 启动脚本
├── requirements.txt
├── modules/
│   ├── douyin_parser.py      # 抖音解析
│   ├── youtube_parser.py     # YouTube/TikTok 解析
│   ├── transcriber.py        # ASR（Whisper API / faster-whisper）
│   ├── translator.py         # 翻译（GPT/Google/MyMemory）
│   ├── ollama_translator.py  # 翻译（Ollama）
│   ├── tts_engine.py         # TTS（edge-tts / Azure）
│   ├── subtitle.py           # 字幕生成（ASS/SRT）
│   ├── segment_merger.py     # 智能断句合并
│   ├── video_composer.py     # 视频合成
│   └── dependency_check.py   # 依赖检查
├── .models/                  # ASR 模型（gitignore）
├── .work/                    # 中间文件（gitignore）
└── output/                   # 输出（gitignore）
```

## 常见问题

**Q: 没有 API Key 能用吗？**
A: 可以。ASR 用 faster-whisper 本地，翻译用 Ollama 或 Google，配音用 edge-tts。

**Q: faster-whisper 很慢？**
A: CPU 上较慢，建议用 Whisper API 或装 CUDA。medium/large-v3 强烈建议 GPU。

**Q: 抖音/TikTok 下载失败？**
A: 在 `.env` 中设置 `TIKTOK_COOKIES_BROWSER` 为你登录的浏览器。

**Q: YouTube 下载失败？**
A: 需要代理，在 `.env` 中设置 `NETWORK_PROXY`。

**Q: 如何使用第三方 OpenAI 兼容 API？**
A: 在 `.env` 中修改 `OPENAI_BASE_URL` 和 `TRANSLATE_MODEL`。

## License

MIT
