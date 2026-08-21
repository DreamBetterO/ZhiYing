"""V6.1 EvidenceCorrectionOverlay v1：不可变 transcript 上的本地纠错覆盖层。

原则（目标合同 evidence_reconciliation）：
- 原始 transcript 不可变：overlay 只记录 raw → candidate，不修改 transcript Artifact；
- 下游读取 EffectiveEvidenceView = raw + accepted overlay；
- 低置信度（<0.9）候选进入 unresolved，不自动改写；
- 本地先检测领域高频近音/同音错误，云端只接收疑点窗口（CP61-4 后）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

EVIDENCE_OVERLAY_VERSION = 1

ACCEPTED_CONFIDENCE = 0.9


def transcript_digest(transcript: Mapping[str, Any]) -> str:
    """原始 transcript 的内容摘要（验证不可变性）。"""
    plain = json.dumps(transcript, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


# 数学领域高频近音/同音错误表（来自冻结高数样本）。
# (raw, candidate, confidence, context_pattern)；context_pattern 命中才检测。
_TERMINOLOGY_RULES: list[tuple[str, str, float, str | None]] = [
    ("便上线级分", "变上限积分", 0.95, None),
    ("不定级分", "不定积分", 0.95, None),
    ("圆寒数", "原函数", 0.95, None),
    ("简去", "减去", 0.90, None),
    ("长数", "常数", 0.90, None),
    ("级分", "积分", 0.90, None),
    ("寒数", "函数", 0.85, None),
    ("维分", "微分", 0.85, None),
    ("用算", "运算", 0.80, None),
    ("阶段", "间断", 0.70, r"第[一二两]类阶段"),
]


@dataclass(frozen=True)
class EvidenceCorrection:
    """单条纠错候选。"""

    correction_id: str
    raw_text: str
    candidate_text: str
    source_span: str  # segment_id
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    method: str = "local_terminology_rule"
    state: str = "unresolved"  # accepted | rejected | unresolved

    def __post_init__(self) -> None:
        if self.state not in {"accepted", "rejected", "unresolved"}:
            object.__setattr__(self, "state", "unresolved")
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "raw_text": self.raw_text,
            "candidate_text": self.candidate_text,
            "source_span": self.source_span,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "method": self.method,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceCorrection":
        return cls(
            correction_id=str(data.get("correction_id", "")),
            raw_text=str(data.get("raw_text", "")),
            candidate_text=str(data.get("candidate_text", "")),
            source_span=str(data.get("source_span", "")),
            evidence_refs=list(data.get("evidence_refs", [])),
            confidence=float(data.get("confidence", 0.0)),
            method=str(data.get("method", "local_terminology_rule")),
            state=str(data.get("state", "unresolved")),
        )


@dataclass
class EvidenceCorrectionOverlay:
    """纠错覆盖层：有效视图 = 原始文本 + accepted 修正。"""

    version: int = EVIDENCE_OVERLAY_VERSION
    transcript_digest: str = ""
    corrections: list[EvidenceCorrection] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.corrections = [row for row in self.corrections if isinstance(row, EvidenceCorrection)]

    def _accepted(self) -> list[EvidenceCorrection]:
        # 长 raw 优先，避免“寒数”先于“圆寒数”被替换导致部分改写错误
        return sorted(
            (row for row in self.corrections if row.state == "accepted"),
            key=lambda row: len(row.raw_text),
            reverse=True,
        )

    def apply_to(self, text: str) -> str:
        effective = text
        for row in self._accepted():
            effective = effective.replace(row.raw_text, row.candidate_text)
        return effective

    def apply_segment(self, text: str, segment_id: str) -> str:
        effective = text
        for row in self._accepted():
            if row.source_span == segment_id:
                effective = effective.replace(row.raw_text, row.candidate_text)
        return effective

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "transcript_digest": self.transcript_digest,
            "corrections": [row.to_dict() for row in self.corrections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceCorrectionOverlay":
        return cls(
            version=int(data.get("version", EVIDENCE_OVERLAY_VERSION)),
            transcript_digest=str(data.get("transcript_digest", "")),
            corrections=[EvidenceCorrection.from_dict(row) for row in data.get("corrections", []) if isinstance(row, dict)],
        )


def _correction_id(segment_id: str, raw: str) -> str:
    digest = hashlib.sha256(f"{segment_id}|{raw}".encode("utf-8")).hexdigest()[:12]
    return f"corr_{digest}"


def detect_local_corrections(transcript: Mapping[str, Any]) -> list[EvidenceCorrection]:
    """本地检测数学领域高频近音/同音错误，产出纠错候选。

    高置信度（>=0.9）→ accepted；低置信度 → unresolved（不自动改写）。
    只读 transcript，不修改任何字段。
    """
    corrections: list[EvidenceCorrection] = []
    seen: set[tuple[str, str]] = set()
    segments = transcript.get("segments", [])
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        segment_id = str(segment.get("segment_id", ""))
        text = str(segment.get("text", ""))
        for raw, candidate, confidence, context in _TERMINOLOGY_RULES:
            if context is not None:
                if not re.search(context, text):
                    continue
                matched = True
            else:
                matched = raw in text
            if not matched or (segment_id, raw) in seen:
                continue
            seen.add((segment_id, raw))
            corrections.append(EvidenceCorrection(
                correction_id=_correction_id(segment_id, raw),
                raw_text=raw,
                candidate_text=candidate,
                source_span=segment_id,
                evidence_refs=[segment_id],
                confidence=confidence,
                method="local_terminology_rule",
                state="accepted" if confidence >= ACCEPTED_CONFIDENCE else "unresolved",
            ))
    return corrections


def build_evidence_overlay(transcript: Mapping[str, Any]) -> EvidenceCorrectionOverlay:
    """构建覆盖层：digest + 本地检测候选（无云端）。"""
    return EvidenceCorrectionOverlay(
        version=EVIDENCE_OVERLAY_VERSION,
        transcript_digest=transcript_digest(transcript),
        corrections=detect_local_corrections(transcript),
    )


def apply_overlay_to_units(units: Iterable[Mapping[str, Any]], overlay: EvidenceCorrectionOverlay) -> list[dict[str, Any]]:
    """对知识单元文本字段应用有效视图（accepted 修正），返回新列表，不修改原对象。"""
    result: list[dict[str, Any]] = []
    for unit in units:
        row = dict(unit)
        for field_name in ("definition_or_conclusion",):
            if isinstance(row.get(field_name), str):
                row[field_name] = overlay.apply_to(row[field_name])
        result.append(row)
    return result
