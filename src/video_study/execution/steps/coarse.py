from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ...progress import ProgressEvent
from ..artifacts import (
    AUDIO_FLAC,
    DOCUMENT_V2,
    FRAMES_CANDIDATES,
    FRAMES_SELECTED,
    SOURCE_MANIFEST,
    TRANSCRIPT_RAW,
    TRANSCRIPT_NORMALIZED,
    TRANSCRIPT_SRT,
    ArtifactId,
    ArtifactRef,
)
from ..context import ProcessingContext
from ..contracts import (
    ExecutionCancelled,
    FingerprintMaterial,
    RemoteCost,
    StepOutcome,
    StepSpec,
    StepStatus,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Artifact 顶层必须是 JSON 对象：{path.name}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _input(inputs: Mapping[ArtifactId, ArtifactRef], artifact_id: ArtifactId) -> ArtifactRef:
    try:
        return inputs[artifact_id]
    except KeyError as exc:
        raise ValueError(f"缺少上游 Artifact：{artifact_id.name}") from exc


def _fingerprint(context: ProcessingContext, inputs: Mapping[ArtifactId, ArtifactRef], section: str) -> FingerprintMaterial:
    components: dict[str, Any] = {
        f"config.{section}": getattr(context.options, section, {}),
        "source.fingerprint": context.source.fingerprint,
    }
    for artifact_id, ref in sorted(inputs.items(), key=lambda item: item[0].name):
        components[f"upstream.{artifact_id.name}"] = ref.digest
    return FingerprintMaterial(components)


def _emit(context: ProcessingContext, step_id: str, stage: str, level: str, message: str, code: str, **details) -> None:
    context.services.event_sink({
        "timestamp": _now(),
        "type": "runtime",
        "run_id": context.run_id,
        "step_id": step_id,
        "stage": stage,
        "level": level,
        "message": message,
        "code": code,
        **details,
    })


def _stage_progress(context: ProcessingContext, stage: str, message: str, percent: int) -> None:
    context.services.stage_progress_sink(stage, message, percent)


def _task_progress(context: ProcessingContext, event: ProgressEvent) -> None:
    context.services.progress_sink(event)


def _cancelled_exception(exc: BaseException) -> bool:
    return isinstance(exc, ExecutionCancelled) or type(exc).__name__ == "TaskCancelled"


@dataclass
class SourceProbeStep:
    spec = StepSpec(
        "source.probe", 1,
        outputs=(SOURCE_MANIFEST,),
        config_keys=(), remote_cost=RemoteCost.NONE,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_coarse_pipeline.py",), error_code_prefix="SOURCE",
        contract_version="source-manifest-v1",
    )

    def fingerprint(self, context, _inputs) -> FingerprintMaterial:
        return FingerprintMaterial({
            "source.fingerprint": context.source.fingerprint,
            "source.size_bytes": context.source.size_bytes,
        })

    def execute(self, context, _inputs, staging_dir: Path) -> StepOutcome:
        _emit(context, self.spec.step_id, "queued", "info", f"开始处理：{context.source.path.name}", "video_started")
        probe = context.services.port("media").probe(context.source.path)
        duration = float(probe.get("format", {}).get("duration", context.source.duration_seconds) or 0.0)
        manifest = {
            "schema_version": 1,
            "video_id": context.source.video_id,
            "title": context.source.path.stem,
            "source_path": str(context.source.path),
            "fingerprint": context.source.fingerprint.removeprefix("sha256:"),
            "duration_seconds": duration,
            "size_bytes": context.source.size_bytes,
            "created_at": _now(),
            "probe": probe,
            "stages": {},
        }
        output = staging_dir / SOURCE_MANIFEST.relative_paths[0]
        _write_json(output, manifest)
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(SOURCE_MANIFEST, output),),
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        manifest = _json(outcome.artifacts[0].path)
        if manifest.get("video_id") is None or float(manifest.get("duration_seconds", 0)) < 0:
            raise ValueError("source manifest 无效")


@dataclass
class AudioExtractStep:
    spec = StepSpec(
        "audio.extract", 1, dependencies=("source.probe",),
        inputs=(SOURCE_MANIFEST,), outputs=(AUDIO_FLAC,),
        config_keys=(), remote_cost=RemoteCost.LOCAL_HEAVY,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_coarse_pipeline.py",), error_code_prefix="AUDIO",
        contract_version="flac-16k-mono-v1",
    )

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        return FingerprintMaterial({
            "source.fingerprint": context.source.fingerprint,
            "upstream.source.manifest": _input(inputs, SOURCE_MANIFEST).digest,
            "audio.format": "flac-16k-mono-v1",
        })

    def execute(self, context, _inputs, staging_dir: Path) -> StepOutcome:
        _stage_progress(context, "audio", "正在提取或复用音频", 10)
        _task_progress(context, ProgressEvent(
            "audio", "extract", 0, 1, False,
            task_id="audio.extract", cache_state="miss", bucket="ffmpeg-flac",
        ))
        _emit(context, self.spec.step_id, "audio", "info", "正在从视频提取 16 kHz 单声道音频", "audio_extract_started", cache_hit=False)
        output = staging_dir / AUDIO_FLAC.relative_paths[0]
        output.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            context.services.port("media").extract_audio(
                context.source.path, output, cancel_check=context.services.cancelled,
            )
        except BaseException as exc:
            if _cancelled_exception(exc):
                raise ExecutionCancelled(str(exc)) from exc
            raise
        _task_progress(context, ProgressEvent(
            "audio", "extract", 1, 1, False, max(0.001, time.monotonic() - started),
            task_id="audio.extract", cache_state="miss", bucket="ffmpeg-flac",
        ))
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(AUDIO_FLAC, output),),
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        if not outcome.artifacts[0].path.is_file() or outcome.artifacts[0].path.stat().st_size == 0:
            raise ValueError("音频 Artifact 缺失或为空")


@dataclass
class TranscriptDecodeStep:
    spec = StepSpec(
        "transcript.decode", 1, dependencies=("audio.extract", "source.probe"),
        inputs=(AUDIO_FLAC, SOURCE_MANIFEST),
        outputs=(TRANSCRIPT_RAW,), config_keys=("asr.decode",), remote_cost=RemoteCost.LOCAL_HEAVY,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_fine_pipeline.py",), error_code_prefix="TRANSCRIPT_DECODE",
        contract_version="raw-transcript-v1",
    )

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        options = {
            key: value for key, value in dict(context.options.asr).items()
            if key not in {"terminology_replacements", "_preserve_cached_engine"}
            and not key.startswith("_runtime_")
        }
        return FingerprintMaterial({
            "upstream.audio.flac": _input(inputs, AUDIO_FLAC).digest,
            "asr.decode": options,
            "source.title": context.source.path.stem,
        })

    def execute(self, context, inputs, staging_dir: Path) -> StepOutcome:
        manifest = _json(_input(inputs, SOURCE_MANIFEST).path)
        audio = _input(inputs, AUDIO_FLAC).path
        output = staging_dir / TRANSCRIPT_RAW.relative_paths[0]
        output.parent.mkdir(parents=True, exist_ok=True)
        audio_seconds = max(1.0, float(manifest.get("duration_seconds", 0.0) or 0.0))
        options = dict(context.options.asr)
        options["_duration_seconds"] = audio_seconds
        engines = list(options.pop("_engine_chain", (options.get("engine", "faster-whisper"),)))
        _stage_progress(context, "asr", "正在执行本地语音识别", 30)
        last_error: BaseException | None = None
        transcript: dict[str, Any] | None = None
        started = time.monotonic()
        for index, engine in enumerate(engines):
            current = {**options, "engine": engine, "context": str(manifest.get("title", ""))}
            _stage_progress(context, "asr", f"正在使用 {engine} 执行语音识别", 20)

            def progress(fraction: float) -> None:
                bounded = max(0.0, min(1.0, fraction))
                _stage_progress(context, "asr", "正在执行本地语音识别", 20 + int(bounded * 40))
                _task_progress(context, ProgressEvent(
                    "asr", "audio_second", bounded * audio_seconds, audio_seconds, False,
                    task_id="asr.transcribe", cache_state="miss", bucket=f"{engine}|{options.get('device', 'auto')}",
                ))

            try:
                transcript = context.services.port("speech").decode(
                    audio, output, current,
                    cancel_check=context.services.cancelled,
                    progress=progress,
                )
                break
            except BaseException as exc:
                if _cancelled_exception(exc):
                    raise ExecutionCancelled(str(exc)) from exc
                last_error = exc
                if index + 1 < len(engines):
                    _emit(
                        context, self.spec.step_id, "asr", "warning",
                        f"语音模型 {engine} 失败，已降级到 {engines[index + 1]}：{type(exc).__name__}: {exc}",
                        "asr_engine_fallback", failed_engine=engine, next_engine=engines[index + 1],
                    )
        if transcript is None:
            raise last_error or RuntimeError("所有语音模型均不可用")
        engine = str(transcript.get("engine") or engines[0])
        _task_progress(context, ProgressEvent(
            "asr", "audio_second", audio_seconds, audio_seconds, False,
            max(0.001, time.monotonic() - started) / audio_seconds,
            task_id="asr.transcribe", cache_state="miss", bucket=f"{engine}|{transcript.get('device', options.get('device', 'auto'))}",
        ))
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(TRANSCRIPT_RAW, output),),
            diagnostics={
                "engine": transcript.get("engine"), "device": transcript.get("device"),
                "compute_type": transcript.get("compute_type"),
            },
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        transcript = _json(outcome.artifacts[0].path)
        if not isinstance(transcript.get("segments"), list):
            raise ValueError("原始转写 Artifact 缺少 segments")


@dataclass
class TranscriptNormalizeStep:
    spec = StepSpec(
        "transcript.normalize", 1, dependencies=("transcript.decode", "source.probe"),
        inputs=(TRANSCRIPT_RAW, SOURCE_MANIFEST), outputs=(TRANSCRIPT_NORMALIZED, TRANSCRIPT_SRT),
        config_keys=("asr.terminology_replacements",), remote_cost=RemoteCost.NONE,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_fine_pipeline.py",), error_code_prefix="TRANSCRIPT_NORMALIZE",
        contract_version="normalized-transcript-v1",
    )

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        return FingerprintMaterial({
            "upstream.transcript.raw": _input(inputs, TRANSCRIPT_RAW).digest,
            "upstream.source.manifest": _input(inputs, SOURCE_MANIFEST).digest,
            "terminology_replacements": dict(context.options.asr).get("terminology_replacements", {}),
            "normalization.version": 1,
        })

    def execute(self, context, inputs, staging_dir: Path) -> StepOutcome:
        from ...transcript import normalize_transcript, write_srt

        manifest = _json(_input(inputs, SOURCE_MANIFEST).path)
        raw = _json(_input(inputs, TRANSCRIPT_RAW).path)
        normalized = normalize_transcript(
            raw, dict(context.options.asr), float(manifest.get("duration_seconds", 0.0) or 0.0),
        )
        output = staging_dir / TRANSCRIPT_NORMALIZED.relative_paths[0]
        srt = staging_dir / TRANSCRIPT_SRT.relative_paths[0]
        _write_json(output, normalized)
        write_srt(srt, normalized.get("segments", []))
        _emit(
            context, self.spec.step_id, "asr", "info",
            f"转写规范化完成：{len(normalized.get('segments', []))} 个片段",
            "transcript_normalized",
        )
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(TRANSCRIPT_NORMALIZED, output), ArtifactRef(TRANSCRIPT_SRT, srt)),
            diagnostics={
                "engine": normalized.get("engine"), "device": normalized.get("device"),
                "compute_type": normalized.get("compute_type"),
            },
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        transcript = _json(next(ref.path for ref in outcome.artifacts if ref.artifact_id == TRANSCRIPT_NORMALIZED))
        if not isinstance(transcript.get("segments"), list):
            raise ValueError("规范化转写 Artifact 缺少 segments")
        srt = next(ref.path for ref in outcome.artifacts if ref.artifact_id == TRANSCRIPT_SRT)
        if not srt.is_file():
            raise ValueError("规范化转写缺少 SRT Artifact")


@dataclass
class FramesCandidatesStep:
    spec = StepSpec(
        "frames.candidates", 1, dependencies=("source.probe",),
        inputs=(SOURCE_MANIFEST,), outputs=(FRAMES_CANDIDATES,),
        config_keys=("frames.sampling",), remote_cost=RemoteCost.LOCAL_HEAVY,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_fine_pipeline.py",), error_code_prefix="FRAMES_CANDIDATES",
        contract_version="frame-candidates-v1",
    )

    _SAMPLING_KEYS = ("sample_interval_seconds", "max_candidates", "max_width")

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        settings = dict(context.options.frames)
        return FingerprintMaterial({
            "upstream.source.manifest": _input(inputs, SOURCE_MANIFEST).digest,
            "frames.sampling": {key: settings.get(key) for key in self._SAMPLING_KEYS},
            "sampling.version": 1,
        })

    def execute(self, context, inputs, staging_dir: Path) -> StepOutcome:
        manifest = _json(_input(inputs, SOURCE_MANIFEST).path)
        settings = dict(context.options.frames)
        settings["duration_seconds"] = float(manifest.get("duration_seconds", 0.0) or 0.0)
        interval = max(0.001, float(settings.get("sample_interval_seconds", 10.0)))
        candidate_total = max(1, min(max(1, int(settings.get("max_candidates", 600))), int(
            max(1.0, settings["duration_seconds"]) / interval + 0.999
        )))
        _stage_progress(context, "frames", "正在提取候选画面", 55)
        _task_progress(context, ProgressEvent(
            "frames", "candidate", 0, candidate_total, False,
            task_id="frames.extract", cache_state="miss",
            bucket=f"interval={interval:g}|width={settings.get('max_width', 1280)}",
        ))
        _emit(context, self.spec.step_id, "frames", "info", "正在提取候选画面", "frame_candidates_started")
        started = time.monotonic()
        try:
            candidates = context.services.port("media").extract_frame_candidates(
                context.source.path,
                staging_dir / "images",
                settings,
                cancel_check=context.services.cancelled,
            )
        except BaseException as exc:
            if _cancelled_exception(exc):
                raise ExecutionCancelled(str(exc)) from exc
            raise
        _task_progress(context, ProgressEvent(
            "frames", "candidate", candidate_total, candidate_total, False,
            max(0.001, time.monotonic() - started) / candidate_total,
            task_id="frames.extract", cache_state="miss",
            bucket=f"interval={interval:g}|width={settings.get('max_width', 1280)}",
        ))
        _emit(
            context, self.spec.step_id, "frames", "info",
            f"候选画面完成：{len(candidates.get('candidates', []))} 张",
            "frame_candidates_completed",
        )
        index = staging_dir / FRAMES_CANDIDATES.relative_paths[0]
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(FRAMES_CANDIDATES, index),),
            diagnostics={"count": len(candidates.get("candidates", []))},
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        candidates = _json(outcome.artifacts[0].path)
        if not isinstance(candidates.get("candidates"), list):
            raise ValueError("候选帧 Artifact 缺少 candidates")


@dataclass
class FramesSelectStep:
    spec = StepSpec(
        "frames.select", 1,
        dependencies=("frames.candidates", "transcript.normalize", "source.probe"),
        inputs=(FRAMES_CANDIDATES, TRANSCRIPT_NORMALIZED, SOURCE_MANIFEST),
        outputs=(FRAMES_SELECTED,), config_keys=("frames.selection",), remote_cost=RemoteCost.NONE,
        owner="video_study.execution.steps.coarse",
        tests=("tests/test_fine_pipeline.py",), error_code_prefix="FRAMES_SELECT",
        contract_version="selected-frames-v1",
    )

    _SELECTION_KEYS = (
        "scene_change_threshold", "min_content_entropy", "max_keyframes",
        "content_start_padding_seconds", "min_keyframe_gap_seconds",
    )

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        settings = dict(context.options.frames)
        transcript = _json(_input(inputs, TRANSCRIPT_NORMALIZED).path)
        first_start = (
            float(transcript["segments"][0].get("start_seconds", 0.0))
            if transcript.get("segments") else 0.0
        )
        return FingerprintMaterial({
            "upstream.frames.candidates": _input(inputs, FRAMES_CANDIDATES).digest,
            "transcript.content_start": first_start,
            "frames.selection": {key: settings.get(key) for key in self._SELECTION_KEYS},
            "selection.version": 4,
        })

    def execute(self, context, inputs, staging_dir: Path) -> StepOutcome:
        from ...frames import select_sampled_frames

        manifest = _json(_input(inputs, SOURCE_MANIFEST).path)
        transcript = _json(_input(inputs, TRANSCRIPT_NORMALIZED).path)
        settings = dict(context.options.frames)
        if transcript.get("segments"):
            settings["content_start_seconds"] = (
                float(transcript["segments"][0]["start_seconds"])
                + float(settings.get("content_start_padding_seconds", 30.0))
            )
        _stage_progress(context, "frames", "正在筛选关键画面", 65)
        output_dir = staging_dir / "images"
        final_selected = context.workspace.artifact_paths(FRAMES_SELECTED)[1]
        frames = select_sampled_frames(
            _input(inputs, FRAMES_CANDIDATES).path,
            output_dir,
            float(manifest.get("duration_seconds", 0.0) or 0.0),
            settings,
            final_selected_dir=final_selected,
            cancel_check=context.services.cancelled,
        )
        _emit(
            context, self.spec.step_id, "frames", "info",
            f"关键画面完成：保留 {len(frames.get('frames', []))} 张真实视频帧",
            "frames_completed",
        )
        index = staging_dir / FRAMES_SELECTED.relative_paths[0]
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(FRAMES_SELECTED, index),),
            diagnostics={"count": len(frames.get("frames", []))},
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        frames = _json(outcome.artifacts[0].path)
        if not isinstance(frames.get("frames"), list):
            raise ValueError("关键帧 Artifact 缺少 frames")


@dataclass
class RenderBundleStep:
    output_artifact: ArtifactId

    def __post_init__(self) -> None:
        self.spec = StepSpec(
            "render.bundle", 2, dependencies=("document.assemble",),
            inputs=(DOCUMENT_V2,), outputs=(self.output_artifact,),
            config_keys=("render",), remote_cost=RemoteCost.LOCAL_HEAVY,
            owner="video_study.execution.steps.coarse",
            tests=("tests/test_coarse_pipeline.py",), error_code_prefix="RENDER",
            contract_version="render-bundle-v1",
        )

    def fingerprint(self, context, inputs) -> FingerprintMaterial:
        return _fingerprint(context, inputs, "render")

    def execute(self, context, inputs, staging_dir: Path) -> StepOutcome:
        document_ref = _input(inputs, DOCUMENT_V2)
        document = _json(document_ref.path)
        markdown = staging_dir / self.output_artifact.relative_paths[0]
        docx = staging_dir / self.output_artifact.relative_paths[1]
        pdf = staging_dir / self.output_artifact.relative_paths[2]
        adapter = context.services.port("document")
        _stage_progress(context, "render", "正在生成 Markdown、Word 和 PDF", 90)
        _emit(context, self.spec.step_id, "render", "info", "正在生成 Markdown、Word 和 PDF", "render_started")
        try:
            started = time.monotonic()
            adapter.render_markdown(document, markdown)
            _task_progress(context, ProgressEvent(
                "render", "markdown", 1, 1, False, max(0.001, time.monotonic() - started),
                task_id="render.markdown", cache_state="miss", bucket="document-v2",
            ))
            started = time.monotonic()
            adapter.render_word(document_ref.path, docx, cancel_check=context.services.cancelled)
            _task_progress(context, ProgressEvent(
                "render", "word", 1, 1, False, max(0.001, time.monotonic() - started),
                task_id="render.word", cache_state="miss", bucket="document-v2",
            ))
            started = time.monotonic()
            pdf_mode = adapter.render_pdf(
                document, docx, pdf, cancel_check=context.services.cancelled,
            )
            _task_progress(context, ProgressEvent(
                "render", "pdf", 1, 1, False, max(0.001, time.monotonic() - started),
                task_id="render.pdf", cache_state="miss", bucket="document-v2",
            ))
        except BaseException as exc:
            if _cancelled_exception(exc):
                raise ExecutionCancelled(str(exc)) from exc
            raise
        _emit(
            context, self.spec.step_id, "completed", "info",
            f"处理完成：{context.workspace.output_root / context.source.video_id}",
            "video_completed",
        )
        _stage_progress(context, "completed", "处理完成", 100)
        return StepOutcome(
            self.spec.step_id, context.run_id, StepStatus.SUCCEEDED,
            artifacts=(ArtifactRef(self.output_artifact, markdown),),
            diagnostics={"pdf_mode": pdf_mode},
        )

    def validate(self, _context, outcome: StepOutcome) -> None:
        for relative in self.output_artifact.relative_paths:
            path = outcome.artifacts[0].path.parent / Path(relative).name
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"渲染输出缺失：{path.name}")


def build_coarse_steps(output_artifact: ArtifactId):
    from .knowledge import build_knowledge_steps

    return (
        SourceProbeStep(),
        AudioExtractStep(),
        TranscriptDecodeStep(),
        TranscriptNormalizeStep(),
        FramesCandidatesStep(),
        FramesSelectStep(),
        *build_knowledge_steps(),
        RenderBundleStep(output_artifact),
    )
