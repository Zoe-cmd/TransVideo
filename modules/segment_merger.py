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


# 句末标点（含其后可能的闭合引号/括号/空白）。
# 小数点不算句末：点号两侧都是数字时（如 2.8、3.5）不匹配。
_SENTENCE_BOUNDARY_RE = re.compile(
    r'(?:(?<!\d)\.(?!\d)|[!?。！？])["\'”’\)）\]】»]*\s*')


def rebalance_segments(segments: List[Segment],
                       min_side_duration: float = 0.4):
    """句读边界对齐：让每个片段都以完整句子结束

    merge_segments 受 max_duration 限制，长句子依然会被切在句中
    （如 "...预测一个" / "分数。这里..."），导致字幕读着不连贯、
    翻译模型拿到半句话还容易擅自合并条目（引发整批错位）。

    本函数在合并之后做两遍边界对齐：
      第一遍（正向）：片段最后一个句末标点之后的残句 → 挪到下一片段开头
      第二遍（反向）：整段无句末标点（whisper 漏标点）→ 从下一片段
                     开头"借"回这句的后半截，补全本段
      - 时间边界按字符占比同步移动，文字始终落在它被说出的时间窗内
      - 任一侧不足 min_side_duration 秒时放弃挪动（避免一闪而过的片段）

    片段总数不变。返回 (segments, moved_count)。
    """
    if len(segments) < 2:
        return segments, 0

    moved = 0
    for i in range(len(segments) - 1):
        text = segments[i].text.strip()
        # 找最后一个句末标点的结束位置
        last_end = 0
        for m in _SENTENCE_BOUNDARY_RE.finditer(text):
            last_end = m.end()
        if last_end <= 0 or last_end >= len(text):
            continue  # 无句末标点，或本来就以句末标点结尾

        head, tail = text[:last_end].strip(), text[last_end:].strip()
        if not head or not tail:
            continue

        # 按字符占比移动时间边界
        seg_dur = segments[i].end - segments[i].start
        ratio = len(head) / len(text)
        boundary = segments[i].start + seg_dur * ratio

        # 两侧窗口都不能太短
        if boundary - segments[i].start < min_side_duration:
            continue
        if segments[i].end - boundary < min_side_duration:
            continue

        segments[i].text = head
        segments[i].end = boundary
        next_text = segments[i + 1].text.strip()
        segments[i + 1].text = f"{tail} {next_text}" if next_text else tail
        segments[i + 1].start = boundary
        moved += 1

    # 第二遍（反向补全）：整段没有任何句末标点时（whisper 偶尔整段漏标点，
    # 如 "...to predict one" / "score for each expert." 被切成两段），
    # 从下一片段开头"借"回这句的后半截，让本段以完整句子结束。
    # 时间边界同样按字符占比同步移动（借来的文字在下一片段窗口的开头说出，
    # 所以本段窗口向右延伸、下一片段从边界处开始）。
    for i in range(len(segments) - 1):
        if _ends_with_sentence_punct(segments[i].text):
            continue  # 已是完整句子（或第一遍已修好）
        next_text = segments[i + 1].text.strip()
        m = _SENTENCE_BOUNDARY_RE.search(next_text)
        if not m:
            continue  # 下一片段也没有句末标点，无法补全
        prefix, rest = next_text[:m.end()].strip(), next_text[m.end():].strip()
        if not prefix or not rest:
            continue  # 不能把下一片段借空

        next_dur = segments[i + 1].end - segments[i + 1].start
        ratio = len(prefix) / len(next_text)
        if ratio > 0.6:
            continue  # 借得太多会毁掉下一片段，放弃
        boundary = segments[i + 1].start + next_dur * ratio
        if segments[i + 1].end - boundary < min_side_duration:
            continue

        segments[i].text = segments[i].text.strip() + " " + prefix
        segments[i].end = boundary
        segments[i + 1].text = rest
        segments[i + 1].start = boundary
        moved += 1

    return segments, moved


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
