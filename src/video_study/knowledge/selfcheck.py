"""轻量自检：低成本确定性检查，防止明显错误。

不建立大规模样本集或独立评测模型，只做生成期防错与统计。
V2 增加 plan 覆盖、facet_status、binding basis 和 understanding_tip 检查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .schema import KnowledgeUnit, LessonPlan, VisualBinding

# 方向/否定关键词——原文出现时整理结果不能静默删除
_DIRECTION_WORDS = [
    "不", "非", "无", "没有", "否", "同价", "相同",
    "前", "后", "上", "下", "高", "低", "升", "降",
    "例外", "除外", "边界", "限制",
    "成立", "不成立",
]


@dataclass
class CheckResult:
    """单条检查结果。"""
    level: str  # "pass", "warning", "error"
    check: str
    message: str
    unit_id: str = ""


@dataclass
class SelfCheckReport:
    """自检报告。"""
    results: list[CheckResult] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.level == "error"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.level == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [
                {"level": r.level, "check": r.check, "message": r.message, "unit_id": r.unit_id}
                for r in self.results
            ],
            "stats": self.stats,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "passed": self.passed,
        }


def _collect_segment_text(unit: KnowledgeUnit, segment_text_map: dict[str, str]) -> str:
    """收集知识单元关联的来源文本。"""
    texts: list[str] = []
    for ref in unit.evidence_refs:
        for sid in ref.get("segment_ids", []):
            if sid in segment_text_map:
                texts.append(segment_text_map[sid])
    return "；".join(texts)


def _check_source_existence(
    unit: KnowledgeUnit,
    valid_segment_ids: set[str],
    valid_frame_ids: set[str],
) -> list[CheckResult]:
    """检查来源 segment 和 frame 是否存在。"""
    results: list[CheckResult] = []
    has_segment = False
    for ref in unit.evidence_refs:
        for sid in ref.get("segment_ids", []):
            if sid not in valid_segment_ids:
                results.append(CheckResult(
                    level="error", check="source_existence",
                    message=f"引用了不存在的 segment: {sid}",
                    unit_id=unit.unit_id,
                ))
            else:
                has_segment = True
        for fid in ref.get("frame_ids", []):
            if fid not in valid_frame_ids:
                results.append(CheckResult(
                    level="warning", check="source_existence",
                    message=f"引用了不存在的 frame: {fid}",
                    unit_id=unit.unit_id,
                ))
    if unit.importance == "core" and not has_segment:
        results.append(CheckResult(
            level="error", check="source_existence",
            message="核心知识未引用任何真实 segment",
            unit_id=unit.unit_id,
        ))
    return results


def _check_direction_words(unit: KnowledgeUnit, source_text: str) -> list[CheckResult]:
    """检查原文中的方向/否定词是否在整理结果中保留。"""
    results: list[CheckResult] = []
    if not source_text:
        return results
    unit_text = " ".join([
        unit.definition_or_conclusion,
        " ".join(unit.exceptions),
        " ".join(unit.pitfalls),
        " ".join(str(r) for r in unit.rules),
        " ".join(str(b) for b in unit.branches),
    ])
    for word in _DIRECTION_WORDS:
        if word in source_text and word not in unit_text:
            results.append(CheckResult(
                level="warning", check="direction_word",
                message=f'原文出现"{word}"但整理结果中未保留',
                unit_id=unit.unit_id,
            ))
    return results


def _check_plan_coverage(
    units: list[KnowledgeUnit],
    lesson_plan: LessonPlan,
) -> list[CheckResult]:
    """检查 plan 和 unit 的覆盖关系。"""
    results: list[CheckResult] = []
    plan_ids = {up.plan_id for up in lesson_plan.all_unit_plans}
    unit_plan_ids = {u.plan_id for u in units if u.plan_id}

    # 每个 unit 的 plan_id 必须存在于 plan
    for unit in units:
        if unit.plan_id and unit.plan_id not in plan_ids:
            results.append(CheckResult(
                level="warning", check="plan_coverage",
                message=f"unit 引用了不存在的 plan_id: {unit.plan_id}",
                unit_id=unit.unit_id,
            ))

    # 每个 plan 必须有对应 unit（只提示不阻止）
    for plan_id in plan_ids - unit_plan_ids:
        results.append(CheckResult(
            level="warning", check="plan_coverage",
            message=f"plan_id {plan_id} 没有对应的知识单元",
            unit_id="",
        ))

    return results


def _check_facet_status(unit: KnowledgeUnit) -> list[CheckResult]:
    """检查 required_facets 是否有 facet_status 覆盖。"""
    results: list[CheckResult] = []
    if not unit.facet_status:
        return results
    for facet, status in unit.facet_status.items():
        if status not in ("present", "missing_in_source", "uncertain"):
            results.append(CheckResult(
                level="warning", check="facet_status",
                message=f"facet {facet} 的状态值不合法: {status}",
                unit_id=unit.unit_id,
            ))
    return results


def _check_expand_completeness(unit: KnowledgeUnit, lesson_plan: LessonPlan) -> list[CheckResult]:
    """deep/standard 的规则若无条件、步骤或边界，产生提示但不强行编造。"""
    results: list[CheckResult] = []
    if unit.type != "rule" or unit.importance != "core":
        return results
    if unit.detail_level not in ("deep", "standard"):
        return results
    if not unit.rules and not unit.procedure and not unit.exceptions and not unit.branches:
        results.append(CheckResult(
            level="warning", check="expand_completeness",
            message=f"标记为 {unit.detail_level} 的规则缺少条件、步骤或边界字段",
            unit_id=unit.unit_id,
        ))
    return results


def _check_conflicts(units: list[KnowledgeUnit]) -> list[CheckResult]:
    """同一术语出现明显相反结论时标记未解决。"""
    results: list[CheckResult] = []
    title_groups: dict[str, list[KnowledgeUnit]] = {}
    for unit in units:
        key = unit.title.strip()
        if key:
            title_groups.setdefault(key, []).append(unit)

    for title, group in title_groups.items():
        if len(group) < 2:
            continue
        conclusions = [u.definition_or_conclusion for u in group if u.definition_or_conclusion]
        if len(conclusions) < 2:
            continue
        has_negative = any(re.search(r"不(?:成立|算|是)", c) for c in conclusions)
        has_positive = any(
            re.search(r"(?<!(?:不))(?:成立|算是?|是)", c)
            for c in conclusions
        )
        if has_negative and has_positive:
            for unit in group:
                if not unit.unresolved:
                    results.append(CheckResult(
                        level="warning", check="conflict",
                        message=f'标题「{title}」出现相反结论，已标记为未解决',
                        unit_id=unit.unit_id,
                    ))
    return results


def _check_visual_evidence(
    unit: KnowledgeUnit,
    frames: list[dict],
    segment_frame_map: dict[str, list[str]],
) -> list[CheckResult]:
    """依赖画面的内容若没有匹配图，标记需回看画面确认。"""
    results: list[CheckResult] = []
    if unit.type != "visual_or_formula":
        return results

    has_frame = False
    for ref in unit.evidence_refs:
        if ref.get("frame_ids"):
            has_frame = True
            break
        for sid in ref.get("segment_ids", []):
            if segment_frame_map.get(sid):
                has_frame = True
                break

    if not has_frame and frames:
        results.append(CheckResult(
            level="warning", check="visual_evidence",
            message="依赖画面的内容没有匹配图，需回看画面确认",
            unit_id=unit.unit_id,
        ))
    return results


def _check_binding_basis(
    bindings: list[VisualBinding],
    valid_frame_ids: set[str],
    valid_unit_ids: set[str],
) -> list[CheckResult]:
    """检查图片绑定的依据和引用是否存在。"""
    results: list[CheckResult] = []
    for b in bindings:
        if b.frame_id and b.frame_id not in valid_frame_ids:
            results.append(CheckResult(
                level="warning", check="binding_basis",
                message=f"绑定引用了不存在的 frame_id: {b.frame_id}",
            ))
        if b.unit_id and b.unit_id not in valid_unit_ids:
            results.append(CheckResult(
                level="warning", check="binding_basis",
                message=f"绑定引用了不存在的 unit_id/plan_id: {b.unit_id}",
            ))
        if b.decision == "bind" and (b.basis == ["time"] or (len(b.basis) == 1 and b.basis[0] == "time")):
            results.append(CheckResult(
                level="warning", check="binding_basis",
                message=f"绑定 {b.frame_id}→{b.unit_id} 的 basis 只有 time",
            ))
    return results


def _check_understanding_tip_limits(units: list[KnowledgeUnit]) -> list[CheckResult]:
    """检查 understanding_tip 的数量和长度限制。"""
    results: list[CheckResult] = []
    total_tip_chars = 0
    total_body_chars = 0
    for unit in units:
        tips: list[str] = []
        body_chars = len(unit.definition_or_conclusion)
        for block in unit.content_blocks:
            if isinstance(block, dict) and block.get("type") == "understanding_tip":
                tip_text = str(block.get("text", ""))
                tips.append(tip_text)
                total_tip_chars += len(tip_text)
            elif isinstance(block, dict) and block.get("text"):
                body_chars += len(str(block.get("text", "")))
            elif isinstance(block, dict) and block.get("items"):
                body_chars += sum(len(str(i)) for i in block.get("items", []))
        total_body_chars += body_chars
        if len(tips) > 1:
            results.append(CheckResult(
                level="warning", check="understanding_tip",
                message=f"核心知识点 {unit.unit_id} 超过一个 understanding_tip",
                unit_id=unit.unit_id,
            ))
    if total_body_chars > 0 and total_tip_chars > total_body_chars * 0.1:
        results.append(CheckResult(
            level="warning", check="understanding_tip",
            message=f"理解提示总字符数 ({total_tip_chars}) 超过正文约 10% ({int(total_body_chars * 0.1)})",
        ))
    return results


def _compute_stats(units: list[KnowledgeUnit], lesson_plan: LessonPlan) -> dict[str, int]:
    """统计各 detail_level 数量和 plan 覆盖率。"""
    stats = {
        "deep": 0,
        "standard": 0,
        "brief": 0,
        "mention": 0,
        "total_units": len(units),
        "total_plans": len(lesson_plan.all_unit_plans),
        "plans_covered": 0,
    }
    for unit in units:
        if unit.detail_level in stats:
            stats[unit.detail_level] += 1
    covered_plan_ids = {u.plan_id for u in units if u.plan_id}
    stats["plans_covered"] = len(covered_plan_ids & {up.plan_id for up in lesson_plan.all_unit_plans})
    return stats


def run_selfcheck(
    units: list[KnowledgeUnit],
    lesson_plan: LessonPlan,
    transcript: dict,
    frames: dict | None = None,
    bindings: list[VisualBinding] | None = None,
) -> SelfCheckReport:
    """运行全部轻量自检。"""
    report = SelfCheckReport()
    valid_segment_ids = {row["segment_id"] for row in transcript.get("segments", [])}
    valid_frame_ids = {f.get("image_id", "") for f in (frames or {}).get("frames", [])}
    segment_text_map = {row["segment_id"]: row["text"] for row in transcript.get("segments", [])}
    valid_unit_ids = {u.unit_id for u in units} | {u.plan_id for u in units if u.plan_id}

    # 构建 segment → frame 映射
    segment_frame_map: dict[str, list[str]] = {}
    for frame in (frames or {}).get("frames", []):
        ts = float(frame.get("timestamp_seconds", 0))
        for seg in transcript.get("segments", []):
            if float(seg["start_seconds"]) <= ts <= float(seg["end_seconds"]):
                segment_frame_map.setdefault(seg["segment_id"], []).append(frame.get("image_id", ""))

    for unit in units:
        source_text = _collect_segment_text(unit, segment_text_map)
        report.results.extend(_check_source_existence(unit, valid_segment_ids, valid_frame_ids))
        report.results.extend(_check_direction_words(unit, source_text))
        report.results.extend(_check_facet_status(unit))
        report.results.extend(_check_expand_completeness(unit, lesson_plan))
        report.results.extend(_check_visual_evidence(unit, (frames or {}).get("frames", []), segment_frame_map))

    report.results.extend(_check_plan_coverage(units, lesson_plan))
    report.results.extend(_check_conflicts(units))
    report.results.extend(_check_understanding_tip_limits(units))

    if bindings:
        report.results.extend(_check_binding_basis(bindings, valid_frame_ids, valid_unit_ids))

    report.stats = _compute_stats(units, lesson_plan)
    from .dedup import run_dedup_gate
    _, dedup_report = run_dedup_gate(units)
    report.stats.update({
        "duplicate_claim_count": dedup_report.duplicate_claim_count,
        "containment_duplicate_count": dedup_report.containment_duplicate_count,
        "title_body_overlap_count": dedup_report.title_body_overlap_count,
        "near_duplicate_pairs": len(dedup_report.near_duplicate_pairs),
        "claims_without_source": dedup_report.claims_without_source,
    })
    return report
