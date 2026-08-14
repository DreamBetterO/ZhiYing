from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "yue": "Cantonese",
    "ja": "Japanese",
    "ko": "Korean",
}


def _load_official_backend(model_path: str):
    import torch
    from qwen_asr import Qwen3ASRModel

    use_cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if use_cuda else torch.float32
    model = Qwen3ASRModel.from_pretrained(
        model_path,
        dtype=dtype,
        device_map="cuda:0" if use_cuda else "cpu",
        max_inference_batch_size=1,
        max_new_tokens=768,
    )

    def transcribe(audio, sample_rate: int, language: str) -> tuple[str, str | None]:
        results = model.transcribe(
            audio=(audio, sample_rate),
            language=_LANGUAGE_NAMES.get(language),
        )
        if not results:
            return "", None
        return str(results[0].text).strip(), getattr(results[0], "language", None)

    return transcribe, use_cuda, str(dtype).replace("torch.", ""), "qwen-asr"


def _load_legacy_backend(model_path: str):
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path, dtype=dtype, local_files_only=True,
    ).to("cuda" if use_cuda else "cpu").eval()

    def transcribe(audio, _sample_rate: int, language: str) -> tuple[str, str | None]:
        inputs = processor.apply_transcription_request(audio=audio, language=language)
        inputs = inputs.to(model.device, model.dtype)
        with torch.inference_mode():
            output_ids = model.generate(**inputs, max_new_tokens=768, do_sample=False)
        generated = output_ids[:, inputs["input_ids"].shape[1]:]
        return processor.decode(generated, return_format="transcription_only")[0].strip(), None

    return transcribe, use_cuda, str(dtype).replace("torch.", ""), "transformers-legacy"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--chunk-seconds", type=int, default=60)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.runtime).resolve()))
    import soundfile as sf

    model_path = str(Path(args.model).resolve())
    try:
        transcribe_block, use_cuda, compute_type, backend = _load_official_backend(model_path)
    except (ImportError, ModuleNotFoundError):
        transcribe_block, use_cuda, compute_type, backend = _load_legacy_backend(model_path)

    rows: list[dict] = []
    detected_language = None
    with sf.SoundFile(args.audio) as handle:
        sample_rate = int(handle.samplerate)
        blocksize = max(sample_rate, int(args.chunk_seconds) * sample_rate)
        total = max(1, (len(handle) + blocksize - 1) // blocksize)
        for index, audio in enumerate(handle.blocks(blocksize=blocksize, dtype="float32", always_2d=False), start=1):
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            start = (index - 1) * blocksize / sample_rate
            end = start + len(audio) / sample_rate
            text, block_language = transcribe_block(audio, sample_rate, args.language)
            detected_language = detected_language or block_language
            if text:
                rows.append({"start_seconds": start, "end_seconds": end, "text": text})
            print(f"[qwen-asr] {index}/{total}", file=sys.stderr, flush=True)

    Path(args.output).write_text(json.dumps({
        "device": "cuda" if use_cuda else "cpu",
        "compute_type": compute_type,
        "backend": backend,
        "language": detected_language or args.language,
        "segments": rows,
    }, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
