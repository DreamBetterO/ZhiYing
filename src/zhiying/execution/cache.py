from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TYPE_CHECKING

from .artifacts import ArtifactRef
from .artifacts import ArtifactId, artifact_file_digest, canonical_json_bytes, canonical_json_hash, file_digest

if TYPE_CHECKING:
    from .context import ProcessingContext
    from .contracts import FingerprintMaterial, StepOutcome, StepSpec


class CacheReason(str, Enum):
    CACHE_HIT = "CACHE_HIT"
    NO_RECORD = "NO_RECORD"
    FORCED = "FORCED"
    STEP_VERSION_CHANGED = "STEP_VERSION_CHANGED"
    CONTRACT_CHANGED = "CONTRACT_CHANGED"
    UPSTREAM_CHANGED = "UPSTREAM_CHANGED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    CAPABILITY_INSUFFICIENT = "CAPABILITY_INSUFFICIENT"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    OUTPUT_CORRUPT = "OUTPUT_CORRUPT"
    PREVIOUS_RUN_INCOMPLETE = "PREVIOUS_RUN_INCOMPLETE"
    LEGACY_ADOPTED = "LEGACY_ADOPTED"


@dataclass(frozen=True)
class CacheDecision:
    hit: bool
    reason: CacheReason
    artifacts: tuple[ArtifactRef, ...] = ()
    produced_capability: str = "offline"
    changed_components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.hit and self.reason not in {CacheReason.CACHE_HIT, CacheReason.LEGACY_ADOPTED}:
            raise ValueError("cache hit 必须使用命中 reason code")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "changed_components", tuple(self.changed_components))


@dataclass(frozen=True)
class CacheRecord:
    step_id: str
    implementation_version: int
    contract_version: str
    cache_key: str
    input_components: Mapping[str, Any]
    outputs: tuple[ArtifactRef, ...]
    requested_capability: str
    produced_capability: str
    status: str
    run_id: str
    created_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_components", MappingProxyType(dict(self.input_components)))
        object.__setattr__(self, "outputs", tuple(self.outputs))


class WorkspaceCache(Protocol):
    def decide(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        inputs: Mapping,
    ) -> CacheDecision: ...

    def record(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        outcome: "StepOutcome",
    ) -> None: ...


def _artifact_to_dict(ref: ArtifactRef) -> dict[str, Any]:
    return {
        "artifact_id": ref.artifact_id.name,
        "relative_paths": list(ref.artifact_id.relative_paths),
        "storage_root": ref.artifact_id.storage_root,
        "path": str(ref.path),
        "digest": ref.digest,
        "metadata": dict(ref.metadata),
    }


def _artifact_from_dict(value: Mapping[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        ArtifactId(
            str(value["artifact_id"]),
            tuple(str(item) for item in value["relative_paths"]),
            str(value.get("storage_root", "workspace")),
        ),
        __import__("pathlib").Path(str(value["path"])),
        str(value.get("digest", "")),
        value.get("metadata", {}),
    )


class FileWorkspaceCache:
    def record_path(self, context: "ProcessingContext", step_id: str):
        return context.workspace.state_dir / "cache" / f"{step_id}.json"

    def decide(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        inputs: Mapping,
    ) -> CacheDecision:
        if spec.step_id in context.policy.force_steps:
            return CacheDecision(False, CacheReason.FORCED, changed_components=("run_policy.force_steps",))
        path = self.record_path(context, spec.step_id)
        if not path.is_file():
            return CacheDecision(False, CacheReason.NO_RECORD)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return CacheDecision(False, CacheReason.PREVIOUS_RUN_INCOMPLETE, changed_components=("cache_record",))
        if int(value.get("implementation_version", 0)) != spec.implementation_version:
            return CacheDecision(False, CacheReason.STEP_VERSION_CHANGED, changed_components=("implementation_version",))
        if str(value.get("contract_version", "")) != spec.contract_version:
            return CacheDecision(False, CacheReason.CONTRACT_CHANGED, changed_components=("contract_version",))
        current_components = json.loads(canonical_json_bytes(dict(fingerprint.components)))
        cached_components = value.get("input_components", {})
        if cached_components != current_components:
            changed = sorted(
                key for key in set(cached_components) | set(current_components)
                if cached_components.get(key) != current_components.get(key)
            )
            upstream = any(key.startswith("upstream.") for key in changed)
            return CacheDecision(
                False,
                CacheReason.UPSTREAM_CHANGED if upstream else CacheReason.CONFIG_CHANGED,
                changed_components=tuple(changed),
            )
        requested = (
            "cloud"
            if context.policy.cloud_authorized and "cloud" in spec.capabilities
            else "offline"
        )
        produced = str(value.get("produced_capability", "offline"))
        if requested == "cloud" and str(value.get("status", "")) == "degraded":
            return CacheDecision(
                False,
                CacheReason.CAPABILITY_INSUFFICIENT,
                changed_components=("status",),
            )
        if requested == "cloud" and produced not in {"cloud", "tool_native", "structured_only"}:
            return CacheDecision(False, CacheReason.CAPABILITY_INSUFFICIENT, changed_components=("capability",))
        try:
            artifacts = tuple(_artifact_from_dict(item) for item in value.get("outputs", []))
        except (KeyError, TypeError, ValueError):
            return CacheDecision(False, CacheReason.OUTPUT_CORRUPT, changed_components=("outputs",))
        for ref in artifacts:
            if not ref.path.exists():
                return CacheDecision(False, CacheReason.OUTPUT_MISSING, changed_components=(ref.artifact_id.name,))
            if ref.path.is_file():
                stat = ref.path.stat()
                if (
                    ref.metadata.get("size_bytes") == stat.st_size
                    and ref.metadata.get("mtime_ns") == stat.st_mtime_ns
                ):
                    continue
                try:
                    current_digest = artifact_file_digest(ref.artifact_id, ref.path)
                except OSError:
                    return CacheDecision(False, CacheReason.OUTPUT_MISSING, changed_components=(ref.artifact_id.name,))
                if ref.digest and current_digest != ref.digest:
                    return CacheDecision(False, CacheReason.OUTPUT_CORRUPT, changed_components=(ref.artifact_id.name,))
        reason = CacheReason.LEGACY_ADOPTED if value.get("adoption_reason") == CacheReason.LEGACY_ADOPTED.value else CacheReason.CACHE_HIT
        return CacheDecision(True, reason, artifacts, produced)

    def record(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        outcome: "StepOutcome",
    ) -> None:
        self._write_record(context, spec, fingerprint, outcome, adoption_reason=None)

    def adopt(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        artifacts: tuple[ArtifactRef, ...],
        *,
        capability: str = "offline",
    ) -> None:
        from .contracts import StepOutcome, StepStatus
        outcome = StepOutcome(
            spec.step_id,
            context.run_id,
            StepStatus.SUCCEEDED,
            capability=capability,
            artifacts=artifacts,
            diagnostics={"cache_reason": CacheReason.LEGACY_ADOPTED.value},
        )
        self._write_record(
            context,
            spec,
            fingerprint,
            outcome,
            adoption_reason=CacheReason.LEGACY_ADOPTED.value,
        )

    def _write_record(
        self,
        context: "ProcessingContext",
        spec: "StepSpec",
        fingerprint: "FingerprintMaterial",
        outcome: "StepOutcome",
        *,
        adoption_reason: str | None,
    ) -> None:
        if outcome.status.value not in {"succeeded", "degraded"}:
            raise ValueError("仅成功或降级的有效产物可写 CacheRecord")
        value = {
            "schema_version": 1,
            "step_id": spec.step_id,
            "implementation_version": spec.implementation_version,
            "contract_version": spec.contract_version,
            "cache_key": canonical_json_hash(dict(fingerprint.components)),
            "input_components": json.loads(canonical_json_bytes(dict(fingerprint.components))),
            "outputs": [_artifact_to_dict(ref) for ref in outcome.artifacts],
            "requested_capability": (
                "cloud"
                if context.policy.cloud_authorized and "cloud" in spec.capabilities
                else "offline"
            ),
            "produced_capability": outcome.capability,
            "status": outcome.status.value,
            "run_id": context.run_id,
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        if adoption_reason:
            value["adoption_reason"] = adoption_reason
        path = self.record_path(context, spec.step_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            json.loads(temporary.read_text(encoding="utf-8"))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class LegacyAdoptingWorkspaceCache:
    """单向认领 P4 之前的有效产物；认领成功后只走 FileWorkspaceCache。"""

    def __init__(self, base: FileWorkspaceCache, adapter, artifact_store) -> None:
        self.base = base
        self.adapter = adapter
        self.artifact_store = artifact_store

    def decide(self, context, spec, fingerprint, inputs) -> CacheDecision:
        decision = self.base.decide(context, spec, fingerprint, inputs)
        if decision.reason != CacheReason.NO_RECORD or spec.step_id in context.policy.force_steps:
            return decision
        if (
            any(artifact_id.storage_root == "output" for artifact_id in spec.outputs)
            and inputs
            and not all(bool(ref.metadata.get("legacy_adopted")) for ref in inputs.values())
        ):
            return decision
        artifacts = self._legacy_outputs(context, spec)
        if len(artifacts) != len(spec.outputs):
            return decision
        if not all(self.artifact_store.validate(context, ref) for ref in artifacts):
            return decision
        capability = "offline"
        if "cloud" in spec.capabilities:
            try:
                document = json.loads(artifacts[0].path.read_text(encoding="utf-8"))
                if bool(document.get("cloud_sent")) or str(document.get("mode", "")).startswith("cloud"):
                    capability = "cloud"
            except (OSError, ValueError, json.JSONDecodeError):
                return decision
            if context.policy.cloud_authorized and capability != "cloud":
                return CacheDecision(
                    False, CacheReason.CAPABILITY_INSUFFICIENT,
                    changed_components=("capability",),
                )
        self.base.adopt(context, spec, fingerprint, artifacts, capability=capability)
        return CacheDecision(True, CacheReason.LEGACY_ADOPTED, artifacts, capability)

    def record(self, context, spec, fingerprint, outcome) -> None:
        self.base.record(context, spec, fingerprint, outcome)

    def _legacy_outputs(self, context, spec) -> tuple[ArtifactRef, ...]:
        from .artifacts import (
            DOCUMENT_V2, TRANSCRIPT_RAW, ArtifactRef, _atomic_write_json, artifact_file_digest,
        )

        discovered = self.adapter.discover(context.source.path)
        refs: list[ArtifactRef] = []
        for artifact_id in spec.outputs:
            ref = discovered.get(artifact_id)
            if artifact_id == TRANSCRIPT_RAW:
                target = context.workspace.artifact_paths(TRANSCRIPT_RAW)[0]
                if target.is_file():
                    ref = ArtifactRef(
                        TRANSCRIPT_RAW, target, artifact_file_digest(TRANSCRIPT_RAW, target),
                        {"legacy_adopted": True},
                    )
                else:
                    raw = self.adapter.read_transcript_raw(context.source.path)
                    if raw is not None:
                        _atomic_write_json(target, dict(raw))
                        ref = ArtifactRef(
                            TRANSCRIPT_RAW, target, artifact_file_digest(TRANSCRIPT_RAW, target),
                            {"legacy_adopted": True, "reconstructed_from_normalized": True},
                        )
            if ref is None and artifact_id == DOCUMENT_V2:
                document = self.adapter.read_document_v2(context.source.path)
                if document is not None:
                    target = context.workspace.artifact_paths(DOCUMENT_V2)[0]
                    _atomic_write_json(target, dict(document))
                    ref = ArtifactRef(
                        DOCUMENT_V2, target, artifact_file_digest(DOCUMENT_V2, target),
                        {"legacy_adopted": True, "migrated_to_v2": True},
                    )
            if ref is None and artifact_id.storage_root == "output":
                paths = context.workspace.artifact_paths(artifact_id)
                if all(path.is_file() and path.stat().st_size > 0 for path in paths):
                    ref = ArtifactRef(
                        artifact_id, paths[0], artifact_file_digest(artifact_id, paths[0]),
                        {"legacy_adopted": True},
                    )
            if ref is None:
                return ()
            refs.append(ref)
        return tuple(refs)
