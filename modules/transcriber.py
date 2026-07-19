# -*- coding: utf-8 -*-
"""ASR 语音识别模块 —— 支持 OpenAI Whisper API 和 faster-whisper 本地"""

# 必须在任何 import 之前设置：解决 Windows 上 faster-whisper (ctranslate2 + Intel OpenMP)
# 与其他库（PyTorch/numpy/MKL）的 OpenMP runtime 冲突错误：
#   OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized
# 这是 Windows 上 Intel OpenMP 多副本共存的标准 workaround
import os as _os
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import os
import json
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from config import Config


@dataclass
class Segment:
    """语音片段（字幕基本单元）"""
    start: float          # 开始时间（秒）
    end: float            # 结束时间（秒）
    text: str             # 原文
    translated_text: str = ""  # 译文（翻译后填充）

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "text": self.text,
                "translated_text": self.translated_text}


class Transcriber:
    """ASR 基类"""

    def transcribe(self, audio_path: str, source_lang: str = "auto") -> List[Segment]:
        raise NotImplementedError


class WhisperAPITranscriber(Transcriber):
    """OpenAI Whisper API 语音识别"""

    def __init__(self, config: Config):
        self.config = config
        from openai import OpenAI
        # 支持自定义 base_url（兼容第三方 OpenAI 兼容服务）
        self.client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url if config.openai_base_url else None,
        )
        self.model = config.whisper_model

    def transcribe(self, audio_path: str, source_lang: str = "auto") -> List[Segment]:
        print(f"[asr] Whisper API 识别中: {audio_path}")

        # Whisper API 支持 verbose_json 格式带时间戳
        with open(audio_path, "rb") as audio_file:
            kwargs = {
                "model": self.model,
                "file": audio_file,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }
            # Whisper API 不支持 "auto"，不传 language 即自动检测
            if source_lang and source_lang != "auto":
                kwargs["language"] = source_lang

            transcript = self.client.audio.transcriptions.create(**kwargs)

        segments = []
        # 优先使用 segments（带时间戳）
        if hasattr(transcript, "segments") and transcript.segments:
            for seg in transcript.segments:
                text = seg.text.strip()
                if text:
                    segments.append(Segment(
                        start=seg.start,
                        end=seg.end,
                        text=text,
                    ))
        elif hasattr(transcript, "text") and transcript.text:
            # 回退：无时间戳，整段作为一个 segment
            segments.append(Segment(start=0, end=0, text=transcript.text.strip()))

        print(f"[asr] 识别完成: {len(segments)} 个片段")
        return segments


class FasterWhisperTranscriber(Transcriber):
    """faster-whisper 本地语音识别（无需 API key）"""

    def __init__(self, config: Config):
        self.config = config
        self.model_size = config.faster_whisper_model

    @staticmethod
    def _detect_device() -> tuple:
        """自动检测可用推理设备

        返回 (device, compute_type):
          - ("cuda", "float16"): 有 NVIDIA GPU + CUDA，速度最快（推荐）
          - ("cpu", "int8"): 无 GPU 或 CUDA 不可用，CPU 量化推理

        检测顺序：
          1. ctranslate2 是否支持 CUDA（faster-whisper 底层用 ctranslate2）
          2. 尝试 torch.cuda（如果安装了 PyTorch）
          3. 尝试 nvidia-smi 命令
          4. 都失败则回退 CPU
        """
        import os as _os
        import subprocess as _sp

        # 方法1: ctranslate2 自检（最可靠）
        try:
            import ctranslate2
            # ctranslate2.get_cuda_device_count() 返回可用 GPU 数量
            cuda_count = ctranslate2.get_cuda_device_count()
            if cuda_count > 0:
                return ("cuda", "float16")
        except Exception:
            pass

        # 方法2: PyTorch 检测
        try:
            import torch
            if torch.cuda.is_available():
                return ("cuda", "float16")
        except ImportError:
            pass

        # 方法3: nvidia-smi 命令检测
        try:
            result = _sp.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                # 有 NVIDIA GPU，但 ctranslate2 可能没装 CUDA 版本
                # 仍然回退 CPU，因为 ctranslate2 的 CUDA 支持需要在编译时启用
                pass
        except Exception:
            pass

        # 回退到 CPU
        return ("cpu", "int8")

    def transcribe(self, audio_path: str, source_lang: str = "auto") -> List[Segment]:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "faster-whisper 未安装。请运行: pip install faster-whisper\n"
                "或使用 OpenAI Whisper API（需 OPENAI_API_KEY）"
            )

        # 设置 HuggingFace 镜像（国内网络优化）
        import os as _os
        _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        _os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
        # 禁用 Xet 存储（新版本 huggingface_hub 默认使用，国内 401）
        _os.environ["HF_HUB_DISABLE_XET"] = "1"
        _os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

        # Windows 下强制禁用符号链接：普通用户没有创建 symlink 的权限
        # （需要开发者模式或管理员权限），否则会报 [WinError 1314]
        # 方法：预置 huggingface_hub.file_download._are_symlinks_supported_in_dir = {cache_dir: False}
        # 这样 huggingface_hub 会直接用文件复制而非 symlink
        if _os.name == "nt":
            try:
                from huggingface_hub import file_download as _hf_file_download
                from huggingface_hub.constants import HF_HUB_CACHE as _HF_CACHE
                _cache_dir = str(__import__("pathlib").Path(_HF_CACHE).expanduser().resolve())
                _hf_file_download._are_symlinks_supported_in_dir[_cache_dir] = False
            except Exception:
                pass  # 失败不影响主流程（huggingface_hub 会自动检测并降级）

        # 代理：如果配置了 network_proxy，设置给 HuggingFace 下载
        # （cli.py 启动时已全局设置，这里做保险：直接 import transcriber 也能用）
        proxy = getattr(self.config, "network_proxy", "") or ""
        if proxy:
            if not proxy.startswith(("http://", "https://", "socks")):
                proxy = f"http://{proxy}"
            _os.environ.setdefault("HTTP_PROXY", proxy)
            _os.environ.setdefault("HTTPS_PROXY", proxy)
            _os.environ.setdefault("http_proxy", proxy)
            _os.environ.setdefault("https_proxy", proxy)
            # 本地地址不走代理（Ollama 在本地）
            _os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,0.0.0.0,::1")
            _os.environ.setdefault("no_proxy", "localhost,127.0.0.1,0.0.0.0,::1")

        print(f"[asr] faster-whisper 本地识别中（模型: {self.model_size}）...")
        print(f"[asr] 使用 HuggingFace 镜像: {_os.environ.get('HF_ENDPOINT', '默认')}")
        if proxy:
            print(f"[asr] 使用代理下载模型: {proxy}")

        # 首次使用提示：模型未下载时给出预估大小
        model_sizes = {"tiny": "39MB", "base": "142MB", "small": "466MB",
                       "medium": "1.5GB", "large-v3": "3GB"}
        expected_size = model_sizes.get(self.model_size, "未知")
        print(f"[asr] 模型大小: {expected_size}" + (f"（首次使用需下载，请耐心等待）" if expected_size != "未知" else ""))

        # 模型下载目录：指定自定义目录后，faster-whisper 会用 local_dir_use_symlinks=False
        # 完全禁用符号链接（Windows 普通用户没有 symlink 权限，会报 [WinError 1314]）
        # 默认用项目下的 .models 目录，避免污染用户目录
        models_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".models")
        _os.makedirs(models_dir, exist_ok=True)

        # 自动检测 GPU：优先用 CUDA 加速（比 CPU 快 10-20 倍）
        # - 有 CUDA：device=cuda, compute_type=float16（GPU 原生精度，速度最快）
        # - 无 CUDA：device=cpu, compute_type=int8（CPU 量化，速度较慢但能跑）
        device, compute_type = self._detect_device()
        print(f"[asr] 推理设备: {device.upper()}" + (f" (compute_type={compute_type})" if device == "cuda" else f" (compute_type={compute_type})"))

        # download_root 指定后，faster-whisper 内部调用 download_model 时会传 local_dir，禁用 symlink
        model = WhisperModel(self.model_size, device=device, compute_type=compute_type, download_root=models_dir)

        language = None if source_lang == "auto" else source_lang
        segs, info = model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )

        segments = []
        for seg in segs:
            text = seg.text.strip()
            if text:
                segments.append(Segment(
                    start=seg.start,
                    end=seg.end,
                    text=text,
                ))

        print(f"[asr] 识别完成: {len(segments)} 个片段, 检测语言: {info.language}")
        return segments


def pre_download_faster_whisper_model(model_size: str, config: Config = None) -> bool:
    """预下载 faster-whisper 模型（不执行识别）

    在 CLI 配置菜单中调用，让用户提前下载模型，避免首次使用时等待。

    返回 True 表示成功（或已存在），False 表示失败。
    """
    model_sizes = {"tiny": "39MB", "base": "142MB", "small": "466MB",
                   "medium": "1.5GB", "large-v3": "3GB"}
    if model_size not in model_sizes:
        print(f"[asr] 未知模型: {model_size}")
        return False

    print(f"[asr] 准备下载 faster-whisper 模型: {model_size} ({model_sizes[model_size]})")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[asr] faster-whisper 未安装。请运行: pip install faster-whisper")
        return False

    import os as _os
    _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    _os.environ["HF_HUB_DISABLE_XET"] = "1"
    _os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    models_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".models")
    _os.makedirs(models_dir, exist_ok=True)

    # 代理设置
    if config and getattr(config, "network_proxy", ""):
        proxy = config.network_proxy
        if not proxy.startswith(("http://", "https://", "socks")):
            proxy = f"http://{proxy}"
        _os.environ.setdefault("HTTP_PROXY", proxy)
        _os.environ.setdefault("HTTPS_PROXY", proxy)

    try:
        print(f"[asr] 下载中（保存到 {models_dir}）...")
        # 只下载模型，不做识别
        WhisperModel(model_size, device="cpu", compute_type="int8", download_root=models_dir)
        print(f"[asr] ✓ 模型 {model_size} 下载完成!")
        return True
    except Exception as e:
        print(f"[asr] ✗ 下载失败: {e}")
        return False


def create_transcriber(config: Config) -> Transcriber:
    """工厂函数：根据配置创建识别器，自动降级"""
    if config.asr_engine == "whisper-api" and config.has_openai():
        return WhisperAPITranscriber(config)
    elif config.asr_engine == "faster-whisper":
        return FasterWhisperTranscriber(config)
    elif config.has_openai():
        # 配置了 whisper-api 但没 key，且有 openai key，仍用 API
        return WhisperAPITranscriber(config)
    else:
        # 降级到本地
        print("[asr] 无 OPENAI_API_KEY，降级到 faster-whisper 本地")
        return FasterWhisperTranscriber(config)


def extract_audio(video_path: str, output_path: str, config: Config) -> str:
    """用 ffmpeg 从视频提取音频（16kHz 单声道 wav，适合 ASR）

    如果视频没有音频流，会给出清晰的错误提示。
    """
    # 先检查视频是否有音频流（避免 ffmpeg 报 "Output file does not contain any stream"）
    try:
        probe_cmd = [
            config.ffprobe_path,
            "-v", "error",
            "-select_streams", "a",  # 只选音频流
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1",
            video_path,
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        has_audio = "audio" in (probe_result.stdout or "").lower()

        if not has_audio:
            raise RuntimeError(
                f"视频文件没有音频流: {video_path}\n"
                "可能原因：\n"
                "  1. 该视频是在 TikTok 音频修复前下载的（旧的无音频视频文件）\n"
                "  2. 视频本身就是无声的\n"
                "解决方法：\n"
                "  - 删除该工作目录并重新下载视频（会自动获取含音频的版本）\n"
                "  - 检查 yt-dlp 是否需要更新: pip install -U yt-dlp\n"
                "  - 如果是 TikTok 视频，确保 yt-dlp 的 TikTok 补丁已应用"
            )
    except RuntimeError:
        raise
    except Exception as e:
        # ffprobe 检查失败时继续尝试 ffmpeg（不阻塞流程）
        print(f"[asr] 警告: 检查音频流失败（继续尝试）: {e}")

    cmd = [
        config.ffmpeg_path,
        "-y", "-i", video_path,
        "-vn",                    # 不要视频
        "-acodec", "pcm_s16le",   # 16bit PCM
        "-ar", "16000",           # 16kHz
        "-ac", "1",               # 单声道
        output_path,
    ]
    print(f"[asr] 提取音频: {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 提取音频失败: {result.stderr[-500:]}")
    return output_path


def save_segments(segments: List[Segment], output_path: str):
    """保存片段为 JSON（便于断点续跑）"""
    data = [s.to_dict() for s in segments]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[asr] 片段已保存: {output_path}")


def load_segments(output_path: str) -> List[Segment]:
    """从 JSON 加载片段"""
    with open(output_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Segment(**d) for d in data]
