---
name: workbuddy-desktop-checkin
description: 在 Windows 上通过 comtypes UI Automation 完成 WorkBuddy 桌面端每日签到，支持隔离环境、计划任务、结果日志和界面诊断。
---

# WorkBuddy Windows 每日签到

先确保 WorkBuddy 已登录，当前用户会话已解锁。脚本会占用鼠标，避免多个入口同时运行。

1. 用 `python scripts/setup_env.py` 安装隔离环境；`--print` 可打印解释器路径。
2. 使用隔离解释器运行 `scripts/run_checkin.py --dry --json`，只导航读状态。
3. 正式执行 `scripts/run_checkin.py --json`，自动拉起应用并写入 logs。
4. 只有 `success` / `already_claimed` 算完成；`dry_ok` 未领取，`uncertain` 不可报告成功。
5. 需要用户指定定时时，再运行 `scripts/install_task.py` 注册；`--verify` 查询，`--uninstall` 卸载，`--force` 明确覆盖同名任务。

当日结果 `logs/result-YYYY-MM-DD.json`，子流程详情在 `checkin` 字段。兜底应读取与主任务同一部署目录的日志，未完成时也使用 `run_checkin.py`，确保结果落盘。

`checkin.py --json` 仅输出 JSON、不落盘；`run_checkin.py --json` 输出文本日志并附加 JSON 行。两者支持 `--dry` / `--dry-run`。

定位使用有效矩形、左列区域、精确文本与 `fuel-expanded-claim` 控件 ID；不依赖用户名、不猜坐标，不点击认证入口 `fuel-action`。进度占位 `(0,0)` 不用于计算增量。界面变化时使用 `scripts/dump_uia.py` 和 `references/ui-map.md` 排查。

计划任务仅在交互会话执行，锁屏时跳过；错过时间可补跑，但解锁本身不保证重试。IgnoreNew 只约束同一计划任务，兜底需错开时间。
