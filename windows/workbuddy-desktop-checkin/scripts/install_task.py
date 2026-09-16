"""把 WorkBuddy 每日签到注册成 Windows 计划任务（系统自带，主执行入口）。

为什么用 XML 而不是 `schtasks /Create ... /SC DAILY`：
    命令行开关无法表达 StartWhenAvailable / IgnoreNew / 电池策略等关键设置，
    而这些恰恰决定了「开机晚了能不能补跑」「和兜底任务会不会重复点击」。

用法（用技能自带的解释器跑）:
    python install_task.py                # 注册（默认每天 09:00）
    python install_task.py --time 08:30   # 指定时间
    python install_task.py --verify       # 查看任务详情与下次运行时间
    python install_task.py --run          # 立即手动触发一次（用于试跑）
    python install_task.py --uninstall    # 删除任务
    python install_task.py --print-xml    # 只打印任务定义，不注册

注意：注册/删除需要调用系统 schtasks.exe。若该程序被安全策略拦截，
      请在系统终端（PowerShell / cmd）里手动执行本脚本。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
TASK_NAME = "WorkBuddyDailyCheckin"
# 按当前用户推导隔离环境路径，不可写死用户名——否则换台电脑（用户名不同）
# 注册出来的任务会指向不存在的解释器，而且 check_env() 会直接中止注册。
VENV = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default"
PYTHONW = VENV / "Scripts" / "pythonw.exe"
PYTHON = VENV / "Scripts" / "python.exe"
RUNNER = SCRIPTS_DIR / "run_checkin.py"

XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>{date}</Date>
    <Author>{user}</Author>
    <Description>WorkBuddy 桌面端每日签到（主执行入口）。调用 skill workbuddy-desktop-checkin 的
run_checkin.py：先检测会话是否锁定与 WorkBuddy 是否运行，再用 UI Automation 完成签到，
结果写入 skill 的 logs 目录。幂等，重复运行不会重复领取。</Description>
    <URI>\\{task}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{date}T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{runner}"</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def run(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="gbk",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return -1, "", f"找不到程序: {args[0]}（可能被安全策略拦截）"
    except Exception as e:  # noqa: BLE001
        return -1, "", f"{type(e).__name__}: {e}"


def current_user() -> str:
    rc, out, _ = run(["whoami"])
    return out.strip() if rc == 0 and out.strip() else rf"{Path.home().drive[0]}:x"


def build_xml(time_str: str, user: str, date_str: str) -> str:
    return XML_TEMPLATE.format(
        date=escape(date_str), time=escape(time_str), user=escape(user),
        task=TASK_NAME,
        pythonw=escape(str(PYTHONW if PYTHONW.exists() else PYTHON)),
        runner=escape(str(RUNNER)), workdir=escape(str(SKILL_DIR)),
    )


def check_env() -> bool:
    ok = True
    for label, p in (("python.exe", PYTHON), ("pythonw.exe", PYTHONW), ("run_checkin.py", RUNNER)):
        exists = p.exists()
        print(f"  [{'OK ' if exists else 'MISS'}] {label:16} {p}")
        ok &= exists
    rc, out, err = run([str(PYTHON), "-c",
                        "import comtypes,PIL;print('deps ok')"]) if PYTHON.exists() else (-1, "", "")
    print(f"  [{'OK ' if rc == 0 else 'MISS'}] 依赖 comtypes/PIL  {(out or err).strip()}")
    if rc != 0:
        print("       → 先运行: python scripts/setup_env.py")
        ok = False
    return ok


def verify() -> bool:
    rc, out, err = run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if rc != 0:
        print(f"  查询失败: {err or out}")
        return False
    keep = ("TaskName", "任务名", "Status", "状态", "Next Run Time", "下次运行时间",
            "Last Run Time", "上次运行时间", "Last Result", "上次结果",
            "Task To Run", "要运行的任务", "Schedule", "计划", "Schedule Type", "计划类型",
            "Start Time", "开始时间", "Logon Mode", "登录模式")
    for line in out.splitlines():
        s = line.strip()
        if any(s.startswith(k) for k in keep):
            print("  " + s)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 WorkBuddy 每日签到计划任务")
    ap.add_argument("--time", default="09:00", help="每日触发时间 HH:MM")
    ap.add_argument("--user", default=None, help="运行身份，默认当前用户")
    ap.add_argument("--date", default=None, help="生效起始日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--verify", action="store_true", help="只查询任务详情")
    ap.add_argument("--run", action="store_true", help="立即触发一次")
    ap.add_argument("--uninstall", action="store_true", help="删除任务")
    ap.add_argument("--print-xml", action="store_true", help="只打印 XML，不注册")
    ap.add_argument("--force", action="store_true", help="/F 覆盖同名任务")
    args = ap.parse_args()

    from datetime import date as _date
    user = args.user or current_user()
    date_str = args.date or _date.today().isoformat()

    if args.verify:
        print(f"== 任务 {TASK_NAME} ==")
        return 0 if verify() else 2

    if args.run:
        rc, out, err = run(["schtasks", "/Run", "/TN", TASK_NAME])
        print(f"触发结果 rc={rc} {out} {err}".strip())
        if rc == 0:
            print(f"  稍后查看日志: {SKILL_DIR / 'logs'}")
        return 0 if rc == 0 else 2

    if args.uninstall:
        rc, out, err = run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        print(f"删除结果 rc={rc} {out} {err}".strip())
        return 0 if rc == 0 else 2

    xml = build_xml(args.time, user, date_str)
    if args.print_xml:
        print(xml)
        return 0

    print("== 1. 环境检查 ==")
    if not check_env():
        print("!! 环境不完整，已中止注册")
        return 3

    print("\n== 2. 写入任务定义 ==")
    tmp = Path(tempfile.gettempdir()) / f"{TASK_NAME}.xml"
    tmp.write_text(xml, encoding="utf-16")  # 计划任务 XML 规范编码为 UTF-16
    print(f"  {tmp} ({tmp.stat().st_size} 字节, UTF-16)")

    print("\n== 3. 注册 ==")
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(tmp)]
    if args.force:
        cmd.append("/F")
    rc, out, err = run(cmd)
    print(f"  rc={rc} {out} {err}".strip())
    if rc != 0:
        print("\n!! 注册失败。若提示被拦截，请直接在系统终端（PowerShell/cmd）里运行:")
        print(f'   schtasks /Create /TN {TASK_NAME} /XML "{tmp}" /F')
        return 3

    print("\n== 4. 校验 ==")
    if not verify():
        return 3
    print(f"\n完成：每天 {args.time} 自动签到。日志目录 {SKILL_DIR / 'logs'}")
    print(f"手动试跑：schtasks /Run /TN {TASK_NAME}   或  python {RUNNER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
