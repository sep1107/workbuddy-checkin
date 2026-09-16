"""WorkBuddy 桌面端每日签到（UIA 驱动，无硬编码坐标）。

用法:
    python checkin.py              # 执行签到
    python checkin.py --dry        # 只导航 + 读状态，不点击
    python checkin.py --json       # 结果以 JSON 输出（便于自动化编排）
    python checkin.py --exe "C:/Program Files/WorkBuddy/WorkBuddy.exe"

退出码: 0=已签到或本次签到成功; 2=需要用户介入(未登录/界面改版); 3=执行异常

设计要点见 references/ui-map.md，底层能力见 scripts/uia_win.py。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uia_win as uw  # noqa: E402

# ---------- WorkBuddy 界面常量（改版时优先在此修正） ----------
EXE_NAME = "workbuddy.exe"
TITLE_HINT = "WorkBuddy"
FUEL_ENTRY_TEXT = "Buddy加油站"          # 账号菜单里的入口行
CLAIM_AID = "fuel-expanded-claim"        # 签到按钮 automationId（文案会变）
CLAIM_READY_TEXT = "立即领取"             # 可领取态
CLAIM_DONE_TEXT = "今日已领"              # 今日已领态
CLAIM_DONE_ALT = "今日已签到"             # 备用已签文案
VERIFY_BTN_TEXT = "认证领积分"            # 需实名认证，禁止自动点击
LEFT_COL_MAX_X = 0                     # 运行时按窗口左边界计算


def log(msg, quiet=False, force=False):
    if not quiet:
        print(msg, flush=True)


def find_user_entry(root, win_rect):
    """定位左下角账号入口。

    不依赖用户名（i18n / 改名都不影响）：取窗口底部区域内唯一的 MenuItem。
    """
    _, t, _, b = win_rect
    cands = []
    for e in uw.walk_all(root):
        try:
            if e.CurrentControlType != uw.ctype_id("MenuItemControlTypeId"):
                continue
            x, y, w, h = uw.rect_of(e)
            if w > 100 and h > 40 and y > b - 160:
                cands.append(e)
        except Exception:
            continue
    return cands


def read_balance(root, left_max_x):
    """读账号菜单里的积分余额。

    只认「名称以 积分余额 开头的 Button」（菜单里的真实余额行，
    如 '积分余额 刷新 2,119.36'）。聊天正文里可能出现
    '……积分余额为 1927.81……' 这类历史对话文本，若不做
    控件类型 + 前缀约束会被误读为当前余额。
    """
    for e in uw.walk_all(root):
        try:
            name = (e.CurrentName or "").strip()
            if not name.startswith("积分余额"):
                continue
            if e.CurrentControlType != uw.ctype_id("ButtonControlTypeId"):
                continue
            x, _, w, _ = uw.rect_of(e)
            if x > left_max_x or w <= 4:
                continue
            m = re.search(r"([\d,]+\.\d+)", name)
            if m:
                return float(m.group(1).replace(",", ""))
        except Exception:
            continue
    return None


def read_progress(root, left_max_x):
    """读加油站弹窗的「已领 N 天 | 累计领取 N 分」。

    该行在无障碍树里被拆成多个文本节点（'已领' / '1' / '天' / ...），
    因此先定位 '已领' 锚点，再取同一行的数字。
    """
    anchor = None
    texts = []
    for e in uw.walk_all(root):
        try:
            name = (e.CurrentName or "").strip()
            if not name:
                continue
            x, y, w, h = uw.rect_of(e)
            if w <= 4 or x > left_max_x:
                continue
            texts.append((x, y, name))
            if name == "已领":
                anchor = (x, y)
        except Exception:
            continue
    if not anchor:
        return None
    ay = anchor[1]
    row = sorted([t for t in texts if abs(t[1] - ay) < 12], key=lambda t: t[0])
    nums = [t[2] for t in row if re.fullmatch(r"\d+", t[2])]
    return (int(nums[0]), int(nums[1])) if len(nums) >= 2 else None


def claim_state(root, left_max_x=None):
    """判定加油站弹窗当前状态: ready(可领) / claimed(已领) / unknown。

    传入 left_max_x 时限定左列弹窗区域判定：聊天正文里可能出现「今日已领」
    等字样（历史对话内容也在无障碍树里），不做区域过滤会误判为已签到。
    已领文案优先于按钮 aid（领取后 aid 可能仍存在）。
    """
    def in_area(x, w, h):
        return w > 4 and h > 4 and (left_max_x is None or x <= left_max_x)

    ready = False
    for e in uw.walk_all(root):
        try:
            name = (e.CurrentName or "").strip()
            aid = e.CurrentAutomationId or ""
            x, y, w, h = uw.rect_of(e)
        except Exception:
            continue
        if not in_area(x, w, h):
            continue
        if aid == CLAIM_AID and name in (CLAIM_DONE_TEXT, CLAIM_DONE_ALT):
            return "claimed"
        if aid == CLAIM_AID and name == CLAIM_READY_TEXT:
            ready = True
    return "ready" if ready else "unknown"


def run(exe_path=None, dry=False, quiet=False):
    result = {"status": "unknown", "exe": exe_path, "steps": []}

    mw = uw.main_window(EXE_NAME, TITLE_HINT)
    if not mw:
        result["status"] = "no_window"
        result["hint"] = "未找到 WorkBuddy 窗口：请确认已启动并登录"
        return result
    hwnd, rect = mw["hwnd"], mw["rect"]
    left_max_x = rect[0] + 500
    log(f"[1] 主窗口 hwnd={hwnd} rect={rect} title={mw['title']!r}", quiet)
    result["window"] = {"hwnd": hwnd, "rect": list(rect), "title": mw["title"]}

    root = uw.element_of(hwnd)
    # 等到无障碍树就绪（以能否找到账号入口为判据，比数节点数更可靠）
    ok, _ = uw.wait_until(
        lambda: find_user_entry(uw.element_of(hwnd), rect) or None, timeout=20, step=1.0)
    root = uw.element_of(hwnd)
    n = uw.count_children(root)
    log(f"    无障碍树节点数={n}（Chromium 异步开启）", quiet)

    if not ok:
        if n < 10:
            result["status"] = "a11y_unavailable"
            result["hint"] = "UIA 无障碍树未暴露，无法读取控件；不要改用猜测坐标"
        else:
            result["status"] = "no_user_entry"
            result["hint"] = "无障碍树可读但找不到账号入口：请确认 WorkBuddy 已登录"
        return result

    # ---- 打开账号菜单（带重试） ----
    fuel = None
    for attempt in range(1, 4):
        entries = find_user_entry(root, rect)
        if not entries:
            uw.press_key()  # Esc
            time.sleep(1.0)
            root = uw.element_of(hwnd)
            entries = find_user_entry(root, rect)
        if not entries:
            result["status"] = "no_user_entry"
            result["hint"] = "未找到账号入口：请确认 WorkBuddy 已登录"
            return result
        x, y = uw.center(entries[0])
        log(f"[2] 第{attempt}次点开账号菜单 ({x},{y})", quiet)
        uw.click_at(x, y)
        time.sleep(3.0)  # 首次点击可能只用于聚焦窗口，留足菜单渲染时间
        root = uw.element_of(hwnd)
        fuel = [e for e in uw.visible_find(root, text=FUEL_ENTRY_TEXT, exact=True)
                if rect[0] <= uw.rect_of(e)[0] <= left_max_x]
        if fuel:
            break
        log("    菜单未出现，Esc 后重试", quiet)
        uw.press_key()
        time.sleep(1.2)
        root = uw.element_of(hwnd)

    if not fuel:
        result["status"] = "menu_failed"
        result["hint"] = "账号菜单打不开（界面可能改版，用 dump_uia.py 重新测绘）"
        return result

    result["balance_before"] = read_balance(root, left_max_x)
    log(f"    签到前积分余额: {result['balance_before']}", quiet)

    # ---- 进入 Buddy加油站 ----
    x, y = uw.center(fuel[0])
    log(f"[3] 点击「{FUEL_ENTRY_TEXT}」({x},{y})", quiet)
    uw.click_at(x, y)
    time.sleep(4)
    root = uw.element_of(hwnd)

    # 弹窗首帧可能只渲染出骨架（控件树里既无「立即领取」也无「今日已领」）。
    # 单次读空就下结论，会在无人值守场景制造假 ui_changed 告警（实测：连续两次
    # 运行中的前一次会读空，紧接着重跑即正常）。故重试数次再判定。
    state, prog_before = "unknown", None
    for attempt in range(1, 4):
        root = uw.element_of(hwnd)
        state = claim_state(root, left_max_x)
        prog_before = read_progress(root, left_max_x)
        if state != "unknown":
            break
        log(f"    未识别到签到按钮（第{attempt}次读），1.5s 后重试", quiet)
        time.sleep(1.5)
    # (0,0) 不是真实进度：弹窗刚打开时该行可能只渲染出占位 0，
    # 若照此计算 gained，会把累计分整体误报成本次所得（如 after=[5,500] → gained=5500）。
    # 进度读数不可信时置空，交由 state_after == claimed 作最终判定。
    if prog_before == (0, 0):
        log("    进度行读到占位 (0,0)，视为未读，gained 不可计算", quiet)
        prog_before = None
    result["state_before"] = state
    result["progress_before"] = list(prog_before) if prog_before else None
    log(f"[4] 状态={state} 进度={prog_before}", quiet)

    if state == "claimed":
        result["status"] = "already_claimed"
        log("[结果] 今日已签到，无需重复操作", quiet, force=True)
        return result

    if state == "unknown":
        result["status"] = "ui_changed"
        result["hint"] = "加油站弹窗结构变化，连续 3 次读取仍未识别到签到按钮；请用 dump_uia.py 重新测绘"
        return result

    if dry:
        result["status"] = "dry_ok"
        log("[dry] 未执行签到点击", quiet, force=True)
        return result

    btn = [e for e in uw.visible_find(root, automation_id=CLAIM_AID)
           if rect[0] <= uw.rect_of(e)[0] <= left_max_x
           and (e.CurrentName or "").strip() == CLAIM_READY_TEXT]
    if len(btn) != 1:
        result.update(status="ui_changed", hint="未找到唯一可领取按钮，请重新测绘")
        return result
    x, y = uw.center(btn[0])
    log(f"[5] 点击「{CLAIM_READY_TEXT}」({x},{y})", quiet)
    uw.click_at(x, y)
    time.sleep(4)
    root = uw.element_of(hwnd)

    prog_after = read_progress(root, left_max_x)
    state_after = claim_state(root, left_max_x)
    result["progress_after"] = list(prog_after) if prog_after else None
    result["state_after"] = state_after
    log(f"[6] 签到后 进度={prog_after} 状态={state_after}", quiet)

    gained = None
    if prog_before and prog_after:
        gained = prog_after[1] - prog_before[1]

    if state_after == "claimed" or (gained is not None and gained > 0):
        result["status"] = "success"
        result["gained"] = gained
        log(f"[结果] 签到成功，本次增加 {gained} 积分", quiet, force=True)
    else:
        result["status"] = "uncertain"
        result["hint"] = "点击后状态未确认，请人工核对界面"
        log("[结果] 状态未确认，请人工核对", quiet, force=True)

    # 收起弹窗，恢复界面
    try:
        uw.press_key()
    except Exception:
        pass
    return result


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 桌面端每日签到")
    ap.add_argument("--dry", "--dry-run", action="store_true", help="只导航和读状态，不点击签到")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--exe", default=None, help="WorkBuddy 安装目录（可选，仅用于日志）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        res = run(exe_path=args.exe, dry=args.dry, quiet=args.quiet or args.json)
    except Exception as e:  # noqa: BLE001
        res = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        if not args.json:
            print(f"!! 执行异常: {res['error']}", flush=True)
        print(json.dumps(res, ensure_ascii=False))
        return 3

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    code = {"success": 0, "already_claimed": 0, "dry_ok": 0}.get(res["status"], 2)
    return code


if __name__ == "__main__":
    sys.exit(main())
