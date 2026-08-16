#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 每日签到 - 跨平台入口
================================
根据操作系统自动选择对应平台脚本执行：
  - macOS  → wb_checkin_macos.py   (截屏分析 + CGEvent 鼠标模拟)
  - Windows → wb_checkin_windows.py (uiautomation 辅助功能树)

用法:
  python3 wb_checkin.py             # 正式签到
  python3 wb_checkin.py --debug     # 调试模式（macOS 保存截图）
  python3 wb_checkin.py --dry-run   # 只检测不点击（仅 macOS）

所有命令行参数原样传递给平台脚本。
"""

import sys
import os
import runpy

script_dir = os.path.dirname(os.path.abspath(__file__))

if sys.platform == 'darwin':
    target = os.path.join(script_dir, 'wb_checkin_macos.py')
elif sys.platform == 'win32':
    target = os.path.join(script_dir, 'wb_checkin_windows.py')
else:
    print(f"不支持的操作系统: {sys.platform}")
    print("仅支持 macOS (darwin) 和 Windows (win32)")
    sys.exit(1)

if not os.path.isfile(target):
    print(f"平台脚本不存在: {target}")
    sys.exit(1)

# 原样传递所有命令行参数
sys.argv[0] = target
runpy.run_path(target, run_name='__main__')
