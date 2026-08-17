"""只读诊断一个视频 Workspace；不读取秘密、不初始化模型、不修改文件。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DOC = PROJECT_ROOT / "docs" / "architecture" / "pipeline-steps.yaml"
PROBLEM_INDEX = PROJECT_ROOT / "docs" / "diagnostics" / "problem-index.yaml"


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

    artifacts = []
    for step in steps:
        for artifact in step.get("outputs", []):
            if artifact in {row["artifact_id"] for row in artifacts}:
                continue
            relative = {
                "source.manifest": "manifest.json", "audio.flac": "audio/audio.flac",
                "transcript.raw": "transcript/raw.json", "transcript.normalized": "transcript/transcript.json",
                "transcript.srt": "transcript/transcript.srt", "frames.candidates": "images/candidates.json",
                "frames.selected": "images/keyframes.json", "knowledge.plan": "knowledge/lesson-plan.json",
                "visual.jobs": "knowledge/visual-jobs/index.json", "visual.evidence": "knowledge/visual-evidence.json",
                "frames.semantics": "knowledge/frame-semantics.json", "knowledge.course_ir": "knowledge/course-ir.json",
                "knowledge.units": "knowledge/knowledge-units.json", "knowledge.selfcheck": "knowledge/selfcheck.json",
                "document.v2": "knowledge/document.json",
            }.get(str(artifact), "")
            target = root / relative if relative else root
            artifacts.append({"artifact_id": artifact, "status": "present" if target.exists() else "missing", "path": str(target)})

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
        "suggested_rerun_step": known.get("safe_rerun_from", last_step),
        "artifacts": artifacts,
        "cache": cache_rows,
        "media_checks": _media_checks(root),
        "runs": runs,
        "recent_asr_events": last_asr_events,
        "recent_asr_source_run_id": asr_source_run_id,
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
