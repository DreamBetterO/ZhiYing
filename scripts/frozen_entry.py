from __future__ import annotations

import ctypes
import os
import traceback

from video_study.cli import main
from video_study.runtime import executable_root


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_path = executable_root() / "crash.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{type(exc).__name__}: {exc}\n\n详细信息已写入：\n{log_path}",
                "知影启动失败",
                0x10,
            )
        raise
