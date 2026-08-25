from __future__ import annotations

import os
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .. import __version__
from ..application.requests import AggregateRequest, CloudAuthorization, ProcessingRequest, ProcessingResult
from ..config import AppConfig
from . import STAGE_LABELS, cloud_authorization_message, format_duration, watermark_options, blended_hex
from .controller import DesktopController
from .models import DesktopState, QueueItem
from .settings import (
    VISUAL_TEACHING_LEVELS,
    qwen_asr_ready,
    save_api_credentials,
    save_desktop_settings,
    save_source_download_dir,
    source_download_dir,
    validate_desktop_settings,
    validate_speech_models,
)


PRIMARY_UI_ACTIONS = (
    "add", "add_link", "toggle_all", "remove", "clear_selected_cache", "clear_cache",
    "local", "cloud", "cancel", "aggregate", "local_aggregate", "open_output", "open_video",
    "open_markdown", "open_docx", "open_pdf", "open_aggregate", "settings",
)

QUEUE_COLUMNS = (
    "order", "check", "source", "name", "status", "stage", "progress", "elapsed", "tokens",
)

PRODUCT_DISPLAY_NAME = "知影"
UI_COPY = {
    "tagline": "将教学视频整理为可溯源的学习文档",
    "add_local": "＋ 添加本地视频",
    "add_link": "＋ 添加视频链接",
    "local_process": "生成本地文档",
    "cloud_process": "使用云端优化",
    "local_merge": "合并本地文档",
    "cloud_merge": "云端优化合并",
    "settings": "模型与服务设置",
    "open_output": "打开输出目录",
    "open_merged": "打开合并文档",
}

_RUNNING_STATES = {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}
UI_EVENT_BATCH_LIMIT = 200
EDITORIAL_BRIEF_FILENAME = "课程资料整理偏好.md"
MAX_EDITORIAL_BRIEF_CHARS = 4000
DEFAULT_EDITORIAL_BRIEF_TEXT = """# 课程资料整理偏好

希望最终文档形成适合系统学习和复习的正式课程资料。

优先提炼全课主线，再根据内容关系组织章节；如果课程明显按照演示、推导、案例或操作步骤逐步展开，可以保留老师讲课的时间顺序。

重点关注概念定义、判断条件、推导关系、操作步骤、案例结论、容易混淆的地方，以及老师反复强调或明确提醒的内容。

删除寒暄、口头语和无信息重复。详略由知识的重要程度、理解难度和课程强调程度决定，不平均分配篇幅。
"""


def drain_ui_events(events: queue.Queue, *, limit: int = UI_EVENT_BATCH_LIMIT) -> list:
    """有界排空 UI 队列，防止持续生产事件时饿死 Tk 主循环。"""
    drained = []
    for _ in range(max(1, int(limit))):
        try:
            drained.append(events.get_nowait())
        except queue.Empty:
            break
    return drained


class DesktopView:
    """Tk 展示层：呈现完整产品能力，并只通过 Controller 调用业务。"""

    BG = "#f8f2ed"
    CARD = "#fffaf6"
    RED = "#a9364b"
    RED_DARK = "#7f2636"
    INK = "#382a2d"
    MUTED = "#846a6f"
    GREEN = "#2f7d55"

    def __init__(self, root: tk.Tk, config: AppConfig, controller: DesktopController, cloud_resolver) -> None:
        self.root = root
        self.config = config
        self.controller = controller
        self.cloud_resolver = cloud_resolver
        self.status = tk.StringVar(value="请先添加视频。默认本地处理，源视频不会上传。")
        self.selection_status = tk.StringVar(value="未添加视频")
        self.content_level = tk.StringVar(value=str(config.raw.get("desktop", {}).get("content_level", "推荐")))
        visual = str(config.raw.get("visual_teaching", {}).get("level", "auto"))
        self.visual_level = tk.StringVar(value=next(
            (label for label, value in VISUAL_TEACHING_LEVELS.items() if value == visual), "自动",
        ))
        qwen = config.raw.get("qwen", {})
        api_env = str(qwen.get("api_key_env", "QWEN_API_KEY"))
        base_env = str(qwen.get("base_url_env", "QWEN_BASE_URL"))
        self.api_key = tk.StringVar(value=os.getenv(api_env, ""))
        self.base_url = tk.StringVar(value=os.getenv(base_env, str(qwen.get("default_base_url", ""))))
        self.models = tk.StringVar(value="，".join(qwen.get("default_models", ())))
        speech = list(config.raw.get("desktop", {}).get("speech_models", ("faster-whisper",)))
        if qwen_asr_ready(config) and "qwen3-asr-0.6b" not in speech:
            speech.insert(0, "qwen3-asr-0.6b")
        self._speech_engine = tk.StringVar(
            value=speech[0] if speech else "faster-whisper",
        )
        self.remember_key = tk.BooleanVar(value=bool(self.api_key.get()))
        self.aggregate_result: dict = {}
        self._buttons: list[ttk.Button] = []
        self._configure_window()
        self._configure_style()
        self._build()
        self._drain_after_id = self.root.after(80, self._drain_events)
        self._tick_after_id = self.root.after(1000, self._tick)

    def _configure_window(self) -> None:
        self.root.title(f"{PRODUCT_DISPLAY_NAME} · 视频知识工作台 · V{__version__}")
        try:
            self.root.iconbitmap(default=str(self.config.root / "icon" / "知影.ico"))
        except tk.TclError:
            pass
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1380, max(1040, screen_width - 80))
        height = min(940, max(700, screen_height - 100))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.minsize(min(1040, screen_width - 20), min(680, screen_height - 40))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        dpi = max(96.0, float(self.root.winfo_fpixels("1i")))
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        families = set(tkfont.families(self.root))
        family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Microsoft YaHei"
        for name, size, weight in (
            ("TkDefaultFont", 9, "normal"), ("TkTextFont", 9, "normal"),
            ("TkMenuFont", 9, "normal"), ("TkHeadingFont", 9, "bold"),
        ):
            try:
                tkfont.nametofont(name, self.root).configure(family=family, size=size, weight=weight)
            except tk.TclError:
                pass

    def _configure_style(self) -> None:
        self.root.configure(bg=self.BG)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.INK)
        style.configure("Card.TLabel", background=self.CARD, foreground="#4d353a")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"), foreground=self.RED_DARK)
        style.configure("Sub.TLabel", foreground=self.MUTED)
        style.configure("Chip.TLabel", background="#f3dfe1", foreground=self.RED_DARK, padding=(8, 4))
        style.configure("Accent.TButton", background=self.RED, foreground="#ffffff", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#8b293b"), ("disabled", "#c7aeb2")])
        style.configure("Soft.TButton", background="#f3dfe1", foreground=self.RED_DARK, padding=(11, 8), borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#eccdd1")])
        style.configure("Danger.TButton", foreground="#8b293b", padding=(11, 8))
        style.configure("Section.TButton", background=self.CARD, foreground="#702637", font=("Microsoft YaHei UI", 10, "bold"), padding=(12, 10), borderwidth=0, anchor="w")
        style.configure("Success.TButton", background=self.GREEN, foreground="#ffffff", padding=(13, 8), borderwidth=0)
        style.map("Success.TButton", background=[("active", "#286c49")])
        style.configure("TButton", padding=(11, 8))
        style.configure("Treeview", rowheight=36, fieldbackground="#ffffff", background="#ffffff", borderwidth=0)
        style.map("Treeview", background=[("selected", "#f2dadd")], foreground=[("selected", "#54232d")])
        style.configure("Treeview.Heading", background="#f4e5e3", foreground="#633641", font=("Microsoft YaHei UI", 9, "bold"), padding=(6, 9), borderwidth=0)
        style.configure("Horizontal.TProgressbar", background=self.RED, troughcolor="#eadfda", borderwidth=0)
        # 主题无关的勾选框：使用 Canvas 绘制 ✓，避免 clam 主题的叉号问题
        style.configure("TCheckbutton", background=self.CARD, foreground="#4d353a")

    def _build(self) -> None:
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        outer = ttk.Frame(viewport, padding=(24, 18))
        outer.pack(fill="both", expand=True)

        watermark_text, watermark_opacity = watermark_options(self.config)
        if watermark_text:
            watermark = tk.Label(
                viewport, text=watermark_text, bg=self.BG,
                fg=blended_hex(self.RED_DARK, self.BG, watermark_opacity),
                font=("Microsoft YaHei UI", 8), borderwidth=0,
            )
            watermark.place(relx=1.0, rely=1.0, x=-20, y=-10, anchor="se")

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text=PRODUCT_DISPLAY_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=UI_COPY["tagline"], style="Sub.TLabel").pack(anchor="w", pady=(3, 0))
        ttk.Label(header, text=f"V{__version__}", style="Chip.TLabel").pack(side="right", pady=8)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        self.add_button = ttk.Button(toolbar, text=UI_COPY["add_local"], style="Accent.TButton", command=self.add_videos)
        self.add_link_button = ttk.Button(toolbar, text=UI_COPY["add_link"], style="Accent.TButton", command=self.add_link_dialog)
        self.select_all_button = ttk.Button(toolbar, text="全选", style="Soft.TButton", command=self.toggle_all)
        self.remove_button = ttk.Button(toolbar, text="移除所选", command=self.remove_selected)
        self.clear_selected_button = ttk.Button(toolbar, text="删除所选缓存", style="Danger.TButton", command=self.clear_selected_cache)
        self.clear_cache_button = ttk.Button(toolbar, text="清空全部缓存", style="Danger.TButton", command=self.clear_workspace)
        for button, pad in (
            (self.add_button, (0, 0)), (self.add_link_button, (8, 0)),
            (self.select_all_button, (20, 0)),
            (self.remove_button, (8, 0)), (self.clear_selected_button, (8, 0)), (self.clear_cache_button, (8, 0)),
        ):
            button.pack(side="left", padx=pad)
        ttk.Label(toolbar, textvariable=self.selection_status, style="Sub.TLabel").pack(side="right", pady=8)

        card = ttk.Frame(outer, style="Card.TFrame", padding=1)
        card.pack(fill="both", expand=True)
        columns = QUEUE_COLUMNS
        self.tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="browse", height=9)
        headings = {
            "order": "顺序", "check": "选择", "source": "来源", "name": "视频文件", "status": "状态", "stage": "当前步骤",
            "progress": "进度", "elapsed": "用时", "tokens": "云端用量（输入 / 输出 / 合计）",
        }
        widths = {"order": 46, "check": 46, "source": 46, "name": 170, "status": 125, "stage": 115, "progress": 50, "elapsed": 60, "tokens": 225}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=50, anchor="w" if column == "name" else "center", stretch=column == "name")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", lambda _event: self.open_video())
        self.tree.bind("<B1-Motion>", self._tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._tree_drop)
        self._drag_row: str | None = None
        self.empty_panel = ttk.Frame(card, style="Card.TFrame")
        ttk.Label(self.empty_panel, text="开始整理第一条视频", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).pack()
        ttk.Label(self.empty_panel, text="添加本地视频或视频链接，生成可回看的 Markdown、Word 和 PDF 学习文档", style="Card.TLabel").pack(pady=(5, 0))

        preferences = ttk.Frame(outer, style="Card.TFrame", padding=(14, 11))
        preferences.pack(fill="x", pady=(12, 0))
        ttk.Label(preferences, text="处理偏好", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(0, 16))
        ttk.Label(preferences, text="内容保留量", style="Card.TLabel").pack(side="left")
        self.content_level_box = ttk.Combobox(preferences, textvariable=self.content_level, values=("精简", "推荐", "丰富"), state="readonly", width=7)
        self.content_level_box.pack(side="left", padx=(6, 18))
        ttk.Label(preferences, text="图文教学", style="Card.TLabel").pack(side="left")
        self.visual_level_box = ttk.Combobox(preferences, textvariable=self.visual_level, values=tuple(VISUAL_TEACHING_LEVELS), state="readonly", width=7)
        self.visual_level_box.pack(side="left", padx=(6, 18))
        ttk.Label(preferences, text="内容详略和配图强度可分别调整", style="Card.TLabel").pack(side="left")
        self.settings_button = ttk.Button(preferences, text=UI_COPY["settings"], style="Section.TButton", command=self.open_settings)
        self.settings_button.pack(side="right")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(14, 6))
        primary = ttk.Frame(actions)
        primary.pack(fill="x")
        self.local_button = ttk.Button(primary, text=UI_COPY["local_process"], style="Accent.TButton", command=lambda: self.start(False))
        self.cloud_button = ttk.Button(primary, text=UI_COPY["cloud_process"], command=lambda: self.start(True))
        self.local_aggregate_button = ttk.Button(primary, text=UI_COPY["local_merge"], command=self.aggregate_local)
        self.aggregate_button = ttk.Button(primary, text=UI_COPY["cloud_merge"], style="Success.TButton", command=self.aggregate)
        self.local_button.pack(side="left")
        self.cloud_button.pack(side="left", padx=8)
        self.local_aggregate_button.pack(side="left")
        self.aggregate_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(primary, text="取消当前任务", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")

        secondary = ttk.Frame(actions)
        secondary.pack(fill="x", pady=(8, 0))
        specs = (
            (UI_COPY["open_output"], self.open_output), ("打开原视频", self.open_video),
            ("打开 Markdown", lambda: self.open_artifact("markdown")),
            ("打开 Word", lambda: self.open_artifact("docx")),
            ("打开 PDF", lambda: self.open_artifact("pdf")),
            (UI_COPY["open_merged"], self.open_aggregate),
        )
        self.artifact_buttons = []
        for column, (label, command) in enumerate(specs):
            button = ttk.Button(secondary, text=label, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            secondary.columnconfigure(column, weight=1)
            self.artifact_buttons.append(button)

        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=(5, 0))
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="x", pady=(6, 0))
        self.log = tk.Text(
            log_frame, height=2, wrap="word", relief="flat", bg=self.BG, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9), padx=0, pady=4,
        )
        self.log.pack(fill="x")
        self._log_history_limit = 200
        self.log.configure(state="normal")
        self.log.insert("end", "请先添加视频。默认本地处理，源视频不会上传。")
        self.log.configure(state="disabled")
        self._buttons = [
            self.add_button, self.add_link_button, self.select_all_button,
            self.remove_button, self.clear_selected_button,
            self.clear_cache_button, self.settings_button, self.local_button, self.cloud_button,
            self.local_aggregate_button, self.aggregate_button,
        ]
        self._refresh()

    def add_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择一个或多个视频",
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi"), ("所有文件", "*.*")],
        )
        if paths:
            self._command(lambda: self.controller.add([Path(path) for path in paths]))

    def add_link_dialog(self) -> None:
        """「＋ 添加链接」对话框：粘贴链接 → 下载到本地缓存 → 已就绪（不自动开始整理）。"""
        if not self.config.raw.get("source", {}).get("enabled", True):
            messagebox.showwarning("链接源未启用", "当前配置已关闭链接源获取（source.enabled=false）")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("添加视频链接")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        frame = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="添加视频链接", style="Card.TLabel",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="支持视频页面链接、直链、HLS 流或 B 站 BV/av 号；"
                 "首次使用请注意：仅下载您有权访问的内容。当前暂不支持抖音链接。",
            style="Card.TLabel", wraplength=560, justify="left",
        ).pack(anchor="w", pady=(6, 10))
        self._link_url_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self._link_url_var, width=72)
        entry.pack(fill="x")
        entry.focus_set()

        hint = tk.StringVar(value="")
        ttk.Label(frame, textvariable=hint, style="Card.TLabel", foreground="#2f7d55").pack(anchor="w", pady=(8, 4))

        def on_submit(_event=None) -> str | None:
            url = self._link_url_var.get().strip()
            if not url:
                messagebox.showwarning("链接为空", "请先粘贴视频链接", parent=dialog)
                return "break"
            try:
                self.controller.add_url(url)
            except (ValueError, RuntimeError) as exc:
                messagebox.showwarning("无法添加", str(exc), parent=dialog)
                return "break"
            hint.set("已加入队列：下载完成后会显示“已就绪”，勾选后点击“生成本地文档”即可开始")
            dialog.destroy()
            return "break"

        entry.bind("<Return>", on_submit)
        # 左端：「修改保存地址」设置（与右侧操作按钮分开）
        left_buttons = ttk.Frame(frame, style="Card.TFrame")
        left_buttons.pack(side="left", anchor="w", pady=(12, 0))

        def choose_save_dir() -> None:
            current = source_download_dir(self.config)
            chosen = filedialog.askdirectory(
                parent=dialog, title="选择视频链接保存地址",
                initialdir=str(current) if current.is_dir() else str(self.config.root),
            )
            if not chosen:
                return
            try:
                save_source_download_dir(self.config, Path(chosen))
            except (OSError, ValueError) as exc:
                messagebox.showwarning("无法保存地址", str(exc), parent=dialog)

        ttk.Button(left_buttons, text="修改保存地址", command=choose_save_dir).pack(side="left")

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(side="right", anchor="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="解析并下载", style="Accent.TButton", command=on_submit).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        dialog.geometry(f"{max(620, dialog.winfo_reqwidth())}x{dialog.winfo_reqheight()}")

    def remove_selected(self) -> None:
        self._command(self.controller.remove_selected)

    def toggle_all(self) -> None:
        self._command(self.controller.toggle_all)

    def start(self, use_cloud: bool) -> None:
        try:
            primary = self._speech_engine.get()
            speech = validate_speech_models(primary)
            cloud = self._cloud_authorization(aggregate=False) if use_cloud else None
        except ValueError as exc:
            messagebox.showerror("设置无效", str(exc))
            return
        self._command(lambda: self.controller.start(self._request_factory(cloud)))

    def _request_factory(self, cloud: CloudAuthorization | None):
        def build(item: QueueItem) -> ProcessingRequest:
            common = dict(
                content_level=self.content_level.get(),
                visual_level=VISUAL_TEACHING_LEVELS[self.visual_level.get()],
                speech_models=tuple(validate_speech_models(self._speech_engine.get())),
                cloud=cloud,
            )
            if item.source_kind == "url" and item.source_url:
                return ProcessingRequest(url=item.source_url, **common)
            return ProcessingRequest(video=item.resolved_path, **common)
        return build

    def _cloud_authorization(self, *, aggregate: bool) -> CloudAuthorization:
        base, models, _speech = validate_desktop_settings(
            self.base_url.get(), self.models.get(), self._speech_engine.get(),
        )
        qwen = {
            **self.config.raw.get("qwen", {}),
            "_runtime_base_url": base,
            "_runtime_models": models,
        }
        brief_text = self._cloud_authorization_dialog(qwen, aggregate=aggregate)
        if brief_text is None:
            raise ValueError("未授权云端处理")
        return self.cloud_resolver(
            api_key=self.api_key.get(), base_url=base,
            models=tuple(models), editorial_brief=brief_text,
        )

    def _default_editorial_brief(self) -> str:
        try:
            path = self.config.root / EDITORIAL_BRIEF_FILENAME
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text[:MAX_EDITORIAL_BRIEF_CHARS]
        except OSError:
            pass
        return DEFAULT_EDITORIAL_BRIEF_TEXT.strip()

    def _cloud_authorization_dialog(self, qwen: dict, *, aggregate: bool) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("确认使用云端优化")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        result: dict[str, str | None] = {"brief": None}

        frame = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="确认使用云端优化", style="Card.TLabel",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")
        notice = tk.Text(
            frame, height=4, wrap="word", relief="flat",
            bg=self.CARD, fg="#4d353a", font=("Microsoft YaHei UI", 9),
            padx=0, pady=8,
        )
        notice.pack(fill="x", pady=(6, 10))
        notice.insert("end", cloud_authorization_message(qwen, aggregate=aggregate))
        notice.configure(state="disabled")

        ttk.Label(
            frame,
            text="本次文档要求（仅本次生效，不会保存）",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        brief_box = tk.Text(
            frame, height=11, wrap="word", bg="#ffffff", fg=self.INK,
            font=("Microsoft YaHei UI", 9), padx=8, pady=8,
        )
        brief_box.pack(fill="both", expand=True)
        brief_box.insert("1.0", self._default_editorial_brief())
        counter = tk.StringVar(value="")
        ttk.Label(frame, textvariable=counter, style="Card.TLabel").pack(anchor="e", pady=(4, 8))

        def update_counter(_event=None) -> None:
            count = len(brief_box.get("1.0", "end-1c").strip())
            counter.set(f"{count} / {MAX_EDITORIAL_BRIEF_CHARS} 字")

        def approve() -> None:
            text = brief_box.get("1.0", "end-1c").strip()
            if not text:
                messagebox.showerror("整理要求无效", "本次整理要求不能为空", parent=dialog)
                return
            if len(text) > MAX_EDITORIAL_BRIEF_CHARS:
                messagebox.showerror(
                    "整理要求过长",
                    f"本次整理要求超过 {MAX_EDITORIAL_BRIEF_CHARS} 字符上限（当前 {len(text)} 字符）",
                    parent=dialog,
                )
                return
            result["brief"] = text
            dialog.destroy()

        def cancel() -> None:
            result["brief"] = None
            dialog.destroy()

        brief_box.bind("<KeyRelease>", update_counter)
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="取消", command=cancel).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="同意并开始", style="Accent.TButton", command=approve).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        update_counter()
        dialog.update_idletasks()
        width = min(900, max(720, dialog.winfo_reqwidth()))
        height = min(720, max(560, dialog.winfo_reqheight()))
        dialog.geometry(f"{width}x{height}")
        self.root.wait_window(dialog)
        return result["brief"]

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(UI_COPY["settings"])
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, False)
        frame = ttk.Frame(dialog, style="Card.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=UI_COPY["settings"], style="Card.TLabel", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        fields = (
            ("服务地址", self.base_url, False), ("API 密钥", self.api_key, True),
            ("云端模型顺序", self.models, False),
        )
        for row, (label, variable, secret) in enumerate(fields, start=1):
            ttk.Label(frame, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            ttk.Entry(frame, textvariable=variable, width=62, show="•" if secret else "").grid(row=row, column=1, sticky="ew", pady=5)

        speech_options = ("faster-whisper（高速）", "Qwen3-ASR（方言）")
        speech_values = {"faster-whisper（高速）": "faster-whisper", "Qwen3-ASR（方言）": "qwen3-asr-0.6b"}
        speech_labels = {v: k for k, v in speech_values.items()}
        current_speech_label = speech_labels.get(self._speech_engine.get(), "faster-whisper（高速）")
        speech_var = tk.StringVar(value=current_speech_label)
        ttk.Label(frame, text="本地语音模型", style="Card.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=5)
        speech_box = ttk.Combobox(frame, textvariable=speech_var, values=speech_options, state="readonly", width=30)
        speech_box.grid(row=4, column=1, sticky="w", pady=5)
        ttk.Label(
            frame,
            text="首选模型不可用时会自动尝试备用模型，并在结果中标注降级。",
            style="Card.TLabel",
        ).grid(row=5, column=1, sticky="w", pady=(0, 3))

        remember_var = tk.BooleanVar(value=self.remember_key.get())
        chk = tk.Checkbutton(
            frame,
            text="在本机保存 API 密钥",
            variable=remember_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.CARD, fg="#4d353a", activebackground=self.CARD, activeforeground="#4d353a",
            selectcolor=self.CARD,
        )
        chk.grid(row=6, column=1, sticky="w", pady=(8, 3))
        ttk.Label(
            frame,
            text="设置仅保存在本机；保存设置不会发起云端请求。",
            style="Card.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 12))
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=8, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=(0, 8))

        def save() -> None:
            try:
                speech_internal = speech_values.get(speech_var.get(), "faster-whisper")
                base, models, speech = validate_desktop_settings(
                    self.base_url.get(), self.models.get(), speech_internal,
                )
                save_desktop_settings(
                    self.config, base, models, speech, self.content_level.get(),
                    VISUAL_TEACHING_LEVELS[self.visual_level.get()],
                )
                if remember_var.get():
                    save_api_credentials(self.config, self.api_key.get(), base)
                self.base_url.set(base)
                self.models.set("，".join(models))
                self._speech_engine.set(speech_internal)
                self.remember_key.set(remember_var.get())
                self._set_status("设置已保存", "未发起任何云端请求")
                dialog.destroy()
            except (OSError, ValueError) as exc:
                messagebox.showerror("设置未保存", str(exc), parent=dialog)

        ttk.Button(buttons, text="保存设置", style="Accent.TButton", command=save).pack(side="left")
        frame.columnconfigure(1, weight=1)
        dialog.update_idletasks()
        dialog.geometry(f"{max(650, dialog.winfo_reqwidth())}x{dialog.winfo_reqheight()}")

    def aggregate(self) -> None:
        completed = [
            ProcessingResult.from_legacy(item.result)
            for item in self.controller.items if item.checked and item.result
        ]
        if len(completed) < 2:
            messagebox.showinfo("无法合并文档", "请至少勾选两个已完成的视频")
            return
        try:
            auth = self._cloud_authorization(aggregate=True)
        except ValueError as exc:
            messagebox.showwarning("无法合并文档", str(exc))
            return
        self._command(lambda: self.controller.aggregate(AggregateRequest(tuple(completed), auth)))

    def aggregate_local(self) -> None:
        completed = tuple(
            ProcessingResult.from_legacy(item.result)
            for item in self.controller.items if item.checked and item.result
        )
        if len(completed) < 2:
            messagebox.showinfo("无法合并文档", "请至少勾选两个已完成的视频")
            return
        self._command(lambda: self.controller.aggregate_local(completed))

    def cancel(self) -> None:
        current = self.controller.current_item
        label = f"\n\n当前视频：{current.path.name}" if current else ""
        if messagebox.askyesno("取消任务", f"确定取消当前处理任务？{label}\n队列中的源视频不会被删除。"):
            self.controller.cancel()

    def clear_selected_cache(self) -> None:
        selected = [item for item in self.controller.items if item.checked]
        if not selected:
            messagebox.showinfo("没有选择", "请先勾选视频")
            return
        if not messagebox.askyesno(
            "删除所选缓存",
            "将删除所选视频的工作区缓存和最终 Markdown、Word、PDF，"
            "同时删除由这些视频生成的合并结果。原视频始终保留。是否继续？",
        ):
            return
        self._command(self.controller.clear_selected_cache)

    def clear_workspace(self) -> None:
        workspace = self.config.path("paths", "workspace_dir").resolve()
        if not messagebox.askyesno(
            "清空全部缓存",
            f"确定清空全部中间缓存？\n\n{workspace}\n\n最终输出和原视频会保留；重新处理时需要重建缓存。",
        ):
            return
        self._command(lambda: self._set_status(
            f"已清理 {self.controller.clear_workspace()} 项缓存", "最终输出已保留",
        ))

    def _single_selected(self, *, require_result: bool = False) -> QueueItem | None:
        selected = [item for item in self.controller.items if item.checked]
        if len(selected) != 1 or (require_result and not selected[0].result):
            detail = "请只勾选一个已完成视频" if require_result else "请只勾选一个视频"
            messagebox.showinfo("请选择一个视频", detail)
            return None
        return selected[0]

    def open_artifact(self, kind: str) -> None:
        item = self._single_selected(require_result=True)
        if item is None or not item.result.get(kind):
            if item is not None:
                messagebox.showinfo("暂无文档", f"当前视频没有可用的 {kind} 文档")
            return
        self._open_path(Path(item.result[kind]))

    def open_video(self) -> None:
        item = self._single_selected()
        if item is not None:
            if item.source_kind == "url" and item.path is not None:
                self._open_path(item.path)  # 打开下载后的本地缓存文件，不跳网页
            elif item.source_kind == "url":
                messagebox.showinfo("尚未就绪", "链接视频尚未下载完成，请等待「已就绪」后再打开")
            else:
                self._open_path(item.path)

    def open_output(self) -> None:
        selected = [item for item in self.controller.items if item.checked and item.result]
        path = Path(selected[0].result["docx"]).parent if len(selected) == 1 else self.config.path("paths", "output_dir")
        self._open_path(path)

    def open_aggregate(self) -> None:
        result = self.controller.aggregate_result or self.aggregate_result
        path = Path(result.get("docx", "")) if result else Path()
        if not result or not path.is_file():
            messagebox.showinfo("暂无合并文档", "当前批次尚未生成合并文档")
            return
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror("无法打开", f"文件或目录不存在：\n{path}")
            return
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]

    def _tree_click(self, event) -> str | None:
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if row and column == "#2" and self.controller.state not in _RUNNING_STATES:
            item = self.controller.items[int(row)]
            self._command(lambda: self.controller.select(item.resolved_path, not item.checked))
            return "break"
        self._drag_row = row if row else None
        return None

    def _tree_drag(self, event) -> str | None:
        if self._drag_row is None or self.controller.state in _RUNNING_STATES:
            return None
        target = self.tree.identify_row(event.y)
        if target and target != self._drag_row:
            self.tree.selection_set(target)
            self.tree.see(target)
        return "break"

    def _tree_drop(self, event) -> str | None:
        if self._drag_row is None:
            return None
        source_index = int(self._drag_row)
        self._drag_row = None
        target_row = self.tree.identify_row(event.y)
        if not target_row:
            return None
        target_index = int(target_row)
        if source_index != target_index and self.controller.state not in _RUNNING_STATES:
            self._command(lambda: self.controller.reorder(source_index, target_index))
        return "break"

    def _drain_events(self) -> None:
        changed = False
        latest_message = ""
        latest_detail = ""
        history_notices: list[str] = []
        for event in drain_ui_events(self.controller.events):
            if event.kind == "aggregate" and event.payload:
                self.aggregate_result = dict(event.payload)
            if event.message:
                latest_message = event.message
                latest_detail = event.detail
                self.status.set(event.message)
            if event.kind == "history_restored":
                history_notices.append(f"{event.message}\n{event.detail}")
            changed = True
        if changed:
            self._append_log(latest_message, latest_detail)
            self._refresh()
        if history_notices:
            messagebox.showinfo(
                "发现历史运行记录",
                "\n\n".join(history_notices)
                + "\n\n可以直接查看历史产物；若确实需要重新开始，请先使用“清除所选缓存”，确认后再生成。",
            )
        delay = 1 if not self.controller.events.empty() else 80
        self._drain_after_id = self.root.after(delay, self._drain_events)

    def _set_status(self, message: str, detail: str = "") -> None:
        """更新状态文案并追加日志行。"""
        self.status.set(message)
        self._append_log(message, detail)

    def _append_log(self, message: str, detail: str = "") -> None:
        """追加日志行到历史，保留滚动回看能力；自动滚动到底部。"""
        lines = []
        if message:
            lines.append(message)
        if detail and detail != message:
            lines.append(detail)
        if not lines:
            return
        self.log.configure(state="normal")
        current = self.log.get("1.0", "end-1c")
        entry = "\n".join(lines)
        if current:
            self.log.insert("end", "\n" + entry)
        else:
            self.log.insert("end", entry)
        # 限制历史行数，防止无限增长
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > self._log_history_limit:
            self.log.delete("1.0", f"{line_count - self._log_history_limit}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _values(self, item: QueueItem, position: int) -> tuple[str, ...]:
        usage = item.result.get("cloud_usage") or {}
        tokens = "—" if not usage else (
            f"{int(usage.get('prompt_tokens', 0)):,} / "
            f"{int(usage.get('completion_tokens', 0)):,} / "
            f"{int(usage.get('total_tokens', 0)):,}"
        )
        stage = STAGE_LABELS.get(item.stage, item.stage)
        source_label = "链接" if item.source_kind == "url" else "本地"
        if item.source_kind == "url":
            name = item.detail_title or item.source_url
        else:
            name = item.path.name if item.path is not None else ""
        return (
            str(position), "✓" if item.checked else "☐", source_label, name, item.status, stage,
            f"{item.progress}%", format_duration(item.elapsed), tokens,
        )

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.controller.items):
            self.tree.insert("", "end", iid=str(index), values=self._values(item, index + 1))
        total = len(self.controller.items)
        checked = sum(item.checked for item in self.controller.items)
        self.selection_status.set(f"已选择 {checked} / {total} 个视频" if total else "未添加视频")
        self.select_all_button.configure(text="取消全选" if total and checked == total else "全选")
        if total:
            self.empty_panel.place_forget()
        else:
            self.empty_panel.place(relx=.5, rely=.5, anchor="center")
        active = self.controller.current_item
        self.progress["value"] = active.progress if active else (
            100 if self.controller.state == DesktopState.COMPLETED and total else 0
        )
        self._sync_controls()

    def _sync_controls(self) -> None:
        running = self.controller.state in _RUNNING_STATES
        normal = "disabled" if running else "normal"
        for button in self._buttons:
            button.configure(state=normal)
        self.content_level_box.configure(state="disabled" if running else "readonly")
        self.visual_level_box.configure(state="disabled" if running else "readonly")
        self.cancel_button.configure(state="normal" if running else "disabled")
        completed = sum(item.checked and bool(item.result) for item in self.controller.items)
        if not running:
            self.local_aggregate_button.configure(state="normal" if completed >= 2 else "disabled")
            self.aggregate_button.configure(state="normal" if completed >= 2 else "disabled")
        # 产物按钮：仅当恰好选择一个已完成视频且文件存在时显示
        single = self._single_selected_silent()
        artifact_specs = (
            ("markdown", 2),
            ("docx", 3),
            ("pdf", 4),
        )
        for kind, col in artifact_specs:
            button = self.artifact_buttons[col]
            if running:
                button.configure(state="disabled")
            elif single and single.result.get(kind) and Path(single.result[kind]).is_file():
                button.configure(state="normal")
            else:
                button.configure(state="disabled")
        # 聚合文档按钮：仅在聚合 Document v2 和目标文件完整落盘后显示
        agg_result = self.controller.aggregate_result or self.aggregate_result
        agg_button = self.artifact_buttons[-1]
        if running:
            agg_button.configure(state="disabled")
        elif agg_result and Path(agg_result.get("docx", "")).is_file():
            agg_button.configure(state="normal")
        else:
            agg_button.configure(state="disabled")

    def _single_selected_silent(self) -> QueueItem | None:
        selected = [item for item in self.controller.items if item.checked]
        if len(selected) != 1:
            return None
        return selected[0] if selected[0].result else None

    def _tick(self) -> None:
        active = self.controller.current_item
        if active is not None:
            active.update_elapsed()
            self._refresh()
        self._tick_after_id = self.root.after(1000, self._tick)

    def close(self) -> None:
        if self.controller.state in _RUNNING_STATES:
            if not messagebox.askyesno("退出", "当前任务仍在处理。退出会取消界面任务，源视频会保留。确定继续？"):
                return
            self.controller.cancel()
        for callback_id in (self._drain_after_id, self._tick_after_id):
            try:
                self.root.after_cancel(callback_id)
            except tk.TclError:
                pass
        self.root.unbind_all("<MouseWheel>")
        self.root.destroy()

    def _command(self, command) -> None:
        try:
            command()
            self._refresh()
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            messagebox.showwarning("无法执行", str(exc))
