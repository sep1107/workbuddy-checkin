#!/usr/bin/env python3
"""兼容原 Windows 入口，转交带日志的新版执行器。"""
import runpy
import sys
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Windows 签到脚本只能在 Windows 上运行")

target = Path(__file__).resolve().parents[1] / "windows/workbuddy-desktop-checkin/scripts/run_checkin.py"
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
