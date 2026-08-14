"""V2.2 可选 OCR 与本地视觉模型 provider。

所有能力都显式 opt-in。模块导入本身不会加载模型、占用 GPU、联网或下载权重。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import hashlib
from pathlib import Path
from typing import Any, Callable, Protocol

from ...progress import ProgressEvent
from ...utils import TaskCancelled, emit_runtime_event, ensure_not_cancelled, terminate_process


class VisualProviderError(RuntimeError):
    pass


class VisualProviderOOMError(VisualProviderError):
    pass


class VisualProviderSessionError(VisualProviderError):
    pass


class VisualModelProvider(Protocol):
    name: str

    def compare_candidates(
        self,
        question: dict[str, Any],
        candidates: list[dict[str, Any]],
        contract: dict[str, Any],
    ) -> dict[str, Any]: ...

    def extract_selected(
        self,
        image_or_roi: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]: ...


class RapidOCRProvider:
    """兼容 rapidocr-onnxruntime 与新版 rapidocr 的轻量包装。"""

    name = "rapidocr"

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except (ImportError, ModuleNotFoundError):
            try:
                from rapidocr import RapidOCR  # type: ignore
            except (ImportError, ModuleNotFoundError) as exc:
                raise VisualProviderError("未安装 RapidOCR；OCR 已安全禁用") from exc
        self._engine = RapidOCR()

    @staticmethod
    def _texts(result: Any) -> list[str]:
        if result is None:
            return []
        if hasattr(result, "txts"):
            return [str(item).strip() for item in result.txts if str(item).strip()]
        rows = result[0] if isinstance(result, tuple) else result
        if not isinstance(rows, (list, tuple)):
            return []
        texts: list[str] = []
        for row in rows:
            if isinstance(row, (list, tuple)) and len(row) >= 2 and isinstance(row[1], str):
                text = row[1].strip()
                if text:
                    texts.append(text)
        return texts

    def __call__(self, image_path: str) -> str:
        return "\n".join(self._texts(self._engine(image_path)))


def create_ocr_provider(settings: dict[str, Any]) -> tuple[Callable[[str], str] | None, str]:
    if not bool(settings.get("ocr_enabled", False)):
        return None, "OCR 未启用"
    try:
        return RapidOCRProvider(), ""
    except VisualProviderError as exc:
        return None, str(exc)


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class QwenVLSession:
    """One short-lived runner process that loads the model once per video."""

    def __init__(
        self,
        *,
        python: Path,
        runner: Path,
        runtime: Path,
        model: Path,
        timeout: float,
        settings: dict[str, Any],
    ) -> None:
        self.python = python
        self.runner = runner
        self.runtime = runtime
        self.model = model
        self.timeout = timeout
        self.settings = settings
        self.process: subprocess.Popen[str] | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.completed: dict[str, dict[str, Any]] = {}
        self.restart_count = 0
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.model_load_count = 0

    @staticmethod
    def _drain(stream: Any, target: list[str]) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            target.append(str(line).rstrip())

    def _wait_for(self, path: Path, started: float) -> dict[str, Any]:
        while True:
            ensure_not_cancelled(self.settings.get("_cancel_check"))
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
                else:
                    if not isinstance(value, dict):
                        raise VisualProviderSessionError("VLM 会话返回必须是 JSON 对象")
                    return value
            if self.process is not None and self.process.poll() is not None:
                diagnostic = "\n".join((self.stderr_lines + self.stdout_lines)[-12:])
                raise VisualProviderSessionError((diagnostic or "本地 VLM 会话意外退出")[-700:])
            if time.monotonic() - started >= self.timeout:
                raise VisualProviderSessionError(f"本地 VLM 会话超时（{int(self.timeout)} 秒）")
            time.sleep(0.05)

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self._temporary = tempfile.TemporaryDirectory(prefix="video-study-vlm-session-")
        self.root = Path(self._temporary.name)
        command = [
            str(self.python), "-u", str(self.runner),
            "--runtime", str(self.runtime), "--model", str(self.model),
            "--session", str(self.root),
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        threading.Thread(target=self._drain, args=(self.process.stdout, self.stdout_lines), daemon=True).start()
        threading.Thread(target=self._drain, args=(self.process.stderr, self.stderr_lines), daemon=True).start()
        ready = self._wait_for(self.root / "ready.json", time.monotonic())
        self.model_load_count += int(ready.get("model_load_count", 1) or 1)

    def _submit_once(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.start()
        assert self.root is not None
        request = self.root / f"request-{job_id}.json"
        result_path = self.root / f"result-{job_id}.json"
        _atomic_json(request, {"job_id": job_id, "payload": payload})
        response = self._wait_for(result_path, time.monotonic())
        error = str(response.get("error", "")).strip()
        if error:
            if "out of memory" in error.lower() or "cuda oom" in error.lower():
                raise VisualProviderOOMError("本地 VLM 显存不足")
            raise VisualProviderError(error[-700:])
        result = response.get("result", response)
        if not isinstance(result, dict):
            raise VisualProviderSessionError("本地 VLM 会话 result 必须是 JSON 对象")
        return result

    def submit(self, payload: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
        stable_id = job_id or hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        if stable_id in self.completed:
            return dict(self.completed[stable_id])
        try:
            result = self._submit_once(stable_id, payload)
        except TaskCancelled:
            self.close(force=True)
            raise
        except VisualProviderSessionError:
            if self.restart_count >= 1:
                self.close(force=True)
                raise
            self.restart_count += 1
            self.close(force=True, preserve_completed=True)
            result = self._submit_once(stable_id, payload)
        self.completed[stable_id] = dict(result)
        return result

    def run_jobs(self, jobs: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        for job_id, payload in jobs:
            self.submit(payload, job_id)
        return {key: dict(value) for key, value in self.completed.items()}

    def close(self, force: bool = False, preserve_completed: bool = False) -> None:
        process = self.process
        root = self.root
        if process is not None and process.poll() is None and not force and root is not None:
            try:
                _atomic_json(root / "done.json", {"done": True})
                process.wait(timeout=5.0)
            except (OSError, subprocess.TimeoutExpired):
                terminate_process(process)
        elif process is not None and process.poll() is None:
            terminate_process(process)
        self.process = None
        self.root = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if not preserve_completed:
            self.completed.clear()


class LocalQwenVLProvider:
    """通过 ImageT10 短生命周期子进程调用本地 Qwen3-VL。"""

    name = "qwen3-vl-2b-local"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        root = Path(str(settings.get("_config_root") or ".")).resolve()
        self.python = _resolve_path(root, str(settings.get(
            "local_vlm_runtime_python", "D:/Anaconda/envs/envs/ImageT10/python.exe",
        )))
        self.runtime = _resolve_path(root, str(settings.get(
            "local_vlm_runtime_dir", "models/qwen3-asr-runtime",
        )))
        model_value = str(settings.get("local_vlm_model_dir") or settings.get("local_vlm_model") or "models/qwen3-vl-2b-instruct")
        self.model = _resolve_path(root, model_value)
        self.runner = _resolve_path(root, str(settings.get("local_vlm_runner", "scripts/qwen_vl_runner.py")))
        self.timeout = max(30.0, float(settings.get("local_vlm_timeout_seconds", 180.0)))
        self.runtime_info: dict[str, Any] = {}
        self._session: QwenVLSession | None = None

    def _run_command(
        self,
        command: list[str],
        timeout: float,
        completion_path: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                ensure_not_cancelled(self.settings.get("_cancel_check"))
                if completion_path is not None and completion_path.is_file():
                    try:
                        json.loads(completion_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                    else:
                        # 结果已安全落盘，不再等待可能卡住的 CUDA 解释器析构。
                        terminate_process(process)
                        stdout, stderr = process.communicate()
                        return subprocess.CompletedProcess(command, 0, stdout, stderr)
                if time.monotonic() - started >= timeout:
                    terminate_process(process)
                    raise VisualProviderError(f"本地 VLM 超时（{int(timeout)} 秒）")
                time.sleep(0.1)
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except TaskCancelled:
            terminate_process(process)
            raise
        finally:
            if process.poll() is None:
                terminate_process(process)

    def preflight(self) -> dict[str, Any]:
        missing = [
            label for label, path in (
                ("ImageT10 Python", self.python),
                ("runtime", self.runtime),
                ("runner", self.runner),
                ("model", self.model),
            )
            if not path.exists()
        ]
        if missing:
            raise VisualProviderError(f"本地视觉能力缺少：{'、'.join(missing)}")
        command = [
            str(self.python), str(self.runner), "--preflight",
            "--runtime", str(self.runtime), "--model", str(self.model),
        ]
        completed = self._run_command(command, 60.0)
        if completed.returncode != 0:
            raise VisualProviderError((completed.stderr or completed.stdout or "VLM 预检失败").strip()[-500:])
        try:
            self.runtime_info = json.loads(completed.stdout)
            return dict(self.runtime_info)
        except json.JSONDecodeError as exc:
            raise VisualProviderError("VLM 预检未返回合法 JSON") from exc

    def start_session(self) -> float:
        """Start the per-video session and return cold-load duration."""
        if self._session is not None and self._session.process is not None and self._session.process.poll() is None:
            return 0.0
        progress_callback = self.settings.get("_progress_event_callback")
        if progress_callback:
            progress_callback(ProgressEvent(
                "visual", "cold_load", 0, 1, False,
                task_id="visual.cold_load",
                cache_state="miss",
                bucket=self.eta_bucket("cold_load"),
            ))
        if self._session is None:
            self._session = QwenVLSession(
                python=self.python,
                runner=self.runner,
                runtime=self.runtime,
                model=self.model,
                timeout=self.timeout,
                settings=self.settings,
            )
        started = time.monotonic()
        self._session.start()
        duration = max(0.001, time.monotonic() - started)
        if progress_callback:
            progress_callback(ProgressEvent(
                "visual", "cold_load", 1, 1, False, duration,
                task_id="visual.cold_load",
                cache_state="miss",
                bucket=self.eta_bucket("cold_load"),
            ))
        return duration

    def eta_bucket(self, unit_kind: str) -> str:
        return "|".join((
            "qwen3-vl-2b",
            "session",
            unit_kind,
            str(self.runtime_info.get("device", "gpu") or "gpu"),
        ))

    def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", "compare"))
        emit_runtime_event(
            self.settings, "visual", "info",
            "本地视觉模型正在比较候选帧" if action == "compare" else "本地视觉模型正在复核图片细节",
            code=f"vlm_{action}_started", device="gpu",
        )
        self.start_session()
        assert self._session is not None
        result = self._session.submit(payload)
        emit_runtime_event(
            self.settings, "visual", "info", "本地视觉模型已返回像素核对结果",
            code=f"vlm_{action}_completed", device="gpu",
        )
        return result

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def compare_candidates(
        self,
        question: dict[str, Any],
        candidates: list[dict[str, Any]],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        return self._invoke({
            "action": "compare",
            "question": question,
            "candidates": candidates,
            "contract": contract,
        })

    def extract_selected(
        self,
        image_or_roi: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        return self._invoke({
            "action": "detail",
            "candidate": image_or_roi,
            "contract": contract,
        })


def create_visual_provider(settings: dict[str, Any]) -> tuple[VisualModelProvider | None, str]:
    mode = settings.get("local_vlm_enabled", False)
    enabled = mode is True or str(mode).strip().lower() in {"1", "true", "yes", "on", "auto"}
    if not enabled:
        return None, "本地 VLM 未启用"
    provider = LocalQwenVLProvider(settings)
    automatic = str(mode).strip().lower() == "auto"
    try:
        info = provider.preflight()
    except VisualProviderError as exc:
        emit_runtime_event(
            settings, "visual", "info" if automatic else "warning", f"本地视觉能力不可用：{exc}",
            code="vlm_auto_unavailable" if automatic else "vlm_unavailable",
        )
        return None, str(exc)
    if not bool(info.get("ok", False)):
        emit_runtime_event(
            settings, "visual", "info" if automatic else "warning", "本地 Qwen3-VL 权重不完整；视觉阶段不会下载模型",
            code="vlm_auto_unavailable" if automatic else "vlm_model_incomplete",
        )
        return None, "本地 Qwen3-VL 权重不完整；未加载模型"
    if not bool(info.get("cuda_available", False)):
        emit_runtime_event(
            settings, "visual", "info" if automatic else "warning", "本地 Qwen3-VL 需要 CUDA，但当前未检测到可用 GPU",
            code="vlm_auto_unavailable" if automatic else "vlm_cuda_unavailable", device="cpu",
        )
        return None, "本地 Qwen3-VL 需要 CUDA；当前未检测到可用 GPU"
    device = "gpu" if bool(info.get("cuda_available", False)) else "cpu"
    emit_runtime_event(
        settings, "visual", "info",
        f"视觉能力就绪：Qwen3-VL · {'GPU' if device == 'gpu' else 'CPU'} · {info.get('device', '')}",
        code="vlm_ready", device=device, device_name=str(info.get("device", "")),
        automatic=automatic,
    )
    return provider, ""


class VisionSessionAdapter:
    def __init__(self, provider: VisualModelProvider) -> None:
        self.provider = provider

    def compare(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.provider.compare_candidates(
            dict(job.get("question", {})),
            list(job.get("candidates", [])),
            dict(job.get("contract", {})),
        )

    def detail(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.provider.extract_selected(
            dict(job.get("candidate", {})),
            dict(job.get("contract", {})),
        )

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close:
            close()


class VisionAdapter:
    """把取消/事件/进度显式注入旧 provider 边界的 VisionPort adapter。"""

    def __init__(self, settings: dict[str, Any], *, cancel_check, event_sink, progress_sink) -> None:
        self.settings = dict(settings)
        self.cancel_check = cancel_check
        self.event_sink = event_sink
        self.progress_sink = progress_sink

    def _settings(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = dict(self.settings)
        evidence = settings.pop("visual_evidence", {})
        if isinstance(evidence, dict):
            settings.update(evidence)
        if overrides:
            settings.update(overrides)
        return {
            **settings,
            "_cancel_check": self.cancel_check,
            "_event_callback": self.event_sink,
            "_progress_event_callback": self.progress_sink,
        }

    def preflight(self) -> dict[str, Any]:
        provider = LocalQwenVLProvider(self._settings())
        return provider.preflight()

    def open_session(self, options: dict[str, Any]) -> VisionSessionAdapter:
        provider, reason = create_visual_provider(self._settings(options))
        if provider is None:
            raise VisualProviderError(reason or "本地视觉能力不可用")
        start = getattr(provider, "start_session", None)
        if start:
            start()
        return VisionSessionAdapter(provider)
