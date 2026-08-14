from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import time
import tkinter as tk
from urllib.parse import urlparse
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import yaml
from dotenv import set_key

from .config import AppConfig
from .aggregate import aggregate_documents
from .pipeline import run_all
from .localplay import register_protocol
from .providers import test_openai_connection
from .runtime import bundled_path
from .utils import TaskCancelled


STAGE_LABELS = {
    "queued": "等待中", "audio": "音频", "asr": "语音识别", "frames": "关键画面",
    "knowledge": "知识整理", "render": "文档生成", "completed": "已完成",
    "cancelling": "正在取消", "cancelled": "已取消", "failed": "失败",
}


def _model_names(value) -> list[str]:
    import re
    if value is None:
        return []
    raw = value if isinstance(value, list) else re.split(r"[\s,，、]+", str(value))
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _speech_models(payload: dict, defaults: list[str]) -> list[str]:
    names = _model_names(payload.get("speech_models")) or _model_names(payload.get("asr_model")) or defaults
    unknown = [name for name in names if name not in {"faster-whisper", "qwen3-asr-0.6b"}]
    if unknown:
        raise ValueError(f"尚未接入的语音模型：{'、'.join(unknown)}")
    return list(dict.fromkeys(names))


def qwen_asr_ready(config: AppConfig) -> bool:
    """仅在可选 Qwen 运行时和模型文件完整时把它展示给普通用户。"""
    settings = config.raw.get("asr", {})

    def resolve(name: str) -> Path:
        value = os.path.expandvars(str(settings.get(name, "")))
        path = Path(value)
        return path if path.is_absolute() else config.root / path

    runtime_python = resolve("qwen_runtime_python")
    runtime_dir = resolve("qwen_runtime_dir")
    model_dir = resolve("qwen_model_dir")
    return (
        runtime_python.is_file()
        and runtime_dir.is_dir()
        and (model_dir / "config.json").is_file()
        and (model_dir / "model.safetensors").is_file()
    )


def _runtime_cloud_config(payload: dict, settings: dict) -> dict:
    api_key = str(payload.get("api_key") or os.getenv(settings.get("api_key_env", "QWEN_API_KEY")) or "").strip()
    base_url = str(payload.get("base_url") or os.getenv(settings.get("base_url_env", "QWEN_BASE_URL"), settings.get("default_base_url", "")) or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if not api_key or len(api_key) > 4096:
        raise ValueError("请输入有效的 API Key")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 http(s) API URL")
    supplied = _model_names(payload.get("llm_models")); configured = list(settings.get("default_models", []))
    models = list(dict.fromkeys([*(supplied or configured), *(configured if supplied and payload.get("allow_fallback", True) else [])]))
    if not models:
        raise ValueError("请填写至少一个大语言模型")
    runtime = deepcopy(settings)
    runtime.update(_runtime_api_key=api_key, _runtime_base_url=base_url, _runtime_models=models, _runtime_max_calls=len(models))
    return runtime


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def clear_workspace_cache(config: AppConfig) -> int:
    """清空配置指定的工作区内容，但保留工作区目录和最终输出。"""
    workspace = config.path("paths", "workspace_dir").resolve()
    protected = {config.root.resolve(), Path.home().resolve(), Path(workspace.anchor).resolve()}
    if workspace in protected:
        raise ValueError(f"拒绝清理不安全的工作区路径：{workspace}")
    if not workspace.exists():
        return 0
    if not workspace.is_dir():
        raise ValueError(f"工作区路径不是目录：{workspace}")
    removed = 0
    for child in workspace.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)
        removed += 1
    return removed


def watermark_options(config: AppConfig) -> tuple[str, float]:
    value = config.raw.get("desktop", {}).get("watermark", {})
    if not isinstance(value, dict):
        value = {}
    text = str(value.get("text", "powed by Fx")).strip()
    try:
        opacity = float(value.get("opacity", 0.14))
    except (TypeError, ValueError):
        opacity = 0.14
    return text, min(0.35, max(0.05, opacity))


def blended_hex(foreground: str, background: str, opacity: float) -> str:
    opacity = min(1.0, max(0.0, opacity))
    fg = tuple(int(foreground[index:index + 2], 16) for index in (1, 3, 5))
    bg = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(back + (front - back) * opacity) for front, back in zip(fg, bg))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def enable_windows_dpi_awareness() -> bool:
    """在创建 Tk 根窗口前启用 Per-Monitor V2，避免 Windows 位图拉伸导致字体模糊。"""
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        return False


def validate_desktop_settings(base_url: str, llm_value: str, speech_value: str) -> tuple[str, list[str], list[str]]:
    base_url = base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("API URL 必须是有效的 http(s) 地址，且不能包含用户名或密码")
    llm_models = _model_names(llm_value)
    if not llm_models or len(llm_models) > 5 or any(len(name) > 160 for name in llm_models):
        raise ValueError("语言模型链需要包含 1–5 个有效模型名称")
    speech_models = _speech_models({"speech_models": speech_value}, ["qwen3-asr-0.6b", "faster-whisper"])
    return base_url, llm_models, speech_models


def config_with_content_level(config: AppConfig, level: str) -> AppConfig:
    if level not in {"精简", "推荐", "丰富"}:
        raise ValueError("内容保留量必须是精简、推荐或丰富")
    raw = deepcopy(config.raw)
    raw.setdefault("render", {})["content_level"] = level
    qwen_budget = raw.setdefault("qwen", {}).setdefault("budget", {})
    raw["qwen"]["content_level"] = level
    raw["qwen"]["timeout_seconds"] = 120
    if level == "精简":
        raw["frames"]["max_keyframes"] = 4
        raw["render"]["offline_section_seconds"] = 480
        raw["render"]["offline_points_per_section"] = 1
        qwen_budget["max_output_tokens"] = min(int(qwen_budget.get("max_output_tokens", 5000)), 3500)
        raw["qwen"]["timeout_seconds"] = 90
    elif level == "丰富":
        raw["frames"]["max_keyframes"] = 12
        raw["render"]["offline_section_seconds"] = 180
        raw["render"]["offline_points_per_section"] = 4
        # qwen3.7-plus 已验证可接受 6000；保留适度余量，避免 8000 档位的端点拒绝。
        qwen_budget["max_output_tokens"] = int(qwen_budget.get("rich_max_output_tokens", 6000))
        raw["qwen"]["timeout_seconds"] = 240
    return AppConfig(root=config.root, raw=raw)


def cached_result_for_video(config: AppConfig, video: Path) -> dict | None:
    work_root = config.path("paths", "workspace_dir").resolve()
    source = str(video.resolve()).casefold()
    for manifest_path in work_root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if str(Path(manifest["source_path"]).resolve()).casefold() != source:
                continue
            render = manifest.get("stages", {}).get("render", {})
            paths = {kind: Path(render.get(kind, "")) for kind in ("markdown", "docx", "pdf")}
            if not all(path.is_file() for path in paths.values()):
                continue
            document_path = manifest_path.parent / "knowledge" / "document.json"
            document = json.loads(document_path.read_text(encoding="utf-8")) if document_path.is_file() else {}
            return {
                "video_id": manifest["video_id"], "manifest": manifest_path, **paths,
                "mode": document.get("mode"), "model": document.get("model"),
                "model_attempts": document.get("model_attempts", []),
                "cloud_usage": document.get("cloud_usage", {}),
            }
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return None


def save_desktop_settings(
    config: AppConfig, base_url: str, llm_models: list[str], speech_models: list[str], content_level: str = "推荐"
) -> None:
    """原子更新可提交的配置元数据；API Key 永不进入此函数。"""
    api_path = (config.root / config.raw.get("api_config", "api.yaml")).resolve()
    config_path = config.root / "config.yaml"
    api_data = yaml.safe_load(api_path.read_text(encoding="utf-8")) or {}
    main_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(api_data.get("qwen"), dict) or not isinstance(main_data.get("asr"), dict):
        raise ValueError("配置文件缺少 qwen 或 asr 映射，已停止保存")
    api_data["qwen"]["default_base_url"] = base_url
    api_data["qwen"]["default_models"] = llm_models
    main_data["asr"]["engine"] = speech_models[0]
    if content_level not in {"精简", "推荐", "丰富"}:
        raise ValueError("内容保留量必须是精简、推荐或丰富")
    main_data.setdefault("desktop", {})["speech_models"] = speech_models
    main_data["desktop"]["content_level"] = content_level
    prepared: list[tuple[Path, Path]] = []
    for target, data in ((api_path, api_data), (config_path, main_data)):
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        yaml.safe_load(temporary.read_text(encoding="utf-8"))
        prepared.append((temporary, target))
    for temporary, target in prepared:
        temporary.replace(target)
    config.raw["qwen"]["default_base_url"] = base_url
    config.raw["qwen"]["default_models"] = llm_models
    config.raw["asr"]["engine"] = speech_models[0]
    config.raw.setdefault("desktop", {})["speech_models"] = speech_models
    config.raw["desktop"]["content_level"] = content_level


def save_api_credentials(config: AppConfig, api_key: str, base_url: str) -> None:
    """把用户明确选择持久化的凭据写入已忽略的本机 .env。"""
    if not api_key.strip():
        raise ValueError("勾选保存 API Key 后，API Key 不能为空")
    qwen = config.raw["qwen"]
    env_path = config.root / ".env"
    set_key(str(env_path), qwen.get("api_key_env", "QWEN_API_KEY"), api_key.strip(), quote_mode="always")
    set_key(str(env_path), qwen.get("base_url_env", "QWEN_BASE_URL"), base_url.strip(), quote_mode="always")
    os.environ[qwen.get("api_key_env", "QWEN_API_KEY")] = api_key.strip()
    os.environ[qwen.get("base_url_env", "QWEN_BASE_URL")] = base_url.strip()


@dataclass
class QueueItem:
    path: Path
    checked: bool = True
    status: str = "等待中"
    stage: str = "queued"
    progress: int = 0
    started_at: float | None = None
    elapsed: float = 0.0
    eta: float | None = None
    result: dict = field(default_factory=dict)


class DesktopApp:
    """不扫描默认目录、直接处理用户所选绝对路径的原生桌面界面。"""

    def __init__(self, root: tk.Tk, config: AppConfig):
        self.root, self.config = root, config
        self.items: list[QueueItem] = []
        self.cancel_event = threading.Event()
        self.running = False
        self.current_item: QueueItem | None = None
        self.aggregate_result: dict = {}
        self.connection_testing = False
        self.root.title("知影 · 视频知识工作台")
        try:
            self.root.iconbitmap(default=str(bundled_path("icon", "知影.ico")))
        except tk.TclError:
            pass
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = min(1380, screen_width - 70), min(940, screen_height - 90)
        left, top = max(0, (screen_width - width) // 2), max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.minsize(min(960, max(720, screen_width - 20)), min(680, max(560, screen_height - 40)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_dpi_and_fonts()
        self._configure_style()
        self._build()
        self._tick()

    @property
    def videos(self) -> list[Path]:
        return [item.path for item in self.items]

    def _configure_dpi_and_fonts(self) -> None:
        dpi = max(96.0, float(self.root.winfo_fpixels("1i")))
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        families = set(tkfont.families(self.root))
        family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Microsoft YaHei"
        for name, size, weight in (
            ("TkDefaultFont", 9, "normal"), ("TkTextFont", 9, "normal"),
            ("TkMenuFont", 9, "normal"), ("TkHeadingFont", 9, "bold"),
            ("TkCaptionFont", 9, "normal"), ("TkSmallCaptionFont", 8, "normal"),
        ):
            try:
                tkfont.nametofont(name, self.root).configure(family=family, size=size, weight=weight)
            except tk.TclError:
                continue

    def _configure_style(self) -> None:
        self.root.configure(bg="#f8f2ed")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f8f2ed")
        style.configure("Card.TFrame", background="#fffaf6")
        style.configure("TLabel", background="#f8f2ed", foreground="#382a2d", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background="#fffaf6", foreground="#4d353a")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"), foreground="#7f2636")
        style.configure("Sub.TLabel", foreground="#846a6f")
        style.configure("Accent.TButton", background="#a9364b", foreground="#ffffff", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#8b293b"), ("disabled", "#c7aeb2")])
        style.configure("Soft.TButton", background="#f3dfe1", foreground="#7f2636", padding=(11, 8), borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#eccdd1")])
        style.configure("Danger.TButton", foreground="#8b293b", padding=(11, 8))
        style.configure("Section.TButton", background="#fffaf6", foreground="#702637", font=("Microsoft YaHei UI", 11, "bold"), padding=(12, 10), borderwidth=0, anchor="w")
        style.map("Section.TButton", background=[("active", "#f7e9e7")])
        style.configure("Toggle.TButton", background="#eee5df", foreground="#665257", padding=(12, 7), font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0)
        style.map("Toggle.TButton", background=[("active", "#e5d9d2"), ("disabled", "#f2ece8")], foreground=[("disabled", "#aa999c")])
        style.configure("ToggleOn.TButton", background="#2f7d55", foreground="#ffffff", padding=(12, 7), font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0)
        style.map("ToggleOn.TButton", background=[("active", "#286c49"), ("disabled", "#8db7a0")], foreground=[("disabled", "#f3f7f4")])
        style.configure("Connection.TLabel", background="#fffaf6", foreground="#846a6f")
        style.configure("ConnectionOk.TLabel", background="#fffaf6", foreground="#24724b", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("ConnectionError.TLabel", background="#fffaf6", foreground="#a12e3f", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StatusOk.TLabel", foreground="#168447", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StatusError.TLabel", foreground="#b3263e", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Help.TButton", padding=(2, 1), font=("Microsoft YaHei UI", 8, "bold"), borderwidth=0)
        style.configure("TButton", padding=(11, 8), font=("Microsoft YaHei UI", 9))
        style.configure("Treeview", rowheight=36, font=("Microsoft YaHei UI", 9), fieldbackground="#ffffff", background="#ffffff", borderwidth=0)
        style.map("Treeview", background=[("selected", "#f2dadd")], foreground=[("selected", "#54232d")])
        style.configure("Treeview.Heading", background="#f4e5e3", foreground="#633641", font=("Microsoft YaHei UI", 9, "bold"), padding=(6, 9), borderwidth=0)
        style.configure("Horizontal.TProgressbar", background="#a9364b", troughcolor="#eadfda", borderwidth=0)

    def _build(self) -> None:
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, bg="#f8f2ed", highlightthickness=0)
        page_scroll = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=page_scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        page_scroll.pack(side="right", fill="y")
        watermark_text, watermark_opacity = watermark_options(self.config)
        if watermark_text:
            self.watermark = tk.Label(
                viewport, text=watermark_text, bg="#f8f2ed",
                fg=blended_hex("#7f2636", "#f8f2ed", watermark_opacity),
                font=("Microsoft YaHei UI", 8), borderwidth=0, padx=4, pady=2,
            )
            self.watermark.place(relx=1.0, rely=1.0, x=-20, y=-10, anchor="se")
            self.watermark.lift()
        outer = ttk.Frame(self.canvas, padding=(24, 18))
        self.page_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize_page)
        self.canvas.bind_all("<MouseWheel>", self._scroll_page)
        self.root.bind("<Configure>", self._queue_layout_sync, add="+")
        self.root.after_idle(self._sync_page_width)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text="知影", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="视频知识整理与溯源工作台", style="Sub.TLabel").pack(anchor="w", pady=(3, 0))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        add_group = ttk.Frame(toolbar)
        add_group.pack(side="left")
        select_group = ttk.Frame(toolbar)
        select_group.pack(side="left", padx=(22, 0))
        manage_group = ttk.Frame(toolbar)
        manage_group.pack(side="left", padx=(22, 0))
        self.add_button = ttk.Button(add_group, text="＋ 添加视频", style="Accent.TButton", command=self.add_videos)
        self.select_all_button = ttk.Button(select_group, text="全选", style="Soft.TButton", command=self.toggle_all)
        self.remove_button = ttk.Button(manage_group, text="移除所选", command=self.remove_selected)
        self.delete_button = ttk.Button(manage_group, text="删除所选产物", style="Danger.TButton", command=self.delete_generated)
        self.clear_cache_button = ttk.Button(manage_group, text="清理全部缓存", style="Danger.TButton", command=self.clear_all_cache)
        self.add_button.pack(side="left")
        self.select_all_button.pack(side="left")
        self.remove_button.pack(side="left", padx=(8, 0))
        self.delete_button.pack(side="left", padx=(8, 0))
        self.clear_cache_button.pack(side="left", padx=(8, 0))
        self.selection_status = tk.StringVar(value="未添加视频")
        ttk.Label(toolbar, textvariable=self.selection_status, style="Sub.TLabel").pack(side="right", pady=8)

        card = ttk.Frame(outer, style="Card.TFrame", padding=1)
        card.pack(fill="both", expand=True)
        columns = ("check", "name", "status", "stage", "progress", "elapsed", "eta", "tokens")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="extended")
        headings = {"check": "选择", "name": "视频文件", "status": "状态", "stage": "当前阶段", "progress": "进度", "elapsed": "已用", "eta": "预计剩余", "tokens": "Token（入 / 出 / 总）"}
        widths = {"check": 58, "name": 335, "status": 105, "stage": 105, "progress": 66, "elapsed": 68, "eta": 78, "tokens": 170}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=50, anchor="w" if column == "name" else "center", stretch=column == "name")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", lambda _event: self.open_video())

        empty = ttk.Frame(outer)
        empty.place(relx=.5, rely=.38, anchor="center")
        self.empty_panel = empty
        ttk.Label(empty, text="还没有视频", font=("Microsoft YaHei UI", 14, "bold")).pack()
        ttk.Label(empty, text="点击“添加视频”可一次选择多个 MP4", style="Sub.TLabel").pack(pady=(5, 0))

        self.settings_card = ttk.Frame(outer, style="Card.TFrame", padding=1)
        self.settings_card.pack(fill="x", pady=(12, 0))
        settings_header = ttk.Frame(self.settings_card, style="Card.TFrame")
        settings_header.pack(fill="x")
        self.settings_open = False
        self.settings_button = ttk.Button(settings_header, text="▸  模型与云端设置", style="Section.TButton", command=self.toggle_settings)
        self.settings_button.pack(side="left", fill="x", expand=True)
        ttk.Label(settings_header, text="连接与凭据", style="Card.TLabel").pack(side="right", padx=14)
        settings = ttk.Frame(self.settings_card, style="Card.TFrame", padding=(14, 6, 14, 12))
        self.settings_body = settings
        self.base_url = self._field(settings, "API URL", 0, 0)
        self.api_key = self._field(settings, "API Key", 0, 2, show="•")
        qwen = self.config.raw.get("qwen", {})
        configured_models = list(qwen.get("default_models", []))
        self.llm_models = self._field(settings, "语言模型链", 1, 0)
        self.speech_models = self._field(settings, "语音模型链", 1, 2)
        self.base_url.insert(0, os.getenv(qwen.get("base_url_env", "QWEN_BASE_URL"), qwen.get("default_base_url", "")))
        saved_api_key = os.getenv(qwen.get("api_key_env", "QWEN_API_KEY"), "")
        if saved_api_key:
            self.api_key.insert(0, saved_api_key)
        self.llm_models.insert(0, "，".join(configured_models))
        saved_speech = list(self.config.raw.get("desktop", {}).get("speech_models", ["faster-whisper"]))
        if qwen_asr_ready(self.config) and "qwen3-asr-0.6b" not in saved_speech:
            saved_speech.insert(0, "qwen3-asr-0.6b")
        self.speech_models.insert(0, "，".join(saved_speech))
        settings.columnconfigure(1, weight=1); settings.columnconfigure(3, weight=1)
        setting_footer = ttk.Frame(settings, style="Card.TFrame")
        setting_footer.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        self.persist_api_key = tk.BooleanVar(value=bool(saved_api_key))
        self.persist_key_button = ttk.Button(setting_footer, text="记住密钥", command=self._toggle_persist_api_key)
        self.persist_key_button.pack(side="left", padx=(0, 14))
        self._refresh_toggle_style(self.persist_key_button, self.persist_api_key.get())
        self.connection_status = tk.StringVar(value="未测试 · /models · 0 Token")
        self.connection_label = ttk.Label(setting_footer, textvariable=self.connection_status, style="Connection.TLabel")
        self.connection_label.pack(side="left")
        self.save_button = ttk.Button(setting_footer, text="保存并测试", style="Soft.TButton", command=self.save_settings)
        self.save_button.pack(side="right")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(14, 6))
        primary_actions = ttk.Frame(actions)
        primary_actions.pack(fill="x")
        self.local_button = ttk.Button(primary_actions, text="本地整理", style="Accent.TButton", command=lambda: self.start("local"))
        self.cloud_button = ttk.Button(primary_actions, text="云端优化", command=lambda: self.start("cloud"))
        self.auto_button = ttk.Button(primary_actions, text="自动处理", command=lambda: self.start("auto"))
        self.local_button.pack(side="left"); self.cloud_button.pack(side="left", padx=8); self.auto_button.pack(side="left")
        self.cancel_button = ttk.Button(primary_actions, text="取消当前视频并清理", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")
        retention = ttk.Frame(actions)
        retention.pack(fill="x", pady=(9, 0))
        ttk.Label(retention, text="内容保留量", style="Sub.TLabel").pack(side="left", padx=(0, 7))
        self.content_level = tk.StringVar(value=self.config.raw.get("desktop", {}).get("content_level", "推荐"))
        self.content_level_box = ttk.Combobox(retention, textvariable=self.content_level, values=("精简", "推荐", "丰富"), state="readonly", width=8)
        self.content_level_box.pack(side="left")
        ttk.Label(retention, text="控制讲义详略、知识点密度和截图数量", style="Sub.TLabel").pack(side="left", padx=8)
        self.aggregate_enabled = tk.BooleanVar(value=False)
        self.aggregate_check = ttk.Button(retention, text="聚合模式", style="Toggle.TButton", command=self._toggle_aggregate, state="disabled")
        ttk.Button(retention, text="?", width=2, style="Help.TButton", command=self.show_aggregate_help).pack(side="right")
        self.aggregate_check.pack(side="right", padx=(0, 5))
        secondary_actions = ttk.Frame(actions)
        secondary_actions.pack(fill="x", pady=(8, 0))
        secondary_specs = (
            ("打开产物目录", self.open_output),
            ("打开原视频", self.open_video), ("打开 PDF", lambda: self.open_artifact("pdf")),
            ("打开 Word", lambda: self.open_artifact("docx")),
        )
        for column, (label, command) in enumerate(secondary_specs):
            ttk.Button(secondary_actions, text=label, command=command).grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            secondary_actions.columnconfigure(column, weight=1)
        self.open_aggregate_button = ttk.Button(secondary_actions, text="打开聚合文档", command=self.open_aggregate, state="disabled")
        self.open_aggregate_button.grid(row=0, column=4, sticky="ew", padx=(4, 0))
        secondary_actions.columnconfigure(4, weight=1)

        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))
        self.status = tk.StringVar(value="请选择视频开始；启动列表不会扫描 Resource")
        self.status_label = ttk.Label(outer, textvariable=self.status, style="Sub.TLabel")
        self.status_label.pack(anchor="w", pady=(6, 0))
        self.status.trace_add("write", lambda *_args: self.status_label.configure(style="Sub.TLabel"))

    def _resize_page(self, event) -> None:
        self._sync_page_width(event.width)

    def _queue_layout_sync(self, _event=None) -> None:
        if not getattr(self, "_layout_sync_pending", False):
            self._layout_sync_pending = True
            self.root.after_idle(self._sync_page_width)

    def _sync_page_width(self, width: int | None = None) -> None:
        self._layout_sync_pending = False
        width = max(640, width or self.canvas.winfo_width())
        self.canvas.itemconfigure(self.page_window, width=width)
        content_width = max(760, width - 52)
        fixed_width = 58 + 105 + 105 + 66 + 68 + 78 + 170
        self.tree.column("name", width=max(190, content_width - fixed_width))

    def _scroll_page(self, event) -> str | None:
        if event.widget is self.tree:
            return None
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def toggle_settings(self) -> None:
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.settings_body.pack(fill="x")
            self.settings_button.configure(text="▾  模型与云端设置")
        else:
            self.settings_body.pack_forget()
            self.settings_button.configure(text="▸  模型与云端设置")
        self.root.after_idle(lambda: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    @staticmethod
    def _field(parent, label: str, row: int, column: int, show: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=4)
        entry = ttk.Entry(parent, show=show)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=4)
        return entry

    def add_videos(self) -> None:
        paths = filedialog.askopenfilenames(title="选择一个或多个视频", filetypes=[("MP4 视频", "*.mp4")])
        added = 0
        known = {str(path).casefold() for path in self.videos}
        for raw in paths:
            path = Path(raw).resolve()
            if path.is_file() and path.suffix.lower() == ".mp4" and str(path).casefold() not in known:
                item = QueueItem(path)
                cached = cached_result_for_video(self.config, path)
                if cached:
                    item.result = cached
                    item.status = "云端缓存就绪" if cached.get("mode") == "cloud_summary" else "本地缓存就绪"
                    item.stage, item.progress = "completed", 100
                self.items.append(item); known.add(str(path).casefold()); added += 1
                self.tree.insert("", "end", iid=str(len(self.items) - 1), values=self._values(item))
        self._show_empty()
        if paths:
            self.status.set(f"已添加 {added} 个视频；使用真实绝对路径，不上传、不复制")

    def remove_selected(self) -> None:
        if self.running:
            return
        self.items = [item for item in self.items if not item.checked]
        self._rebuild_tree(); self._show_empty()

    def selected_items(self) -> list[QueueItem]:
        return [item for item in self.items if item.checked]

    def toggle_all(self) -> None:
        if self.running:
            return
        check = not self.items or not all(item.checked for item in self.items)
        for item in self.items:
            item.checked = check
            self._refresh(item)
        self._update_selection_status()

    @staticmethod
    def _refresh_toggle_style(button: ttk.Button, enabled: bool) -> None:
        button.configure(style="ToggleOn.TButton" if enabled else "Toggle.TButton")

    def _toggle_persist_api_key(self) -> None:
        enabled = not self.persist_api_key.get()
        self.persist_api_key.set(enabled)
        self._refresh_toggle_style(self.persist_key_button, enabled)
        self.status.set("密钥将在保存时写入本机 .env" if enabled else "密钥仅用于本次运行")

    def _toggle_aggregate(self) -> None:
        self.aggregate_enabled.set(not self.aggregate_enabled.get())
        self._refresh_toggle_style(self.aggregate_check, self.aggregate_enabled.get())
        self._aggregate_toggled()

    def _aggregate_toggled(self) -> None:
        self._refresh_toggle_style(self.aggregate_check, self.aggregate_enabled.get())
        if self.aggregate_enabled.get():
            self.status.set("聚合模式已启用：云端阶段生成一份多章节课程讲义，不逐视频重复精炼")
        else:
            self.status.set("聚合模式已关闭：云端阶段将为每个视频分别生成精炼文档")

    def show_aggregate_help(self) -> None:
        messagebox.showinfo(
            "聚合模式说明",
            "关闭（默认）：每个视频分别进行云端优化并生成各自文档。\n\n"
            "开启：\n"
            "• 云端优化：复用所选视频已有缓存，只调用一次云端并生成一份多章节课程讲义；缺少缓存时会提示先完成本地整理。\n"
            "• 自动处理：依次完成所选视频的本地整理，然后只调用一次云端聚合。\n"
            "• 已经精炼过的视频不会重复进行逐视频云端调用。",
        )

    def _tree_click(self, event) -> str | None:
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if row and column == "#1":
            if self.running:
                return "break"
            item = self.items[int(row)]
            item.checked = not item.checked
            self._refresh(item)
            self._update_selection_status()
            return "break"
        return None

    def save_settings(self) -> None:
        try:
            base_url, llm_models, speech_models = validate_desktop_settings(
                self.base_url.get(), self.llm_models.get(), self.speech_models.get()
            )
            save_desktop_settings(self.config, base_url, llm_models, speech_models, self.content_level.get())
            if self.persist_api_key.get():
                save_api_credentials(self.config, self.api_key.get(), base_url)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            messagebox.showerror("设置未保存", str(exc))
            return
        self.base_url.set(base_url)
        self.llm_models.set("，".join(llm_models))
        self.speech_models.set("，".join(speech_models))
        key_note = "API Key 已保存到本机 .env" if self.persist_api_key.get() else "API Key 仅用于当前会话"
        self.status.set(f"设置已保存；{key_note}。正在测试云端连接……")
        self.connection_status.set("● 测试中 · /models · 0 Token")
        self.connection_label.configure(style="Connection.TLabel")
        self.connection_testing = True
        self.save_button.configure(state="disabled", text="测试中…")
        messagebox.showinfo("设置已保存", f"{key_note}。\n现在将请求 /models 测试连接，不发送项目内容，预计 0 推理 Token。")
        threading.Thread(
            target=self._test_connection_worker, args=(self.api_key.get(), base_url, True),
            daemon=True, name="video-study-connection-test",
        ).start()

    def startup_connection_test(self) -> None:
        api_key = self.api_key.get()
        base_url = self.base_url.get().strip()
        if not api_key or not base_url:
            self.connection_status.set("● 未配置 · 请填写 URL 与密钥")
            self.connection_label.configure(style="ConnectionError.TLabel")
            self.status.set("云端连接尚未测试；展开设置填写 API URL 和 API Key 后保存")
            return
        self.connection_status.set("● 启动测试中 · /models · 0 Token")
        self.connection_label.configure(style="Connection.TLabel")
        self.connection_testing = True
        threading.Thread(
            target=self._test_connection_worker, args=(api_key, base_url, False),
            daemon=True, name="video-study-startup-connection-test",
        ).start()

    def _test_connection_worker(self, api_key: str, base_url: str, notify: bool) -> None:
        try:
            result = test_openai_connection(api_key=api_key, base_url=base_url)
        except (ValueError, RuntimeError) as exc:
            self.root.after(0, self._connection_finished, False, str(exc), notify)
            return
        self.root.after(0, self._connection_finished, True, f"{result['latency_ms']} ms · 可见模型 {result['model_count']} 个", notify)

    def _connection_finished(self, ok: bool, detail: str, notify: bool) -> None:
        self.connection_testing = False
        self.save_button.configure(state="disabled" if self.running else "normal", text="保存并测试")
        if ok:
            self.connection_status.set(f"● 连接成功 · {detail} · 0 推理 Token")
            self.connection_label.configure(style="ConnectionOk.TLabel")
            self.status.set("云端连接测试成功")
            self.status_label.configure(style="StatusOk.TLabel")
            if notify:
                messagebox.showinfo("连接测试成功", f"云端兼容接口可用。\n{detail}\n未发送项目内容，推理 Token 为 0。")
        else:
            self.connection_status.set(f"● 连接失败 · {detail}")
            self.connection_label.configure(style="ConnectionError.TLabel")
            self.status.set("云端连接测试失败；请检查 URL、API Key 或网络")
            self.status_label.configure(style="StatusError.TLabel")
            if notify:
                messagebox.showerror("连接测试失败", f"设置已保存，但云端连接不可用：{detail}\n请检查 URL、API Key 或网络。")

    def start(self, action: str) -> None:
        items = self.selected_items()
        if self.running or not items:
            messagebox.showwarning("无法开始", "请先添加视频，或等待当前任务结束。")
            return
        try:
            asr_chain = _speech_models({"speech_models": self.speech_models.get()}, ["qwen3-asr-0.6b", "faster-whisper"])
            cloud = action in {"cloud", "auto"}
            qwen = _runtime_cloud_config({"api_key": self.api_key.get(), "base_url": self.base_url.get(), "llm_models": self.llm_models.get(), "allow_fallback": True}, self.config.raw["qwen"]) if cloud else None
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc)); return
        aggregate = bool(cloud and self.aggregate_enabled.get() and len(items) > 1)
        if aggregate and action == "cloud":
            missing = [item.path.name for item in items if cached_result_for_video(self.config, item.path) is None]
            if missing:
                messagebox.showwarning(
                    "聚合模式缺少缓存",
                    "以下视频没有可聚合的本地/云端文档缓存：\n\n"
                    + "\n".join(f"• {name}" for name in missing[:8])
                    + "\n\n请先完成本地整理，或使用“自动处理”。",
                )
                self.status.set("聚合未开始：部分视频缺少缓存文档")
                return
        if cloud:
            budget = qwen.get("budget", {})
            models = " → ".join(qwen.get("_runtime_models", []))
            max_calls = min(int(budget.get("max_calls_per_video", 1)), int(qwen.get("_runtime_max_calls", 1)))
            if aggregate:
                detail = (
                    "聚合模式已启用：不会逐视频进行云端优化。\n"
                    "将读取所选视频的缓存知识文档，合并后只发起一次整体梳理请求，并只生成一个聚合文档。\n"
                    f"候选模型：{models}\n"
                    f"聚合请求上限：最多 {max_calls} 次候选尝试，输入 {int(budget.get('max_input_chars', 60000)):,} 字符，"
                    f"输出 {int(budget.get('max_output_tokens', 5000)):,} tokens。\n\n"
                    "不发送视频、截图或密钥。是否明确授权？"
                )
            else:
                detail = (
                    "普通模式：将为每个视频分别发送压缩转写文本与来源块 ID，并分别生成云端优化文档。\n"
                    f"候选模型：{models}\n"
                    f"每视频上限：最多 {max_calls} 次候选尝试，输入 {int(budget.get('max_input_chars', 60000)):,} 字符，"
                    f"输出 {int(budget.get('max_output_tokens', 5000)):,} tokens。\n\n"
                    "不发送视频、截图或密钥。是否明确授权？"
                )
            if not messagebox.askyesno("确认云端处理与额度", detail):
                return
        for item in items:
            item.status, item.stage, item.progress, item.result = "等待中", "queued", 0, {}
            self._refresh(item)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.aggregate_result = {}
        self.open_aggregate_button.configure(state="disabled")
        self.running = True; self.cancel_event.clear(); self._set_running(True)
        threading.Thread(
            target=self._run,
            args=(items, action, asr_chain, qwen, self.content_level.get(), aggregate),
            daemon=True, name="video-study-worker",
        ).start()

    def _run(
        self, items: list[QueueItem], action: str, asr_chain: list[str], qwen: dict | None,
        content_level: str, aggregate: bool,
    ) -> None:
        completed_results: list[dict] = []
        task_config = config_with_content_level(self.config, content_level)
        try:
            for number, item in enumerate(items, 1):
                self.current_item = item; item.status = "处理中"; item.started_at = time.monotonic(); item.elapsed = 0
                phase = "local" if action == "auto" else "single"
                def report(stage: str, message: str, percent: int) -> None:
                    if self.cancel_event.is_set():
                        item.status, item.stage, item.eta = "正在取消", "cancelling", None
                        self.root.after(0, self._refresh, item)
                        return
                    shown = percent // 2 if action == "auto" and phase == "local" else (50 + percent // 2 if action == "auto" else percent)
                    item.stage, item.progress, item.status = stage, shown, "处理中"
                    if shown > 1 and item.started_at:
                        item.elapsed = time.monotonic() - item.started_at
                        item.eta = item.elapsed * (100 - shown) / shown
                    self.root.after(0, self._progress, item, f"{number}/{len(items)} · {message}")
                asr = deepcopy(self.config.raw["asr"]); asr["engine"] = asr_chain[0]; asr["_engine_chain"] = asr_chain
                if aggregate and action == "cloud":
                    result = cached_result_for_video(task_config, item.path)
                    if result is None:
                        raise RuntimeError(f"缓存文档不可用：{item.path.name}")
                    item.status, item.stage, item.progress, item.eta = "聚合素材就绪", "completed", 100, 0
                elif aggregate and action == "auto":
                    result = run_all(
                        task_config, str(item.path), cloud_summary=False, asr_settings=asr,
                        progress=report, cancel_check=self.cancel_event.is_set,
                    )[0]
                    item.status, item.stage, item.progress, item.eta = "本地素材就绪", "completed", 100, 0
                else:
                    if action == "auto":
                        run_all(task_config, str(item.path), cloud_summary=False, asr_settings=asr, progress=report, cancel_check=self.cancel_event.is_set)
                        phase = "cloud"
                    result = run_all(task_config, str(item.path), force_summary=action in {"cloud", "auto"}, cloud_summary=action in {"cloud", "auto"}, qwen_settings=qwen, asr_settings=asr, progress=report, cancel_check=self.cancel_event.is_set)[0]
                    mode = result.get("mode")
                    completed_status = "云端优化完成" if mode == "cloud_summary" else "本地整理完成"
                    item.status, item.stage, item.progress, item.eta = completed_status, "completed", 100, 0
                item.result = result
                completed_results.append(result)
                self.root.after(0, self._refresh, item)
            if aggregate and qwen:
                self.root.after(0, self._aggregate_started)
                try:
                    aggregate_qwen = dict(qwen)
                    aggregate_qwen["content_level"] = content_level
                    aggregate_qwen["budget"] = {**aggregate_qwen.get("budget", {}), **task_config.raw["qwen"].get("budget", {})}
                    aggregate_result = aggregate_documents(task_config, completed_results, aggregate_qwen)
                except Exception as exc:
                    self.root.after(0, self._finish, f"单视频均已完成，但聚合失败：{type(exc).__name__}: {exc}")
                    return
                self.aggregate_result = aggregate_result
                self.root.after(0, self._aggregate_finished)
            else:
                self.root.after(0, self._finish, "全部处理完成")
        except TaskCancelled:
            cancelled = self.current_item
            if cancelled:
                self._cleanup(cancelled.path)
                cancelled.status, cancelled.stage, cancelled.eta = "已取消", "cancelled", None
                self.root.after(0, self._refresh, cancelled)
            self.root.after(0, self._finish, "当前视频已取消并精确清理；队列其他视频未删除")
        except Exception as exc:
            failed = self.current_item
            if failed:
                failed.status, failed.stage, failed.eta = "失败", "failed", None
                self.root.after(0, self._refresh, failed)
            self.root.after(0, self._finish, f"失败：{type(exc).__name__}: {exc}")
        finally:
            self.current_item = None

    def _progress(self, item: QueueItem, message: str) -> None:
        if item.stage != "cancelling":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress["value"] = item.progress; self.status.set(f"{item.path.name} · {message}"); self._refresh(item)

    def _finish(self, message: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.running = False; self._set_running(False); self.status.set(message)

    def _aggregate_started(self) -> None:
        self.current_item = None
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.cancel_button.configure(state="disabled", text="正在聚合…")
        self.status.set("正在把多个知识资料整理为多章节课程讲义……")

    def _aggregate_finished(self) -> None:
        self.open_aggregate_button.configure(state="normal")
        self._finish("全部视频及聚合章节文档处理完成")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button in (
            self.local_button, self.cloud_button, self.auto_button, self.add_button,
            self.select_all_button, self.remove_button, self.delete_button, self.clear_cache_button,
            self.persist_key_button,
        ):
            button.configure(state=state)
        self.save_button.configure(state="disabled" if running or self.connection_testing else "normal")
        self.content_level_box.configure(state="disabled" if running else "readonly")
        if running:
            self.aggregate_check.configure(state="disabled")
        else:
            self.aggregate_check.configure(state="normal" if len(self.selected_items()) > 1 else "disabled")
        self.cancel_button.configure(text="取消当前视频并清理", state="normal" if running else "disabled")

    def cancel(self) -> None:
        if self.current_item and messagebox.askyesno("取消当前视频", f"仅取消并清理：\n{self.current_item.path.name}\n\n源视频和队列中其他视频都会保留。"):
            self.cancel_event.set()
            self.current_item.status = "正在取消"
            self.current_item.stage = "cancelling"
            self.current_item.eta = None
            self._refresh(self.current_item)
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.cancel_button.configure(text="正在取消…", state="disabled")
            self.status.set(f"正在终止 {self.current_item.path.name} 的当前阶段；完成后将只清理该视频……")

    def delete_generated(self) -> None:
        items = self.selected_items()
        if not items or not messagebox.askyesno("删除生成文件", "删除所选视频的缓存与文档？原视频始终保留。"):
            return
        for item in items: self._cleanup(item.path); item.result = {}; item.status = "等待中"; item.stage = "queued"; item.progress = 0; self._refresh(item)
        self.status.set("所选生成文件已删除；原视频保留")

    def clear_all_cache(self) -> None:
        if self.running:
            return
        workspace = self.config.path("paths", "workspace_dir").resolve()
        if not messagebox.askyesno(
            "清理全部缓存",
            f"确定清空所有工作区缓存吗？\n\n{workspace}\n\n"
            "转写、音频、关键帧和知识 JSON 将被删除，之后处理视频需要重新生成。"
            "最终 Word、PDF、Markdown 和原视频都会保留；但文档中的本地溯源链接及 Markdown 截图"
            "会在重新处理对应视频前暂时不可用。",
        ):
            return
        try:
            removed = clear_workspace_cache(self.config)
        except (OSError, ValueError) as exc:
            messagebox.showerror("清理失败", str(exc))
            self.status.set("缓存清理失败")
            return
        for item in self.items:
            item.result = {}
            item.status = "等待中"
            item.stage = "queued"
            item.progress = 0
            self._refresh(item)
        self.aggregate_result = {}
        self.open_aggregate_button.configure(state="disabled")
        self.status.set(f"已清理全部工作区缓存（{removed} 项）；最终文档和原视频已保留")

    def open_artifact(self, kind: str) -> None:
        items = self.selected_items()
        if len(items) != 1 or not items[0].result.get(kind):
            self.status.set("请选择一个已完成的视频再打开产物"); return
        path = Path(items[0].result[kind])
        if path.exists(): os.startfile(path)  # type: ignore[attr-defined]

    def open_video(self) -> None:
        items = self.selected_items()
        if len(items) != 1:
            self.status.set("请只勾选一个视频再打开原文件")
            return
        if items[0].path.exists():
            os.startfile(items[0].path)  # type: ignore[attr-defined]
        else:
            messagebox.showerror("无法打开", "原视频已被移动或删除")

    def open_output(self) -> None:
        items = self.selected_items()
        path = Path(items[0].result.get("docx", "")).parent if len(items) == 1 and items[0].result else self.config.path("paths", "output_dir")
        if path.exists(): os.startfile(path)  # type: ignore[attr-defined]

    def open_aggregate(self) -> None:
        path = Path(self.aggregate_result.get("docx", "")) if self.aggregate_result else Path()
        if self.aggregate_result and path.is_file():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            self.status.set("当前批次尚未生成聚合文档")

    def _values(self, item: QueueItem) -> tuple[str, ...]:
        usage = item.result.get("cloud_usage") or {}
        tokens = "—" if not usage else f"{usage.get('prompt_tokens', 0):,} / {usage.get('completion_tokens', 0):,} / {usage.get('total_tokens', 0):,}"
        return ("☑" if item.checked else "☐", item.path.name, item.status, STAGE_LABELS.get(item.stage, item.stage), f"{item.progress}%", format_duration(item.elapsed), format_duration(item.eta), tokens)

    def _refresh(self, item: QueueItem) -> None:
        try: index = self.items.index(item); self.tree.item(str(index), values=self._values(item))
        except (ValueError, tk.TclError): pass

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items): self.tree.insert("", "end", iid=str(index), values=self._values(item))
        self._update_selection_status()

    def _update_selection_status(self) -> None:
        checked = sum(item.checked for item in self.items)
        self.selection_status.set(f"已选择 {checked} / {len(self.items)} 个视频" if self.items else "未添加视频")
        self.select_all_button.configure(text="取消全选" if self.items and checked == len(self.items) else "全选")
        if hasattr(self, "aggregate_check") and not self.running:
            self.aggregate_check.configure(state="normal" if checked > 1 else "disabled")
            if checked <= 1:
                self.aggregate_enabled.set(False)
                self._refresh_toggle_style(self.aggregate_check, False)

    def _show_empty(self) -> None:
        if self.items: self.empty_panel.place_forget()
        else: self.empty_panel.place(relx=.5, rely=.38, anchor="center")
        self._update_selection_status()

    def _tick(self) -> None:
        if self.current_item and self.current_item.started_at:
            self.current_item.elapsed = time.monotonic() - self.current_item.started_at
            self._refresh(self.current_item)
        self.root.after(1000, self._tick)

    def _cleanup(self, video: Path) -> None:
        work_root = self.config.path("paths", "workspace_dir").resolve(); output_root = self.config.path("paths", "output_dir").resolve()
        for manifest_path in work_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if str(Path(manifest["source_path"]).resolve()).casefold() != str(video.resolve()).casefold(): continue
                targets = ((manifest_path.parent.resolve(), work_root), ((output_root / manifest["video_id"]).resolve(), output_root))
                for target, root in targets:
                    if target != root and root in target.parents and target.exists(): shutil.rmtree(target)
            except (OSError, ValueError, KeyError): continue

    def close(self) -> None:
        if self.running and not messagebox.askyesno("退出", "当前视频仍在处理。退出会终止界面，确定继续？"):
            return
        self.cancel_event.set()
        self.root.destroy()


def launch_desktop(config: AppConfig) -> None:
    enable_windows_dpi_awareness()
    register_protocol(config.root / "config.yaml")
    root = tk.Tk()
    app = DesktopApp(root, config)
    root.after(250, app.startup_connection_test)
    root.after_idle(lambda: app.canvas.yview_moveto(0.0))
    root.mainloop()
