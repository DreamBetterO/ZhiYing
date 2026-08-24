"""Conservative deterministic de-duplication for canonical content blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .schema import KnowledgeUnit


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。；;：:、！？!?（）()\[\]【】]+", "", str(text)).lower()


def _block_texts(block: dict) -> list[str]:
    if block.get("items"):
        return [str(item).strip() for item in block.get("items", []) if str(item).strip()]
    text = str(block.get("text", "")).strip()
    return [text] if text else []


@dataclass(frozen=True)
class TitleValidation:
    valid: bool
    overlap_ratio: float = 0.0
    reason: str = ""
    replacement: str = ""


@dataclass
class DedupReport:
    duplicate_claim_count: int = 0
    containment_duplicate_count: int = 0
    title_body_overlap_count: int = 0
    near_duplicate_pairs: list[dict[str, str]] = field(default_factory=list)
    claims_without_source: int = 0

    def to_dict(self) -> dict:
        return {
            "duplicate_claim_count": self.duplicate_claim_count,
            "containment_duplicate_count": self.containment_duplicate_count,
            "title_body_overlap_count": self.title_body_overlap_count,
            "near_duplicate_pairs": list(self.near_duplicate_pairs),
            "claims_without_source": self.claims_without_source,
        }


def validate_title(title: str, body: str) -> TitleValidation:
    title = str(title).strip()
    normalized_title = _normalize(title)
    normalized_body = _normalize(body)[: max(1, len(normalized_title) * 2)]
    overlap = 0.0
    if normalized_title and normalized_body:
        overlap = SequenceMatcher(None, normalized_title, normalized_body).ratio()
        if normalized_body.startswith(normalized_title):
            overlap = 1.0
    invalid_start = re.match(r"^(?:这个|那个|它|所以|然后|来看|我们来看)", title) is not None
    invalid_end = re.search(r"(?:，|、|和|与|以及|然后)$", title) is not None
    valid = bool(title) and not invalid_start and not invalid_end and overlap <= 0.65
    reason = ""
    if not title:
        reason = "empty"
    elif invalid_start or invalid_end:
        reason = "fragment"
    elif overlap > 0.65:
        reason = "title_body_overlap"
    return TitleValidation(valid=valid, overlap_ratio=overlap, reason=reason)


def _replacement_title(unit: KnowledgeUnit) -> str:
    labels = {
        "rule": "规则与判断", "procedure": "操作步骤", "mechanism": "原理与机制",
        "comparison": "对比要点", "case": "课程案例", "boundary_case": "边界与例外",
        "visual_or_formula": "图示与公式", "conclusion": "课程结论", "concept": "核心概念",
    }
    suffix = re.sub(r"\D", "", unit.unit_id)[-4:] or "本节"
    return f"{labels.get(unit.type, '知识要点')}（{suffix}）"


def _dedup_block(block: dict, report: DedupReport) -> dict | None:
    result = dict(block)
    texts = _block_texts(result)
    if not texts:
        return result if result.get("type") in {"visual_group", "figure", "source_links"} else None
    unique: list[str] = []
    fingerprints: set[str] = set()
    for text in texts:
        fingerprint = _normalize(text)
        if fingerprint in fingerprints:
            report.duplicate_claim_count += 1
            continue
        fingerprints.add(fingerprint)
        unique.append(text)
    if result.get("items") is not None:
        if not unique:
            return None
        result["items"] = unique
    else:
        result["text"] = unique[0]
    return result


def _remove_sentence(paragraph: str, item: str) -> tuple[str, bool]:
    parts = re.split(r"([。；;！？!?])", paragraph)
    rebuilt: list[str] = []
    removed = False
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        punctuation = parts[index + 1] if index + 1 < len(parts) else ""
        if not removed and _normalize(sentence) == _normalize(item):
            removed = True
            continue
        rebuilt.extend((sentence, punctuation))
    return "".join(rebuilt).strip(), removed


def _has_sensitive_difference(left: str, right: str) -> bool:
    sensitive = re.compile(r"\d+(?:\.\d+)?%?|不|非|无|没有|除非|例外|条件|如果|只有|必须|时间")
    return set(sensitive.findall(left)) != set(sensitive.findall(right))


def run_dedup_gate(units: list[KnowledgeUnit]) -> tuple[list[KnowledgeUnit], DedupReport]:
    report = DedupReport()
    cleaned_units: list[KnowledgeUnit] = []
    for original in units:
        unit = KnowledgeUnit.from_dict(original.to_dict())
        blocks = [block for raw in unit.content_blocks if (block := _dedup_block(raw, report)) is not None]
        paragraphs = [block for block in blocks if block.get("type") == "paragraph" and block.get("text")]
        structured = [block for block in blocks if block.get("type") in {"rule_list", "steps", "example", "pitfall"}]
        for list_block in structured:
            kept_items: list[str] = []
            for item in list_block.get("items", []):
                duplicate = False
                for paragraph in paragraphs:
                    if _normalize(item) and _normalize(item) in _normalize(paragraph.get("text", "")):
                        shortened, removed = _remove_sentence(str(paragraph.get("text", "")), item)
                        if removed:
                            paragraph["text"] = shortened
                        else:
                            duplicate = True
                        report.containment_duplicate_count += 1
                        break
                if not duplicate:
                    kept_items.append(item)
            list_block["items"] = kept_items
        blocks = [block for block in blocks if block.get("type") in {"visual_group", "figure", "source_links"} or block.get("text") or block.get("items")]

        rows = [(block.get("block_id", ""), text) for block in blocks for text in _block_texts(block)]
        for index, (left_id, left) in enumerate(rows):
            for right_id, right in rows[index + 1:]:
                left_norm, right_norm = _normalize(left), _normalize(right)
                if not left_norm or left_norm == right_norm or _has_sensitive_difference(left, right):
                    continue
                if SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.88:
                    report.near_duplicate_pairs.append({"left": left_id, "right": right_id})

        body = " ".join(text for _, text in rows)
        title_validation = validate_title(unit.title, body)
        if not title_validation.valid:
            if title_validation.reason == "title_body_overlap":
                report.title_body_overlap_count += 1
            unit.title = _replacement_title(unit)
        for block in blocks:
            if block.get("origin") not in {"model_aid", "visual_backed"} and not block.get("source_ids") and unit.evidence_refs:
                block["source_ids"] = list(dict.fromkeys(
                    source_id for ref in unit.evidence_refs for source_id in ref.get("source_ids", [])
                ))
            if block.get("origin") == "audio_backed" and not block.get("source_ids") and not unit.evidence_refs:
                report.claims_without_source += len(_block_texts(block))
        unit.content_blocks = blocks
        cleaned_units.append(unit)
    return cleaned_units, report
