# -*- coding: utf-8 -*-
"""
WorkBuddy 每日签到自动领取积分脚本
=====================================
流程:
  1. 确保 WorkBuddy 客户端已启动（未启动则拉起）
  2. 临时开启系统"屏幕阅读器"标志，激活 Electron/Chromium accessibility 树
  3. 点击左下角头像 → 打开个人中心菜单
  4. 点击"签到领积分"(Buddy 加油站) → 打开签到面板
  5. 判断签到状态:
     - 按钮为"今日已领" → 今日已签到，跳过
     - 按钮为"立即领取/领取" → 点击领取并确认
  6. 恢复屏幕阅读器标志，记录日志

日志: %USERPROFILE%/.workbuddy/scripts/checkin.log
"""
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime

import uiautomation as auto

WORKBUDDY_EXE = r"C:\Program Files\WorkBuddy\WorkBuddy.exe"
LOG_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "scripts")
LOG_FILE = os.path.join(LOG_DIR, "checkin.log")

SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_SENDCHANGE = 0x0002

USER_ITEM_KEYWORDS = ["你的昵称"]   # ← 改成你在 WorkBuddy 里显示的昵称
CHECKIN_ENTRY_KEYWORDS = ["签到领积分"]     # 个人中心菜单中的签到入口
CLAIMED_KEYWORDS = ["今日已领", "已领"]     # 已签到状态按钮
CLAIM_KEYWORDS = ["立即领取", "领取", "领"]  # 未签到时的领取按钮


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def set_screen_reader(on):
    """设置/恢复系统屏幕阅读器标志，返回旧值"""
    old = ctypes.c_bool(False)
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETSCREENREADER, 0, ctypes.byref(old), 0)
        val = ctypes.c_bool(bool(on))
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETSCREENREADER, 1 if on else 0, ctypes.byref(val), SPIF_SENDCHANGE)
        return old.value
    except Exception:
        return None


def real_click(x, y):
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def is_process_running(exe_name="WorkBuddy.exe"):
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
        return exe_name.lower() in out.lower()
    except Exception:
        return True  # 无法确认时假设已运行，避免重复启动


def start_workbuddy():
    if is_process_running():
        log("WorkBuddy 已在运行")
        return True
    log("WorkBuddy 未运行，正在启动...")
    try:
        subprocess.Popen([WORKBUDDY_EXE], cwd=os.path.dirname(WORKBUDDY_EXE))
        log("已发出启动命令，等待窗口出现...")
        return True
    except Exception as e:
        log(f"启动 WorkBuddy 失败: {e}")
        return False


def get_win():
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ProcessId and 'WorkBuddy' in (w.Name or ''):
                return w
        except Exception:
            continue
    return None


def find_ctrls(win, keywords, ctype=None, max_depth=16):
    """按名称关键词递归查找控件，返回列表"""
    found = []
    def walk(ctrl, depth=0):
        try:
            name = ctrl.Name or ''
            if (ctype is None or ctrl.ControlTypeName == ctype) and \
               any(k in name for k in keywords):
                found.append(ctrl)
            if depth >= max_depth:
                return
            for ch in ctrl.GetChildren():
                walk(ch, depth + 1)
        except Exception:
            pass
    walk(win)
    return found


def click_center(ctrl):
    r = ctrl.BoundingRectangle
    if not r or r.width() <= 0 or r.height() <= 0:
        return False
    real_click((r.left + r.right) / 2, (r.top + r.bottom) / 2)
    return True


def wait_for(win, keywords, ctype=None, timeout=10, interval=0.5):
    """轮询等待控件出现"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = find_ctrls(win, keywords, ctype)
        if res:
            return res[0]
        time.sleep(interval)
    return None


def open_avatar_menu(win):
    """打开左下角头像菜单，返回是否成功"""
    # 若签到入口已在（菜单已开），直接返回
    if find_ctrls(win, CHECKIN_ENTRY_KEYWORDS, 'ButtonControl'):
        return True

    wr = win.BoundingRectangle
    win_h = wr.height() if wr and wr.height() > 0 else 810
    win_w = wr.width() if wr and wr.width() > 0 else 400

    avatar = None
    # 关键词查找，带重试（accessibility 树初始化可能滞后）
    for attempt in range(4):
        for kw in USER_ITEM_KEYWORDS:
            res = find_ctrls(win, [kw], 'MenuItemControl')
            if res:
                avatar = res[0]
                break
        if avatar:
            break
        if attempt < 3:
            log(f"第 {attempt + 1} 次未找到头像，重试中...")
            try:
                win.SetActive()
            except Exception:
                pass
            time.sleep(2)

    # 备选：按窗口相对位置找左下角用户项
    if not avatar:
        cands = []
        def walk(ctrl, depth=0):
            try:
                if ctrl.ControlTypeName == 'MenuItemControl':
                    r = ctrl.BoundingRectangle
                    if r and r.top > wr.top + win_h * 0.75 and r.left < wr.left + win_w * 0.4:
                        cands.append(ctrl)
                if depth > 12:
                    return
                for ch in ctrl.GetChildren():
                    walk(ch, depth + 1)
            except Exception:
                pass
        walk(win)
        if cands:
            avatar = sorted(cands, key=lambda c: c.BoundingRectangle.top)[-1]
    if not avatar:
        log("未找到用户头像入口（可能未登录）")
        return False
    click_center(avatar)
    time.sleep(2)
    return bool(find_ctrls(win, CHECKIN_ENTRY_KEYWORDS, 'ButtonControl'))


def open_checkin_panel(win):
    """打开签到面板，返回是否成功"""
    btn = find_ctrls(win, CHECKIN_ENTRY_KEYWORDS, 'ButtonControl')
    if not btn:
        if not open_avatar_menu(win):
            return False
        btn = find_ctrls(win, CHECKIN_ENTRY_KEYWORDS, 'ButtonControl')
        if not btn:
            return False
    b = btn[0]
    # 优先 InvokePattern
    invoked = False
    try:
        pat = b.GetPattern(auto.PatternId.InvokePattern)
        pat.Invoke()
        invoked = True
    except Exception:
        invoked = False
    if not invoked:
        click_center(b)
    time.sleep(2)
    return True


def do_checkin(win):
    """在签到面板中执行/确认签到，返回 'claimed' | 'done' | 'failed'"""
    # 找签到状态按钮
    claimed = wait_for(win, CLAIMED_KEYWORDS, 'ButtonControl', timeout=8)
    if claimed:
        log("今日已签到（按钮状态: 今日已领）")
        return "claimed"
    # 未找到"已领"，找领取按钮
    claim_btn = wait_for(win, CLAIM_KEYWORDS, 'ButtonControl', timeout=8)
    if not claim_btn:
        log("未找到签到/领取按钮，签到面板可能未打开")
        return "failed"
    log(f"发现领取按钮: '{claim_btn.Name}'，点击领取...")
    try:
        pat = claim_btn.GetPattern(auto.PatternId.InvokePattern)
        pat.Invoke()
    except Exception:
        click_center(claim_btn)
    time.sleep(3)
    # 验证
    again = find_ctrls(win, CLAIMED_KEYWORDS, 'ButtonControl')
    if again:
        log("✅ 签到成功！积分已到账")
        return "done"
    log("点击领取后未确认到'已领'状态，可能领取失败或需要刷新")
    return "failed"


def main():
    log("========== WorkBuddy 每日签到开始 ==========")
    old_sr = None
    win = None
    try:
        # 1. 启动客户端
        if not start_workbuddy():
            log("启动失败，退出")
            return 1
        # 2. 等待窗口
        deadline = time.time() + 60
        while time.time() < deadline:
            win = get_win()
            if win:
                break
            time.sleep(2)
        if not win:
            log("等待 WorkBuddy 窗口超时")
            return 1
        # 3. 激活窗口并开启 accessibility
        try:
            win.SetActive()
        except Exception:
            pass
        time.sleep(1)
        old_sr = set_screen_reader(True)
        log("已激活 accessibility 树")
        time.sleep(3)
        # 4. 打开签到面板
        if not open_checkin_panel(win):
            log("无法打开签到面板（可能未登录/界面异常）")
            return 1
        # 5. 执行签到
        result = do_checkin(win)
        # 6. 关闭面板（ESC）
        try:
            win.SendKeys("{ESC}")
        except Exception:
            pass
        log(f"签到结果: {result}")
        log("========== 签到流程结束 ==========")
        return 0
    except Exception as e:
        log(f"脚本异常: {e}")
        try:
            import traceback
            log(traceback.format_exc())
        except Exception:
            pass
        return 1
    finally:
        # 恢复屏幕阅读器标志
        if old_sr is not None:
            try:
                set_screen_reader(old_sr)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
