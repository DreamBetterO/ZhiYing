"""V2.1 视觉问题驱动的候选帧召回与 fallback 视觉证据。

默认实现不调用云端模型、不下载本地 VLM。它先在课程级建立近重复场景，
再按视觉问题召回少量 canonical frame。时间只作为召回弱先验；没有 OCR
或 VLM 可见证据时必须 no_match。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from ..utils import ensure_not_cancelled, hhmmss
from ..progress import ProgressEvent
from .schema import LessonPlan, VisualEvidence, VisualNeed, VisualQuestion
from .planning import collect_visual_jobs, validate_visual_question
from ..execution.adapters.vision import (
    VisualModelProvider,
    VisualProviderError,
    VisualProviderOOMError,
    create_ocr_provider,
)
from .visuals import cluster_visual_scenes

_VISUAL_EVIDENCE_VERSION = 11
_VISUAL_SOURCE_VERSION = "3.0"

_TRANSIENT_VISUAL_SOURCES = {
    "vlm_provider_error",
    "vlm_oom_no_match",
    "vlm_detail_failed",
}

_VLM_FAILURE_SOURCES = _TRANSIENT_VISUAL_SOURCES | {
    "vlm_invalid_candidate_id",
}

_VLM_BATCH_FAILURE_SOURCES = {
    "vlm_provider_error",
    "vlm_oom_no_match",
}


def is_vlm_failure_source(source: str) -> bool:
    """只有推理未完成或结果违反接口契约才属于 VLM 降级。

    候选不足、模型主动拒绝、证据门槛未满足和全局去重都是正常的
    no_match 决策，不能触发任务级降级通知。
    """
    return str(source).strip() in _VLM_FAILURE_SOURCES

_VISUAL_TERMS = (
    "K线", "均线", "中枢线", "重叠区间", "反穿", "走势图", "流程图",
    "公式", "表格", "界面", "按钮", "菜单", "坐标", "边界", "区间",
)


def _candidate_index(path: Path) -> int:
    match = re.search(r"candidate_(\d+)", path.stem)
    return max(0, int(match.group(1)) - 1) if match else 0


def _image_sha256(path: str) -> str:
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _content_score(path: str) -> float:
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as image:
            gray = image.convert("L")
            width, height = gray.size
            crop = gray.crop((int(width * 0.10), int(height * 0.12), int(width * 0.90), int(height * 0.88)))
            stat = ImageStat.Stat(crop)
            return float(stat.stddev[0]) / 64.0
    except Exception:
        return 0.0


def _all_visual_questions(lesson_plan: LessonPlan) -> list[VisualQuestion]:
    questions: list[VisualQuestion] = []
    for unit in lesson_plan.all_unit_plans:
        questions.extend(unit.visual_questions)
    return questions


def _visual_contracts(lesson_plan: LessonPlan) -> dict[str, VisualNeed]:
    return {unit.plan_id: unit.visual_need for unit in lesson_plan.all_unit_plans if unit.plan_id}


def _candidate_rows(work_dir: Path, frames: dict) -> list[dict]:
    images_dir = work_dir / "images"
    candidates_dir = images_dir / "candidates"
    interval = float(frames.get("sample_interval_seconds", 10.0) or 10.0)
    rows: list[dict] = []
    if candidates_dir.is_dir():
        for path in sorted(candidates_dir.glob("candidate_*.jpg")):
            index = _candidate_index(path)
            timestamp = round(index * interval, 3)
            rows.append({
                "image_id": path.stem,
                "timestamp_seconds": timestamp,
                "timestamp_label": hhmmss(timestamp),
                "path": str(path.resolve()),
                "content_score": round(_content_score(str(path)), 4),
            })
    if not rows:
        for frame in (frames.get("candidates") or frames.get("frames") or []):
            row = dict(frame)
            path = str(row.get("path") or row.get("file") or "")
            row["image_id"] = str(row.get("image_id") or row.get("candidate_id") or Path(path).stem)
            row["path"] = path
            row["timestamp_seconds"] = float(row.get("timestamp_seconds", 0.0) or 0.0)
            row["timestamp_label"] = str(row.get("timestamp_label") or hhmmss(row["timestamp_seconds"]))
            row["content_score"] = float(row.get("content_score", _content_score(path)) or 0.0)
            rows.append(row)
    return rows


def _window_for_question(question: VisualQuestion) -> tuple[float, float] | None:
    spans = question.anchor_spans
    if not spans:
        return None
    start = min(span.start_seconds for span in spans)
    end = max(span.end_seconds for span in spans)
    lower = max(0.0, start - 15.0)
    upper = min(max(lower + 1.0, end + 30.0), lower + 120.0)
    return lower, upper


def recall_candidates_for_question(
    question: VisualQuestion,
    candidates: list[dict],
    max_candidates: int = 6,
) -> list[dict]:
    """按内容质量和弱时间先验召回少量场景 canonical frame。"""
    if candidates and not all(row.get("scene_cluster_id") for row in candidates):
        candidates = cluster_visual_scenes(candidates)
    canonical_rows = [row for row in candidates if row.get("is_canonical", True)]
    window = _window_for_question(question)
    rows: list[dict] = []
    for candidate in canonical_rows:
        row = dict(candidate)
        timestamp = float(row.get("timestamp_seconds", 0.0))
        if window:
            lower, upper = window
            if timestamp < lower:
                time_distance = lower - timestamp
            elif timestamp > upper:
                time_distance = timestamp - upper
            else:
                time_distance = 0.0
            time_prior = 1.0 / (1.0 + time_distance / 60.0)
        else:
            time_prior = 0.0
        content_score = max(0.0, min(1.5, float(row.get("content_score", 0.0))))
        row["time_prior"] = round(time_prior, 4)
        row["recall_score"] = round(content_score * 0.8 + time_prior * 0.2, 4)
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -float(row.get("recall_score", 0.0)),
            -float(row.get("content_score", 0.0)),
            float(row.get("timestamp_seconds", 0.0)),
        )
    )
    return rows[:max(1, max_candidates)]


def _question_terms(question: VisualQuestion) -> list[str]:
    terms: list[str] = []
    for value in [*question.expected_entities, question.expected_relation]:
        for term in re.split(r"[\s,，、;；:/]+", str(value)):
            cleaned = term.strip()
            if len(cleaned) >= 2 and cleaned not in terms:
                terms.append(cleaned)
    for term in _VISUAL_TERMS:
        if term in question.question and term not in terms:
            terms.append(term)
    return terms


def _ocr_hits(question: VisualQuestion, ocr_text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", ocr_text).lower()
    return [term for term in _question_terms(question) if re.sub(r"\s+", "", term).lower() in normalized]


def _criterion_satisfied(criterion: str, hits: list[str], ocr_text: str) -> bool:
    normalized_criterion = re.sub(r"\s+", "", criterion).lower()
    normalized_ocr = re.sub(r"\s+", "", ocr_text).lower()
    for hit in hits:
        normalized_hit = re.sub(r"\s+", "", hit).lower()
        if normalized_hit in normalized_criterion or normalized_hit[:2] in normalized_criterion:
            return True
    explicit_terms = [term for term in _VISUAL_TERMS if term in criterion]
    return bool(explicit_terms) and all(term.lower() in normalized_ocr for term in explicit_terms)


def _targeted_ocr_rerank(
    question: VisualQuestion,
    candidate_rows: list[dict],
    ocr_provider: Callable[[str], str] | None,
    ocr_cache: dict[str, str],
) -> list[dict]:
    """OCR 只处理问题已召回的 canonical frame，再参与内容优先重排。"""
    rows: list[dict] = []
    for candidate in candidate_rows:
        row = dict(candidate)
        path = str(row.get("path", ""))
        ocr_text = ""
        if ocr_provider and path:
            if path not in ocr_cache:
                try:
                    ocr_cache[path] = str(ocr_provider(path) or "").strip()
                except Exception:
                    ocr_cache[path] = ""
            ocr_text = ocr_cache[path]
        hits = _ocr_hits(question, ocr_text)
        role = question.preferred_visual_role
        scene_score = 1.0 if (
            (role == "procedure" and any(term in ocr_text for term in ("步骤", "菜单", "按钮", "流程")))
            or (role == "compare" and any(term in ocr_text for term in ("对比", "区别", "差异")))
            or bool(hits)
        ) else 0.0
        hit_score = min(1.0, len(hits) / max(1, len(_question_terms(question))))
        row["ocr_text"] = ocr_text
        row["ocr_hits"] = hits
        row["scene_type_score"] = scene_score
        row["semantic_recall_score"] = round(
            hit_score * 0.55
            + max(0.0, min(1.0, float(row.get("content_score", 0.0)))) * 0.20
            + scene_score * 0.15
            + float(row.get("time_prior", 0.0)) * 0.10,
            4,
        )
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -float(row.get("semantic_recall_score", row.get("recall_score", 0.0))),
            -len(row.get("ocr_hits", [])),
            float(row.get("timestamp_seconds", 0.0)),
        )
    )
    return rows


def _fallback_select(
    question: VisualQuestion,
    candidate_rows: list[dict],
    contract: VisualNeed,
    capability_warning: str = "",
) -> VisualEvidence:
    if not candidate_rows:
        return VisualEvidence(
            evidence_id=f"ve_{question.question_id}",
            question_id=question.question_id,
            matched_knowledge_point_id=question.unit_id,
            decision="no_match",
            source="fallback_no_candidate",
            visual_role=contract.role,
            sequence_mode=contract.sequence_mode,
            visual_group_id=f"vg_{question.unit_id}",
            capability_warning=capability_warning,
        )
    if not any(str(row.get("ocr_text", "")).strip() for row in candidate_rows):
        return VisualEvidence(
            evidence_id=f"ve_{question.question_id}",
            question_id=question.question_id,
            matched_knowledge_point_id=question.unit_id,
            matched_knowledge_id=question.unit_id,
            decision="no_match",
            source="fallback_no_pixel_evidence",
            match_reason="未启用 OCR/VLM；时间和附近语音不能构成图片证据",
            candidate_count=len(candidate_rows),
            visual_role=contract.role,
            criteria_missing=list(contract.success_criteria),
            sequence_mode=contract.sequence_mode,
            visual_group_id=f"vg_{question.unit_id}",
            capability_warning=capability_warning,
        )

    scored_rows: list[tuple[float, dict, str, list[str], list[str], list[str]]] = []
    for row in candidate_rows:
        ocr_text = str(row.get("ocr_text", "")).strip()
        hits = list(row.get("ocr_hits", [])) or _ocr_hits(question, ocr_text)
        if not hits:
            continue
        criteria_met = [
            criterion for criterion in contract.success_criteria
            if _criterion_satisfied(criterion, hits, ocr_text)
        ]
        criteria_missing = [
            criterion for criterion in contract.success_criteria
            if criterion not in criteria_met
        ]
        score = min(
            0.72,
            0.45 + min(0.15, len(hits) * 0.05) + min(0.12, float(row.get("content_score", 0.0)) * 0.08),
        )
        scored_rows.append((score, row, ocr_text, hits, criteria_met, criteria_missing))

    if not scored_rows:
        return VisualEvidence(
            evidence_id=f"ve_{question.question_id}",
            question_id=question.question_id,
            matched_knowledge_point_id=question.unit_id,
            matched_knowledge_id=question.unit_id,
            decision="no_match",
            source="fallback_ocr_no_match",
            match_reason="候选 OCR 未命中视觉问题中的对象或关系",
            candidate_count=len(candidate_rows),
            visual_role=contract.role,
            criteria_missing=list(contract.success_criteria),
            sequence_mode=contract.sequence_mode,
            visual_group_id=f"vg_{question.unit_id}",
            capability_warning=capability_warning,
        )

    score, selected, ocr_text, hits, criteria_met, criteria_missing = max(
        scored_rows,
        key=lambda item: (-len(item[5]), item[0], float(item[1].get("semantic_recall_score", 0.0))),
    )
    if criteria_missing:
        return VisualEvidence(
            evidence_id=f"ve_{question.question_id}",
            question_id=question.question_id,
            matched_knowledge_point_id=question.unit_id,
            matched_knowledge_id=question.unit_id,
            frame_id=str(selected.get("image_id", "")),
            ocr_text=ocr_text,
            visual_role=contract.role,
            criteria_met=criteria_met,
            criteria_missing=criteria_missing,
            sequence_mode=contract.sequence_mode,
            visual_group_id=f"vg_{question.unit_id}",
            decision="no_match",
            source="fallback_ocr_criteria_missing",
            match_reason="OCR 命中对象，但不足以证明全部视觉成功条件",
            candidate_count=len(candidate_rows),
            capability_warning=capability_warning,
        )
    path = str(selected.get("path", ""))
    timestamp = float(selected.get("timestamp_seconds", 0.0))
    match_reason = f"OCR 命中视觉问题关键词：{'、'.join(hits)}"
    return VisualEvidence(
        evidence_id=f"ve_{question.question_id}",
        question_id=question.question_id,
        image_path=path,
        timestamp=timestamp,
        ocr_text=ocr_text,
        visual_summary=f"候选帧 OCR 提取到：{ocr_text[:120]}",
        matched_knowledge_point_id=question.unit_id,
        matched_knowledge_id=question.unit_id,
        relevance_score=score,
        why_useful=f"用于核对视觉问题：{question.question}",
        match_reason=match_reason,
        suggested_caption=f"{question.question}（{selected.get('timestamp_label', hhmmss(timestamp))}）",
        explanation_for_reader=(
            f"这张图的可见文字命中“{'、'.join(hits)}”；它只支持定位这些对象，"
            "不替代正文中的课堂判断。"
        ),
        frame_id=str(selected.get("image_id", "")),
        source_timestamp=timestamp,
        dedup_group_id=str(selected.get("dedup_group_id", "")),
        scene_cluster_id=str(selected.get("scene_cluster_id", "")),
        image_sha256=str(selected.get("image_sha256", "")),
        perceptual_hash=str(selected.get("perceptual_hash", "")),
        visible_evidence=[f"OCR 命中：{term}" for term in hits],
        visual_role=contract.role,
        criteria_met=criteria_met or [f"OCR 命中：{term}" for term in hits],
        criteria_missing=[],
        visual_answer=f"画面文字可定位：{'、'.join(hits)}",
        sequence_mode=contract.sequence_mode,
        visual_group_id=f"vg_{question.unit_id}",
        capability_warning=capability_warning,
        decision="select",
        confidence=score,
        source="ocr_content_fallback",
        candidate_count=len(candidate_rows),
    )


def _no_match_from_provider(
    question: VisualQuestion,
    contract: VisualNeed,
    source: str,
    reason: str,
    candidate_count: int,
    warning: str = "",
    visible_evidence: list[str] | None = None,
    criteria_met: list[str] | None = None,
    criteria_missing: list[str] | None = None,
    visual_answer: str = "",
) -> VisualEvidence:
    return VisualEvidence(
        evidence_id=f"ve_{question.question_id}",
        question_id=question.question_id,
        matched_knowledge_point_id=question.unit_id,
        matched_knowledge_id=question.unit_id,
        visual_role=contract.role,
        visible_evidence=list(visible_evidence or []),
        criteria_met=list(criteria_met or []),
        criteria_missing=list(contract.success_criteria) if criteria_missing is None else list(criteria_missing),
        visual_answer=visual_answer,
        sequence_mode=contract.sequence_mode,
        visual_group_id=f"vg_{question.unit_id}",
        decision="no_match",
        source=source,
        match_reason=reason,
        candidate_count=candidate_count,
        capability_warning=warning,
    )


def _actual_visible_evidence(items: list[str], criteria: list[str]) -> list[str]:
    criteria_text = {str(item).strip() for item in criteria if str(item).strip()}
    placeholders = {"像素中明确可见的事实", "图片直接回答了什么"}
    rows: list[str] = []
    for item in items:
        text = str(item).strip()
        if (
            not text
            or text in criteria_text
            or text in placeholders
            or _reader_unsafe_visual_text(text)
        ):
            continue
        rows.append(text)
    return rows


def _criteria_supported_by_visible_evidence(criteria: list[str], evidence: list[str]) -> list[str]:
    """仅用模型已报告的像素事实补齐合同勾选，不生成任何新事实。"""
    combined = "；".join(evidence).casefold()
    if not combined:
        return []
    entity_terms = (
        "K线", "均线", "中枢线", "重叠区间", "反穿", "流程", "公式", "表格",
        "界面", "按钮", "菜单", "输入", "输出", "边界",
    )
    relation_cues = (
        "上方", "下方", "左侧", "右侧", "之间", "位置", "相对", "连接", "指向",
        "穿过", "反穿", "重叠", "交叉", "线条", "形态", "箭头", "边界",
    )
    supported: list[str] = []
    for criterion in criteria:
        required_entities = [term for term in entity_terms if term.casefold() in criterion.casefold()]
        if required_entities and not all(term.casefold() in combined for term in required_entities):
            continue
        relation_required = any(term in criterion for term in ("关系", "相对位置", "线条", "连接", "边界"))
        if relation_required and not any(cue.casefold() in combined for cue in relation_cues):
            continue
        if required_entities:
            supported.append(criterion)
    return supported


def _reader_unsafe_visual_text(text: str) -> bool:
    value = str(text).strip()
    if re.search(r"\bcandidate_\d+\b", value, flags=re.IGNORECASE):
        return True
    if "000000" in value:
        return True
    if value.startswith("画面中可辨认") and "可同时辨认" in value:
        return True
    return False


def _visual_answer_as_evidence(answer: str, criteria: list[str]) -> str:
    text = str(answer).strip()
    if (
        not text
        or re.fullmatch(r"(?:候选|图片)?\s*[A-D](?:\s*图)?", text, flags=re.IGNORECASE)
        or text in {str(item).strip() for item in criteria}
        or _reader_unsafe_visual_text(text)
    ):
        return ""
    visual_cues = (
        "图", "画面", "标注", "箭头", "区域", "K线", "柱状", "红色", "蓝色",
        "线条", "横线", "竖线", "框", "价格轴", "坐标",
    )
    return text if any(cue in text for cue in visual_cues) else ""


def sanitize_visual_evidence_for_reader(item: VisualEvidence) -> VisualEvidence:
    """移除 A/B/C/D 等候选别名，改用模型已经报告的像素事实。"""
    answer = str(item.visual_answer or item.visual_summary).strip()
    if not re.fullmatch(r"(?:候选|图片)?\s*[A-D](?:\s*图)?", answer, flags=re.IGNORECASE):
        return item
    evidence_text = "；".join(item.visible_evidence[:2]).strip()
    if not evidence_text:
        return item
    timestamp = item.source_timestamp or item.timestamp
    focus = item.criteria_met[0] if item.criteria_met else "图中关键对象"
    item.visual_answer = evidence_text
    item.visual_summary = evidence_text
    item.suggested_caption = f"{evidence_text}（{hhmmss(timestamp)}）"
    item.explanation_for_reader = f"看哪里：{focus}；看到什么：{evidence_text}"
    return item


def _resolve_candidate_id(value: Any, allowed_ids: set[str]) -> str:
    """容忍模型附带空白/大小写/图片后缀，但最终必须精确落在允许集合内。"""
    cleaned = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"\.(?:jpe?g|png)$", "", cleaned, flags=re.IGNORECASE)
    allowed_map = {item.casefold(): item for item in allowed_ids}
    return allowed_map.get(cleaned.casefold(), "")


def _normalize_reported_criteria(items: list[Any], allowed: list[str]) -> list[str]:
    """把 VLM 的轻微抄写错字映射回合同原文，但绝不接受集合外新条件。"""
    normalized_allowed = [(criterion, re.sub(r"[\s，。；、,:：;]", "", criterion)) for criterion in allowed]
    matched: list[str] = []
    for item in items:
        raw = re.sub(r"[\s，。；、,:：;]", "", str(item).strip())
        if not raw:
            continue
        best = ""
        best_score = 0.0
        for criterion, compact in normalized_allowed:
            score = SequenceMatcher(None, raw, compact).ratio()
            if score > best_score:
                best, best_score = criterion, score
        # 阈值只容忍少量 OCR/生成式抄写误差；低相似度表述仍按未满足处理。
        if best and best_score >= 0.78 and best not in matched:
            matched.append(best)
    return matched


def _vlm_select(
    question: VisualQuestion,
    candidate_rows: list[dict],
    contract: VisualNeed,
    provider: VisualModelProvider,
    allow_detail_pass: bool,
    detail_counter: list[int] | None = None,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> VisualEvidence:
    question_task_id = f"visual.detail.{question.question_id}"
    candidates = [{
        "candidate_id": str(row.get("image_id", "")),
        "path": str(row.get("path", "")),
        "timestamp_seconds": float(row.get("timestamp_seconds", 0.0)),
        "ocr_text": str(row.get("ocr_text", "")),
        "scene_cluster_id": str(row.get("scene_cluster_id", "")),
        "content_score": float(row.get("content_score", 0.0)),
    } for row in candidate_rows if row.get("image_id") and row.get("path")]
    if not candidates:
        return _no_match_from_provider(question, contract, "vlm_no_candidate", "没有合法候选图片", 0)

    question_payload = question.to_dict()
    contract_payload = contract.to_dict()
    oom_retried = False
    try:
        comparison = provider.compare_candidates(question_payload, candidates, contract_payload)
    except VisualProviderOOMError:
        oom_retried = True
        try:
            candidates = candidates[:2]
            comparison = provider.compare_candidates(question_payload, candidates, contract_payload)
        except (VisualProviderOOMError, VisualProviderError) as exc:
            return _no_match_from_provider(
                question, contract, "vlm_oom_no_match", "2 图低预算重试仍失败", len(candidates), str(exc),
            )
    except VisualProviderError as exc:
        return _no_match_from_provider(
            question, contract, "vlm_provider_error", "本地 VLM 调用失败", len(candidates), str(exc),
        )

    if str(comparison.get("decision", "no_match")) != "select":
        return _no_match_from_provider(
            question,
            contract,
            "vlm_rejected",
            str(comparison.get("reject_reason", "候选图片不能回答视觉问题")),
            len(candidates),
        )
    candidate_map = {item["candidate_id"]: item for item in candidates}
    raw_selected_id = comparison.get("selected_candidate_id", "")
    selected_id = _resolve_candidate_id(raw_selected_id, set(candidate_map))
    if not selected_id:
        return _no_match_from_provider(
            question,
            contract,
            "vlm_invalid_candidate_id",
            f"VLM 返回集合外 ID={str(raw_selected_id).strip()[:80]!r}；允许={sorted(candidate_map)}",
            len(candidates),
        )

    visible_evidence = _actual_visible_evidence(
        [str(item) for item in comparison.get("visible_evidence", []) if str(item)],
        contract.success_criteria,
    )
    criteria_met = _normalize_reported_criteria(
        list(comparison.get("criteria_met", [])), contract.success_criteria,
    )
    reported_missing = _normalize_reported_criteria(
        list(comparison.get("criteria_missing", [])), contract.success_criteria,
    )
    evidence_supported = _criteria_supported_by_visible_evidence(contract.success_criteria, visible_evidence)
    reported_missing = [criterion for criterion in reported_missing if criterion not in evidence_supported]
    for criterion in evidence_supported:
        if criterion not in criteria_met:
            criteria_met.append(criterion)
    criteria_met = [criterion for criterion in criteria_met if criterion not in reported_missing]
    criteria_missing = [criterion for criterion in contract.success_criteria if criterion not in criteria_met]
    visual_answer = str(comparison.get("visual_answer", "")).strip()
    detail_warning = ""
    if not visible_evidence:
        answer_evidence = _visual_answer_as_evidence(visual_answer, contract.success_criteria)
        if answer_evidence:
            visible_evidence = [answer_evidence]
    needs_detail = bool(comparison.get("needs_detail_pass", False))
    if not visible_evidence and criteria_met:
        needs_detail = True

    detail_available = detail_counter is None or detail_counter[0] < 2
    if needs_detail and allow_detail_pass and detail_available and not oom_retried:
        if detail_counter is not None:
            detail_counter[0] += 1
        detail_started = time.monotonic()
        if progress_callback:
            progress_callback(ProgressEvent(
                "visual", "detail", 0, 1, False,
                task_id=question_task_id, cache_state="miss", bucket="visual-detail",
            ))
        try:
            detail = provider.extract_selected(candidate_map[selected_id], contract_payload)
            visible_evidence = _actual_visible_evidence(
                [str(item) for item in detail.get("visible_evidence", visible_evidence) if str(item)],
                contract.success_criteria,
            )
            criteria_met = _normalize_reported_criteria(
                list(detail.get("criteria_met", criteria_met)), contract.success_criteria,
            )
            criteria_missing = [criterion for criterion in contract.success_criteria if criterion not in criteria_met]
            visual_answer = str(detail.get("visual_answer", visual_answer)).strip()
            if not visible_evidence:
                answer_evidence = _visual_answer_as_evidence(visual_answer, contract.success_criteria)
                if answer_evidence:
                    visible_evidence = [answer_evidence]
            needs_detail = False
        except (VisualProviderOOMError, VisualProviderError) as exc:
            if visible_evidence and not criteria_missing:
                # 粗选已经给出可见事实并覆盖全部成功条件时，细节复核只是增强步骤；
                # 其失败不应抹掉已经通过像素门槛的证据。
                detail_warning = f"细节复核失败，保留已通过门槛的粗选证据：{exc}"
                needs_detail = True
            else:
                return _no_match_from_provider(
                    question, contract, "vlm_detail_failed", "细节复核失败，未采用粗选结果", len(candidates), str(exc),
                )
        finally:
            if progress_callback:
                progress_callback(ProgressEvent(
                    "visual", "detail", 1, 1, False,
                    max(0.001, time.monotonic() - detail_started),
                    task_id=question_task_id, cache_state="miss", bucket="visual-detail",
                ))

    if not visible_evidence or criteria_missing:
        missing_summary = "、".join(criteria_missing) if criteria_missing else "无（缺少可见事实）"
        return _no_match_from_provider(
            question,
            contract,
            "vlm_criteria_rejected",
            f"VLM 结果缺少可见证据或未满足全部视觉成功条件；未满足：{missing_summary}",
            len(candidates),
            visible_evidence=visible_evidence,
            criteria_met=criteria_met,
            criteria_missing=criteria_missing,
            visual_answer=visual_answer,
        )

    selected = next(row for row in candidate_rows if str(row.get("image_id", "")) == selected_id)
    timestamp = float(selected.get("timestamp_seconds", 0.0))
    focus = contract.success_criteria[0] if contract.success_criteria else question.question
    evidence_text = "；".join(visible_evidence[:3])
    answer = _visual_answer_as_evidence(visual_answer, contract.success_criteria) or evidence_text
    confidence = max(0.0, min(1.0, float(comparison.get("confidence", 0.0))))
    return VisualEvidence(
        evidence_id=f"ve_{question.question_id}",
        question_id=question.question_id,
        image_path=str(selected.get("path", "")),
        timestamp=timestamp,
        ocr_text=str(selected.get("ocr_text", "")),
        visual_summary=answer,
        matched_knowledge_point_id=question.unit_id,
        matched_knowledge_id=question.unit_id,
        relevance_score=confidence,
        why_useful=f"先看“{focus}”，再用图中证据回答：{question.question}",
        match_reason=(
            "本地 VLM 粗选已通过视觉成功条件；细节复核失败但未影响已核实证据"
            if detail_warning else "本地 VLM 比较候选并通过视觉成功条件校验"
        ),
        suggested_caption=f"{answer}（{selected.get('timestamp_label', hhmmss(timestamp))}）",
        explanation_for_reader=f"看哪里：{focus}；看到什么：{evidence_text}；为什么重要：{answer}",
        frame_id=selected_id,
        source_timestamp=timestamp,
        dedup_group_id=str(selected.get("dedup_group_id", "")),
        scene_cluster_id=str(selected.get("scene_cluster_id", "")),
        image_sha256=str(selected.get("image_sha256", "")),
        perceptual_hash=str(selected.get("perceptual_hash", "")),
        visible_evidence=visible_evidence,
        visual_role=contract.role,
        criteria_met=criteria_met,
        criteria_missing=[],
        visual_answer=answer,
        needs_detail_pass=needs_detail,
        sequence_mode=contract.sequence_mode,
        visual_group_id=f"vg_{question.unit_id}",
        decision="select",
        confidence=confidence,
        source=getattr(provider, "name", "local_vlm"),
        candidate_count=len(candidates),
        capability_warning=detail_warning,
    )


def arbitrate_visual_evidence(
    evidence: list[VisualEvidence],
    contracts: dict[str, VisualNeed] | None = None,
) -> list[VisualEvidence]:
    """课程级场景仲裁：同一场景只保留一个 primary unit。"""
    selected_groups: dict[str, list[VisualEvidence]] = {}
    for item in evidence:
        if item.decision != "select":
            continue
        if not item.visible_evidence and not item.ocr_text:
            item.decision = "no_match"
            item.match_reason = "缺少 OCR 或 VLM 可见证据，仲裁拒绝"
            item.source = "global_arbitration_no_pixel_evidence"
            continue
        if item.criteria_missing:
            item.decision = "no_match"
            item.match_reason = "视觉成功条件未全部满足，仲裁拒绝"
            item.source = "global_arbitration_criteria_missing"
            continue
        key = item.scene_cluster_id or item.dedup_group_id or item.image_sha256 or item.image_path
        if not key:
            item.decision = "no_match"
            item.match_reason = "候选缺少可追踪的场景或图片标识"
            item.source = "global_arbitration_untraceable"
            continue
        selected_groups.setdefault(key, []).append(item)

    for group_id, rows in selected_groups.items():
        ranked = sorted(
            rows,
            key=lambda item: (item.relevance_score, item.confidence, len(item.visible_evidence)),
            reverse=True,
        )
        winner = ranked[0]
        matched_ids: list[str] = []
        for item in ranked:
            for matched_id in [
                *item.matched_knowledge_ids,
                item.matched_knowledge_id or item.matched_knowledge_point_id,
            ]:
                if matched_id and matched_id not in matched_ids:
                    matched_ids.append(matched_id)
        winner.matched_knowledge_ids = matched_ids
        winner.primary_unit_id = winner.matched_knowledge_id or winner.matched_knowledge_point_id
        winner.scene_cluster_id = winner.scene_cluster_id or group_id
        winner.dedup_group_id = winner.dedup_group_id or group_id
        for loser in ranked[1:]:
            loser.decision = "no_match"
            loser.match_reason = f"同场景已分配给 {winner.primary_unit_id}，避免跨知识点重复插图"
            loser.source = "global_scene_arbitration"

    selected_by_unit: dict[str, list[VisualEvidence]] = {}
    for item in evidence:
        if item.decision != "select":
            continue
        unit_id = item.primary_unit_id or item.matched_knowledge_id or item.matched_knowledge_point_id
        selected_by_unit.setdefault(unit_id, []).append(item)
    for unit_id, rows in selected_by_unit.items():
        contract = (contracts or {}).get(unit_id, VisualNeed(required=True))
        ranked = sorted(
            rows,
            key=lambda item: (item.relevance_score, item.confidence, len(item.visible_evidence)),
            reverse=True,
        )
        for loser in ranked[contract.max_count:]:
            loser.decision = "no_match"
            loser.match_reason = f"知识点图片已达到 max_count={contract.max_count}，全局仲裁拒绝额外图片"
            loser.source = "global_unit_budget_arbitration"
    return evidence


def _visual_job_fingerprint(
    job_id: str,
    question: VisualQuestion,
    contract: VisualNeed,
    candidates: list[dict],
    settings: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "version": 1,
        "question": question.to_dict(),
        "contract": contract.to_dict(),
        "candidates": [
            {
                "image_id": row.get("image_id", ""),
                "scene_cluster_id": row.get("scene_cluster_id", ""),
                "path": row.get("path", ""),
                "mtime": Path(str(row.get("path", ""))).stat().st_mtime if Path(str(row.get("path", ""))).is_file() else 0,
            }
            for row in candidates
        ],
        "runner": str(settings.get("local_vlm_runner", "")),
        "runtime_python": str(settings.get("local_vlm_runtime_python", "")),
        "runtime_dir": str(settings.get("local_vlm_runtime_dir", "")),
        "model": str(settings.get("local_vlm_model_dir", settings.get("local_vlm_model", ""))),
        "local_vlm_enabled": str(settings.get("local_vlm_enabled", False)).lower(),
        "ocr_enabled": bool(settings.get("ocr_enabled", False)),
        "backend": str(settings.get("backend", "fallback")),
    }
    return payload


def build_visual_evidence(
    lesson_plan: LessonPlan,
    frames: dict,
    transcript: dict,
    work_dir: Path,
    settings: dict | None = None,
    ocr_provider: Callable[[str], str] | None = None,
    vlm_provider: VisualModelProvider | None = None,
    task_cache: Any = None,
    *,
    provider_factory: Callable[[], tuple[VisualModelProvider | None, str]] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    progress_sink: Callable[[ProgressEvent], None] | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> list[VisualEvidence]:
    """生成结构化 VisualEvidence。

    settings.enabled 为 false 时返回空列表；backend 预留给后续本地 VLM。
    """
    runtime_settings = dict(settings or {})
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    runtime_state.update({
        "completed": False,
        "overall_cache_hit": False,
        "provider_initialized": vlm_provider is not None,
        "session_started": False,
        "session_closed": False,
        "model_load_count": 0,
        "session_restart_count": 0,
        "current_failure_sources": [],
        "batch_skipped_count": 0,
    })
    if runtime_settings.get("enabled", True) is False:
        return []
    candidates = _candidate_rows(work_dir, frames)
    duration = max((float(row.get("end_seconds", 0.0)) for row in transcript.get("segments", [])), default=0.0)
    jobs = collect_visual_jobs(lesson_plan, duration, runtime_settings)
    progress_callback = progress_sink

    def emit(level: str, message: str, **details: Any) -> None:
        if event_sink is not None:
            event_sink({"stage": "visual", "level": level, "message": message, **details})

    clustered_candidates = cluster_visual_scenes(
        candidates,
        max_neighbor_seconds=float(runtime_settings.get("scene_neighbor_seconds", 45.0)),
        max_scene_span_seconds=float(runtime_settings.get("scene_max_span_seconds", 150.0)),
        hash_threshold=int(runtime_settings.get("scene_hash_threshold", 6)),
        ssim_threshold=float(runtime_settings.get("scene_ssim_threshold", 0.97)),
    )
    max_candidates = int(runtime_settings.get("max_candidates_per_question", 6))
    compare_max_candidates = max(1, min(4, int(runtime_settings.get("vlm_compare_max_candidates", 4))))
    capability_warnings: list[str] = []
    if ocr_provider is None:
        ocr_provider, warning = create_ocr_provider(runtime_settings)
        if warning:
            capability_warnings.append(warning)
    owns_vlm_provider = vlm_provider is None
    provider_initialized = vlm_provider is not None

    def get_vlm_provider() -> VisualModelProvider | None:
        nonlocal vlm_provider, provider_initialized
        if provider_initialized:
            return vlm_provider
        provider_initialized = True
        runtime_state["provider_initialized"] = True
        preflight_started = time.monotonic()
        if progress_callback:
            progress_callback(ProgressEvent(
                "visual", "preflight", 0, 1, False,
                task_id="visual.preflight", cache_state="miss", bucket="qwen3-vl-local",
            ))
        if provider_factory is None:
            return None
        vlm_provider, warning = provider_factory()
        if progress_callback:
            progress_callback(ProgressEvent(
                "visual", "preflight", 1, 1, False,
                max(0.001, time.monotonic() - preflight_started),
                task_id="visual.preflight", cache_state="miss", bucket="qwen3-vl-local",
            ))
        if warning:
            capability_warnings.append(warning)
        return vlm_provider
    contracts = _visual_contracts(lesson_plan)
    ocr_cache: dict[str, str] = {}
    evidence: list[VisualEvidence] = []
    job_by_question = {job.question.question_id: job for job in jobs}
    reserved_scenes: set[str] = set()
    reservation_owners: dict[str, VisualEvidence] = {}
    detail_counter = [0]
    compare_completed = 0
    if progress_callback and jobs:
        for job in jobs:
            progress_callback(ProgressEvent(
                "visual", "compare", 0, 1, False,
                task_id=f"visual.compare.{job.job_id}",
                cache_state="unknown", bucket="visual-compare",
            ))
    batch_failure_source = ""
    try:
        for question in _all_visual_questions(lesson_plan):
            ensure_not_cancelled(cancel_check)
            job = job_by_question.get(question.question_id)
            contract = job.contract if job is not None else (contracts.get(question.unit_id) or VisualNeed(
                required=True,
                question=question.question,
                role=(question.preferred_visual_role if question.preferred_visual_role in {
                    "locate", "explain", "procedure", "compare", "evidence", "recap",
                } else "explain"),
                success_criteria=["画面可直接回答视觉问题"],
            ))
            validation = validate_visual_question(question, contract)
            if not validation.accepted or job is None:
                source = "visual_contract_rejected" if not validation.accepted else "visual_job_budget_rejected"
                reason = validation.reason if not validation.accepted else "视觉任务超过本视频 compare 预算"
                item = _no_match_from_provider(question, contract, source, reason, 0)
                evidence.append(item)
                emit(
                    "info",
                    f"视觉任务在调用前收敛：{reason}",
                    code="visual_question_rejected", question_id=question.question_id,
                    decision="no_match", source=source, inference_failed=False,
                )
                continue
            emit(
                "info", f"正在核对视觉问题：{question.question}",
                code="visual_question_started", question_id=question.question_id,
            )
            recalled = recall_candidates_for_question(question, clustered_candidates, max_candidates=max_candidates)
            targeted = _targeted_ocr_rerank(question, recalled, ocr_provider, ocr_cache)
            available = [
                row for row in targeted
                if not row.get("scene_cluster_id") or str(row.get("scene_cluster_id")) not in reserved_scenes
            ]
            if targeted and not available:
                job_cache_hit = False
                job_cache_state = "not_applicable"
                compare_duration = None
                item = _no_match_from_provider(
                    question, contract, "scene_reserved", "候选场景已被更早的视觉任务占用", len(targeted),
                )
                for row in targeted:
                    scene_id = str(row.get("scene_cluster_id", ""))
                    owner = reservation_owners.get(scene_id)
                    if owner is not None:
                        owner.matched_knowledge_ids = list(dict.fromkeys([
                            *owner.matched_knowledge_ids,
                            owner.matched_knowledge_point_id,
                            question.unit_id,
                        ]))
                        break
            else:
                compared = available[:job.max_candidates]
                job_fingerprint = _visual_job_fingerprint(
                    job.job_id, question, contract, compared, runtime_settings,
                )
                item = None
                job_cache_hit = False
                job_cache_state = "miss"
                compare_duration = None
                cached_payload = task_cache.load(job.job_id, job_fingerprint) if task_cache is not None else None
                if cached_payload is not None:
                    try:
                        cached_job = VisualEvidence.from_dict(cached_payload)
                    except (KeyError, TypeError, ValueError):
                        pass
                    else:
                        retry_transient = bool(runtime_settings.get("retry_transient_failures", False))
                        if not (retry_transient and is_vlm_failure_source(cached_job.source)):
                            item = cached_job
                            job_cache_hit = True
                            job_cache_state = "hit"
                if item is None and (batch_failure_source or not compared):
                    job_cache_state = "not_applicable"
                if progress_callback:
                    progress_callback(ProgressEvent(
                        "visual", "compare", 0, 1, job_cache_hit,
                        task_id=f"visual.compare.{job.job_id}",
                        cache_state=job_cache_state,
                        bucket="visual-cache" if job_cache_hit else "visual-compare",
                    ))
                if item is None:
                    compare_started = time.monotonic()
                    if batch_failure_source:
                        item = _no_match_from_provider(
                            question,
                            contract,
                            "vlm_batch_skipped",
                            f"本批次已因 {batch_failure_source} 停止后续视觉推理",
                            len(compared),
                        )
                        runtime_state["batch_skipped_count"] += 1
                    elif not compared:
                        item = _no_match_from_provider(
                            question, contract, "vlm_no_candidate", "没有合法候选图片", 0,
                        )
                    else:
                        active_provider = get_vlm_provider()
                        if active_provider:
                            item = _vlm_select(
                                question,
                                compared[:compare_max_candidates],
                                contract,
                                active_provider,
                                bool(runtime_settings.get("allow_detail_pass", True)),
                                detail_counter,
                                progress_callback,
                            )
                            runtime_state["session_started"] = bool(
                                getattr(active_provider, "_session", None) is not None
                            )
                        else:
                            item = _fallback_select(
                                question,
                                available,
                                contract,
                                "；".join(dict.fromkeys(capability_warnings)),
                            )
                    compare_duration = max(0.001, time.monotonic() - compare_started)
                    if task_cache is not None:
                        task_cache.record(job.job_id, job_fingerprint, item.to_dict())
            compare_completed += 1
            if not job_cache_hit and is_vlm_failure_source(item.source):
                failures = runtime_state["current_failure_sources"]
                if item.source not in failures:
                    failures.append(item.source)
                if item.source in _VLM_BATCH_FAILURE_SOURCES:
                    batch_failure_source = item.source
            if progress_callback:
                progress_callback(ProgressEvent(
                    "visual", "compare", 1, 1, job_cache_hit,
                    compare_duration,
                    task_id=f"visual.compare.{job.job_id}",
                    cache_state=job_cache_state,
                    bucket=(
                        "visual-cache"
                        if job_cache_hit else
                        vlm_provider.eta_bucket("compare")
                        if vlm_provider is not None and hasattr(vlm_provider, "eta_bucket")
                        else "visual-compare"
                    ),
                ))
            if item.image_path and not item.image_sha256:
                item.image_sha256 = _image_sha256(item.image_path)
            if item.decision == "select":
                scene_id = item.scene_cluster_id or item.dedup_group_id
                if scene_id:
                    reserved_scenes.add(scene_id)
                    reservation_owners[scene_id] = item
            evidence.append(item)
            inference_failed = item.decision != "select" and is_vlm_failure_source(item.source)
            emit(
                "info",
                (
                    f"视觉问题已匹配真实帧：{Path(item.image_path).name}"
                    if item.decision == "select"
                    else (
                        f"视觉推理未完成：{item.match_reason or item.capability_warning or item.source}"
                        if inference_failed
                        else f"视觉核验完成，按证据门槛不配图：{item.match_reason or item.source}"
                    )
                ),
                code="visual_question_selected" if item.decision == "select" else "visual_question_no_match",
                question_id=question.question_id,
                decision=item.decision,
                source=item.source,
                device="gpu" if "qwen3-vl" in item.source else "cpu",
                inference_failed=inference_failed,
            )
    finally:
        session = getattr(vlm_provider, "_session", None) if vlm_provider is not None else None
        if session is not None:
            runtime_state["model_load_count"] = int(getattr(session, "model_load_count", 0) or 0)
            runtime_state["session_restart_count"] = int(getattr(session, "restart_count", 0) or 0)
        if owns_vlm_provider and vlm_provider is not None and hasattr(vlm_provider, "close"):
            vlm_provider.close()
            runtime_state["session_closed"] = True
    runtime_state["completed"] = True
    evidence = arbitrate_visual_evidence(evidence, contracts)
    evidence = [sanitize_visual_evidence_for_reader(item) for item in evidence]

    return evidence
