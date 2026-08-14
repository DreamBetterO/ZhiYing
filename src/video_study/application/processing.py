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
from ..pipeline import process_video
from ..progress import EtaEstimator, ProgressEvent
from .requests import AggregateRequest, ProcessingHandle, ProcessingRequest, ProcessingResult


def resolve_cloud_authorization(
    config: AppConfig,
    *,
    api_key: str = "",
    base_url: str = "",
    models: tuple[str, ...] = (),
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
    )


class ProcessingService(Protocol):
    def cached_result(self, video: Path) -> ProcessingResult | None: ...
    def process(self, request: ProcessingRequest) -> ProcessingHandle: ...
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

    def cached_result(self, video: Path) -> ProcessingResult | None:
        entry = self.catalog.find_by_source(video)
        if not entry:
            return None
        render = entry.manifest.get("stages", {}).get("render", {})
        paths = {kind: Path(str(render.get(kind, ""))) for kind in ("markdown", "docx", "pdf")}
        if not all(path.is_file() for path in paths.values()) or not entry.document_path.is_file():
            return None
        try:
            document = read_document_v2(entry.document_path)
        except (OSError, ValueError, json.JSONDecodeError):
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
                    handle.publish({"type": "task_progress", "event": payload})

                result = process_video(
                    config, request.video, force=force,
                    force_summary=request.action == "knowledge",
                    force_asr=request.action == "asr",
                    cloud_summary=bool(request.cloud and request.cloud.authorized),
                    qwen_settings=qwen,
                    asr_settings={**config.raw.get("asr", {}), "engine_chain": list(request.speech_models)},
                    progress=lambda stage, message, percent: handle.publish({
                        "type": "progress", "stage": stage, "message": message, "progress": percent,
                    }),
                    task_progress=publish_task_progress,
                    cancel_check=handle.cancelled,
                    event=handle.publish,
                )
                handle.finish(ProcessingResult.from_legacy(result))
            except BaseException as exc:
                handle.fail(exc)

        threading.Thread(target=worker, name="video-study-processing", daemon=True).start()
        return handle

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
