"""Qwen3-ASR 隔离推理 runner。

通过 stdout 输出机器可解析的 JSON Lines 事件，父进程据此更新真实进度。
stderr 只承载诊断文本。
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "yue": "Cantonese",
    "ja": "Japanese",
    "ko": "Korean",
}

_MIN_IMPROVEMENT_RATIO = 1.15
_OFFICIAL_REQUIRED_MODULES = {
    "qwen_asr": "qwen-asr",
    "nagisa": "nagisa",
    "soynlp": "soynlp",
    "qwen_omni_utils": "qwen-omni-utils",
    "accelerate": "accelerate",
    "librosa": "librosa",
    "soundfile": "soundfile",
    "torch": "torch",
    "transformers": "transformers",
}


def _emit(event: dict) -> None:
    """输出一行 JSON 事件到 stdout。"""
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _emit_stderr(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _missing_modules(modules: dict[str, str]) -> list[str]:
    return [
        distribution
        for module, distribution in modules.items()
        if importlib.util.find_spec(module) is None
    ]


def _model_requires_official_backend(model_path: str) -> bool:
    config_path = Path(model_path) / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    architectures = [str(item) for item in config.get("architectures", [])]
    model_type = str(config.get("model_type", ""))
    return model_type == "qwen3_asr" or any("Qwen3ASR" in item for item in architectures)


def _patch_qwen_tokenizer_audio_attrs() -> None:
    from transformers import Qwen2Tokenizer, Qwen2TokenizerFast

    def make_property(name: str):
        return property(lambda self: self.init_kwargs.get(name))

    for cls in (Qwen2Tokenizer, Qwen2TokenizerFast):
        for name in ("audio_token", "audio_bos_token", "audio_eos_token"):
            if not hasattr(cls, name):
                setattr(cls, name, make_property(name))


def _ascii_nagisa_overlay(runtime: Path) -> Path | None:
    if os.name != "nt" or str(runtime).isascii():
        return None
    source = runtime / "nagisa"
    if not (source / "data" / "nagisa_v001.model").is_file():
        return None
    cache_root = Path(tempfile.gettempdir()) / "zhiying_qwen_runtime" / "nagisa_ascii"
    if not str(cache_root).isascii():
        raise RuntimeError(f"Qwen3-ASR 的 nagisa 运行缓存路径不是 ASCII：{cache_root}")
    target = cache_root / "nagisa"
    marker = cache_root / "nagisa_runtime.json"
    source_model = source / "data" / "nagisa_v001.model"
    expected = {
        "source": str(source_model),
        "size": source_model.stat().st_size,
        "mtime_ns": source_model.stat().st_mtime_ns,
    }
    current = {}
    try:
        current = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        current = {}
    if current != expected or not (target / "data" / "nagisa_v001.model").is_file():
        cache_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        for extension in runtime.glob("nagisa_utils*.pyd"):
            shutil.copy2(extension, cache_root / extension.name)
        marker.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache_root


def probe_environment() -> dict:
    """检测 torch 来源、CUDA、GPU、显存、compute capability、BF16、SDPA。"""
    import torch
    env = {
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": "cpu",
        "gpu_name": "",
        "gpu_uuid": "",
        "compute_capability": "",
        "total_vram_mb": 0,
        "free_vram_mb": 0,
        "bf16_supported": False,
        "sdpa_available": hasattr(torch.nn.functional, "scaled_dot_product_attention"),
    }
    if not torch.cuda.is_available():
        return env
    try:
        props = torch.cuda.get_device_properties(0)
        free, total = torch.cuda.mem_get_info()
        env.update(
            device="cuda",
            gpu_name=props.name,
            gpu_uuid=str(props.uuid) if hasattr(props, "uuid") else "",
            compute_capability=f"{props.major}.{props.minor}",
            total_vram_mb=total // (1024 * 1024),
            free_vram_mb=free // (1024 * 1024),
            bf16_supported=torch.cuda.is_bf16_supported(),
        )
    except Exception as exc:
        env["cuda_error"] = str(exc)[:200]
    return env


def _verify_cuda_operator() -> bool:
    """执行极小 CUDA 算子以排除'能枚举 GPU 但 DLL/kernel 不可用'。"""
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        x = torch.zeros(1, device="cuda")
        y = (x + 1).sum().item()
        del x
        torch.cuda.empty_cache()
        return y == 1.0
    except Exception:
        return False


def _select_dtype(env: dict) -> str:
    if env.get("bf16_supported"):
        return "bfloat16"
    try:
        import torch
        if torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] >= 70:
            return "float16"
    except Exception:
        pass
    return "float32"


def _select_initial_batch(env: dict) -> int:
    if env.get("device") != "cuda":
        return 1
    free_vram = int(env.get("free_vram_mb") or 0)
    if free_vram >= 10_000:
        return 8
    if free_vram >= 6_000:
        return 4
    if free_vram >= 3_500:
        return 2
    return 1


def _speed_warning_required(samples: list[float], threshold: float, already_emitted: bool) -> bool:
    """速度低于建议阈值只触发告警，不判定已完成的 ASR 结果失败。"""
    if already_emitted or len(samples) < 2:
        return False
    return (sum(samples) / len(samples)) > float(threshold)


def _load_official_backend(model_path: str, device: str, dtype_str: str, batch_size: int):
    import torch
    from qwen_asr.core.transformers_backend.configuration_qwen3_asr import Qwen3ASRConfig
    from qwen_asr import Qwen3ASRModel

    _patch_qwen_tokenizer_audio_attrs()
    raw_config = json.loads((Path(model_path) / "config.json").read_text(encoding="utf-8"))
    if "thinker_config" in raw_config:
        thinker_config = dict(raw_config["thinker_config"])
    else:
        text_config = dict(raw_config.get("text_config") or {})
        rope_parameters = text_config.get("rope_parameters") or {}
        if text_config.get("rope_scaling") is None:
            text_config["rope_scaling"] = {
                "rope_type": rope_parameters.get("rope_type", "default"),
                "mrope_section": [24, 20, 20],
            }
        if "rope_theta" not in text_config and rope_parameters.get("rope_theta") is not None:
            text_config["rope_theta"] = rope_parameters["rope_theta"]
        thinker_config = {
            "audio_config": raw_config.get("audio_config") or {},
            "text_config": text_config,
            "audio_token_id": raw_config.get("audio_token_id", 151646),
            "initializer_range": raw_config.get("initializer_range", 0.02),
        }
    passthrough = {
        key: value for key, value in raw_config.items()
        if key not in {"architectures", "audio_config", "text_config", "thinker_config", "model_type"}
    }
    config = Qwen3ASRConfig(thinker_config=thinker_config, **passthrough)
    dtype = getattr(torch, dtype_str)
    model = Qwen3ASRModel.from_pretrained(
        model_path,
        config=config,
        local_files_only=True,
        dtype=dtype,
        low_cpu_mem_usage=True,
        max_inference_batch_size=batch_size,
        max_new_tokens=768,
    )
    if device == "cuda":
        model.model.to("cuda")
    else:
        model.model.to("cpu")
    return model, "qwen-asr", batch_size


def _load_legacy_backend(model_path: str, device: str, dtype_str: str):
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    dtype = getattr(torch, dtype_str)
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path, dtype=dtype, local_files_only=True,
    ).to(device).eval()

    def transcribe_blocks(audio_blocks, sample_rate: int, language: str):
        results = []
        for audio in audio_blocks:
            inputs = processor.apply_transcription_request(audio=audio, language=language)
            inputs = inputs.to(model.device, model.dtype)
            with torch.inference_mode():
                output_ids = model.generate(**inputs, max_new_tokens=768, do_sample=False)
            generated = output_ids[:, inputs["input_ids"].shape[1]:]
            text = processor.decode(generated, return_format="transcription_only")[0].strip()
            results.append(text)
        return results

    return {"transcribe_blocks": transcribe_blocks}, "transformers-legacy", 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--chunk-seconds", type=int, default=60)
    parser.add_argument("--max-rtf", type=float, default=0.75)
    args = parser.parse_args()

    runtime_path = Path(args.runtime).resolve()
    nagisa_overlay = _ascii_nagisa_overlay(runtime_path)
    sys.path.insert(0, str(runtime_path))
    if nagisa_overlay is not None:
        sys.path.insert(0, str(nagisa_overlay))
    model_path = str(Path(args.model).resolve())
    official_required = _model_requires_official_backend(model_path)
    if official_required:
        missing = _missing_modules(_OFFICIAL_REQUIRED_MODULES)
        if missing:
            detail = "缺少 Qwen3-ASR 官方运行依赖：" + ", ".join(missing)
            _emit({
                "type": "load_failed",
                "reason": "missing_dependency",
                "missing": missing,
                "error_type": "MissingDependency",
                "error": detail,
            })
            _emit_stderr(detail)
            return 1

    try:
        import soundfile as sf
    except Exception as exc:
        _emit({
            "type": "load_failed",
            "reason": "missing_dependency",
            "missing": ["soundfile"],
            "error_type": type(exc).__name__,
            "error": str(exc)[-300:],
        })
        _emit_stderr(f"缺少音频读取依赖：{type(exc).__name__}: {exc}")
        return 1

    try:
        env = probe_environment()
    except Exception as exc:
        _emit({
            "type": "load_failed",
            "reason": "environment_probe_failed",
            "error_type": type(exc).__name__,
            "error": str(exc)[-300:],
        })
        _emit_stderr(f"运行环境探测失败：{type(exc).__name__}: {exc}")
        return 1
    if env["device"] == "cuda" and not _verify_cuda_operator():
        env["device"] = "cpu"
        env["cuda_operator_failed"] = True

    _emit({"type": "environment", **env})

    dtype_str = _select_dtype(env)
    device = env["device"]
    initial_batch = _select_initial_batch(env)

    _emit({"type": "load_started", "device": device, "dtype": dtype_str, "batch_size": initial_batch})

    load_started = time.monotonic()
    backend = "unknown"
    try:
        if official_required:
            model_obj, backend, initial_batch = _load_official_backend(model_path, device, dtype_str, initial_batch)
        else:
            model_obj, backend, initial_batch = _load_legacy_backend(model_path, device, dtype_str)
    except Exception as exc:
        _emit({"type": "load_failed", "error_type": type(exc).__name__,
               "error": str(exc)[-300:]})
        _emit_stderr(f"模型加载失败：{type(exc).__name__}: {exc}")
        return 1

    load_seconds = time.monotonic() - load_started
    _emit({"type": "load_completed", "device": device, "dtype": dtype_str,
           "backend": backend, "load_seconds": round(load_seconds, 3)})

    rows: list[dict] = []
    with sf.SoundFile(args.audio) as handle:
        sample_rate = int(handle.samplerate)
        blocksize = max(sample_rate, int(args.chunk_seconds) * sample_rate)
        total_chunks = max(1, (len(handle) + blocksize - 1) // blocksize)

        all_blocks = []
        block_starts = []
        cursor = 0
        for index, audio in enumerate(handle.blocks(blocksize=blocksize, dtype="float32", always_2d=False), start=1):
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            block_starts.append(cursor / sample_rate)
            cursor += len(audio)
            all_blocks.append(audio)

        batch_size = initial_batch
        chunk_index = 0
        total_audio_seconds = sum(len(b) for b in all_blocks) / sample_rate
        warm_rtf_samples: list[float] = []
        speed_warning_emitted = False

        while chunk_index < total_chunks:
            remaining = total_chunks - chunk_index
            current_batch = min(batch_size, remaining)
            batch_blocks = all_blocks[chunk_index:chunk_index + current_batch]
            batch_audio_seconds = sum(len(b) for b in batch_blocks) / sample_rate

            inference_started = time.monotonic()
            try:
                if backend == "qwen-asr":
                    results = model_obj.transcribe(
                        audio=[(b, sample_rate) for b in batch_blocks],
                        language=_LANGUAGE_NAMES.get(args.language),
                    )
                    texts = [str(r.text).strip() if r else "" for r in (results or [])]
                else:
                    texts = model_obj["transcribe_blocks"](batch_blocks, sample_rate, args.language)
                if len(texts) < len(batch_blocks):
                    texts.extend([""] * (len(batch_blocks) - len(texts)))

                inference_seconds = time.monotonic() - inference_started
                rtf = inference_seconds / max(0.1, batch_audio_seconds)

                for offset, text in enumerate(texts):
                    ci = chunk_index + offset + 1
                    start = block_starts[chunk_index + offset]
                    end = start + len(batch_blocks[offset]) / sample_rate
                    if text:
                        rows.append({"start_seconds": start, "end_seconds": end, "text": text})
                    _emit({
                        "type": "chunk_completed",
                        "chunk_index": ci,
                        "chunk_total": total_chunks,
                        "audio_seconds": round(end, 3),
                        "total_audio_seconds": round(total_audio_seconds, 3),
                        "inference_seconds": round(inference_seconds, 3),
                        "rtf": round(rtf, 4),
                        "batch_size": current_batch,
                    })

                warm_rtf_samples.append(rtf)
                if _speed_warning_required(warm_rtf_samples, float(args.max_rtf), speed_warning_emitted):
                    avg_rtf = sum(warm_rtf_samples) / len(warm_rtf_samples)
                    speed_warning_emitted = True
                    _emit({"type": "speed_circuit_breaker",
                           "avg_rtf": round(avg_rtf, 4),
                           "threshold": float(args.max_rtf)})

                if (
                    batch_size < 8
                    and chunk_index + current_batch < total_chunks
                    and len(warm_rtf_samples) >= 2
                ):
                    if current_batch > 1 and len(warm_rtf_samples) >= 2:
                        prev_rtf = warm_rtf_samples[-2]
                        if rtf < prev_rtf / _MIN_IMPROVEMENT_RATIO:
                            pass
                    try:
                        import torch
                        if device == "cuda":
                            free_vram = torch.cuda.mem_get_info()[0] // (1024 * 1024)
                            if free_vram < 1024:
                                pass
                            else:
                                batch_size = min(batch_size * 2, 8)
                    except Exception:
                        pass

                chunk_index += current_batch

            except RuntimeError as exc:
                if "out of memory" in str(exc).lower() and batch_size > 1:
                    _emit({"type": "oom_fallback", "batch_size": batch_size,
                           "new_batch_size": batch_size // 2})
                    batch_size = max(1, batch_size // 2)
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    gc.collect()
                    continue
                else:
                    _emit({"type": "inference_error",
                           "error_type": type(exc).__name__, "error": str(exc)[-300:]})
                    raise

            except Exception as exc:
                _emit({"type": "inference_error",
                       "error_type": type(exc).__name__, "error": str(exc)[-300:]})
                raise

    try:
        if device == "cuda":
            import torch
            del model_obj
            torch.cuda.empty_cache()
    except Exception:
        pass
    gc.collect()

    _emit({"type": "final", "device": device, "backend": backend,
           "dtype": dtype_str, "segment_count": len(rows)})

    Path(args.output).write_text(json.dumps({
        "device": device,
        "compute_type": dtype_str,
        "backend": backend,
        "language": args.language,
        "segments": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
