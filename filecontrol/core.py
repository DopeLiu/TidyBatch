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
from dataclasses import dataclass, field as _dataclass_field
from typing import Callable, Dict, Iterable, List, Sequence

# --------------------------------------------------------------------------- #
# 日志回调:level 取值 "info" | "ok" | "warn" | "error"
# --------------------------------------------------------------------------- #
LogFn = Callable[[str, str], None]

# 统一的回收站目录名(按后缀删除时默认移入此目录,可随时找回)
TRASH_DIRNAME = ".filecontrol_trash"


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
