from __future__ import annotations

import re
from pathlib import Path


KEY_PATTERN = re.compile(r"QWEN_API_KEY\s*=\s*[\"']?([^\s\"']+)", re.I)
URL_PATTERN = re.compile(r"QWEN_BASE_URL\s*=\s*[\"']?([^\s\"']+)", re.I)
def _decode(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def import_qwen_txt(source: Path, destination: Path, force: bool = False) -> dict:
    if destination.exists() and not force:
        raise FileExistsError(f"{destination} 已存在；为避免覆盖密钥，请使用 --force 明确覆盖。")
    text = _decode(source)
    key_match = KEY_PATTERN.search(text)
    url_match = URL_PATTERN.search(text)
    if not key_match or not url_match:
        raise ValueError("未在源文件中找到 QWEN_API_KEY 或 QWEN_BASE_URL")
    lines = [
        "# Generated from QwenAPI.txt. This file is git-ignored.",
        "# Model chains and budgets are stored in api.yaml.",
        f"QWEN_API_KEY={key_match.group(1)}",
        f"QWEN_BASE_URL={url_match.group(1)}",
        "CLOUD_LLM_ENABLED=false",
        "CLOUD_ASR_ENABLED=false",
        "",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_text("\n".join(lines), encoding="utf-8")
    temp.replace(destination)
    return {
        "destination": str(destination),
        "base_url": url_match.group(1),
        "cloud_enabled": False,
    }
