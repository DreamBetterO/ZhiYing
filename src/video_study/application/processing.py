from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from ..aggregate import aggregate_documents, local_aggregate_documents
from ..config import AppConfig
from ..execution.artifacts import WorkspaceCatalog, read_document_v2
from ..progress import EtaEstimator, ProgressEvent
from .requests import (
    AggregateRequest, JobHandle, JobRequest, JobResult,
    ProcessingHandle, ProcessingRequest, ProcessingResult,
)


def resolve_cloud_authorization(
    config: AppConfig,
    *,
    api_key: str = "",
    base_url: str = "",
    models: tuple[str, ...] = (),
    editorial_brief: str = "",
) -> "CloudAuthorization":
    from .requests import CloudAuthorization
    qwen = config.raw.get("qwen", {})
    key = str(api_key or os.getenv(qwen.get("api_key_env", "QWEN_API_KEY"), "")).strip()
    endpoint = str(base_url or os.getenv(
        qwen.get("base_url_env", "QWEN_BASE_URL"), qwen.get("default_base_url", ""),
    )).strip().rstrip("/")
    parsed = urlparse(endpoint)
    if not key or len(key) > 4096:
        raise ValueError("当前会话缺少有效 API Key")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("API URL 必须是有效且不含凭据的 http(s) 地址")
    env_models = tuple(
        item.strip() for item in str(os.getenv(qwen.get("model_chain_env", "QWEN_MODEL_CHAIN"), "")).split(",")
        if item.strip()
    )
    chain = tuple(dict.fromkeys(item.strip() for item in (
        models or env_models or tuple(qwen.get("default_models", ()))
    ) if item.strip()))
    if not chain:
        raise ValueError("没有可用的大语言模型")
    return CloudAuthorization(
        True, key, endpoint, chain,
        max_calls=int(qwen.get("budget", {}).get("max_calls_per_video", len(chain))),
        editorial_brief=editorial_brief,
    )


class ProcessingService(Protocol):
    def process(self, request: ProcessingRequest) -> ProcessingHandle: ...
    def process_job(self, request: JobRequest) -> JobHandle: ...
    def download_url(self, url: str, *, progress=None, cancel_check=None) -> dict: ...
    def aggregate(self, request: AggregateRequest) -> ProcessingResult: ...
    def local_aggregate(
        self, results: tuple[ProcessingResult, ...], *, cancel_check=None,
    ) -> ProcessingResult: ...
    def delete_video_workspace(self, video: Path) -> None: ...
    def clear_workspace(self) -> int: ...


class DefaultProcessingService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.catalog = WorkspaceCatalog(config.path("paths", "workspace_dir"), project_root=config.root)

    def history_result(self, video: Path) -> ProcessingResult | None:
        """Read-only history projection; never used to bypass Graph execution."""
        entry = self.catalog.find_by_source(video)
        if not entry or not entry.document_path.is_file():
            return None
        try:
            document = read_document_v2(entry.document_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        render = entry.manifest.get("stages", {}).get("render", {})
        output_value = str(self.config.raw.get("paths", {}).get("output_dir", "")).strip()
        output_dir = (
            (Path(output_value) if Path(output_value).is_absolute() else self.config.root / output_value)
            / entry.layout.video_id
            if output_value else Path()
        )

        def find_file(kind: str, extension: str) -> Path:
            candidate = Path(str(render.get(kind, "")))
            if candidate.is_file():
                return candidate
            if output_dir.is_dir():
                return next((path for path in output_dir.iterdir() if path.is_file() and path.suffix.lower() == extension), Path())
            return Path()

        paths = {
            "markdown": find_file("markdown", ".md"),
            "docx": find_file("docx", ".docx"),
            "pdf": find_file("pdf", ".pdf"),
        }
        if not paths["markdown"].is_file():
            return None
        return ProcessingResult(
            str(entry.manifest.get("video_id", "")), entry.manifest_path,
            paths["markdown"], paths["docx"], paths["pdf"],
            str(document.get("mode", "")), str(document.get("model", "")),
            dict(document.get("cloud_usage", {})),
        )

    def process(self, request: ProcessingRequest) -> ProcessingHandle:
        handle = ProcessingHandle()

        def worker() -> None:
            try:
                handle.finish(self._execute_request(request, handle))
            except BaseException as exc:
                handle.fail(exc)

        threading.Thread(target=worker, name="video-study-processing", daemon=True).start()
        return handle

    def process_job(self, request: JobRequest) -> JobHandle:
        import uuid

        from ..execution.checkpointing import SqliteCheckpointAdapter
        from ..execution.graphs.job_graph import JobGraph

        handle = JobHandle()

        def worker() -> None:
            try:
                current_index = -1

                class EventPort:
                    @staticmethod
                    def cancelled() -> bool:
                        return handle.cancelled()

                    @staticmethod
                    def publish(event: dict) -> None:
                        handle.publish({**dict(event), "source_index": current_index})

                def process_one(source: ProcessingRequest) -> dict:
                    nonlocal current_index
                    current_index += 1
                    return self._execute_request(source, EventPort()).to_legacy()

                def aggregate_rows(rows: list[dict]) -> dict:
                    results = tuple(ProcessingResult.from_legacy(row) for row in rows)
                    if request.aggregate_mode == "local":
                        return self.local_aggregate(results, cancel_check=handle.cancelled).to_legacy()
                    return self.aggregate(AggregateRequest(
                        results, request.cloud, cancel_check=handle.cancelled,
                    )).to_legacy()

                checkpoint_path = self.config.path("paths", "workspace_dir") / ".jobs" / "graph-checkpoints.sqlite3"
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                checkpoint = SqliteCheckpointAdapter(checkpoint_path, "v6-job-1")
                try:
                    state = JobGraph().run(
                        request.sources,
                        process=process_one,
                        aggregate_mode=request.aggregate_mode,
                        aggregate=aggregate_rows if request.aggregate_mode != "none" else None,
                        event_sink=handle.publish,
                        checkpoint_adapter=checkpoint,
                        thread_id=f"job:{uuid.uuid4().hex}",
                    )
                finally:
                    checkpoint.close()
                videos = tuple(ProcessingResult.from_legacy(row) for row in state["video_results"])
                aggregate_result = (
                    ProcessingResult.from_legacy(state["aggregate_result"])
                    if state.get("aggregate_result") else None
                )
                handle.finish(JobResult(videos, aggregate_result, str(state["status"])))
            except BaseException as exc:
                handle.fail(exc)

        threading.Thread(target=worker, name="video-study-job", daemon=True).start()
        return handle

    def _execute_request(self, request: ProcessingRequest, event_port) -> ProcessingResult:
        config = self._request_config(request)
        qwen = self._cloud_settings(request)
        force = request.action == "rebuild"
        estimator = EtaEstimator(
            config.path("paths", "workspace_dir") / ".eta-history.json",
            model=" + ".join(request.speech_models),
            content_level=request.content_level,
        )

        def publish_task_progress(event: ProgressEvent) -> None:
            estimator.observe(event)
            payload = asdict(event)
            payload["eta_seconds"] = estimator.estimate()
            event_port.publish({"type": "task_progress", "event": payload})

        runner = self._process_runner(request, config)
        result = runner(
            config, request, force=force,
            force_summary=request.action == "knowledge",
            force_asr=request.action == "asr",
            cloud_summary=bool(request.cloud and request.cloud.authorized),
            qwen_settings=qwen,
            asr_settings={**config.raw.get("asr", {}), "engine_chain": list(request.speech_models)},
            progress=lambda stage, message, percent: event_port.publish({
                "type": "progress", "stage": stage, "message": message, "progress": percent,
            }),
            task_progress=publish_task_progress,
            cancel_check=event_port.cancelled,
            event=event_port.publish,
        )
        return ProcessingResult.from_legacy(result)

    def _process_runner(self, request: ProcessingRequest, config: AppConfig):
        from ..execution.bootstrap import run_compatible_pipeline, run_compatible_pipeline_from_url

        if request.url:
            return lambda cfg, req, **kwargs: run_compatible_pipeline_from_url(cfg, req.url, **kwargs)
        return lambda cfg, req, **kwargs: run_compatible_pipeline(cfg, req.video, **kwargs)

    def download_url(self, url: str, *, progress=None, cancel_check=None) -> dict:
        from ..execution.bootstrap import acquire_source_from_url
        return dict(acquire_source_from_url(
            self.config, url, progress=progress, cancel_check=cancel_check,
        ))

    def aggregate(self, request: AggregateRequest) -> ProcessingResult:
        if not request.cloud.authorized:
            raise ValueError("聚合必须获得本次云端授权")
        settings = request.cloud.legacy_settings(self.config.raw.get("qwen", {}))
        settings["_cancel_check"] = request.cancel_check or (lambda: False)
        value = aggregate_documents(self.config, [item.to_legacy() for item in request.results], settings)
        return ProcessingResult.from_legacy(value)

    def local_aggregate(
        self, results: tuple[ProcessingResult, ...], *, cancel_check=None,
    ) -> ProcessingResult:
        """本地聚合：不使用云端，按队列顺序保守组合。"""
        value = local_aggregate_documents(
            self.config, [item.to_legacy() for item in results], cancel_check=cancel_check,
        )
        return ProcessingResult.from_legacy(value)

    def delete_video_workspace(self, video: Path) -> None:
        self.catalog.delete_video(video, self.config.path("paths", "output_dir"))

    def clear_workspace(self) -> int:
        return self.catalog.clear()

    def _request_config(self, request: ProcessingRequest) -> AppConfig:
        raw = deepcopy(self.config.raw)
        raw.setdefault("qwen", {})["content_level"] = request.content_level
        raw.setdefault("render", {})["content_level"] = request.content_level
        raw.setdefault("visual_teaching", {})["level"] = request.visual_level
        return AppConfig(self.config.root, raw)

    def _cloud_settings(self, request: ProcessingRequest) -> dict | None:
        auth = request.cloud
        if auth is None or not auth.authorized:
            return None
        return auth.legacy_settings(self.config.raw.get("qwen", {}))
