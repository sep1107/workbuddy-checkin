"""计划任务入口：带文件日志、会话锁定检测、WorkBuddy 自动拉起。

与 checkin.py 的分工：
  checkin.py      —— 纯签到逻辑，输出到 stdout（人/agent 直接看）
  run_checkin.py  —— 无人值守入口，负责「让 checkin.py 能在计划任务里稳定跑」

为什么要这层壳（每条都是实测踩出来的）：
1. 计划任务没有控制台，stdout 会被丢弃 → 所有输出必须落盘，否则失败无法追溯。
2. 锁屏/未登录会话下 UI 注入点不到任何东西，会误判成「界面改版」→ 必须先探测
   输入桌面是否可用（OpenInputDesktop），锁屏时直接跳过并如实记录。
3. 计划任务触发时 WorkBuddy 可能还没启动（机器刚开机）→ 可自动拉起并等待窗口。
4. 需要一份机器可读的当日结果文件，供上游兜底任务判断「主入口是否已经成功」。

用法:
    python run_checkin.py                    # 正常执行（计划任务默认调用）
    python run_checkin.py --dry              # 只导航不点击
    python run_checkin.py --no-launch        # WorkBuddy 没开就放弃，不尝试拉起
    python run_checkin.py --ignore-lock      # 锁屏也照常尝试（默认跳过）

退出码:
    0  已签到 / 本次签到成功
    2  需人工介入（未登录、界面改版、状态未确认）
    3  执行异常
    4  已跳过（会话锁定 / WorkBuddy 未运行且不拉起）—— 视为「主入口未完成」，兜底应接手
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
LOG_DIR = SKILL_DIR / "logs"
DEFAULT_EXE = r"C:\Program Files\WorkBuddy\WorkBuddy.exe"

sys.path.insert(0, str(SCRIPTS_DIR))


def console_python() -> str:
    """从当前解释器推出带控制台的 python（pythonw.exe 无法回传子进程 stdout）。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def session_locked() -> bool:
    """输入桌面不可打开即视为锁屏/切换用户（此时无法向应用注入点击）。"""
    try:
        u = ctypes.windll.user32
        u.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        u.OpenInputDesktop.restype = wt.HANDLE
        u.CloseDesktop.argtypes = [wt.HANDLE]
        u.CloseDesktop.restype = wt.BOOL
        h = u.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if h:
            u.CloseDesktop(h)
            return False
        return True
    except Exception:
        return True  # 无法确认交互桌面可用时跳过


class Tee:
    """同时写 stdout 与日志文件。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")

    def write(self, s: str):
        try:
            if sys.stdout is not None:
                sys.stdout.write(s)
                sys.stdout.flush()
        except Exception:
            pass
        self.fh.write(s)
        self.fh.flush()

    def flush(self):
        try:
            self.fh.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


def ensure_running(exe_path: str, timeout: float, logf) -> bool:
    """WorkBuddy 未运行则拉起，返回是否最终有可用主窗口。"""
    import uia_win as uw

    if uw.main_window("workbuddy.exe", "WorkBuddy"):
        return True
    exe = Path(exe_path)
    if not exe.exists():
        logf(f"    WorkBuddy 未运行，且找不到可执行文件: {exe}")
        return False
    logf(f"    WorkBuddy 未运行，尝试拉起: {exe}")
    try:
        subprocess.Popen([str(exe)], close_fds=True)
    except Exception as e:  # noqa: BLE001
        logf(f"    拉起失败: {type(e).__name__}: {e}")
        return False
    ok, mw = uw.wait_until(lambda: uw.main_window("workbuddy.exe", "WorkBuddy"),
                           timeout=timeout, step=2.0)
    if ok:
        logf(f"    窗口已就绪 hwnd={mw['hwnd']}")
    else:
        logf(f"    等待窗口超时（{timeout:.0f}s）")
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser(description="计划任务入口：WorkBuddy 每日签到")
    ap.add_argument("--dry", "--dry-run", action="store_true", help="只导航读状态，不点击")
    ap.add_argument("--exe", default=DEFAULT_EXE, help="WorkBuddy 可执行文件路径")
    ap.add_argument("--no-launch", action="store_true", help="不自动拉起 WorkBuddy")
    ap.add_argument("--launch-timeout", type=float, default=90.0)
    ap.add_argument("--ignore-lock", action="store_true", help="锁屏时也继续尝试")
    ap.add_argument("--json", action="store_true", help="在日志末尾附加 JSON 结果")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_path = LOG_DIR / f"checkin-{now:%Y-%m}.log"
    tee = Tee(log_path)

    def logf(msg: str):
        tee.write(msg + "\n")

    started = time.time()
    logf(f"\n{'=' * 68}")
    logf(f"[{stamp}] 计划任务运行 run_checkin.py"
         f"  pid={__import__('os').getpid()}  dry={args.dry}  python={console_python()}")

    result = {
        "run_at": now.isoformat(timespec="seconds"),
        "driver": "windows_task_scheduler",
        "status": "unknown",
        "dry": args.dry,
    }

    def finish(status: str, code: int, **extra) -> int:
        result.update(status=status, exit_code=code, **extra)
        result["duration_sec"] = round(time.time() - started, 1)
        day_file = LOG_DIR / f"result-{now:%Y-%m-%d}.json"
        for p in (day_file, LOG_DIR / "last_result.json"):
            try:
                p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                logf(f"    !! 结果文件写入失败 {p.name}: {e}")
        logf(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 状态={status} 退出码={code} "
             f"耗时={result['duration_sec']}s")
        logf(f"    结果文件: {day_file}")
        if args.json:
            logf("JSON " + json.dumps(result, ensure_ascii=False))
        tee.close()
        return code

    # ---- 1. 会话可用性 ----
    if session_locked() and not args.ignore_lock:
        logf("[1] 会话处于锁定/切换状态 → 跳过（锁屏下无法注入点击，不是界面问题）")
        return finish("skipped_locked", 4,
                      hint="计划任务触发时用户会话被锁定；请在解锁后手动运行，"
                           "或依赖兜底任务在之后重试")

    # ---- 2. 应用可用性 ----
    try:
        if args.no_launch:
            import uia_win as uw
            if not uw.main_window("workbuddy.exe", "WorkBuddy"):
                logf("[2] WorkBuddy 未运行（--no-launch）→ 跳过")
                return finish("skipped_not_running", 4, hint="请先启动 WorkBuddy 桌面端")
        else:
            logf("[2] 确认 WorkBuddy 窗口可用…")
            if not ensure_running(args.exe, args.launch_timeout, logf):
                return finish("skipped_not_running", 4,
                              hint="WorkBuddy 未运行且拉起失败；请检查安装路径或手动启动")
    except Exception as e:
        return finish("error", 3, hint=f"应用检查失败: {type(e).__name__}: {e}")

    # ---- 3. 调用签到逻辑（子进程，隔离异常）----
    cmd = [console_python(), str(SCRIPTS_DIR / "checkin.py"), "--json"]
    if args.dry:
        cmd.append("--dry")
    logf(f"[3] 执行: {' '.join(cmd)}")
    try:
        p = subprocess.run(cmd, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        logf("    !! 子进程超时（300s）")
        return finish("error", 3, hint="签到子进程超时")

    except OSError as e:
        return finish("error", 3, hint=f"签到子进程启动失败: {e}")

    out = (p.stdout or "").strip()
    logf("---- checkin.py stdout ----")
    logf(out or "(空)")
    if p.stderr:
        logf("---- checkin.py stderr ----")
        logf(p.stderr.strip())

    payload = {}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    result["checkin"] = payload

    status = payload.get("status", "unknown")
    code = {"success": 0, "already_claimed": 0, "dry_ok": 0, "error": 3}.get(status, 2)
    if not payload:
        status, code = "error", 3
        result["hint"] = "checkin.py 未返回可解析的 JSON 结果"
    elif code == 2 and "hint" not in result:
        result["hint"] = payload.get("hint", "需人工介入")

    return finish(status, code)


if __name__ == "__main__":
    sys.exit(main())
