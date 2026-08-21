from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import ArtifactStore
from .cache import WorkspaceCache
from .context import CloudCredentials, ProcessingContext, ProcessingOptions, RunPolicy, RuntimeServices
from .registry import StepRegistry


def summarize_terminal_status(statuses) -> str:
    """按最严重结果聚合单视频终态，不把 degraded 美化为 succeeded。"""
    values = {getattr(status, "value", status) for status in statuses}
    for terminal in ("cancelled", "failed", "degraded"):
        if terminal in values:
            return terminal
    return "succeeded"


@dataclass(frozen=True)
class ExecutionKernel:
    context: ProcessingContext
    registry: StepRegistry
    artifacts: ArtifactStore
    cache: WorkspaceCache

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
    from ..render import DocumentAdapterV31
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
        # V6.1 生产渲染端口：原生消费 Document v3.1（不经过 v3_to_v2）
        "document": lambda: DocumentAdapterV31(project_root),
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

        def cloud_tool_factory():
            from ..providers import OpenAICloudToolAdapter
            return OpenAICloudToolAdapter(
                api_key=credentials.api_key,
                base_url=credentials.base_url,
                model=credentials.models[0],
                timeout=float(options.knowledge.get("timeout_seconds", 90.0)),
                max_tokens=int(options.knowledge.get("tool_max_output_tokens", 2000)),
            )

        factories["cloud_tool"] = cloud_tool_factory
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
    source_url: str = "",
    display_title: str = "",
    video_id: str | None = None,
) -> dict[str, Any]:
    """P4 生产 composition root；把旧公开参数编译为不可变 Context。"""
    import json
    import uuid

    from ..providers import CloudRequestBudget, cloud_request_limit
    from ..utils import TaskCancelled, quick_fingerprint, safe_name
    from .artifacts import (
        ArtifactId,
        DOCUMENT_V3,
        EDITORIAL_SESSION,
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
    if video_id:
        resolved_video_id = video_id
    else:
        resolved_video_id = f"{safe_name(source_path.stem, 48)}-{fingerprint}"
    video_id = resolved_video_id
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
            "editorial.policy", "evidence.reconcile", "document.blueprint",
            "document.write", "document.assemble", "document.validate",
            "render.markdown", "render.word", "render.pdf", "render.verify",
        })
    if force_asr:
        force_steps.add("transcript.decode")
    if force_summary:
        force_steps.update({
            "knowledge.course_ir", "knowledge.units",
            "knowledge.selfcheck", "editorial.policy", "evidence.reconcile",
            "document.blueprint", "document.write", "document.assemble", "document.validate",
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
        target_steps=("render.verify",),
        force_steps=frozenset(force_steps),
    )
    source = VideoSource(
        source_path, video_id, f"sha256:{fingerprint}", 0.0, source_path.stat().st_size,
        source_url=source_url, display_title=display_title,
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
            from .checkpointing import SqliteCheckpointAdapter
            from .graph_runtime import GraphRuntime

            checkpoint = SqliteCheckpointAdapter(
                layout.state_dir / "graph-checkpoints.sqlite3", "v6.1-editorial-tools-1",
            )
            try:
                state = GraphRuntime().run_compatible_state(
                    kernel,
                    checkpoint_adapter=checkpoint,
                    thread_id=f"video:{video_id}:{run_id}",
                )
            finally:
                checkpoint.close()
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
        document_path = layout.artifact_paths(DOCUMENT_V3)[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = json.loads(document_path.read_text(encoding="utf-8"))
        editorial_session_path = layout.artifact_paths(EDITORIAL_SESSION)[0]
        editorial_session = (
            json.loads(editorial_session_path.read_text(encoding="utf-8"))
            if editorial_session_path.is_file() else {}
        )
        render_paths = layout.artifact_paths(render_artifact)
        render_outcome = state.outcomes["render.pdf"]
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
        terminal_status = summarize_terminal_status(state.statuses.values())
        degraded_steps = [
            step_id for step_id, outcome in state.outcomes.items()
            if outcome.status.value == "degraded"
        ]
        editorial_mode = str(
            editorial_session.get("capability")
            or document.get("provenance", {}).get("blueprint")
            or "local_deterministic"
        )
        result_mode = {
            "tool_native": "cloud_tool_native",
            "structured_only": "cloud_structured",
            "local_deterministic": "offline_extract",
        }.get(editorial_mode, "offline_extract")
        model_chain = [str(item) for item in editorial_session.get("model_chain", []) if str(item)]
        result = {
            "video_id": video_id,
            "manifest": manifest_path,
            "markdown": render_paths[0],
            "docx": render_paths[1],
            "pdf": render_paths[2],
            "pdf_mode": pdf_mode,
            # V6.1：真实记录编辑来源，不再用误导性 cloud_summary 标签
            "mode": result_mode,
            "model": " + ".join(model_chain),
            "model_attempts": [],
            "cloud_usage": dict(editorial_session.get("usage", {})),
            "status": terminal_status,
            "editorial_mode": editorial_mode,
            "degradation_summary": [
                *degraded_steps,
                *[str(item) for item in editorial_session.get("degradation_reasons", [])],
            ],
            "asr_runtime": asr_runtime,
            "visual_runtime": visual_runtime,
            "compute_summary": f"{asr_compute} · {visual_compute}",
        }
        journal.finish(terminal_status, outputs={
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


def _acquire_source_from_url_impl(
    config,
    url: str,
    *,
    source_port=None,
    progress=None,
    event=None,
    cancel_check=None,
) -> dict[str, Any]:
    """V5.0 链接源获取：预检 → 下载到本地缓存（或命中已下载缓存）。

    返回：
    - 命中缓存：{"path", "title", "url", "video_id", "cached": True}
    - 新下载：   {"path", "title", "url", "video_id", "cached": False,
                 "duration_seconds", "size_bytes", "extractor", "format"}
    下载完成前先做 ffprobe 时长一致性校验（acquire 内），失败抛 SourceError。

    下载保存根目录由 ``source.download_dir`` 配置决定（默认项目根 ``视频/``，
    可在桌面「添加视频链接」对话框左侧「保存地址」设置并持久化）。

    ``progress`` 为单参下载事件回调（Mapping：phase/percent/total_bytes/speed_bytes）。
    """
    import os

    from ..source import YtDlpSourceAdapter
    from ..utils import safe_name
    from .artifacts import WorkspaceCatalog

    source_cfg = dict(config.raw.get("source", {}))
    if not source_cfg.get("enabled", True):
        raise RuntimeError("链接源获取已禁用（source.enabled=false）")

    adapter = source_port or YtDlpSourceAdapter()
    candidate = adapter.preflight(
        url, options=source_cfg, cancel_check=cancel_check or (lambda: False),
    )
    source_url = str(candidate.get("url") or "")
    display_title = str(candidate.get("title") or "")

    catalog = WorkspaceCatalog(
        config.path("paths", "workspace_dir"), project_root=config.root,
    )
    existing = catalog.find_by_url(source_url)
    if existing:
        local = Path(str(existing.manifest.get("source_path") or ""))
        if local.is_file():
            return {
                "path": str(local), "title": display_title, "url": source_url,
                "video_id": str(candidate.get("video_id") or ""), "cached": True,
            }

    video_id = _stable_source_video_id(candidate)
    download_root = _source_download_dir(config, source_cfg)
    download_dir = download_root / video_id / "source"
    download_dir.mkdir(parents=True, exist_ok=True)
    target = download_dir / safe_name(display_title)

    def download_progress(event_payload) -> None:
        if progress:
            progress(event_payload)
        if event:
            event({
                "type": "runtime", "step_id": "source.acquire", "stage": "source",
                "level": "info", "code": "source_downloading", "message": "正在下载视频",
                "event": dict(event_payload),
            })

    acquired = adapter.acquire(
        candidate, target,
        options=source_cfg, cancel_check=cancel_check or (lambda: False),
        progress=download_progress,
    )
    return {
        "path": str(acquired["path"]), "title": display_title, "url": source_url,
        "video_id": str(acquired.get("video_id") or str(candidate.get("video_id") or "")),
        "cached": False,
        "duration_seconds": float(acquired.get("duration_seconds") or 0.0),
        "size_bytes": int(acquired.get("size_bytes") or 0),
        "extractor": str(acquired.get("extractor") or ""),
        "format": str(acquired.get("format") or ""),
    }


def acquire_source_from_url(
    config,
    url: str,
    *,
    source_port=None,
    progress=None,
    event=None,
    cancel_check=None,
) -> dict[str, Any]:
    """Resolve and verify a URL source through SourceGraph."""
    from .graphs.source_graph import SourceGraph

    state = SourceGraph().run_url(
        url,
        acquire=lambda source_url: _acquire_source_from_url_impl(
            config,
            source_url,
            source_port=source_port,
            progress=progress,
            event=event,
            cancel_check=cancel_check,
        ),
    )
    return dict(state["verified_source"])


def run_compatible_pipeline_from_url(
    config,
    url: str,
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
    source_port=None,
) -> dict[str, Any]:
    """链接源 Graph 入口：预检 → 下载/缓存 → 验证 → 复用 23 节点 Video Graph。

    - ``source.enabled: false`` 时拒绝调用（回滚开关）。
    - 已下载过的链接（``find_by_url`` 命中）直接复用本地文件，不重复下载/重复 ASR。
    - 下载完成并经 ffprobe 时长一致性校验（acquire 内）后进入 ``run_compatible_pipeline``。
    """
    from ..source import SourceError  # noqa: F401  (错误在 acquire 内抛出)
    from ..utils import safe_name  # noqa: F401  (供稳定身份使用)

    def download_progress(event_payload) -> None:
        if progress:
            percent = int(event_payload.get("percent") or 0)
            progress("source.acquire", f"下载：{percent}%", percent)

    acquired = acquire_source_from_url(
        config, url,
        source_port=source_port, progress=download_progress, event=event, cancel_check=cancel_check,
    )
    downloaded = Path(str(acquired["path"]))
    return run_compatible_pipeline(
        config, downloaded,
        force=force, force_summary=force_summary, cloud_summary=cloud_summary,
        force_asr=force_asr, qwen_settings=qwen_settings, asr_settings=asr_settings,
        progress=progress, task_progress=task_progress,
        cancel_check=cancel_check, event=event,
        source_url=str(acquired.get("url") or ""),
        display_title=str(acquired.get("title") or ""),
        video_id=str(acquired.get("video_id") or ""),
    )


def _stable_source_video_id(candidate: Mapping[str, Any]) -> str:
    """URL 源稳定身份：优先站点 id（如 B 站 BV 号），通用直链用规范化 URL 哈希。"""
    import hashlib

    from ..utils import safe_name

    source_id = str(candidate.get("video_id") or "").strip()
    if source_id and source_id not in {"url", "none"}:
        return safe_name(source_id, 48)
    url = str(candidate.get("url") or "")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return safe_name(f"url-{digest}", 48)


def _source_download_dir(config, source_cfg: Mapping[str, Any]) -> Path:
    """链接源下载保存根目录：优先 source.download_dir，默认项目根/视频。"""
    import os

    value = str(source_cfg.get("download_dir") or "视频").strip()
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = config.root / path
    return path.expanduser().resolve()
