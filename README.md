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
git clone https://github.com/sep1107/wb-checkin.git
cd wb-checkin/scripts
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

1. 裁剪 WorkBuddy 窗口左下角搜索区域
2. 灰度化，构建深色像素二值掩码（grayscale < 35）
3. BFS 连通区域分析（8-邻域），找到独立的深色区域
4. 按尺寸匹配度（45%）、位置偏左下（35%）、密度（20%）打分
5. 返回得分最高的区域作为目标按钮

### 两平台对比

| 项目 | macOS | Windows |
|------|-------|---------|
| UI 自动化 | 截屏分析 + CGEvent | uiautomation 辅助功能树 |
| 按钮定位 | 颜色+形状+位置图像分析 | 按 Name 属性匹配控件 |
| 鼠标点击 | ctypes → CoreGraphics CGEvent | ctypes.windll.user32 |
| 截图验证 | Pillow 截图对比 | 读取控件属性变化 |
| 屏幕阅读器标志 | 不需要 | 需临时开启 SPI_SETSCREENREADER |
| 依赖 | Pillow | uiautomation |

## 积分规则

- 每日签到 +100 积分
- 连续第 7 天额外 +1000 积分

## License

MIT
