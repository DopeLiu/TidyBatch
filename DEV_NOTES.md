# TidyBatch 开发笔记

> 本文档面向开发者与维护者，记录项目结构、设计取舍、界面实现细节、扩展方式与踩坑记录。
> 面向使用者的操作说明见 [README.md](README.md)。

---

## 目录

- [1. 目录结构](#1-目录结构)
- [2. 架构设计](#2-架构设计)
- [3. 核心概念](#3-核心概念)
- [4. 执行模型](#4-执行模型)
- [5. 功能参数参考](#5-功能参数参考)
- [6. 界面实现细节](#6-界面实现细节)
- [7. 扩展指南：新增一个功能](#7-扩展指南新增一个功能)
- [8. 安全设计](#8-安全设计)
- [9. 已知陷阱与设计取舍](#9-已知陷阱与设计取舍)
- [10. 回归测试](#10-回归测试)
- [11. 后续可扩展方向](#11-后续可扩展方向)
- [12. 设计原则速查](#12-设计原则速查)

---

## 1. 目录结构

```
TidyBatch/
├── app.py                      启动入口(含 tkinter 缺失时的降级提示)
├── README.md                   使用说明(面向使用者)
├── DEV_NOTES.md                本文档(面向开发者)
│
├── tidybatch/                主包
│   ├── __init__.py             包说明与版本号
│   ├── __main__.py             支持 python -m tidybatch
│   ├── core.py                 核心层:抽象与工具(无 GUI 依赖)
│   ├── operations.py           功能层:6 个具体功能的实现
│   └── gui.py                  界面层:tkinter 窗口
│
├── assets/
│   ├── icon.ico                图标(多尺寸 16/32/48/64/256)
│   ├── icon.icns               macOS 程序图标(.app 包使用)
│   └── icon.png                macOS 备用图标(窗口图标回退分支读取)
│
├── tools/
│   └── build_exe.py            打包脚本(Windows exe / macOS .app)
│
├── packaging/
│   └── 首次打开说明.txt        macOS 发行包内附的用户说明
│
├── .github/workflows/
│   └── build-macos.yml         macOS 安装包自动构建流水线(云端)
│
├── tests/
│   └── selftest.py             回归自测
│
├── dist/                       打包产物(构建时生成,可删除)
└── build/                      PyInstaller 临时目录(构建后自动清理)
```

**三层职责边界(务必保持):**

| 层 | 文件 | 允许做什么 | 禁止做什么 |
|---|---|---|---|
| 核心层 | `core.py` | 定义抽象、提供文件操作工具函数 | 导入 tkinter、弹窗、读用户输入 |
| 功能层 | `operations.py` | 实现具体批量功能 | 导入 tkinter、直接操作界面 |
| 界面层 | `gui.py` | 展示、收集参数、调度、显示日志 | 直接读写文件(必须委托给 Operation) |

`core.py` + `operations.py` 不含任何 GUI 代码,因此可以被脚本、定时任务、测试直接复用 —— 例如:

```python
from tidybatch.core import REGISTRY

REGISTRY["change_ext"].run(
    {"directory": r"D:\downloads", "old_ext": ".7zz", "new_ext": ".7z", "recursive": ""},
    log=print,
    dry_run=False,
)
```

---

## 2. 架构设计

### 2.1 整体结构

```
                        ┌──────────────────────────────────┐
                        │           gui.py (界面层)         │
                        │                                  │
   用户操作 ───────────▶ │  侧边栏   参数表单   预览表  日志 │
                        │    │         │        │      ▲     │
                        └────┼─────────┼────────┼──────┼─────┘
                             │         │        │      │
                     选择功能│  收集参数│  填充  │      │日志/进度
                             ▼         ▼        │      │(队列)
                        ┌──────────────────────────────────┐
                        │     核心层 core.py (抽象 + 工具)  │
                        │  Operation.run() ── 计划/执行流程 │
                        │  REGISTRY ── 功能注册表           │
                        └──────────────┬───────────────────┘
                                       │ 遍历注册表
                        ┌──────────────▼───────────────────┐
                        │    功能层 operations.py (6 个)    │
                        │  AddExt / ChangeExt / StripString │
                        │  DeleteExt / Compress / Extract   │
                        └──────────────┬───────────────────┘
                                       │
                                       ▼
                                  磁盘文件系统
```

### 2.2 关键机制:注册表驱动

这是整个项目最重要的设计。界面**不硬编码任何功能**,而是遍历注册表动态生成:

```
operations.py 中的 @register 装饰器
        │
        ▼
   REGISTRY = { "add_ext": AddExtOp(), "change_ext": ..., ... }
        │
        ├──▶ categories() ──▶ 侧边栏按 category 分组生成
        │
        └──▶ op.fields    ──▶ 参数表单按 Field 声明自动渲染
```

**直接收益:新增功能时,`gui.py` 一个字都不用改。** 这是"为后续升级预留空间"的具体落实方式。

---

## 3. 核心概念

### 3.1 `Field` —— 参数描述

```python
@dataclass
class Field:
    key: str                      # 参数名,对应 params[key]
    label: str                    # 界面显示名
    kind: str = "text"            # text | dir | choice | check
    default: str = ""             # 默认值
    hint: str = ""                # 输入框下方的灰色提示
    choices: Sequence[str] = ()   # kind == "choice" 时的候选项
```

支持的控件类型:

| `kind` | 渲染出的控件 | 传值形式 | 备注 |
|---|---|---|---|
| `text` | 单行输入框 | 字符串 | 默认类型 |
| `dir` | 输入框 + 「浏览…」按钮 | 路径字符串 | 调用系统目录选择对话框 |
| `choice` | 只读下拉框 | 选项字符串 | 必须提供 `choices` |
| `check` | 复选框 | `"1"` 或 `""` | 用 `is_on(params, key)` 解析为布尔值 |

> `check` 类型不使用布尔值传递,而是统一的字符串 —— 因为界面的所有控件值都通过 `str` 汇总,
> 保持参数类型一致能避免大量类型判断。用 `is_on()` 解析即可。

### 3.2 `Action` —— 一条变更

`Action` 是纯粹的**数据**,自身不执行任何操作。

```python
@dataclass
class Action:
    src: str      # 源路径(绝对路径)
    dst: str = "" # 目标路径;删除类操作为空
    verb: str = "重命名"   # 动作类型,用于界面展示
    note: str = ""         # 备注,如 "纯数字文件名"
```

`plan()` 返回 `List[Action]`,界面拿它填预览表;`apply()` 逐条执行。

### 3.3 `Operation` —— 功能基类

子类必须提供:

- **类属性**:`key`(唯一标识)、`name`(显示名)、`category`(侧边栏分组)、`description`(参数区顶部说明)、`fields`(参数声明)
- **`plan(params, log) -> List[Action]`** —— 只读扫描,禁止修改磁盘
- **`apply(action, params, log) -> None`** —— 执行单条变更

子类可选覆盖:

- **`destructive = True`** —— 标记为危险操作。界面会把执行按钮变为警示红,并在执行前弹出强化确认。

### 3.4 常用工具函数(`core.py`)

| 函数 | 作用 |
|---|---|
| `require_dir(path)` | 校验并规范化目录参数,非法时抛出中文 `ValueError` |
| `iter_files(dir, recursive)` | 遍历目录中的文件(不含目录本身) |
| `parse_exts(text)` | 解析后缀输入,兼容逗号/顿号/分号,自动补 `.` 并小写 |
| `unique_path(path)` | 目标重名时自动追加 `_1`/`_2`,**避免静默覆盖** |
| `is_on(params, key)` | 解析复选框值 |
| `ensure_dir(path)` | 递归创建目录 |

---

## 4. 执行模型

### 4.1 计划 / 执行两阶段

这是保证安全的核心机制。**预览和执行走的是同一套扫描代码**,因此预览结果与实际执行必然一致。

```
          ┌─────────────────────────────────────────┐
          │  用户点击「预览计划」                     │
          └────────────────┬────────────────────────┘
                           ▼
                  op.plan(params, log)          ← 只读,不碰磁盘
                           │
                           ▼
                    List[Action]  ──────────▶  填入预览表
                           │
          ┌────────────────▼────────────────────────┐
          │  用户核对无误,点击「执行」并确认         │
          └────────────────┬────────────────────────┘
                           ▼
                  op.plan(params, log)          ← 重新扫描,确保状态最新
                           │
                           ▼
                  for each action:
                      op.apply(action, params, log)   ← 真正改动磁盘
                           │
                           ├─ 成功 → ok += 1
                           └─ 异常 → fail += 1(不中断整批)
                           │
                           ▼
                  日志汇总:成功 N 项,失败 M 项
```

`Operation.run()` 是统一的执行入口,签名如下:

```python
def run(self, params, log, dry_run=True, should_stop=None, on_progress=None) -> List[Action]:
    """
    dry_run=True   仅扫描并返回变更清单(预览),磁盘不做任何改动
    dry_run=False  逐条执行,单条失败不中断整批,最后汇总成功/失败数量
    should_stop    返回 True 时中止(用于「中止」按钮)
    on_progress    每处理完一条回调 (done, total),用于驱动进度条
    """
```

**为什么执行前要重新 `plan()` 一次?** 因为从预览到点击执行之间可能过了几秒,磁盘状态可能已经变化。
重新扫描可以保证操作针对的是当前真实状态,避免"照着过期清单操作"。

### 4.2 线程模型

批量操作可能持续很久,不能阻塞界面线程。因此:

```
   主线程 (Tk 事件循环)                工作线程 (daemon)
   ────────────────────                ──────────────────
   _start_task()
        └── threading.Thread ────────▶ op.run(...)
                                          │
   _drain_queue()  ◀──── queue.Queue ─────┤ ("log",      (level, message))
   (每 80ms 轮询一次)                      ├ ("progress", (done, total))
        │                                 ├ ("plan",     (dry_run, actions))
        ├─▶ 追加日志                       └ ("done",     None)
        ├─▶ 更新进度条
        ├─▶ 填充预览表
        └─▶ 恢复按钮状态
```

**约定:工作线程绝不直接操作控件。** 它只往 `queue.Queue` 里投递消息,由主线程的
`_drain_queue()` 轮询取出后再更新界面。这是 Tk 多线程编程的基本纪律 ——
跨线程操作控件会导致随机的崩溃或界面错乱。

---

## 5. 功能参数参考

### 5.1 重命名类

#### 补充文件后缀 —— `add_ext`

为「文件名完全由数字组成且没有扩展名」的文件补上指定后缀。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 目标目录 | dir | — | |
| 要补充的后缀 | text | `.zip` | 无需输入前导点,程序会自动补 |
| 包含子目录 | check | 关 | |

匹配规则严格:文件名必须是**纯数字**(`str.isdigit()`)。`999.txt`、`hello` 都不会被处理。

#### 修改文件后缀 —— `change_ext`

替换扩展名,不改文件名主体。

| 参数 | 类型 | 默认 |
|---|---|---|
| 目标目录 | dir | — |
| 原后缀 | text | `.7zz` |
| 新后缀 | text | `.7z` |
| 包含子目录 | check | 关 |

原后缀与新后缀相同时会直接报错(无需处理)。后缀比较不区分大小写。

#### 清理文件名中的字符 —— `strip_string`

递归扫描,删除文件名中包含的指定字符串,用于批量去除推广水印。

| 参数 | 类型 | 默认 |
|---|---|---|
| 目标目录 | dir | — |
| 要删除的字符串 | text | — |
| 包含子目录 | check | **开** |

若删完后文件名为空(整个文件名就是水印),则跳过该文件,不会产生无名文件。

### 5.2 删除类

#### 按后缀删除文件 —— `delete_ext` ⚠️

标记为 `destructive`,执行按钮会变为警示红。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 目标目录 | dir | — | |
| 要删除的后缀 | text | — | 多个用逗号分隔,如 `.7z,.zip` |
| 包含子目录 | check | 关 | |
| 删除方式 | choice | 移入系统回收站(推荐) | 另一选项为「直接删除(不可恢复)」 |

**默认的"回收站"模式**按平台调用系统能力(实现见 `core.send_to_recycle_bin()`),
均不依赖第三方库,文件可随时还原:

| 平台 | 途径 |
|---|---|
| Windows | Shell API `SHFileOperationW`(`FO_DELETE` + `FOF_ALLOWUNDO`) |
| macOS | Objective-C 运行时调用 `NSFileManager trashItemAtURL:`(等价废纸篓删除) |
| Linux | 按 FreeDesktop 回收站规范内置实现(家目录卷 `$XDG_DATA_HOME/Trash`,其他卷 `.Trash/$uid`);异常时退回 `gio trash` / `trash-put` 命令 |

选择「直接删除」则调用 `os.remove`,不可恢复。

### 5.3 压缩 / 解压类

#### 批量压缩 —— `compress`

把目录下每个符合条件的文件**各自**打包成一个独立压缩包。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 目标目录 | dir | — | |
| 只处理这些后缀 | text | 空 | 留空 = 全部;多个用逗号分隔 |
| 压缩格式 | choice | `zip` | 可选 `zip` / `7z` |
| 压缩包命名 | choice | 追加后缀 | 见下表 |
| 包含子目录 | check | 关 | |
| 压缩成功后删除原文件 | check | 关 | |

命名方式对比:

| 方式 | 效果 | 适用场合 |
|---|---|---|
| 追加后缀 | `影片.mp4` → `影片.mp4.zip` | 保留原扩展名信息(默认) |
| 替换后缀 | `影片.mp4` → `影片.zip` | 重新打包,如 `.7zz` → `.7z` |

> 替换模式下有一个兜底保护:若目标名与源文件同名(即源文件本身已是该格式,例如把 `a.zip`
> 替换为 `a.zip`),会强制改为追加模式,避免**边读边覆盖自己**。

zip 由 Python 内置 `zipfile` 实现(压缩级别 6);7z 调用外部 7-Zip。

#### 批量解压 —— `extract`

扫描目录中的压缩包并批量解压。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 目标目录 | dir | — | |
| 压缩包后缀 | text | `.zip,.7z,.7zz,.rar,.tar,.gz` | 多个用逗号分隔 |
| 包含子目录 | check | 关 | |
| 解压位置 | choice | 同名文件夹(推荐) | 另可选「当前目录」「统一输出到 _extracted」 |
| 解压成功后删除原压缩包 | check | 关 | |

**格式分发不依赖文件后缀,而是嗅探真实格式**(`zipfile.is_zipfile` / `tarfile.is_tarfile`),
因此后缀写错的压缩包也能正确解压。zip / tar 系列由内置库处理,其余交给 7-Zip。

`_safe_extract_zip()` 会拦截含 `..` 或绝对路径的压缩包条目(路径穿越攻击);
tar 使用 Python 3.12+ 的 `filter="data"` 安全过滤。

---

## 6. 界面实现细节

### 6.1 布局

```
┌──────────────────────────────────────────────────────────────┐
│ 批量文件工具箱   预览确认 · 后台执行 · 全程日志               │
├────────────┬─────────────────────────────────────────────────┤
│ 功能列表    │ 功能名称                                         │
│            │ 功能说明(随窗口宽度自动换行)                    │
│ ▾ 重命名    │  目标目录   [_______________] [浏览…]            │
│   补充后缀  │  参数名     [_______________]                    │
│   修改后缀  │             灰色提示文字                          │
│   清理字符  │  ☑ 包含子目录                                    │
│ ▾ 删除     ├─────────────────────────────────────────────────┤
│   按后缀删除│ 变更预览                        共 N 项           │
│ ▾ 压缩/解压 │  操作 │ 源文件 │ 目标 / 结果                    │
│   批量压缩  │                                                  │
│   批量解压  ├─────────────────────────────────────────────────┤
│            │ 操作日志              [清空日志] [保存日志]       │
│            │ [12:03:41] 开始执行,共 42 项 …                   │
│            │ [12:03:42] 已移入系统回收站:xxx.7z               │
├────────────┴─────────────────────────────────────────────────┤
│ [预览计划] [执行] [中止]   ████░░░░░░░░  执行中 12/42 (28%)   │
└──────────────────────────────────────────────────────────────┘
```

**布局自适应要点:**

- 参数区高度随内容自动增长,但**上限 400×SCALE px**;超过则内部出现滚动条。
  这样保证参数再多的功能也不会把预览区挤没。
- 上下分栏(工作区 / 日志区)可拖动,初始比例约 72 : 28。
- 窗口尺寸按屏幕分辨率自适应,小屏也能完整显示。

### 6.2 DPI 与字体 —— 为什么文字会发虚

这是本项目排查成本最高的问题,结论值得记录。

**现象**:界面文字看起来比原生 Windows 程序模糊。

**根因(两个叠加因素):**

1. **进程默认是 DPI-unaware。** 在非 100% 缩放的显示器上(本机为 125%),
   Windows 会把整个窗口做**位图拉伸**,文字被重采样后必然发虚。
2. **点数字号被折算成小数像素。** Tk 的 `tk scaling` 为 1.333,`10pt` 实际换算为
   `13.33px` 这种非整数尺寸,字形无法对齐像素网格,进一步加重模糊。

**解决办法:**

```python
# 1. 必须在创建 Tk 根窗口之前声明 DPI 感知
_enable_dpi_awareness()      # SetProcessDpiAwareness(2) = PER_MONITOR_DPI_AWARE

# 2. 读取真实 DPI,计算全局缩放比
_init_metrics()              # SCALE = GetDpiForSystem() / 96
```

之后:

- **所有字号使用整数像素单位(负值)**,例如 `("Microsoft YaHei UI", -19)`。
  负值在 Tk 中表示"以像素为单位",而非点值。
- **所有像素尺寸经 `S(px)` 统一缩放**:`S(400)` 在 125% 下等于 500。
- **提高文字对比度**。低对比度的灰色文字在深色背景上会被感知为"模糊",
  这属于视觉心理效应而非渲染问题。次要文字色从 `#8e94a1` 提升到 `#a8aebc`。

| 诊断陷阱 | 说明 |
|---|---|
| `GetDpiForSystem()` 返回 96 | 在 DPI-unaware 进程中返回的是**虚拟化值**,会让人误判为 100% 缩放。必须**先声明 DPI 感知再读**,才得到真实的 120。 |
| 截图看起来正常 | 用 DPI-unaware 进程截图,取到的是虚拟化坐标,只能截到窗口的一部分并被缩放,反而看不出问题。 |

验证方法:截图后放大 3 倍观察文字边缘。若呈现**彩色次像素条纹(ClearType)**即为原生渲染;
若只有灰色过渡带,说明仍被位图拉伸。

### 6.3 窗口图标

查找顺序(相对项目根目录):

```
assets/icon.ico  →  icon.ico  →  assets/icon.png  →  icon.png  →  都找不到则不设置,显示 tkinter 默认图标
```

**换图标:把文件放到上述任一位置即可,无需改代码。** 推荐 `.ico`
(多尺寸内嵌,标题栏和任务栏都清晰);`.png` 也可以,但只提供一个尺寸。

`assets/icon.ico` 内含 16/32/48/64/256 五种尺寸;macOS 用的 `icon.icns` / `icon.png`
由其一次性转换而来,均为静态资源,不需要重新生成。

### 6.4 进度条

必须显示**真实进度**而非装饰性动画,因此使用 `mode="determinate"`:

| 阶段 | 进度条状态 |
|---|---|
| 空闲 | 确定态,`value=0`,**完全无填充** |
| 扫描 / 预览 | 不确定态,来回移动的动画(此时无法预知总数) |
| 执行 | 确定态,按 `已完成/总数` 推进,状态栏同步显示百分比 |
| 执行完成 | 停在 100%,保留终态 |

驱动链路:`Operation.run(on_progress=...)` → 工作线程投递 `("progress", (done, total))`
→ 主线程 `_progress_update()` 更新控件。

### 6.5 参数记忆

程序会维护 `.tidybatch_settings.json`(源码运行:项目根目录;打包运行:Windows 为 exe 同级目录,
macOS 为 `~/Library/Application Support/TidyBatch`,原因见 §9.4),记录:

- 每个功能上次填写的参数
- 上次使用的功能(下次启动自动选中)

删除该文件即可恢复全部默认值。它属于运行时状态,可以安全地加入 `.gitignore`。

---

## 7. 扩展指南：新增一个功能

**这是本文档最实用的部分。** 新增功能只需要在 `tidybatch/operations.py`
末尾追加一个类,无需改动 `gui.py`。

下面以一个实际可用的例子演示 —— 「按文件名关键词批量重命名」:

```python
@register
class RenameByPatternOp(Operation):
    key = "rename_pattern"                 # 唯一标识,不可重复
    name = "按关键词重命名"                 # 显示在侧边栏
    category = "重命名"                     # 侧边栏分组(可复用已有分类)
    description = (
        "在文件名前/后批量添加前缀或后缀。\n"
        "例如给所有 .mp4 文件加上「[已整理]」前缀。"
    )
    destructive = False                    # 危险操作则设为 True

    fields = (
        Field("directory", "目标目录", "dir"),
        Field("ext_filter", "只处理这些后缀", "text", ".mp4", "留空=全部;多个用逗号分隔"),
        Field("prefix", "添加前缀", "text", "", "留空则不添加"),
        Field("suffix", "添加后缀", "text", ""),
        Field("recursive", "包含子目录", "check", ""),
    )

    def plan(self, params, log):
        """只读扫描:返回将要执行的变更清单。绝对不要在这里改文件。"""
        directory = require_dir(params.get("directory", ""))   # 校验 + 规范化
        prefix = params.get("prefix", "")
        suffix = params.get("suffix", "")
        filters = parse_exts(params.get("ext_filter", ""))

        if not prefix and not suffix:
            raise ValueError("请至少填写前缀或后缀之一。")      # 中文错误会直接显示给用户

        actions = []
        for path in iter_files(directory, is_on(params, "recursive")):
            if filters and split_ext(path)[1].lower() not in filters:
                continue
            head, ext = split_ext(path)
            new_head = os.path.join(os.path.dirname(head), prefix + os.path.basename(head) + suffix)
            actions.append(
                Action(
                    src=path,
                    dst=unique_path(new_head + ext),     # 重名自动避让
                    verb="重命名",
                    note=f"加前缀「{prefix}」后缀「{suffix}」",
                )
            )
        return actions

    def apply(self, action, params, log):
        """执行单条变更。抛出的异常会被 run() 捕获并计入失败数。"""
        os.rename(action.src, action.dst)
        log("ok", f"{os.path.basename(action.src)} → {os.path.basename(action.dst)}")
```

保存后直接运行 `python app.py`,侧边栏的「重命名」分组下就会出现新功能,表单自动生成。

### 扩展检查清单

- [ ] `key` 全局唯一,不与已有功能冲突
- [ ] `plan()` 中**没有**任何写操作(这是硬性要求)
- [ ] 所有目标路径都经过 `unique_path()`,不静默覆盖
- [ ] 目录参数用 `require_dir()` 校验,错误信息用中文
- [ ] 删除类操作考虑是否应标记 `destructive = True`
- [ ] 在 `tests/selftest.py` 中补一条断言
- [ ] 运行 `python tests/selftest.py` 确认全绿

### 完全新增一个分类

`category` 填一个新字符串即可,侧边栏会自动多出一个分组,无需注册。

---

## 8. 安全设计

这个工具会真实修改磁盘,因此安全机制是设计重点。

| 机制 | 实现位置 | 说明 |
|---|---|---|
| **预览后执行** | `Operation.run()` | 所有变更先以只读方式列成清单供人工核对 |
| **危险操作强化确认** | `gui.py` | `destructive=True` 的功能按钮变红,确认弹窗明确列出删除方式与数量 |
| **删除可恢复** | `DeleteExtOp` | 默认移入系统回收站,而非直接删除 |
| **禁止静默覆盖** | `core.unique_path()` | 目标重名时自动追加 `_1`/`_2`,绝不覆盖已有文件 |
| **路径穿越防护** | `_safe_extract_zip()` | 拒绝解压含 `..` 或绝对路径的条目 |
| **tar 安全过滤** | `_extract()` | 使用 Python 3.12+ 的 `filter="data"` |
| **单条失败不中断** | `Operation.run()` | 某条出错只记录并继续,最后汇总失败清单 |
| **后台线程隔离** | `gui.py` | 界面不冻结,可随时中止 |
| **零第三方依赖** | 全项目 | 不存在供应链风险,也不会有依赖版本问题 |

**一条纪律**:任何会修改磁盘的逻辑都必须写在 `apply()` 里,并且只能通过
`Operation.run(dry_run=False)` 触发。禁止在 `plan()` 中修改任何文件 ——
否则"预览"按钮会变成破坏性操作。

---

## 9. 已知陷阱与设计取舍

开发过程中实际踩过并修复的问题,记录下来避免重蹈覆辙。

### 9.1 tkinter 相关

| 陷阱 | 现象 | 解决 |
|---|---|---|
| **`ttk.Progressbar.stop()` 会清空 value** | 执行完成后调用 `stop()`,已达成的 100% 被复位为 0 | 封装 `_set_progress_mode()`,只在「从不确定态切走」时调用 `stop()` |
| **自定义 Button 子类透传参数** | `configure(hover=...)` 把自定义参数传给 `tk.Button`,抛 `TclError: unknown option "-hover"` | 改用独立方法 `set_colors()`,不走 `configure` |
| **grid 行号推算易错** | 提示文字与下一字段的输入框占用同一网格,相互重叠 | 每个字段渲染为独立的 `Frame` 分组,彻底不用行号推算 |
| **clam 主题的浅色描边** | Treeview / Combobox 在深色主题下带浅灰边框 | 显式设置 `bordercolor` / `lightcolor` / `darkcolor` |
| **Canvas 内嵌 Frame 的宽度** | 表单随窗口拉伸时内容宽度不同步 | 绑定 `<Configure>` 事件,把 canvas 宽度同步给内部 window item |
| **`iconbitmap` 只设 default 不生效** | 当前窗口仍是默认图标 | 先 `iconbitmap(path)` 再 `iconbitmap(default=path)` |

### 9.2 文件操作相关

| 陷阱 | 说明 |
|---|---|
| **压缩时自我覆盖** | 替换命名模式下,若源文件本身已是目标格式,会导致边读边写同一文件。已加兜底判断强制改为追加命名。 |
| **`.tar.gz` 双后缀** | `os.path.splitext` 只能切出 `.gz`。解压过滤时额外做了一次整名后缀匹配。 |
| **扩展名比较大小写** | Windows 不区分大小写,但字符串比较区分。所有后缀比较统一 `.lower()`。 |
| **中文路径与编码** | 全程使用 `str` 路径并显式 `encoding="utf-8"` 读写配置,避免 GBK 环境下的乱码。 |

### 9.3 关于 tkinter 的技术取舍

| 决策 | 理由 | 代价 |
|---|---|---|
| 选择 tkinter 而非 PyQt / 现代 GUI | 标准库自带,零依赖,打包体积小 | 样式能力弱,需要手工调主题 |
| 放弃点数字号,改用像素字号 | 避免小数像素导致的字形模糊 | 需要 `S()` 统一缩放,代码略繁琐 |
| 自绘控件(FlatButton)而非 ttk | 深色主题下 ttk 按钮难以完全控制配色 | 需要自行实现 hover / disabled 状态 |

### 9.4 打包(PyInstaller / frozen)相关

打包脚本 `tools/build_exe.py` 已固化全部必要参数,Windows 与 macOS 通用,常用命令:

```bash
pip install pyinstaller            # 一次性准备(打包工具本身是第三方依赖,不影响程序运行)

python tools/build_exe.py          # Windows:单文件,产物 dist/TidyBatch.exe
python tools/build_exe.py -d       # Windows:文件夹模式(启动更快,少了自解压开销)
python tools/build_exe.py -k       # 保留 build 临时目录,便于排查打包问题
```

| 模式 | 产物 | 特点 |
|---|---|---|
| Windows 单文件(默认) | `dist/TidyBatch.exe` | 约 11 MB,单文件易分发;启动需自解压,首启约 1–2 秒 |
| Windows 文件夹(`-d`) | `dist/TidyBatch/TidyBatch.exe` | 整体目录需一起拷贝,启动更快 |
| macOS(固定文件夹模式) | `dist/TidyBatch.app` | 双击即用;用 universal2 解释器构建时自动产出"Apple 芯片 + Intel"通用版 |

脚本中三项参数值得注意:

1. `--windowed` —— GUI 程序:Windows 下不弹控制台黑窗,macOS 下产出 `.app` 包;
2. `--icon` + `--add-data` —— 图标既是程序文件图标(Windows 用 `.ico`,macOS 用 `.icns`),
   也打入内部供界面启动时读取;
3. `--exclude-module` —— 排除 numpy / pandas / PyQt 等未被使用的大体积库,防止体积膨胀。

**配置文件的落点**:打包后 `.tidybatch_settings.json` 生成在 **exe 同级目录**(便携式);
macOS 例外 —— 写入 `~/Library/Application Support/TidyBatch`。原因是 `.app` 包内部不可写:
未签名应用可能被系统从只读的随机路径启动(Gatekeeper 的转移运行机制),配置写进包内会
表现为"设置永远保存不上"。程序通过 `resource_dir()` / `data_dir()` 区分「只读资源」与
「可写数据」,绝不会把配置写进 PyInstaller 的临时解包目录(否则单文件模式退出即丢失)。

**macOS 打包与分发**(要点):

- 无法在 Windows 上交叉打包,Mac 版由 `.github/workflows/build-macos.yml` 在云端构建
  (公开仓库的 macOS 构建机免费):装 python.org 的 universal2 解释器 → 打包 → 校验架构与签名
  → 回归测试 → 生成 zip / dmg;
- 产物为 `.zip`(ditto 压缩,保留符号链接与权限;**禁止用 Windows 工具二次压缩**,否则
  用户端报"已损坏")与 `.dmg`(hdiutil,内含「应用程序」快捷方式),两者均附《首次打开说明》;
- 未做付费签名与公证(无 Apple 开发者账号),用户首次打开需按说明在
  「系统设置 → 隐私与安全性」点「仍要打开」(macOS 15 起,右键打开的绕过方式已被取消);
- **签名后禁止再改动 `.app` 包内文件**,否则用户端提示"已损坏"且无法自行修复;
- 打 `v*` 标签时流水线自动创建 Release 并附上 zip / dmg 两个安装包。

打包需用**含 tkinter 的解释器**;新增资源文件时,必须同步在 `tools/build_exe.py` 里补 `--add-data`,
否则打包后读不到。

常见陷阱:

| 陷阱 | 现象 | 解决 |
|---|---|---|
| **`__file__` 在打包后指向临时解包目录** | 用 `dirname(dirname(__file__))` 推导出的"项目根"实为 `sys._MEIPASS`;配置写进去后,单文件模式一退出就被清理,表现为"设置永远不保存" | 拆成 `resource_dir()`(只读资源,指向 `_MEIPASS`)与 `data_dir()`(可写数据,指向 `sys.executable` 所在目录),两者都在 `gui.py` 内根据 `sys.frozen` 判断 |
| **图标需要显式打入** | 只加 `--icon` 仅有 exe 文件图标,界面运行时的 `iconbitmap` 仍找不到文件,会显示 tkinter 默认图标 | `--add-data "assets/icon.ico;assets"`,`resource_dir()` 保证能定位到 |
| **`--windowed` 后看不到报错** | 未加 `--windowed` 会弹控制台黑窗;加了之后启动异常时无任何输出,难以排查 | 排查时改用文件夹模式(`-d -k`)直接运行包内 exe,或临时去掉 `--windowed` |

---

## 10. 回归测试

```bash
python tests/selftest.py
```

当前 **30 项断言**,覆盖两个层面:

**核心逻辑层(在系统临时目录中真实读写文件,结束后自动清理,不触碰用户数据):**

- 补充后缀 —— 预览阶段只读、执行结果正确、非纯数字文件不被误伤
- 修改后缀 —— 只替换扩展名,不匹配的文件保持不变
- 清理字符串 —— 递归遍历子目录生效
- 按后缀删除 —— 文件确实被移入系统回收站(可枚举回收站的平台对比计数前后变化,其余平台校验文件已离开原目录)
- 批量压缩 —— 追加命名生成 `one.txt.zip`
- 批量解压 —— 正确展开内容并删除原包
- 异常输入 —— 非法目录、空后缀均抛出中文错误
- 重名避让 —— 目标已存在时生成 `100_1.zip`

**界面层(驱动真实控件完成端到端流程):**

- 主窗口构建成功
- 每个功能的表单都能正确渲染并采集到全部参数
- 参数区不超出高度上限,预览区未被挤压
- **进度条初始从 0 开始 / 预览后归零 / 执行后推进到总数**
- 字体确为整数像素单位(防止有人改回点数字号导致重新发虚)
- 图标文件存在
- 通过界面完成「预览 → 执行」全流程,且预览阶段不修改磁盘
- 危险操作的按钮正确切换为警示态

> 值得强调的是「执行后进度条推进到总数」这条断言 ——
> 它当时直接抓出了一个隐蔽缺陷:执行完成后的一行 `stop()` 把已达成的 100% 清空了。
> 这类问题靠肉眼看界面很难发现,但断言能立刻暴露。

若当前 Python 不含 tkinter,或环境未提供图形会话(CI 中设 `TIDYBATCH_SKIP_GUI=1`,
如 macOS 构建流水线——无图形会话时界面初始化会阻塞),界面层测试会自动跳过,只跑核心逻辑层。

---

## 11. 后续可扩展方向

按价值排序,供后续迭代参考:

1. **配置文件驱动的批量流水线** —— 把「补后缀 → 解压 → 删原包」编排成一个可保存的方案,
   一键执行。核心层已具备条件(所有功能都是统一签名的 `Operation`)。
2. **命令行入口** —— 基于同一套 `REGISTRY` 增加 `argparse` 入口,便于脚本化和定时任务调用。
3. **更多功能**:按正则批量重命名、重复文件查找、按大小/日期筛选、文件内容去重(哈希)。
4. **预览表支持排序与导出** —— 目前预览表不可排序,大批量时可考虑导出为 CSV 供外部核对。
5. **操作撤销** —— 目前只有删除可恢复。可考虑把每次执行的 Action 清单落盘,支持整批回滚。
6. **国际化** —— 目前界面文案全部硬编码中文,若需多语言可抽出文案表。

> 打包为独立程序的目标已实现(Windows `.exe` / macOS `.app`),见 README 的「打包」一节与
> 本节第 9.4 节。

---

## 12. 设计原则速查

1. **逻辑与界面严格分离** —— 核心层可脱离界面独立运行。
2. **注册表驱动** —— 新功能通过新增类实现,而非修改界面代码。
3. **预览即执行** —— 预览和执行共用同一套扫描逻辑,结果必然一致。
4. **绝不静默覆盖** —— 所有路径冲突都显式避让。
5. **破坏性操作默认保守** —— 删除默认走可恢复路径,危险功能强制二次确认。
6. **失败不扩大** —— 单条出错只记录,不中断整批。
