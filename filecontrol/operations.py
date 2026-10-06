# -*- coding: utf-8 -*-
"""所有批量功能的实现。

新增功能只需在文件末尾追加一个类::

    @register
    class MyOp(Operation):
        key = "my_op"
        name = "我的新功能"
        category = "其他"
        description = "功能说明(会显示在界面顶部)"
        fields = (
            Field("directory", "目标目录", "dir"),
            Field("suffix", "参数", "text", "默认值"),
        )

        def plan(self, params, log):
            ...  # 只读扫描,返回 List[Action]

        def apply(self, action, params, log):
            ...  # 执行单条变更

界面会自动出现入口与表单,无需改动 gui.py。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile

from .core import (
    TRASH_DIRNAME,
    Action,
    Field,
    Operation,
    ensure_dir,
    is_on,
    iter_files,
    parse_exts,
    register,
    require_dir,
    split_ext,
    unique_path,
)

# --------------------------------------------------------------------------- #
# 外部工具探测(7-Zip)
# --------------------------------------------------------------------------- #
_7Z = {"checked": False, "path": None}


def find_7z():
    """探测系统中的 7-Zip 可执行文件;找不到返回 None。"""
    if not _7Z["checked"]:
        _7Z["checked"] = True
        candidates = [shutil.which(name) for name in ("7z", "7za", "7zz", "7z.exe")]
        candidates += [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                _7Z["path"] = candidate
                break
    return _7Z["path"]


def _run_7z(args, log=None):
    exe = find_7z()
    if not exe:
        raise RuntimeError("未找到 7-Zip,请先安装 7-Zip 并确保 7z.exe 在 PATH 中。")
    # -bso0 / -bsp0 关闭 7z 自身的进度输出,避免污染日志
    completed = subprocess.run(
        [exe] + args + ["-y", "-bso0", "-bsp0"],
        capture_output=True,
        text=True,
    )
    if completed.returncode not in (0, 1):  # 1 = 完成但有警告
        raise RuntimeError((completed.stderr or completed.stdout or "7-Zip 执行失败").strip()[:300])
    return completed


# --------------------------------------------------------------------------- #
# 归档辅助函数
# --------------------------------------------------------------------------- #
def _zip_compress(src: str, dst: str) -> None:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(src, arcname=os.path.basename(src))


def _safe_extract_zip(src: str, outdir: str) -> None:
    """解压 zip,并拦截绝对路径 / 上级目录穿越,防止恶意压缩包写出目标目录。"""
    with zipfile.ZipFile(src) as zf:
        for member in zf.infolist():
            normalized = member.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise RuntimeError(f"压缩包内含不安全路径,已拒绝解压:{member.filename}")
        zf.extractall(outdir)


def _extract(src: str, outdir: str) -> None:
    """按真实格式分发解压,优先使用内置库,不依赖文件后缀。"""
    ensure_dir(outdir)

    if zipfile.is_zipfile(src):
        _safe_extract_zip(src, outdir)
        return

    try:
        is_tar = tarfile.is_tarfile(src)
    except Exception:  # noqa: BLE001
        is_tar = False
    if is_tar:
        with tarfile.open(src) as tf:
            try:
                tf.extractall(outdir, filter="data")   # Python 3.12+ 安全过滤
            except TypeError:                          # 兼容更早版本
                tf.extractall(outdir)
        return

    # 其余格式(.7z / .7zz / .rar …)交给 7-Zip
    _run_7z(["x", src, f"-o{outdir}"], None)


# --------------------------------------------------------------------------- #
# 1. 重命名类
# --------------------------------------------------------------------------- #
@register
class AddExtOp(Operation):
    key = "add_ext"
    name = "补充文件后缀"
    category = "重命名"
    description = (
        "为「文件名完全由数字组成且没有扩展名」的文件补上指定后缀。\n"
        "典型场景:网盘下载的无后缀分片文件,如 12345 → 12345.zip"
    )
    fields = (
        Field("directory", "目标目录", "dir"),
        Field("ext", "要补充的后缀", "text", ".zip", "例如 .zip / .7z / .mp4"),
        Field("recursive", "包含子目录", "check", ""),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        ext = (params.get("ext") or "").strip()
        if not ext:
            raise ValueError("请填写要补充的后缀,例如 .zip")
        if not ext.startswith("."):
            ext = "." + ext

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            if os.path.basename(path).isdigit():
                actions.append(Action(src=path, dst=unique_path(path + ext), verb="重命名", note="纯数字文件名"))
        return actions

    def apply(self, action, params, log):
        os.rename(action.src, action.dst)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(action.dst)}")


@register
class ChangeExtOp(Operation):
    key = "change_ext"
    name = "修改文件后缀"
    category = "重命名"
    description = (
        "把指定后缀批量替换为新后缀,只改扩展名,不改文件名主体。\n"
        "典型场景:把 .7zz 批量改为 .7z,让解压软件能正确识别。"
    )
    fields = (
        Field("directory", "目标目录", "dir"),
        Field("old_ext", "原后缀", "text", ".7zz", "例如 .7zz"),
        Field("new_ext", "新后缀", "text", ".7z", "例如 .7z"),
        Field("recursive", "包含子目录", "check", ""),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        old_ext = (params.get("old_ext") or "").strip().lower()
        new_ext = (params.get("new_ext") or "").strip().lower()
        if not old_ext or not new_ext:
            raise ValueError("请同时填写原后缀与新后缀。")
        if not old_ext.startswith("."):
            old_ext = "." + old_ext
        if not new_ext.startswith("."):
            new_ext = "." + new_ext
        if old_ext == new_ext:
            raise ValueError("原后缀与新后缀相同,无需处理。")

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            if split_ext(path)[1].lower() == old_ext:
                target = unique_path(split_ext(path)[0] + new_ext)
                actions.append(Action(src=path, dst=target, verb="重命名", note=f"{old_ext} → {new_ext}"))
        return actions

    def apply(self, action, params, log):
        os.rename(action.src, action.dst)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(action.dst)}")


@register
class StripStringOp(Operation):
    key = "strip_string"
    name = "清理文件名中的字符"
    category = "重命名"
    description = (
        "递归扫描目录,删除文件名中包含的指定字符串(可批量去除推广水印等)。\n"
        "例如将 freeshare666.vip@影片名.mp4 还原为 影片名.mp4"
    )
    fields = (
        Field("directory", "目标目录", "dir"),
        Field("target_string", "要删除的字符串", "text", "", "例如 freeshare666.vip@"),
        Field("recursive", "包含子目录", "check", "1"),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        target = params.get("target_string") or ""
        if not target:
            raise ValueError("请填写要从文件名中删除的字符串。")

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            name = os.path.basename(path)
            if target not in name:
                continue
            new_name = name.replace(target, "")
            if not new_name or new_name == name:
                continue
            target_path = os.path.join(os.path.dirname(path), new_name)
            actions.append(Action(src=path, dst=unique_path(target_path), verb="重命名", note="移除指定字符串"))
        return actions

    def apply(self, action, params, log):
        os.rename(action.src, action.dst)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(action.dst)}")


# --------------------------------------------------------------------------- #
# 2. 删除类
# --------------------------------------------------------------------------- #
@register
class DeleteExtOp(Operation):
    key = "delete_ext"
    name = "按后缀删除文件"
    category = "删除"
    description = (
        "删除目录中扩展名匹配的文件。\n"
        "默认「移入回收站文件夹」:文件被移动到同级 .filecontrol_trash 目录里,随时可手动找回。\n"
        "选择「直接删除」则不可恢复,请务必先预览确认。"
    )
    destructive = True
    fields = (
        Field("directory", "目标目录", "dir"),
        Field("ext", "要删除的后缀", "text", "", "多个用逗号分隔,如 .7z,.zip"),
        Field("recursive", "包含子目录", "check", ""),
        Field(
            "mode",
            "删除方式",
            "choice",
            "移入回收站文件夹(推荐)",
            "推荐保留退路,可随时手动还原",
            ("移入回收站文件夹(推荐)", "直接删除(不可恢复)"),
        ),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        exts = parse_exts(params.get("ext", ""))
        if not exts:
            raise ValueError("请填写要删除的文件后缀,例如 .7z")

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            if split_ext(path)[1].lower() in exts:
                actions.append(Action(src=path, verb="删除", note=params.get("mode", "")))
        return actions

    def apply(self, action, params, log):
        if str(params.get("mode", "")).startswith("移入"):
            trash = ensure_dir(os.path.join(os.path.dirname(action.src), TRASH_DIRNAME))
            target = unique_path(os.path.join(trash, os.path.basename(action.src)))
            shutil.move(action.src, target)
            log("warn", f"已移入回收站:{os.path.basename(action.src)}")
        else:
            os.remove(action.src)
            log("warn", f"已删除:{os.path.basename(action.src)}")


# --------------------------------------------------------------------------- #
# 3. 压缩 / 解压类
# --------------------------------------------------------------------------- #
@register
class CompressOp(Operation):
    key = "compress"
    name = "批量压缩"
    category = "压缩 / 解压"
    description = (
        "把目录下每一个符合条件的文件,各自打包成一个独立压缩包。\n"
        "· zip 格式由 Python 内置支持,开箱可用\n"
        "· 7z 格式需要系统已安装 7-Zip"
    )
    fields = (
        Field("directory", "目标目录", "dir"),
        Field("ext_filter", "只处理这些后缀", "text", "", "留空=全部;多个用逗号分隔,如 .7zz,.rar"),
        Field("fmt", "压缩格式", "choice", "zip", "", ("zip", "7z")),
        Field(
            "naming",
            "压缩包命名",
            "choice",
            "追加后缀(影片.mp4 → 影片.mp4.zip)",
            "追加可保留原扩展名信息;替换适用于重新打包(如 .7zz → .7z)",
            ("追加后缀(影片.mp4 → 影片.mp4.zip)", "替换后缀(影片.mp4 → 影片.zip)"),
        ),
        Field("recursive", "包含子目录", "check", ""),
        Field("delete_src", "压缩成功后删除原文件", "check", ""),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        fmt = (params.get("fmt") or "zip").strip()
        filters = parse_exts(params.get("ext_filter", ""))
        append = not str(params.get("naming", "")).startswith("替换")
        if fmt == "7z" and not find_7z():
            log("warn", "未检测到 7-Zip:选择 7z 格式将无法执行,建议改用 zip。")

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            if filters and split_ext(path)[1].lower() not in filters:
                continue
            base = path if append else split_ext(path)[0]
            target = base + "." + fmt
            # 兜底:替换模式下若目标与源同名(源本身已是该格式),强制改为追加,避免自我覆盖
            if os.path.normcase(target) == os.path.normcase(path):
                target = path + "." + fmt
            actions.append(Action(src=path, dst=unique_path(target), verb="压缩", note=fmt))
        return actions

    def apply(self, action, params, log):
        fmt = (params.get("fmt") or "zip").strip()
        if fmt == "7z":
            _run_7z(["a", "-t7z", action.dst, action.src], log)
        else:
            _zip_compress(action.src, action.dst)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(action.dst)}")

        if is_on(params, "delete_src"):
            os.remove(action.src)
            log("warn", f"已删除原文件:{os.path.basename(action.src)}")


@register
class ExtractOp(Operation):
    key = "extract"
    name = "批量解压"
    category = "压缩 / 解压"
    description = (
        "扫描目录中的压缩包并批量解压到各自的同名文件夹。\n"
        "· zip / tar / tar.gz / tgz / bz2 / xz 由 Python 内置支持\n"
        "· 7z / rar 等格式需要系统已安装 7-Zip\n"
        "· 内置路径穿越防护,拒绝解压含 .. 或绝对路径的异常压缩包"
    )
    fields = (
        Field("directory", "目标目录", "dir"),
        Field(
            "ext_filter",
            "压缩包后缀",
            "text",
            ".zip,.7z,.7zz,.rar,.tar,.gz",
            "多个用逗号分隔",
        ),
        Field("recursive", "包含子目录", "check", ""),
        Field(
            "target_mode",
            "解压位置",
            "choice",
            "同名文件夹(推荐)",
            "",
            ("同名文件夹(推荐)", "当前目录", "统一输出到 _extracted"),
        ),
        Field("delete_src", "解压成功后删除原压缩包", "check", ""),
    )

    def plan(self, params, log):
        directory = require_dir(params.get("directory", ""))
        exts = parse_exts(params.get("ext_filter", ""))
        if not exts:
            raise ValueError("请填写要解压的压缩包后缀。")
        mode = params.get("target_mode", "")

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            # .tar.gz 这类双后缀额外做一次整体匹配
            name_lower = os.path.basename(path).lower()
            ext = split_ext(path)[1].lower()
            if ext not in exts and not any(name_lower.endswith(e) for e in exts):
                continue

            parent = os.path.dirname(path)
            if mode.startswith("同名文件夹"):
                outdir = os.path.join(parent, os.path.splitext(os.path.basename(path))[0])
            elif mode.startswith("统一输出"):
                outdir = os.path.join(parent, "_extracted")
            else:
                outdir = parent

            actions.append(Action(src=path, dst=outdir, verb="解压", note=os.path.basename(outdir)))
        return actions

    def apply(self, action, params, log):
        outdir = action.dst
        if os.path.exists(outdir) and os.path.isfile(outdir):
            raise RuntimeError("解压目标已被同名文件占用,已跳过。")
        _extract(action.src, outdir)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(outdir)}/")

        if is_on(params, "delete_src"):
            os.remove(action.src)
            log("warn", f"已删除原压缩包:{os.path.basename(action.src)}")
