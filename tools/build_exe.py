# -*- coding: utf-8 -*-
"""把 TidyBatch 打包成独立的可执行程序。

产物:
    Windows  默认     dist/TidyBatch.exe            单文件,双击即用,便于分发
             -d 模式   dist/TidyBatch/TidyBatch.exe  文件夹模式,启动更快
    macOS              dist/TidyBatch.app            文件夹模式(.app 包,双击即用)
                      —— 使用 universal2 解释器时自动构建"Apple 芯片 + Intel"通用版本

设计要点:
    * 通过 PyInstaller 的 Python API 调用,参数集中于此,避免命令行引号转义问题;
    * 图标资源经 --add-data 一并打入,保证打包后窗口图标不丢失
      (Windows 用 assets/icon.ico,macOS 用 assets/icon.icns);
    * --windowed 去掉控制台黑窗(Windows)并产出 .app 包(macOS);
    * 项目本身零第三方依赖,故无需 hidden-import;此处额外排除若干常见
      重量级库,防止将来误引入时体积膨胀。

用法:
    python tools/build_exe.py           # Windows:单文件(默认) / macOS:.app
    python tools/build_exe.py -d        # Windows:文件夹模式(对 macOS 无影响,始终为文件夹模式)
    python tools/build_exe.py -d -k     # 文件夹模式且保留解包内容(调试用)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import sysconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "app.py")
ICON_ICO = os.path.join(ROOT, "assets", "icon.ico")
ICON_ICNS = os.path.join(ROOT, "assets", "icon.icns")
ICON_PNG = os.path.join(ROOT, "assets", "icon.png")
APP_NAME = "TidyBatch"
BUNDLE_ID = "io.github.dopeliu.tidybatch"

# 可能被间接引入但本项目确实用不到的大体积库,排除以控制产物体积
EXCLUDES = (
    "numpy",
    "pandas",
    "matplotlib",
    "scipy",
    "PIL",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "setuptools",
    "pip",
)


def _ensure_pyinstaller() -> None:
    """确认当前解释器已安装 PyInstaller,否则给出明确安装指引。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "\n[打包失败] 当前 Python 环境未安装 PyInstaller。\n\n"
            f"  当前解释器:{sys.executable}\n\n"
            "请先安装后重试:\n"
            f'  "{sys.executable}" -m pip install pyinstaller\n\n'
        )
        sys.exit(1)


def _is_universal2() -> bool:
    """当前解释器是否为 universal2(同时含 x86_64 与 arm64)。"""
    return sysconfig.get_platform().endswith("universal2")


def _dir_size(path: str) -> int:
    """递归统计目录体积(用于 .app 包)。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.isfile(file_path):
                total += os.path.getsize(file_path)
    return total


def build(onedir: bool = False, keep_work: bool = False) -> str:
    """执行打包,返回产物路径(.exe 文件或 .app 目录)。"""
    if sys.platform not in ("win32", "darwin"):
        sys.stderr.write("\n[打包失败] 本脚本目前仅支持在 Windows 与 macOS 上运行。\n")
        sys.exit(1)

    _ensure_pyinstaller()
    from PyInstaller.__main__ import run as pyinstaller_run

    is_mac = sys.platform == "darwin"
    if is_mac:
        # .app 包内始终使用文件夹模式:启动更快,签名更稳(对用户无感知,仍是一个图标)
        onedir = True
        if not os.path.isfile(ICON_ICNS):
            sys.stderr.write("\n[打包失败] 缺少 macOS 图标文件 assets/icon.icns。\n")
            sys.exit(1)
        icon = ICON_ICNS
    else:
        icon = ICON_ICO

    build_dir = os.path.join(ROOT, "build")
    dist_dir = os.path.join(ROOT, "dist")

    # 清理上次产物,避免旧文件混入造成误判
    shutil.rmtree(build_dir, ignore_errors=True)
    for stale in ("TidyBatch.exe", "TidyBatch", "TidyBatch.app"):
        path = os.path.join(dist_dir, stale)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)

    if is_mac:
        out = os.path.join(dist_dir, APP_NAME + ".app")
    elif onedir:
        out = os.path.join(dist_dir, APP_NAME, APP_NAME + ".exe")
    else:
        out = os.path.join(dist_dir, APP_NAME + ".exe")

    args = [
        ENTRY,
        "--name", APP_NAME,
        "--onedir" if onedir else "--onefile",
        "--windowed",                                   # GUI 程序:Windows 去控制台窗,macOS 产出 .app 包
        "--noconfirm",                                  # 覆盖输出目录不再询问
        "--clean",                                      # 清理 PyInstaller 缓存
        "--distpath", dist_dir,
        "--workpath", build_dir,
        "--specpath", build_dir,
        # 图标同时用于:程序文件图标、资源内图标(界面启动时读取)
        "--icon", icon,
        "--add-data", f"{ICON_ICO}{os.pathsep}assets",
    ]
    if is_mac:
        # macOS 下 gui.py 的图标查找会回退到 .png(iconbitmap 在 mac 上不可用)
        if os.path.isfile(ICON_PNG):
            args += ["--add-data", f"{ICON_PNG}{os.pathsep}assets"]
        args += ["--osx-bundle-identifier", BUNDLE_ID]
        if _is_universal2():
            args += ["--target-arch", "universal2"]
        else:
            print("[提示] 当前解释器不是 universal2,产物只支持本机芯片架构;")
            print("       如需同时支持 Apple 芯片与 Intel,请使用 python.org 的 universal2 安装包。")
    for name in EXCLUDES:
        args += ["--exclude-module", name]

    print("[打包] 入口:", ENTRY)
    print("[打包] 模式:", ("文件夹(onedir) —— .app 包" if is_mac else
                          ("文件夹(onedir)" if onedir else "单文件(onefile)")))
    print("[打包] 执行 PyInstaller ...\n")

    pyinstaller_run(args)

    if not keep_work:
        shutil.rmtree(build_dir, ignore_errors=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 TidyBatch 为可执行程序")
    parser.add_argument("-d", "--onedir", action="store_true",
                        help="输出文件夹而非单文件(Windows 下启动更快;macOS 固定为该模式)")
    parser.add_argument("-k", "--keep-work", action="store_true",
                        help="保留 build 临时目录,便于排查问题")
    opts = parser.parse_args()

    out = build(onedir=opts.onedir, keep_work=opts.keep_work)

    if os.path.exists(out):
        size = os.path.getsize(out) if os.path.isfile(out) else _dir_size(out)
        print("\n[打包成功] 产物:", out)
        print(f"[打包成功] 体积: {size / 1024 / 1024:.1f} MB")
        if sys.platform == "win32" and not opts.onedir:
            print("[提示] 配置 .tidybatch_settings.json 会保存在 exe 同级目录")
        elif sys.platform == "darwin":
            print("[提示] 配置保存在 ~/Library/Application Support/TidyBatch")
        return 0

    sys.stderr.write("\n[打包失败] 未找到产物,请检查上方 PyInstaller 输出。\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
