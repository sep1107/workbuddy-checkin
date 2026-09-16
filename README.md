# WorkBuddy 每日签到脚本 (跨平台)

自动完成 WorkBuddy 客户端每日签到领积分，支持 macOS 和 Windows。

## 工作原理

WorkBuddy 是 Electron 应用，不同操作系统下 UI 自动化方案不同：

- **macOS**：Electron 不暴露辅助功能树给 System Events，因此采用**截屏分析 + CGEvent 鼠标模拟**——用 `screencapture` 截图，Pillow 图像分析定位"Buddy加油站"深色卡片，通过 CoreGraphics C API 模拟点击，再用截图对比验证签到结果。
- **Windows**：见下方 Windows 新版安装流程。

### 4. 运行

```bash
# 统一入口（自动选择平台）
python3 scripts/wb_checkin.py

# macOS 调试模式（保存各阶段截图到 /tmp/wb_checkin_debug/）
python3 scripts/wb_checkin.py --debug

# macOS dry-run（只检测按钮位置，不执行点击）
python3 scripts/wb_checkin.py --dry-run
```

macOS 日志输出到 `~/.workbuddy/scripts/checkin.log`；Windows 日志位置见下文。

## 权限设置

### macOS

| 权限 | 路径 | 用途 |
|------|------|------|
| 辅助功能 | 系统设置 → 隐私与安全性 → 辅助功能 | System Events 获取窗口位置 + CGEvent 鼠标点击 |
| 屏幕录制 | 系统设置 → 隐私与安全性 → 屏幕录制 | screencapture 截取 WorkBuddy 窗口 |

将运行脚本的程序（Terminal / iTerm / WorkBuddy）添加到列表并开启。

### Windows

- 在已登录且未锁屏的交互会话中运行；确保 WorkBuddy 客户端已登录。

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

## 文件结构

- `scripts/wb_checkin.py`：跨平台入口。
- `scripts/wb_checkin_macos.py`：macOS 实现。
- `scripts/wb_checkin_windows.py`：Windows 兼容入口，转交新版日志执行器。
- `windows/`：可独立复制的 Windows 便携包、五个 CMD 入口及使用说明。
- `windows/workbuddy-desktop-checkin/`：Windows Skill、六个 Python 模块和 UI 地图。
- `tests/test_windows.py`：无需 Windows 桌面的回归测试。

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
| 已签到 | `find_claimed_button` | 灰色连通区域（grayscale 70-240），长宽比 1.5-10，**实心度 density ≥ 0.5**（v3.6） | 灰色「今日已领」按钮 |

> ⚠️ **v3.6 判定权威**：**先跑 `find_dark_button`**。深色「立即领取」存在 ⇔ 未签到 → 必须点击。
> 仅当它不存在时，才用 `find_claimed_button` 判"今日已签"。
> 原因：5.5.x 卡片里常驻一个白底灰描边的「认证领积分」按钮，其边框 density≈0.04 曾被
> `find_claimed_button` 误判为"实心灰今日已领"，导致脚本每次直接返回"已签到"而**从不点击**。

单个函数内部流程：

1. 裁剪 WorkBuddy 窗口左下角搜索区域（`CARD_SEARCH = (0.02, 0.35, 0.72, 0.95)`）
2. 灰度化，构建二值掩码
3. BFS 连通区域分析（8-邻域），找到独立区域
4. 按位置偏左下（55-60%）、上下文（25%）、形状（20%）打分
5. 返回得分最高的区域作为目标按钮

### v3.5 入口路径：侧边栏导航（WorkBuddy 5.5.x）

签到入口从"右上角头像"迁到侧边栏：

```
展开侧边栏（若隐藏）→ 左下角头像 → 头像菜单首项区 "Buddy加油站" → 底部签到卡片
```

> ⚠️ **v3.7 坐标注意**：WorkBuddy 5.5.6 起头像菜单增至「体验版 / 积分余额 / Buddy加油站 /
> 主邀约 / 成长计划 / 设置 / …」，"Buddy加油站" 下移至 `MENU_BUDDY_RATIO = (0.088, 0.385)`
> （旧值 0.339 会点中"积分余额"，打开设置面板，签到卡片不出现）。
> 若再次连续失败且日志报"导航后仍未找到签到入口"，多半是菜单又变了：跑
> `bash ~/.workbuddy/scripts/wb_checkin.sh --debug`，看 `p1`/`03_after_nav` 截图重测菜单项 ratio。

- 坐标：`SIDEBAR_TOGGLE_RATIO=(0.075,0.034)`、`AVATAR_RATIO=(0.041,0.950)`、`MENU_BUDDY_RATIO=(0.088,0.339)`。
- ⚠️ 点"Buddy加油站"必须用 `cg_click_nomove()`：Electron 头像下拉菜单"锚定型"，`cg_click` 会先移光标离开头像 → 菜单即关 → 点击落到下层项（曾误触"定时任务"页）。

### v3.4 关键行为：先验证再弹窗

v5.4.x 起 WorkBuddy 点击「立即领取」后**直接领取、无确认弹窗**。因此 v3.4 把
验证提到弹窗处理之前：

```
先 find_dark_button（深色「立即领取」）           ← v3.6：深色按钮优先
   ├─ 存在 → 点击「立即领取」
   │            ↓ 等待 2.5s + 重新截屏
   │         检出「今日已领」? → 成功
   │         否则「立即领取」已消失? → 成功（弱信号）
   │         否则 → WARNING + 返回 False
   └─ 不存在 → find_claimed_button 检出「今日已领」? → 今日已签到
```

旧版（≤ v3.3）只看窗口像素差异 > 1% 就去找"弹窗按钮"，而
`ensure_running` / `activate_app` 造成的 2%+ 全局差异会触发假阳性，
导致在主界面点错位置——日志报"签到流程完成"但实际未签到。
v3.4 起只有**在界面上真的检出「今日已领」**才算成功。

### 两平台对比

| 项目 | macOS | Windows |
|------|-------|---------|
| UI 自动化 | 截屏分析 + CGEvent | comtypes UIAutomationCore |
| 按钮定位 | 颜色+形状+位置图像分析 | 控件 ID + 精确文案 + 区域过滤 |
| 鼠标点击 | ctypes → CoreGraphics CGEvent | ctypes.windll.user32 |
| 截图验证 | Pillow 截图对比 | 读取控件属性变化 |
| 屏幕阅读器标志 | 不需要 | 不修改系统标志 |
| 依赖 | Pillow | comtypes、pillow |

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v3.7 | 2026-09-16 | **macOS 关键修复**：校准 WorkBuddy 5.5.6 头像菜单坐标——菜单重构后 "Buddy加油站" 从 `MENU_BUDDY_RATIO` 0.339 下移至 0.385，旧坐标点中的是"积分余额"（打开设置→套餐与积分面板），签到卡片永不出现，导致 09-12~09-16 连续报"导航后仍未找到签到入口"。另整合 Windows 便携包、comtypes UIA、计划任务、日志与测绘工具；修正状态过滤、JSON 输出、UTF-8 子进程输出、64 位桌面句柄与 XML 路径转义。 |
| v3.6 | 2026-09-11 | **关键修复**：`find_claimed_button` 误报「今日已领」。5.5.x 卡片常驻「认证领积分」白底灰描边按钮（density≈0.04）被灰度连通域当成实心灰按钮，导致脚本每次直接返回"已签到"而从不点击。修复：① 加实心度门槛 `density >= 0.5`；② 判定权威改为"深色「立即领取」按钮优先"（存在即未签到）；③ 点击后验证补"按钮消失"弱信号 |
| v3.5 | 2026-09-10 | 适配 WorkBuddy 5.5.x：签到入口改「侧边栏 → 头像 → Buddy加油站」；新增 `cg_click_nomove()` 解决 Electron 下拉菜单"光标离开锚点即关闭"；新增 `is_sidebar_open()` 判侧边栏状态 |
| v3.4 | 2026-09-02 | **关键修复**：主流程改「先验证再弹窗」。适配 v5.4.x「立即领取」直接领取无弹窗；修复 `ensure_running`/`activate_app` 全局差异触发 `find_bright_button` 假阳性导致点错位置、日志报成功但实际未签到的问题；弹窗分支点击后同样二次验证，未检出「今日已领」报 WARNING 并返回 False |
| v3.3 | 2026-09-01 | 头像坐标改右上角 `(0.91, 0.04)`（旧左下角会点中"新版本就绪"横幅）；移除"亮色按钮质心"随机点击回退，改为安全 abort；配合 `wb_checkin.sh` wrapper 隔离解释器依赖 |
| v3.2 | 2026-08-17 | 回退流程重构；`find_bright_button` 增加 `win_rect` 限制搜索范围，防止点到其他应用窗口 |
| v3.1 | 2026-08-17 | 去除 System Events/AppleScript 硬依赖，改用 `CGWindowListCopyWindowInfo` + `pgrep` + `open -a` |

## 积分规则

- 每日签到 +100 积分
- 连续第 7 天额外 +1000 积分

## License

MIT
