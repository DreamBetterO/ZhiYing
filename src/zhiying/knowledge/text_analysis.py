from __future__ import annotations

import re
from collections import Counter


GENERIC_BIGRAMS = {
    "同学", "我们", "那么", "这个", "就是", "今天", "课程", "非常", "如果", "一个",
    "很多", "的话", "因为", "所以", "什么", "没有", "还是", "可以", "应该", "现在",
    "时候", "进行", "大家", "这里", "然后", "部分", "对于", "来看", "好吧", "相信",
}


def bigrams(text: str) -> set[str]:
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}


def keyword_counts(rows: list[dict]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(item for item in bigrams(row["text"]) if item not in GENERIC_BIGRAMS)
    return counts


def informativeness(text: str, counts: Counter[str]) -> float:
    compact = re.sub(r"\s+", "", text)
    score = min(len(compact), 90) / 24
    score += sum(0.55 for cue in ("是", "包括", "分为", "核心", "重点", "原因", "目的", "需要") if cue in compact)
    score += sum(min(counts[item] - 1, 3) * 0.12 for item in bigrams(compact) if counts[item] > 1)
    return score - (1.5 if len(compact) < 14 else 0.0)
