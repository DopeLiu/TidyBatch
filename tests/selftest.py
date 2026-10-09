# -*- coding: utf-8 -*-
"""TidyBatch 回归自测。

用途
----
后续新增功能(Operation 子类)后,运行本脚本可快速验证:
  · 所有已注册功能的「预览 → 执行」链路是否正常
  · 界面能否为每个功能正确生成表单
  · 危险操作的确认与着色是否正确

运行:
    python tests/selftest.py

注意:
    测试全程在系统临时目录中进行,结束后自动清理,不会触碰你的真实文件。
    界面测试需要当前 Python 支持 tkinter;不支持时会自动跳过界面部分。
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tidybatch.core import REGISTRY  # noqa: E402
from tidybatch import operations  # noqa: F401,E402

PASS, FAIL = [], []


def log(level, message):
    print(f"      [{level}] {message}")


def check(name, condition, extra=""):
    (PASS if condition else FAIL).append(name)
    print(f"{'  OK  ' if condition else ' FAIL '} {name} {extra}")


def pump(app, timeout=15):
    """驱动事件循环,直到后台任务结束并排空队列。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        if not app._busy and app._queue.empty():
            break
        time.sleep(0.02)
    app.update()


def make(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def recycle_bin_count(sample_path):
    """查询样本文件所在系统回收站的项目数;无法可靠查询时返回 None。

    Windows:按盘符查询系统回收站;
    macOS  :家目录卷共用 ~/.Trash;
    Linux  :仅当样本与家目录同设备时可对应到 ~/.local/share/Trash
            (临时目录常为独立 tmpfs,此时返回 None)。
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class SHQUERYRBINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("i64Size", ctypes.c_int64),
                    ("i64NumItems", ctypes.c_int64),
                ]

            info = SHQUERYRBINFO()
            info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
            drive = os.path.splitdrive(os.path.abspath(sample_path))[0] + "\\"
            if ctypes.windll.shell32.SHQueryRecycleBinW(drive, ctypes.byref(info)) != 0:
                return None
            return info.i64NumItems
        if sys.platform == "darwin":
            return len(os.listdir(os.path.expanduser("~/.Trash")))
        home = os.path.expanduser("~")
        if os.lstat(os.path.abspath(sample_path)).st_dev != os.lstat(home).st_dev:
            return None
        base = os.environ.get("XDG_DATA_HOME", "")
        if not (base and os.path.isabs(base)):
            base = os.path.join(home, ".local", "share")
        return len(os.listdir(os.path.join(base, "Trash", "files")))
    except Exception:  # noqa: BLE001
        return None


def test_core(root):
    print("\n[1] 核心逻辑")

    # 补充后缀
    base = os.path.join(root, "add_ext", "sub")
    for name in ("12345", "678", "hello", "999.txt"):
        make(os.path.join(base, name))
    params = {"directory": os.path.join(root, "add_ext"), "ext": ".zip", "recursive": "1"}
    actions = REGISTRY["add_ext"].run(params, log, dry_run=True)
    check("补充后缀 / 预览只读", len(actions) == 2 and not os.path.exists(os.path.join(base, "12345.zip")))
    REGISTRY["add_ext"].run(params, log, dry_run=False)
    names = sorted(os.listdir(base))
    check("补充后缀 / 执行", names == ["12345.zip", "678.zip", "999.txt", "hello"], str(names))

    # 修改后缀
    folder = os.path.join(root, "change")
    for name in ("a.7zz", "b.7zz", "c.7z"):
        make(os.path.join(folder, name))
    REGISTRY["change_ext"].run(
        {"directory": folder, "old_ext": ".7zz", "new_ext": ".7z", "recursive": ""}, log, dry_run=False
    )
    check("修改后缀", sorted(os.listdir(folder)) == ["a.7z", "b.7z", "c.7z"], str(sorted(os.listdir(folder))))

    # 清理文件名字符串
    folder = os.path.join(root, "strip")
    make(os.path.join(folder, "sub", "watermark@videoA.mp4"))
    make(os.path.join(folder, "watermark@videoB.mkv"))
    REGISTRY["strip_string"].run(
        {"directory": folder, "target_string": "watermark@", "recursive": "1"}, log, dry_run=False
    )
    names = sorted(os.listdir(folder)) + sorted(os.listdir(os.path.join(folder, "sub")))
    check("清理文件名字符串 / 递归", "videoA.mp4" in names and "videoB.mkv" in names, str(names))

    # 按后缀删除(移入系统回收站)
    folder = os.path.join(root, "delete")
    for name in ("keep.txt", "kill.7z", "kill2.7z"):
        make(os.path.join(folder, name))
    before = recycle_bin_count(folder)
    REGISTRY["delete_ext"].run(
        {"directory": folder, "ext": ".7z", "recursive": "", "mode": "移入系统回收站(推荐)"}, log, dry_run=False
    )
    after = recycle_bin_count(folder)
    gone = sorted(os.listdir(folder)) == ["keep.txt"]
    if before is not None and after is not None:
        # 能枚举回收站:必须严格校验文件确实进了回收站(计数 +2)
        ok, extra = gone and after == before + 2, f"回收站计数 {before} → {after}"
    else:
        # 无法枚举回收站(如 Linux 临时目录在独立 tmpfs):
        # 退化为校验文件已离开原目录,且未创建隐藏文件夹
        ok, extra = gone, "无法枚举回收站,仅校验文件已离开原目录"
    check("按后缀删除 / 移入系统回收站", ok, extra)

    # 批量压缩
    folder = os.path.join(root, "compress")
    for name in ("one.txt", "two.txt"):
        make(os.path.join(folder, name), b"hello" * 100)
    REGISTRY["compress"].run(
        {
            "directory": folder,
            "ext_filter": ".txt",
            "fmt": "zip",
            "naming": "追加后缀(影片.mp4 → 影片.mp4.zip)",
            "recursive": "",
            "delete_src": "",
        },
        log,
        dry_run=False,
    )
    check("批量压缩 zip / 追加命名", "one.txt.zip" in os.listdir(folder), str(sorted(os.listdir(folder))))

    # 批量解压
    folder = os.path.join(root, "extract")
    os.makedirs(folder, exist_ok=True)
    shutil.copy(os.path.join(root, "compress", "one.txt.zip"), folder)
    REGISTRY["extract"].run(
        {
            "directory": folder,
            "ext_filter": ".zip",
            "recursive": "",
            "target_mode": "同名文件夹(推荐)",
            "delete_src": "1",
        },
        log,
        dry_run=False,
    )
    out = os.path.join(folder, "one.txt")
    check("批量解压 zip / 删除原包", os.listdir(out) == ["one.txt"] and not os.path.exists(os.path.join(folder, "one.txt.zip")))

    # 异常输入
    try:
        REGISTRY["add_ext"].run({"directory": os.path.join(root, "缺失目录"), "ext": ".zip"}, log, dry_run=True)
        check("非法目录报错", False)
    except ValueError as exc:
        check("非法目录报错", "目录不存在" in str(exc))
    try:
        REGISTRY["add_ext"].run({"directory": folder, "ext": ""}, log, dry_run=True)
        check("空后缀报错", False)
    except ValueError as exc:
        check("空后缀报错", "后缀" in str(exc))

    # 重名避让
    folder = os.path.join(root, "conflict")
    make(os.path.join(folder, "100"))
    make(os.path.join(folder, "100.zip"))
    REGISTRY["add_ext"].run({"directory": folder, "ext": ".zip", "recursive": ""}, log, dry_run=False)
    check("重名自动避让", "100_1.zip" in os.listdir(folder), str(sorted(os.listdir(folder))))


def test_gui(root):
    print("\n[2] 图形界面")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("  跳过:当前 Python 不支持 tkinter")
        return
    from tidybatch.gui import App

    app = App()
    app.deiconify()
    app.update()
    check("主窗口构建成功", len(REGISTRY) > 0, f"已注册 {len(REGISTRY)} 个功能")

    for key, op in REGISTRY.items():
        try:
            app.ops_tree.selection_set(key)
            app.update()
            params = app._collect()
            missing = [f.key for f in op.fields if f.key not in params]
            check(f"表单渲染 / {op.name}", not missing, f"缺参数 {missing}" if missing else "")
        except Exception as exc:  # noqa: BLE001
            check(f"表单渲染 / {op.name}", False, str(exc))

    # 参数区不应挤压预览区
    app.ops_tree.selection_set("compress")
    app.update()
    check("参数区受高度上限约束", app._form_outer.winfo_height() <= app._form_limit + 4)
    check("预览区可见", app.preview.winfo_height() > 120, f"{app.preview.winfo_height()}px")

    # 进度条:默认必须是"从 0 开始"的确定态,不能有残留填充
    app.ops_tree.selection_set("add_ext")
    app.update()
    check(
        "进度条初始从 0 开始",
        str(app.progress.cget("mode")) == "determinate" and float(app.progress.cget("value")) == 0,
        f"mode={app.progress.cget('mode')} value={app.progress.cget('value')}",
    )

    # 字体必须是整数像素单位(负值),否则字形无法对齐像素网格会发虚
    from tidybatch.gui import FONT, FONT_MONO, FONT_SMALL
    from tidybatch.gui import SCALE as _scale

    check("正文字体为整数像素单位", FONT[1] < 0 and float(FONT[1]).is_integer(), f"{FONT} scale={_scale}")
    check("提示字体为整数像素单位", FONT_SMALL[1] < 0)
    check("日志字体为整数像素单位", FONT_MONO[1] < 0)

    # 窗口图标:应能加载 assets/icon.ico,而不是停留在 tkinter 默认图标
    icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.ico")
    check("图标文件存在", os.path.isfile(icon_path), icon_path)

    # 端到端:界面驱动预览 + 执行
    work = os.path.join(root, "gui_e2e")
    for name in ("111", "222", "keep.txt"):
        make(os.path.join(work, name))
    app.ops_tree.selection_set("add_ext")
    app.update()
    app._vars["directory"].set(work)
    app._vars["ext"].set(".zip")
    app.update()

    app._on_preview()
    pump(app)
    check("界面预览生成结果行", len(app.preview.get_children()) == 2)
    check("预览阶段未改动磁盘", not os.path.exists(os.path.join(work, "111.zip")))
    check("预览后进度条归零", float(app.progress.cget("value")) == 0)

    app._on_execute()
    pump(app)
    check("界面驱动执行成功", "111.zip" in os.listdir(work) and "keep.txt" in os.listdir(work))
    check(
        "执行后进度条推进到总数",
        float(app.progress.cget("value")) == 2,
        f"value={app.progress.cget('value')}",
    )

    app.ops_tree.selection_set("delete_ext")
    app.update()
    check("危险操作按钮提示", app.run_btn.cget("text") == "执行(危险操作)", app.run_btn.cget("text"))

    app.destroy()


def main():
    root = tempfile.mkdtemp(prefix="tidybatch_selftest_")
    print(f"临时测试目录:{root}")
    try:
        test_core(root)
        test_gui(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项,失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项:")
        for name in FAIL:
            print("  -", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
