# -*- coding: utf-8 -*-
"""视频合成模块 —— 混合 TTS 音频 + 烧录 ASS 字幕 + 输出最终视频

合成流程：
  1. 获取视频时长和分辨率
  2. 将 TTS 片段按时间戳混合为完整音轨（支持变速处理超时片段）
  3. 用 ffmpeg 合成：视频 + 混合音频 + ASS 字幕烧录
  4. 可选保留原音频（降音混合）

性能优化：优先用 numpy 混合音频（快），无 numpy 时回退到 ffmpeg amix 分批。
"""

import os
import json
import array
import wave
import subprocess
from pathlib import Path
from typing import List, Optional

from config import Config
from modules.transcriber import Segment
from modules.tts_engine import TTSResult


# 尝试导入 numpy（可选，用于加速音频混合）
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


class VideoComposer:
    """视频合成器"""

    def __init__(self, config: Config):
        self.config = config
        self.sample_rate = 44100

    # ===== 视频信息 =====

    def get_video_duration(self, video_path: str) -> float:
        """获取视频时长（秒）"""
        cmd = [
            self.config.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return float(result.stdout.strip())

    def get_video_resolution(self, video_path: str) -> tuple:
        """获取视频分辨率 (width, height)"""
        cmd = [
            self.config.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])

    # ===== 音频转换 =====

    def _mp3_to_wav(self, mp3_path: str, wav_path: str):
        """MP3 → WAV（统一采样率/单声道）"""
        cmd = [
            self.config.ffmpeg_path, "-y",
            "-i", mp3_path,
            "-ar", str(self.sample_rate),
            "-ac", "1",
            "-acodec", "pcm_s16le",
            wav_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"MP3→WAV 失败: {result.stderr[-300:]}")

    def _apply_atempo(self, input_path: str, output_path: str, speed: float):
        """用 ffmpeg atempo 变速（speed > 1 加速, < 1 减速）"""
        # atempo 范围 0.5-2.0，超出则链式
        filters = []
        remaining = speed
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        filters.append(f"atempo={remaining:.4f}")
        filter_str = ",".join(filters)

        cmd = [
            self.config.ffmpeg_path, "-y",
            "-i", input_path,
            "-filter:a", filter_str,
            "-acodec", "pcm_s16le",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(f"atempo 变速失败: {result.stderr[-300:]}")

    # ===== 音频混合 =====

    def _mix_with_numpy(self, tts_results: List[TTSResult],
                        total_duration: float, output_path: str,
                        original_audio_path: Optional[str] = None) -> str:
        """用 numpy 混合音频（快速，含重采样 + 音量标准化）"""
        total_samples = int(total_duration * self.sample_rate)
        mixed = np.zeros(total_samples, dtype=np.float32)

        # 可选：加载原音频（降音）
        if original_audio_path and os.path.isfile(original_audio_path):
            orig = self._read_wav_resampled(original_audio_path, self.sample_rate)
            if len(orig) < total_samples:
                orig = np.pad(orig, (0, total_samples - len(orig)))
            mixed += orig[:total_samples] * self.config.original_audio_volume

        # 放置每个 TTS 片段（重采样到 self.sample_rate）
        placed_count = 0
        for r in tts_results:
            data = self._read_wav_resampled(r.audio_path, self.sample_rate)
            if len(data) == 0:
                print(f"[compose] 警告: 片段 {r.index} 音频为空，跳过")
                continue
            start_sample = int(r.start * self.sample_rate)
            end_sample = start_sample + len(data)
            if end_sample > total_samples:
                data = data[:total_samples - start_sample]
                end_sample = total_samples
            if start_sample < 0:
                data = data[-start_sample:]
                start_sample = 0
                end_sample = start_sample + len(data)
            mixed[start_sample:end_sample] += data
            placed_count += 1

        # 音量标准化：将峰值归一化到 0.9（避免削波），提升整体音量
        peak = float(np.max(np.abs(mixed)))
        if peak > 0.001:  # 仅在有实际音频时标准化（避免放大纯底噪）
            target_peak = 0.9
            if peak < target_peak:
                gain = target_peak / peak
                # 限制最大增益 100 倍（-40dB → 0dB），避免放大底噪
                gain = min(gain, 100.0)
                mixed = mixed * gain
                if placed_count > 0:
                    actual_peak = min(peak * gain, target_peak)
                    print(f"[compose] 音量标准化: peak {peak:.4f} -> {actual_peak:.4f} (gain={gain:.2f})")
        elif placed_count > 0:
            print(f"[compose] 警告: 音频峰值 {peak:.6f} 过低，可能 TTS 生成异常（检查 prompt_text）")

        # 限幅 + 转 int16
        mixed = np.clip(mixed, -1.0, 1.0)
        mixed_int16 = (mixed * 32767).astype(np.int16)

        self._write_wav(output_path, mixed_int16.tobytes(), self.sample_rate, 1, 2)
        return output_path

    def _mix_with_array(self, tts_results: List[TTSResult],
                        total_duration: float, output_path: str,
                        original_audio_path: Optional[str] = None) -> str:
        """用 array 模块混合音频（无 numpy 时的回退）"""
        total_samples = int(total_duration * self.sample_rate)
        # 用 int32 存储避免溢出
        mixed = np.zeros(total_samples, dtype=np.int32) if HAS_NUMPY else \
                array.array('i', [0] * total_samples)

        if original_audio_path and os.path.isfile(original_audio_path):
            orig = self._read_wav_array(original_audio_path)
            vol = int(self.config.original_audio_volume * 32768)
            end = min(len(orig), total_samples)
            for i in range(end):
                mixed[i] += (orig[i] * vol) >> 15

        for r in tts_results:
            data = self._read_wav_array(r.audio_path)
            start_sample = int(r.start * self.sample_rate)
            end = min(start_sample + len(data), total_samples)
            chunk = end - start_sample
            for i in range(chunk):
                val = mixed[start_sample + i] + data[i]
                mixed[start_sample + i] = max(-32768, min(32767, val))

        # 转 int16
        if HAS_NUMPY:
            mixed_int16 = np.clip(mixed, -32768, 32767).astype(np.int16)
            pcm = mixed_int16.tobytes()
        else:
            mixed_int16 = array.array('h', [max(-32768, min(32767, v)) for v in mixed])
            pcm = mixed_int16.tobytes()

        self._write_wav(output_path, pcm, self.sample_rate, 1, 2)
        return output_path

    def _read_wav_numpy(self, path: str) -> np.ndarray:
        """读取 WAV 为 numpy float 数组（保持原始采样率）"""
        with wave.open(path, "rb") as f:
            frames = f.readframes(f.getnframes())
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return data

    def _read_wav_resampled(self, path: str, target_rate: int) -> np.ndarray:
        """读取 WAV 并重采样到目标采样率

        某些音频源可能输出 32000Hz，但混合缓冲区是 44100Hz。
        不重采样会导致音频变速变调（播放速度变为 44100/32000=1.375 倍）。

        使用线性插值重采样（简单但有效，音质损失极小）。
        """
        with wave.open(path, "rb") as f:
            src_rate = f.getframerate()
            n_frames = f.getnframes()
            frames = f.readframes(n_frames)
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        if src_rate == target_rate or len(data) == 0:
            return data

        # 线性插值重采样
        n_src = len(data)
        n_dst = int(n_src * target_rate / src_rate)
        if n_dst <= 1:
            return data

        # 生成源和目标的索引位置
        src_indices = np.arange(n_src)
        dst_indices = np.linspace(0, n_src - 1, n_dst)

        # 线性插值
        try:
            resampled = np.interp(dst_indices, src_indices, data).astype(np.float32)
        except Exception:
            # 插值失败时回退到最近邻
            nearest = np.round(dst_indices).astype(int)
            nearest = np.clip(nearest, 0, n_src - 1)
            resampled = data[nearest]

        return resampled

    def _read_wav_array(self, path: str) -> array.array:
        """读取 WAV 为 array"""
        with wave.open(path, "rb") as f:
            frames = f.readframes(f.getnframes())
            data = array.array('h')
            data.frombytes(frames)
        return data

    def _write_wav(self, path: str, pcm: bytes, sample_rate: int, channels: int, sampwidth: int):
        """写入 WAV 文件"""
        with wave.open(path, "wb") as f:
            f.setnchannels(channels)
            f.setsampwidth(sampwidth)
            f.setframerate(sample_rate)
            f.writeframes(pcm)

    def mix_tts_audio(self, tts_results: List[TTSResult],
                      total_duration: float, output_path: str,
                      original_audio_path: Optional[str] = None) -> str:
        """混合 TTS 片段到完整音轨"""
        print(f"[compose] 混合音频: {len(tts_results)} 个片段, 总时长 {total_duration:.1f}s")

        # 先将所有 MP3 转为 WAV
        wav_dir = os.path.dirname(output_path)
        Path(wav_dir).mkdir(parents=True, exist_ok=True)

        wav_results = []
        for r in tts_results:
            wav_path = os.path.join(wav_dir, f"seg_{r.index:05d}.wav")
            if r.audio_path.endswith(".wav"):
                wav_path = r.audio_path
            elif not os.path.isfile(wav_path):
                self._mp3_to_wav(r.audio_path, wav_path)
            wav_results.append(TTSResult(
                index=r.index, audio_path=wav_path,
                start=r.start, end=r.end,
                tts_duration=r.tts_duration, text=r.text,
            ))

        # 检查并处理超时片段（变速）
        speedup_count = 0
        for r in wav_results:
            original_duration = r.end - r.start
            if r.tts_duration > original_duration + 0.5:
                speed = r.tts_duration / original_duration
                speedup_path = os.path.join(wav_dir, f"speed_{r.index:05d}.wav")
                if not os.path.isfile(speedup_path):
                    self._apply_atempo(r.audio_path, speedup_path, speed)
                r.audio_path = speedup_path
                speedup_count += 1

        if speedup_count > 0:
            print(f"[compose] {speedup_count} 个片段已变速对齐")

        # 混合
        if HAS_NUMPY:
            self._mix_with_numpy(wav_results, total_duration, output_path, original_audio_path)
        else:
            self._mix_with_array(wav_results, total_duration, output_path, original_audio_path)

        print(f"[compose] 音轨已生成: {output_path}")
        return output_path

    # ===== 视频合成 =====

    @staticmethod
    def _ffmpeg_filter_path(path: str) -> str:
        """将路径转为 ffmpeg 滤镜兼容格式
        ass 滤镜路径需要：
        1. 正斜杠（Windows 反斜杠转正斜杠）
        2. 转义冒号（盘符 I:/ → I\:/）
        3. 转义单引号（' → \\'）
        4. 转义反斜杠（在滤镜字符串中）
        5. 转义逗号（避免被识别为滤镜分隔符）
        最终用单引号包裹整个路径
        """
        import os
        abs_path = os.path.abspath(path)
        abs_path = abs_path.replace('\\', '/')
        # 转义冒号（Windows 盘符 I:/ → I\:/ ）
        abs_path = abs_path.replace(':', '\\:')
        # 转义单引号
        abs_path = abs_path.replace("'", "\\'")
        # 转义逗号（避免被识别为滤镜分隔符）
        abs_path = abs_path.replace(',', '\\,')
        # 单引号包裹
        return f"'{abs_path}'"

    @staticmethod
    def _ffmpeg_output_path(path: str) -> str:
        """规范化 ffmpeg 输出路径（确保目录存在）"""
        import os
        out_dir = os.path.dirname(path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        return path

    def compose_video(self, video_path: str, audio_path: str,
                      subtitle_path: str, output_path: str,
                      keep_original: bool = False) -> str:
        """合成最终视频：视频 + 音频 + 字幕烧录

        策略：两步合成（更稳定，避免长视频崩溃）
          步骤1：烧录字幕到视频（重编码视频，复制音频）
          步骤2：替换/混合音频（复制视频流，处理音频）
        这样避免 ass 滤镜 + 音频处理 + libx264 同时工作导致内存崩溃。
        若两步失败，回退到单步模式（fast preset 降低内存压力）。
        """
        # 确保输出目录存在
        self._ffmpeg_output_path(output_path)
        print(f"[compose] 合成视频: {output_path}")

        # 为避免字幕路径含特殊字符导致 ass 滤镜解析失败
        # 复制字幕到无特殊字符的临时路径
        import os
        import shutil
        temp_dir = os.path.dirname(output_path) or "."
        temp_ass = os.path.join(temp_dir, "_subtitle_temp.ass")
        try:
            shutil.copy2(subtitle_path, temp_ass)
            sub_filter = self._ffmpeg_filter_path(temp_ass)
        except Exception:
            sub_filter = self._ffmpeg_filter_path(subtitle_path)
            temp_ass = None

        # 尝试两步合成（更稳定）
        try:
            result_path = self._compose_two_pass(
                video_path, audio_path, sub_filter, output_path, keep_original
            )
            # 清理临时文件
            if temp_ass and os.path.isfile(temp_ass):
                try: os.remove(temp_ass)
                except: pass
            return result_path
        except Exception as e:
            print(f"[compose] 两步合成失败: {e}")
            print(f"[compose] 回退到单步模式（fast preset）...")
            # 清理可能残留的临时文件
            temp_video = output_path + ".tmp_sub.mp4"
            if os.path.isfile(temp_video):
                try: os.remove(temp_video)
                except: pass
            if temp_ass and os.path.isfile(temp_ass):
                try: os.remove(temp_ass)
                except: pass
            # 单步回退
            return self._compose_single_pass(
                video_path, audio_path, sub_filter, output_path, keep_original
            )

    def _compose_two_pass(self, video_path: str, audio_path: str,
                          sub_filter: str, output_path: str,
                          keep_original: bool) -> str:
        """两步合成：先烧字幕，再处理音频"""
        import os
        import subprocess
        temp_video = output_path + ".tmp_sub.mp4"

        # ===== 步骤1：烧录字幕（重编码视频，复制音频）=====
        print(f"[compose] 步骤1/2: 烧录字幕（重编码视频）...")
        cmd1 = [
            self.config.ffmpeg_path, "-y",
            "-i", video_path,
            "-vf", f"ass={sub_filter}",
            "-map", "0:v", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "copy",
            "-threads", "2",           # 限制线程数，避免内存爆炸
            "-max_muxing_queue_size", "1024",
            temp_video,
        ]
        result1 = subprocess.run(cmd1, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result1.returncode != 0:
            err = result1.stderr[-1500:] if result1.stderr else "(无 stderr)"
            print(f"[compose] 步骤1失败 (返回码 {result1.returncode})")
            print(f"[compose] stderr:\n{err}")
            if os.path.isfile(temp_video):
                try: os.remove(temp_video)
                except: pass
            raise RuntimeError(f"字幕烧录失败 (返回码 {result1.returncode})")

        print(f"[compose] 步骤1完成: {temp_video}")

        # ===== 步骤2：替换/混合音频（复制视频流，处理音频）=====
        print(f"[compose] 步骤2/2: {'混合音频' if keep_original else '替换音频'}...")
        if keep_original:
            filter_complex = (
                f"[0:a]volume={self.config.original_audio_volume}[a_orig];"
                f"[a_orig][1:a]amix=inputs=2:duration=first:dropout_transition=0[a_mix]"
            )
            cmd2 = [
                self.config.ffmpeg_path, "-y",
                "-i", temp_video,
                "-i", audio_path,
                "-filter_complex", filter_complex,
                "-map", "0:v", "-map", "[a_mix]",
                "-c:v", "copy",              # 视频流直接复制，不重编码
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-threads", "2",
                output_path,
            ]
        else:
            cmd2 = [
                self.config.ffmpeg_path, "-y",
                "-i", temp_video,
                "-i", audio_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy",              # 视频流直接复制
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-threads", "2",
                output_path,
            ]

        result2 = subprocess.run(cmd2, capture_output=True, text=True, encoding="utf-8", errors="replace")

        # 清理临时视频
        if os.path.isfile(temp_video):
            try: os.remove(temp_video)
            except: pass

        if result2.returncode != 0:
            err = result2.stderr[-1500:] if result2.stderr else "(无 stderr)"
            print(f"[compose] 步骤2失败 (返回码 {result2.returncode})")
            print(f"[compose] stderr:\n{err}")
            raise RuntimeError(f"音频处理失败 (返回码 {result2.returncode})")

        print(f"[compose] 视频已生成: {output_path}")
        return output_path

    def _compose_single_pass(self, video_path: str, audio_path: str,
                             sub_filter: str, output_path: str,
                             keep_original: bool) -> str:
        """单步合成（回退方案，使用 fast preset 降低内存压力）"""
        import subprocess

        if keep_original:
            filter_complex = (
                f"[0:a]volume={self.config.original_audio_volume}[a_orig];"
                f"[a_orig][1:a]amix=inputs=2:duration=first:dropout_transition=0[a_mix];"
                f"[0:v]ass={sub_filter}[v_out]"
            )
            cmd = [
                self.config.ffmpeg_path, "-y",
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", filter_complex,
                "-map", "[v_out]", "-map", "[a_mix]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "21",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-threads", "2",
                "-max_muxing_queue_size", "1024",
                output_path,
            ]
        else:
            cmd = [
                self.config.ffmpeg_path, "-y",
                "-i", video_path,
                "-i", audio_path,
                "-vf", f"ass={sub_filter}",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "21",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-threads", "2",
                "-max_muxing_queue_size", "1024",
                output_path,
            ]

        print(f"[compose] ffmpeg 执行中（单步 fast 模式）...")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            err_tail = result.stderr[-2000:] if result.stderr else "(无 stderr)"
            print(f"[compose] ffmpeg 错误码: {result.returncode}")
            print(f"[compose] ffmpeg stderr 末尾:\n{err_tail}")
            raise RuntimeError(f"ffmpeg 合成失败 (返回码 {result.returncode})")

        print(f"[compose] 视频已生成: {output_path}")
        return output_path

    def compose_subtitle_only(self, video_path: str, subtitle_path: str,
                              output_path: str) -> str:
        """仅烧录字幕（不替换音频）"""
        # 确保输出目录存在
        self._ffmpeg_output_path(output_path)
        print(f"[compose] 仅烧录字幕: {output_path}")

        # 为避免字幕路径含特殊字符（括号、省略号等）导致 ass 滤镜解析失败
        # 复制字幕到无特殊字符的临时路径
        import os
        import tempfile
        temp_dir = os.path.dirname(output_path) or "."
        temp_ass = os.path.join(temp_dir, "_subtitle_temp.ass")
        try:
            import shutil
            shutil.copy2(subtitle_path, temp_ass)
            sub_filter = self._ffmpeg_filter_path(temp_ass)
        except Exception:
            # 复制失败则用原路径
            sub_filter = self._ffmpeg_filter_path(subtitle_path)
            temp_ass = None

        cmd = [
            self.config.ffmpeg_path, "-y",
            "-i", video_path,
            "-vf", f"ass={sub_filter}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "copy",
            "-threads", "2",                  # 限制线程数，避免长视频内存崩溃
            "-max_muxing_queue_size", "1024",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        # 清理临时字幕文件
        if temp_ass and os.path.isfile(temp_ass):
            try:
                os.remove(temp_ass)
            except Exception:
                pass

        if result.returncode != 0:
            err_tail = result.stderr[-2000:] if result.stderr else "(无 stderr)"
            print(f"[compose] ffmpeg 错误码: {result.returncode}")
            print(f"[compose] ffmpeg stderr 末尾:\n{err_tail}")
            if result.stdout:
                print(f"[compose] ffmpeg stdout:\n{result.stdout[-500:]}")
            raise RuntimeError(f"ffmpeg 字幕烧录失败 (返回码 {result.returncode})")
        print(f"[compose] 字幕视频已生成: {output_path}")
        return output_path
