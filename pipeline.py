# -*- coding: utf-8 -*-
"""流水线编排 —— 串联所有模块，执行完整翻译配音流程

流程：
  1. 获取视频（下载/本地/抖音）
  2. 提取音频
  3. ASR 语音识别
  4. 翻译
  5. 生成字幕
  6. TTS 配音（可选）
  7. 混合音频 + 合成视频

特性：
  - 时间戳输出目录：每次运行在 output_dir 下创建以时间命名的子目录，本次所有产物都在此目录
  - 抖音文案保存：解析抖音视频时，自动保存原文案和翻译文案到输出目录
  - 断点续跑：中间结果保存到 work_dir，可跳过已完成步骤
"""

# 必须在任何 import 之前设置：解决 Windows 上 faster-whisper (Intel OpenMP) 冲突
import os as _os
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import os
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from config import Config, load_config
from modules.downloader import VideoDownloader
from modules.transcriber import (
    Transcriber, create_transcriber, extract_audio,
    Segment, save_segments, load_segments
)
from modules.translator import create_translator
from modules.tts_engine import create_tts_engine
from modules.subtitle import SubtitleGenerator
from modules.video_composer import VideoComposer


class TranslationPipeline:
    """翻译配音流水线"""

    def __init__(self, config: Config):
        self.config = config
        self.downloader = VideoDownloader(config)
        self.composer = VideoComposer(config)
        self.subtitle_gen = SubtitleGenerator(config)
        # 本次会话的时间戳输出目录（每次 run 创建新的）
        self.session_output_dir: Optional[str] = None
        # 本次会话独立的 work 子目录（避免跨会话/跨视频的中间文件污染）
        self.session_work_dir: Optional[str] = None
        self.douyin_info: Optional[dict] = None

    def _create_session_dir(self, base_name: str = None) -> str:
        """创建以时间命名的输出子目录 + 独立的 work 子目录

        输出目录：output/{timestamp}/          ← 最终产物（视频、字幕、文案）
        工作目录：.work/{timestamp}_{base}/    ← 中间文件（ASR、TTS、混合音频）

        两层隔离：
          - 不同次运行的时间戳不同 → 避免跨次污染
          - base_name 后缀 → 避免同时间戳不同视频混淆

        base_name 可为 None（下载前），此时只用时间戳创建目录，
        下载完成后调用 _update_session_work_base 补上 base_name 后缀。

        重要：Windows 文件系统会自动截断目录名末尾的点号和空格，
        所以 _sanitize_name 必须去掉末尾的点号和空格，避免代码路径和实际路径不一致。
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(self.config.output_dir, timestamp)
        Path(session_dir).mkdir(parents=True, exist_ok=True)
        self.session_output_dir = session_dir

        # 本次会话独立的 work 子目录
        if base_name:
            safe_base = self._sanitize_name(base_name)[:40]
            session_work = os.path.join(self.config.work_dir, f"{timestamp}_{safe_base}")
        else:
            # 下载前 base_name 未知，先用纯时间戳创建（不再用 session 占位，避免重命名）
            session_work = os.path.join(self.config.work_dir, f"{timestamp}")
        Path(session_work).mkdir(parents=True, exist_ok=True)
        self.session_work_dir = session_work

        print(f"[pipeline] 本次输出目录: {session_dir}")
        print(f"[pipeline] 本次工作目录: {session_work}")
        return session_dir

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """清理文件名/目录名中的非法字符

        Windows 文件系统特殊限制：
        - 非法字符: / \\ : * ? " < > |
        - 末尾不能有点号 . 或空格（会被自动截断，导致代码路径和实际路径不一致）
        - 保留名: CON, PRN, AUX, NUL, COM1-9, LPT1-9
        """
        # 1. 替换非法字符为下划线
        for ch in ' /\\:*?"<>|':
            name = name.replace(ch, "_")
        # 2. 替换控制字符
        name = "".join(c if c.isprintable() else "_" for c in name)
        # 3. 压缩连续下划线
        while "__" in name:
            name = name.replace("__", "_")
        # 4. 去掉末尾的点号、空格、下划线（Windows 会截断末尾点号和空格）
        name = name.rstrip("._ ")
        # 5. 如果为空或全是下划线，用默认名
        if not name:
            name = "video"
        # 6. 避免保留名
        reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
        if name.upper() in reserved:
            name = "video"
        return name

    def _rename_session_work(self, base_name: str):
        """下载完成后，用真实视频名重命名 work 目录

        重要：本方法会处理 Windows 文件系统的特殊限制：
        - 末尾点号会被自动截断，所以 _sanitize_name 已处理
        - 如果重命名失败（例如目标名和实际名因截断而相同），不更新 session_work_dir
        - 重命名成功后，验证新路径确实存在，避免路径不一致
        """
        if not self.session_work_dir or not os.path.isdir(self.session_work_dir):
            return
        old = self.session_work_dir
        old_name = os.path.basename(old)
        # 如果已经是带 base_name 的格式（非纯时间戳），不重复处理
        # 纯时间戳格式：YYYYMMDD_HHMMSS（15 字符）
        if len(old_name) == 15 and old_name[8] == "_" and old_name.replace("_", "").isdigit():
            pass  # 纯时间戳，需要重命名
        else:
            return  # 已经有 base_name 后缀，不处理

        # 去掉 base_name 的文件扩展名
        from pathlib import Path as _Path
        stem = _Path(base_name).stem
        safe_base = self._sanitize_name(stem)[:40]
        new_name = f"{old_name}_{safe_base}"
        new = os.path.join(os.path.dirname(old), new_name)

        if old == new:
            return

        try:
            os.rename(old, new)
            # 验证：Windows 可能截断末尾点号，实际创建的目录名和 new 不同
            # 用 os.listdir 找到实际存在的目录
            parent = os.path.dirname(new)
            actual_new = None
            expected_prefix = old_name + "_"
            for entry in os.listdir(parent):
                if entry.startswith(expected_prefix) and os.path.isdir(os.path.join(parent, entry)):
                    actual_new = os.path.join(parent, entry)
                    break
            if actual_new:
                self.session_work_dir = actual_new
                if actual_new != new:
                    print(f"[pipeline] 工作目录重命名: {actual_new} (注意: 系统截断了非法字符)")
                else:
                    print(f"[pipeline] 工作目录重命名: {actual_new}")
            else:
                # 重命名后找不到新目录，回退到旧路径
                print(f"[pipeline] 警告: work 目录重命名验证失败，保持原路径: {old}")
        except OSError as e:
            print(f"[pipeline] 警告: work 目录重命名失败 ({e})，保持原路径: {old}")
            # 重命名失败不影响主流程，继续用旧路径

    def _save_transcript(self, segments: list, base_name: str,
                         video_info: Optional[dict] = None,
                         target_lang: Optional[str] = None):
        """保存原文案和翻译文案到输出目录

        生成文件：
          - {base}_原文.txt   原始字幕文本（带时间戳）
          - {base}_译文_{lang}.txt  翻译后字幕文本（带时间戳）
          - {base}_文案.md   完整文案（含视频信息和段落）
        """
        if not self.session_output_dir:
            return

        def fmt_ts(s: float) -> str:
            m = int(s // 60)
            sec = s % 60
            return f"[{m:02d}:{sec:05.2f}]"

        # 1. 原文案（纯文本，带时间戳）
        orig_path = os.path.join(self.session_output_dir, f"{base_name}_原文.txt")
        with open(orig_path, "w", encoding="utf-8") as f:
            if video_info:
                f.write(f"# 视频标题: {video_info.get('desc', '本地视频')}\n")
                src = video_info.get("source", "local")
                if src == "douyin":
                    f.write(f"# 作者: {video_info.get('author', '未知')}\n")
                    f.write(f"# 视频ID: {video_info.get('aweme_id', '')}\n")
                elif src in ("youtube", "streaming"):
                    f.write(f"# 作者: {video_info.get('author', '未知')}\n")
                    f.write(f"# 视频ID: {video_info.get('video_id', '')}\n")
                    if video_info.get('upload_date'):
                        f.write(f"# 上传日期: {video_info.get('upload_date', '')}\n")
                    if video_info.get('view_count'):
                        f.write(f"# 观看数: {video_info.get('view_count', 0):,}\n")
                    f.write(f"# 平台: {video_info.get('extractor', src).title()}\n")
                f.write(f"# 时长: {video_info.get('duration_ms', 0) / 1000:.1f}s\n")
                f.write("-" * 60 + "\n\n")
            for seg in segments:
                f.write(f"{fmt_ts(seg.start)} {seg.text}\n")
        print(f"[pipeline] 原文案已保存: {orig_path}")

        # 2. 译文案（如有翻译）
        if target_lang and segments and segments[0].translated_text:
            trans_path = os.path.join(
                self.session_output_dir, f"{base_name}_译文_{target_lang}.txt"
            )
            with open(trans_path, "w", encoding="utf-8") as f:
                if video_info:
                    f.write(f"# Video: {video_info.get('desc', 'local')}\n")
                    f.write(f"# Target Language: {target_lang}\n")
                    f.write("-" * 60 + "\n\n")
                for seg in segments:
                    f.write(f"{fmt_ts(seg.start)} {seg.translated_text}\n")
            print(f"[pipeline] 译文案已保存: {trans_path}")

        # 3. 完整文案 Markdown
        md_path = os.path.join(self.session_output_dir, f"{base_name}_文案.md")
        with open(md_path, "w", encoding="utf-8") as f:
            title = video_info.get("desc", "本地视频") if video_info else "本地视频"
            f.write(f"# {title}\n\n")
            if video_info:
                src = video_info.get("source", "local")
                if src == "douyin":
                    f.write(f"- 作者: {video_info.get('author', '未知')}\n")
                    f.write(f"- 视频ID: {video_info.get('aweme_id', '')}\n")
                    f.write(f"- 来源: 抖音\n")
                elif src in ("youtube", "streaming"):
                    f.write(f"- 作者: {video_info.get('author', '未知')}\n")
                    f.write(f"- 视频ID: {video_info.get('video_id', '')}\n")
                    if video_info.get('upload_date'):
                        f.write(f"- 上传日期: {video_info.get('upload_date', '')}\n")
                    if video_info.get('view_count'):
                        f.write(f"- 观看数: {video_info.get('view_count', 0):,}\n")
                    f.write(f"- 平台: {video_info.get('extractor', src).title()}\n")
                else:
                    f.write(f"- 来源: 本地视频\n")
                dur = video_info.get('duration_ms', 0) / 1000
                f.write(f"- 时长: {dur:.1f}秒\n\n")
            else:
                f.write(f"- 来源: 本地视频\n\n")

            f.write("## 原文\n\n")
            for seg in segments:
                f.write(f"{fmt_ts(seg.start)} {seg.text}\n")
            f.write("\n")

            if target_lang and segments and segments[0].translated_text:
                f.write(f"## 译文 ({target_lang})\n\n")
                for seg in segments:
                    f.write(f"{fmt_ts(seg.start)} {seg.translated_text}\n")
                f.write("\n")
        print(f"[pipeline] 完整文案已保存: {md_path}")

    def run(self, source: str, target_lang: str,
            source_lang: str = "auto",
            subtitle_only: bool = False,
            keep_original_audio: bool = False,
            skip_download: bool = False,
            skip_asr: bool = False,
            skip_translate: bool = False) -> str:
        """执行完整流水线"""

        self.config.ensure_dirs()
        # 创建本次会话的输出目录 + 独立 work 子目录（下载前 base_name 未知，用占位）
        self._create_session_dir(base_name=None)

        # ===== 步骤 1: 获取视频 =====
        video_path, video_info = self._step_download(source, self.session_work_dir, skip_download)
        if not video_path:
            return ""
        self.douyin_info = video_info if video_info and video_info.get("source") == "douyin" else None

        # 下载完成后，用真实视频名重命名 work 目录
        base_name = Path(video_path).stem
        old_work_dir = self.session_work_dir
        self._rename_session_work(base_name)
        work_dir = self.session_work_dir

        # 如果 work 目录被重命名了，同步更新 video_path（video 在旧目录里，需要指向新目录）
        if work_dir != old_work_dir:
            video_filename = os.path.basename(video_path)
            video_path = os.path.join(work_dir, video_filename)

        # ===== 步骤 2: 提取音频 =====
        audio_path = self._step_extract_audio(video_path, work_dir, source)

        # ===== 步骤 3: ASR 语音识别 =====
        segments = self._step_transcribe(audio_path, source_lang, work_dir, skip_asr)
        if not segments:
            print("[pipeline] ASR 无结果，退出")
            return ""

        # ===== 步骤 4: 翻译 =====
        segments = self._step_translate(segments, source_lang, target_lang, work_dir, skip_translate)

        # 保存文案（原文 + 译文 + 完整 markdown）
        self._save_transcript(segments, base_name, video_info, target_lang)

        # ===== 步骤 5: 生成字幕 =====
        width, height = self.composer.get_video_resolution(video_path)

        ass_path = os.path.join(work_dir, f"{base_name}.ass")
        srt_path = os.path.join(work_dir, f"{base_name}.srt")
        self.subtitle_gen.generate_ass(segments, ass_path, width, height)
        self.subtitle_gen.generate_srt(segments, srt_path, video_width=width)

        # 输出文件路径（放到本次会话目录）
        output_path = os.path.join(self.session_output_dir, f"{base_name}_{target_lang}.mp4")

        if subtitle_only:
            # 仅烧录字幕
            self.composer.compose_subtitle_only(video_path, ass_path, output_path)
        else:
            # ===== 步骤 6: TTS 配音 =====
            tts_dir = os.path.join(work_dir, "tts")
            tts_results = self._step_tts(segments, target_lang, tts_dir)

            if not tts_results:
                print("[pipeline] TTS 无结果，回退到仅字幕模式")
                self.composer.compose_subtitle_only(video_path, ass_path, output_path)
            else:
                # ===== 步骤 7: 混合音频 + 合成视频 =====
                duration = self.composer.get_video_duration(video_path)
                mixed_audio = os.path.join(work_dir, f"{base_name}_mixed.wav")

                # 保留原音频时，先提取原音
                orig_audio_path = None
                if keep_original_audio:
                    orig_audio_path = os.path.join(work_dir, f"{base_name}_orig.wav")
                    extract_audio(video_path, orig_audio_path, self.config)

                self.composer.mix_tts_audio(tts_results, duration, mixed_audio, orig_audio_path)
                self.composer.compose_video(
                    video_path, mixed_audio, ass_path, output_path, keep_original_audio
                )

        # 复制字幕文件到输出目录
        out_ass = os.path.join(self.session_output_dir, f"{base_name}_{target_lang}.ass")
        out_srt = os.path.join(self.session_output_dir, f"{base_name}_{target_lang}.srt")
        shutil.copy2(ass_path, out_ass)
        shutil.copy2(srt_path, out_srt)

        # 保存翻译结果 JSON 到输出目录
        result_json = os.path.join(self.session_output_dir, f"{base_name}_segments.json")
        save_segments(segments, result_json)

        print(f"\n[pipeline] ✓ 完成！")
        print(f"[pipeline]   视频: {output_path}")
        print(f"[pipeline]   字幕: {out_ass}")
        print(f"[pipeline]   SRT: {out_srt}")
        print(f"[pipeline]   翻译数据: {result_json}")
        print(f"[pipeline]   输出目录: {self.session_output_dir}")
        return output_path

    def _step_download(self, source: str, work_dir: str, skip: bool) -> Tuple[str, dict]:
        """步骤1: 下载/获取视频，返回 (video_path, info)"""
        if skip:
            # 查找已下载的视频
            for f in Path(work_dir).glob("*.mp4"):
                print(f"[pipeline] 跳过下载，使用已有: {f}")
                return str(f), {"source": "local", "filename": f.name}

        print("\n" + "=" * 60)
        print("  步骤 1/7: 获取视频")
        print("=" * 60)
        video_path, info = self.downloader.download(source, work_dir)
        print(f"  视频: {video_path}")
        if info.get("source") == "douyin":
            print(f"  抖音标题: {info.get('desc', '')[:50]}")
            print(f"  作者: {info.get('author', '')}")
        return video_path, info

    def _step_extract_audio(self, video_path: str, work_dir: str, source_url: str = "") -> str:
        """步骤2: 提取音频

        如果视频没有音频流，会尝试重新下载（TikTok 的 DASH 格式有时只下载了视频流）。
        """
        print("\n" + "=" * 60)
        print("  步骤 2/7: 提取音频")
        print("=" * 60)
        base_name = Path(video_path).stem
        audio_path = os.path.join(work_dir, f"{base_name}_audio.wav")

        # 检查视频是否有音频流
        if not self._video_has_audio(video_path):
            print(f"[pipeline] ⚠ 视频没有音频流，尝试修复...")
            if source_url and self._repair_video_audio(video_path, source_url):
                print(f"[pipeline] ✓ 视频音频修复成功")
            else:
                raise RuntimeError(
                    f"视频文件没有音频流且无法修复: {video_path}\n"
                    "请删除该工作目录并重新下载视频"
                )

        # 会话隔离后无需检查旧文件，直接提取
        extract_audio(video_path, audio_path, self.config)
        return audio_path

    def _video_has_audio(self, video_path: str) -> bool:
        """用 ffprobe 检查视频是否包含音频流"""
        try:
            probe_cmd = [
                self.config.ffprobe_path,
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1",
                video_path,
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            return "audio" in (result.stdout or "").lower()
        except Exception:
            return True  # 检查失败，假设有音频

    def _repair_video_audio(self, video_path: str, source_url: str) -> bool:
        """修复无音频的视频：重新下载或单独下载音频并合并

        返回 True 表示修复成功
        """
        # 检查是否为 TikTok 链接
        is_tiktok = any(pattern in source_url.lower() for pattern in ["tiktok.com", "vm.tiktok", "vt.tiktok"])

        # 策略1：重新下载（强制合并音视频）
        if is_tiktok:
            print(f"[pipeline] 重新下载视频（强制合并音视频）...")
            try:
                # 备份原文件
                backup_path = video_path + ".bak"
                if os.path.isfile(backup_path):
                    os.remove(backup_path)
                shutil.move(video_path, backup_path)

                # 重新下载
                self.downloader.youtube_parser._redownload_with_audio(source_url, video_path)

                # 检查结果
                if os.path.isfile(video_path) and self._video_has_audio(video_path):
                    size_mb = os.path.getsize(video_path) / 1024 / 1024
                    print(f"[pipeline] 重新下载成功: {size_mb:.1f}MB (含音频)")
                    # 删除备份
                    if os.path.isfile(backup_path):
                        os.remove(backup_path)
                    return True
                else:
                    print(f"[pipeline] 重新下载后仍无音频，尝试单独下载音频...")
                    # 恢复备份
                    if os.path.isfile(video_path):
                        os.remove(video_path)
                    if os.path.isfile(backup_path):
                        shutil.move(backup_path, video_path)
            except Exception as e:
                print(f"[pipeline] 重新下载失败: {e}")
                # 恢复备份
                if not os.path.isfile(video_path) and os.path.isfile(video_path + ".bak"):
                    shutil.move(video_path + ".bak", video_path)

        # 策略2：单独下载音频并用 ffmpeg 合并
        print(f"[pipeline] 单独下载音频流并合并...")
        try:
            self.downloader.youtube_parser._download_audio_and_merge(source_url, video_path)
            return self._video_has_audio(video_path)
        except Exception as e:
            print(f"[pipeline] 合并音频失败: {e}")
            return False

    def _step_transcribe(self, audio_path: str, source_lang: str,
                         work_dir: str, skip: bool) -> list:
        """步骤3: ASR 识别 + 智能断句合并"""
        print("\n" + "=" * 60)
        print("  步骤 3/7: 语音识别 (ASR)")
        print("=" * 60)

        segments_path = os.path.join(work_dir, "segments_asr.json")
        if skip and os.path.isfile(segments_path):
            print(f"  跳过 ASR，加载已有: {segments_path}")
            return load_segments(segments_path)

        transcriber = create_transcriber(self.config)
        segments = transcriber.transcribe(audio_path, source_lang)

        # 智能断句合并：ASR 常把一个完整句子切分成多个片段，
        # 按句末标点和时长阈值合并，让翻译和配音获得完整上下文
        from modules.segment_merger import merge_segments
        original_count = len(segments)
        segments = merge_segments(segments)
        merged_count = len(segments)
        if merged_count < original_count:
            print(f"[asr] 智能断句合并: {original_count} → {merged_count} 个片段（减少 {original_count - merged_count} 个过碎片段）")

        save_segments(segments, segments_path)
        return segments

    def _step_translate(self, segments: list, source_lang: str,
                        target_lang: str, work_dir: str, skip: bool) -> list:
        """步骤4: 翻译"""
        print("\n" + "=" * 60)
        print("  步骤 4/7: 翻译")
        print("=" * 60)

        segments_path = os.path.join(work_dir, f"segments_translated_{target_lang}.json")
        if skip and os.path.isfile(segments_path):
            print(f"  跳过翻译，加载已有: {segments_path}")
            return load_segments(segments_path)

        translator = create_translator(self.config)
        segments = translator.translate(segments, source_lang, target_lang)
        save_segments(segments, segments_path)
        return segments

    def _step_tts(self, segments: list, target_lang: str, tts_dir: str) -> list:
        """步骤6: TTS 配音"""
        print("\n" + "=" * 60)
        print("  步骤 6/7: TTS 配音")
        print("=" * 60)
        tts = create_tts_engine(self.config)
        return tts.synthesize(segments, target_lang, tts_dir)


def run_pipeline(source: str, target_lang: str, **kwargs):
    """便捷入口"""
    config = load_config()
    # CLI 参数覆盖
    for k, v in kwargs.items():
        if v is not None and hasattr(config, k):
            setattr(config, k, v)

    print(f"\n{'='*60}")
    print(f"  TransVideo 视频翻译配音")
    print(f"  配置: {config}")
    print(f"  源: {source[:50]}...")
    print(f"  目标语言: {target_lang}")
    print(f"{'='*60}\n")

    pipeline = TranslationPipeline(config)
    return pipeline.run(source, target_lang, **kwargs)
