# -*- coding: utf-8 -*-
"""TTS 配音模块 —— 支持 edge-tts（免费）和 Azure TTS

配音策略：
  - 每个片段生成独立音频文件
  - 记录 TTS 实际时长，用于后续对齐
  - 如果 TTS 时长 > 原片段时长，需要标记（合成时变速或留白处理）
"""

import os
import sys
import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

from config import Config
from modules.transcriber import Segment


@dataclass
class TTSResult:
    """单个片段的 TTS 结果"""
    index: int
    audio_path: str
    start: float         # 原片段开始时间
    end: float            # 原片段结束时间
    tts_duration: float   # TTS 实际音频时长
    text: str             # 译文文本

    @property
    def needs_speedup(self) -> bool:
        """TTS 时长是否超过原片段，需要加速"""
        return self.tts_duration > self.end - self.start + 0.3  # 0.3s 容差


def _clean_old_tts_files(output_dir: str):
    """清理旧的 TTS 音频文件，避免跳过片段复用旧音频

    场景：TTS 合成时某些片段会被 _should_skip_text 跳过或合成失败，
    如果目录里残留上次的同名文件（tts_00000.mp3 等），
    后续音频混合步骤会错误地使用旧音频，导致字幕是 A 语言但配音是 B 语言。

    本函数在每次 synthesize 开始时清理所有 tts_*.mp3 / tts_*.wav 旧文件。
    """
    import glob
    patterns = ["tts_*.mp3", "tts_*.wav"]
    old_files = []
    for pat in patterns:
        old_files.extend(glob.glob(os.path.join(output_dir, pat)))
    if old_files:
        print(f"[tts] 清理旧 TTS 文件: {len(old_files)} 个")
        for f in old_files:
            try:
                os.remove(f)
            except OSError:
                pass


# 需要跳过的错误/警告文本模式（翻译失败时可能残留）
# 模块级常量，供 EdgeTTSEngine 和 AzureTTSEngine 共用
SKIP_TEXT_PATTERNS = [
    "MYMEMORY WARNING",
    "YOU USED ALL",
    "FREE WORDS FOR TODAY",
    "PLEASE RETRY",
    "PLEASE USE A VALID EMAIL",
    "PLEASE SELECT TWO DISTINCT LANGUAGES",   # MyMemory 配额耗尽后的固定错误返回
    "SELECT TWO DISTINCT",
    "INVALID LANGUAGE PAIR",
    "QUERY IS EMPTY",
    "TRANSLATION ERROR",
    "ERROR:",
    "HTTP ERROR",
]


def _should_skip_text(text: str) -> bool:
    """检测是否是错误/警告文本（翻译失败时残留的）

    模块级函数，供所有 TTS 引擎共用。
    """
    if not text or not text.strip():
        return True
    text_upper = text.upper()
    return any(p in text_upper for p in SKIP_TEXT_PATTERNS)


class EdgeTTSEngine:
    """edge-tts 免费配音（无需 API key）"""

    def __init__(self, config: Config):
        self.config = config
        # 在初始化时就检查依赖，避免每条都失败
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            raise ImportError(
                "edge-tts 未安装。请运行:\n"
                "  pip install edge-tts\n"
                f"  (当前 Python: {sys.executable})\n"
                "如果上述命令安装的 Python 与运行 CLI 的不同，请用:\n"
                f"  \"{sys.executable}\" -m pip install edge-tts"
            )

    async def _synthesize_one(self, text: str, voice: str, output_path: str, rate: str, volume: str,
                              timeout: float = 30.0):
        """合成单条音频（异步）

        超时保护：edge-tts 的 WebSocket 连接可能卡住（网络问题/服务端限流），
        用 asyncio.wait_for 包装，超时后抛出 TimeoutError，由调用方重试。

        代理处理：edge-tts 连接微软服务器，国内可以直连。
        如果全局代理被设置（HTTP_PROXY/HTTPS_PROXY），aiohttp 的 WebSocket 可能不支持，
        导致连接失败或卡住。所以合成前临时清除代理环境变量。
        """
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        # 设置超时，避免 WebSocket 卡住导致整个程序卡死
        await asyncio.wait_for(communicate.save(output_path), timeout=timeout)

    def synthesize_segment(self, text: str, voice: str, output_path: str,
                            max_retries: int = 3, timeout: float = 30.0) -> float:
        """合成单条音频，带重试和超时

        参数：
          max_retries: 失败时的最大重试次数（含首次共 max_retries 次尝试）
          timeout: 单次合成的超时时间（秒），超时后重试

        返回：音频时长（秒）。失败重试耗尽后抛出最后一个异常。
        """
        # 临时清除代理环境变量（edge-tts 的 WebSocket 不支持 HTTP 代理，会卡住）
        # 微软服务器国内可直连，不需要代理
        saved_env = {}
        proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
        for key in proxy_keys:
            if key in os.environ:
                saved_env[key] = os.environ.pop(key)

        last_error = None
        try:
            for attempt in range(max_retries):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._synthesize_one(
                        text, voice, output_path,
                        self.config.tts_rate, self.config.tts_volume,
                        timeout=timeout,
                    ))
                    # 合成成功：验证文件非空
                    if os.path.isfile(output_path) and os.path.getsize(output_path) > 100:
                        # 用 ffprobe 获取时长
                        duration = self._get_audio_duration(output_path)
                        return duration
                    else:
                        last_error = RuntimeError("合成的音频文件为空")
                        # 删除空文件
                        if os.path.isfile(output_path):
                            os.remove(output_path)
                except asyncio.TimeoutError:
                    last_error = RuntimeError(f"合成超时（{timeout}s）")
                    print(f" ⏱超时重试 {attempt+1}/{max_retries}", end="", flush=True)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        print(f" 重试 {attempt+1}/{max_retries}", end="", flush=True)
                        import time
                        time.sleep(1.0 * (attempt + 1))  # 1s, 2s 退避
                finally:
                    loop.close()

            # 所有重试都失败
            raise last_error if last_error else RuntimeError("合成失败")
        finally:
            # 恢复代理环境变量
            os.environ.update(saved_env)

    def _get_audio_duration(self, audio_path: str) -> float:
        """用 ffprobe 获取音频时长"""
        cmd = [
            self.config.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            return float(result.stdout.strip())
        except (ValueError, IndexError):
            return 0.0

    # 需要跳过的错误/警告文本模式（已提取为模块级常量，此处保留别名以兼容旧代码）
    SKIP_TEXT_PATTERNS = SKIP_TEXT_PATTERNS

    @staticmethod
    def _should_skip_text(text: str) -> bool:
        """检测是否是错误/警告文本（委托给模块级函数）"""
        return _should_skip_text(text)

    def synthesize(self, segments: List[Segment], target_lang: str, output_dir: str) -> List[TTSResult]:
        voice = self.config.voice_for(target_lang)
        print(f"[tts] edge-tts 配音: voice={voice}, {len(segments)} 个片段")

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 防御性清理：删除旧的 TTS 文件，避免跳过片段复用旧音频（导致字幕和配音语言不一致）
        _clean_old_tts_files(output_dir)

        results = []
        skipped = 0
        failed = 0

        for i, seg in enumerate(segments):
            text = seg.translated_text or seg.text
            # 跳过空文本和错误/警告文本
            if self._should_skip_text(text):
                skipped += 1
                if skipped <= 3:
                    print(f"[tts]   {i+1}/{len(segments)}: 跳过（无效文本: {text[:40]}...）")
                continue

            audio_path = os.path.join(output_dir, f"tts_{i:05d}.mp3")
            print(f"[tts]   {i+1}/{len(segments)}: {text[:30]}...", end="", flush=True)

            try:
                duration = self.synthesize_segment(text, voice, audio_path,
                                                    max_retries=3, timeout=30.0)
                results.append(TTSResult(
                    index=i,
                    audio_path=audio_path,
                    start=seg.start,
                    end=seg.end,
                    tts_duration=duration,
                    text=text,
                ))
                flag = "!" if duration > (seg.end - seg.start) else " "
                print(f" ({duration:.1f}s){flag}")
            except Exception as e:
                failed += 1
                print(f" 失败({failed}): {str(e)[:80]}")
                # 失败的片段继续处理下一个，不卡住整个流程
                # 后续 mix_tts_audio 会跳过缺失的片段（用静音填充）

        if skipped > 0:
            print(f"[tts] 跳过 {skipped} 个无效文本片段")
        if failed > 0:
            print(f"[tts] ⚠ {failed} 个片段合成失败（将用静音填充）")
            # 如果失败超过 30%，给出建议
            failure_rate = failed / len(segments)
            if failure_rate > 0.3:
                print(f"[tts]   失败率 {failure_rate:.0%}，建议:")
                print(f"[tts]   1. 检查网络连接（edge-tts 需要访问微软服务器）")
                print(f"[tts]   2. 尝试切换 voice（如 zh-CN-XiaoxiaoNeural）")
                print(f"[tts]   3. 或改用 Azure TTS（更稳定，需要 key）")
        print(f"[tts] 配音完成: 成功 {len(results)}/{len(segments)} 个音频")
        return results


class AzureTTSEngine:
    """Azure TTS 配音（需 key）"""

    def __init__(self, config: Config):
        self.config = config
        if not config.azure_speech_key:
            raise ValueError("Azure TTS 需要 AZURE_SPEECH_KEY")

    def synthesize(self, segments: List[Segment], target_lang: str, output_dir: str) -> List[TTSResult]:
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise ImportError("Azure SDK 未安装: pip install azure-cognitiveservices-speech")

        voice = self.config.voice_for(target_lang)
        print(f"[tts] Azure TTS 配音: voice={voice}")

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 防御性清理：删除旧的 TTS 文件，避免跳过片段复用旧音频
        _clean_old_tts_files(output_dir)

        results = []

        speech_config = speechsdk.SpeechConfig(
            subscription=self.config.azure_speech_key,
            region=self.config.azure_speech_region,
        )
        speech_config.speech_synthesis_voice_name = voice

        for i, seg in enumerate(segments):
            text = seg.translated_text or seg.text
            if not text.strip():
                continue

            audio_path = os.path.join(output_dir, f"tts_{i:05d}.wav")
            audio_config = speechsdk.audio.AudioOutputConfig(filename=audio_path)
            synthesizer = speechsdk.SpeechSynthesizer(speech_config, audio_config)

            print(f"[tts]   {i+1}/{len(segments)}: {text[:30]}...", end="", flush=True)
            result = synthesizer.speak_text_async(text).get()

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                duration = self._get_duration_wav(audio_path)
                results.append(TTSResult(
                    index=i, audio_path=audio_path,
                    start=seg.start, end=seg.end,
                    tts_duration=duration, text=text,
                ))
                print(f" ({duration:.1f}s)")
            else:
                print(f" 失败: {result.reason}")

        return results

    def _get_duration_wav(self, path: str) -> float:
        import wave
        with wave.open(path, "rb") as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return frames / rate


def create_tts_engine(config: Config):
    """工厂函数：根据 config.tts_engine 选择 TTS 引擎"""
    if config.tts_engine == "azure" and config.azure_speech_key:
        return AzureTTSEngine(config)
    # 默认 edge-tts
    return EdgeTTSEngine(config)
