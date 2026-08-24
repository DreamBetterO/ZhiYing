"""只读诊断一个视频 Workspace；不读取秘密、不初始化模型、不修改文件。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import yaml

from zhiying.execution.artifacts import STANDARD_ARTIFACTS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DOC = PROJECT_ROOT / "docs" / "architecture" / "pipeline-steps.yaml"
PROBLEM_INDEX = PROJECT_ROOT / "docs" / "diagnostics" / "problem-index.yaml"
ACTIVE_GRAPH_VERSION = "v6.1-editorial-tools-1"
TOOL_CONTRACT_VERSION = "tool-contract-v1"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _workspace_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if (resolved / "manifest.json").is_file() or (resolved / "state").is_dir():
        return resolved
    raise ValueError(f"不是视频 Workspace：{resolved}")


def _derive_interrupted(summary: dict[str, Any]) -> str:
    """检查 running 状态是否应派生为 interrupted（owner PID 已不存在）。"""
    if summary.get("status") != "running":
        return str(summary.get("status", "unknown"))
    metadata = summary.get("metadata") or {}
    runtime = metadata.get("runtime") or {}
    pid = runtime.get("process_id")
    if pid is None:
        return "interrupted"
    try:
        os.kill(int(pid), 0)
    except (OSError, ProcessLookupError):
        return "interrupted"
    except (ValueError, TypeError):
        return "interrupted"
    return "running"


def _find_failed_step(run_events: list[dict[str, Any]], state_steps: dict[str, Any]) -> dict[str, Any]:
    """优先从 step_state 事件中找最近的失败步骤，而非 run_lifecycle 汇总事件。"""
    for row in reversed(run_events):
        if (
            row.get("status") == "failed"
            and row.get("type") in {"step_state", "step_lifecycle"}
        ):
            return row
    failed_rows = [
        {"step_id": step_id, **value}
        for step_id, value in state_steps.items()
        if isinstance(value, dict) and value.get("status") == "failed"
    ]
    return failed_rows[-1] if failed_rows else {}


def _read_run_events(state_dir: Path, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not run_id:
        return rows
    run_path = state_dir / "runs" / f"{run_id}.jsonl"
    if not run_path.is_file():
        return rows
    for line in run_path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _recent_asr_events(run_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": row.get("sequence"),
            "code": row.get("code"),
            "message": row.get("message"),
            "level": row.get("level"),
            "engine": row.get("engine"),
            "device": row.get("device"),
            "failed_engine": row.get("failed_engine"),
            "next_engine": row.get("next_engine"),
            "reason": row.get("reason"),
            "error_type": row.get("error_type"),
        }
        for row in run_events
        if row.get("type") == "asr" or str(row.get("code", "")).startswith("asr_")
    ][-10:]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ffprobe_duration(path: Path) -> tuple[float | None, str]:
    if not path.is_file():
        return None, "missing"
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"probe_error:{type(exc).__name__}"
    if result.returncode != 0:
        return None, "probe_failed"
    duration = _float_or_none(result.stdout.strip())
    return duration, "" if duration is not None else "duration_unavailable"


def _media_checks(root: Path) -> dict[str, Any]:
    manifest = _json(root / "manifest.json")
    source_duration = _float_or_none(manifest.get("duration_seconds"))
    audio_path = root / "audio" / "audio.flac"
    audio_duration, audio_issue = _ffprobe_duration(audio_path)
    coverage = (
        round(audio_duration / source_duration, 4)
        if source_duration and source_duration > 0 and audio_duration is not None
        else None
    )
    incomplete = bool(
        source_duration
        and source_duration > 60.0
        and audio_duration is not None
        and audio_duration + 1.0 < source_duration * 0.95
    )
    issue = "audio_incomplete" if incomplete else audio_issue
    return {
        "source_duration_seconds": source_duration,
        "audio_duration_seconds": audio_duration,
        "audio_coverage_ratio": coverage,
        "audio_issue": issue,
    }


def _graph_checkpoint_summary(state_dir: Path) -> dict[str, Any]:
    database = state_dir / "graph-checkpoints.sqlite3"
    if not database.is_file():
        return {"present": False, "thread_count": 0, "graph_versions": [], "checkpoint_count": 0}
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            threads = connection.execute("SELECT graph_version, COUNT(*) FROM zhiying_graph_threads GROUP BY graph_version").fetchall()
            checkpoint_count = int(connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0])
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return {"present": True, "read_error": True, "thread_count": 0, "graph_versions": [], "checkpoint_count": 0}
    return {
        "present": True,
        "thread_count": sum(int(row[1]) for row in threads),
        "graph_versions": [str(row[0]) for row in threads],
        "checkpoint_count": checkpoint_count,
    }


def _artifact_target(root: Path, artifact_name: str) -> Path | None:
    registered = STANDARD_ARTIFACTS.get(artifact_name)
    if registered is not None and registered.storage_root == "workspace":
        return root / registered.relative_paths[0]
    relative = {
        "render.markdown.draft": "state/render/document.md",
        "render.word.draft": "state/render/document.docx",
        "render.pdf.draft": "state/render/document.pdf",
        # 历史诊断专用；不属于 V6.1 新写 STANDARD_ARTIFACTS。
        "document.v2": "knowledge/document.json",
        "document.plan": "knowledge/document-plan.json",
        "document.chapter_drafts": "knowledge/chapters/drafts.json",
        "document.chapter_validated": "knowledge/chapters/validated.json",
        "document.chapter_repaired": "knowledge/chapters/repaired.json",
    }.get(artifact_name)
    return root / relative if relative else None


def _quality_rerun(issues: list[dict[str, Any]]) -> str:
    codes = {str(row.get("code", "")) for row in issues}
    owners = {str(row.get("owner_component", "")) for row in issues}
    if "MATH_OMML_MISMATCH" in codes or "render.word" in owners:
        return "render.word"
    if "PDF_MISSING" in codes or "render.pdf" in owners:
        return "render.pdf"
    if "MARKDOWN_ABSOLUTE_PATH" in codes or "render.markdown" in owners:
        return "render.markdown"
    if any(code.startswith("EVIDENCE_") for code in codes):
        return "evidence.reconcile"
    if any(code.startswith("INTENT_") for code in codes):
        return "document.blueprint"
    if issues:
        return "document.write"
    return ""


def _v61_summary(
    root: Path,
    *,
    cache_rows: list[dict[str, Any]],
    graph_checkpoints: dict[str, Any],
    suggested_rerun: str,
) -> dict[str, Any]:
    policy = _json(root / "knowledge" / "editorial-policy.json")
    blueprint = _json(root / "knowledge" / "document-blueprint-v2.json")
    session = _json(root / "knowledge" / "editorial-session.json")
    document = _json(root / "knowledge" / "document-v3.json")
    validation = _json(root / "knowledge" / "document-validation.json")
    quality = validation.get("quality_report") if isinstance(validation.get("quality_report"), dict) else {}
    if not quality and isinstance(session.get("quality_report"), dict):
        quality = dict(session["quality_report"])
    page = session.get("page_report") if isinstance(session.get("page_report"), dict) else {}
    issues = [dict(row) for row in quality.get("issues", []) if isinstance(row, dict)]
    math = quality.get("math") if isinstance(quality.get("math"), dict) else {}
    visual = quality.get("visual") if isinstance(quality.get("visual"), dict) else {}
    evidence = quality.get("evidence") if isinstance(quality.get("evidence"), dict) else {}
    statistics = quality.get("statistics") if isinstance(quality.get("statistics"), dict) else {}
    page_statistics = page.get("statistics") if isinstance(page.get("statistics"), dict) else {}
    graph_versions = list(graph_checkpoints.get("graph_versions", []))
    graph_version = graph_versions[-1] if graph_versions else ACTIVE_GRAPH_VERSION
    quality_rerun = _quality_rerun(issues)
    return {
        "versions": {
            "graph_version": graph_version,
            "tool_contract_version": TOOL_CONTRACT_VERSION,
            "editorial_policy_version": policy.get("version", policy.get("schema_version")),
            "blueprint_version": blueprint.get("schema_version"),
            "document_contract_version": document.get("contract_version"),
            "renderer_capability_version": document.get("renderer_capability_version", "renderer-capability-v1"),
        },
        "provider": {
            "requested_capability": str(session.get("requested_capability", "")),
            "effective_capability": str(session.get("capability", "")),
            "terminal_status": str(session.get("terminal_status", "")),
            "model_chain": [str(row) for row in session.get("model_chain", [])],
            "tool_turns": int(session.get("tool_turns", 0) or 0),
            "token_usage": dict(session.get("usage", {})),
            "cache_hits": sum(
                1 for row in cache_rows
                if row.get("status") == "recorded" and row.get("reason") in {"CACHE_HIT", "LEGACY_ADOPTED"}
            ),
        },
        "provenance": dict(session.get("provenance", document.get("provenance", {})) or {}),
        "degradation_reasons": [str(row) for row in session.get("degradation_reasons", [])],
        "error_codes": [str(row) for row in session.get("error_codes", [])],
        "quality": {
            "status": str(quality.get("status", "unavailable")),
            "issue_ids": [str(row.get("issue_id") or row.get("code", "")) for row in issues],
            "owner_components": [str(row.get("owner_component", "")) for row in issues],
            "component_revision": int(session.get("document_revision", 0) or 0),
            "revision_cycles_used": int(session.get("revision_cycles_used", 0) or 0),
        },
        "statistics": {
            "equation_count": int(math.get("equation_components", statistics.get("equation", 0)) or 0),
            "word_omml_count": math.get("word_omml"),
            "image_count": int(visual.get("image_components", statistics.get("image", 0)) or 0),
            "source_reference_count": int(evidence.get("source_reference_components", statistics.get("source_reference", 0)) or 0),
            "page_audit_summary": {
                "status": str(page.get("status", "unavailable")),
                "issue_count": len(page.get("issues", [])),
                "page_break_count": int(page_statistics.get("page_break_components", 0) or 0),
            },
        },
        "suggested_rerun_node": quality_rerun or suggested_rerun or "evidence.reconcile",
    }


def diagnose(path: Path) -> dict[str, Any]:
    root = _workspace_root(path)
    state_dir = root / "state"
    pipeline = yaml.safe_load(PIPELINE_DOC.read_text(encoding="utf-8"))
    problem_index = yaml.safe_load(PROBLEM_INDEX.read_text(encoding="utf-8"))
    steps = list(pipeline.get("steps", []))
    state = _json(state_dir / "pipeline-state.json")
    state_steps = state.get("steps", {}) if isinstance(state.get("steps"), dict) else {}
    run_id = str(state.get("run_id", ""))

    run_events: list[dict[str, Any]] = []
    run_summary: dict[str, Any] = {}
    if run_id:
        run_events = _read_run_events(state_dir, run_id)
        run_summary = _json(state_dir / "runs" / f"{run_id}.summary.json")

    derived_status = _derive_interrupted(run_summary)
    failed = _find_failed_step(run_events, state_steps)
    legacy_v2 = root / "knowledge" / "document.json"
    current_v3 = root / "knowledge" / "document-v3.json"

    artifacts = []
    for step in steps:
        for artifact in step.get("outputs", []):
            if artifact in {row["artifact_id"] for row in artifacts}:
                continue
            target = _artifact_target(root, str(artifact))
            legacy_not_applicable = legacy_v2.is_file() and not current_v3.is_file() and str(artifact) in {
                "document.plan", "document.chapter_drafts", "document.chapter_validated",
                "document.chapter_repaired", "document.v3", "document.validation",
                "render.markdown.draft", "render.word.draft", "render.pdf.draft",
            }
            artifacts.append({
                "artifact_id": artifact,
                "status": "untracked_contract" if target is None else "present" if target.exists() else "legacy_not_applicable" if legacy_not_applicable else "missing",
                "path": "" if target is None else str(target),
            })

    cache_rows = []
    cache_dir = state_dir / "cache"
    for step in steps:
        value = _json(cache_dir / f"{step['id']}.json")
        cache_rows.append({
            "step_id": step["id"],
            "status": "recorded" if value else "none",
            "run_id": str(value.get("run_id", "")),
            "capability": str(value.get("produced_capability", "")),
            "reason": str(value.get("adoption_reason", "CACHE_HIT" if value else "NO_RECORD")),
        })

    error_code = str(failed.get("error_code") or "")
    known = next((row for row in problem_index.get("errors", []) if error_code.startswith(str(row["prefix"]))), {})
    last_step = str(failed.get("step_id") or "")
    summary_error = run_summary.get("error") if isinstance(run_summary.get("error"), dict) else {}
    diagnostics = failed.get("diagnostics") if isinstance(failed.get("diagnostics"), dict) else {}
    runs = []
    runs_dir = state_dir / "runs"
    for summary_path in sorted(runs_dir.glob("*.summary.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True):
        value = _json(summary_path)
        current_run_id = str(value.get("run_id", summary_path.name.removesuffix(".summary.json")))
        runs.append({
            "run_id": current_run_id,
            "status": _derive_interrupted(value),
            "started_at": value.get("started_at"),
            "finished_at": value.get("finished_at"),
            "summary": str(summary_path),
            "events": str(runs_dir / f"{current_run_id}.jsonl"),
            "readable_log": str(runs_dir / f"{current_run_id}.log"),
        })
    last_asr_events = _recent_asr_events(run_events)
    asr_source_run_id = run_id
    if not last_asr_events:
        transcript_cache = next(
            (row for row in cache_rows if row.get("step_id") == "transcript.decode"),
            {},
        )
        cache_run_id = str(transcript_cache.get("run_id") or "")
        if cache_run_id and cache_run_id != run_id:
            last_asr_events = _recent_asr_events(_read_run_events(state_dir, cache_run_id))
            asr_source_run_id = cache_run_id
    graph_checkpoints = _graph_checkpoint_summary(state_dir)
    suggested_rerun = str(known.get("safe_rerun_from", last_step))
    return {
        "workspace": str(root),
        "run_id": run_id,
        "run_status": derived_status,
        "last_failed_step": last_step,
        "error_code": error_code,
        "error_message": str(summary_error.get("message") or diagnostics.get("error_message") or failed.get("message") or ""),
        "exception_type": str(summary_error.get("exception_type") or diagnostics.get("exception_type") or ""),
        "traceback": str(summary_error.get("traceback") or ""),
        "owner": known.get("owner", ""),
        "suggested_rerun_step": suggested_rerun,
        "artifacts": artifacts,
        "cache": cache_rows,
        "media_checks": _media_checks(root),
        "runs": runs,
        "recent_asr_events": last_asr_events,
        "recent_asr_source_run_id": asr_source_run_id,
        "migration": {
            "mode": (
                "current_v3" if _json(current_v3).get("contract_version") == "document-v3.1"
                else "legacy_v3_read_only" if current_v3.is_file()
                else "legacy_v2_read_only" if legacy_v2.is_file()
                else "incomplete"
            ),
            "document_v2_present": legacy_v2.is_file(),
            "document_v3_present": current_v3.is_file(),
            "historical_workspace_preserved": True,
        },
        "graph_checkpoints": graph_checkpoints,
        "v61": _v61_summary(
            root, cache_rows=cache_rows, graph_checkpoints=graph_checkpoints,
            suggested_rerun=suggested_rerun,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="workspace/<video_id> 目录")
    args = parser.parse_args()
    try:
        report = diagnose(args.workspace)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
