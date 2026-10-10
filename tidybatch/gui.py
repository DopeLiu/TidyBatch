# -*- coding: utf-8 -*-
"""tkinter 图形界面。

职责边界:只做「展示 + 交互」,不含任何文件操作逻辑,所有实际变更都委托给 core.Operation。

界面结构
--------
┌──────────────────────────────────────────────────────┐
│ 标题栏                                                │
├────────────┬─────────────────────────────────────────┤
│ 功能侧边栏  │ 参数表单(按 Operation.fields 自动生成) │
│ (按分类分组)├─────────────────────────────────────────┤
│            │ 预览结果表(源 → 目标)                  │
├────────────┴─────────────────────────────────────────┤
│ 操作日志(带时间戳,分级着色)                         │
├──────────────────────────────────────────────────────┤
│ 预览计划 / 执行 / 中止   进度条            状态提示   │
└──────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import operations as _operations  # noqa: F401 —— 导入即触发全部 @register
from .core import REGISTRY, Field, Operation, categories

# --------------------------------------------------------------------------- #
# 配色(深色主题)
# --------------------------------------------------------------------------- #
C = {
    "bg": "#1b1d22",
    "panel": "#22252c",
    "panel2": "#2c3038",
    "field": "#2f343d",
    "fg": "#eef0f5",
    "muted": "#a8aebc",
    "border": "#3f4552",
    "accent": "#5b8cff",
    "accent_dark": "#3f6ad0",
    "accent_dim": "#33457a",
    "ok": "#57e08d",
    "warn": "#fcc44d",
    "error": "#fa8181",
    "info": "#b3bccb",
    "danger": "#c8503f",
    "danger_dark": "#a03a2c",
}

# --------------------------------------------------------------------------- #
# 字体与尺寸
# --------------------------------------------------------------------------- #
# 文字发虚的两个根因:
#   1) Tk 在 Windows 上请求的是「灰度抗锯齿」字体,而非原生应用的 ClearType 次像素抗锯齿;
#   2) 用点值字号时,tk scaling(1.333)会把 10pt 折算成 13.33px 这种非整数像素大小,
#      字形无法对齐像素网格,进一步发虚。
# 对策:字号一律使用「负值 = 像素单位」并取整数,再配合更高的文字对比度。
#
# 下面这些量在 App 启动时按真实 DPI 计算(见 _init_metrics)。
SCALE = 1.0
FONT = ("Microsoft YaHei UI", -15)          # 正文字体
FONT_SMALL = ("Microsoft YaHei UI", -13)    # 提示 / 次要文字
FONT_MONO = ("Consolas", -14)               # 日志
FONT_TITLE = ("Microsoft YaHei UI", -21, "bold")
FONT_SECTION = ("Microsoft YaHei UI", -17, "bold")


def S(px: float) -> int:
    """按 DPI 缩放像素尺寸(内边距、宽度、行高等)。"""
    return max(1, int(round(px * SCALE)))


def _enable_dpi_awareness():
    """声明进程 DPI 感知。

    必须在创建 Tk 根窗口之前调用:否则在非 100% 缩放的显示器上,
    Windows 会把整个窗口做位图拉伸,文字会明显模糊。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 2 = PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001 —— 老系统没有 shcore
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _init_metrics():
    """按当前显示器 DPI 计算全局缩放比与字体。"""
    global SCALE, FONT, FONT_SMALL, FONT_MONO, FONT_TITLE, FONT_SECTION

    SCALE = 1.0
    if sys.platform == "win32":
        try:
            import ctypes

            dpi = ctypes.windll.user32.GetDpiForSystem()
            if dpi:
                SCALE = max(1.0, dpi / 96.0)
        except Exception:  # noqa: BLE001
            SCALE = 1.0

    def px(n):
        # 负值表示像素单位;取整避免字形落在非整数像素上
        return -max(10, int(round(n * SCALE)))

    FONT = ("Microsoft YaHei UI", px(15))
    FONT_SMALL = ("Microsoft YaHei UI", px(13))
    FONT_MONO = ("Consolas", px(14))
    FONT_TITLE = ("Microsoft YaHei UI", px(21), "bold")
    FONT_SECTION = ("Microsoft YaHei UI", px(17), "bold")

# --------------------------------------------------------------------------- #
# 路径解析 —— 区分「只读资源」与「可写数据」,保证打包成 exe 后行为一致
# --------------------------------------------------------------------------- #
def _is_frozen() -> bool:
    """是否运行在 PyInstaller 等打包器生成的独立可执行文件中。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """只读资源目录(图标等)。

    - 源码运行:项目根目录;
    - 打包运行:PyInstaller 的解包目录(sys._MEIPASS)。资源经 --add-data 打入,
      该目录随时可能被清理,因此只用于读取。
    """
    if _is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """可写数据目录(配置、日志等)。

    - 源码运行:项目根目录;
    - 打包运行(Windows):exe 所在目录 —— 绿色便携,配置随程序走,不会因
      单文件模式的临时解包目录被清理而丢失;
    - 打包运行(macOS):~/Library/Application Support/TidyBatch —— 不能写进
      .app 包内部(未签名应用可能被系统从只读的随机路径启动,配置会丢失)。
    """
    if _is_frozen():
        if sys.platform == "darwin":
            path = os.path.join(
                os.path.expanduser("~"),
                "Library", "Application Support", "TidyBatch",
            )
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                return os.path.dirname(os.path.abspath(sys.executable))
            return path
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SETTINGS_PATH = os.path.join(data_dir(), ".tidybatch_settings.json")


# --------------------------------------------------------------------------- #
# 小工具:可着色的扁平按钮
# --------------------------------------------------------------------------- #
class FlatButton(tk.Button):
    def __init__(self, parent, text, command, bg=None, fg=None, hover=None, width=None):
        self._bg = bg or C["panel2"]
        self._fg = fg or C["fg"]
        self._hover = hover or self._blend(self._bg)
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=self._bg,
            fg=self._fg,
            activebackground=self._hover,
            activeforeground=self._fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=FONT,
            padx=S(16),
            pady=S(8),
            width=width,
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    @staticmethod
    def _blend(hex_color):
        """简单提亮,作为 hover 底色。"""
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = (min(255, int(v * 1.22 + 12)) for v in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_enter(self, _event):
        if self["state"] != "disabled":
            self.configure(bg=self._hover)

    def _on_leave(self, _event):
        if self["state"] != "disabled":
            self.configure(bg=self._bg)

    def set_colors(self, bg, hover=None, fg=None):
        """切换按钮配色(如危险操作切换为警示色)。"""
        self._bg = bg
        self._hover = hover or self._blend(bg)
        if fg:
            self._fg = fg
        self.configure(bg=self._bg, fg=self._fg, activebackground=self._hover, activeforeground=self._fg)

    def set_enabled(self, enabled: bool):
        if enabled:
            self.configure(state="normal", bg=self._bg, fg=self._fg, cursor="hand2")
        else:
            self.configure(state="disabled", bg=C["panel2"], fg=C["muted"], cursor="arrow")


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        # 必须在创建根窗口之前声明 DPI 感知,否则高缩放屏幕上文字会被位图拉伸
        _enable_dpi_awareness()
        _init_metrics()

        super().__init__()
        self.title(f"TidyBatch_v{__version__}")
        # 窗口尺寸按屏幕与 DPI 自适应:小屏也能完整显示,大屏更宽松
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = int(min(1180 * SCALE, max(1000 * SCALE, screen_w - 120 * SCALE)))
        height = int(min(920 * SCALE, max(700 * SCALE, screen_h - 130 * SCALE)))
        self.geometry(f"{width}x{height}")
        self.minsize(S(1000), S(700))
        self.configure(bg=C["bg"])

        self._icon_image = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._stop_flag = threading.Event()
        self._busy = False
        self._current: Operation | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._planned: list = []
        self._memory: dict[str, dict] = {}      # 各功能的参数记忆

        self._init_style()
        self._build_ui()
        self._set_window_icon()
        self._load_settings()
        self._select_first()
        self.after(80, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 窗口图标
    def _set_window_icon(self):
        """加载窗口图标。

        查找顺序(相对项目根目录):
            assets/icon.ico → icon.ico → assets/icon.png → icon.png
        都找不到时不设置图标(显示 tkinter 默认图标)。想换成自己的图标,
            直接把图标文件放到上述任一位置即可(推荐 .ico,多尺寸更清晰)。
        """
        root_dir = resource_dir()

        for relative in (os.path.join("assets", "icon.ico"), "icon.ico"):
            path = os.path.join(root_dir, relative)
            if os.path.isfile(path):
                try:
                    self.iconbitmap(path)
                    # 同时设为默认图标,后续新建的弹窗也会沿用
                    self.iconbitmap(default=path)
                    return
                except tk.TclError:
                    pass

        for relative in (os.path.join("assets", "icon.png"), "icon.png"):
            path = os.path.join(root_dir, relative)
            if os.path.isfile(path):
                try:
                    self._icon_image = tk.PhotoImage(file=path)
                    self.iconphoto(True, self._icon_image)
                    return
                except tk.TclError:
                    pass

    # ------------------------------------------------------------------ 样式
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=C["panel"],
            fieldbackground=C["panel"],
            foreground=C["fg"],
            borderwidth=0,
            rowheight=S(30),
            font=FONT,
        )
        # clam 主题的 Treeview / Combobox 默认带浅色描边,与深色主题冲突,统一抹平
        style.configure(
            "Treeview",
            bordercolor=C["panel"],
            lightcolor=C["panel"],
            darkcolor=C["panel"],
        )
        style.map(
            "Treeview",
            background=[("selected", C["accent_dim"])],
            foreground=[("selected", C["fg"])],
        )
        style.configure("Treeview.Heading", background=C["panel2"], foreground=C["muted"], borderwidth=0, font=FONT_SMALL)
        style.configure(
            "TCombobox",
            fieldbackground=C["field"],
            background=C["field"],
            foreground=C["fg"],
            borderwidth=0,
            arrowcolor=C["fg"],
            bordercolor=C["field"],
            lightcolor=C["field"],
            darkcolor=C["field"],
        )
        style.map("TCombobox", fieldbackground=[("readonly", C["field"])], foreground=[("readonly", C["fg"])])
        style.configure("TScrollbar", background=C["panel2"], troughcolor=C["bg"], borderwidth=0, arrowcolor=C["muted"])
        style.configure("TSeparator", background=C["border"])
        style.configure(
            "TProgressbar",
            background=C["accent"],
            troughcolor=C["panel2"],
            borderwidth=0,
            thickness=S(7),
        )
        # Combobox 下拉列表(原生 Listbox,需走 option database)
        self.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.option_add("*TCombobox*Listbox.foreground", C["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["accent_dim"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["fg"])
        self.option_add("*TCombobox*Listbox.font", FONT)

    # ------------------------------------------------------------------ 布局
    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()

        # 上下可拖动的分栏:上部工作区,下部日志
        paned = tk.PanedWindow(self, orient="vertical", bg=C["bg"], sashwidth=S(6), bd=0, sashrelief="flat")
        paned.grid(row=1, column=0, sticky="nsew", padx=S(12), pady=(0, S(8)))
        self._paned = paned

        work = tk.Frame(paned, bg=C["bg"])
        work.grid_rowconfigure(0, weight=1)
        work.grid_columnconfigure(1, weight=1)
        work.grid_columnconfigure(0, minsize=S(250))
        paned.add(work, minsize=S(300), stretch="always")

        self._build_sidebar(work)
        self._build_workspace(work)
        self._build_log(paned)

        self._build_footer()

        # 首帧后把分隔条下移,让工作区占约 2/3 高度
        self.after(120, self._place_sash)

    def _place_sash(self):
        try:
            total = self._paned.winfo_height()
            if total > 300:
                self._paned.sash_place(0, 0, int(total * 0.72))
        except tk.TclError:
            pass

    def _build_header(self):
        header = tk.Frame(self, bg=C["bg"])
        header.grid(row=0, column=0, sticky="ew", padx=S(12), pady=(S(12), S(8)))

        tk.Label(header, text="TidyBatch · 批量文件工具箱", bg=C["bg"], fg=C["fg"], font=FONT_TITLE).pack(side="left")
        tk.Label(
            header,
            text=f"  Version: v{__version__}  Author: DopeLiu",
            bg=C["bg"],
            fg=C["muted"],
            font=FONT_SMALL,
        ).pack(side="left", pady=(S(7), 0))

    def _build_sidebar(self, parent):
        wrap = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
        wrap.grid(row=0, column=0, sticky="nsew", padx=(0, S(10)))
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        tk.Label(wrap, text="功能列表", bg=C["panel"], fg=C["muted"], font=FONT_SMALL, anchor="w", padx=S(14), pady=S(10)).grid(
            row=0, column=0, sticky="ew"
        )

        self.ops_tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.ops_tree.grid(row=1, column=0, sticky="nsew", padx=S(8), pady=(0, S(10)))
        self.ops_tree.tag_configure("category", foreground=C["muted"])

        for category, ops in categories().items():
            parent_id = self.ops_tree.insert("", "end", iid=f"cat::{category}", text=f"  {category}", open=True, tags=("category",))
            for op in ops:
                self.ops_tree.insert(parent_id, "end", iid=op.key, text=op.name)
        self.ops_tree.bind("<<TreeviewSelect>>", self._on_op_select)

    def _build_workspace(self, parent):
        wrap = tk.Frame(parent, bg=C["bg"])
        wrap.grid(row=0, column=1, sticky="nsew")
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        # --- 参数表单 ---
        # 外层容器固定上限高度,内部用 Canvas 承载,参数过多时出现滚动条,
        # 保证预览区永远不会被参数区挤没(为后续增加字段预留)。
        form_outer = tk.Frame(wrap, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"], height=S(120))
        form_outer.grid(row=0, column=0, sticky="ew")
        form_outer.grid_propagate(False)
        form_outer.grid_rowconfigure(0, weight=1)
        form_outer.grid_columnconfigure(0, weight=1)
        self._form_outer = form_outer
        self._form_limit = S(400)

        canvas = tk.Canvas(form_outer, bg=C["panel"], highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        self._form_canvas = canvas

        self._form_scroll = ttk.Scrollbar(form_outer, orient="vertical", command=canvas.yview)
        self._form_scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=self._form_scroll.set)

        self.form_card = tk.Frame(canvas, bg=C["panel"])
        self.form_card.grid_columnconfigure(0, weight=1)
        self._form_window = canvas.create_window((0, 0), window=self.form_card, anchor="nw")

        self.op_title = tk.Label(self.form_card, text="", bg=C["panel"], fg=C["fg"], font=FONT_SECTION, anchor="w")
        self.op_title.grid(row=0, column=0, sticky="ew", padx=S(16), pady=(S(12), S(3)))

        self.op_desc = tk.Label(
            self.form_card, text="", bg=C["panel"], fg=C["muted"], font=FONT_SMALL, anchor="w", justify="left", wraplength=S(760)
        )
        self.op_desc.grid(row=1, column=0, sticky="ew", padx=S(16), pady=(0, S(9)))

        self.form_body = tk.Frame(self.form_card, bg=C["panel"])
        self.form_body.grid(row=2, column=0, sticky="ew", padx=S(16), pady=(0, S(10)))
        self.form_body.grid_columnconfigure(0, weight=1)

        def _sync(_event=None):
            # 说明文字随窗口宽度自动换行
            wrap_width = max(S(240), canvas.winfo_width() - S(40))
            if int(self.op_desc.cget("wraplength")) != wrap_width:
                self.op_desc.configure(wraplength=wrap_width)

            canvas.configure(scrollregion=canvas.bbox("all"))
            needed = self.form_card.winfo_reqheight()
            form_outer.configure(height=min(needed, self._form_limit))
            # 留 4px 容差,避免因像素舍入误判为需要滚动
            overflow = needed > canvas.winfo_height() + 4
            if overflow and not self._form_scroll.winfo_ismapped():
                self._form_scroll.grid()
            elif not overflow and self._form_scroll.winfo_ismapped():
                self._form_scroll.grid_remove()

        def _on_wheel(event):
            if self.form_card.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        self.form_card.bind("<Configure>", _sync)
        canvas.bind(
            "<Configure>",
            lambda e: (canvas.itemconfigure(self._form_window, width=e.width), _sync()),
        )
        form_outer.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", _on_wheel))
        form_outer.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))
        self._sync_form = _sync

        # --- 预览结果 ---
        preview_card = tk.Frame(wrap, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
        preview_card.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        preview_card.grid_rowconfigure(1, weight=1)
        preview_card.grid_columnconfigure(0, weight=1)

        bar = tk.Frame(preview_card, bg=C["panel"])
        bar.grid(row=0, column=0, sticky="ew", padx=S(14), pady=(S(10), S(6)))
        tk.Label(bar, text="变更预览", bg=C["panel"], fg=C["muted"], font=FONT_SMALL).pack(side="left")
        self.preview_count = tk.Label(bar, text="尚未预览", bg=C["panel"], fg=C["info"], font=FONT_SMALL)
        self.preview_count.pack(side="right")

        columns = ("verb", "src", "dst")
        self.preview = ttk.Treeview(preview_card, columns=columns, show="headings", selectmode="browse")
        self.preview.heading("verb", text="操作")
        self.preview.heading("src", text="源文件")
        self.preview.heading("dst", text="目标 / 结果")
        self.preview.column("verb", width=S(84), anchor="center", stretch=False)
        self.preview.column("src", width=S(380), anchor="w")
        self.preview.column("dst", width=S(380), anchor="w")
        self.preview.grid(row=1, column=0, sticky="nsew", padx=(S(14), 0), pady=(0, S(6)))

        pv_scroll = ttk.Scrollbar(preview_card, orient="vertical", command=self.preview.yview)
        pv_scroll.grid(row=1, column=1, sticky="ns", padx=(0, S(14)), pady=(0, S(6)))
        self.preview.configure(yscrollcommand=pv_scroll.set)

        self.preview.tag_configure("danger", foreground=C["error"])

    def _build_log(self, paned):
        wrap = tk.Frame(paned, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        paned.add(wrap, minsize=140, stretch="always")

        bar = tk.Frame(wrap, bg=C["panel"])
        bar.grid(row=0, column=0, sticky="ew", padx=S(14), pady=(S(10), S(4)))
        tk.Label(bar, text="操作日志", bg=C["panel"], fg=C["muted"], font=FONT_SMALL).pack(side="left")
        FlatButton(bar, "保存日志", self._save_log).pack(side="right", padx=(S(6), 0))
        FlatButton(bar, "清空日志", self._clear_log).pack(side="right")

        self.log = tk.Text(
            wrap,
            bg=C["panel"],
            fg=C["fg"],
            font=FONT_MONO,
            relief="flat",
            bd=0,
            wrap="none",
            height=8,
            padx=S(12),
            pady=S(6),
            insertbackground=C["fg"],
            state="disabled",
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=(S(14), 0), pady=(0, S(10)))

        log_scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=1, column=1, sticky="ns", padx=(0, S(14)), pady=(0, S(10)))
        self.log.configure(yscrollcommand=log_scroll.set)

        self.log.tag_configure("info", foreground=C["info"])
        self.log.tag_configure("ok", foreground=C["ok"])
        self.log.tag_configure("warn", foreground=C["warn"])
        self.log.tag_configure("error", foreground=C["error"])
        self.log.tag_configure("time", foreground=C["muted"])

    def _build_footer(self):
        footer = tk.Frame(self, bg=C["bg"])
        footer.grid(row=2, column=0, sticky="ew", padx=S(12), pady=(0, S(12)))
        footer.grid_columnconfigure(2, weight=1)

        self.preview_btn = FlatButton(footer, "预览计划", self._on_preview, bg=C["panel2"])
        self.preview_btn.grid(row=0, column=0, padx=(0, S(8)))

        self.run_btn = FlatButton(footer, "执行", self._on_execute, bg=C["accent"], fg="#ffffff", hover=C["accent_dark"])
        self.run_btn.grid(row=0, column=1)

        self.stop_btn = FlatButton(footer, "中止", self._on_stop, bg=C["panel2"])
        self.stop_btn.grid(row=0, column=2, padx=(S(8), 0), sticky="w")
        self.stop_btn.set_enabled(False)

        # 确定性进度条:默认停在 0(无任何填充),执行时按真实进度推进
        self.progress = ttk.Progressbar(footer, mode="determinate", length=S(200), maximum=100, value=0)
        self.progress.grid(row=0, column=3, padx=(0, S(14)))

        self.status = tk.Label(footer, text="就绪", bg=C["bg"], fg=C["muted"], font=FONT_SMALL)
        self.status.grid(row=0, column=4, sticky="e")

    # ------------------------------------------------------------------ 表单
    def _select_first(self):
        """优先恢复上次使用的功能,否则默认选中第一个。"""
        if not self.ops_tree.selection():
            first = next(iter(REGISTRY.values()), None)
            if first:
                self.ops_tree.selection_set(first.key)
                self.ops_tree.focus(first.key)
        if self._current is None:
            self._on_op_select()

    def _on_op_select(self, _event=None):
        selection = self.ops_tree.selection()
        if not selection:
            return
        key = selection[0]
        op = REGISTRY.get(key)
        if op is None:          # 点到分类节点,忽略
            return
        if self._current is not None:      # 记住上一个功能的输入
            self._memory[self._current.key] = self._collect()
        self._current = op
        self._render_form(op)
        self._clear_preview()
        self.status.configure(text=f"当前功能:{op.name}")

    def _render_form(self, op: Operation):
        for child in self.form_body.winfo_children():
            child.destroy()
        self._vars = {}

        self.op_title.configure(text=op.name)
        self.op_desc.configure(text=op.description)

        saved = self._memory.get(op.key, {})
        row = 0
        for field in op.fields:
            if field.kind == "check":
                continue
            self._render_field(field, saved.get(field.key, field.default), row)
            row += 1

        # 勾选项统一排在最后一行,横向排列
        checks = [f for f in op.fields if f.kind == "check"]
        if checks:
            holder = tk.Frame(self.form_body, bg=C["panel"])
            holder.grid(row=row, column=0, sticky="w", pady=(S(4), 0))
            for field in checks:
                default_on = str(saved.get(field.key, field.default)).lower() in ("1", "true", "yes", "on")
                var = tk.BooleanVar(value=default_on)
                tk.Checkbutton(
                    holder,
                    text=field.label,
                    variable=var,
                    bg=C["panel"],
                    fg=C["fg"],
                    selectcolor=C["panel2"],
                    activebackground=C["panel"],
                    activeforeground=C["fg"],
                    font=FONT,
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2",
                ).pack(side="left", padx=(0, S(18)))
                self._vars[field.key] = var

        # 危险操作:执行按钮换成警示色
        if op.destructive:
            self.run_btn.configure(text="执行(危险操作)")
            self.run_btn.set_colors(C["danger"], C["danger_dark"], "#ffffff")
        else:
            self.run_btn.configure(text="执行")
            self.run_btn.set_colors(C["accent"], C["accent_dark"], "#ffffff")

        # 表单重建后回到顶部,并重新计算参数区高度
        self._form_canvas.yview_moveto(0)
        self.after(10, self._sync_form)

    def _render_field(self, field: Field, value: str, row: int):
        """每个字段渲染为一个独立分组(标签 + 控件 + 可选提示),避免行号错位。"""
        group = tk.Frame(self.form_body, bg=C["panel"])
        group.grid(row=row, column=0, sticky="ew", pady=(0, S(8)))
        group.grid_columnconfigure(1, weight=1)

        tk.Label(group, text=field.label, bg=C["panel"], fg=C["fg"], font=FONT, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, S(12))
        )

        if field.kind == "choice":
            current = value or field.default or (field.choices[0] if field.choices else "")
            var = tk.StringVar(value=current)
            ttk.Combobox(
                group, textvariable=var, values=list(field.choices), state="readonly", font=FONT
            ).grid(row=0, column=1, sticky="ew")
            self._vars[field.key] = var
        else:
            var = tk.StringVar(value=value)
            tk.Entry(
                group,
                textvariable=var,
                bg=C["field"],
                fg=C["fg"],
                font=FONT,
                relief="flat",
                insertbackground=C["fg"],
                highlightthickness=1,
                highlightbackground=C["border"],
                highlightcolor=C["accent"],
            ).grid(row=0, column=1, sticky="ew", ipady=S(5))
            self._vars[field.key] = var
            if field.kind == "dir":
                FlatButton(group, "浏览…", lambda v=var: self._pick_dir(v)).grid(row=0, column=2, padx=(S(8), 0))

        if field.hint:
            tk.Label(group, text=field.hint, bg=C["panel"], fg=C["muted"], font=FONT_SMALL, anchor="w").grid(
                row=1, column=1, sticky="w", pady=(S(4), 0)
            )

    def _pick_dir(self, var: tk.StringVar):
        initial = var.get() if os.path.isdir(var.get() or "") else os.path.expanduser("~")
        chosen = filedialog.askdirectory(title="选择目录", initialdir=initial)
        if chosen:
            var.set(os.path.normpath(chosen))

    def _collect(self) -> dict:
        if self._current is None:
            return {}
        params = {}
        for field in self._current.fields:
            var = self._vars.get(field.key)
            if var is None:
                params[field.key] = field.default
            elif field.kind == "check":
                params[field.key] = "1" if var.get() else ""
            else:
                params[field.key] = str(var.get()).strip()
        return params

    # ------------------------------------------------------------------ 预览 / 执行
    def _on_preview(self):
        self._start_task(dry_run=True)

    def _on_execute(self):
        if self._current is None:
            return
        if not self._planned:
            self._append_log("warn", "尚未预览,请先点击「预览计划」确认将要变更的文件。")
            return

        total = len(self._planned)
        lines = [f"即将处理 {total} 项文件。", "", "确认执行吗?"]
        if self._current.destructive:
            mode = self._collect().get("mode", "")
            lines.insert(0, "⚠  这是一个危险操作,执行后可能无法恢复。")
            lines.insert(1, f"删除方式:{mode}")
            lines.insert(2, "")
        if not messagebox.askyesno("执行确认", "\n".join(lines), parent=self, icon="warning"):
            self._append_log("info", "用户取消了执行。")
            return
        self._start_task(dry_run=False)

    def _start_task(self, dry_run: bool):
        if self._busy or self._current is None:
            return
        op = self._current
        params = self._collect()
        self._memory[op.key] = params

        self._busy = True
        self._stop_flag.clear()
        if dry_run:
            # 扫描阶段无法预知总数,用不确定进度条表示"正在工作"
            self._progress_scanning()
        else:
            # 执行阶段已知总数,用确定性进度条从 0 推进
            self._progress_reset(len(self._planned))
        self._set_running(True)

        if dry_run:
            self._clear_preview()
            self._append_log("info", f"开始预览:{op.name}")
        else:
            self._append_log("info", f"开始执行:{op.name}")

        def worker():
            def log(level, message):
                self._queue.put(("log", (level, message)))

            def progress(done, total):
                self._queue.put(("progress", (done, total)))

            try:
                actions = op.run(
                    params,
                    log,
                    dry_run=dry_run,
                    should_stop=self._stop_flag.is_set,
                    on_progress=progress,
                )
                self._queue.put(("plan", (dry_run, actions)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("log", ("error", f"操作失败:{exc}")))
                self._queue.put(("plan", (dry_run, [])))
            finally:
                self._queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ 进度条
    # 注意:ttk 的 Progressbar.stop() 会把 value 复位为 0(即使处于确定态),
    # 所以只在"确实需要停止扫描动画"时才调用它,否则会把已完成的进度清空。
    def _set_progress_mode(self, mode: str):
        current = str(self.progress.cget("mode"))
        if current == mode:
            return
        if current == "indeterminate":
            self.progress.stop()
        self.progress.configure(mode=mode)

    def _progress_scanning(self):
        """扫描/预览:不确定总量,显示来回移动的动画。"""
        self._set_progress_mode("indeterminate")
        self.progress.configure(value=0, maximum=100)
        self.progress.start(14)

    def _progress_reset(self, total=None):
        """执行:确定性进度条,从 0 开始,最大值为待处理总数。"""
        self._set_progress_mode("determinate")
        self.progress.configure(maximum=max(1, total or 100), value=0)

    def _progress_update(self, done, total):
        self._set_progress_mode("determinate")
        total = max(1, total)
        self.progress.configure(maximum=total, value=done)
        self.status.configure(text=f"执行中 {done} / {total}  ({int(done * 100 / total)}%)")

    def _on_stop(self):
        if self._busy:
            self._stop_flag.set()
            self._append_log("warn", "已请求中止,将在当前文件处理完成后停止…")

    def _set_running(self, running: bool):
        self._busy = running
        self.preview_btn.set_enabled(not running)
        self.run_btn.set_enabled(not running)
        self.stop_btn.set_enabled(running)
        if running:
            self.status.configure(text="处理中…")
        else:
            # 若仍在扫描动画中则停掉;确定态下保留终态进度,不要清零
            self._set_progress_mode("determinate")
            self.status.configure(text="就绪")

    # ------------------------------------------------------------------ 队列轮询
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    level, message = payload
                    self._append_log(level, message)
                elif kind == "progress":
                    done, total = payload
                    self._progress_update(done, total)
                elif kind == "plan":
                    dry_run, actions = payload
                    if dry_run:
                        # 仅预览时复位为"从 0 开始";执行完成后应保留 100% 的终态
                        self._progress_reset(len(actions))
                    self._fill_preview(actions, dry_run)
                elif kind == "done":
                    self._set_running(False)
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    # ------------------------------------------------------------------ 预览表
    def _clear_preview(self):
        self.preview.delete(*self.preview.get_children())
        self.preview_count.configure(text="尚未预览")
        self._planned = []

    def _fill_preview(self, actions, dry_run: bool):
        self._clear_preview()
        self._planned = list(actions)
        for index, action in enumerate(actions, 1):
            target = action.dst or "—"
            tags = ("danger",) if action.verb == "删除" else ()
            self.preview.insert("", "end", values=(action.verb, self._short(action.src), self._short(target)), tags=tags)
        label = "待执行" if dry_run else "已处理"
        self.preview_count.configure(text=f"共 {len(actions)} 项" if actions else "无匹配文件")

    @staticmethod
    def _short(path: str, limit: int = 90) -> str:
        if len(path) <= limit:
            return path
        return "…" + path[-(limit - 1):]

    # ------------------------------------------------------------------ 日志
    def _append_log(self, level: str, message: str):
        self.log.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{stamp}] ", "time")
        self.log.insert("end", f"{message}\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _save_log(self):
        content = self.log.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("提示", "当前没有日志可保存。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="保存日志",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"tidybatch_{time.strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content + "\n")
            self._append_log("ok", f"日志已保存至:{path}")
        except OSError as exc:
            self._append_log("error", f"日志保存失败:{exc}")

    # ------------------------------------------------------------------ 配置持久化
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._memory = data.get("params", {}) or {}
                last = data.get("last_op")
                if last and last in REGISTRY:
                    self.ops_tree.selection_set(last)
                    self.ops_tree.focus(last)
        except (OSError, ValueError):
            pass

    def _save_settings(self):
        try:
            if self._current is not None:
                self._memory[self._current.key] = self._collect()
            with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "params": self._memory,
                        "last_op": self._current.key if self._current else "",
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass

    def _on_close(self):
        if self._busy and not messagebox.askyesno("确认退出", "任务正在执行,确定要退出吗?", parent=self, icon="warning"):
            return
        self._save_settings()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
