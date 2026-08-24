from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    root: Path
    raw: dict[str, Any]

    def path(self, section: str, key: str) -> Path:
        value = Path(os.path.expandvars(str(self.raw[section][key])))
        return value if value.is_absolute() else (self.root / value).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    load_dotenv(config_path.parent / ".env", override=False)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    api_config = raw.get("api_config")
    if api_config:
        api_path = Path(api_config)
        if not api_path.is_absolute():
            api_path = (config_path.parent / api_path).resolve()
        with api_path.open("r", encoding="utf-8") as handle:
            api_raw = yaml.safe_load(handle) or {}
        if not isinstance(api_raw, dict):
            raise ValueError(f"API 配置必须是 YAML 映射：{api_path}")
        for section, value in api_raw.items():
            if section in raw:
                raise ValueError(f"配置段 {section!r} 同时出现在 {config_path.name} 和 {api_path.name}")
            raw[section] = value
    return AppConfig(root=config_path.parent, raw=raw)
