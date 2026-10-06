# -*- coding: utf-8 -*-
"""FileControler 启动入口。

用法:
    python app.py

说明:
    本项目仅依赖 Python 标准库(tkinter),无需安装任何第三方包。
    若提示缺少 tkinter,说明所用 Python 未包含 tcl/tk 组件,
    请改用 python.org 官方安装包安装的 Python(默认自带)。
"""
import os
import sys

# 让脚本在任意工作目录下都能 import 到 filecontrol 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import tkinter  # noqa: F401
except ImportError:
    sys.stderr.write(
        "\n[启动失败] 当前 Python 环境缺少 tkinter 图形库,无法打开界面。\n\n"
        f"  当前解释器:{sys.executable}\n\n"
        "解决办法(任选其一):\n"
        "  1. 使用 python.org 官方安装包安装的 Python(默认自带 tkinter)运行本程序;\n"
        "  2. 若使用自定义/精简版 Python,请安装 tcl/tk 组件后重试;\n"
        "  3. 用 PyCharm 时,在 Settings → Project → Python Interpreter 中\n"
        "     切换到带 tkinter 的解释器。\n\n"
        "提示:可在命令行执行  python -m tkinter  验证是否支持;\n"
        "      能弹出一个测试窗口即表示支持。\n\n"
    )
    sys.exit(1)

from filecontrol.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
