# -*- coding: utf-8 -*-
"""字幕智能合并模块

解决 ASR 过度切分导致配音不连贯的问题。

问题：
  Whisper/faster-whisper 按语音停顿切分，常把一个完整句子拆成多个片段。
  例如 "Space station." "GPS." "Can we zoom in?" 是三个独立片段，
  翻译后变成 "空间站。" "全球定位系统。" "我们能放大吗？"
  每个片段单独配音，导致中文配音支离破碎。

方案：
  按句末标点（. ! ? 。！？）和时长阈值智能合并相邻片段。
  - 不以句末标点结尾的片段 → 与下一个片段合并
  - 时长过短（< min_duration）的片段 → 与下一个片段合并
  - 合并后片段不超过 max_duration，避免单个片段过长

合并时机：
  在 ASR 之后、翻译之前合并原文片段。这样翻译能获得完整上下文，
  译文更准确，配音更连贯。
"""

import re
from typing import List

from modules.transcriber import Segment


# 句末标点（中英文）
SENTENCE_END_PUNCT = re.compile(r'[.!?。！？]\s*$')

# 句末标点（用于判断是否应该结束当前合并）
def _ends_with_sentence_punct(text: str) -> bool:
    """文本是否以句末标点结尾"""
    return bool(SENTENCE_END_PUNCT.search(text.strip()))


def merge_segments(segments: List[Segment],
                   min_duration: float = 1.5,
                   max_duration: float = 15.0,
                   max_gap: float = 1.5) -> List[Segment]:
    """智能合并 ASR 过度切分的片段

    合并规则（满足任一即合并当前片段到下一个）：
      1. 当前片段时长 < min_duration（过短）
      2. 当前片段不以句末标点结尾（句子未说完）
      3. 当前片段与下一个片段的间隔 < max_gap（停顿小，语义连贯）

    限制：
      - 合并后片段时长不超过 max_duration
      - 与下一个片段间隔超过 max_gap 时不合并（明显的段落分隔）
      - 原文不以句末标点结尾时强制合并（即使时长足够）

    Args:
        segments: ASR 识别的原始片段列表
        min_duration: 片段最小时长（秒），低于此值考虑合并
        max_duration: 合并后片段最大时长（秒），超过则不再合并
        max_gap: 相邻片段最大间隔（秒），超过则不合并

    Returns:
        合并后的片段列表
    """
    if not segments:
        return segments

    merged = []
    current = Segment(
        start=segments[0].start,
        end=segments[0].end,
        text=segments[0].text,
        translated_text=segments[0].translated_text,
    )

    for i in range(1, len(segments)):
        nxt = segments[i]
        gap = nxt.start - current.end

        # 判断是否应该结束当前合并，开始新片段
        should_end = False

        # 规则1：间隔太大，结束当前片段
        if gap > max_gap:
            should_end = True

        # 规则2：合并后时长超过上限，结束当前片段
        merged_duration = nxt.end - current.start
        if merged_duration > max_duration:
            should_end = True

        # 规则3：当前片段以句末标点结尾，且时长足够，结束当前片段
        if _ends_with_sentence_punct(current.text) and (current.end - current.start) >= min_duration:
            should_end = True

        if should_end:
            merged.append(current)
            current = Segment(
                start=nxt.start,
                end=nxt.end,
                text=nxt.text,
                translated_text=nxt.translated_text,
            )
        else:
            # 合并到当前片段
            current.end = nxt.end
            current.text = current.text + " " + nxt.text
            if current.translated_text and nxt.translated_text:
                current.translated_text = current.translated_text + " " + nxt.translated_text

    # 添加最后一个片段
    merged.append(current)

    return merged


def merge_translated_segments(segments: List[Segment],
                               min_duration: float = 1.5,
                               max_duration: float = 15.0,
                               max_gap: float = 1.5) -> List[Segment]:
    """智能合并已翻译的片段

    与 merge_segments 类似，但针对翻译后的中文片段优化。
    中文句末标点：。！？

    注意：这个函数在翻译之后调用，用于合并翻译后仍然过碎的片段。
    通常情况下，在翻译之前用 merge_segments 合并原文即可，
    翻译会自然获得完整上下文。这个函数作为补充。
    """
    return merge_segments(segments, min_duration, max_duration, max_gap)


# 简单测试
if __name__ == "__main__":
    import json

    # 加载实际的 ASR 结果测试
    asr_path = r"i:\TreaSpace\test\TransVideo\.work\20260719_135532_Introducing_Kimi_K3_Open_Frontier_Intel_\segments_asr.json"

    with open(asr_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    segments = [Segment(**d) for d in data]
    print(f"原始片段数: {len(segments)}")
    print()

    # 显示过短片段
    short_count = 0
    for i, seg in enumerate(segments):
        dur = seg.end - seg.start
        if dur < 2.0:
            short_count += 1
            print(f"  [{i+1}] {dur:.1f}s: {seg.text[:50]}")
    print(f"\n过短片段（<2s）: {short_count}/{len(segments)}")

    # 合并
    merged = merge_segments(segments)
    print(f"\n合并后片段数: {len(merged)}")
    print(f"减少: {len(segments) - len(merged)} 个片段")

    # 显示合并结果
    print("\n合并后片段:")
    for i, seg in enumerate(merged):
        dur = seg.end - seg.start
        print(f"  [{i+1}] {dur:.1f}s: {seg.text[:80]}")
