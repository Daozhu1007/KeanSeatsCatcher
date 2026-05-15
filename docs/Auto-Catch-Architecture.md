# Auto-Catch 模式集成架构说明书

> **状态**: 草案 — 待评审
> **日期**: 2026-05-16
> **约束**: 严禁修改 `core_api.py` 现有发包逻辑；未经明确批准不得改动任何代码文件。

---

## 目录

1. [KSM 源码分析](#1-ksm-源码分析)
2. [整体架构概览](#2-整体架构概览)
3. [UI 交互模块](#3-ui-交互模块)
4. [多线程守护 (AutoCatchWorker)](#4-多线程守护-autocatchworker)
5. [抢注触发链路 (Execution Pipeline)](#5-抢注触发链路-execution-pipeline)
6. [系统级通知](#6-系统级通知)
7. [文件变更清单](#7-文件变更清单)
8. [实现路线图](#8-实现路线图)
9. [风险与约束](#9-风险与约束)

---

## 1. KSM 源码分析

### 1.1 项目概览

KSM (`KeanSeatsMonitor.py`, 912 行) 是一个基于 **Selenium + Tkinter** 的课程余量监控与自动抢课工具。架构为单体脚本：一个浏览器控制类 `KeanCourseMonitor` + 一个 GUI 类 `CourseMonitorGUI`。

### 1.2 轮询机制分析

核心位于 `start_monitoring()` 方法（第 813-889 行），采用 **串行同步轮询** 模式：

```
while self.monitoring:
    1. 刷新页面 (driver.refresh)
    2. 切换学期 (switch_to_term)
    3. 逐个 section 查询 (get_section_details, skip_refresh=True)
    4. 对比历史数据，检测变化
    5. sleep(interval)
```

**关键特征：**
- 每轮都刷新整个页面（`driver.refresh()`），开销较大
- 学期切换通过页面元素识别 + "下一个"按钮翻页实现（最多 25 次翻页）
- 轮询间隔由用户自由设定（Tkinter Entry，无上限校验）
- 没有使用 `threading.Event` 实现可中断睡眠，而是直接 `time.sleep(interval)`——无法即时响应停止信号

**KSC 对比优势：** KSC 的 `MonitorWorker` 使用 `_stop_event.wait(seconds)` 实现可中断睡眠，AutoCatch 将继承这一模式。

### 1.3 座位状态解析逻辑

核心在 `get_section_details()` 方法（第 323-433 行）：

**解析流程：**
1. 在页面 DOM 中定位 section 代码对应的链接元素
2. 点击打开 Section Details 弹窗
3. 提取弹窗文本内容
4. 正则匹配三种模式：

```python
# 模式 1: "Seats Available.*?(\d+)\s*/\s*(\d+)\s*/\s*(\d+)"
# 模式 2: "Seats Available.*?(\d+)\s*[/|]\s*(\d+)\s*[/|]\s*(\d+)"
# 模式 3: "Available\s*[:\s]*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)"
#
# 捕获组: group(1)=available, group(2)=capacity, group(3)=waitlist
```

**数据结构：**
```python
section_info = {
    'code': section_code,
    'available': int,    # 当前剩余名额
    'capacity': int,     # 课程总容量
    'waitlist': int,     # Waitlist 排队人数
    'timestamp': str     # 查询时间戳
}
```

**Waitlist 状态判断逻辑：**
- KSM 中 waitlist 数值直接从页面提取，**未做阈值判断**（仅展示数据）
- 自动抢课触发条件仅依赖 `available > 0`（第 474 行）
- Waitlist 条件的利用在 KSC 的 `execute_full_attack` 中已有更完善的实现（`enable_waitlist` 参数 + `WAITLIST_ACTION` 回退逻辑）

**KSC Auto-Catch 的改进方向：**
- 将 `available > 0` 和 `waitlist > 0` 都作为可配置的触发条件
- 相较 Selenium DOM 抓取，API 直连方式更轻量、更快

### 1.4 API 请求的循环处理

KSM 中 **没有独立的 API 层**——所有数据获取通过 Selenium 页面操作完成：

| 操作 | Selenium 方式 | 耗时估算 |
|------|-------------|---------|
| 刷新页面 | `driver.refresh()` | 2-5 秒 |
| 切换学期 | 翻页按钮定位 + 点击循环 | 1-5 秒 |
| 查询单个 Section | 元素定位 + 弹窗等待 + 正则 | 2-4 秒 |
| 自动注册点击 | "Register Now" 按钮定位 + 点击 | 0.5-1 秒 |

**关键发现：** KSM 的自动抢课（第 474-487 行）是**在主监控循环内同步执行**的。检测到空位后：
1. 调用 `attempt_registration()` 点击 "Register Now" 按钮
2. `time.sleep(2)` 等待
3. **继续监控循环**（不中断）

这意味着注册点击后，监控仍在继续——可能导致同一空位触发多次注册尝试。

**KSC Auto-Catch 改进：** 检测到余量后**立即挂起轮询**，专注执行抢注，抢注完成后（无论成败）再决定是否恢复轮询。

### 1.5 KSM 架构总结

| 维度 | KSM 实现 | KSC Auto-Catch 应采取的方案 |
|------|---------|--------------------------|
| 数据获取 | Selenium DOM 抓取 | HTTP API 直连（沿用 `KeanApiClient`） |
| 轮询睡眠 | `time.sleep()` 不可中断 | `threading.Event.wait()` 可中断 |
| 抢课触发 | 循环内同步调用 | 挂起轮询 → 专注抢注 → 恢复/停止 |
| 状态解析 | 正则匹配页面文本 | API JSON 响应解析 |
| GUI 框架 | Tkinter | PyQt6 + qfluentwidgets |
| 线程模型 | `threading.Thread` + `root.after()` | `QThread` + `pyqtSignal` |

---

## 2. 整体架构概览

### 2.1 模块关系图

```
┌─────────────────────────────────────────────────────────────────┐
│                     KeanSeatsCatcherApp                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │  Auth    │  │ Monitor  │  │  About   │  │  AutoCatch       │ │
│  │Interface │  │Interface │  │Interface │  │  Interface (NEW) │ │
│  └────┬─────┘  └────┬─────┘  └──────────┘  └───────┬──────────┘ │
│       │             │                               │            │
│       │  engine_ready_signal (shared to both)       │            │
│       └─────────────┴───────────────────────────────┘            │
│                          │                                       │
│                   KeanApiClient                                  │
│                   (core_api.py)                                  │
│                   + check_section_status()  [待批准]              │
│                   + execute_full_attack()                        │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
[用户启动 AutoCatch]
        │
        ▼
[AutoCatchWorker] ◄── KeanApiClient (共享实例)
        │
        ├── 低频轮询: check_section_status(section_ids)
        │       │
        │       ▼
        │   [Seat > 0 或 Waitlist 条件满足?]
        │       │
        │   NO  └── 继续轮询 ──┘
        │   YES
        │       │
        │       ▼
        │   [挂起轮询定时器]
        │       │
        │       ▼
        │   [execute_full_attack(section_ids, enable_waitlist)]
        │       │
        │  success ──► Windows Toast 通知 → 停止工作器
        │  failure ──► 记录日志 → 恢复轮询
        │
        ▼
[UI 信号] → 日志更新 / 状态变更 / 系统通知
```

### 2.3 设计原则

1. **零侵入**: 新模块通过信号与现有架构通信，不动 `core_api.py` 现有发包逻辑
2. **极简 UI**: Auto-Catch 界面不超过 3 个输入控件 + 1 个按钮
3. **静默后台**: 轮询线程不影响主 GUI 响应，不干扰 Monitor 标签页独立操作
4. **快速响应**: 检测到空位后立即停止轮询，切换至抢注模式

---

## 3. UI 交互模块

### 3.1 界面布局

```
┌─────────────────────────────────────────┐
│  Auto-Catch                             │  ← SubtitleLabel, 26px bold
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ Section IDs: [___________________]  ││  ← LineEdit, placeholder 提示
│  │                                     ││
│  │ Interval:    [___] sec              ││  ← LineEdit + QIntValidator(10-3600)
│  │                                     ││
│  │ ┌─ Waitlist Fallback ── [Switch] ─┐ ││  ← SwitchButton
│  │ └──────────────────────────────────┘││
│  │                                     ││
│  │ [▶ Start Monitoring]   [⏹ Stop]   ││  ← PushButton (FIF.PLAY / FIF.PAUSE)
│  └─────────────────────────────────────┘│
│                                         │
│  Status: ● Monitoring (Round #12)...   │  ← BodyLabel, 动态状态指示
│                                         │
│  ┌─ System Logs ───────────────────────┐│
│  │ [HH:MM:SS] >> Polling round #1...  ││  ← TextEdit, 等宽 Consolas, #1e1e1e
│  │ [HH:MM:SS] [INFO] Section 21421: 0 ││
│  │ [HH:MM:SS] [SUCCESS] Seat found!   ││
│  │ ...                                ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

### 3.2 控件规格

| 控件 | 类型 | 说明 |
|------|------|------|
| Section IDs 输入 | `LineEdit` | 逗号分隔，支持多个 Section。placeholder: 如 "21421, 19334" |
| Interval 输入 | `LineEdit` + `QIntValidator(10, 3600)` | 默认 60 秒，最小 10 秒（防止高频请求），最大 3600 秒 |
| Waitlist 开关 | `SwitchButton` | 传给 `execute_full_attack`，与 Monitor 标签页逻辑一致 |
| Start 按钮 | `PushButton(FIF.PLAY, ...)` | 启动轮询。要求 API Engine 已就绪 |
| Stop 按钮 | `PushButton(FIF.PAUSE, ...)` | 停止轮询。初始 disabled |
| 状态指示 | `BodyLabel` | 动态显示：空闲 / 轮询中（轮次） / 抢注中 / 成功 / 失败 |
| 日志框 | `TextEdit` (readOnly) | 与 Monitor 标签页的日志框样式一致 |

### 3.3 侧边栏注册

在 `KeanSeatsCatcherApp.__init__` 中新增一项：

```python
self.autocatch_interface = AutoCatchInterface(self)
self.auth_interface.engine_ready_signal.connect(
    self.autocatch_interface.set_api_engine)
self.addSubInterface(
    self.autocatch_interface,
    FIF.ZOOM,                            # 或 FIF.SEARCH
    i18n.tr("tab_autocatch"),
    position=NavigationItemPosition.TOP  # 排在 Monitor 之后
)
```

### 3.4 状态管理

`AutoCatchInterface` 维护以下状态：

| 状态 | 枚举值 | Start 按钮 | Stop 按钮 | 输入控件 |
|------|--------|-----------|----------|---------|
| `IDLE` | 0 | enabled | disabled | enabled |
| `POLLING` | 1 | disabled | enabled | disabled |
| `ATTACKING` | 2 | disabled | disabled | disabled |

状态通过 `AutoCatchWorker` 的信号驱动切换。

---

## 4. 多线程守护 (AutoCatchWorker)

### 4.1 类定义

```python
class AutoCatchWorker(QThread):
    # 信号
    log_signal = pyqtSignal(str, str)           # (消息, 级别: normal/success/error)
    status_signal = pyqtSignal(int)             # 状态: 0=IDLE, 1=POLLING, 2=ATTACKING
    seat_found_signal = pyqtSignal(str, int)    # (section_code, available_seats)
    attack_result_signal = pyqtSignal(bool, str) # (success, detail_msg)
    round_signal = pyqtSignal(int)              # 当前轮次计数

    def __init__(self, api_engine, section_ids, interval,
                 enable_waitlist=False):
        super().__init__()
        self.api_engine = api_engine
        self.section_ids = section_ids
        self.interval = interval
        self.enable_waitlist = enable_waitlist
        self.is_running = True
        self._stop_event = threading.Event()
        self._attack_mode = False               # 抢注模式标志
```

### 4.2 线程生命周期

```
                    ┌──────────┐
                    │  IDLE    │
                    └────┬─────┘
                         │ start()
                         ▼
                    ┌──────────┐
            ┌───────│ POLLING  │◄──────────────┐
            │       └────┬─────┘               │
            │            │ 检测到空位            │
            │            ▼                      │
            │       ┌──────────┐   失败/超时    │
            │       │ ATTACKING│───────────────┘
            │       └────┬─────┘
            │            │ 成功
            │            ▼
            │       ┌──────────┐
            │       │  DONE    │ (触发 Toast, 停止)
            │       └──────────┘
            │
            │ 用户点击 Stop / is_running=False
            └──────────────────────► 停止线程
```

### 4.3 run() 核心逻辑

```python
def run(self):
    self._stop_event.clear()
    round_count = 0

    while self.is_running:
        round_count += 1
        self.round_signal.emit(round_count)
        self.status_signal.emit(1)  # POLLING
        self.log_signal.emit(
            i18n.tr("autocatch_round_start", round_count), "normal")

        # ──── 阶段 1: 余量检测 ────
        try:
            availability = self.api_engine.check_section_status(
                self.section_ids)
        except Exception as e:
            self.log_signal.emit(
                i18n.tr("autocatch_check_error", str(e)), "error")
            self._sleep(self.interval)
            continue

        # 解析检测结果
        target_seats = self._find_available_seats(availability)

        if target_seats:
            # ──── 阶段 2: 抢注 ────
            self._attack_mode = True
            self.status_signal.emit(2)  # ATTACKING

            for sec_id, seat_count in target_seats.items():
                self.seat_found_signal.emit(sec_id, seat_count)
                self.log_signal.emit(
                    i18n.tr("autocatch_seat_found", sec_id, seat_count),
                    "success")

            success, msg = self.api_engine.execute_full_attack(
                self.section_ids,
                enable_waitlist=self.enable_waitlist)

            self.attack_result_signal.emit(success, msg)

            if success:
                self.is_running = False
                self.status_signal.emit(0)  # IDLE
                break
            else:
                self.log_signal.emit(
                    i18n.tr("autocatch_attack_failed", msg), "error")
                self._attack_mode = False
        else:
            self.log_signal.emit(
                i18n.tr("autocatch_no_seats"), "normal")

        # 轮询间隔等待（可中断）
        self._sleep(self.interval)

    self.status_signal.emit(0)  # IDLE

def _sleep(self, seconds: float):
    """可中断的睡眠，响应 stop 信号"""
    self._stop_event.wait(seconds)

def _find_available_seats(self, availability: dict) -> dict:
    """解析 check_section_status 返回值，返回有空位的 section"""
    result = {}
    for sec_id, info in availability.items():
        if info.get('available', 0) > 0:
            result[sec_id] = info['available']
    return result
```

### 4.4 与 MonitorWorker 的关键差异

| 维度 | MonitorWorker | AutoCatchWorker |
|------|-------------|-----------------|
| 运行模式 | 直接重复调用 `execute_full_attack` | 先检测后攻击，两阶段分离 |
| 轮询频率 | 高频（默认 5 秒，可更低） | 低频（默认 60 秒，最低 10 秒） |
| 是否存在定时触发 | 支持定时（`target_time_str`） | 仅支持立即开始，持续轮询 |
| 成功时行为 | 停止循环，发出完成信号 | 停止循环，发出完成信号 + Toast |
| 失败时行为 | 根据错误类型冷却后重试 | 恢复轮询，等待下一轮检测 |
| 超时处理 | 指数退避（前 2 次 2s，后续 interval） | 视为检测失败，按 interval 恢复轮询 |

---

## 5. 抢注触发链路 (Execution Pipeline)

### 5.1 完整时序

```
时间轴 ─────────────────────────────────────────────────────────────►

轮询阶段                          抢注阶段               结果阶段
┌───────────┐   ┌───────────┐   ┌───────────────┐   ┌────────────┐
│ sleep(Ns) │──►│ 余量检测   │──►│ execute_full  │──►│ Toast/日志 │
│           │   │ (API GET)  │   │ _attack()     │   │            │
└───────────┘   └─────┬─────┘   └───────┬───────┘   └────────────┘
                      │                 │
              无空位   │         ┌──────┴──────┐
              继续轮询  │         │ 成功 → 停止  │
                      │         │ 失败 → 恢复  │
                      │         │ 超时 → 恢复  │
                      │         └─────────────┘
```

### 5.2 检测阶段 (check_section_status)

**待批准的 API 方法**（需在 `core_api.py` 新增）：

```python
def check_section_status(self, section_ids: List[str]) -> dict:
    """
    查询 Section 余量状态（不执行注册）。

    参数:
        section_ids: Section ID 列表
    返回:
        {
            "21421": {"available": 0, "capacity": 30, "waitlist": 5, "status": "Waitlist"},
            "19334": {"available": 3, "capacity": 25, "waitlist": 0, "status": "Open"},
        }

    注: 具体 API 端点需根据 Ellucian Banner 9 的 Section Search API 确认。
    建议端点: GET/POST {base_url}/Sections
    """
```

**设计要点：**
- 该方法必须是**只读查询**，不修改选课状态
- 返回的字段需包含 `available`、`capacity`、`waitlist`，以及可选的状态描述
- 超时设置应较短（建议 `timeout=(1.5, 8)`），因为检测不需要等服务器处理注册
- 如果 API 端点不可用，可以设计为发送一个模拟的 "check-only" 注册请求（需要服务端支持），或者通过其他轻量接口获取数据

**API 端点待确认项：**
- 实际 URL 路径（需通过抓包或 API 文档确认）
- 请求方法（GET vs POST）
- 是否需要额外参数（term code 等）

### 5.3 攻击阶段 (execute_full_attack)

**已有方法**（`core_api.py:143-167`），**不做任何修改**：

```python
def execute_full_attack(self, section_ids, enable_waitlist=False) -> Tuple[bool, str]:
    # 1. register_multiple_sections(section_ids, action="Add")
    # 2. complete_registration()
    # 3. 如果失败 + waitlist 开启 + 非超时 → 用 action="Waitlist" 重试
```

### 5.4 触发条件决策矩阵

| available > 0 | waitlist > 0 | enable_waitlist | 行为 |
|:---:|:---:|:---:|------|
| ✓ | - | - | 立即抢注（正常 Action="Add"） |
| ✗ | ✓ | True | 立即抢注（Waitlist 回退） |
| ✗ | ✓ | False | 只记录日志，继续轮询 |
| ✗ | ✗ | - | 只记录日志，继续轮询 |

### 5.5 异常处理策略

| 异常场景 | 处理方式 |
|---------|---------|
| 检测阶段 HTTP 超时 | 记录警告日志，视为"本轮无数据"，继续轮询 |
| 检测阶段返回非预期格式 | 记录错误日志，继续轮询 |
| 抢注阶段成功 | Toast 通知，停止工作器 |
| 抢注阶段超时（ReadTimeout） | 乐观假设可能已处理，Toast 提示手动确认 |
| 抢注阶段被拒（课程已满） | 日志记录，恢复轮询 |
| 抢注阶段网络错误 | 日志记录，恢复轮询 |
| 连续 N 次检测失败 | 不触发抢注，仅日志警告 |
| 用户手动 Stop | 立即通过 `_stop_event` 唤醒，优雅退出 |

---

## 6. 系统级通知

### 6.1 方案选择

推荐使用 **Windows Toast 通知**，方案对比：

| 方案 | 依赖 | 兼容性 | 交互性 | 推荐 |
|------|------|--------|--------|------|
| `winotify` (PyPI) | 无额外依赖，纯 ctypes | Win 8+ | 支持按钮回调 | **推荐** |
| `win10toast` (PyPI) | `pypiwin32` | Win 10+ | 仅文本 | 备选 |
| 原生 `ctypes` 调用 | 无 | Win 10+ | 支持完整 XML | 高级方案 |

**推荐 `winotify`**（轻量、无额外二进制依赖、支持点击回调）。

### 6.2 Toast 内容设计

**成功通知：**
```
┌─────────────────────────────────────────┐
│  KeanSeatsCatcher                       │  ← App 名称
│                                         │
│  Auto-Catch 抢注成功!                    │  ← 标题 (bold)
│  Section 21421 已成功注册。             │  ← 正文
│  请登录 KeanWISE 确认课表。             │
│                                         │
│  [打开 KeanWISE]                        │  ← 可选按钮（打开浏览器）
└─────────────────────────────────────────┘
```

**检测到空位通知（可选，抢注进行中）：**
```
┌─────────────────────────────────────────┐
│  KeanSeatsCatcher                       │
│                                         │
│  检测到空位!                             │
│  Section 21421: 剩余 3 个名额           │
│  正在自动抢注...                        │
└─────────────────────────────────────────┘
```

### 6.3 集成点

Toast 触发在 `AutoCatchInterface` 的信号槽中：

```python
def _on_attack_result(self, success: bool, msg: str):
    if success:
        self._show_toast(
            title=i18n.tr("toast_success_title"),
            msg=i18n.tr("toast_success_body"),
            duration="long"
        )
    # 失败不弹 Toast，仅日志记录（避免骚扰用户）
```

### 6.4 静默模式选项

UI 中可增加一个 `SwitchButton` "Silent Mode"：
- 开启时：仅成功时弹 Toast
- 关闭时（默认）：成功和异常都弹 Toast

---

## 7. 文件变更清单

### 7.1 新增文件

| 文件 | 说明 |
|------|------|
| `ui_autocatch.py` | AutoCatchInterface 界面类（约 250 行） |
| `auto_catch_worker.py` | AutoCatchWorker 线程类（约 120 行） |
| 以上两个模块可考虑合并为一个文件以减少碎片化，视复杂度决定 |

### 7.2 修改文件

| 文件 | 修改部位 | 修改量 | 风险 |
|------|---------|--------|------|
| `ui_main.py` | `KeanSeatsCatcherApp.__init__`: 新增 `AutoCatchInterface` 实例化 + 侧边栏注册 + signal 连接 | ~8 行 | **低** |
| `core_api.py` | 新增 `check_section_status()` 方法（**不修改现有方法**） | ~30 行 | **低**（纯新增） |
| `locales/en_US.json` | 新增 ~20 个翻译 key | ~20 行 | **低** |
| `locales/zh_CN.json` | 新增 ~20 个翻译 key | ~20 行 | **低** |
| `requirements.txt` | 新增 `winotify` 依赖 | 1 行 | **低** |

### 7.3 不变更文件

| 文件 | 原因 |
|------|------|
| `core_auth.py` | 不涉及认证流程 |
| `i18n.py` | 现有国际化框架无需改动 |
| `config.json` | 不新增持久化配置项（首版 Auto-Catch 不需要记忆上次输入） |

---

## 8. 实现路线图

### Phase 1: 基础框架 (预计实现顺序)

1. **创建 `ui_autocatch.py`** — 实现 `AutoCatchInterface` 类，包含完整 UI 布局
2. **创建 `auto_catch_worker.py`** — 实现 `AutoCatchWorker(QThread)`，包含轮询逻辑骨架
3. **集成到 `ui_main.py`** — 侧边栏注册、信号连接
4. **新增 i18n 键** — 英文和中文翻译

**验证标准：** Auto-Catch 标签页可正常显示，Start/Stop 按钮可切换状态（无后端逻辑）。

### Phase 2: API 检测方法

5. **确认 `check_section_status` API 端点**（抓包验证）
6. **在 `core_api.py` 中新增 `check_section_status()`**（仅新增，不改旧代码）

**验证标准：** 可以成功查询 Section 余量并返回正确数据。

### Phase 3: 抢注链路

7. **实现完整的"检测 → 攻击"Pipeline**
8. **实现异常处理和重试逻辑**

**验证标准：** 模拟"检测到空位 → 自动调用 execute_full_attack"的完整流程。

### Phase 4: 系统通知

9. **安装 `winotify` 依赖**
10. **实现 Toast 通知集成**

**验证标准：** 抢注成功后弹出 Windows 系统通知。

### Phase 5: 集成测试

11. **端到端测试**：从认证 → 开始监控 → 检测空位 → 自动抢注 → 成功通知
12. **边界测试**：网络断开、Session 过期、课程已满、Waitlist-only 等场景

---

## 9. 风险与约束

### 9.1 已知风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `check_section_status` API 端点未确认 | 轮询检测功能无法工作 | Phase 2 需优先抓包确认端点 |
| 低频轮询可能错过短暂空位 | 抢注成功率降低 | 由用户权衡 interval 设置；默认 60s 兼顾安全与效率 |
| 多个 Section 同时检测到空位 | 抢注策略不确定 | `execute_full_attack` 本身支持多 Section 一次提交 |
| `winotify` 在早期 Windows 版本不工作 | 通知不弹出 | 降级为 `InfoBar` 应用内弹窗 + `QApplication.beep()` |

### 9.2 设计约束

1. **`core_api.py` 发包逻辑不可变** — `execute_full_attack`、`register_multiple_sections`、`complete_registration` 保持原样。`check_section_status` 仅为新增方法，不对现有代码做任何修改。
2. **不阻塞主线程** — 所有网络 I/O 必须在 `QThread` 中执行，UI 通过 `pyqtSignal` 更新。
3. **不与 Monitor 标签页冲突** — AutoCatch 和 Monitor 共享同一个 `KeanApiClient` 实例，但不能同时运行（两个 Worker 同时发包会互相干扰）。需要在 UI 层面互斥或提示用户。
4. **极简 UI** — 不增加不必要的配置项、不引入新的卡片组或复杂布局。

### 9.3 后续扩展（不在本次范围内）

- Auto-Catch 与 Monitor **同时运行**（多线程并发检测 + 抢注）
- 持久化 Auto-Catch 配置（记住上次的 Section IDs 和 Interval）
- 统计面板（轮询次数、成功率、空位出现时间分布）
- 多学期/多 Section 组同时监控

---

> **评审要点：**
> 1. `check_section_status` API 端点是否已有抓包数据？若无，需优先安排。
> 2. AutoCatch 与 Monitor 共享 engine 的互斥策略是否接受？
> 3. UI 布局是否需要增加 "Silent Mode" 开关？
> 4. Interval 下限 10 秒是否合理？是否需要更低？
