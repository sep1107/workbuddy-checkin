"""Windows UI Automation + 输入注入底层模块（跨机器通用）。

不依赖 pywinauto；直接用 comtypes 调用 UIAutomationCore。

关键前提（踩坑点，勿删）:
1. 必须调用 SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)。
   否则 GetWindowRect / UIA BoundingRectangle 会返回被系统缩放后的
   虚拟坐标，而截图是物理像素，二者错位导致点击落空。
2. Electron(Chromium) 默认不暴露无障碍树。UIA 客户端连上后 Chromium 会
   异步开启 a11y（约 0.3~1s），因此首次 dump 可能只拿到窗口壳 —— 需重试。
3. 键盘 SendInput 在部分环境被安全软件拦截（返回 0），需 keybd_event 兜底；
   鼠标 SendInput 通常正常。
4. comtypes 的 IUIAutomationElement 属性是方法而非 property，且
   CurrentFirstChild 在部分生成版本不存在 —— 统一用 RawViewWalker 遍历。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import comtypes
import comtypes.client

# ---------------- DPI ----------------

DPI_SET = False


def set_dpi_aware():
    global DPI_SET
    if DPI_SET:
        return True
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            DPI_SET = True
            return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        DPI_SET = True
    except Exception:
        pass
    return DPI_SET


set_dpi_aware()

# ---------------- UIA ----------------

_auto = None


def get_auto():
    """获取 IUIAutomation 单例（自动 CoInitialize）。"""
    global _auto
    if _auto is None:
        try:
            comtypes.CoInitialize()
        except OSError:
            pass  # 已初始化
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        _auto = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    return _auto


def _uia():
    from comtypes.gen import UIAutomationClient as UIA

    return UIA


def control_type_map():
    UIA = _uia()
    return {getattr(UIA, n): n[4:] for n in dir(UIA)
            if n.startswith("UIA_") and isinstance(getattr(UIA, n), int)}


CT = {}          # id -> name
CT_BY_NAME = {}  # name -> id


def _init_ct():
    global CT, CT_BY_NAME
    if not CT:
        CT = control_type_map()
        CT_BY_NAME = {v: k for k, v in CT.items()}


def ctype_name(ct):
    _init_ct()
    return CT.get(ct, f"t{ct}")


def ctype_id(name):
    _init_ct()
    return CT_BY_NAME[name]


def walk_all(el, limit=20000):
    """迭代式遍历元素子树（避免深递归爆栈）。"""
    walker = get_auto().RawViewWalker
    out = []
    stack = [el]
    while stack and len(out) < limit:
        cur = stack.pop()
        out.append(cur)
        try:
            c = walker.GetFirstChildElement(cur)
        except Exception:
            c = None
        sibs = []
        while c:
            sibs.append(c)
            try:
                nxt = walker.GetNextSiblingElement(c)
            except Exception:
                nxt = None
            if nxt is None:
                break
            c = nxt
        stack.extend(reversed(sibs))
    return out


def rect_of(el):
    r = el.CurrentBoundingRectangle
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def describe(el):
    try:
        x, y, w, h = rect_of(el)
        return (f"{ctype_name(el.CurrentControlType):<20} name={el.CurrentName!r:<44} "
                f"aid={el.CurrentAutomationId!r:<24} ({x},{y},{w}x{h})")
    except Exception as e:
        return f"<stale {e}>"


def find(root, text=None, ctype=None, exact=False, regex=None, automation_id=None):
    """在子树中查找元素。text: 子串(默认)/全等(exact=True)；regex: 正则。

    automation_id 是最稳的定位依据（不受 i18n 文案变化影响），优先使用。
    """
    import re as _re

    res = []
    pat = _re.compile(regex) if regex else None
    for e in walk_all(root):
        try:
            name = e.CurrentName or ""
            ct = e.CurrentControlType
            aid = e.CurrentAutomationId or ""
        except Exception:
            continue
        if ctype is not None and ct != ctype:
            continue
        if automation_id is not None and aid != automation_id:
            continue
        if pat is not None:
            ok = pat.search(name) is not None
        elif text is None:
            ok = True
        elif exact:
            ok = name == text
        else:
            ok = text in name
        if ok:
            res.append(e)
    return res


def visible_find(root, **kw):
    """只返回矩形有效的元素（过滤掉 0x0 的虚拟/离屏节点）。"""
    return [e for e in find(root, **kw) if _valid(e)]


def _valid(el):
    try:
        _, _, w, h = rect_of(el)
        return w > 4 and h > 4
    except Exception:
        return False


def center(el):
    x, y, w, h = rect_of(el)
    return x + w // 2, y + h // 2


def invoke(el):
    """尝试 UIA 模式激活，失败则物理点击元素中心。返回使用的方式。"""
    UIA = _uia()
    for pid, caller in (
        ("UIA_InvokePatternId", lambda p: p.Invoke()),
        ("UIA_TogglePatternId", lambda p: p.Toggle()),
        ("UIA_LegacyIAccessiblePatternId", lambda p: p.DoDefaultAction()),
    ):
        try:
            pat = el.GetCurrentPattern(getattr(UIA, pid))
            if pat:
                caller(pat)
                return pid
        except Exception:
            continue
    click_at(*center(el))
    return "PhysicalClick"


# ---------------- 鼠标 / 键盘 ----------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _UNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF = dict(MOVE=0x0001, LEFTDOWN=0x0002, LEFTUP=0x0004, ABSOLUTE=0x8000)
KEYEVENTF_KEYUP = 0x0002
VK_ESCAPE = 0x1B


def _send(arr):
    u = ctypes.windll.user32
    u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    u.SendInput.restype = ctypes.c_uint
    return u.SendInput(len(arr), arr, ctypes.sizeof(INPUT))


def _mk_mouse(flags, dx, dy):
    i = INPUT()
    i.type = INPUT_MOUSE
    i.union.mi.dx = dx
    i.union.mi.dy = dy
    i.union.mi.dwFlags = flags
    return i


def click_at(x, y):
    """物理像素坐标点击（绝对定位）。"""
    u = ctypes.windll.user32
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    ax = int(x * 65535 / (sw - 1))
    ay = int(y * 65535 / (sh - 1))
    arr = (INPUT * 3)(
        _mk_mouse(MOUSEEVENTF["MOVE"] | MOUSEEVENTF["ABSOLUTE"], ax, ay),
        _mk_mouse(MOUSEEVENTF["LEFTDOWN"] | MOUSEEVENTF["ABSOLUTE"], ax, ay),
        _mk_mouse(MOUSEEVENTF["LEFTUP"] | MOUSEEVENTF["ABSOLUTE"], ax, ay),
    )
    if _send(arr) != 3:
        raise OSError(f"mouse SendInput failed, err={ctypes.GetLastError()}")
    return True


def press_key(vk=VK_ESCAPE):
    """发送按键；SendInput 被拦截时回退 keybd_event。返回实际使用的方式。"""
    a = INPUT()
    a.type = INPUT_KEYBOARD
    a.union.ki.wVk = vk
    b = INPUT()
    b.type = INPUT_KEYBOARD
    b.union.ki.wVk = vk
    b.union.ki.dwFlags = KEYEVENTF_KEYUP
    if _send((INPUT * 2)(a, b)) == 2:
        return "SendInput"
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return "keybd_event"


def dismiss_popup(rect):
    """点击窗口中部空白处收起弹窗（rect = 窗口物理矩形）。"""
    l, t, r, b = rect
    click_at((l + r) // 2, t + int((b - t) * 0.35))


# ---------------- 进程 / 窗口 ----------------


def pids_of(exe_name):
    """按可执行文件名（如 workbuddy.exe）取全部进程 PID。"""
    import subprocess

    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, encoding="gbk", errors="replace").stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == exe_name.lower():
            try:
                pids.add(int(parts[1]))
            except (IndexError, ValueError):
                pass
    return pids


def visible_windows(pids, min_w=50, min_h=50):
    """枚举指定进程的可见顶层窗口。"""
    u = ctypes.windll.user32
    wins = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(n + 1, 2))
        u.GetWindowTextW(hwnd, buf, n + 1)
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left >= min_w and r.bottom - r.top >= min_h:
            wins.append({"hwnd": hwnd, "pid": pid.value, "title": buf.value,
                         "rect": (r.left, r.top, r.right, r.bottom)})
        return True

    u.EnumWindows(CB(cb), 0)
    return wins


def restore_iconic(pids, delay=1.0):
    """恢复目标进程被最小化的可见窗口，返回恢复数。

    最小化窗口的矩形会被压到屏幕外（约 -21333,-21333 @150% DPI），高度只剩
    标题栏 26px，会被 visible_windows 的 min_w/min_h 过滤掉，导致
    main_window 误报 no_window（进程明明在跑）。无人值守场景下这是最常见的
    no_window 诱因，应先恢复再枚举。

    注意：ShowWindow 在 WorkBuddy agent 沙箱里可能被拦截（返回 0），
    首次失败时重试一次，仍失败则用 PostMessageW(SC_RESTORE) 兜底。
    """
    u = ctypes.windll.user32
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    iconic = []

    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and u.IsIconic(hwnd):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                iconic.append(hwnd)
        return True

    u.EnumWindows(CB(cb), 0)
    for hwnd in iconic:
        if not u.ShowWindow(hwnd, 9):  # SW_RESTORE；返回 0 视为被拦截
            time.sleep(delay)
            u.ShowWindow(hwnd, 9)
        if u.IsIconic(hwnd):
            u.PostMessageW(hwnd, 0x0112, 0xF120, 0)  # WM_SYSCOMMAND, SC_RESTORE
    if iconic:
        time.sleep(delay)
    return len(iconic)


def main_window(exe_name, title_hint=None):
    """取目标进程面积最大的可见窗口（即主窗口）。

    若找不到（最常见于主窗口被最小化，矩形被尺寸过滤），先尝试恢复
    最小化窗口再重新枚举一次。
    """
    pids = pids_of(exe_name)
    wins = visible_windows(pids)
    if title_hint:
        wins = [w for w in wins if title_hint.lower() in (w["title"] or "").lower()]
    if not wins and restore_iconic(pids):
        wins = visible_windows(pids)
        if title_hint:
            wins = [w for w in wins if title_hint.lower() in (w["title"] or "").lower()]
    if not wins:
        return None
    return max(wins, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))


def element_of(hwnd):
    return get_auto().ElementFromHandle(hwnd)


def wait_until(predicate, timeout=15.0, step=0.8):
    """轮询直到 predicate() 为真。返回 (是否成功, 最后一次值)。"""
    deadline = time.time() + timeout
    last = None
    while True:
        try:
            last = predicate()
        except Exception:
            last = None
        if last:
            return True, last
        if time.time() > deadline:
            return False, last
        time.sleep(step)


def count_children(el, cap=200):
    """统计子树节点数（用于判断 a11y 是否已就绪）。"""
    return max(0, len(walk_all(el, limit=cap + 1)) - 1)


def wait_children(el, min_count=80, timeout=15.0, step=0.8):
    """等待 Chromium 异步开启无障碍树。

    注意：探测时必须给出足够大的 cap，否则节点数被人为截断，
    会在树上只有窗口壳时就误判为「已就绪」。
    """
    cap = max(min_count * 2, 200)
    ok, n = wait_until(lambda: (count_children(el, cap) >= min_count) or None,
                       timeout=timeout, step=step)
    return count_children(el, cap) if ok or n is None else n
