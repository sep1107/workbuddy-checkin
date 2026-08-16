# WorkBuddy 每日签到脚本说明 (跨平台版)

## 脚本结构

```
~/.workbuddy/scripts/
  wb_checkin.py            ← 跨平台入口（自动选择平台脚本，传递所有参数）
  wb_checkin_macos.py      ← macOS 实现（截屏分析 + CGEvent）
  wb_checkin_windows.py    ← Windows 实现（uiautomation 辅助功能树）
  checkin.log              ← 日志文件
```

## 运行方式

统一入口，两个平台用同一条命令：

```bash
python3 ~/.workbuddy/scripts/wb_checkin.py
```

入口脚本通过 `sys.platform` 自动选择平台实现，所有命令行参数原样传递。

### macOS 专用参数

```bash
# 调试模式：保存各阶段截图到 /tmp/wb_checkin_debug/
python3 ~/.workbuddy/scripts/wb_checkin.py --debug

# Dry run：只检测按钮位置，不执行点击
python3 ~/.workbuddy/scripts/wb_checkin.py --dry-run
```

---

## macOS 版 (v3)

### 技术方案

WorkBuddy 是 Electron 应用，macOS 的 System Events 无法暴露其内部 DOM 元素。v3 完全抛弃辅助功能树方案，改用截屏分析 + CGEvent 鼠标模拟：

1. System Events 获取窗口位置（points 坐标）
2. `screencapture` 截屏（物理像素）
3. Pillow 图像分析：在左下角搜索深色"Buddy加油站"卡片（BFS 连通区域分析）
4. CGEvent 模拟点击（ctypes 调用 CoreGraphics C API，points 坐标）
5. 截屏对比验证（窗口区域采样比较，1% 差异阈值）

### 坐标体系

| 坐标系 | 使用者 | 单位 |
|--------|--------|------|
| Points（逻辑坐标） | System Events 窗口位置、CGEvent 点击 | 1x |
| Pixels（物理像素） | screencapture 截图 | Retina 2x |
| 窗口相对像素 | Pillow 图像分析 | 从截图裁剪 |

转换：`屏幕points坐标 = 窗口origin + 像素偏移 / 缩放因子`

### 前置条件

**1. Pillow 库（必须）**

```bash
pip3 install Pillow
```

**2. 辅助功能授权（必须）**

System Events 获取窗口位置和 CGEvent 模拟点击都需要辅助功能权限：

1. 系统设置 → 隐私与安全性 → 辅助功能
2. 添加运行脚本的程序（Terminal / WorkBuddy / osascript）
3. 开启开关

**3. 屏幕录制授权（可能需要）**

如截图空白：系统设置 → 隐私与安全性 → 屏幕录制 → 添加运行程序

### 调试截图

`--debug` 模式保存到 `/tmp/wb_checkin_debug/`：

| 文件 | 说明 |
|------|------|
| `01_before_full.png` | 点击前全屏截图 |
| `02_window.png` | 裁剪后的 WorkBuddy 窗口 |
| `02_window_detected.png` | 窗口 + 红色检测框 |
| `02_window_claimed.png` | 窗口 + 绿色框（已签到） |
| `03_after_click_N.png` | 第 N 次点击后全屏 |
| `04_final.png` | 最终状态 |

### 配置参数

`wb_checkin_macos.py` 顶部：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CARD_SEARCH` | `(0.02, 0.35, 0.72, 0.95)` | 深色卡片搜索区域（窗口比例） |
| `DARK_THRESHOLD` | `35` | 灰度低于此值视为深色 |
| `CARD_MIN_W / CARD_MAX_W` | `0.08, 0.30` | 卡片宽度范围 |
| `CARD_MIN_H / CARD_MAX_H` | `0.06, 0.22` | 卡片高度范围 |
| `DIALOG_SEARCH` | `(0.20, 0.80, 0.30, 0.85)` | 弹窗按钮搜索区域 |
| `DIFF_THRESHOLD` | `0.01` | 截图差异阈值 |

---

## Windows 版

### 技术方案

通过 `uiautomation` 库访问 Windows UI Automation 辅助功能树：

1. 确保 WorkBuddy 客户端运行（未运行则拉起）
2. 临时开启系统"屏幕阅读器"标志（`SPI_SETSCREENREADER`），激活 Electron accessibility 树
3. 在左下角找到用户头像并点击，打开个人中心菜单
4. 点击"签到领积分"打开签到面板
5. 判断状态：今日已领 → 跳过；立即领取 → 点击领取并确认
6. 恢复屏幕阅读器标志，记录日志

### 前置条件

**1. uiautomation 库（必须）**

```cmd
pip install uiautomation
```

**2. WorkBuddy 安装路径**

默认 `C:\Program Files\WorkBuddy\WorkBuddy.exe`，如不同需修改 `wb_checkin_windows.py` 中的 `WORKBUDDY_EXE`。

**3. 用户昵称配置**

修改 `USER_ITEM_KEYWORDS`（默认 `["你的昵称"]`）为实际 WorkBuddy 显示名。

### 配置参数

`wb_checkin_windows.py` 顶部：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `WORKBUDDY_EXE` | `C:\Program Files\WorkBuddy\WorkBuddy.exe` | WorkBuddy 安装路径 |
| `USER_ITEM_KEYWORDS` | `["你的昵称"]` | 头像/昵称关键词 |
| `CHECKIN_ENTRY_KEYWORDS` | `["签到领积分"]` | 签定入口按钮文字 |
| `CLAIMED_KEYWORDS` | `["今日已领", "已领"]` | 已签到状态 |
| `CLAIM_KEYWORDS` | `["立即领取", "领取", "领"]` | 未签到领取按钮 |

### Windows 修复记录（2026-08-16）

**问题**：签到失败，报"未找到用户头像入口（可能未登录）"。

**根因**：`open_avatar_menu` 关键词查找无重试（accessibility 树刚激活时控件可能还没暴露）；备选位置用绝对坐标（`top > 1200 and left < 300`），跟实际窗口尺寸不匹配。

**修复**：关键词查找重试 4 次，每次间隔 2 秒；位置改为基于 `BoundingRectangle` 的相对计算。修复后重跑成功。

---

## 两平台对比

| 项目 | macOS (v3) | Windows |
|------|-----------|---------|
| UI 自动化方案 | 截屏分析 + CGEvent 鼠标模拟 | uiautomation 辅助功能树 |
| 按钮定位 | 颜色+形状+位置图像分析 | 按 Name 属性匹配控件 |
| 鼠标点击 | ctypes → CoreGraphics CGEvent | ctypes.windll.user32 |
| 截图验证 | Pillow 截图对比 | 读取控件属性变化 |
| 屏幕阅读器标志 | 不需要 | 需临时开启 SPI_SETSCREENREADER |
| 第三方依赖 | Pillow | uiautomation |
| 调试模式 | --debug 保存截图 | 无（日志输出） |
| 进程名 | "WorkBuddy" / "Electron" 双名称 | "WorkBuddy.exe" |

---

## 自动化配置

### 方式一：WorkBuddy 自动化任务（推荐）

统一入口，两个平台用同一条命令。已配置每日定时执行。

### 方式二：macOS launchd

创建 `~/Library/LaunchAgents/com.workbuddy.checkin.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.workbuddy.checkin</string>
    <key>ProgramArguments</key>
    <array>
        <string>osascript</string>
        <string>-e</string>
        <string>do shell script "python3 ~/.workbuddy/scripts/wb_checkin.py"</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/wb_checkin_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/wb_checkin_stderr.log</string>
</dict>
</plist>
```

加载：`launchctl load ~/Library/LaunchAgents/com.workbuddy.checkin.plist`
卸载：`launchctl unload ~/Library/LaunchAgents/com.workbuddy.checkin.plist`

> launchd 方式需在辅助功能和屏幕录制中为 `osascript` 授权。

### 方式三：Windows 计划任务

```cmd
schtasks /create /tn "WorkBuddy签到" /tr "python %USERPROFILE%\.workbuddy\scripts\wb_checkin.py" /sc daily /st 09:00 /f
```

或通过任务计划程序 GUI 创建：
1. 打开「任务计划程序」
2. 创建基本任务 → 名称"WorkBuddy签到"
3. 触发器：每天 09:00
4. 操作：启动程序 → `python`，参数 `~/.workbuddy/scripts/wb_checkin.py`

---

## 积分规则

- 每日签到 +100 积分
- 连续第 7 天额外 +1000 积分

---

## 故障排查

### macOS

**"错误: 需要 Pillow 库"**：`pip3 install Pillow`

**截图空白**：屏幕录制权限未授予。系统设置 → 隐私与安全性 → 屏幕录制。

**"无法获取 WorkBuddy 窗口"**：辅助功能权限不足或 WorkBuddy 未运行。检查辅助功能列表；确保客户端已启动并登录。

**"未找到深色可点击按钮"**：用 `--debug` 查看 `02_window.png`；检查 `CARD_SEARCH` 是否覆盖卡片位置；可能今日已签到。

**点击后无反应**：用 `--debug` 查看 `03_after_click_*.png`；检查日志中缩放因子是否正确（应 2.0 或 1.0）。

### Windows

**"未找到用户头像入口"**：脚本会自动重试 4 次；检查 `USER_ITEM_KEYWORDS` 是否匹配 WorkBuddy 显示名。

**"未找到签到/领取按钮"**：检查 `CHECKIN_ENTRY_KEYWORDS` 等关键词；WorkBuddy 界面可能改版。

**启动失败**：修改 `WORKBUDDY_EXE` 为实际安装路径。

**权限问题**：确保脚本以管理员权限运行；检查 `SPI_SETSCREENREADER` 调用是否被拦截。
