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

## macOS 实现 (v3.6)

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

- 侧边栏开关 `SIDEBAR_TOGGLE_RATIO=(0.075,0.034)`；头像 `AVATAR_RATIO=(0.041,0.950)`；菜单项 `MENU_BUDDY_RATIO=(0.088,0.339)`。
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
| `MENU_BUDDY_RATIO` | `(0.088, 0.339)` | v3.5 头像菜单中"Buddy加油站"项 |
| `find_claimed_button` 实心度门槛 | `density >= 0.5` | v3.6 排除描边按钮误报 |

---

## Windows 实现

### 技术方案

Windows 版通过 `uiautomation` 库访问 Windows UI Automation 辅助功能树：

1. 确保 WorkBuddy 客户端运行（未运行则拉起）
2. 临时开启系统"屏幕阅读器"标志（`SPI_SETSCREENREADER`），激活 Electron/Chromium accessibility 树
3. 在左下角找到用户头像并点击，打开个人中心菜单
4. 点击"签到领积分"打开签到面板
5. 判断状态：今日已领 → 跳过；立即领取 → 点击领取并确认
6. 恢复屏幕阅读器标志，记录日志

### 前置条件

**uiautomation 库（必须）**：
```cmd
pip install uiautomation
```

**WorkBuddy 安装路径**：默认 `C:\Program Files\WorkBuddy\WorkBuddy.exe`，如不同需修改脚本中的 `WORKBUDDY_EXE`。

**用户昵称配置**：修改脚本中的 `USER_ITEM_KEYWORDS`（默认 `["你的昵称"]`）为实际 WorkBuddy 显示名。

### 配置参数

Windows 脚本顶部可调参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `WORKBUDDY_EXE` | `C:\Program Files\WorkBuddy\WorkBuddy.exe` | WorkBuddy 安装路径 |
| `USER_ITEM_KEYWORDS` | `["你的昵称"]` | 左下角头像/昵称关键词 |
| `CHECKIN_ENTRY_KEYWORDS` | `["签到领积分"]` | 签到入口按钮文字 |
| `CLAIMED_KEYWORDS` | `["今日已领", "已领"]` | 已签到状态按钮 |
| `CLAIM_KEYWORDS` | `["立即领取", "领取", "领"]` | 未签到时的领取按钮 |

---

## 两平台方案对比

| 项目 | macOS (v3.6) | Windows |
|------|-----------|---------|
| UI 自动化方案 | 截屏分析 + CGEvent 鼠标模拟 | uiautomation 辅助功能树 |
| 按钮定位 | 颜色+形状+位置图像分析 | 按 `Name` 属性匹配控件 |
| 鼠标点击 | ctypes → CoreGraphics CGEvent | `ctypes.windll.user32` |
| 截图验证 | Pillow 截图对比 | 读取控件属性变化 |
| 屏幕阅读器标志 | 不需要 | 需临时开启 `SPI_SETSCREENREADER` |
| 第三方依赖 | Pillow | uiautomation |
| 调试模式 | `--debug` 保存截图 | 无（日志输出） |
| 进程名 | "WorkBuddy" / "Electron" 双名称 | "WorkBuddy.exe" |

---

## 自动化调度

### WorkBuddy 自动化任务（推荐）

统一入口，两个平台用同一条命令：

> 执行 WorkBuddy 签到脚本：运行命令 `python3 ~/.workbuddy/scripts/wb_checkin.py`，报告签到结果。

### macOS launchd

创建 `~/Library/LaunchAgents/com.workbuddy.checkin.plist`，详见 `references/wb_checkin_说明.md`。

### Windows 计划任务

通过 `schtasks` 或任务计划程序设置每日定时运行，详见 `references/wb_checkin_说明.md`。

---

## 故障排查

### macOS

| 症状 | 原因 | 修复 |
|------|------|------|
| "错误: 需要 Pillow 库" | Pillow 未安装 | `pip3 install Pillow` |
| 截图空白 | 屏幕录制权限未授予 | 系统设置 → 隐私与安全性 → 屏幕录制 |
| "无法获取窗口位置" | 辅助功能权限不足或 WorkBuddy 未运行 | 检查辅助功能列表；确保 WorkBuddy 已登录 |
| "未找到深色可点击按钮" | 卡片不可见或界面改版 | `--debug` 查看 `03_after_nav_window.png`；核对 `SIDEBAR_TOGGLE_RATIO`/`AVATAR_RATIO`/`MENU_BUDDY_RATIO` |
| **误报"已是今日已领"但实际未签到** | `find_claimed_button` 把卡片常驻的描边按钮（如"认证领积分"）当成实心灰按钮 | v3.6 已修：加 `density>=0.5` + "深色按钮优先"判定。若复现，按 `--debug` 截图排查命中框 |
| 点击无反应 | 缩放因子错误或卡片偏移 | `--debug` 查看 `04_final.png`；检查日志中的 scale 值 |
| 头像菜单点不开/误触侧边栏项 | 用了 `cg_click` 而非 `cg_click_nomove` | 点"Buddy加油站"必须 `cg_click_nomove`（光标离开锚点菜单即关） |

### Windows

| 症状 | 原因 | 修复 |
|------|------|------|
| "未找到用户头像入口" | accessibility 树未初始化或昵称不匹配 | 脚本会自动重试 4 次；检查 `USER_ITEM_KEYWORDS` 是否匹配 |
| "未找到签到/领取按钮" | 界面改版或签到面板未打开 | 检查 `CHECKIN_ENTRY_KEYWORDS` 等关键词是否匹配 |
| 启动失败 | WorkBuddy 安装路径不正确 | 修改 `WORKBUDDY_EXE` 为实际路径 |
| accessibility 树为空 | 屏幕阅读器标志未生效 | 确保脚本以管理员权限运行；检查 `SPI_SETSCREENREADER` 调用 |

## Resources

- `scripts/wb_checkin.py` — 跨平台入口脚本（自动选择平台）
- `scripts/wb_checkin_macos.py` — macOS 实现（截屏分析 + CGEvent，需 Pillow）
- `scripts/wb_checkin_windows.py` — Windows 实现（uiautomation，需 uiautomation 库）
- `README.md` — 面向公众的快速开始、权限设置、定时任务模板和故障排查
