# -*- coding: utf-8 -*-
"""TidyBatch —— 批量文件工具箱(核心包)。

模块划分
--------
core.py        核心抽象:Field(参数描述) / Action(变更条目) / Operation(操作基类) / 注册表
operations.py  所有具体功能的实现,每个功能 = 一个 @register 的 Operation 子类
gui.py         tkinter 图形界面(只负责展示与交互,不含任何文件操作逻辑)

扩展方式
--------
新增一个功能只需要在 operations.py 里写一个 Operation 子类并用 @register 装饰,
界面会自动出现对应入口与参数表单,无需修改 gui.py。
"""

__version__ = "2.0.3"
__all__ = ["core", "operations", "gui"]
