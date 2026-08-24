from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from ..config import AppConfig
from ..runtime import find_tool
from ..execution.artifacts import WorkspaceCatalog
from ..utils import background_process_kwargs


SCHEME = "video-study"


def timestamp_url(video_id: str, seconds: int | float) -> str:
    return f"{SCHEME}://play/{quote(str(video_id), safe='')}?t={max(0, int(seconds))}"


def launch_local_player(video: Path, seconds: int | float = 0) -> bool:
    video = video.resolve()
    if not video.is_file():
        raise RuntimeError("原视频已被移动或删除")
    start = max(0.0, float(seconds))
    ffplay = find_tool("ffplay")
    if ffplay:
        subprocess.Popen(
            [ffplay, "-ss", f"{start:.3f}", "-i", str(video), "-autoexit", "-window_title", video.name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **background_process_kwargs(),
        )
        return True
    os.startfile(video)  # type: ignore[attr-defined]
    return start == 0


def play_protocol_url(config: AppConfig, url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != SCHEME or parsed.netloc != "play":
        raise ValueError("无效的本地回看链接")
    video_id = unquote(parsed.path.lstrip("/"))
    try:
        seconds = max(0, int(parse_qs(parsed.query).get("t", ["0"])[0]))
    except ValueError as exc:
        raise ValueError("无效的时间戳") from exc
    entry = WorkspaceCatalog(
        config.path("paths", "workspace_dir"),
        project_root=config.root,
    ).find_by_video_id(video_id)
    if entry:
        return launch_local_player(Path(str(entry.manifest["source_path"])), seconds)
    raise RuntimeError("未找到该视频的本地缓存，请在桌面软件中重新添加视频")


def register_protocol(config_path: Path) -> bool:
    if os.name != "nt":
        return False
    import winreg
    config_path = config_path.resolve()
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}" play-url --config "{config_path}" "%1"'
    else:
        command = f'"{sys.executable}" -m zhiying play-url --config "{config_path}" "%1"'
    root = rf"Software\Classes\{SCHEME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:知影本地回看")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    return True
