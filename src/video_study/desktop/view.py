from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .. import __version__
from ..application.requests import AggregateRequest, CloudAuthorization, ProcessingRequest, ProcessingResult
from ..config import AppConfig
from . import STAGE_LABELS, cloud_authorization_message, format_duration, format_eta, watermark_options, blended_hex
from .controller import DesktopController
from .models import DesktopState, QueueItem
from .settings import (
    VISUAL_TEACHING_LEVELS,
    qwen_asr_ready,
    save_api_credentials,
    save_desktop_settings,
    validate_desktop_settings,
    validate_speech_models,
)


PRIMARY_UI_ACTIONS = (
    "add", "toggle_all", "move_up", "move_down", "remove", "delete_generated", "clear_cache",
    "local", "cloud", "cancel", "aggregate", "open_output", "open_video",
    "open_markdown", "open_docx", "open_pdf", "open_aggregate", "settings",
)

_RUNNING_STATES = {DesktopState.PREPARING, DesktopState.RUNNING, DesktopState.CANCELLING}


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
        self.status = tk.StringVar(value="请选择本地视频；源文件不会上传或复制")
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
        self.speech_models = tk.StringVar(value="，".join(speech))
        self.remember_key = tk.BooleanVar(value=bool(self.api_key.get()))
        self.aggregate_result: dict = {}
        self._buttons: list[ttk.Button] = []
        self._configure_window()
        self._configure_style()
        self._build()
        self.root.after(80, self._drain_events)
        self.root.after(1000, self._tick)

    def _configure_window(self) -> None:
        self.root.title(f"知影 · 视频知识工作台 {__version__}")
        try:
            self.root.iconbitmap(default=str(self.config.root / "icon" / "知影.ico"))
        except tk.TclError:
            pass
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1380, max(980, screen_width - 80))
        height = min(940, max(700, screen_height - 100))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.minsize(min(980, screen_width - 20), min(680, screen_height - 40))
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

    def _build(self) -> None:
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, bg=self.BG, highlightthickness=0)
        page_scroll = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=page_scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        page_scroll.pack(side="right", fill="y")
        outer = ttk.Frame(self.canvas, padding=(24, 18))
        self.page_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize_page)
        self.canvas.bind_all("<MouseWheel>", self._scroll_page)

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
        ttk.Label(title_box, text="知影", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="视频知识整理与溯源工作台", style="Sub.TLabel").pack(anchor="w", pady=(3, 0))
        ttk.Label(header, text=f"V{__version__} · 15 步可恢复内核", style="Chip.TLabel").pack(side="right", pady=8)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        self.add_button = ttk.Button(toolbar, text="＋ 添加视频", style="Accent.TButton", command=self.add_videos)
        self.select_all_button = ttk.Button(toolbar, text="全选", style="Soft.TButton", command=self.toggle_all)
        self.move_up_button = ttk.Button(toolbar, text="↑ 上移所选", command=self.move_selected_up)
        self.move_down_button = ttk.Button(toolbar, text="↓ 下移所选", command=self.move_selected_down)
        self.remove_button = ttk.Button(toolbar, text="移除所选", command=self.remove_selected)
        self.delete_button = ttk.Button(toolbar, text="删除所选产物", style="Danger.TButton", command=self.delete_generated)
        self.clear_cache_button = ttk.Button(toolbar, text="清理全部缓存", style="Danger.TButton", command=self.clear_workspace)
        for button, pad in (
            (self.add_button, (0, 0)), (self.select_all_button, (20, 0)),
            (self.move_up_button, (8, 0)), (self.move_down_button, (4, 0)),
            (self.remove_button, (8, 0)), (self.delete_button, (8, 0)), (self.clear_cache_button, (8, 0)),
        ):
            button.pack(side="left", padx=pad)
        ttk.Label(toolbar, textvariable=self.selection_status, style="Sub.TLabel").pack(side="right", pady=8)

        card = ttk.Frame(outer, style="Card.TFrame", padding=1)
        card.pack(fill="both", expand=True)
        columns = ("order", "check", "name", "status", "stage", "progress", "elapsed", "eta", "tokens")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", selectmode="browse", height=9)
        headings = {
            "order": "顺序", "check": "选择", "name": "视频文件", "status": "状态", "stage": "当前步骤",
            "progress": "进度", "elapsed": "已用", "eta": "预计剩余", "tokens": "Token（入 / 出 / 总）",
        }
        widths = {"order": 52, "check": 58, "name": 300, "status": 100, "stage": 135, "progress": 62, "elapsed": 65, "eta": 78, "tokens": 155}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=50, anchor="w" if column == "name" else "center", stretch=column == "name")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", lambda _event: self.open_video())
        self.empty_panel = ttk.Frame(card, style="Card.TFrame")
        ttk.Label(self.empty_panel, text="还没有视频", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).pack()
        ttk.Label(self.empty_panel, text="点击“添加视频”可一次选择多个本地视频", style="Card.TLabel").pack(pady=(5, 0))

        preferences = ttk.Frame(outer, style="Card.TFrame", padding=(14, 11))
        preferences.pack(fill="x", pady=(12, 0))
        ttk.Label(preferences, text="处理偏好", style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(0, 16))
        ttk.Label(preferences, text="内容保留量", style="Card.TLabel").pack(side="left")
        self.content_level_box = ttk.Combobox(preferences, textvariable=self.content_level, values=("精简", "推荐", "丰富"), state="readonly", width=7)
        self.content_level_box.pack(side="left", padx=(6, 18))
        ttk.Label(preferences, text="图文教学", style="Card.TLabel").pack(side="left")
        self.visual_level_box = ttk.Combobox(preferences, textvariable=self.visual_level, values=tuple(VISUAL_TEACHING_LEVELS), state="readonly", width=7)
        self.visual_level_box.pack(side="left", padx=(6, 18))
        ttk.Label(preferences, text="内容详略与视觉证据预算相互独立", style="Card.TLabel").pack(side="left")
        self.settings_button = ttk.Button(preferences, text="模型与云端设置", style="Section.TButton", command=self.open_settings)
        self.settings_button.pack(side="right")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(14, 6))
        primary = ttk.Frame(actions)
        primary.pack(fill="x")
        self.local_button = ttk.Button(primary, text="本地整理", style="Accent.TButton", command=lambda: self.start(False))
        self.cloud_button = ttk.Button(primary, text="云端优化", command=lambda: self.start(True))
        self.aggregate_button = ttk.Button(primary, text="聚合所选", style="Success.TButton", command=self.aggregate)
        self.local_button.pack(side="left")
        self.cloud_button.pack(side="left", padx=8)
        self.aggregate_button.pack(side="left")
        self.cancel_button = ttk.Button(primary, text="取消当前任务", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")

        secondary = ttk.Frame(actions)
        secondary.pack(fill="x", pady=(8, 0))
        specs = (
            ("打开产物目录", self.open_output), ("打开原视频", self.open_video),
            ("打开 Markdown", lambda: self.open_artifact("markdown")),
            ("打开 Word", lambda: self.open_artifact("docx")),
            ("打开 PDF", lambda: self.open_artifact("pdf")),
            ("打开聚合文档", self.open_aggregate),
        )
        self.artifact_buttons = []
        for column, (label, command) in enumerate(specs):
            button = ttk.Button(secondary, text=label, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            secondary.columnconfigure(column, weight=1)
            self.artifact_buttons.append(button)

        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=(5, 0))
        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(6, 0))
        ttk.Label(status_row, textvariable=self.status, style="Sub.TLabel").pack(side="left", fill="x", expand=True)
        self.log = tk.Text(
            outer, height=3, wrap="word", relief="flat", bg=self.BG, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9), padx=0, pady=4,
        )
        self.log.pack(fill="x")
        self.log.insert("end", "等待任务。真实云端请求只会在展示数据、端点、模型链和预算并获得授权后发起。")
        self.log.configure(state="disabled")
        self._buttons = [
            self.add_button, self.select_all_button, self.move_up_button, self.move_down_button,
            self.remove_button, self.delete_button,
            self.clear_cache_button, self.settings_button, self.local_button, self.cloud_button,
            self.aggregate_button,
        ]
        self._refresh()

    def _resize_page(self, event) -> None:
        width = max(780, event.width)
        self.canvas.itemconfigure(self.page_window, width=width)

    def _scroll_page(self, event) -> str | None:
        if event.widget is self.tree:
            return None
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def add_videos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择一个或多个视频",
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi"), ("所有文件", "*.*")],
        )
        if paths:
            self._command(lambda: self.controller.add([Path(path) for path in paths]))

    def remove_selected(self) -> None:
        self._command(self.controller.remove_selected)

    def toggle_all(self) -> None:
        self._command(self.controller.toggle_all)

    def move_selected_up(self) -> None:
        self._command(self.controller.move_selected_up)

    def move_selected_down(self) -> None:
        self._command(self.controller.move_selected_down)

    def start(self, use_cloud: bool) -> None:
        try:
            speech = validate_speech_models(self.speech_models.get())
            cloud = self._cloud_authorization(aggregate=False) if use_cloud else None
        except ValueError as exc:
            messagebox.showerror("设置无效", str(exc))
            return
        self._command(lambda: self.controller.start(lambda path: ProcessingRequest(
            path,
            content_level=self.content_level.get(),
            visual_level=VISUAL_TEACHING_LEVELS[self.visual_level.get()],
            speech_models=tuple(speech),
            cloud=cloud,
        )))

    def _cloud_authorization(self, *, aggregate: bool) -> CloudAuthorization:
        base, models, _speech = validate_desktop_settings(
            self.base_url.get(), self.models.get(), self.speech_models.get(),
        )
        qwen = {
            **self.config.raw.get("qwen", {}),
            "_runtime_base_url": base,
            "_runtime_models": models,
        }
        if not messagebox.askyesno(
            "云端请求授权",
            cloud_authorization_message(qwen, aggregate=aggregate),
            parent=self.root,
        ):
            raise ValueError("未授权云端处理")
        return self.cloud_resolver(api_key=self.api_key.get(), base_url=base, models=tuple(models))

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("模型与云端设置")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, False)
        frame = ttk.Frame(dialog, style="Card.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="模型与云端设置", style="Card.TLabel", font=("Microsoft YaHei UI", 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        fields = (
            ("API URL", self.base_url, False), ("API Key", self.api_key, True),
            ("语言模型链", self.models, False), ("语音模型链", self.speech_models, False),
        )
        for row, (label, variable, secret) in enumerate(fields, start=1):
            ttk.Label(frame, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            ttk.Entry(frame, textvariable=variable, width=62, show="•" if secret else "").grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(frame, text="记住 API Key（仅写入本机 .env）", variable=self.remember_key).grid(row=5, column=1, sticky="w", pady=(8, 3))
        ttk.Label(
            frame,
            text="保存只更新本地配置，不会自动联网测试；每次真实云请求仍需单独授权。",
            style="Card.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 12))
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left", padx=(0, 8))

        def save() -> None:
            try:
                base, models, speech = validate_desktop_settings(
                    self.base_url.get(), self.models.get(), self.speech_models.get(),
                )
                save_desktop_settings(
                    self.config, base, models, speech, self.content_level.get(),
                    VISUAL_TEACHING_LEVELS[self.visual_level.get()],
                )
                if self.remember_key.get():
                    save_api_credentials(self.config, self.api_key.get(), base)
                self.base_url.set(base)
                self.models.set("，".join(models))
                self.speech_models.set("，".join(speech))
                self.status.set("设置已保存；未发起任何云端请求")
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
            messagebox.showinfo("无法聚合", "请至少勾选两个已完成视频")
            return
        try:
            auth = self._cloud_authorization(aggregate=True)
        except ValueError as exc:
            messagebox.showwarning("无法聚合", str(exc))
            return
        self._command(lambda: self.controller.aggregate(AggregateRequest(tuple(completed), auth)))

    def cancel(self) -> None:
        current = self.controller.current_item
        label = f"\n\n当前视频：{current.path.name}" if current else ""
        if messagebox.askyesno("取消任务", f"确定取消当前处理任务？{label}\n队列中的源视频不会被删除。"):
            self.controller.cancel()

    def delete_generated(self) -> None:
        selected = [item for item in self.controller.items if item.checked]
        if not selected:
            messagebox.showinfo("没有选择", "请先勾选视频")
            return
        if not messagebox.askyesno(
            "删除生成文件",
            "将删除所选视频的工作区缓存和最终 Markdown、Word、PDF。原视频始终保留。是否继续？",
        ):
            return
        self._command(self.controller.delete_selected)

    def clear_workspace(self) -> None:
        workspace = self.config.path("paths", "workspace_dir").resolve()
        if not messagebox.askyesno(
            "清理全部缓存",
            f"确定清空全部中间缓存？\n\n{workspace}\n\n最终输出和原视频会保留；重新处理时需要重建缓存。",
        ):
            return
        self._command(lambda: self.status.set(f"已清理 {self.controller.clear_workspace()} 项缓存；最终输出已保留"))

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
                messagebox.showinfo("暂无产物", f"当前视频没有可用的 {kind} 产物")
            return
        self._open_path(Path(item.result[kind]))

    def open_video(self) -> None:
        item = self._single_selected()
        if item is not None:
            self._open_path(item.path)

    def open_output(self) -> None:
        selected = [item for item in self.controller.items if item.checked and item.result]
        path = Path(selected[0].result["docx"]).parent if len(selected) == 1 else self.config.path("paths", "output_dir")
        self._open_path(path)

    def open_aggregate(self) -> None:
        result = self.controller.aggregate_result or self.aggregate_result
        path = Path(result.get("docx", "")) if result else Path()
        if not result or not path.is_file():
            messagebox.showinfo("暂无聚合文档", "当前批次尚未生成聚合文档")
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
            self._command(lambda: self.controller.select(item.path, not item.checked))
            return "break"
        return None

    def _drain_events(self) -> None:
        changed = False
        while not self.controller.events.empty():
            event = self.controller.events.get_nowait()
            if event.kind == "aggregate" and event.payload:
                self.aggregate_result = dict(event.payload)
            if event.message:
                self.status.set(event.message)
                self._append_log(event.message)
            changed = True
        if changed:
            self._refresh()
        self.root.after(80, self._drain_events)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        rows = (self.log.get("1.0", "end-1c").splitlines() + [message])[-4:]
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(rows))
        self.log.configure(state="disabled")

    def _values(self, item: QueueItem, position: int) -> tuple[str, ...]:
        usage = item.result.get("cloud_usage") or {}
        tokens = "—" if not usage else (
            f"{int(usage.get('prompt_tokens', 0)):,} / "
            f"{int(usage.get('completion_tokens', 0)):,} / "
            f"{int(usage.get('total_tokens', 0)):,}"
        )
        stage = STAGE_LABELS.get(item.stage, item.stage)
        return (
            str(position), "☑" if item.checked else "☐", item.path.name, item.status, stage,
            f"{item.progress}%", format_duration(item.elapsed),
            format_eta(item.eta, item.estimating), tokens,
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
            self.aggregate_button.configure(state="normal" if completed >= 2 else "disabled")
        self.artifact_buttons[-1].configure(
            state="normal" if (self.controller.aggregate_result or self.aggregate_result) else "disabled",
        )

    def _tick(self) -> None:
        active = self.controller.current_item
        if active is not None:
            active.update_elapsed()
            self._refresh()
        self.root.after(1000, self._tick)

    def close(self) -> None:
        if self.controller.state in _RUNNING_STATES:
            if not messagebox.askyesno("退出", "当前任务仍在处理。退出会取消界面任务，源视频会保留。确定继续？"):
                return
            self.controller.cancel()
        self.root.destroy()

    def _command(self, command) -> None:
        try:
            command()
            self._refresh()
        except (KeyError, OSError, ValueError, RuntimeError) as exc:
            messagebox.showwarning("无法执行", str(exc))
