# -*- coding: utf-8 -*-
"""核心层 —— 纯逻辑,不依赖任何 GUI 框架,可单独被脚本 / 定时任务 / 测试复用。

设计要点
--------
1. 「计划 / 执行」两阶段:
       plan(params)  -> 只读扫描目录,返回 List[Action],不碰磁盘
       apply(action) -> 真正执行单条变更
   「预览」与「执行」共用同一套扫描代码,保证预览结果与实际执行完全一致。

2. 声明式参数:
   Operation.fields 用 Field 描述自己需要哪些输入,界面据此自动渲染表单。

3. 注册表:
   所有操作类通过 @register 装饰器登记到 REGISTRY,界面遍历注册表生成菜单。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field as _dataclass_field
from typing import Callable, Dict, Iterable, List, Sequence

# --------------------------------------------------------------------------- #
# 日志回调:level 取值 "info" | "ok" | "warn" | "error"
# --------------------------------------------------------------------------- #
LogFn = Callable[[str, str], None]


# --------------------------------------------------------------------------- #
# 参数描述
# --------------------------------------------------------------------------- #
@dataclass
class Field:
    """描述一个输入项,界面根据 kind 自动生成对应控件。"""

    key: str                                   # 参数名,对应 params[key]
    label: str                                 # 界面显示名
    kind: str = "text"                         # text | dir | choice | check
    default: str = ""                          # 默认值
    hint: str = ""                             # 输入框下方的灰色提示
    choices: Sequence[str] = ()                # kind == "choice" 时的候选项


# --------------------------------------------------------------------------- #
# 变更条目
# --------------------------------------------------------------------------- #
@dataclass
class Action:
    """一条待执行的变更(Action 本身不执行任何操作,只是数据)。"""

    src: str                                   # 源路径(绝对路径)
    dst: str = ""                              # 目标路径;删除类操作为空
    verb: str = "重命名"                        # 动作类型,用于界面展示与统计
    note: str = ""                             # 备注(如 "纯数字文件名")
    extra: Dict[str, object] = _dataclass_field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def is_on(params: Dict[str, str], key: str) -> bool:
    """把界面传来的字符串勾选值解析为布尔值。"""
    return str(params.get(key, "")).strip().lower() in ("1", "true", "yes", "on")


def iter_files(directory: str, recursive: bool = False) -> Iterable[str]:
    """遍历目录下的文件(不含目录本身)。recursive=True 时递归子目录。"""
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                yield os.path.join(root, name)
    else:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_file():
                    yield entry.path
            except OSError:
                continue


def parse_exts(text: str) -> set:
    """解析后缀输入:支持顿号/逗号/分号分隔,自动补前导点并小写。"""
    result = set()
    for part in str(text or "").replace(";", ",").replace("、", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith("."):
            part = "." + part
        result.add(part)
    return result


def split_ext(filename: str):
    return os.path.splitext(filename)


def unique_path(path: str) -> str:
    """目标已存在时自动追加 _1 / _2 …,避免静默覆盖已有文件。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    index = 1
    while os.path.exists(f"{base}_{index}{ext}"):
        index += 1
    return f"{base}_{index}{ext}"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def require_dir(path: str) -> str:
    """校验目录参数,返回规范化路径。"""
    path = (path or "").strip().strip('"')
    if not path:
        raise ValueError("请先选择目标目录。")
    if not os.path.isdir(path):
        raise ValueError(f"目录不存在:{path}")
    return path


# --------------------------------------------------------------------------- #
# 系统回收站(Windows / macOS / Linux 通用)
# --------------------------------------------------------------------------- #
def send_to_recycle_bin(path: str) -> None:
    """把文件移入系统回收站(Windows / macOS / Linux 通用,可随时还原)。

    各平台的实现途径:
        Windows   Shell API SHFileOperationW(与资源管理器里删除等效)
        macOS     Objective-C 运行时调用 NSFileManager(与废纸篓删除等效)
        Linux     按 FreeDesktop 回收站规范内置实现;异常时退回 gio / trash-put 命令

    均为进程内调用或系统标准命令,不引入任何第三方 Python 依赖。
    失败时抛出异常,由 Operation.run() 按单条失败记录,不会中断整批任务。
    """
    if sys.platform == "win32":
        _recycle_bin_windows(path)
    elif sys.platform == "darwin":
        _recycle_bin_macos(path)
    else:
        _recycle_bin_linux(path)


def _recycle_bin_windows(path: str) -> None:
    """Windows:SHFileOperationW + FOF_ALLOWUNDO 移入回收站。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    src = os.path.abspath(path)
    if not os.path.exists(src):
        raise FileNotFoundError(src)

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                            # FO_DELETE
    op.pFrom = src + "\0"                   # 路径须以两个 \0 结尾
    op.fFlags = 0x40 | 0x10 | 0x400 | 0x4   # ALLOWUNDO|NOCONFIRMATION|NOERRORUI|SILENT
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    # 官方文档:非零返回值仅为调试参考,不可作为最终结论 —— 实测部分系统上
    # 成功移入回收站也会返回 2。成败以「中止标志」和「文件是否已离开原位置」为准。
    if op.fAnyOperationsAborted:
        raise OSError("移入系统回收站被中止。")
    if os.path.exists(src):
        raise OSError(f"移入系统回收站失败(错误码 {result})。")


def _recycle_bin_macos(path: str) -> None:
    """macOS:经 Objective-C 运行时调用 NSFileManager 移入废纸篓。

    等价于 Finder 的「移到废纸篓」,核心调用为:
        [[NSFileManager defaultManager] trashItemAtURL:url resultingItemURL:nil error:&e]
    objc_msgSend 是可变参数函数,因此按每种调用签名取独立入口。
    """
    import ctypes
    from ctypes import CFUNCTYPE, POINTER, byref, c_byte, c_char_p, c_void_p
    from ctypes.util import find_library

    libobjc = ctypes.cdll.LoadLibrary(find_library("objc") or "/usr/lib/libobjc.A.dylib")
    ctypes.cdll.LoadLibrary(
        find_library("Foundation") or "/System/Library/Frameworks/Foundation.framework/Foundation"
    )

    libobjc.objc_getClass.restype = c_void_p
    libobjc.objc_getClass.argtypes = [c_char_p]
    libobjc.sel_registerName.restype = c_void_p
    libobjc.sel_registerName.argtypes = [c_char_p]

    address = ctypes.cast(libobjc.objc_msgSend, c_void_p).value
    msg0 = CFUNCTYPE(c_void_p, c_void_p, c_void_p)(address)                   # () -> id
    msg1 = CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p)(address)         # (id) -> id
    msg_path = CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_char_p)(address)     # (const char*) -> id
    msg_chars = CFUNCTYPE(c_char_p, c_void_p, c_void_p)(address)              # () -> const char*
    msg_void = CFUNCTYPE(None, c_void_p, c_void_p)(address)                   # () -> void
    msg_trash = CFUNCTYPE(
        c_byte, c_void_p, c_void_p, c_void_p, c_void_p, POINTER(c_void_p)
    )(address)                                                                # (url, nil, NSError **) -> BOOL

    def selector(name: str):
        return libobjc.sel_registerName(name.encode("ascii"))

    def cls(name: str):
        return libobjc.objc_getClass(name.encode("ascii"))

    src = os.path.abspath(path)
    if not os.path.exists(src):
        raise FileNotFoundError(src)

    # 每次调用建立独立的自动释放池,避免批量删除时 NSString / NSURL 对象堆积
    pool = msg0(cls("NSAutoreleasePool"), selector("alloc"))
    pool = msg0(pool, selector("init"))
    try:
        ns_path = msg_path(cls("NSString"), selector("stringWithUTF8String:"), src.encode("utf-8"))
        url = msg1(cls("NSURL"), selector("fileURLWithPath:"), ns_path)
        error = c_void_p()
        ok = msg_trash(
            msg0(cls("NSFileManager"), selector("defaultManager")),
            selector("trashItemAtURL:resultingItemURL:error:"),
            url,
            None,
            byref(error),
        )
        if not ok:
            reason = ""
            if error.value:
                description = msg0(error.value, selector("localizedDescription"))
                text = msg_chars(description, selector("UTF8String")) if description else None
                if text:
                    reason = text.decode("utf-8", "replace")
            raise OSError(f"移入废纸篓失败:{reason or src}")
    finally:
        msg_void(pool, selector("drain"))


def _recycle_bin_linux(path: str) -> None:
    """Linux / 其他类 Unix:移入系统回收站。

    优先使用按 FreeDesktop 回收站规范的内置实现(零外部依赖);
    卷挂载识别等边缘场景失败时,退回系统命令 gio / trash-put(如已安装)。
    """
    src = os.path.abspath(path)
    if not os.path.exists(src):
        raise FileNotFoundError(src)

    native_error = None
    try:
        _linux_trash_native(src)
        return
    except OSError as exc:
        native_error = exc

    for command in (["gio", "trash", "--"], ["trash-put", "--"]):
        exe = shutil.which(command[0])
        if not exe:
            continue
        completed = subprocess.run([exe, *command[1:], src], capture_output=True)
        if completed.returncode == 0:
            return

    raise native_error


def _mount_point_of(path: str) -> str:
    """向上查找 path 所在文件系统的挂载点;兜底返回根目录 "/"。"""
    probe = os.path.realpath(path)
    while not os.path.ismount(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return probe


def _linux_trash_native(src: str) -> None:
    """按 FreeDesktop 回收站规范移入回收站(纯内置实现,含 .trashinfo 元数据)。

    规范:家目录卷 → $XDG_DATA_HOME/Trash;
    其他卷 → 挂载点下 .Trash/$uid(须为目录、非符号链接、带 sticky 位,优先)
    或 .Trash-$uid。
    """
    from datetime import datetime
    from urllib.parse import quote

    home = os.path.expanduser("~")
    if os.lstat(src).st_dev == os.lstat(home).st_dev:
        data_home = os.environ.get("XDG_DATA_HOME", "")
        if not (data_home and os.path.isabs(data_home)):
            data_home = os.path.join(home, ".local", "share")
        trash_root = os.path.join(data_home, "Trash")
    else:
        volume = _mount_point_of(src)
        uid = os.getuid()
        trash_root = ""
        topdir = os.path.join(volume, ".Trash")
        if (
            os.path.isdir(topdir)
            and not os.path.islink(topdir)
            and os.lstat(topdir).st_mode & 0o1000
        ):
            candidate = os.path.join(topdir, str(uid))
            try:
                os.makedirs(candidate, mode=0o700, exist_ok=True)
                trash_root = candidate
            except OSError:
                trash_root = ""
        if not trash_root:
            trash_root = os.path.join(volume, f".Trash-{uid}")

    files_dir = os.path.join(trash_root, "files")
    info_dir = os.path.join(trash_root, "info")
    os.makedirs(files_dir, mode=0o700, exist_ok=True)
    os.makedirs(info_dir, mode=0o700, exist_ok=True)

    # 重名避让:「名字 1.ext」「名字 2.ext」…(沿用 send2trash 的做法)
    name = os.path.basename(src)
    base, ext = os.path.splitext(name)
    dest_name = name
    counter = 0
    while (
        os.path.exists(os.path.join(files_dir, dest_name))
        or os.path.exists(os.path.join(info_dir, dest_name + ".trashinfo"))
    ):
        counter += 1
        dest_name = f"{base} {counter}{ext}"

    # 先写元数据再移动文件:中途失败则回滚,避免回收站出现"幽灵条目"
    info_path = os.path.join(info_dir, dest_name + ".trashinfo")
    with open(info_path, "w", encoding="utf-8") as handle:
        handle.write(
            "[Trash Info]\n"
            f"Path={quote(src)}\n"
            f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
        )
    try:
        os.rename(src, os.path.join(files_dir, dest_name))
    except OSError:
        try:
            os.remove(info_path)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# 操作基类
# --------------------------------------------------------------------------- #
class Operation:
    """所有批量功能的基类。

    子类必须覆盖:
        key / name / category / description
        fields
        plan(params, log)              -> List[Action]
        apply(action, params, log)     -> None

    子类可选覆盖:
        destructive = True   标记为危险操作,界面会加强确认
    """

    key: str = ""
    name: str = "未命名操作"
    category: str = "其他"
    description: str = ""
    destructive: bool = False
    fields: Sequence[Field] = ()

    # ---------------------------------------------------------------- 待实现
    def plan(self, params: Dict[str, str], log: LogFn) -> List[Action]:
        raise NotImplementedError

    def apply(self, action: Action, params: Dict[str, str], log: LogFn) -> None:
        raise NotImplementedError

    # ---------------------------------------------------------------- 模板流程
    def run(
        self,
        params: Dict[str, str],
        log: LogFn,
        dry_run: bool = True,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> List[Action]:
        """统一执行入口。

        dry_run=True  仅扫描并返回变更清单(预览),磁盘不做任何改动
        dry_run=False 逐条执行,遇错不中断,最后汇总成功 / 失败数量

        on_progress(done, total) 在每处理完一条后回调,用于驱动界面进度条。
        """
        actions = self.plan(params, log) or []

        if not actions:
            log("warn", "没有匹配到任何文件,未执行操作。")
            return []

        if dry_run:
            log("info", f"预览完成:共 {len(actions)} 项待处理(磁盘未改动)。")
            return actions

        total = len(actions)
        log("info", f"开始执行,共 {total} 项 …")
        if on_progress is not None:
            on_progress(0, total)

        ok = fail = 0
        for index, action in enumerate(actions, 1):
            if should_stop is not None and should_stop():
                log("warn", f"已中止:在第 {index} 项之前停止,剩余 {total - index + 1} 项未处理。")
                break
            try:
                self.apply(action, params, log)
                ok += 1
            except Exception as exc:  # noqa: BLE001 —— 单条失败不应中断整批
                fail += 1
                log("error", f"失败 [{os.path.basename(action.src)}]:{exc}")
            if on_progress is not None:
                on_progress(index, total)

        if fail:
            log("warn", f"执行结束:成功 {ok} 项,失败 {fail} 项。")
        else:
            log("ok", f"执行结束:成功 {ok} 项。")
        return actions


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
REGISTRY: Dict[str, Operation] = {}


def register(cls):
    """把一个 Operation 子类登记进全局注册表(装饰器)。"""
    instance = cls()
    if not instance.key:
        instance.key = cls.__name__
    REGISTRY[instance.key] = instance
    return cls


def categories() -> Dict[str, List[Operation]]:
    """按分类归拢所有已注册操作,供界面生成侧边栏。"""
    grouped: Dict[str, List[Operation]] = {}
    for op in REGISTRY.values():
        grouped.setdefault(op.category, []).append(op)
    return grouped
