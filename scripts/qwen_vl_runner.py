from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


def _model_complete(model: Path) -> bool:
    if not (model / "config.json").is_file():
        return False
    return any(model.glob("*.safetensors")) or any(model.glob("pytorch_model*.bin"))


def _preflight(runtime: Path, model: Path) -> dict[str, Any]:
    sys.path.insert(0, str(runtime))
    import torch
    import transformers

    return {
        "ok": _model_complete(model),
        "model_complete": _model_complete(model),
        "cuda_available": bool(torch.cuda.is_available()),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "torch_version": str(torch.__version__),
        "transformers_version": str(transformers.__version__),
    }


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出不含 JSON 对象")
    candidate = cleaned[start:end + 1]
    parsed = None
    for value in (
        candidate,
        re.sub(r",\s*([}\]])", r"\1", candidate),
    ):
        try:
            parsed = json.loads(value)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        # 兼容模型偶发的 Python 字面量式单引号；literal_eval 不执行代码。
        try:
            parsed = ast.literal_eval(candidate)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("模型输出不是合法 JSON 对象") from exc
    if not isinstance(parsed, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return parsed


def _extract_string_field(raw: str, field: str) -> str:
    match = re.search(
        rf'''["']{re.escape(field)}["']\s*:\s*(["'])(.*?)\1''',
        raw,
        flags=re.DOTALL,
    )
    return match.group(2).strip() if match else ""


def _extract_string_array(raw: str, field: str, limit: int = 4) -> list[str]:
    start = re.search(rf'''["']{re.escape(field)}["']\s*:\s*\[''', raw)
    if not start:
        return []
    tail = raw[start.end():]
    end = tail.find("]")
    section = tail if end < 0 else tail[:end]
    rows: list[str] = []
    for match in re.finditer(r'''(["'])(.*?)(?<!\\)\1''', section, flags=re.DOTALL):
        value = match.group(2).strip()
        if value and value not in rows:
            rows.append(value)
        if len(rows) >= limit:
            break
    return rows


def _repair_structured_result(
    raw: str,
    action: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """从截断输出提取已完整闭合的字段；不补写图片事实或成功条件。"""
    confidence_match = re.search(r'''["']confidence["']\s*:\s*([0-9]+(?:\.[0-9]+)?)''', raw)
    confidence = float(confidence_match.group(1)) if confidence_match else 0.0
    result: dict[str, Any] = {
        "visible_evidence": _extract_string_array(raw, "visible_evidence", 2),
        "criteria_met": _extract_string_array(raw, "criteria_met", 4),
        "criteria_missing": _extract_string_array(raw, "criteria_missing", 4),
        "visual_answer": _extract_string_field(raw, "visual_answer"),
        "confidence": max(0.0, min(1.0, confidence)),
    }
    if action == "compare":
        decision = _extract_string_field(raw, "decision")
        result.update({
            # 只有模型明确写出 select 才进入后续像素门槛；其余一律安全拒绝。
            "decision": "select" if decision == "select" else "no_match",
            "selected_candidate_id": _extract_string_field(raw, "selected_candidate_id"),
            "needs_detail_pass": False,
            "reject_reason": _extract_string_field(raw, "reject_reason") or "本地 VLM 结构化输出不完整",
        })
        result = _map_candidate_alias(result, candidates)
    return result


def _prompt(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], int]:
    action = str(payload.get("action", "compare"))
    contract = payload.get("contract", {})
    success_criteria = json.dumps(contract.get("success_criteria", []), ensure_ascii=False)
    if action == "detail":
        candidate = dict(payload.get("candidate", {}))
        prompt = f"""你只核对这一张课程图片，不补充外部知识。
视觉职责：{contract.get('role', 'explain')}
成功条件：{success_criteria}
只输出 JSON 对象，字段必须为：
{{"visible_evidence":[],"criteria_met":[],"criteria_missing":[],"visual_answer":"","confidence":0.0}}
visible_evidence 写图片中实际可见的事实；criteria_met 只能从成功条件数组中原样复制已满足的条目；criteria_missing 只能从成功条件数组中原样复制未满足的条目。看不清就保留 criteria_missing，不得猜测。"""
        prompt += "\nvisible_evidence 最多 2 条，每条只写一个不重复的短事实；整个 JSON 控制在 220 个汉字以内。"
        return prompt, [candidate], 1280

    question = payload.get("question", {})
    candidates = [dict(item) for item in payload.get("candidates", [])][:4]
    candidate_ids = [chr(ord("A") + index) for index in range(len(candidates))]
    prompt = f"""你负责比较少量课程候选帧，可以拒绝全部候选。只依据图片像素和给出的 OCR，不使用时间接近作为最终证据。
视觉问题：{question.get('question', '')}
视觉职责：{contract.get('role', 'explain')}
成功条件：{success_criteria}
候选按图片顺序为：{json.dumps(candidate_ids, ensure_ascii=False)}
候选 OCR：{json.dumps([{item.get('candidate_id'): item.get('ocr_text', '')} for item in candidates], ensure_ascii=False)}
只输出 JSON 对象，字段必须为：
{{"decision":"select","selected_candidate_id":"","visible_evidence":[],"criteria_met":[],"criteria_missing":[],"visual_answer":"","confidence":0.0,"needs_detail_pass":false,"reject_reason":""}}
selected_candidate_id 只能取候选字母 A/B/C/D 中本次实际显示的一个，或空字符串；visible_evidence 写图片中实际可见的事实；criteria_met 只能从成功条件数组中原样复制已满足的条目；criteria_missing 只能从成功条件数组中原样复制未满足的条目。
只有 visible_evidence 非空且全部成功条件满足时才能 select；看不清、候选不对应或只因时间接近时必须 no_match。"""
    prompt += "\nvisible_evidence 最多 2 条，每条只写一个不重复的短事实；整个 JSON 控制在 220 个汉字以内。"
    return prompt, candidates, 512


def _map_candidate_alias(result: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """把模型使用的 A/B/C/D 映射回本次允许集合中的真实候选 ID。"""
    mapped = dict(result)
    raw = str(mapped.get("selected_candidate_id", "") or "").strip()
    normalized = re.sub(r"^(?:候选|图片|candidate[_\s-]*)", "", raw, flags=re.IGNORECASE).strip().upper()
    aliases = {
        chr(ord("A") + index): str(candidate.get("candidate_id", ""))
        for index, candidate in enumerate(candidates[:4])
    }
    if normalized in aliases:
        mapped["selected_candidate_id"] = aliases[normalized]
    return mapped


def _load_model(runtime: Path, model_path: Path):
    sys.path.insert(0, str(runtime))
    import torch
    from transformers import AutoProcessor

    try:
        from transformers import Qwen3VLForConditionalGeneration as ModelClass
    except ImportError:
        from transformers import AutoModelForImageTextToText as ModelClass

    if not torch.cuda.is_available():
        raise RuntimeError("Qwen3-VL 本地 runner 要求 CUDA；当前未检测到 GPU")
    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = ModelClass.from_pretrained(
        str(model_path),
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
        device_map="cuda:0",
        local_files_only=True,
    ).eval()
    return torch, processor, model


def _generate(
    runtime: Path,
    model_path: Path,
    payload: dict[str, Any],
    loaded: tuple[Any, Any, Any] | None = None,
) -> dict[str, Any]:
    from PIL import Image

    action = str(payload.get("action", "compare"))
    torch, processor, model = loaded or _load_model(runtime, model_path)
    prompt, candidate_rows, visual_tokens = _prompt(payload)
    images = []
    for candidate in candidate_rows:
        path = Path(str(candidate.get("path", ""))).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"候选图片不存在：{path}")
        with Image.open(path) as image:
            images.append(image.convert("RGB").copy())

    image_processor = getattr(processor, "image_processor", None)
    if image_processor is not None:
        image_processor.min_pixels = 256 * 28 * 28
        image_processor.max_pixels = visual_tokens * 28 * 28

    messages = [{
        "role": "user",
        "content": [
            *[{"type": "image"} for _ in images],
            {"type": "text", "text": prompt},
        ],
    }]
    def generate(current_messages: list[dict[str, Any]], max_new_tokens: int = 256) -> str:
        text = processor.apply_chat_template(current_messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[text], images=images, padding=True, return_tensors="pt")
        inputs = {key: value.to(model.device) if hasattr(value, "to") else value for key, value in inputs.items()}
        with torch.inference_mode():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                # 四图输入下，病理性重复不能无限占满外层 180 秒预算。
                # Transformers 会在当前解码步结束后停止，仍可返回已生成内容供 JSON 修复。
                max_time=55.0,
                do_sample=False,
                repetition_penalty=1.08,
                no_repeat_ngram_size=6,
            )
        input_length = inputs["input_ids"].shape[1]
        generated = output_ids[:, input_length:]
        return processor.batch_decode(generated, skip_special_tokens=True)[0]

    decoded = generate(messages)
    try:
        return _map_candidate_alias(_json_object(decoded), candidate_rows) if action == "compare" else _json_object(decoded)
    except ValueError:
        debug_path = os.getenv("VIDEO_STUDY_VLM_DEBUG_RAW", "").strip()
        if debug_path:
            Path(debug_path).write_text(
                json.dumps({"first": decoded}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return _repair_structured_result(decoded, action, candidate_rows)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _run_session(runtime: Path, model_path: Path, jobs_dir: Path) -> None:
    """Load once, then process atomic job files sequentially (micro_batch_size=1)."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    loaded = _load_model(runtime, model_path)
    _atomic_json(jobs_dir / "ready.json", {
        "ready": True,
        "pid": os.getpid(),
        "model_load_count": 1,
        "micro_batch_size": 1,
    })
    processed: set[str] = set()
    while True:
        pending = [path for path in sorted(jobs_dir.glob("request-*.json")) if path.stem not in processed]
        for request_path in pending:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            job_id = str(request.get("job_id", request_path.stem.removeprefix("request-")))
            result_path = jobs_dir / f"result-{job_id}.json"
            try:
                result = _generate(runtime, model_path, dict(request.get("payload", {})), loaded=loaded)
                response = {
                    "job_id": job_id,
                    "device": "cuda",
                    "compute_type": "bfloat16",
                    "backend": "qwen3-vl-2b-local",
                    "model_load_count": 1,
                    "result": result,
                }
            except BaseException as exc:
                response = {
                    "job_id": job_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "model_load_count": 1,
                }
            _atomic_json(result_path, response)
            processed.add(request_path.stem)
        if (jobs_dir / "done.json").is_file() and not [
            path for path in jobs_dir.glob("request-*.json") if path.stem not in processed
        ]:
            _atomic_json(jobs_dir / "stopped.json", {
                "done": True,
                "model_load_count": 1,
                "processed_jobs": len(processed),
            })
            return
        time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--session")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()

    runtime = Path(args.runtime).resolve()
    model = Path(args.model).resolve()
    if args.preflight:
        print(json.dumps(_preflight(runtime, model), ensure_ascii=False))
        return
    if args.session:
        if not _model_complete(model):
            raise FileNotFoundError(f"本地视觉模型权重不完整：{model}")
        _run_session(runtime, model, Path(args.session).resolve())
        os._exit(0)
    if not args.input or not args.output:
        parser.error("非预检模式必须提供 --input 和 --output")
    if not _model_complete(model):
        raise FileNotFoundError(f"本地视觉模型权重不完整：{model}")
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = _generate(runtime, model, payload)
    Path(args.output).write_text(json.dumps({
        "device": "cuda",
        "compute_type": "bfloat16",
        "backend": "qwen3-vl-2b-local",
        "result": result,
    }, ensure_ascii=False), encoding="utf-8")
    # 部分 Windows + CUDA 驱动组合会在解释器析构阶段长时间阻塞；结果文件已经
    # 完整关闭落盘，此处直接退出，避免把成功推理误判为外层 180 秒超时。
    os._exit(0)


if __name__ == "__main__":
    main()
