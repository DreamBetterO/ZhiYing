"""课程规划：合并画像和选择，生成 LessonPlan。

替代原来分步的 profiling + selection 调用。
保留离线启发式和云端精炼两种模式，低置信度退回通用。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..transcript import merge_transcript_segments
from .source_blocks import build_cloud_source_blocks
from .text_analysis import bigrams as _bigrams, keyword_counts as _keyword_counts
from ..utils import TaskCancelled
from .schema import (
    ChapterPlan,
    DepthFactors,
    EvidenceSpan,
    LessonPlan,
    SideTopic,
    UnitPlan,
    VisualNeed,
    VisualProfile,
    VisualQuestion,
    VisualJob,
)
from .prompts.planning import render_planning

_PLANNING_VERSION = 8


@dataclass(frozen=True)
class VisualQuestionValidation:
    accepted: bool
    reason: str = ""


def validate_visual_question(
    question: VisualQuestion,
    contract: VisualNeed,
) -> VisualQuestionValidation:
    text = re.sub(r"\s+", "", question.question)
    if not question.answerable_from_pixels:
        return VisualQuestionValidation(False, "question_not_pixel_answerable")
    if (len(text) < 8 and not question.expected_entities) or re.match(r"^(?:这个|那个|它|这里|那里|然后|所以)(?:呢|吗|是)?[？?]?$", text):
        return VisualQuestionValidation(False, "question_fragment_or_pronoun")
    if any(cue in text for cue in ("重要性是什么", "有什么价值", "为什么重要", "总体目标", "学习意义")):
        return VisualQuestionValidation(False, "question_abstract")
    if any(cue in text for cue in ("前后变化", "变化过程", "执行前后")) and contract.sequence_mode == "single":
        return VisualQuestionValidation(False, "question_requires_multiple_frames")
    if not contract.success_criteria:
        return VisualQuestionValidation(False, "missing_success_criteria")
    return VisualQuestionValidation(True)


def collect_visual_jobs(
    plan: LessonPlan,
    duration: float,
    settings: dict[str, Any] | None = None,
) -> list[VisualJob]:
    """Collect bounded compare jobs after rejecting unsafe visual questions."""
    values = dict(settings or {})
    compare_limit = int(values.get("max_compare_jobs", 8 if 900 < duration <= 2700 else (4 if duration <= 900 else 12)))
    compare_limit = max(0, min(8 if 900 < duration <= 2700 else 12, compare_limit))
    max_candidates = max(1, min(4, int(values.get("vlm_compare_max_candidates", 4))))
    contracts = {unit.plan_id: unit.visual_need for unit in plan.all_unit_plans}
    jobs: list[VisualJob] = []
    for unit in plan.all_unit_plans:
        contract = contracts.get(unit.plan_id, VisualNeed())
        for question in unit.visual_questions:
            if not contract.success_criteria:
                focus = question.expected_entities[0] if question.expected_entities else question.question[:20]
                contract = VisualNeed(
                    required=True,
                    question=contract.question or question.question,
                    role=(contract.role if contract.role in {"locate", "explain", "procedure", "compare", "evidence", "recap"} else "explain"),
                    target_count=1,
                    max_count=1,
                    sequence_mode="single",
                    explanation_depth=contract.explanation_depth,
                    success_criteria=[f"画面中可辨认{focus}"],
                    reason=contract.reason or "由明确视觉问题补齐像素成功条件",
                )
            validation = validate_visual_question(question, contract)
            if not validation.accepted:
                continue
            jobs.append(VisualJob(
                job_id=f"visual_{question.question_id or len(jobs) + 1}",
                kind="compare",
                question=question,
                contract=contract,
                max_candidates=max_candidates,
            ))
            break  # default one primary question per knowledge point
        if len(jobs) >= compare_limit:
            break
    return jobs


def _validate_plan_payload(parsed: dict[str, Any], source_blocks: dict[str, list[str]]) -> None:
    chapters = parsed.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("云端规划缺少 chapters")
    if len(chapters) > 16:
        raise ValueError("云端规划章节数量异常")
    valid_blocks = set(source_blocks)
    valid_segments = {segment_id for values in source_blocks.values() for segment_id in values}
    plan_ids: list[str] = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or not str(chapter.get("title", "")).strip():
            raise ValueError("云端规划章节缺少标题")
        unit_plans = chapter.get("unit_plans")
        if not isinstance(unit_plans, list) or not unit_plans:
            raise ValueError("云端规划章节缺少 unit_plans")
        for unit in unit_plans:
            if not isinstance(unit, dict) or not str(unit.get("title", "")).strip():
                raise ValueError("云端规划知识点缺少标题")
            plan_id = str(unit.get("plan_id", "")).strip()
            if not plan_id or plan_id in plan_ids:
                raise ValueError("云端规划 plan_id 缺失或重复")
            plan_ids.append(plan_id)
            refs = unit.get("source_block_ids", unit.get("source_segment_ids", []))
            if not isinstance(refs, list) or not refs:
                raise ValueError(f"云端规划 {plan_id} 缺少来源块")
            if not any(str(ref).strip() in valid_blocks or str(ref).strip() in valid_segments for ref in refs):
                raise ValueError(f"云端规划 {plan_id} 未引用真实来源块")
    if len(plan_ids) > 64:
        raise ValueError("云端规划知识点数量异常")

_LEVEL_DEEP_RATIO: dict[str, float] = {"精简": 0.15, "推荐": 0.28, "丰富": 0.38}
_LEVEL_CHAR_BUDGET: dict[str, dict[str, int]] = {
    "精简": {"mention": 90, "brief": 140, "standard": 260, "deep": 420},
    "推荐": {"mention": 110, "brief": 180, "standard": 360, "deep": 620},
    "丰富": {"mention": 130, "brief": 220, "standard": 460, "deep": 760},
}

_VISUAL_LEVEL_FACTORS: dict[str, float] = {
    "minimal": 0.5,
    "balanced": 1.0,
    "enhanced": 1.5,
}

# 知识类型关键词线索
_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("concept", ["定义", "概念", "是什么", "本质", "含义", "指的是"]),
    ("rule", ["规则", "条件", "判定", "成立", "不成立", "如果", "则"]),
    ("procedure", ["步骤", "流程", "操作", "先", "然后", "接着", "最后"]),
    ("mechanism", ["原因", "因为", "所以", "导致", "由于"]),
    ("comparison", ["对比", "区别", "相同", "不同", "差异", "比较"]),
    ("case", ["案例", "实例", "例如", "比如", "举例"]),
    ("conclusion", ["结论", "总结", "总之", "因此"]),
]

# 领域关键词线索
_DOMAIN_HINTS: list[tuple[str, list[str]]] = [
    ("金融技术分析", ["缠论", "中枢", "线段", "K线", "均线", "背驰", "买卖点", "走势", "分型", "笔"]),
    ("医学", ["解剖", "生理", "病理", "诊断", "治疗", "患者", "临床", "症状", "病变", "药物"]),
    ("软件操作", ["代码", "函数", "变量", "编译", "运行", "调试", "界面", "点击", "菜单", "配置"]),
    ("法律", ["合同", "条款", "诉讼", "仲裁", "法规", "权利", "义务", "违约", "管辖", "判决"]),
]

# 课程形态关键词线索
_FORM_HINTS: list[tuple[str, list[str]]] = [
    ("rule_teaching", ["规则", "条件", "判定", "成立", "不成立", "例外", "前提", "步骤"]),
    ("concept_lecture", ["定义", "概念", "是什么", "本质", "含义", "分类", "组成"]),
    ("case_review", ["案例", "实例", "回顾", "复盘", "实战", "演示"]),
    ("software_demo", ["操作", "演示", "界面", "点击", "输入", "运行"]),
    ("meeting_discussion", ["讨论", "问题", "意见", "建议", "总结", "决议"]),
]

# required_facets 默认模板
_FACET_TEMPLATES: dict[str, list[str]] = {
    "concept": ["prerequisite", "branches", "pitfalls"],
    "rule": ["prerequisite", "decision_order", "direction_branch", "exception", "counterexample"],
    "procedure": ["goal", "input", "steps", "stop_condition", "failure_handling"],
    "mechanism": ["cause", "process", "result", "scope"],
    "comparison": ["dimensions", "commonalities", "differences"],
    "case": ["background", "rule_application", "conclusion", "transferable_experience"],
    "boundary_case": ["violation_condition", "reason", "counterexample", "pitfalls"],
    "visual_or_formula": ["what_to_look_at", "object_relations", "reading_order", "how_to_conclude"],
    "conclusion": ["prerequisite", "core_conclusion"],
}


def _match_domain(top_terms: list[str]) -> str:
    if not top_terms:
        return ""
    best_domain = ""
    best_hits = 0
    for domain, keywords in _DOMAIN_HINTS:
        hits = sum(1 for term in top_terms if any(kw in term for kw in keywords))
        if hits > best_hits:
            best_domain = domain
            best_hits = hits
    return best_domain or "通用"


def _match_form(all_text: str) -> str:
    best_form = "general"
    best_score = 0.0
    for form, keywords in _FORM_HINTS:
        score = sum(1 for kw in keywords if kw in all_text) / len(keywords)
        if score > best_score:
            best_form = form
            best_score = score
    return best_form


def _infer_visual_profile(all_text: str, course_form: str) -> VisualProfile:
    """只用课堂文本推断视觉依赖倾向，不据此强制每个知识点配图。"""
    chart_terms = ("K线", "均线", "走势图", "图表", "坐标", "中枢", "区间", "边界", "高点", "低点")
    screen_terms = ("界面", "点击", "菜单", "按钮", "输入", "选择", "窗口", "软件", "操作")
    slide_terms = ("PPT", "幻灯片", "这一页", "课件", "表格", "公式", "流程图", "示意图")
    pointing_terms = ("看这里", "这根", "左边", "右边", "上面", "下面", "画面", "图中")
    chart_hits = sum(all_text.count(term) for term in chart_terms)
    screen_hits = sum(all_text.count(term) for term in screen_terms)
    slide_hits = sum(all_text.count(term) for term in slide_terms)
    pointing_hits = sum(all_text.count(term) for term in pointing_terms)

    dominant: list[str] = []
    if chart_hits:
        dominant.append("chart")
    if screen_hits:
        dominant.append("software_ui")
    if any(term in all_text for term in ("PPT", "幻灯片", "这一页", "课件")):
        dominant.append("slide")
    if "流程图" in all_text or "示意图" in all_text:
        dominant.append("diagram")
    if "表格" in all_text:
        dominant.append("table")
    if "公式" in all_text:
        dominant.append("formula")

    signals: list[str] = []
    if chart_hits >= 3:
        signals.append("图表与空间关系术语密集")
        return VisualProfile("chart_analysis", "high", dominant or ["chart"], "enhanced", signals)
    if screen_hits >= 3 or course_form == "software_demo":
        signals.append("界面操作与状态变化术语密集")
        return VisualProfile("screen_demo", "high", dominant or ["software_ui"], "enhanced", signals)
    if slide_hits >= 3:
        signals.append("课件、公式或表格术语密集")
        return VisualProfile("slide_dominant", "high", dominant or ["slide"], "enhanced", signals)
    if pointing_hits >= 2 or dominant:
        signals.append("存在明确指图或视觉对象线索")
        return VisualProfile("mixed", "medium", dominant, "balanced", signals)
    signals.append("主要证据来自连续语音讲解")
    return VisualProfile("speech_dominant", "low", [], "minimal", signals)


def _effective_visual_level(requested: str, profile: VisualProfile) -> str:
    level = requested if requested in {"auto", "minimal", "balanced", "enhanced"} else "auto"
    return profile.recommended_level if level == "auto" else level


def _infer_knowledge_types(text: str) -> list[str]:
    types: list[str] = []
    for ktype, keywords in _TYPE_KEYWORDS:
        if any(kw in text for kw in keywords):
            types.append(ktype)
    return types[:3] if types else ["concept"]


def _infer_importance(text: str, info: float) -> str:
    if any(kw in text for kw in ["同学们", "好吧", "非常好", "有没有", "感谢"]):
        return "peripheral"
    if info > 2.5:
        return "core"
    if info > 0.5:
        return "supporting"
    return "peripheral"


def _is_noise_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    noise_cues = ("同学们今天开始上课", "非常好", "有没有问题", "感谢", "好吧", "OK")
    if any(cue in compact for cue in noise_cues):
        return True
    return len(compact) < 8 and not any(cue in compact for cue in ("是", "包括", "规则", "定义", "条件", "目的"))


def _transcript_hash(transcript: dict) -> str:
    """内容哈希：基于转写文本而非 segment 数量。"""
    segments = transcript.get("segments", [])
    text = "".join(row.get("text", "") for row in segments)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _segment_map(transcript: dict) -> dict[str, dict]:
    return {str(row.get("segment_id", "")): row for row in transcript.get("segments", [])}


def _segment_text(segment_ids: list[str], segment_map: dict[str, dict]) -> str:
    texts = [
        str(segment_map[sid].get("text", "")).strip()
        for sid in segment_ids
        if sid in segment_map and str(segment_map[sid].get("text", "")).strip()
    ]
    return "；".join(texts)


def _evidence_span(unit: UnitPlan, segment_map: dict[str, dict]) -> EvidenceSpan:
    refs = [segment_map[sid] for sid in unit.source_segment_ids if sid in segment_map]
    if not refs:
        return EvidenceSpan(segment_ids=list(unit.source_segment_ids), purpose=unit.title)
    return EvidenceSpan(
        start_seconds=min(float(row.get("start_seconds", 0.0)) for row in refs),
        end_seconds=max(float(row.get("end_seconds", 0.0)) for row in refs),
        segment_ids=list(unit.source_segment_ids),
        purpose=unit.title,
    )


def _score_depth_factors(unit: UnitPlan, source_text: str) -> DepthFactors:
    text = f"{unit.title}；{source_text}"
    ktypes = set(unit.knowledge_types)
    dependency = 3 if unit.role == "core" else 2 if unit.role == "supporting" else 1
    if any(kw in text for kw in ("前提", "基础", "核心", "后续", "判断", "步骤")):
        dependency = min(3, dependency + 1)
    difficulty = 1
    if ktypes & {"rule", "procedure", "mechanism", "comparison", "visual_or_formula"}:
        difficulty += 1
    if any(kw in text for kw in ("条件", "如果", "但是", "反穿", "重叠", "区间", "方向", "边界", "例外")):
        difficulty += 1
    error_risk = 1
    if any(kw in text for kw in ("不", "不是", "不能", "同价", "易错", "错误", "相违背", "例外")):
        error_risk += 2
    transfer_value = 1
    if any(kw in text for kw in ("方法", "法则", "判断", "步骤", "应用", "作业", "案例")):
        transfer_value += 1
    source_capacity = 1
    if len(source_text) > 70:
        source_capacity += 1
    if any(kw in text for kw in ("比如", "例如", "这个图", "看", "步骤", "第一", "第二", "反例")):
        source_capacity += 1
    return DepthFactors(
        dependency=dependency,
        difficulty=difficulty,
        error_risk=error_risk,
        transfer_value=transfer_value,
        source_capacity=source_capacity,
        reason=f"依赖{dependency}/难度{difficulty}/误判风险{error_risk}/迁移{transfer_value}/来源{source_capacity}",
    )


def _depth_priority(factors: DepthFactors) -> int:
    return 3 * factors.dependency + 2 * factors.difficulty + 2 * factors.error_risk + factors.transfer_value


def _requires_visual(unit: UnitPlan, source_text: str) -> bool:
    text = f"{unit.title}；{source_text}"
    abstract_cues = ("重要性", "法则的重要性", "学习方法", "价值", "提醒", "总结", "成年人", "学习是自己的")
    if any(cue in unit.title for cue in abstract_cues):
        return False
    if "visual_or_formula" in unit.knowledge_types:
        return True
    strong_objects = ("K线", "均线", "中枢线", "重叠区间", "反穿", "走势图", "流程图", "公式", "表格", "界面", "点击")
    if any(cue in text for cue in strong_objects):
        return True
    pointing = ("看这里", "这根", "左边", "右边", "上面", "下面", "画面")
    return any(cue in text for cue in pointing) and any(cue in text for cue in ("图", "线", "表", "界面"))


def _visual_contract(unit: UnitPlan, source_text: str, visual_level: str) -> VisualNeed:
    text = f"{unit.title}；{source_text}"
    ktypes = set(unit.knowledge_types)
    if "procedure" in ktypes or any(term in text for term in ("步骤", "操作", "点击", "流程")):
        role = "procedure"
    elif "comparison" in ktypes or any(term in text for term in ("对比", "区别", "差异")):
        role = "compare"
    elif "case" in ktypes or any(term in text for term in ("案例", "实例", "走势图")):
        role = "evidence"
    elif any(term in text for term in ("哪根", "位置", "边界", "左边", "右边")):
        role = "locate"
    elif "conclusion" in ktypes:
        role = "recap"
    else:
        role = "explain"

    # 当前 provider 每个 job 只选择一张图；多帧 progression 合同不能由它可靠履行。
    sequence_mode = "single"
    max_count = 1
    target_count = 1
    explanation_depth = {
        "minimal": "caption",
        "balanced": "brief_note",
        "enhanced": "teaching_note",
    }.get(visual_level, "brief_note")

    entities = [
        term for term in (
            "K线", "均线", "中枢线", "重叠区间", "反穿", "边界", "流程", "公式", "表格", "界面", "按钮", "菜单",
        )
        if term in text
    ][:3]
    focus = entities[0] if entities else unit.title[:20]
    criteria = [f"画面中可辨认{focus}"]
    if role == "procedure":
        criteria.append("可区分关键步骤或前后状态")
    elif role == "compare":
        criteria.append("可同时辨认对比对象及差异")
    elif role in {"explain", "evidence"}:
        # 成功条件由本地 VLM 对像素逐条核验，不能使用“能够回答问题”这类
        # 需要语义推断、无法从单张图片直接判真的抽象条件。
        if len(entities) >= 2:
            criteria.append(f"画面中可辨认{entities[0]}与{entities[1]}的相对位置或线条关系")
        else:
            criteria.append(f"画面中可辨认{focus}的形态、位置或标注关系")
    elif role == "locate":
        criteria.append("可定位目标对象与参照边界")
    return VisualNeed(
        required=True,
        question=unit.visual_need.question.strip(),
        role=role,
        target_count=target_count,
        max_count=max_count,
        sequence_mode=sequence_mode,
        explanation_depth=explanation_depth,
        success_criteria=criteria,
        reason=f"该知识点依赖{role}类可见证据；图文档位为 {visual_level}",
    )


def _make_visual_questions(unit: UnitPlan, source_text: str, spans: list[EvidenceSpan]) -> list[VisualQuestion]:
    if not unit.needs_visual:
        return []
    base_question = unit.visual_need.question.strip()
    if not base_question:
        if "公式" in source_text or "公式" in unit.title:
            base_question = f"哪张图能展示“{unit.title}”中的公式或变量关系？"
        elif "流程" in source_text or "步骤" in source_text:
            base_question = f"哪张图能展示“{unit.title}”的操作步骤或流程状态？"
        elif "对比" in source_text or "区别" in source_text:
            base_question = f"哪张图能展示“{unit.title}”涉及的对比关系？"
        else:
            base_question = f"哪张图能用可见对象解释“{unit.title}”？"
    entities = []
    for term in ("K线", "均线", "中枢线", "重叠区间", "反穿", "流程", "公式", "表格"):
        if term in f"{unit.title}；{source_text}":
            entities.append(term)
    result: list[VisualQuestion] = []
    for index, prompt in enumerate([base_question], start=1):
        question = VisualQuestion(
            question_id=f"vq_{unit.plan_id.replace('plan_', '')}_{index:02d}" if unit.plan_id else "",
            unit_id=unit.plan_id,
            question=prompt,
            answerable_from_pixels=True,
            expected_entities=entities[:4],
            expected_relation=unit.title,
            preferred_visual_role=unit.visual_need.role,
            negative_cues=["只有时间接近但看不出问题答案", "抽象总结或课程提醒画面"],
            anchor_spans=spans,
        )
        if validate_visual_question(question, unit.visual_need).accepted:
            result.append(question)
    return result


def _merge_visual_contract(existing: VisualNeed, inferred: VisualNeed) -> VisualNeed:
    """Preserve a valid model contract; heuristics only fill missing fields."""
    role = existing.role if existing.role in {"locate", "explain", "procedure", "compare", "evidence", "recap"} else inferred.role
    criteria = [str(item) for item in existing.success_criteria if str(item).strip()] or list(inferred.success_criteria)
    question = existing.question.strip() or inferred.question
    return VisualNeed(
        required=True,
        question=question,
        role=role,
        target_count=1,
        max_count=1,
        sequence_mode="single",
        explanation_depth=(existing.explanation_depth if existing.explanation_depth in {"caption", "brief_note", "teaching_note"} else inferred.explanation_depth),
        success_criteria=criteria,
        reason=existing.reason.strip() or inferred.reason,
    )


def _assign_depth_contracts(
    plan: LessonPlan,
    transcript: dict,
    content_level: str,
    visual_level: str = "auto",
) -> LessonPlan:
    """按全课比较分配 detail_level，并补齐 V2.1 计划字段。"""
    segment_map = _segment_map(transcript)
    rows: list[tuple[int, UnitPlan, str]] = []
    for unit in plan.all_unit_plans:
        source_text = _segment_text(unit.source_segment_ids, segment_map)
        unit.classroom_evidence = [source_text[:240]] if source_text else []
        unit.assistant_supplement = []
        span = _evidence_span(unit, segment_map)
        unit.evidence_spans = [span] if span.segment_ids else []
        unit.learner_question = unit.learner_question or f"怎样理解并使用“{unit.title}”？"
        unit.depth_factors = _score_depth_factors(unit, source_text)
        rows.append((_depth_priority(unit.depth_factors), unit, source_text))

    effective_visual_level = _effective_visual_level(visual_level, plan.visual_profile)
    rows.sort(key=lambda item: item[0], reverse=True)
    deep_limit = max(1, round(len(rows) * _LEVEL_DEEP_RATIO.get(content_level, _LEVEL_DEEP_RATIO["推荐"]))) if rows else 0
    for index, (priority, unit, source_text) in enumerate(rows):
        if unit.role == "peripheral":
            detail = "mention"
        elif unit.depth_factors.source_capacity <= 1 and priority >= 14:
            detail = "standard"
            unit.expansion_allowed = False
        elif index < deep_limit and priority >= 13 and unit.depth_factors.source_capacity >= 2:
            detail = "deep"
        elif priority >= 10 or unit.role == "core":
            detail = "standard"
        else:
            detail = "brief"
        unit.detail_level = detail
        unit.target_chars = _LEVEL_CHAR_BUDGET.get(content_level, _LEVEL_CHAR_BUDGET["推荐"])[detail]
        unit.detail_reason = f"{unit.depth_factors.reason}；课程级预算分配为 {detail}"
        unit.needs_visual = _requires_visual(unit, source_text)
        if unit.needs_visual:
            unit.visual_need = _merge_visual_contract(
                unit.visual_need,
                _visual_contract(unit, source_text, effective_visual_level),
            )
        else:
            unit.visual_need = VisualNeed(required=False)
        unit.visual_questions = _make_visual_questions(unit, source_text, unit.evidence_spans)
        unit.visual_need.required = bool(unit.visual_questions)
        if unit.visual_questions and not unit.visual_need.question:
            unit.visual_need.question = unit.visual_questions[0].question
    _limit_visual_questions(plan, transcript, effective_visual_level)
    return plan


def _limit_visual_questions(plan: LessonPlan, transcript: dict, visual_level: str) -> None:
    segments = transcript.get("segments", [])
    duration = max((float(row.get("end_seconds", 0.0)) for row in segments), default=0.0)
    if duration <= 900:
        limit = 4
    elif duration <= 2700:
        limit = 8
    else:
        limit = 12
    limit = max(1, round(limit * _VISUAL_LEVEL_FACTORS.get(visual_level, 1.0)))
    rows = [
        (_depth_priority(unit.depth_factors), unit.depth_factors.source_capacity, unit)
        for unit in plan.all_unit_plans
        if unit.visual_questions
    ]
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    # Limit the actual question total, not merely the number of units that happen
    # to contain questions. Each retained unit exposes only its primary question.
    keep = {id(unit) for _, _, unit in rows[:limit]}
    for _, _, unit in rows[limit:]:
        unit.visual_questions = []
        unit.visual_need = VisualNeed(required=False)
        unit.needs_visual = False
    for _, _, unit in rows[:limit]:
        unit.visual_questions = unit.visual_questions[:1]
        unit.visual_need.target_count = min(1, unit.visual_need.target_count)
        unit.visual_need.max_count = min(1, unit.visual_need.max_count)
        unit.visual_need.sequence_mode = "single"
        unit.needs_visual = id(unit) in keep


def plan_offline(
    transcript: dict,
    content_level: str,
    visual_level: str = "auto",
) -> LessonPlan:
    """离线启发式课程规划。"""
    segments = transcript.get("segments", [])
    merged = merge_transcript_segments(segments, max_chars=180, max_seconds=45.0)
    all_text = "。".join(row["text"] for row in merged)
    keyword_counts = _keyword_counts(merged)
    top_terms = [item for item, _ in keyword_counts.most_common(30)]

    domain = _match_domain(top_terms)
    form = _match_form(all_text)
    visual_profile = _infer_visual_profile(all_text, form)
    terminology = [term for term in top_terms[:15] if len(term) >= 2]
    core_thread = f"以{'、'.join(top_terms[:3])}为主线" if top_terms else "未识别主线"

    # 按 120s 间隙分章
    chapters: list[ChapterPlan] = []
    current_units: list[UnitPlan] = []
    current_seg_ids: list[str] = []
    chapter_index = 0
    plan_index = 0
    section_start = merged[0]["start_seconds"] if merged else 0.0
    section_end = merged[0]["end_seconds"] if merged else 0.0

    for row in merged:
        if current_units and row["start_seconds"] - section_end > 120.0:
            chapter_index += 1
            chapters.append(ChapterPlan(
                chapter_id=f"chapter_{chapter_index:03d}",
                title=f"章节 {chapter_index}",
                source_segment_ids=list(current_seg_ids),
                unit_plans=current_units,
            ))
            current_units = []
            current_seg_ids = []
            section_start = row["start_seconds"]

        text = row["text"]
        if _is_noise_text(text):
            current_seg_ids.extend(row.get("source_segment_ids", []))
            section_end = max(section_end, row["end_seconds"])
            continue
        ktypes = _infer_knowledge_types(text)
        info = len(text) / 30.0  # 简化信息量
        role = _infer_importance(text, info)
        plan_index += 1

        current_units.append(UnitPlan(
            plan_id=f"plan_{plan_index:03d}",
            title=text[:40] if text else f"知识点 {plan_index}",
            role=role,
            knowledge_types=ktypes,
            detail_level="standard",
            detail_reason=f"离线评分 {info:.1f}；角色 {role}",
            required_facets=_FACET_TEMPLATES.get(ktypes[0], []) if ktypes else [],
            source_segment_ids=list(row.get("source_segment_ids", [])),
            visual_need=VisualNeed(required=ktypes[0] == "visual_or_formula" if ktypes else False),
        ))
        current_seg_ids.extend(row.get("source_segment_ids", []))
        section_end = max(section_end, row["end_seconds"])

    if current_units:
        chapter_index += 1
        chapters.append(ChapterPlan(
            chapter_id=f"chapter_{chapter_index:03d}",
            title=f"章节 {chapter_index}",
            source_segment_ids=list(current_seg_ids),
            unit_plans=current_units,
        ))

    plan = LessonPlan(
        schema_version=_PLANNING_VERSION,
        domain=domain,
        course_form=form,
        core_thread=core_thread,
        terminology=terminology,
        visual_profile=visual_profile,
        chapters=chapters,
        side_topics=[],
    )
    return _assign_depth_contracts(plan, transcript, content_level, visual_level)


def plan_cloud(
    transcript: dict,
    content_level: str,
    settings: dict,
    *,
    cloud_port: Any,
    cancel_check=None,
) -> tuple[LessonPlan, dict[str, Any]]:
    """云端课程规划，返回 (plan, cloud_info)。"""

    source, source_blocks = build_cloud_source_blocks(transcript)
    if not source.strip():
        visual_level = str(settings.get("visual_teaching", {}).get("level", "auto"))
        return plan_offline(transcript, content_level, visual_level), {}

    visual_level = str(settings.get("visual_teaching", {}).get("level", "auto"))

    level_labels = {"精简": "复习提纲", "推荐": "标准课程笔记", "丰富": "完整课程讲义"}
    level_label = level_labels.get(content_level, "标准课程笔记")
    budget = settings.get("budget", {})
    max_chars = int(budget.get("max_input_chars", 60000))
    if len(source) > max_chars:
        raise RuntimeError(f"规划输入共 {len(source)} 字符，超过云端上限 {max_chars}；未发送请求")
    max_tokens = min(
        int(budget.get("max_output_tokens", 5000)),
        int(budget.get("planning_max_output_tokens", 3200)),
    )

    prompt = render_planning(
        content_level=content_level,
        level_label=level_label,
        max_tokens=max_tokens,
        transcript_sample=source,
        visual_level=visual_level,
    )
    if len(prompt) > max_chars:
        raise RuntimeError(f"规划请求共 {len(prompt)} 字符，超过云端上限 {max_chars}；未发送请求")

    parsed, request_info = cloud_port.request_json_with_info(
        {"messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": max_tokens},
        validator=lambda value: _validate_plan_payload(value, source_blocks),
        stage="planning",
        cancel_check=cancel_check or (lambda: False),
    )
    request_info = dict(request_info)
    model = str(request_info.get("model", ""))
    attempts = list(request_info.get("attempts", []))
    usage = dict(request_info.get("usage", {}))
    usage["source_chars"] = len(source)
    valid_segment_ids = {str(row.get("segment_id", "")) for row in transcript.get("segments", [])}

    def normalize_refs(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            ref = str(value).strip()
            if not ref:
                continue
            candidates = source_blocks.get(ref, [])
            if not candidates:
                candidates = [ref]
                match = re.fullmatch(r"seg_(\d+)", ref)
                if match:
                    candidates.append(f"seg_{int(match.group(1)):05d}")
            for candidate in candidates:
                if candidate in valid_segment_ids and candidate not in result:
                    result.append(candidate)
        return result

    # 从云端结果构建 LessonPlan
    plan = LessonPlan(
        schema_version=_PLANNING_VERSION,
        domain=str(parsed.get("domain", "")),
        course_form=str(parsed.get("course_form", "general")),
        core_thread=str(parsed.get("core_thread", "")),
        terminology=list(parsed.get("terminology", [])),
        visual_profile=(
            VisualProfile.from_dict(parsed.get("visual_profile", {}))
            if isinstance(parsed.get("visual_profile", {}), dict) and parsed.get("visual_profile")
            else _infer_visual_profile(source, str(parsed.get("course_form", "general")))
        ),
        chapters=[],
        side_topics=[],
    )

    for ch_data in parsed.get("chapters", []):
        chapter_refs = list(ch_data.get("source_block_ids", [])) + list(ch_data.get("source_segment_ids", []))
        chapter = ChapterPlan(
            chapter_id=str(ch_data.get("chapter_id", "")),
            title=str(ch_data.get("title", "")),
            source_segment_ids=normalize_refs(chapter_refs),
            unit_plans=[],
        )
        for up_data in ch_data.get("unit_plans", []):
            up_data = dict(up_data)
            refs = list(up_data.pop("source_block_ids", [])) + list(up_data.get("source_segment_ids", []))
            up_data["source_segment_ids"] = normalize_refs(refs)
            chapter.unit_plans.append(UnitPlan.from_dict(up_data))
        plan.chapters.append(chapter)

    for st_data in parsed.get("side_topics", []):
        st_data = dict(st_data)
        refs = list(st_data.pop("source_block_ids", [])) + list(st_data.get("source_segment_ids", []))
        st_data["source_segment_ids"] = normalize_refs(refs)
        plan.side_topics.append(SideTopic.from_dict(st_data))

    cloud_info = {
        "model": model,
        "attempts": attempts,
        "usage": usage,
    }
    return _assign_depth_contracts(plan, transcript, content_level, visual_level), cloud_info


def build_lesson_plan(
    transcript: dict,
    content_level: str,
    settings: dict,
    cloud: bool = False,
    *,
    cloud_port: Any = None,
    cancel_check=None,
    event_sink=None,
) -> tuple[LessonPlan, dict[str, Any]]:
    """生成课程写作计划；缓存由 execution Step 统一管理。"""
    visual_level = str(settings.get("visual_teaching", {}).get("level", "auto"))

    cloud_info: dict[str, Any] = {}
    if cloud and transcript.get("segments"):
        try:
            if cloud_port is None:
                raise ValueError("云端规划缺少 CloudJsonPort")
            plan, cloud_info = plan_cloud(
                transcript, content_level, settings,
                cloud_port=cloud_port, cancel_check=cancel_check,
            )
        except TaskCancelled:
            raise
        except Exception as exc:
            if event_sink is not None:
                event_sink({
                    "stage": "knowledge", "level": "warning",
                    "message": f"云端规划失败，已回退本地规划：{type(exc).__name__}: {exc}",
                    "code": "cloud_planning_fallback",
                })
            plan = plan_offline(transcript, content_level, visual_level)
    else:
        plan = plan_offline(transcript, content_level, visual_level)

    plan = _assign_depth_contracts(plan, transcript, content_level, visual_level)

    return plan, cloud_info
