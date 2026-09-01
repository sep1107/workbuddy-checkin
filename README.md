# WorkBuddy 每日签到脚本 (跨平台)

自动完成 WorkBuddy 客户端每日签到领积分，支持 macOS 和 Windows。

## 工作原理

WorkBuddy 是 Electron 应用，不同操作系统下 UI 自动化方案不同：

- **macOS**：Electron 不暴露辅助功能树给 System Events，因此采用**截屏分析 + CGEvent 鼠标模拟**——用 `screencapture` 截图，Pillow 图像分析定位"Buddy加油站"深色卡片，通过 CoreGraphics C API 模拟点击，再用截图对比验证签到结果。
- **Windows**：通过 `uiautomation` 库访问 UI Automation 辅助功能树，临时开启系统屏幕阅读器标志激活 Electron accessibility 树，按控件名称定位头像和签到按钮。

统一入口脚本 `wb_checkin.py` 通过 `sys.platform` 自动选择平台实现，无需手动区分。

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/sep1107/workbuddy-checkin.git
cd workbuddy-checkin/scripts
```

### 2. 安装依赖

**macOS**：
```bash
pip3 install Pillow
```

**Windows**：
```cmd
pip install uiautomation
```

### 3. 配置（仅 Windows）

打开 `scripts/wb_checkin_windows.py`，修改以下配置：

```python
WORKBUDDY_EXE = r"C:\Program Files\WorkBuddy\WorkBuddy.exe"  # ← 改成你的安装路径
USER_ITEM_KEYWORDS = ["你的昵称"]  # ← 改成你在 WorkBuddy 里显示的昵称
```

macOS 无需配置，开箱即用。

### 4. 运行

```bash
# 统一入口（自动选择平台）
python3 scripts/wb_checkin.py

# macOS 调试模式（保存各阶段截图到 /tmp/wb_checkin_debug/）
python3 scripts/wb_checkin.py --debug

# macOS dry-run（只检测按钮位置，不执行点击）
python3 scripts/wb_checkin.py --dry-run
```

日志输出到 `~/.workbuddy/scripts/checkin.log`。

## 权限设置

### macOS

| 权限 | 路径 | 用途 |
|------|------|------|
| 辅助功能 | 系统设置 → 隐私与安全性 → 辅助功能 | System Events 获取窗口位置 + CGEvent 鼠标点击 |
| 屏幕录制 | 系统设置 → 隐私与安全性 → 屏幕录制 | screencapture 截取 WorkBuddy 窗口 |

将运行脚本的程序（Terminal / iTerm / WorkBuddy）添加到列表并开启。

### Windows

- 以管理员权限运行脚本（`SPI_SETSCREENREADER` 需要管理员权限）
- 确保 WorkBuddy 客户端已登录

## 定时任务

### macOS（launchd）

```bash
cat > ~/Library/LaunchAgents/com.workbuddy.checkin.plist << 'EOF'
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
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.workbuddy.checkin.plist
```

### Windows（计划任务）

```cmd
schtasks /create /tn "WorkBuddy签到" /tr "python %USERPROFILE%\.workbuddy\scripts\wb_checkin.py" /sc daily /st 09:00 /f
```

## 文件结构

```
scripts/
  wb_checkin.py            ← 跨平台入口（自动选择平台脚本）
  wb_checkin_macos.py      ← macOS 实现（截屏分析 + CGEvent）
  wb_checkin_windows.py    ← Windows 实现（uiautomation 辅助功能树）
references/
  wb_checkin_说明.md        ← 详细设置、配置、故障排查指南
```

## 技术细节

### macOS 坐标体系

| 坐标系 | 使用者 | 单位 |
|--------|--------|------|
| Points（逻辑坐标） | System Events 窗口位置、CGEvent 点击 | 1x |
| Pixels（物理像素） | screencapture 截图 | Retina 2x |

转换：`screen_point = window_origin + pixel_offset / scale_factor`

缩放因子在运行时通过比较 `CGDisplayBounds`（points）和 `screencapture` 尺寸（pixels）自动检测。

### macOS 按钮检测算法

新版（v5.3+）卡片为浅色背景，两种按钮状态**互斥检测**：

| 状态 | 函数 | 判据 | 视觉 |
|------|------|------|------|
| 未签到 | `find_dark_button` | 深色连通区域（grayscale < 55），长宽比 1.5-12，位于搜索区左下 | 深色「立即领取」按钮 |
| 已签到 | `find_claimed_button` | 灰色连通区域（grayscale 70-240），长宽比 1.5-10 | 灰色「今日已领」按钮 |

单个函数内部流程：

1. 裁剪 WorkBuddy 窗口左下角搜索区域（`CARD_SEARCH = (0.02, 0.35, 0.72, 0.95)`）
2. 灰度化，构建二值掩码
3. BFS 连通区域分析（8-邻域），找到独立区域
4. 按位置偏左下（55-60%）、上下文（25%）、形状（20%）打分
5. 返回得分最高的区域作为目标按钮

### v3.4 关键行为：先验证再弹窗

v5.4.x 起 WorkBuddy 点击「立即领取」后**直接领取、无确认弹窗**。因此 v3.4 把
验证提到弹窗处理之前：

```
点击「立即领取」
   ↓ 等待 2.5s + 重新截屏
find_claimed_button 检出「今日已领」?
   ├─ 是 → 签到成功，返回 True
   └─ 否 → 才走 find_bright_button 弹窗分支
              ↓ 点击弹窗按钮后再验证一次
              ├─ 检出「今日已领」→ 成功
              └─ 仍未检出 → WARNING + 返回 False
```

旧版（≤ v3.3）只看窗口像素差异 > 1% 就去找"弹窗按钮"，而
`ensure_running` / `activate_app` 造成的 2%+ 全局差异会触发假阳性，
导致在主界面点错位置——日志报"签到流程完成"但实际未签到。
v3.4 起只有**在界面上真的检出「今日已领」**才算成功。

### 两平台对比

| 项目 | macOS | Windows |
|------|-------|---------|
| UI 自动化 | 截屏分析 + CGEvent | uiautomation 辅助功能树 |
| 按钮定位 | 颜色+形状+位置图像分析 | 按 Name 属性匹配控件 |
| 鼠标点击 | ctypes → CoreGraphics CGEvent | ctypes.windll.user32 |
| 截图验证 | Pillow 截图对比 | 读取控件属性变化 |
| 屏幕阅读器标志 | 不需要 | 需临时开启 SPI_SETSCREENREADER |
| 依赖 | Pillow | uiautomation |

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v3.4 | 2026-09-02 | **关键修复**：主流程改「先验证再弹窗」。适配 v5.4.x「立即领取」直接领取无弹窗；修复 `ensure_running`/`activate_app` 全局差异触发 `find_bright_button` 假阳性导致点错位置、日志报成功但实际未签到的问题；弹窗分支点击后同样二次验证，未检出「今日已领」报 WARNING 并返回 False |
| v3.3 | 2026-09-01 | 头像坐标改右上角 `(0.91, 0.04)`（旧左下角会点中"新版本就绪"横幅）；移除"亮色按钮质心"随机点击回退，改为安全 abort；配合 `wb_checkin.sh` wrapper 隔离解释器依赖 |
| v3.2 | 2026-08-17 | 回退流程重构；`find_bright_button` 增加 `win_rect` 限制搜索范围，防止点到其他应用窗口 |
| v3.1 | 2026-08-17 | 去除 System Events/AppleScript 硬依赖，改用 `CGWindowListCopyWindowInfo` + `pgrep` + `open -a` |

## 积分规则

- 每日签到 +100 积分
- 连续第 7 天额外 +1000 积分

## License

MIT
