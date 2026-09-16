---
name: workbuddy-checkin
description: "Automate WorkBuddy daily check-in (签到领积分) on macOS and Windows. Cross-platform entry script auto-detects OS and runs the platform-specific implementation: macOS uses screenshot analysis + CGEvent mouse simulation (bypasses Electron's inaccessible DOM); Windows uses uiautomation accessibility tree. Use when the user asks to set up, run, troubleshoot, or schedule WorkBuddy auto check-in. Covers Pillow image analysis, coordinate mapping, CGEvent clicking, uiautomation, screen reader flag, debug/dry-run mode, and daily scheduling."
---

# Wb Checkin (跨平台)

Automate WorkBuddy daily check-in on macOS and Windows. A cross-platform entry script (`wb_checkin.py`) auto-detects the operating system and dispatches to the platform-specific implementation.

## 文件结构

```
scripts/
  wb_checkin.py            ← 跨平台入口（自动选择平台脚本，传递所有参数）
  wb_checkin_macos.py      ← macOS 实现（截屏分析 + CGEvent）
  wb_checkin_windows.py    ← Windows 实现（uiautomation 辅助功能树）
```

## 运行方式（统一入口）

```bash
# macOS / Windows 通用
python3 scripts/wb_checkin.py

# macOS 调试模式（保存各阶段截图）
python3 scripts/wb_checkin.py --debug

# macOS dry-run（只检测不点击）
python3 scripts/wb_checkin.py --dry-run
```

入口脚本通过 `sys.platform` 自动选择：
- `darwin` → `wb_checkin_macos.py`
- `win32` → `wb_checkin_windows.py`
- 其他 → 报错退出

所有命令行参数原样传递给平台脚本。

---

## macOS 实现 (v3.7)

### 技术方案

WorkBuddy 是 Electron 应用，macOS 的 System Events 无法暴露其内部 DOM 元素（`entire contents` 和 BFS `UI elements of` 均失败）。v3 完全抛弃辅助功能树方案，改用截屏分析 + CGEvent 鼠标模拟：

1. `CGWindowListCopyWindowInfo` 获取窗口位置（points 坐标，无需辅助功能权限）
2. `screencapture` 截屏（物理像素）
3. Pillow 图像分析：在窗口左下角搜索「Buddy加油站」卡片（BFS 连通区域分析）
   - 未签到：深色「立即领取」按钮（`find_dark_button`）
   - 已签到：**实心灰**「今日已领」按钮（`find_claimed_button`）
4. CGEvent 模拟点击「立即领取」按钮（ctypes 调用 CoreGraphics C API）
5. 重新截屏验证（见下方"判定权威"）

**v3.5 入口路径（WorkBuddy 5.5.x）**：签到入口从"右上角头像"移到**侧边栏**：

```
展开侧边栏（若隐藏）→ 点击左下角头像 "阿七." → 头像菜单首项区域 "Buddy加油站" → 弹出底部签到卡片
```

- 侧边栏开关 `SIDEBAR_TOGGLE_RATIO=(0.075,0.034)`；头像 `AVATAR_RATIO=(0.041,0.950)`；菜单项 `MENU_BUDDY_RATIO=(0.088,0.385)`（**v3.7**：5.5.6 菜单重构后由 0.339 下移，旧值会点中"积分余额"打开设置面板，签到卡片不出现）。
- ⚠️ **点"Buddy加油站"必须用 `cg_click_nomove()`**：Electron 头像下拉菜单是"锚定型"，`cg_click` 会先把光标移离头像 → 菜单立即关闭 → 点击落到下层侧边栏项（曾误触"定时任务"页）。点"立即领取"按钮仍用普通 `cg_click`。
- `is_sidebar_open()` 通过采样头像绿色外环判定侧边栏是否展开（避免重复点开关反而关掉）。

**判定权威（v3.6，关键）**：

- **先** `find_dark_button`（深色「立即领取」）。**它存在 ⇔ 未签到** → 必须点击。
- **仅当**深色按钮不存在时，才用 `find_claimed_button` 判"今日已签到"。
- 点击后验证：**强信号** = 检出「今日已领」；**弱信号** = 「立即领取」按钮已消失。两者皆无 → WARNING + 返回 False。

> 为什么必须"先找深色按钮"：5.5.x 卡片里**常驻**一个白底灰描边的「认证领积分」按钮，
> 其灰色边框（灰度实心度 density≈0.04）曾被 `find_claimed_button` 误判为"实心灰今日已领"，
> 导致脚本每次都直接返回"今日已签到"、**从不点击**。修复：① `find_claimed_button` 加
> `density >= 0.5` 实心度门槛；② 判定改为"深色按钮优先"。**不要**再退回"只靠灰度连通域判已签"。

> 另注：v5.4.x 起「立即领取」是**直接领取、无确认弹窗**（点完按钮直接变灰）。
> 旧版（≤ v3.3）只看窗口像素差异 > 1% 就去找"弹窗按钮"，会被 `ensure_running` /
> `activate_app` 的 2%+ 全局差异误导，在主界面点错位置——日志报
> "签到流程完成"但实际未签到。

### 坐标体系

| 坐标系 | 使用者 | 单位 |
|--------|--------|------|
| Points（逻辑坐标） | System Events 窗口位置、CGEvent 点击 | 1x |
| Pixels（物理像素） | screencapture 截图 | Retina 2x |
| 窗口相对像素 | Pillow 图像分析 | 从截图裁剪 |

转换：`screen_point = win_origin + pixel_offset / scale_factor`

缩放因子在运行时通过比较 `CGDisplayBounds`（points）和 `screencapture` 尺寸（pixels）自动检测。

### 前置条件

**Pillow（必须）**：
```bash
pip3 install Pillow
```

**辅助功能权限**（窗口位置获取 + CGEvent 点击）：
1. 系统设置 → 隐私与安全性 → 辅助功能
2. 添加运行脚本的程序（Terminal / WorkBuddy / osascript）
3. 开启开关

**屏幕录制权限**（screencapture 截图）：
- 如截图空白 → 系统设置 → 隐私与安全性 → 屏幕录制 → 添加运行程序

### 调试模式

`--debug` 保存截图到 `/tmp/wb_checkin_debug/`：
- `01_before_full.png` — 点击前全屏
- `02_window.png` — 裁剪的 WorkBuddy 窗口
- `02a_after_sidebar_window.png` — 展开侧边栏后（仅当原为隐藏时）
- `03_after_nav_window.png` — 导航到 Buddy加油站后（**排查首选**）
- `03_dark_btn.png` — 深色「立即领取」按钮标注（红框）
- `03_claimed.png` — 误判为「今日已领」时标注（绿框）
- `04_final.png` — 点击后最终状态

**排查手法（可复用）**：用 `--debug` 截图后，直接 `import` 脚本模块，对
`03_after_nav_window.png` 调 `find_dark_button` / `find_claimed_button`，
再用 Pillow 把命中框画出来（红=深色按钮、绿=已领判定、蓝=搜索区），
即可直观看清检测落到了哪个 UI 元素上。

**坐标漂移校准手册（WorkBuddy 更新界面后连续失败时用）**：
症状 = 日志报"导航后仍未找到签到入口"，但 `03_after_nav_window.png` 里出现的
是**设置面板**或其他页面而非底部签到卡片 → 说明菜单项 `MENU_BUDDY_RATIO` 已漂移。
校准步骤（2026-09-16 实修验证）：
1. 写探针：`import wb_checkin_macos as wb`（`sys.path` 指向脚本目录），
   调 `wb.ensure_running()` / `wb.activate_app()` / `wb.get_window_rect()`，
   然后 `wb.cg_click(头像坐标)` + `time.sleep(2)` + `wb.screenshot().save(...)`。
   探针**只截图、不点菜单项**，安全。
2. 从截图量出新"Buddy加油站"的 y 像素 → 换算 `ratio = (y_screen - win_y) / win_h`，
   取文字行中心（图像若被缩放显示，注意乘回缩放比）。
3. 改 `MENU_BUDDY_RATIO` 的 y 分量后，先跑一次 `--debug` 确认
   `03_after_nav_window.png` 出现签到卡片（白底 + 黑色「立即领取」），再正式跑。
4. Esc 键可用 `CGEventCreateKeyboardEvent(None, 53, down/up)` + `CGEventPost` 发送，
   用来关闭误开的设置面板恢复原状。

> 检测逻辑与窗口坐标无关的部分（`find_dark_button` 等）通常**不用改**：
> 5.5.6 的新卡片仍是窗口左下角弹出、黑色实心「立即领取」按钮，
> 仍落在 `CARD_SEARCH = (0.02, 0.35, 0.72, 0.95)` 内。

### 配置参数

macOS 脚本顶部可调参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CARD_SEARCH` | `(0.02, 0.35, 0.72, 0.95)` | 签到卡片搜索区域（窗口比例） |
| `DARK_THRESHOLD` | `35` | 灰度低于此值视为深色 |
| `CARD_MIN_W/MAX_W` | `0.08, 0.30` | 卡片宽度范围 |
| `CARD_MIN_H/MAX_H` | `0.06, 0.22` | 卡片高度范围 |
| `DIALOG_SEARCH` | `(0.20, 0.80, 0.30, 0.85)` | 弹窗按钮搜索区域 |
| `DIFF_THRESHOLD` | `0.01` | 截图差异阈值（1%） |
| `SIDEBAR_TOGGLE_RATIO` | `(0.075, 0.034)` | v3.5 侧边栏展开开关 |
| `AVATAR_RATIO` | `(0.041, 0.950)` | v3.5 侧边栏左下头像 |
| `MENU_BUDDY_RATIO` | `(0.088, 0.385)` | 头像菜单中"Buddy加油站"项（**v3.7 更新**；5.5.6 前为 0.339） |
| `find_claimed_button` 实心度门槛 | `density >= 0.5` | v3.6 排除描边按钮误报 |

---

## Windows 新版（2026-09-16）

Windows 使用 `comtypes` 直连 UIAutomationCore，以 `fuel-expanded-claim` 控件 ID 和精确文案判断签到；不需要设置昵称，也不再修改系统屏幕阅读器标志。带 DPI 感知、最小化窗口恢复、菜单重试、锁屏跳过、应用拉起和结果日志。

### 安装与执行

在 Windows 上将仓库放入长期保留的目录，确保 WorkBuddy 已安装并登录。先安装可在命令行运行的 Python 3.9+，然后：

1. 双击 `windows/1_setup_env.cmd`，创建隔离环境并安装 `comtypes`、`pillow`。
2. 双击 `windows/3_run_checkin_now.cmd`，执行并核对结果。运行期间不要操作鼠标。
3. 双击 `windows/2_register_daily_task.cmd`，注册每天 09:00 的 `WorkBuddyDailyCheckin`；可传入 `08:30` 等时间。

在仓库根目录的 Windows cmd 中，也可使用统一入口：

```cmd
"%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" scripts\wb_checkin.py --json
"%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" scripts\wb_checkin.py --dry-run --json
```

`--dry` / `--dry-run` 会导航菜单、读取状态，但不点击领取；`--exe` 可指定 WorkBuddy.exe 路径。默认路径为 `C:\Program Files\WorkBuddy\WorkBuddy.exe`。

计划任务使用当前用户的交互会话，无需存密码；锁屏时跳过，解锁后需手动运行或由兜底任务重试。`StartWhenAvailable` 支持错过计划时间后补跑，但不保证解锁时自动重试。`IgnoreNew` 仅防止同一个计划任务重复启动，手动执行和 WorkBuddy 兜底应错开时间。

日志位于 `windows/workbuddy-desktop-checkin/logs/`：当日 `result-YYYY-MM-DD.json`、最近一次 `last_result.json` 和月度文本日志。只有 `success` / `already_claimed` 算完成；`dry_ok` 只表示试运行通过。退出码：0 成功/已签/试运行通过，2 需人工介入，3 异常，4 跳过。

便携部署、可选 Skill 安装、兜底提示词与诊断方法见 [Windows 使用说明](windows/README.md)。

## 自动化调度与诊断

- macOS 保留现有 launchd / WorkBuddy 调度，快速配置见 `README.md`。
- Windows 以 `windows/workbuddy-desktop-checkin/scripts/run_checkin.py` 为统一日志入口。
- Windows 兜底先读取同一部署目录的当日结果；未完成时调用 `run_checkin.py --json`，避免漏写结果。
- Windows UI 改版时用 `dump_uia.py` 导出控件树；只按当前可见控件操作，不猜坐标。
- `fuel-action` 是认证入口，不能当签到按钮点击。
- Windows 详细说明与界面地图见 `windows/README.md` 和 `windows/workbuddy-desktop-checkin/references/ui-map.md`。
