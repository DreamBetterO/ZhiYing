from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import ArtifactStore
from .cache import WorkspaceCache
from .context import CloudCredentials, ProcessingContext, ProcessingOptions, RunPolicy, RuntimeServices
from .registry import StepRegistry
from .runner import PipelineRunner


@dataclass(frozen=True)
class ExecutionKernel:
    context: ProcessingContext
    registry: StepRegistry
    artifacts: ArtifactStore
    cache: WorkspaceCache

    def runner(self) -> PipelineRunner:
        return PipelineRunner(self.context, self.registry, self.artifacts, self.cache)


def build_execution_kernel(
    context: ProcessingContext,
    registry: StepRegistry,
    artifacts: ArtifactStore,
    cache: WorkspaceCache,
) -> ExecutionKernel:
    """P1 显式组合入口；P3 才在这里连接具体基础设施 adapter。"""
    registry.validate()
    return ExecutionKernel(context, registry, artifacts, cache)


def build_runtime_services(
    *,
    project_root: Path,
    model_dir: Path,
    options: ProcessingOptions,
    policy: RunPolicy,
    credentials: CloudCredentials | None = None,
    cloud_budget: Any = None,
    cancel_check: Callable[[], bool] = lambda: False,
    event_sink: Callable[[dict[str, Any]], None] = lambda _event: None,
    progress_sink: Callable[[dict[str, Any]], None] = lambda _event: None,
    stage_progress_sink: Callable[[str, str, int], None] = lambda _stage, _message, _percent: None,
    extra_factories: Mapping[str, Callable[[], Any]] | None = None,
) -> RuntimeServices:
    """唯一具体 adapter 组合根；所有 factory 均保持惰性。"""
    from ..asr import SpeechAdapter
    from .adapters.vision import VisionAdapter
    from ..media import MediaAdapter
    from ..render import DocumentAdapter
    from ..utils import LocalProcessAdapter

    factories: dict[str, Callable[[], Any]] = {
        "process": LocalProcessAdapter,
        "media": MediaAdapter,
        "speech": lambda: SpeechAdapter(
            model_dir,
            config_root=project_root,
            cancel_check=cancel_check,
            event_sink=event_sink,
        ),
        "vision": lambda: VisionAdapter(
            dict(options.visual),
            cancel_check=cancel_check,
            event_sink=event_sink,
            progress_sink=progress_sink,
        ),
        "document": lambda: DocumentAdapter(
            project_root,
            include_transcript=bool(options.render.get("include_full_transcript", True)),
        ),
    }
    factories.update(extra_factories or {})
    if policy.cloud_authorized:
        if credentials is None or not credentials.api_key or not credentials.base_url or not credentials.models:
            raise ValueError("已授权云端请求，但缺少本次运行所需凭据或模型")
        if cloud_budget is None:
            raise ValueError("已授权云端请求，但缺少单视频 CloudRequestBudget")

        def cloud_factory():
            from ..providers import OpenAICloudJsonAdapter
            return OpenAICloudJsonAdapter(
                api_key=credentials.api_key,
                base_url=credentials.base_url,
                models=list(credentials.models),
                budget=cloud_budget,
                timeout=float(options.knowledge.get("timeout_seconds", 90.0)),
                max_tokens=int(options.knowledge.get("max_output_tokens", 5000)),
            )

        factories["cloud"] = cloud_factory
    return RuntimeServices(
        cancel_check=cancel_check,
        event_sink=event_sink,
        progress_sink=progress_sink,
        stage_progress_sink=stage_progress_sink,
        port_factories=factories,
        cloud_budget=cloud_budget,
        credentials=credentials,
    )


def build_default_kernel(context: ProcessingContext, registry: StepRegistry) -> ExecutionKernel:
    """使用文件 Artifact/Cache 实现构造默认内核；仍由调用方显式提供 DAG。"""
    from .artifacts import FileArtifactStore
    from .cache import FileWorkspaceCache
    return build_execution_kernel(context, registry, FileArtifactStore(), FileWorkspaceCache())


def run_compatible_pipeline(
    config,
    video: Path,
    *,
    force: bool = False,
    force_summary: bool = False,
    cloud_summary: bool | None = None,
    force_asr: bool = False,
    qwen_settings: dict[str, Any] | None = None,
    asr_settings: dict[str, Any] | None = None,
    progress=None,
    task_progress=None,
    cancel_check=None,
    event=None,
) -> dict[str, Any]:
    """P4 生产 composition root；把旧公开参数编译为不可变 Context。"""
    import json
    import uuid

    from ..providers import CloudRequestBudget, cloud_request_limit
    from ..utils import TaskCancelled, quick_fingerprint, safe_name
    from .artifacts import (
        ArtifactId,
        DOCUMENT_V2,
        FileArtifactStore,
        LegacyArtifactAdapter,
        SOURCE_MANIFEST,
        TRANSCRIPT_NORMALIZED,
        WorkspaceCatalog,
        WorkspaceLayout,
    )
    from .cache import FileWorkspaceCache, LegacyAdoptingWorkspaceCache
    from .context import VideoSource
    from .events import RunEventJournal
    from .steps import build_coarse_steps

    source_path = Path(video).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"视频不存在：{source_path}")
    fingerprint = quick_fingerprint(source_path)
    video_id = f"{safe_name(source_path.stem, 48)}-{fingerprint}"
    output_root = config.path("paths", "output_dir")
    layout = WorkspaceLayout(config.path("paths", "workspace_dir"), video_id, output_root)
    run_id = uuid.uuid4().hex
    journal = RunEventJournal(layout, run_id, event)

    _last_logged_progress: dict[str, float] = {}

    def journal_task_progress(progress_event) -> None:
        if is_dataclass(progress_event):
            payload = asdict(progress_event)
        elif isinstance(progress_event, Mapping):
            payload = dict(progress_event)
        else:
            payload = {key: getattr(progress_event, key) for key in (
                "stage", "unit_kind", "completed", "total", "cache_hit",
                "duration_seconds", "task_id", "cache_state", "bucket",
            ) if hasattr(progress_event, key)}
        task_key = str(payload.get("task_id") or payload.get("stage") or "runtime")
        completed = float(payload.get("completed", 0))
        total = float(payload.get("total", 0))
        last = _last_logged_progress.get(task_key)
        # 只在完成量有 >1% 整体变化时写入 JSONL，UI 回调始终更新
        should_log = (
            last is None
            or completed >= total
            or abs(completed - last) / max(1.0, total) >= 0.01
        )
        if should_log:
            _last_logged_progress[task_key] = completed
            journal.publish({
                "type": "task_progress",
                "step_id": task_key,
                "stage": str(payload.get("stage") or "runtime"),
                "code": "task_progress",
                "message": f"任务进度 {completed:.0f}/{total:.0f}",
                "event": payload,
            })
        if task_progress:
            task_progress(progress_event)

    _last_stage_percent: dict[str, int] = {}

    def journal_stage_progress(stage: str, message: str, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        last = _last_stage_percent.get(stage)
        if last is None or percent != last:
            _last_stage_percent[stage] = percent
            journal.publish({
                "type": "stage_progress",
                "step_id": stage,
                "stage": stage.split(".", 1)[0],
                "code": "stage_progress",
                "message": message,
                "progress_percent": percent,
            })
        if progress:
            progress(stage, message, percent)

    raw_qwen = dict(qwen_settings or config.raw.get("qwen", {}))
    cloud_authorized = (
        bool(cloud_summary)
        if cloud_summary is not None
        else bool(raw_qwen.get("_runtime_api_key"))
    )
    credentials = None
    cloud_budget = None
    if cloud_authorized:
        credentials = CloudCredentials(
            api_key=str(raw_qwen.get("_runtime_api_key", "")),
            base_url=str(raw_qwen.get("_runtime_base_url", "")),
            models=tuple(str(item) for item in raw_qwen.get("_runtime_models", ())),
        )
        existing_budget = raw_qwen.get("_runtime_request_budget")
        cloud_budget = existing_budget if isinstance(existing_budget, CloudRequestBudget) else CloudRequestBudget(
            cloud_request_limit(raw_qwen)
        )
    safe_qwen = {
        key: value for key, value in raw_qwen.items()
        if key not in {
            "_runtime_api_key", "_runtime_base_url", "_runtime_models",
            "_runtime_request_budget", "_cancel_check", "_event_callback",
            "_progress_event_callback",
        }
    }
    safe_qwen["budget"] = {
        **safe_qwen.get("budget", {}),
        **config.raw.get("qwen", {}).get("budget", {}),
    }
    safe_qwen["timeout_seconds"] = config.raw.get("qwen", {}).get(
        "timeout_seconds", safe_qwen.get("timeout_seconds", 90.0)
    )
    asr_options = dict(asr_settings or config.raw.get("asr", {}))
    asr_options["_model_dir_identity"] = str(config.path("paths", "model_dir"))
    options = ProcessingOptions(
        asr=asr_options,
        frames=dict(config.raw.get("frames", {})),
        knowledge=safe_qwen,
        visual={
            "visual_teaching": dict(config.raw.get("visual_teaching", {})),
            "visual_evidence": dict(config.raw.get("visual_evidence", {})),
        },
        render=dict(config.raw.get("render", {})),
    )
    force_steps = set()
    if force:
        force_steps.update({
            "audio.extract", "transcript.decode", "transcript.normalize",
            "frames.candidates", "frames.select",
            "knowledge.plan", "visual.jobs", "visual.evidence", "frames.semantics",
            "knowledge.course_ir", "knowledge.units", "knowledge.selfcheck",
            "document.assemble", "render.bundle",
        })
    if force_asr:
        force_steps.add("transcript.decode")
    if force_summary:
        force_steps.update({
            "knowledge.course_ir", "knowledge.units",
            "knowledge.selfcheck", "document.assemble",
        })
    policy = RunPolicy(
        cloud_authorized=cloud_authorized,
        content_level=str(safe_qwen.get(
            "content_level",
            config.raw.get("render", {}).get(
                "content_level", config.raw.get("desktop", {}).get("content_level", "推荐")
            ),
        )),
        visual_level=str(config.raw.get("visual_teaching", {}).get("level", "auto")),
        target_steps=("render.bundle",),
        force_steps=frozenset(force_steps),
    )
    source = VideoSource(
        source_path, video_id, f"sha256:{fingerprint}", 0.0, source_path.stat().st_size,
    )
    services = build_runtime_services(
        project_root=config.root,
        model_dir=config.path("paths", "model_dir"),
        options=options,
        policy=policy,
        credentials=credentials,
        cloud_budget=cloud_budget,
        cancel_check=cancel_check or (lambda: False),
        event_sink=journal.publish,
        progress_sink=journal_task_progress,
        stage_progress_sink=journal_stage_progress,
        extra_factories={
            "journal": lambda: journal,
            "project_root": lambda: config.root,
        },
    )
    context = ProcessingContext(run_id, source, layout, options, policy, services)
    journal.start({
        "work_type": "video_processing",
        "source": {
            "path": source_path,
            "video_id": video_id,
            "fingerprint": source.fingerprint,
            "size_bytes": source.size_bytes,
        },
        "request": {
            "force": force,
            "force_summary": force_summary,
            "force_asr": force_asr,
            "cloud_authorized": cloud_authorized,
        },
        "policy": {
            "target_steps": policy.target_steps,
            "force_steps": sorted(policy.force_steps),
            "content_level": policy.content_level,
            "visual_level": policy.visual_level,
        },
        "settings": {
            "asr": dict(options.asr),
            "frames": dict(options.frames),
            "knowledge": dict(options.knowledge),
            "visual": dict(options.visual),
            "render": dict(options.render),
        },
        "runtime": {
            "python": __import__("sys").version.split()[0],
            "platform": __import__("platform").platform(),
            "process_id": __import__("os").getpid(),
            "product_version": __import__("video_study", fromlist=["__version__"]).__version__,
        },
        "cloud": {
            "authorized": cloud_authorized,
            "endpoint": credentials.base_url if credentials else "",
            "models": list(credentials.models) if credentials else [],
            "max_calls": cloud_budget.max_requests if cloud_budget else 0,
        },
    })
    try:
        title = safe_name(source_path.stem)
        render_artifact = ArtifactId(
            "render.bundle",
            (f"{title}.md", f"{title}.docx", f"{title}.pdf"),
            "output",
        )
        registry = StepRegistry(build_coarse_steps(render_artifact))
        artifact_store = FileArtifactStore()
        catalog = WorkspaceCatalog(layout.root, project_root=config.root)
        cache = LegacyAdoptingWorkspaceCache(
            FileWorkspaceCache(), LegacyArtifactAdapter(catalog), artifact_store,
        )
        kernel = build_execution_kernel(context, registry, artifact_store, cache)
        lease = catalog.acquire_lease(layout, run_id)
        try:
            state = kernel.runner().run()
        finally:
            lease.release()

        cancelled = next((outcome for outcome in state.outcomes.values() if outcome.status.value == "cancelled"), None)
        if cancelled:
            raise TaskCancelled(cancelled.error.message if cancelled.error else "任务已由用户取消")
        failed = next((outcome for outcome in state.outcomes.values() if outcome.status.value == "failed"), None)
        if failed:
            message = failed.error.message if failed.error else f"步骤失败：{failed.step_id}"
            raise RuntimeError(f"{failed.step_id} [{failed.error.code if failed.error else 'STEP_FAILED'}]：{message}")

        manifest_path = layout.artifact_paths(SOURCE_MANIFEST)[0]
        document_path = layout.artifact_paths(DOCUMENT_V2)[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = json.loads(document_path.read_text(encoding="utf-8"))
        render_paths = layout.artifact_paths(render_artifact)
        render_outcome = state.outcomes["render.bundle"]
        transcript = json.loads(
            layout.artifact_paths(TRANSCRIPT_NORMALIZED)[0].read_text(encoding="utf-8")
        )
        visual_runtime = dict(document.get("knowledge_pipeline", {}).get("visual_runtime", {}))
        asr_runtime = {
            "engine": transcript.get("engine"),
            "device": transcript.get("device"),
            "compute_type": transcript.get("compute_type"),
            "cache_hit": state.statuses["transcript.decode"].value == "cached",
        }
        if asr_runtime["cache_hit"]:
            cached_device = "GPU" if asr_runtime["device"] == "cuda" else "CPU" if asr_runtime["device"] == "cpu" else "设备未知"
            asr_compute = f"ASR 缓存（原{cached_device}）"
        else:
            asr_compute = "ASR GPU" if asr_runtime["device"] == "cuda" else "ASR CPU" if asr_runtime["device"] == "cpu" else "ASR 设备未知"
        visual_compute = (
            "视觉 GPU" if visual_runtime.get("gpu_used") else "视觉未配图"
        ) if int(visual_runtime.get("question_count", 0)) else "无视觉问题"
        pdf_mode = str(
            render_outcome.diagnostics.get("pdf_mode")
            or manifest.get("stages", {}).get("render", {}).get("pdf_mode")
            or "built_in"
        )
        result = {
            "video_id": video_id,
            "manifest": manifest_path,
            "markdown": render_paths[0],
            "docx": render_paths[1],
            "pdf": render_paths[2],
            "pdf_mode": pdf_mode,
            "mode": document.get("mode"),
            "model": document.get("model"),
            "model_attempts": document.get("model_attempts", []),
            "cloud_usage": document.get("cloud_usage", {}),
            "asr_runtime": asr_runtime,
            "visual_runtime": visual_runtime,
            "compute_summary": f"{asr_compute} · {visual_compute}",
        }
        journal.finish("succeeded", outputs={
            key: result[key] for key in ("video_id", "manifest", "markdown", "docx", "pdf", "pdf_mode", "mode")
        })
        result["runtime_events"] = list(journal.events)
        result["degradations"] = [
            row for row in journal.events if row.get("level") in {"warning", "error"}
        ]
        return result
    except BaseException as exc:
        import traceback
        journal.finish(
            "cancelled" if isinstance(exc, TaskCancelled) else "failed",
            error=exc,
            traceback_text=traceback.format_exc(),
        )
        raise


def discover_configured_videos(config) -> list[Path]:
    from ..media import discover_videos
    return discover_videos(config.path("paths", "input_dir"))
