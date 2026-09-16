"""无障碍树测绘工具：界面改版后用它重新定位元素。

用法:
    python dump_uia.py                      # 自动找 WorkBuddy 主窗口，全量 dump
    python dump_uia.py --grep 签到,积分      # 只显示命中关键词的元素
    python dump_uia.py --aid fuel-expanded-claim
    python dump_uia.py --screenshot out.png # 同时截图，便于和元素矩形对照

输出为「控制类型 / name / automationId / 物理矩形」四元组列表。
矩形是物理像素，和截图坐标一致（本模块已设 DPI 感知）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uia_win as uw  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="workbuddy.exe")
    ap.add_argument("--title", default="WorkBuddy")
    ap.add_argument("--grep", default=None, help="逗号分隔的关键词")
    ap.add_argument("--aid", default=None, help="按 automationId 精确过滤")
    ap.add_argument("--depth", type=int, default=0, help="保留参数，遍历为全量")
    ap.add_argument("--screenshot", default=None, help="截图输出路径")
    args = ap.parse_args()

    mw = uw.main_window(args.exe, args.title)
    if not mw:
        print("!! 未找到窗口，请确认应用已启动")
        return 2
    print(f"# 主窗口 hwnd={mw['hwnd']} pid={mw['pid']} rect={mw['rect']} title={mw['title']!r}")

    root = uw.element_of(mw["hwnd"])
    n = uw.wait_children(root, min_count=80, timeout=20)
    print(f"# 无障碍节点数={n}")
    if n < 10:
        print("!! a11y 未暴露：Chromium 可能未开启无障碍，重试或检查是否被安全软件拦截")
        return 2

    kws = [k for k in (args.grep.split(",") if args.grep else []) if k]
    shown = 0
    for e in uw.walk_all(root):
        try:
            name = e.CurrentName or ""
            aid = e.CurrentAutomationId or ""
            x, y, w, h = uw.rect_of(e)
        except Exception:
            continue
        if w <= 4 or h <= 4:
            continue
        if args.aid and aid != args.aid:
            continue
        if kws and not any(k in name for k in kws):
            continue
        print(f"{uw.ctype_name(e.CurrentControlType):<22} name={name!r:<50} "
              f"aid={aid!r:<26} ({x},{y},{w}x{h})")
        shown += 1
    print(f"# 命中 {shown} 个元素")

    if args.screenshot:
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(args.screenshot)
            print(f"# 截图已保存: {args.screenshot}")
        except Exception as ex:  # noqa: BLE001
            print(f"# 截图失败: {ex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
