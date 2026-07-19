# -*- coding: utf-8 -*-
"""TransVideo 配置管理 —— 支持 .env 配置文件 + 环境变量 + 默认值三级覆盖

配置优先级（高→低）：
  1. CLI 参数（运行时覆盖）
  2. 环境变量
  3. 配置文件 .env
  4. 内置默认值

.env 格式示例：
  # 引擎选择
  ASR_ENGINE=faster-whisper
  TRANSLATE_ENGINE=openai
  TTS_ENGINE=edge

  # OpenAI
  OPENAI_API_KEY=sk-xxxxx
  OPENAI_BASE_URL=https://api.openai.com/v1
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


# ============ 默认值 ============

def _find_ffmpeg() -> str:
    """定位完整版 ffmpeg"""
    candidate = r"D:\Program Files\ffmpeg\bin\ffmpeg.exe"
    if os.path.isfile(candidate):
        return candidate
    import shutil
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or "ffmpeg"


def _find_ffprobe() -> str:
    """定位 ffprobe"""
    candidate = r"D:\Program Files\ffmpeg\bin\ffprobe.exe"
    if os.path.isfile(candidate):
        return candidate
    import shutil
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe") or "ffprobe"


CONFIG_FILE_NAME = ".env"

# .env 配置文件模板（首次运行时自动生成）
ENV_TEMPLATE = """# =============================================================================
# TransVideo 配置文件
# =============================================================================
# 修改本文件即可自定义所有引擎参数
# 无 API Key 也会自动降级到免费方案（faster-whisper + MyMemory + edge-tts）
# =============================================================================

# ----- 引擎选择 -----
# ASR: whisper-api (云端, 需Key) / faster-whisper (本地免费, 首次下载模型)
ASR_ENGINE=faster-whisper
# 翻译: openai (GPT, 需Key) / ollama (本地免费) / google (免费) / mymemory (免费)
TRANSLATE_ENGINE=openai
# TTS: edge (免费) / azure (需Key, 质量更好)
TTS_ENGINE=edge

# ----- 路径 -----
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
OUTPUT_DIR=output
WORK_DIR=.work

# ----- OpenAI 配置 -----
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
WHISPER_MODEL=whisper-1
TRANSLATE_MODEL=gpt-4o-mini
# 备选模型链(逗号分隔), 主模型失败时按序尝试
TRANSLATE_MODEL_FALLBACKS=gpt-4o-mini,gpt-3.5-turbo,gpt-4o
# faster-whisper 模型: tiny / base / small / medium / large-v3
FASTER_WHISPER_MODEL=base

# ----- Azure TTS (仅 TTS_ENGINE=azure 时需要) -----
AZURE_SPEECH_KEY=
AZURE_SPEECH_REGION=eastasia

# ----- Ollama 本地模型 (仅 TRANSLATE_ENGINE=ollama 时需要) -----
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_BATCH_SIZE=10
OLLAMA_TIMEOUT=120

# ----- TTS 配音音色 -----
TTS_VOICE_ZH=zh-CN-YunxiNeural
TTS_VOICE_EN=en-US-GuyNeural
TTS_VOICE_JA=ja-JP-KeitaNeural
TTS_VOICE_KO=ko-KR-InJoonNeural
TTS_RATE=+0%
TTS_VOLUME=+0%

# ----- 字幕配置 (黑底白字 YouTube 风格) -----
SUBTITLE_STYLE=single
SUBTITLE_FONT=Arial
SUBTITLE_FONTSIZE=28
SUBTITLE_PRIMARY_COLOR=&H00FFFFFF
SUBTITLE_OUTLINE_COLOR=&H00000000
SUBTITLE_OUTLINE_WIDTH=2
SUBTITLE_MARGIN_V=40
SUBTITLE_MAX_WIDTH_PERCENT=0.6
SUBTITLE_MAX_LINES=2

# ----- 音频配置 -----
AUDIO_KEEP_ORIGINAL=false
AUDIO_ORIGINAL_VOLUME=0.15

# ----- Google 翻译 -----
GOOGLE_TRANSLATE_URL=https://translate.googleapis.com/translate_a/single

# ----- 网络配置 -----
NETWORK_PROXY=
NETWORK_TIMEOUT=60
TIKTOK_COOKIES_BROWSER=
"""


@dataclass
class Config:
    """全局配置（运行时对象）"""

    # ===== 路径 =====
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # ===== 目录 =====
    output_dir: str = "output"
    work_dir: str = ".work"

    # ===== 引擎选择 =====
    asr_engine: str = "whisper-api"
    translate_engine: str = "openai"
    tts_engine: str = "edge"

    # ===== OpenAI =====
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    whisper_model: str = "whisper-1"
    translate_model: str = "gpt-4o-mini"
    translate_model_fallbacks: list = None
    faster_whisper_model: str = "base"

    # ===== Azure =====
    azure_speech_key: str = ""
    azure_speech_region: str = "eastasia"

    # ===== Ollama =====
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_batch_size: int = 10
    ollama_timeout: int = 120

    # ===== TTS 音色 =====
    tts_voice_zh: str = "zh-CN-YunxiNeural"
    tts_voice_en: str = "en-US-GuyNeural"
    tts_voice_ja: str = "ja-JP-KeitaNeural"
    tts_voice_ko: str = "ko-KR-InJoonNeural"
    tts_rate: str = "+0%"
    tts_volume: str = "+0%"

    # ===== 字幕 =====
    subtitle_style: str = "single"
    subtitle_font: str = "Arial"
    subtitle_fontsize: int = 28
    subtitle_primary_color: str = "&H00FFFFFF"
    subtitle_outline_color: str = "&H00000000"
    subtitle_outline_width: int = 2
    subtitle_margin_v: int = 40
    subtitle_max_width_percent: float = 0.6
    subtitle_max_lines: int = 2

    # ===== 音频 =====
    keep_original_audio: bool = False
    original_audio_volume: float = 0.15

    # ===== Google 翻译 =====
    google_translate_url: str = "https://translate.googleapis.com/translate_a/single"

    # ===== 网络 =====
    network_proxy: str = ""
    network_timeout: int = 60
    tiktok_cookies_browser: str = ""

    # ===== 配置文件路径 =====
    config_file_path: str = ""

    def ensure_dirs(self):
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)

    def voice_for(self, lang: str) -> str:
        mapping = {
            "zh": self.tts_voice_zh,
            "en": self.tts_voice_en,
            "ja": self.tts_voice_ja,
            "ko": self.tts_voice_ko,
        }
        return mapping.get(lang, self.tts_voice_en)

    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    def __repr__(self) -> str:
        return (
            f"Config(ffmpeg={Path(self.ffmpeg_path).name}, "
            f"asr={self.asr_engine}, translate={self.translate_engine}/{self.translate_model}, "
            f"tts={self.tts_engine}, openai={'Y' if self.has_openai() else 'N'})"
        )


# ============ .env 文件解析 ============

# Config 字段名 → .env 键名 的映射
# 同时记录类型转换信息
_ENV_KEY_MAP = {
    # 路径
    "ffmpeg_path":             ("FFMPEG_PATH", str),
    "ffprobe_path":            ("FFPROBE_PATH", str),
    "output_dir":              ("OUTPUT_DIR", str),
    "work_dir":                ("WORK_DIR", str),
    # 引擎
    "asr_engine":              ("ASR_ENGINE", str),
    "translate_engine":        ("TRANSLATE_ENGINE", str),
    "tts_engine":              ("TTS_ENGINE", str),
    # OpenAI
    "openai_api_key":          ("OPENAI_API_KEY", str),
    "openai_base_url":         ("OPENAI_BASE_URL", str),
    "whisper_model":            ("WHISPER_MODEL", str),
    "translate_model":          ("TRANSLATE_MODEL", str),
    "translate_model_fallbacks":("TRANSLATE_MODEL_FALLBACKS", "list"),
    "faster_whisper_model":    ("FASTER_WHISPER_MODEL", str),
    # Azure
    "azure_speech_key":        ("AZURE_SPEECH_KEY", str),
    "azure_speech_region":     ("AZURE_SPEECH_REGION", str),
    # Ollama
    "ollama_url":              ("OLLAMA_URL", str),
    "ollama_model":            ("OLLAMA_MODEL", str),
    "ollama_batch_size":       ("OLLAMA_BATCH_SIZE", int),
    "ollama_timeout":          ("OLLAMA_TIMEOUT", int),
    # TTS
    "tts_voice_zh":            ("TTS_VOICE_ZH", str),
    "tts_voice_en":            ("TTS_VOICE_EN", str),
    "tts_voice_ja":            ("TTS_VOICE_JA", str),
    "tts_voice_ko":            ("TTS_VOICE_KO", str),
    "tts_rate":                ("TTS_RATE", str),
    "tts_volume":              ("TTS_VOLUME", str),
    # 字幕
    "subtitle_style":          ("SUBTITLE_STYLE", str),
    "subtitle_font":           ("SUBTITLE_FONT", str),
    "subtitle_fontsize":       ("SUBTITLE_FONTSIZE", int),
    "subtitle_primary_color":  ("SUBTITLE_PRIMARY_COLOR", str),
    "subtitle_outline_color":  ("SUBTITLE_OUTLINE_COLOR", str),
    "subtitle_outline_width":  ("SUBTITLE_OUTLINE_WIDTH", int),
    "subtitle_margin_v":       ("SUBTITLE_MARGIN_V", int),
    "subtitle_max_width_percent": ("SUBTITLE_MAX_WIDTH_PERCENT", float),
    "subtitle_max_lines":      ("SUBTITLE_MAX_LINES", int),
    # 音频
    "keep_original_audio":     ("AUDIO_KEEP_ORIGINAL", bool),
    "original_audio_volume":   ("AUDIO_ORIGINAL_VOLUME", float),
    # Google
    "google_translate_url":    ("GOOGLE_TRANSLATE_URL", str),
    # 网络
    "network_proxy":           ("NETWORK_PROXY", str),
    "network_timeout":         ("NETWORK_TIMEOUT", int),
    "tiktok_cookies_browser":  ("TIKTOK_COOKIES_BROWSER", str),
}

# 反向映射：.env 键名 → Config 字段名
_FIELD_MAP = {v[0]: (k, v[1]) for k, v in _ENV_KEY_MAP.items()}


def _parse_env_value(raw: str, val_type):
    """将 .env 原始字符串值转换为 Python 类型"""
    raw = raw.strip()
    # 去引号
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1]

    if val_type == bool:
        return raw.lower() in ("true", "1", "yes", "on")
    elif val_type == int:
        try:
            return int(raw)
        except ValueError:
            return 0
    elif val_type == float:
        try:
            return float(raw)
        except ValueError:
            return 0.0
    elif val_type == "list":
        # 逗号分隔的列表
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]
    else:
        return raw


def _format_env_value(val, val_type) -> str:
    """将 Python 值格式化为 .env 字符串"""
    if val_type == bool:
        return "true" if val else "false"
    elif val_type == "list":
        if not val:
            return ""
        return ",".join(str(v) for v in val)
    elif isinstance(val, str) and val == "":
        return ""
    else:
        return str(val)


def _load_env_file(search_dir: str = ".") -> dict:
    """查找并解析 .env 配置文件

    返回 {ENV_KEY: raw_value_str} 字典
    兼容性：同时检查旧的 YAML 配置（向后兼容）
    """
    candidates = [
        os.path.join(search_dir, ".env"),
        os.path.join(os.path.expanduser("~"), ".transvideo", ".env"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            result = {}
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # 跳过注释和空行
                    if not line or line.startswith("#"):
                        continue
                    # 解析 KEY=VALUE
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    result[key] = value
            result["_config_file_path"] = path
            return result

    # 向后兼容：检查旧版 YAML
    yaml_path = os.path.join(search_dir, "transvideo_config.yaml")
    if os.path.isfile(yaml_path):
        result = _migrate_yaml_to_env(yaml_path)
        if result:
            result["_config_file_path"] = yaml_path
            result["_legacy_yaml"] = True
            return result

    return {}


def _migrate_yaml_to_env(yaml_path: str) -> dict:
    """从旧版 YAML 配置迁移到 .env 格式的键值"""
    try:
        import yaml
    except ImportError:
        return {}

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    result = {}
    # 顶层字段
    for cfg_field, (env_key, _) in _ENV_KEY_MAP.items():
        if cfg_field in data:
            result[env_key] = _format_env_value(data[cfg_field], _ENV_KEY_MAP[cfg_field][1])

    # 嵌套块
    yaml_to_env = {
        "openai": {
            "api_key": "OPENAI_API_KEY", "base_url": "OPENAI_BASE_URL",
            "whisper_model": "WHISPER_MODEL", "translate_model": "TRANSLATE_MODEL",
            "translate_model_fallbacks": "TRANSLATE_MODEL_FALLBACKS",
            "faster_whisper_model": "FASTER_WHISPER_MODEL",
        },
        "azure": {"speech_key": "AZURE_SPEECH_KEY", "speech_region": "AZURE_SPEECH_REGION"},
        "ollama": {"url": "OLLAMA_URL", "model": "OLLAMA_MODEL",
                    "batch_size": "OLLAMA_BATCH_SIZE", "timeout": "OLLAMA_TIMEOUT"},
        "tts": {"voice_zh": "TTS_VOICE_ZH", "voice_en": "TTS_VOICE_EN",
                "voice_ja": "TTS_VOICE_JA", "voice_ko": "TTS_VOICE_KO",
                "rate": "TTS_RATE", "volume": "TTS_VOLUME"},
        "subtitle": {"style": "SUBTITLE_STYLE", "font": "SUBTITLE_FONT",
                      "fontsize": "SUBTITLE_FONTSIZE", "primary_color": "SUBTITLE_PRIMARY_COLOR",
                      "outline_color": "SUBTITLE_OUTLINE_COLOR", "outline_width": "SUBTITLE_OUTLINE_WIDTH",
                      "margin_v": "SUBTITLE_MARGIN_V", "max_width_percent": "SUBTITLE_MAX_WIDTH_PERCENT",
                      "max_lines": "SUBTITLE_MAX_LINES"},
        "audio": {"keep_original": "AUDIO_KEEP_ORIGINAL", "original_volume": "AUDIO_ORIGINAL_VOLUME"},
        "google_translate": {"url": "GOOGLE_TRANSLATE_URL"},
        "network": {"proxy": "NETWORK_PROXY", "timeout": "NETWORK_TIMEOUT",
                     "tiktok_cookies_browser": "TIKTOK_COOKIES_BROWSER"},
    }

    for block_name, field_map in yaml_to_env.items():
        block = data.get(block_name, {}) or {}
        for yaml_key, env_key in field_map.items():
            if yaml_key in block:
                val = block[yaml_key]
                if env_key == "TRANSLATE_MODEL_FALLBACKS":
                    if isinstance(val, list):
                        result[env_key] = ",".join(str(v) for v in val) if val else ""
                    else:
                        result[env_key] = str(val) if val else ""
                elif isinstance(val, bool):
                    result[env_key] = "true" if val else "false"
                elif val is not None:
                    result[env_key] = str(val)

    return result


def _apply_env_dict(cfg: Config, env_data: dict):
    """将 .env 字典应用到 Config 对象"""
    for env_key, raw_value in env_data.items():
        if env_key.startswith("_"):
            continue
        if env_key not in _FIELD_MAP:
            continue
        cfg_field, val_type = _FIELD_MAP[env_key]
        parsed = _parse_env_value(raw_value, val_type)
        setattr(cfg, cfg_field, parsed)

    cfg.config_file_path = env_data.get("_config_file_path", "")


def _apply_env_vars(cfg: Config):
    """系统环境变量覆盖（最高优先级之一）"""
    env_map = {
        "OPENAI_API_KEY": "openai_api_key",
        "OPENAI_BASE_URL": "openai_base_url",
        "AZURE_SPEECH_KEY": "azure_speech_key",
    }
    for env_key, cfg_field in env_map.items():
        val = os.getenv(env_key)
        if val:
            setattr(cfg, cfg_field, val)


def load_config(search_dir: str = ".") -> Config:
    """加载配置：默认值 → .env 文件 → 环境变量"""
    cfg = Config()
    cfg.ffmpeg_path = _find_ffmpeg()
    cfg.ffprobe_path = _find_ffprobe()
    if cfg.translate_model_fallbacks is None:
        cfg.translate_model_fallbacks = ["gpt-4o-mini", "gpt-3.5-turbo", "gpt-4o"]

    # .env 配置文件
    env_data = _load_env_file(search_dir)
    if env_data:
        _apply_env_dict(cfg, env_data)
        if env_data.get("_legacy_yaml"):
            print(f"[config] 从旧版 YAML 迁移配置: {cfg.config_file_path}")
            print(f"[config] 正在生成 .env 文件...")
            create_config_template(search_dir)
            # 重新加载刚生成的 .env
            env_data2 = _load_env_file(search_dir)
            if env_data2:
                _apply_env_dict(cfg, env_data2)
            # 保存当前配置到 .env
            save_config(cfg)
            print(f"[config] 迁移完成，建议删除旧的 transvideo_config.yaml")
        else:
            print(f"[config] 已加载配置: {cfg.config_file_path}")
    else:
        create_config_template(search_dir)
        # 重新加载刚生成的 .env
        env_data = _load_env_file(search_dir)
        if env_data:
            _apply_env_dict(cfg, env_data)

    # 系统环境变量覆盖
    _apply_env_vars(cfg)

    return cfg


def create_config_template(search_dir: str = "."):
    """首次运行时生成 .env 配置文件模板"""
    path = os.path.join(search_dir, CONFIG_FILE_NAME)
    if os.path.isfile(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(ENV_TEMPLATE)
    print(f"[config] 已生成配置文件: {path}")
    print(f"[config] 请编辑此文件填入你的 API key")


def save_config(cfg: Config, path: str = None):
    """将 Config 对象保存到 .env 配置文件

    保留注释策略：读取原文件，按行替换 KEY=VALUE，保留注释和空行。
    """
    if path is None:
        path = cfg.config_file_path or os.path.join(".", CONFIG_FILE_NAME)

    # 获取基础内容
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    else:
        lines = ENV_TEMPLATE.split("\n")

    # 构建当前配置的 ENV_KEY → value 映射
    current_values = {}
    for cfg_field, (env_key, val_type) in _ENV_KEY_MAP.items():
        val = getattr(cfg, cfg_field, None)
        current_values[env_key] = _format_env_value(val, val_type)

    # 按行处理：替换已存在的 KEY=VALUE 行
    found_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过注释和空行
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        # 解析 KEY=VALUE
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in current_values:
            new_val = current_values[key]
            new_lines.append(f"{key}={new_val}")
            found_keys.add(key)
        else:
            new_lines.append(line)

    # 追加缺失的键
    for cfg_field, (env_key, val_type) in _ENV_KEY_MAP.items():
        if env_key not in found_keys:
            new_lines.append(f"{env_key}={current_values[env_key]}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))

    print(f"[config] 配置已保存: {path}")
