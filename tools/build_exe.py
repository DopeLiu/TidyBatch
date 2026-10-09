# -*- coding: utf-8 -*-
"""把 TidyBatch 打包成独立的 Windows 可执行文件。

产物:
    默认   dist/TidyBatch.exe          单文件,双击即用,便于分发
    加 -d  dist/TidyBatch/TidyBatch.exe   文件夹模式,启动更快

设计要点:
    * 通过 PyInstaller 的 Python API 调用,参数集中于此,避免命令行引号转义问题;
    * 仅打入 assets/icon.ico 作为资源(--add-data),保证打包后窗口图标不丢失;
    * --windowed 去掉控制台黑窗(GUI 程序);
    * 项目本身零第三方依赖,故无需 hidden-import;此处额外排除若干常见
      重量级库,防止将来误引入时体积膨胀。

用法:
    python tools/build_exe.py           # 单文件(默认)
    python tools/build_exe.py -d        # 文件夹模式
    python tools/build_exe.py -d -k     # 文件夹模式且保留解包内容(调试用)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "app.py")
ICON = os.path.join(ROOT, "assets", "icon.ico")
APP_NAME = "TidyBatch"

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


def build(onedir: bool = False, keep_work: bool = False) -> str:
    """执行打包,返回可执行文件路径。"""
    _ensure_pyinstaller()
    from PyInstaller.__main__ import run as pyinstaller_run

    build_dir = os.path.join(ROOT, "build")
    dist_dir = os.path.join(ROOT, "dist")

    # 清理上次产物,避免旧文件混入造成误判
    for stale in (build_dir, os.path.join(dist_dir, APP_NAME)):
        shutil.rmtree(stale, ignore_errors=True)
    if onedir:
        exe = os.path.join(dist_dir, APP_NAME, APP_NAME + ".exe")
    else:
        exe = os.path.join(dist_dir, APP_NAME + ".exe")
    if os.path.isfile(exe):
        os.remove(exe)

    args = [
        ENTRY,
        "--name", APP_NAME,
        "--onedir" if onedir else "--onefile",
        "--windowed",                                   # GUI 程序,不弹控制台
        "--noconfirm",                                  # 覆盖输出目录不再询问
        "--clean",                                      # 清理 PyInstaller 缓存
        "--distpath", dist_dir,
        "--workpath", build_dir,
        "--specpath", build_dir,
        # 图标同时用于:exe 文件图标、资源内图标(界面启动时读取)
        "--icon", ICON,
        "--add-data", f"{ICON}{os.pathsep}assets",
    ]
    for name in EXCLUDES:
        args += ["--exclude-module", name]

    print("[打包] 入口:", ENTRY)
    print("[打包] 模式:", "文件夹(onedir)" if onedir else "单文件(onefile)")
    print("[打包] 执行 PyInstaller ...\n")

    pyinstaller_run(args)

    if not keep_work:
        shutil.rmtree(build_dir, ignore_errors=True)
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 TidyBatch 为可执行文件")
    parser.add_argument("-d", "--onedir", action="store_true",
                        help="输出文件夹而非单文件(启动更快)")
    parser.add_argument("-k", "--keep-work", action="store_true",
                        help="保留 build 临时目录,便于排查问题")
    opts = parser.parse_args()

    exe = build(onedir=opts.onedir, keep_work=opts.keep_work)

    if os.path.isfile(exe):
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print("\n[打包成功] 产物:", exe)
        print(f"[打包成功] 体积: {size_mb:.1f} MB")
        if not opts.onedir:
            print("[提示] 配置 .tidybatch_settings.json 会保存在 exe 同级目录")
        return 0

    sys.stderr.write("\n[打包失败] 未找到产物,请检查上方 PyInstaller 输出。\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
