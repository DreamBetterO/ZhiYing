from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_root() -> Path:
    """返回用户可见的应用目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """返回源码资源或 PyInstaller 内部资源目录。"""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle).resolve() if bundle else executable_root()


def default_config_path() -> Path:
    return executable_root() / "config.yaml"


def bundled_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def find_tool(name: str) -> str | None:
    """优先使用发行包随附工具，再查找用户 PATH。"""
    suffix = ".exe" if os.name == "nt" and not name.lower().endswith(".exe") else ""
    tool_name = name + suffix
    candidates = (
        bundled_path("tools", "ffmpeg", tool_name),
        bundled_path("tools", name, tool_name),
        bundled_path("tools", tool_name),
        executable_root() / "tools" / "ffmpeg" / tool_name,
        executable_root() / "tools" / name / tool_name,
        executable_root() / "tools" / tool_name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def add_bundled_tools_to_path() -> None:
    directories = (
        bundled_path("tools"),
        bundled_path("tools", "ffmpeg"),
        bundled_path("tools", "node"),
        executable_root() / "tools",
        executable_root() / "tools" / "ffmpeg",
        executable_root() / "tools" / "node",
    )
    prefixes = [str(path) for path in directories if path.is_dir()]
    if prefixes:
        os.environ["PATH"] = os.pathsep.join([*prefixes, os.environ.get("PATH", "")])
