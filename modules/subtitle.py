# -*- coding: utf-8 -*-
"""字幕生成模块 —— ASS 电影/电视剧样式 + SRT 兼容格式

字幕样式：
  - 单语模式：底部居中白字黑描边（电影标准样式）
  - 双语模式：下方译文 + 上方原文（学习对照）
  - 时间严格对齐 ASR 片段时间戳
"""

import os
from typing import List

from config import Config
from modules.transcriber import Segment


def _format_ass_time(seconds: float) -> str:
    """秒 → ASS 时间格式 H:MM:SS.cc"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds * 100) % 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _format_srt_time(seconds: float) -> str:
    """秒 → SRT 时间格式 HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_ass_text(text: str) -> str:
    """转义 ASS 特殊字符"""
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    text = text.replace("\n", "\\N")
    return text


def _escape_ass_with_newline(text: str) -> str:
    """转义 ASS 特殊字符，同时保留 _wrap_text 生成的 \\N 换行标记

    _wrap_text 返回的字符串中包含字面量 \\N（反斜杠+N 两个字符）作为 ASS 换行标记。
    直接转义反斜杠会把 \\N 变成 \\\\N，破坏换行标记。
    本函数用占位符策略正确处理：先保护 \\N，再转义其他字符，最后还原。
    """
    # 用特殊占位符保护 \N 换行标记（使用 unlikely-to-occur 的字符序列）
    NEWLINE_PLACEHOLDER = "\x00NEWLINE\x00"
    text = text.replace("\\N", NEWLINE_PLACEHOLDER)
    # 转义反斜杠、花括号
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    # 还原换行标记
    text = text.replace(NEWLINE_PLACEHOLDER, "\\N")
    return text


def _estimate_text_width(text: str, fontsize: int) -> float:
    """估算文本像素宽度（基于字号粗略估算）
    
    西文字符宽度约为字号的 0.5 倍，中文字符宽度约为字号的 1.0 倍。
    这是一个近似估算，实际宽度取决于字体，但足以用于换行判断。
    """
    width = 0.0
    for ch in text:
        # 中日韩字符（宽字符）
        if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' or '\uac00' <= ch <= '\ud7af':
            width += fontsize * 1.0
        elif ch in ' \t':
            width += fontsize * 0.25
        elif ch.isupper():
            width += fontsize * 0.7
        else:
            width += fontsize * 0.5
    return width


def _wrap_text_lines(text: str, video_width: int, fontsize: int,
                     max_width_percent: float) -> List[str]:
    """按词换行，返回完整行列表（不截断）
    
    策略：
    - 西文按空格分词，中文按字符分词
    - 贪心填充每行，达到宽度阈值就换行
    """
    max_width = video_width * max_width_percent

    # 中文/日文：按字符拆分（CJK 无空格分词）
    has_cjk = any('\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff' for ch in text)

    if has_cjk:
        # CJK：按词切分（连续中文/连续非中文）
        import re
        tokens = re.findall(r'[\u4e00-\u9fff\u3040-\u30ff]+|[^\u4e00-\u9fff\u3040-\u30ff]+', text)
    else:
        # 西文：按空格分词
        tokens = text.split(' ')
        tokens = [t if i == 0 else ' ' + t for i, t in enumerate(tokens)]

    lines = []
    current_line = ""
    current_width = 0.0

    # 不允许出现在行首的标点（避头尾规则：标点并回上一行，允许略微超宽）
    no_line_start_punct = '。，、！？；：）】」』,.!?;:)]}'

    for token in tokens:
        token_width = _estimate_text_width(token, fontsize)

        if current_width + token_width <= max_width or not current_line:
            current_line += token
            current_width += token_width
        else:
            stripped = token.strip()
            if stripped and all(ch in no_line_start_punct for ch in stripped):
                # 纯标点 token：并回当前行末尾，不换行
                current_line += token
                current_width += token_width
                continue
            lines.append(current_line)
            current_line = token.lstrip() if not has_cjk else token
            current_width = _estimate_text_width(current_line, fontsize)

    if current_line:
        lines.append(current_line)

    return lines


def _wrap_text(text: str, video_width: int, fontsize: int,
               max_width_percent: float, max_lines: int) -> str:
    """按词换行：超过视频宽度 * max_width_percent 时换行，最多 max_lines 行（超出截断）"""
    lines = _wrap_text_lines(text, video_width, fontsize, max_width_percent)
    # 限制最大行数
    if len(lines) > max_lines:
        lines = lines[:max_lines]

    return "\\N".join(lines)


def _split_events(text: str, start: float, end: float, video_width: int,
                  fontsize: int, max_width_percent: float, max_lines: int,
                  mode: str = "split") -> List[tuple]:
    """把一个字幕片段拆成若干条显示事件 [(start, end, wrapped_text), ...]

    mode=split（自适应，默认）：
      文本换行后超过 max_lines 行时，拆成多条事件依次显示——
      每条最多 max_lines 行、宽度不超阈值，字号不变，一个字都不丢。
      各条的显示时长按文字渲染宽度占比分配，与语音节奏大致同步。
      max_lines=1 时即为"单行滚动字幕"效果。
    mode=truncate：
      旧行为，一条事件显示整个片段，超出 max_lines 行的部分直接丢弃。
    """
    lines = _wrap_text_lines(text, video_width, fontsize, max_width_percent)

    if mode != "split" or len(lines) <= max_lines:
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        return [(start, end, "\\N".join(lines))]

    chunks = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]
    weights = [sum(_estimate_text_width(line, fontsize) for line in chunk)
               for chunk in chunks]
    total_weight = sum(weights) or 1.0
    duration = max(end - start, 0.0)

    events = []
    t = start
    acc = 0.0
    for i, (chunk, w) in enumerate(zip(chunks, weights)):
        acc += w
        # 最后一条对齐片段结尾，避免浮点误差漏尾
        t_end = end if i == len(chunks) - 1 else start + duration * acc / total_weight
        events.append((t, t_end, "\\N".join(chunk)))
        t = t_end
    return events


class SubtitleGenerator:
    """字幕生成器"""

    def __init__(self, config: Config):
        self.config = config

    def _build_ass_header(self, width: int = 1920, height: int = 1080) -> str:
        """构建 ASS 文件头（含样式定义）

        字幕样式：黑底白字（YouTube 风格）
        - BorderStyle=4: 方框背景（Outline 颜色作为背景填充色）
        - Outline=2: 背景框内边距
        - 白色文字 + 黑色背景框，无需检测原视频字幕位置即可清晰可见
        """
        cfg = self.config

        # ASS 颜色格式：&HAABBGGRR（AA=透明度 00=不透明, FF=透明）
        # PrimaryColour: 白字 &H00FFFFFF
        # OutlineColour: 黑色背景框 &H00000000（BorderStyle=4 时作为背景填充色）

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.subtitle_font},{cfg.subtitle_fontsize},{cfg.subtitle_primary_color},&H000000FF,{cfg.subtitle_outline_color},&H80000000,-1,0,0,0,100,100,0,0,4,{cfg.subtitle_outline_width},1,2,30,30,{cfg.subtitle_margin_v},1
"""

        if cfg.subtitle_style == "dual":
            # 双语：上方原文（较小灰色，同样黑底）
            secondary_size = max(cfg.subtitle_fontsize - 4, 12)
            header += f"Style: Secondary,{cfg.subtitle_font},{secondary_size},&H00AAAAAA,&H000000FF,{cfg.subtitle_outline_color},&H80000000,0,0,0,0,100,100,0,0,4,{cfg.subtitle_outline_width},1,2,30,30,{cfg.subtitle_margin_v + 35},1\n"

        header += "\n[Events]\n"
        header += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        return header

    def generate_ass(self, segments: List[Segment], output_path: str,
                     video_width: int = 1920, video_height: int = 1080) -> str:
        """生成 ASS 字幕文件（电影样式 + 智能换行）"""
        cfg = self.config

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self._build_ass_header(video_width, video_height))

            for seg in segments:
                translated_raw = seg.translated_text or seg.text

                # 自适应拆分：超长文本拆成多条事件依次显示，不丢字
                translated_events = _split_events(
                    translated_raw, seg.start, seg.end, video_width,
                    cfg.subtitle_fontsize, cfg.subtitle_max_width_percent,
                    cfg.subtitle_max_lines, cfg.subtitle_overflow_mode,
                )
                for ev_start, ev_end, ev_text in translated_events:
                    # 转义 ASS 特殊字符（保护 \N 换行标记）
                    ev_text = _escape_ass_with_newline(ev_text)
                    # 主字幕（译文）layer 0
                    f.write(f"Dialogue: 0,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},Default,,0,0,{cfg.subtitle_margin_v},,{ev_text}\n")

                # 双语模式：原文 layer 1
                if cfg.subtitle_style == "dual" and seg.text:
                    original_events = _split_events(
                        seg.text, seg.start, seg.end, video_width,
                        max(cfg.subtitle_fontsize - 4, 12),
                        cfg.subtitle_max_width_percent,
                        cfg.subtitle_max_lines, cfg.subtitle_overflow_mode,
                    )
                    for ev_start, ev_end, ev_text in original_events:
                        ev_text = _escape_ass_with_newline(ev_text)
                        f.write(f"Dialogue: 1,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},Secondary,,0,0,{cfg.subtitle_margin_v + 35},,{ev_text}\n")

        print(f"[subtitle] ASS 字幕已生成: {output_path} (字号 {cfg.subtitle_fontsize}, 换行阈值 {cfg.subtitle_max_width_percent:.0%})")
        return output_path

    def generate_srt(self, segments: List[Segment], output_path: str,
                     use_translated: bool = True, video_width: int = 1920) -> str:
        """生成 SRT 字幕文件（带智能换行）"""
        cfg = self.config
        idx = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for seg in segments:
                text = (seg.translated_text if use_translated else seg.text) or seg.text

                # 自适应拆分：超长文本拆成多条事件依次显示，不丢字
                events = _split_events(
                    text, seg.start, seg.end, video_width,
                    cfg.subtitle_fontsize, cfg.subtitle_max_width_percent,
                    cfg.subtitle_max_lines, cfg.subtitle_overflow_mode,
                )
                for ev_start, ev_end, ev_text in events:
                    idx += 1
                    start = _format_srt_time(ev_start)
                    end = _format_srt_time(ev_end)
                    # 将 ASS 换行标记转为 SRT 换行
                    wrapped = ev_text.replace("\\N", "\n")

                    f.write(f"{idx}\n")
                    f.write(f"{start} --> {end}\n")
                    f.write(f"{wrapped}\n\n")

        print(f"[subtitle] SRT 字幕已生成: {output_path}")
        return output_path

    def generate_dual_srt(self, segments: List[Segment], output_path: str) -> str:
        """生成双语 SRT（译文在上，原文在下）"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                start = _format_srt_time(seg.start)
                end = _format_srt_time(seg.end)
                translated = seg.translated_text or seg.text
                original = seg.text

                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{translated}\n{original}\n\n")

        print(f"[subtitle] 双语 SRT 已生成: {output_path}")
        return output_path
