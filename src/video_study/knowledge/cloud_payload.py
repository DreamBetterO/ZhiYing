"""Compact, validated CourseIR projection used by the canonical cloud writer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .schema import CourseIR

CLOUD_PAYLOAD_VERSION = 1


class CloudPayloadError(RuntimeError):
    """Raised before a cloud request when the compact payload is unsafe."""


@dataclass
class AllowedIDs:
    source_ids: set[str] = field(default_factory=set)
    unit_ids: set[str] = field(default_factory=set)
    claim_ids: set[str] = field(default_factory=set)
    visual_ids: set[str] = field(default_factory=set)


@dataclass
class CloudPayload:
    version: int
    sources: list[dict[str, Any]]
    units: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    visuals: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        claim_groups: dict[tuple[str, tuple[str, ...], str], list[dict[str, Any]]] = {}
        for claim in self.claims:
            key = (
                str(claim.get("unit_id", "")),
                tuple(str(item) for item in claim.get("source_ids", [])),
                str(claim.get("origin", "audio_backed")),
            )
            claim_groups.setdefault(key, []).append({
                "id": str(claim.get("id", "")),
                "kind": str(claim.get("kind", "explanation")),
                "text": str(claim.get("text", "")),
            })
        serialized_units: list[dict[str, Any]] = []
        for unit in self.units:
            row = dict(unit)
            row["src"] = row.pop("source_ids", [])
            unit_sources = tuple(str(item) for item in unit.get("source_ids", []))
            nested: list[dict[str, Any]] = []
            for (unit_id, source_ids, origin), claims in claim_groups.items():
                if unit_id != str(unit.get("id", "")):
                    continue
                group: dict[str, Any] = {"claims": claims}
                if source_ids != unit_sources:
                    group["source_ids"] = list(source_ids)
                if origin != "audio_backed":
                    group["origin"] = origin
                nested.append(group)
            row["claims"] = nested
            serialized_units.append(row)
        return {
            "sources": [dict(item) for item in self.sources],
            "units": serialized_units,
            "visuals": [dict(item) for item in self.visuals],
        }

    def json_text(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @property
    def char_count(self) -> int:
        return len(self.json_text())

    @property
    def allowed_ids(self) -> AllowedIDs:
        return AllowedIDs(
            source_ids={str(item["id"]) for item in self.sources},
            unit_ids={str(item["id"]) for item in self.units},
            claim_ids={str(item["id"]) for item in self.claims},
            visual_ids={str(item["visual_id"]) for item in self.visuals},
        )


@dataclass
class PayloadBatch:
    batch_id: str
    payload: CloudPayload
    char_count: int
    allowed_ids: AllowedIDs


@dataclass
class ValidatedCloudResult:
    response: dict[str, Any]
    referenced_ids: dict[str, list[str]]


_FORBIDDEN_KEYS = {
    "path", "image_path", "local_path", "base64", "image_hash", "image_sha256",
    "perceptual_hash", "runtime", "runtime_events", "device", "cache_signature",
    "no_match", "markdown", "pdf", "docx", "document_json", "transcript",
}


def _check_safe_value(value: Any, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("_path") or "base64" in normalized:
                raise CloudPayloadError(f"CloudPayload 含禁止字段：{'.'.join((*trail, str(key)))}")
            _check_safe_value(item, (*trail, str(key)))
    elif isinstance(value, list):
        for item in value:
            _check_safe_value(item, trail)
    elif isinstance(value, str):
        stripped = value.strip()
        if re.match(r"^(?:[A-Za-z]:[\\/]|/(?:home|Users|tmp|var|mnt)/)", stripped) or stripped.lower().startswith("data:image"):
            raise CloudPayloadError(f"CloudPayload 含本地路径或图片数据：{'.'.join(trail)}")


def _validate_payload_refs(payload: CloudPayload) -> None:
    allowed = payload.allowed_ids
    if not payload.sources or not payload.units or not payload.claims:
        raise CloudPayloadError("CloudPayload 缺少 sources、units 或 claims")
    for unit in payload.units:
        refs = set(str(item) for item in unit.get("source_ids", []))
        if not refs or not refs <= allowed.source_ids:
            raise CloudPayloadError(f"unit {unit.get('id')} 的 source_ids 非法")
    for claim in payload.claims:
        if str(claim.get("unit_id", "")) not in allowed.unit_ids:
            raise CloudPayloadError(f"claim {claim.get('id')} 引用了非法 unit_id")
        refs = set(str(item) for item in claim.get("source_ids", []))
        if claim.get("origin") == "audio_backed" and (not refs or not refs <= allowed.source_ids):
            raise CloudPayloadError(f"claim {claim.get('id')} 的 source_ids 非法")
    for visual in payload.visuals:
        if str(visual.get("unit_id", "")) not in allowed.unit_ids:
            raise CloudPayloadError(f"visual {visual.get('visual_id')} 引用了非法 unit_id")
    _check_safe_value(payload.to_dict())


def build_cloud_payload(course_ir: CourseIR) -> CloudPayload:
    """Project only referenced sources, canonical claims and selected visuals."""
    unit_ids = {unit.unit_id for unit in course_ir.units if unit.unit_id}
    referenced_sources = {
        source_id for unit in course_ir.units for source_id in unit.source_ids
    } | {
        source_id for claim in course_ir.claims for source_id in claim.source_ids
    }
    claimed_sources = {source_id for claim in course_ir.claims for source_id in claim.source_ids}
    sources = []
    for source in course_ir.sources:
        if source.source_id not in referenced_sources:
            continue
        row: dict[str, Any] = {
            "id": source.source_id,
            "t": [round(source.start_seconds, 3), round(source.end_seconds, 3)],
        }
        # Claims are the canonical writing projection. Preserve raw text only for
        # a referenced source that was not covered by any claim.
        if source.source_id not in claimed_sources:
            row["text"] = source.text
        sources.append(row)
    units = [{
        "id": unit.unit_id,
        "title": unit.title,
        "type": unit.type,
        "depth": unit.depth,
        "source_ids": [item for item in unit.source_ids if item in referenced_sources],
    } for unit in course_ir.units if unit.unit_id]
    claims = [{
        "id": claim.claim_id,
        "unit_id": claim.unit_id,
        "kind": claim.kind,
        "text": claim.text,
        "source_ids": list(claim.source_ids),
        "origin": claim.origin,
    } for claim in course_ir.claims if claim.unit_id in unit_ids and claim.text.strip()]
    visuals: list[dict[str, Any]] = []
    for visual in course_ir.visuals:
        if str(visual.get("decision", "select")) != "select":
            continue
        visual_id = str(visual.get("evidence_id", visual.get("visual_id", "")))
        unit_id = str(visual.get("matched_knowledge_point_id", visual.get("unit_id", "")))
        if not visual_id or not unit_id:
            continue
        visuals.append({
            "visual_id": visual_id,
            "unit_id": unit_id,
            "role": str(visual.get("visual_role", "explain")),
            "facts": [str(item) for item in visual.get("visible_evidence", []) if str(item)],
            "answer": str(visual.get("visual_answer", visual.get("visual_summary", ""))),
        })
    payload = CloudPayload(CLOUD_PAYLOAD_VERSION, sources, units, claims, visuals)
    _validate_payload_refs(payload)
    return payload


def _project_batch(payload: CloudPayload, unit_ids: list[str]) -> CloudPayload:
    allowed_units = set(unit_ids)
    units = [dict(item) for item in payload.units if item["id"] in allowed_units]
    claims = [dict(item) for item in payload.claims if item["unit_id"] in allowed_units]
    source_ids = {
        source_id for unit in units for source_id in unit.get("source_ids", [])
    } | {
        source_id for claim in claims for source_id in claim.get("source_ids", [])
    }
    sources = [dict(item) for item in payload.sources if item["id"] in source_ids]
    visuals = [dict(item) for item in payload.visuals if item["unit_id"] in allowed_units]
    projected = CloudPayload(payload.version, sources, units, claims, visuals)
    _validate_payload_refs(projected)
    return projected


def _estimated_output_tokens(payload: CloudPayload) -> int:
    weights = {"mention": 90, "brief": 180, "standard": 360, "deep": 620}
    return 240 + sum(weights.get(str(unit.get("depth", "standard")), 360) for unit in payload.units)


def plan_payload_batches(
    payload: CloudPayload,
    max_input_chars: int,
    max_output_tokens: int,
) -> list[PayloadBatch]:
    if max_input_chars <= 0 or max_output_tokens <= 0:
        raise CloudPayloadError("CloudPayload 输入/输出预算必须大于 0")
    if payload.char_count <= max_input_chars and _estimated_output_tokens(payload) <= max_output_tokens:
        return [PayloadBatch("batch_001", payload, payload.char_count, payload.allowed_ids)]
    batches: list[PayloadBatch] = []
    current: list[str] = []
    for unit in payload.units:
        candidate_ids = [*current, str(unit["id"])]
        candidate = _project_batch(payload, candidate_ids)
        if candidate.char_count <= max_input_chars and _estimated_output_tokens(candidate) <= max_output_tokens:
            current = candidate_ids
            continue
        if not current:
            raise CloudPayloadError(f"单个 unit {unit['id']} 已超过 CloudPayload 预算")
        projected = _project_batch(payload, current)
        batches.append(PayloadBatch(
            f"batch_{len(batches) + 1:03d}", projected, projected.char_count, projected.allowed_ids,
        ))
        current = [str(unit["id"])]
        single = _project_batch(payload, current)
        if single.char_count > max_input_chars or _estimated_output_tokens(single) > max_output_tokens:
            raise CloudPayloadError(f"单个 unit {unit['id']} 已超过 CloudPayload 预算")
    if current:
        projected = _project_batch(payload, current)
        batches.append(PayloadBatch(
            f"batch_{len(batches) + 1:03d}", projected, projected.char_count, projected.allowed_ids,
        ))
    return batches


def validate_cloud_response(
    response: dict[str, Any],
    allowed_ids: AllowedIDs,
) -> ValidatedCloudResult:
    if not isinstance(response, dict):
        raise CloudPayloadError("云端响应必须是 JSON 对象")
    referenced = {"source_ids": [], "unit_ids": [], "claim_ids": [], "visual_ids": []}
    id_fields = {
        "source_block_ids": (allowed_ids.source_ids, "source_ids"),
        "source_ids": (allowed_ids.source_ids, "source_ids"),
        "plan_id": (allowed_ids.unit_ids, "unit_ids"),
        "unit_id": (allowed_ids.unit_ids, "unit_ids"),
        "claim_id": (allowed_ids.claim_ids, "claim_ids"),
        "claim_ids": (allowed_ids.claim_ids, "claim_ids"),
        "visual_id": (allowed_ids.visual_ids, "visual_ids"),
        "binding_id": (allowed_ids.visual_ids, "visual_ids"),
        "binding_ids": (allowed_ids.visual_ids, "visual_ids"),
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in id_fields:
                    allowed, bucket = id_fields[key]
                    rows = item if isinstance(item, list) else [item]
                    for raw in rows:
                        identifier = str(raw).strip()
                        if identifier and identifier not in allowed:
                            raise CloudPayloadError(f"云端响应引用集合外 ID：{key}={identifier}")
                        if identifier and identifier not in referenced[bucket]:
                            referenced[bucket].append(identifier)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(response)
    return ValidatedCloudResult(dict(response), referenced)
