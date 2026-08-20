from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import set_key

from ..config import AppConfig

VISUAL_TEACHING_LEVELS = {"自动": "auto", "精简": "minimal", "均衡": "balanced", "增强": "enhanced"}
VISUAL_TEACHING_LABELS = {value: label for label, value in VISUAL_TEACHING_LEVELS.items()}


@dataclass(frozen=True)
class DesktopSettingsInput:
    base_url: str
    llm_models: str
    speech_models: str
    content_level: str = "推荐"
    visual_level: str = "auto"
    remember_key: bool = False
    api_key: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class ValidatedDesktopSettings:
    base_url: str
    llm_models: tuple[str, ...]
    speech_models: tuple[str, ...]
    content_level: str
    visual_level: str


def model_names(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[\s,，、]+", str(value or ""))
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def validate_desktop_settings(base_url: str, llm_value: str, speech_value: str) -> tuple[str, list[str], list[str]]:
    base_url = base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("API URL 必须是有效的 http(s) 地址，且不能包含用户名或密码")
    llm_models = model_names(llm_value)
    if not llm_models or len(llm_models) > 5 or any(len(name) > 160 for name in llm_models):
        raise ValueError("大模型链需要包含 1–5 个有效模型名称")
    speech_models = validate_speech_models(speech_value)

    return base_url, llm_models, speech_models


def validate_speech_models(speech_value: str) -> list[str]:
    """接收首选引擎名称，返回首选 + 后备的完整引擎链。"""
    primary = str(speech_value or "").strip()
    if primary not in {"faster-whisper", "qwen3-asr-0.6b"}:
        primary = "faster-whisper"
    fallback = "qwen3-asr-0.6b" if primary == "faster-whisper" else "faster-whisper"
    return [primary, fallback]


def validate_input(value: DesktopSettingsInput) -> ValidatedDesktopSettings:
    base_url, llm, speech = validate_desktop_settings(value.base_url, value.llm_models, value.speech_models)
    if value.content_level not in {"精简", "推荐", "丰富"}:
        raise ValueError("内容保留量必须是精简、推荐或丰富")
    visual = VISUAL_TEACHING_LEVELS.get(value.visual_level, value.visual_level)
    if visual not in set(VISUAL_TEACHING_LEVELS.values()):
        raise ValueError("图文教学必须是自动、精简、均衡或增强")
    return ValidatedDesktopSettings(base_url, tuple(llm), tuple(speech), value.content_level, visual)


def qwen_asr_ready(config: AppConfig) -> bool:
    settings = config.raw.get("asr", {})
    def resolve(name: str) -> Path:
        path = Path(os.path.expandvars(str(settings.get(name, ""))))
        return path if path.is_absolute() else config.root / path
    model = resolve("qwen_model_dir")
    return resolve("qwen_runtime_python").is_file() and resolve("qwen_runtime_dir").is_dir() and (model / "config.json").is_file() and (model / "model.safetensors").is_file()


def config_with_content_level(config: AppConfig, level: str) -> AppConfig:
    if level not in {"精简", "推荐", "丰富"}:
        raise ValueError("内容保留量必须是精简、推荐或丰富")
    raw = deepcopy(config.raw)
    raw.setdefault("render", {})["content_level"] = level
    budget = raw.setdefault("qwen", {}).setdefault("budget", {})
    raw["qwen"]["content_level"] = level
    raw["qwen"]["timeout_seconds"] = 120
    if level == "精简":
        raw["render"].update(offline_section_seconds=480, offline_points_per_section=1)
        budget["max_output_tokens"] = min(int(budget.get("max_output_tokens", 5000)), 3500)
        raw["qwen"]["timeout_seconds"] = 90
    elif level == "丰富":
        raw["render"].update(offline_section_seconds=180, offline_points_per_section=4)
        budget["max_output_tokens"] = int(budget.get("rich_max_output_tokens", 6000))
        raw["qwen"]["timeout_seconds"] = 240
    return AppConfig(config.root, raw)


def config_with_visual_teaching_level(config: AppConfig, level: str) -> AppConfig:
    normalized = VISUAL_TEACHING_LEVELS.get(level, level)
    if normalized not in set(VISUAL_TEACHING_LEVELS.values()):
        raise ValueError("图文教学必须是自动、精简、均衡或增强")
    raw = deepcopy(config.raw)
    raw.setdefault("visual_teaching", {})["level"] = normalized
    raw.setdefault("desktop", {})["visual_teaching_level"] = normalized
    return AppConfig(config.root, raw)


def save_desktop_settings(config: AppConfig, base_url: str, llm_models: list[str], speech_models: list[str], content_level: str = "推荐", visual_teaching_level: str = "auto") -> None:
    validated = validate_input(DesktopSettingsInput(base_url, ",".join(llm_models), ",".join(speech_models), content_level, visual_teaching_level))
    api_path = (config.root / config.raw.get("api_config", "api.yaml")).resolve()
    config_path = config.root / "config.yaml"
    api_data = yaml.safe_load(api_path.read_text(encoding="utf-8")) or {}
    main_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(api_data.get("qwen"), dict) or not isinstance(main_data.get("asr"), dict):
        raise ValueError("配置文件缺少 qwen 或 asr 映射，已停止保存")
    api_data["qwen"]["default_base_url"] = validated.base_url
    api_data["qwen"]["default_models"] = list(validated.llm_models)
    main_data["asr"]["engine"] = validated.speech_models[0]
    main_data.setdefault("desktop", {}).update(speech_models=list(validated.speech_models), content_level=validated.content_level, visual_teaching_level=validated.visual_level)
    main_data.setdefault("visual_teaching", {})["level"] = validated.visual_level
    prepared = []
    for target, data in ((api_path, api_data), (config_path, main_data)):
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        yaml.safe_load(temporary.read_text(encoding="utf-8")); prepared.append((temporary, target))
    for temporary, target in prepared:
        temporary.replace(target)
    config.raw.setdefault("qwen", {})["default_base_url"] = validated.base_url
    config.raw["qwen"]["default_models"] = list(validated.llm_models)
    config.raw.setdefault("asr", {})["engine"] = validated.speech_models[0]
    config.raw.setdefault("desktop", {}).update(
        speech_models=list(validated.speech_models),
        content_level=validated.content_level,
        visual_teaching_level=validated.visual_level,
    )
    config.raw.setdefault("visual_teaching", {})["level"] = validated.visual_level


def save_api_credentials(config: AppConfig, api_key: str, base_url: str) -> None:
    if not api_key.strip():
        raise ValueError("勾选保存 API Key 后，API Key 不能为空")
    qwen = config.raw["qwen"]
    env_path = config.root / ".env"
    set_key(str(env_path), qwen.get("api_key_env", "QWEN_API_KEY"), api_key.strip(), quote_mode="always")
    set_key(str(env_path), qwen.get("base_url_env", "QWEN_BASE_URL"), base_url.strip(), quote_mode="always")


def source_download_dir(config: AppConfig) -> Path:
    """链接源下载保存根目录：优先配置 source.download_dir，默认项目根/视频。"""
    value = str(config.raw.get("source", {}).get("download_dir") or "视频").strip()
    path = Path(os.path.expandvars(value))
    if not path.is_absolute():
        path = config.root / path
    return path.expanduser().resolve()


def save_source_download_dir(config: AppConfig, directory: Path) -> Path:
    """持久化链接源下载保存目录到 config.yaml 的 source.download_dir。"""
    resolved = Path(directory).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"保存地址不是有效目录：{resolved}")
    config_path = config.root / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    source = data.setdefault("source", {})
    if not isinstance(source, dict):
        raise ValueError("配置文件 source 段不是映射，已停止保存")
    source["download_dir"] = str(resolved)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    yaml.safe_load(temporary.read_text(encoding="utf-8"))
    temporary.replace(config_path)
    config.raw.setdefault("source", {})["download_dir"] = str(resolved)
    return resolved
