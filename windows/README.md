# Windows WorkBuddy 自动签到便携包

本目录可整体复制到 Windows 电脑，保留以下相对结构；源码整合自用户提供的 Windows 更新包。

## 快速使用

1. 安装 Python 3.9+ 并确保 `python` 可用；安装并登录 WorkBuddy。
2. `1_setup_env.cmd`：建立 `%USERPROFILE%\.workbuddy\binaries\python\envs\default`，安装 comtypes 和 pillow。
3. `3_run_checkin_now.cmd`：立即运行并写入日志；可带 `--dry` 只导航读状态。
4. `2_register_daily_task.cmd`：每天 09:00 运行，可传时间如 `08:30`。发现同名任务会先展示并提示确认覆盖。
5. `5_unregister_daily_task.cmd`：移除计划任务。

`4_install_as_skill.cmd` 可将内部 Skill 复制到 `%USERPROFILE%\.workbuddy\skills\workbuddy-desktop-checkin`。如果选择安装副本，应使用该副本的 `install_task.cmd` 注册任务，并让兜底读取同一副本日志。已有同名任务不会默认强制覆盖，确定迁移时在目标目录执行 `scripts/install_task.py --force`。不要在注册后移动或删除任务所指目录。

## 模块拆解

| 文件 | 职责 |
|---|---|
| `checkin.py` | 账号菜单 → Buddy加油站 → 领取 → 状态校验；`--json` 输出单个 JSON，不写日志 |
| `uia_win.py` | comtypes UIA 遍历、DPI 感知、窗口恢复、键鼠输入 |
| `run_checkin.py` | 锁屏检测、拉起应用、子进程执行、日志与结果落盘 |
| `setup_env.py` | 隔离 Python 环境及依赖安装 |
| `install_task.py` | 生成 XML，注册、查询、运行、卸载 Windows 计划任务 |
| `dump_uia.py` | UI 控件树与可选截图诊断 |

所有 Python 模块位于 `workbuddy-desktop-checkin/scripts/`。统一跨平台入口仍是仓库根目录的 `scripts/wb_checkin.py`。

## 执行与判定

在 Windows cmd 中：

```cmd
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "SKILL=<实际部署目录>\workbuddy-desktop-checkin"
"%PY%" "%SKILL%\scripts\run_checkin.py" --json
"%PY%" "%SKILL%\scripts\run_checkin.py" --dry-run --json
"%PY%" "%SKILL%\scripts\install_task.py" --verify
"%PY%" "%SKILL%\scripts\dump_uia.py" --aid fuel-expanded-claim
```

默认应用路径 `C:\Program Files\WorkBuddy\WorkBuddy.exe`，不同路径通过 `run_checkin.py --exe` 指定；自定义安装路径的计划任务需在任务操作参数里补上 `--exe`。

签到按钮使用 `fuel-expanded-claim` 的精确文案与有效矩形识别，点击后仍限制左列区域，避免聊天文字误判。`fuel-action` 为实名认证入口，不点击。进度 `(0,0)` 视为未读，不据此计算获得积分。主按钮变为已领，或可信进度积分增加时判定成功；其余报告 `uncertain`。

当日结果：`<SKILL>\logs\result-YYYY-MM-DD.json`；最近结果：`last_result.json`；月日志：`checkin-YYYY-MM.log`。详细签到输出位于结果的 `checkin` 字段。

| 状态 | 退出码 | 含义 |
|---|---|---|
| success / already_claimed | 0 | 已完成 |
| dry_ok | 0 | 试运行通过，未领取 |
| no_window / a11y_unavailable / no_user_entry / menu_failed / ui_changed / uncertain | 2 | 需人工检查 |
| error | 3 | 异常 |
| skipped_locked / skipped_not_running | 4 | 未完成，后续需重试 |

`run_checkin.py --json` 保留文本日志并在最后附加 `JSON {...}`；需要纯 JSON 时用 `checkin.py --json`。

## 计划任务与兜底

任务名 `WorkBuddyDailyCheckin`，当前用户交互会话、低权限、无需密码；支持错过计划时间后补跑和电池供电运行。锁屏跳过；解锁后手动运行或等兜底。`IgnoreNew` 仅限同一计划任务，无法防止手动脚本或 WorkBuddy 任务同时操作鼠标，应错开执行。

可在 WorkBuddy 设每天 10:30 的兜底任务，提示词：

```text
读取 <同一部署目录>/logs/result-<今天 YYYY-MM-DD>.json。
status 是 success 或 already_claimed 时结束。
文件不存在或其他状态时，使用隔离解释器运行该部署目录的 scripts/run_checkin.py --json。
如实汇报结果；skipped、uncertain 和 error 不算签到成功。
避免与手动执行或 Windows 计划任务同时操作鼠标。
```

若系统安全策略拦截 schtasks，改由用户在系统终端运行注册命令。脚本注册失败时打印 XML 路径及命令。

## 验证范围

原包附带 Windows 11 / WorkBuddy 5.5.6 / 150% DPI 的实测说明。此次整合在 macOS 完成语法与模拟控件回归检查，未重新进行 Windows 桌面点击、CMD 或任务计划程序实测。控件地图见 [ui-map.md](workbuddy-desktop-checkin/references/ui-map.md)。日志、截图和重复 ZIP 不进入 Git。
