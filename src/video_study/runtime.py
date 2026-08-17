from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def project_root() -> Path:
    """返回源码项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return project_root() / "config.yaml"


def project_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def find_tool(name: str) -> str | None:
    """优先使用项目 tools 目录，再查找用户 PATH。"""
    suffix = ".exe" if os.name == "nt" and not name.lower().endswith(".exe") else ""
    tool_name = name + suffix
    candidates = (
        project_path("tools", "ffmpeg", tool_name),
        project_path("tools", name, tool_name),
        project_path("tools", tool_name),
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def add_project_tools_to_path() -> None:
    directories = (
        project_path("tools"),
        project_path("tools", "ffmpeg"),
        project_path("tools", "node"),
    )
    prefixes = [str(path) for path in directories if path.is_dir()]
    if prefixes:
        os.environ["PATH"] = os.pathsep.join([*prefixes, os.environ.get("PATH", "")])
