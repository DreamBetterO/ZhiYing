"""只读诊断一个视频 Workspace；不读取秘密、不初始化模型、不修改文件。"""
from __future__ import annotations

import argparse
import json
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
        run_path = state_dir / "runs" / f"{run_id}.jsonl"
        if run_path.is_file():
            for line in run_path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    run_events.append(value)
        run_summary = _json(state_dir / "runs" / f"{run_id}.summary.json")

    failed = next((row for row in reversed(run_events) if row.get("status") == "failed"), {})
    if not failed:
        failed_rows = [
            {"step_id": step_id, **value}
            for step_id, value in state_steps.items()
            if isinstance(value, dict) and value.get("status") == "failed"
        ]
        failed = failed_rows[-1] if failed_rows else {}

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
            "status": value.get("status"),
            "started_at": value.get("started_at"),
            "finished_at": value.get("finished_at"),
            "summary": str(summary_path),
            "events": str(runs_dir / f"{current_run_id}.jsonl"),
            "readable_log": str(runs_dir / f"{current_run_id}.log"),
        })
    return {
        "workspace": str(root),
        "run_id": run_id,
        "last_failed_step": last_step,
        "error_code": error_code,
        "error_message": str(summary_error.get("message") or diagnostics.get("error_message") or ""),
        "exception_type": str(summary_error.get("exception_type") or diagnostics.get("exception_type") or ""),
        "traceback": str(summary_error.get("traceback") or ""),
        "owner": known.get("owner", ""),
        "suggested_rerun_step": known.get("safe_rerun_from", last_step),
        "artifacts": artifacts,
        "cache": cache_rows,
        "runs": runs,
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
