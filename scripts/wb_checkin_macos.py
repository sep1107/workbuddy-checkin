#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 每日签到自动领取积分脚本 (macOS 版 v3.6)
================================================
v3 完全重写：抛弃辅助功能树方案（Electron 不暴露 DOM），
改用截屏分析 + CGEvent 鼠标模拟。
v3.1 改进（2026-08-17）：
  - 去除 System Events/AppleScript 硬依赖：
    * 窗口位置优先用 CGWindowListCopyWindowInfo（CoreGraphics，无权限依赖）
    * 进程检测改用 pgrep，应用激活改用 open -a
    * 解决 osascript 报"权限违例 -10004"导致签到失败的问题
  - "今日已领"检测增加按钮形状 + 深色卡片上下文过滤，
    防止把聊天页面的灰色元素误判为已签到（05:25 误判教训）
v3.2 改进（2026-08-17）：
  - 回退流程重构：点击头像后重新截屏并在窗口内用 find_dark_card
    重新检测签到卡片，而非直接在全屏范围找亮色按钮
  - find_bright_button 支持 win_rect 参数，限制搜索范围到 WorkBuddy 窗口内
    防止点击到其他应用窗口（06:03 误点击 (1092,624) 切换到 Chrome 的教训）
  - 主流程弹窗按钮搜索也限制到窗口范围内
v3.3 改进（2026-09-01）：
  - 头像坐标改右上角：v5.3.x 用户徽章移到 (0.91, 0.04)。
    旧左下角 (0.08, 0.95) 在新版 UI 下点中"新版本就绪/重启升级"横幅，
    从未真正点开头像；之前几天的"成功"也是巧合路径。
  - 移除"亮色按钮质心"盲点回退：所有检测器落空时点击全窗口亮像素质心
    等于随机点击（09-01 两次失败点了 (348,768)/(516,807)），可能误触发
    任意 UI。改为明确 abort + return False，等待 --debug 校准。
  - 配合 wb_checkin.sh wrapper 隔离解释器依赖，托管 Python 被重置也不影响。
v3.4 改进（2026-09-02）：
  - 【关键修复】主流程改为「先验证再弹窗」：v5.4.x UI 点击「立即领取」后
    **直接领取、无确认弹窗**，唯一可靠信号是按钮变灰成「今日已领」。
    旧逻辑只看窗口差异 > 1% 就去 find_bright_button 找"弹窗按钮"，
    而 ensure_running/activate_app 的 2%+ 全局差异会触发假阳性，
    导致在主界面点错位置（2026-09-02 07:13 报"完成"但实际未签到；
    07:17 实际签到成功却报"无变化"——日志完全失真）。
  - 点击「立即领取」后优先用 find_claimed_button 验证「今日已领」，
    命中即报成功并返回；只有确实未直接领取（可能弹出确认框）
    才走 find_bright_button 弹窗分支。
  - 弹窗分支点击后同样再次验证；未检测到「今日已领」则报 WARNING
    并 return False，不再盲信"签到流程完成"。
  - 回退流程（头像→菜单→深色按钮）同样套用「先验证再弹窗」。
v3.6 改进（2026-09-11）：
  - 【关键修复】find_claimed_button 误报「今日已领」——5.5.x 签到卡片里
    常驻一个白底灰描边的「认证领积分」按钮，其灰色边框被灰度连通域检测
    当成"实心灰按钮"，density 仅 ≈0.04 却因 aspect 达标而入选，导致
    脚本每次都误判"今日已领"直接返回，从不点击「立即领取」（09-11 复盘）。
  - 修复 1：find_claimed_button 增加**实心度门槛** density >= 0.5。
    真正的「今日已领」是实心灰填充按钮（density 高），描边按钮被排除。
  - 修复 2：run_checkin 调换判定权威——**先找深色「立即领取」按钮**。
    只要它存在就一定是未签到 → 直接点击；仅当它不存在时才认「今日已领」。
    避免"误报已领"掩盖"实际未领"。
  - 修复 3：点击后验证改为「'立即领取'按钮已消失」或「检出'今日已领'」，
    后者为强信号、前者为弱信号（均明确记日志），两者皆无才 WARNING + False。

技术方案:
  1. CGWindowListCopyWindowInfo 获取窗口位置（points 坐标系，无需自动化权限）
  2. screencapture 截屏（physical pixels）
  3. Pillow 图像分析：检测深色"立即领取"按钮 / 灰色"今日已领"按钮 / 旧版深色卡片
  4. CGEvent 模拟点击按钮
  5. 截屏对比验证：检测弹窗，如有则继续点击签到按钮

坐标体系:
  - CGWindowList 窗口位置: points（逻辑坐标）
  - screencapture 截图: pixels（物理像素，Retina 2x）
  - CGEvent 点击: points（逻辑坐标）
  - 转换: screen_point = win_origin + pixel_offset / scale_factor

依赖: Pillow（图像处理）
日志: ~/.workbuddy/scripts/checkin.log

用法:
  python3 wb_checkin.py            # 正式签到
  python3 wb_checkin.py --debug    # 调试模式（保存各阶段截图）
  python3 wb_checkin.py --dry-run  # 只检测不点击
"""

import subprocess
import time
import os
import sys
import ctypes
import logging
import argparse
from collections import deque
try:
    from PIL import Image, ImageDraw
except ImportError:
    print("错误: 需要 Pillow 库。安装: pip3 install Pillow")
    sys.exit(1)

# ==================== 配置 ====================

APP_NAME = "WorkBuddy"
# WorkBuddy 是 Electron 应用，System Events 进程名可能是 "WorkBuddy" 或 "Electron"
PROCESS_NAMES = ["WorkBuddy", "Electron"]
LOG_FILE = os.path.expanduser("~/.workbuddy/scripts/checkin.log")
DEBUG_DIR = "/tmp/wb_checkin_debug"

# Buddy 加油站卡片搜索区域（窗口宽高的比例: left, right, top, bottom）
CARD_SEARCH = (0.02, 0.35, 0.72, 0.95)

# 深色像素判定阈值（grayscale < 此值视为深色）
DARK_THRESHOLD = 35

# 卡片最小/最大尺寸限制（窗口宽高比例）
CARD_MIN_W, CARD_MAX_W = 0.08, 0.30
CARD_MIN_H, CARD_MAX_H = 0.06, 0.22

# 弹窗按钮搜索区域（全屏比例）
DIALOG_SEARCH = (0.20, 0.80, 0.30, 0.85)

# 截图差异判定（窗口区域内，1% 以上视为有变化）
DIFF_THRESHOLD = 0.01

# v3.5: WorkBuddy 5.5.x UI 全面重构 — 签到入口改为侧边栏→头像菜单。
#       侧边栏默认隐藏，需先点开关展开；头像 "阿七." 移至侧边栏左下角。
#       头像菜单中的 "Buddy加油站" 项会弹出底部签到卡片（含"立即领取"）。
#
# 侧边栏开关（窗口顶部偏左，紧邻 traffic lights 与 "+" 新建按钮）
SIDEBAR_TOGGLE_RATIO = (0.075, 0.034)
# 头像 "阿七."（侧边栏展开时的左下角圆形头像）
AVATAR_RATIO = (0.041, 0.950)
# "Buddy加油站" 菜单项（头像下拉菜单中第 3 行，"阿七." 标题之下）
#   ⚠ 必须用 cg_click_nomove（不预先移动光标）：cg_click 会先把光标移到
#   目标，光标离开头像锚点 → Electron 下拉菜单立即关闭，点击落到下方
#   侧边栏项（曾因此误触"定时任务"页）。
MENU_BUDDY_RATIO = (0.088, 0.339)

# ==================== CGEvent 鼠标模拟 ====================

_apis = ctypes.cdll.LoadLibrary(
    '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
)


class CGPoint(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]


_apis.CGEventCreateMouseEvent.restype = ctypes.c_void_p
_apis.CGEventCreateMouseEvent.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32
]
_apis.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_apis.CFRelease.argtypes = [ctypes.c_void_p]

KCG_MOUSE_DOWN = 1
KCG_MOUSE_UP = 2
KCG_MOUSE_MOVED = 5
KCG_EVENT_TAP = 0


def cg_click(x, y):
    """在屏幕 points 坐标 (x, y) 处模拟鼠标点击。"""
    pt = CGPoint(float(x), float(y))
    # 先移动鼠标到目标位置
    move = _apis.CGEventCreateMouseEvent(None, KCG_MOUSE_MOVED, pt, 0)
    _apis.CGEventPost(KCG_EVENT_TAP, move)
    _apis.CFRelease(move)
    time.sleep(0.15)
    # 按下
    down = _apis.CGEventCreateMouseEvent(None, KCG_MOUSE_DOWN, pt, 0)
    _apis.CGEventPost(KCG_EVENT_TAP, down)
    time.sleep(0.06)
    # 抬起
    up = _apis.CGEventCreateMouseEvent(None, KCG_MOUSE_UP, pt, 0)
    _apis.CGEventPost(KCG_EVENT_TAP, up)
    time.sleep(0.06)
    if down:
        _apis.CFRelease(down)
    if up:
        _apis.CFRelease(up)
    logging.info(f"CGEvent 点击: ({x:.0f}, {y:.0f})")


def cg_click_nomove(x, y):
    """在屏幕 points 坐标 (x, y) 处发送鼠标 down/up，但**不预先移动光标**。
    v3.5: 用于点击锚定型下拉菜单（如头像菜单）中的条目。
          cg_click 会先把光标移到目标；光标一旦离开锚点，Electron 下拉菜单
          会立即关闭，随后点击落到下层 UI（曾因此误触侧边栏"定时任务"）。
    """
    pt = CGPoint(float(x), float(y))
    down = _apis.CGEventCreateMouseEvent(None, KCG_MOUSE_DOWN, pt, 0)
    _apis.CGEventPost(KCG_EVENT_TAP, down)
    time.sleep(0.06)
    up = _apis.CGEventCreateMouseEvent(None, KCG_MOUSE_UP, pt, 0)
    _apis.CGEventPost(KCG_EVENT_TAP, up)
    time.sleep(0.1)
    if down:
        _apis.CFRelease(down)
    if up:
        _apis.CFRelease(up)
    logging.info(f"CGEvent no-move 点击: ({x:.0f}, {y:.0f})")


# ==================== AppleScript 辅助 ====================

def osa(script, timeout=15):
    """执行 AppleScript，返回 (stdout, stderr)。"""
    r = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True, text=True, timeout=timeout
    )
    return r.stdout.strip(), r.stderr.strip()


def ensure_running(name=APP_NAME):
    """确保应用正在运行，如未运行则启动。返回是否已经在运行。
    v3.1: 不再依赖 System Events，改用 pgrep 检测进程。"""
    try:
        r = subprocess.run(
            ['pgrep', '-x', name], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            return True
        # Electron 主进程可能叫 Electron（WorkBuddy.app 可执行文件名）
        r2 = subprocess.run(
            ['pgrep', '-f', f'{name}.app'], capture_output=True, text=True, timeout=5
        )
        if r2.returncode == 0:
            return True
    except Exception as e:
        logging.warning(f"pgrep 检测失败: {e}")
    logging.info(f"{name} 未运行，正在启动...")
    subprocess.run(['open', '-a', name], timeout=30)
    time.sleep(12)
    return False


def activate_app(name=APP_NAME):
    """激活应用窗口并置于最前。
    v3.1: 优先 open -a（无需自动化权限），失败再尝试 AppleScript。"""
    try:
        subprocess.run(['open', '-a', name], timeout=10)
        time.sleep(1.5)
        return
    except Exception as e:
        logging.warning(f"open -a 激活失败: {e}，回退 AppleScript")
    osa(f'tell application "{name}" to activate')
    time.sleep(1.5)
    for proc in PROCESS_NAMES:
        out, _ = osa(
            f'tell application "System Events" to '
            f'tell process "{proc}" to set frontmost to true'
        )
        if "error" not in out.lower():
            break
    time.sleep(0.5)


# ==================== CoreGraphics 窗口检测（无需自动化权限） ====================

def _load_cf():
    """加载 CoreFoundation 并配置常用函数签名。"""
    cf = ctypes.cdll.LoadLibrary(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
    )
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32
    ]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFNumberGetValue.restype = ctypes.c_int
    cf.CFNumberGetValue.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p
    ]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFStringGetCString.restype = ctypes.c_int
    cf.CFStringGetCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32
    ]
    return cf


def get_window_rect_cg(name=APP_NAME):
    """通过 CGWindowListCopyWindowInfo 获取 WorkBuddy 主窗口位置（points）。
    不依赖 System Events / 自动化权限，仅需普通 API 调用。
    注意: CFArray/CFDictionary 等辅助函数必须通过 CoreFoundation 句柄调用，
    通过 CoreGraphics 句柄调用会造成段错误。
    返回 (x, y, w, h) 或 None。"""
    try:
        cg = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
        )
        cf = _load_cf()
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]

        kCGWindowListOptionOnScreenOnly = 0x10
        info = cg.CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, 0)
        if not info:
            return None
        n = cf.CFArrayGetCount(ctypes.c_void_p(info))

        def cfstr(s):
            return cf.CFStringCreateWithCString(None, s.encode(), 0)

        def get_str(d, key):
            v = cf.CFDictionaryGetValue(ctypes.c_void_p(d), ctypes.c_void_p(key))
            if not v:
                return ''
            buf = ctypes.create_string_buffer(256)
            # kCFStringEncodingUTF8
            if cf.CFStringGetCString(v, buf, 256, 0x08000100):
                return buf.value.decode('utf-8', 'ignore')
            return ''

        def get_num(b, key):
            v = cf.CFDictionaryGetValue(ctypes.c_void_p(b), ctypes.c_void_p(key))
            if not v:
                return None
            f = ctypes.c_double()
            # kCFNumberFloat64Type = 6
            if not cf.CFNumberGetValue(v, 6, ctypes.byref(f)):
                return None
            return f.value

        k_owner = cfstr('kCGWindowOwnerName')
        k_bounds = cfstr('kCGWindowBounds')
        k_x = cfstr('X')
        k_y = cfstr('Y')
        k_w = cfstr('Width')
        k_h = cfstr('Height')

        best = None
        best_area = 0
        for i in range(n):
            d = cf.CFArrayGetValueAtIndex(ctypes.c_void_p(info), i)
            if not d:
                continue
            owner = get_str(d, k_owner)
            if owner != name:
                continue
            b = cf.CFDictionaryGetValue(ctypes.c_void_p(d), ctypes.c_void_p(k_bounds))
            if not b:
                continue
            x = get_num(b, k_x)
            y = get_num(b, k_y)
            w = get_num(b, k_w)
            h = get_num(b, k_h)
            if None in (x, y, w, h) or w < 200 or h < 200:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                best = (int(x), int(y), int(w), int(h))
        cf.CFRelease(ctypes.c_void_p(info))
        return best
    except Exception as e:
        logging.warning(f"CGWindowList 检测失败: {e}")
        return None


def get_window_rect(name=APP_NAME):
    """获取前台窗口位置和大小（points）。返回 (x, y, w, h) 或 None。
    v3.1: 优先 CoreGraphics（无权限依赖），失败回退 System Events。"""
    rect = get_window_rect_cg(name)
    if rect:
        logging.info(f"窗口位置(CGWindowList): ({rect[0]}, {rect[1]}) {rect[2]}x{rect[3]}")
        return rect

    for proc in PROCESS_NAMES:
        script = f'''
        tell application "System Events"
            tell process "{proc}"
                try
                    set p to position of front window
                    set s to size of front window
                    return (item 1 of p as string) & "," & (item 2 of p as string) & "," & (item 1 of s as string) & "," & (item 2 of s as string)
                on error errMsg
                    return "ERROR:" & errMsg
                end try
            end tell
        end tell
        '''
        out, _ = osa(script)
        if out and not out.startswith("ERROR"):
            parts = [p.strip() for p in out.split(",") if p.strip()]
            if len(parts) >= 4:
                rect = tuple(int(float(x)) for x in parts[:4])
                logging.info(f"窗口进程名: {proc}")
                return rect
    logging.warning("无法获取窗口位置（尝试了 WorkBuddy 和 Electron）")
    return None


def get_screen_points():
    """获取屏幕尺寸（points）。通过 CoreGraphics C API，不依赖 AppleScript 权限。"""
    try:
        CG = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
        )

        class CGSize(ctypes.Structure):
            _fields_ = [('width', ctypes.c_double), ('height', ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [('origin', CGPoint), ('size', CGSize)]

        CG.CGMainDisplayID.restype = ctypes.c_uint32
        CG.CGMainDisplayID.argtypes = []
        CG.CGDisplayBounds.restype = CGRect
        CG.CGDisplayBounds.argtypes = [ctypes.c_uint32]

        display_id = CG.CGMainDisplayID()
        bounds = CG.CGDisplayBounds(display_id)
        return int(bounds.size.width), int(bounds.size.height)
    except Exception as e:
        logging.warning(f"CoreGraphics 获取屏幕尺寸失败: {e}")
        return None


# ==================== 截屏与图像分析 ====================

def detect_scale():
    """检测 Retina 缩放因子: 截图像素宽 / 屏幕逻辑点宽。"""
    screen = get_screen_points()
    tmp = "/tmp/_wb_scale.png"
    subprocess.run(['screencapture', '-x', tmp], capture_output=True, timeout=10)
    img = Image.open(tmp)
    if screen:
        scr_w, scr_h = screen
        scale = img.width / scr_w
        logging.info(
            f"屏幕: {scr_w}x{scr_h} points, "
            f"截图: {img.width}x{img.height} px, "
            f"缩放: {scale}x"
        )
    else:
        # 无法获取屏幕逻辑尺寸，根据截图大小推断
        scale = 2.0 if img.width > 2500 else 1.0
        logging.info(
            f"截图: {img.width}x{img.height} px, "
            f"推断缩放: {scale}x"
        )
    os.remove(tmp)
    return scale


def screenshot(path="/tmp/wb_screen.png"):
    """全屏截屏（物理像素）。"""
    subprocess.run(['screencapture', '-x', path], check=True, timeout=10)
    return Image.open(path)


def crop_window(img, rect, scale):
    """从全屏截图中裁剪窗口区域。rect 是 points 坐标。"""
    x, y, w, h = rect
    box = (
        int(x * scale),
        int(y * scale),
        int((x + w) * scale),
        int((y + h) * scale),
    )
    return img.crop(box)


def find_dark_card(win_img, search=CARD_SEARCH, threshold=DARK_THRESHOLD):
    """
    在窗口截图中搜索深色"Buddy加油站"卡片。
    使用连通区域分析，避免把整片深色侧边栏误识别为卡片。
    返回卡片 bbox (left, top, right, bottom) 窗口截图像素坐标，或 None。
    """
    w, h = win_img.size
    left = int(w * search[0])
    right = int(w * search[1])
    top = int(h * search[2])
    bottom = int(h * search[3])

    region = win_img.crop((left, top, right, bottom)).convert('L')
    rw, rh = region.size
    px = region.load()

    # 构建二值掩码：True = 深色像素
    mask = [bytearray(rh) for _ in range(rw)]
    for y in range(rh):
        for x in range(rw):
            if px[x, y] < threshold:
                mask[x][y] = 1

    visited = [bytearray(rh) for _ in range(rw)]
    components = []

    for y in range(rh):
        for x in range(rw):
            if not mask[x][y] or visited[x][y]:
                continue

            # BFS 找连通区域（8-邻域）
            queue = deque([(x, y)])
            visited[x][y] = 1
            pixels = [(x, y)]
            min_x, max_x = x, x
            min_y, max_y = y, y

            while queue:
                cx, cy = queue.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < rw and 0 <= ny < rh:
                            if mask[nx][ny] and not visited[nx][ny]:
                                visited[nx][ny] = 1
                                queue.append((nx, ny))
                                pixels.append((nx, ny))
                                if nx < min_x: min_x = nx
                                if nx > max_x: max_x = nx
                                if ny < min_y: min_y = ny
                                if ny > max_y: max_y = ny

            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            # 过滤过小连通区域（噪声、图标）
            if bw < 30 or bh < 18 or len(pixels) < 80:
                continue

            area = len(pixels)
            bbox_area = bw * bh
            density = area / bbox_area if bbox_area > 0 else 0
            components.append({
                'bbox': (min_x, min_y, max_x, max_y),
                'area': area,
                'density': density,
            })

    if not components:
        return None

    # 按卡片几何特征打分：尺寸适中、位于搜索区域底部偏左、密度较高
    scored = []
    for comp in components:
        bbox = comp['bbox']
        bw = bbox[2] - bbox[0] + 1
        bh = bbox[3] - bbox[1] + 1
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2

        # 尺寸打分
        min_w_px = int(w * CARD_MIN_W)
        max_w_px = int(w * CARD_MAX_W)
        min_h_px = int(h * CARD_MIN_H)
        max_h_px = int(h * CARD_MAX_H)

        if min_w_px <= bw <= max_w_px and min_h_px <= bh <= max_h_px:
            size_score = 1.0
        elif (min_w_px * 0.5 <= bw <= max_w_px * 1.5 and
              min_h_px * 0.5 <= bh <= max_h_px * 1.5):
            size_score = 0.5
        else:
            size_score = 0.0

        # 位置打分：底部偏左
        bottom_ratio = cy / rh
        left_ratio = 1.0 - (cx / rw)
        pos_score = 0.35 * bottom_ratio + 0.65 * left_ratio

        # 密度打分
        density_score = comp['density']

        total = size_score * 0.45 + pos_score * 0.35 + density_score * 0.20
        scored.append((total, comp))

    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][1]
    bbox = best['bbox']
    return (
        left + bbox[0], top + bbox[1],
        left + bbox[2], top + bbox[3],
    )


def find_dark_button(win_img, search=CARD_SEARCH, threshold=55):
    """
    在窗口截图中搜索深色的"立即领取"按钮。
    新版卡片为浅色背景，按钮是深色独立连通区域，不会被 find_dark_card 覆盖。
    返回按钮 bbox (left, top, right, bottom) 窗口截图像素坐标，或 None。
    """
    w, h = win_img.size
    left = int(w * search[0])
    right = int(w * search[1])
    top = int(h * search[2])
    bottom = int(h * search[3])

    region = win_img.crop((left, top, right, bottom)).convert('L')
    rw, rh = region.size
    px = region.load()

    # 二值掩码：深色像素
    mask = [bytearray(rh) for _ in range(rw)]
    for y in range(rh):
        for x in range(rw):
            if px[x, y] < threshold:
                mask[x][y] = 1

    visited = [bytearray(rh) for _ in range(rw)]
    components = []

    for y in range(rh):
        for x in range(rw):
            if not mask[x][y] or visited[x][y]:
                continue

            queue = deque([(x, y)])
            visited[x][y] = 1
            pixels = [(x, y)]
            min_x, max_x = x, x
            min_y, max_y = y, y

            while queue:
                cx, cy = queue.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < rw and 0 <= ny < rh:
                            if mask[nx][ny] and not visited[nx][ny]:
                                visited[nx][ny] = 1
                                queue.append((nx, ny))
                                pixels.append((nx, ny))
                                if nx < min_x: min_x = nx
                                if nx > max_x: max_x = nx
                                if ny < min_y: min_y = ny
                                if ny > max_y: max_y = ny

            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            if bw < 50 or bh < 24 or len(pixels) < 150:
                continue

            # 按钮形状过滤
            aspect = bw / bh
            density = len(pixels) / (bw * bh)
            if not (1.5 <= aspect <= 12):
                continue
            if not (60 <= bw <= 360 and 24 <= bh <= 90):
                continue
            if density < 0.35:
                continue

            cx = (min_x + max_x) / 2
            cy = (min_y + max_y) / 2
            # 位置打分：左下优先
            pos_score = (cy / rh) * 0.5 + (1 - cx / rw) * 0.5
            shape_score = 1.0 if (2.0 <= aspect <= 8.0) else 0.7
            total = pos_score * 0.6 + shape_score * 0.4

            components.append({
                'bbox': (min_x, min_y, max_x, max_y),
                'score': total,
            })

    if not components:
        return None

    components.sort(key=lambda c: c['score'], reverse=True)
    best = components[0]
    if best['score'] < 0.3:
        return None

    bbox = best['bbox']
    return (
        left + bbox[0], top + bbox[1],
        left + bbox[2], top + bbox[3],
    )


def find_claimed_button(win_img, search=CARD_SEARCH, gray_range=(70, 240)):
    """
    检测是否已经签到：查找灰色的"今日已领"按钮。
    返回 bbox (left, top, right, bottom) 窗口截图像素坐标，或 None。
    v3.2: 新版卡片为浅色背景，按钮背景更浅（200-220 灰度），放宽范围到 (70, 240)。
    """
    w, h = win_img.size
    left = int(w * search[0])
    right = int(w * search[1])
    top = int(h * search[2])
    bottom = int(h * search[3])

    region = win_img.crop((left, top, right, bottom)).convert('L')
    rw, rh = region.size
    px = region.load()

    # 灰度像素掩码
    mask = [bytearray(rh) for _ in range(rw)]
    for y in range(rh):
        for x in range(rw):
            val = px[x, y]
            if gray_range[0] <= val <= gray_range[1]:
                mask[x][y] = 1

    visited = [bytearray(rh) for _ in range(rw)]
    components = []

    for y in range(rh):
        for x in range(rw):
            if not mask[x][y] or visited[x][y]:
                continue

            queue = deque([(x, y)])
            visited[x][y] = 1
            pixels = [(x, y)]
            min_x, max_x = x, x
            min_y, max_y = y, y

            while queue:
                cx, cy = queue.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < rw and 0 <= ny < rh:
                            if mask[nx][ny] and not visited[nx][ny]:
                                visited[nx][ny] = 1
                                queue.append((nx, ny))
                                pixels.append((nx, ny))
                                if nx < min_x: min_x = nx
                                if nx > max_x: max_x = nx
                                if ny < min_y: min_y = ny
                                if ny > max_y: max_y = ny

            bw = max_x - min_x + 1
            bh = max_y - min_y + 1
            if bw < 40 or bh < 18 or len(pixels) < 200:
                continue

            # v3.1/v3.2: 按钮形状过滤，防止把无关灰色块（输入框/其他UI）误判为"今日已领"
            density = len(pixels) / (bw * bh)
            aspect = bw / bh
            if not (80 <= bw <= 420 and 28 <= bh <= 90):
                continue
            if not (1.5 <= aspect <= 10 or density > 0.8):
                continue

            # v3.6: 实心度门槛 — 「今日已领」是**实心灰填充**按钮（density 高），
            #   而卡片里常驻的白底灰描边「认证领积分」按钮 density 仅 ≈0.04，
            #   必须排除，否则每次都会被误判为"已签到"而跳过点击（09-11 教训）。
            if density < 0.5:
                continue

            # v3.2: 不再强制要求上方深色背景（新版卡片为浅色），改为综合打分
            dark_above = 0
            band_top = max(0, min_y - 50)
            for dy in range(band_top, min_y):
                for dx in range(max(0, min_x - 20), min(rw, max_x + 20), 2):
                    if px[dx, dy] < 80:
                        dark_above += 1

            cx = (min_x + max_x) / 2
            cy = (min_y + max_y) / 2

            # 位置打分：越靠近左下角越好（卡片按钮都在左下）
            pos_score = (cy / rh) * 0.5 + (1 - cx / rw) * 0.5
            # 上下文打分：上方有深色背景加分（旧版深色卡片）
            context_score = min(dark_above / 30.0, 1.0)
            # 形状打分：长宽比接近按钮形状更好
            shape_score = 1.0 if 1.5 <= aspect <= 10 else 0.6

            # 综合：位置权重最高，其次是上下文，形状保底
            total = pos_score * 0.55 + context_score * 0.25 + shape_score * 0.20

            # 只保留位于搜索区域下半部分的候选（排除聊天区上方的灰色块）
            if cy < rh * 0.55:
                continue

            components.append({
                'bbox': (min_x, min_y, max_x, max_y),
                'score': total,
            })

    if not components:
        return None

    components.sort(key=lambda c: c['score'], reverse=True)
    best = components[0]
    if best['score'] < 0.35:
        return None

    bbox = best['bbox']
    return (
        left + bbox[0], top + bbox[1],
        left + bbox[2], top + bbox[3],
    )


def find_bright_button(img, search=DIALOG_SEARCH, win_rect=None, scale=2.0):
    """
    在截图中搜索亮色按钮区域（弹窗中的签到按钮）。
    返回按钮中心 (x, y) 全屏截图像素坐标，或 None。
    v3.2: 如果提供 win_rect (x, y, w, h in points)，则限制搜索范围到窗口区域内，
    防止把其他应用的亮色 UI 误判为签到按钮。
    """
    if win_rect:
        wx, wy, ww, wh = win_rect
        left = int(wx * scale)
        right = int((wx + ww) * scale)
        top = int(wy * scale)
        bottom = int((wy + wh) * scale)
    else:
        w, h = img.size
        left = int(w * search[0])
        right = int(w * search[1])
        top = int(h * search[2])
        bottom = int(h * search[3])

    region = img.crop((left, top, right, bottom)).convert('RGB')
    px = region.load()

    button_pixels = []
    for y in range(0, region.height, 3):
        for x in range(0, region.width, 3):
            r, g, b = px[x, y]
            # 亮色按钮: 至少一个通道 > 140，总亮度 > 320，排除纯白背景
            if (r > 140 or g > 140 or b > 140) and (r + g + b) > 320:
                if not (r > 230 and g > 230 and b > 230):
                    button_pixels.append((x, y))

    if len(button_pixels) < 30:
        return None

    cx = sum(p[0] for p in button_pixels) // len(button_pixels)
    cy = sum(p[1] for p in button_pixels) // len(button_pixels)
    return (left + cx, top + cy)


def images_different(img1, img2, threshold=DIFF_THRESHOLD):
    """比较两张截图是否有显著差异（采样比较）。"""
    if img1.size != img2.size:
        return True
    w, h = img1.size
    g1 = img1.convert('L')
    g2 = img2.convert('L')
    diff_count = 0
    sample_count = 0
    for y in range(0, h, 12):
        for x in range(0, w, 12):
            sample_count += 1
            if abs(g1.getpixel((x, y)) - g2.getpixel((x, y))) > 30:
                diff_count += 1
    ratio = diff_count / sample_count if sample_count > 0 else 0
    logging.info(f"截图差异: {ratio:.2%} (阈值 {threshold:.0%})")
    return ratio > threshold


# ==================== 主流程 ====================

def setup_logging(debug=False):
    fmt = '%(asctime)s [%(levelname)s] %(message)s'
    handlers = [
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=fmt,
        handlers=handlers,
        force=True,
    )
    # 抑制 Pillow 大量 DEBUG 输出
    logging.getLogger('PIL').setLevel(logging.INFO)


def save_debug(img, name, debug, overlay=None):
    """调试模式下保存截图。可选在图片上绘制 overlay 信息。"""
    if not debug:
        return
    path = os.path.join(DEBUG_DIR, name)
    if overlay:
        draw = ImageDraw.Draw(img)
        for item in overlay:
            kind = item['kind']
            if kind == 'rect':
                draw.rectangle(item['bbox'], outline=item.get('color', 'red'), width=3)
            elif kind == 'point':
                x, y = item['pos']
                r = item.get('radius', 5)
                draw.ellipse([x-r, y-r, x+r, y+r], fill=item.get('color', 'red'))
            elif kind == 'text':
                draw.text(item['pos'], item['text'], fill=item.get('color', 'red'))
    img.save(path)
    logging.debug(f"调试截图: {path}")


def is_sidebar_open(win_img):
    """检测 WorkBuddy 5.5.x 侧边栏是否展开：通过采样侧边栏左下角头像
    "阿七." 的绿色外环颜色。展开时该区域有显著绿色像素，关闭时为
    聊天区背景（白/灰，无绿色）。
    头像中心在窗口 (56, 720) points；头像半径约 35 px（@2x），所以需
    采样较大区域才能覆盖绿色外环（中心是白色猪头图标，会误判为背景）。
    """
    w, h = win_img.size
    scale_x = w / 1360.0 if w else 2.0
    scale_y = h / 758.0 if h else 2.0
    cx_pt, cy_pt = 56, 720
    cx = int(cx_pt * scale_x)
    cy = int(cy_pt * scale_y)
    if cx >= w or cy >= h:
        return False
    # 半径需覆盖头像外环（约头像半径 ~35px 的 1.1 倍）
    r = int(40 * max(scale_x, scale_y))
    x0, y0 = max(0, cx - r), max(0, cy - r)
    x1, y1 = min(w, cx + r), min(h, cy + r)
    region = win_img.crop((x0, y0, x1, y1)).convert('RGB')
    green_hits = 0
    total = 0
    for r_c, g_c, b_c in region.getdata():
        total += 1
        # 头像绿环颜色约 RGB(90-160, 180-220, 130-180)
        if g_c > r_c + 20 and g_c > b_c + 10 and g_c > 100 and r_c < 180:
            green_hits += 1
    # 绿色外环仅占小区域约 8-20%；阈值 8% 留余量
    return total > 0 and green_hits / total > 0.08


def run_checkin(debug=False, dry_run=False):
    """执行签到流程。

    v3.6: WorkBuddy 5.5.x UI — 签到入口移至侧边栏头像菜单。
          流程: 展开侧边栏 → 点击头像 → no-move 点击 "Buddy加油站"
              → 弹出底部签到卡片 → 点击"立即领取" → 验证"今日已领"。
          判定权威：深色「立即领取」按钮存在 ⇔ 未签到（优先点击）。

    返回语义（v3.4 起）：
      True  = 已**验证**签到成功（检测到「今日已领」）或今日已签到，
              或处于 dry-run 模式。
      False = 未能在界面上验证到签到结果（此前版本会盲报 True）。
    """
    logging.info("=" * 50)
    logging.info("WorkBuddy 每日签到开始 (v3.6 — 5.5.x 侧边栏导航)")

    if debug:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        logging.info("调试模式: 截图保存到 " + DEBUG_DIR)

    # 1. 确保运行并激活
    ensure_running()
    activate_app()

    # 2. 获取窗口位置（points）
    rect = get_window_rect()
    if not rect:
        logging.error("无法获取 WorkBuddy 窗口，可能未登录或窗口未显示")
        return False
    win_x, win_y, win_w, win_h = rect
    logging.info(f"窗口: ({win_x}, {win_y}) {win_w}x{win_h} points")

    # 3. 检测缩放因子
    scale = detect_scale()

    # 4. 截屏（点击前）
    before_img = screenshot()
    save_debug(before_img, "01_before_full.png", debug)

    # 5. 裁剪窗口区域
    win_img = crop_window(before_img, rect, scale)
    save_debug(win_img, "02_window.png", debug)

    # 6. v3.5: 确保侧边栏展开（5.5.x 头像与 Buddy加油站 都在侧边栏内）。
    #    WorkBuddy 启动后侧边栏默认隐藏，需先点开关展开才能看到头像。
    if not is_sidebar_open(win_img):
        sx = win_x + win_w * SIDEBAR_TOGGLE_RATIO[0]
        sy = win_y + win_h * SIDEBAR_TOGGLE_RATIO[1]
        logging.info(f"侧边栏未展开，点击开关 ({sx:.0f}, {sy:.0f})")
        if not dry_run:
            cg_click(sx, sy)
            time.sleep(2.5)
        before_img = screenshot()
        save_debug(before_img, "02a_after_sidebar.png", debug)
        win_img = crop_window(before_img, rect, scale)
        save_debug(win_img, "02a_after_sidebar_window.png", debug)

    # 7. v3.5: 导航到 Buddy加油站 卡片
    #    头像点击用 cg_click（光标移到头像→下拉菜单打开）；
    #    "Buddy加油站" 必须用 cg_click_nomove：否则光标离开头像锚点会导致
    #    Electron 下拉菜单立即关闭，点击落到下方侧边栏项（曾误触"定时任务"页）。
    avatar_x = win_x + win_w * AVATAR_RATIO[0]
    avatar_y = win_y + win_h * AVATAR_RATIO[1]
    logging.info(f"点击头像 ({avatar_x:.0f}, {avatar_y:.0f})")
    if not dry_run:
        cg_click(avatar_x, avatar_y)
        time.sleep(2.0)

    menu_x = win_x + win_w * MENU_BUDDY_RATIO[0]
    menu_y = win_y + win_h * MENU_BUDDY_RATIO[1]
    logging.info(f"no-move 点击 Buddy加油站 ({menu_x:.0f}, {menu_y:.0f})")
    if not dry_run:
        cg_click_nomove(menu_x, menu_y)
        time.sleep(3.0)

    # 8. 签到卡片已作为浮层显示在窗口左下，截屏检测
    after_nav = screenshot()
    save_debug(after_nav, "03_after_nav.png", debug)
    after_win = crop_window(after_nav, rect, scale)
    save_debug(after_win, "03_after_nav_window.png", debug)

    # 9. v3.6 判定权威：**先找深色「立即领取」按钮**。
    #    只要它存在 → 一定是未签到（按钮尚未变灰）→ 必须点击。
    #    绝不能被 find_claimed_button 的误报掩盖（09-11 教训：卡片常驻的
    #    白底描边「认证领积分」按钮曾被误判成"今日已领"，导致从不点击）。
    dark_btn = find_dark_button(after_win)

    # 仅当深色按钮不存在时，才考虑"今日已领"状态
    if dark_btn is None:
        claimed = find_claimed_button(after_win)
        if claimed:
            logging.info("已是'今日已领'状态，今日已签到，无需操作")
            if debug:
                save_debug(after_win.copy(), "03_claimed.png", debug,
                           overlay=[{'kind': 'rect', 'bbox': claimed, 'color': 'green'}])
            return True

    # 10. 存在"立即领取"深色按钮 → 点击领取
    if dark_btn:
        l, t, r, b = dark_btn
        w_btn = r - l
        h_btn = b - t
        cx = win_x + (l + w_btn * 0.5) / scale
        cy = win_y + (t + h_btn * 0.5) / scale
        logging.info(f"找到深色'立即领取'按钮 ({cx:.0f}, {cy:.0f})")
        if debug:
            save_debug(after_win.copy(), "03_dark_btn.png", debug,
                       overlay=[{'kind': 'rect', 'bbox': dark_btn, 'color': 'red'}])
        if dry_run:
            logging.info("Dry run 模式，不执行点击")
            return True
        cg_click(cx, cy)
        time.sleep(2.5)
        final = screenshot()
        save_debug(final, "04_final.png", debug)
        final_win = crop_window(final, rect, scale)
        # v3.6 验证：强信号 = 检出"今日已领"；弱信号 = 深色"立即领取"按钮已消失
        claimed_after = find_claimed_button(final_win)
        dark_after = find_dark_button(final_win)
        if claimed_after:
            logging.info("验证: 检测到'今日已领'，签到成功")
            return True
        if dark_after is None:
            logging.info("验证: '立即领取'按钮已消失（弱信号），判定签到成功")
            return True
        logging.warning("点击'立即领取'后按钮仍在且未检出'今日已领'，签到结果存疑")
        return False

    # 11. 回退: 旧版深色卡片
    card = find_dark_card(after_win)
    if card:
        l, t, r, b = card
        w_card = r - l
        h_card = b - t
        cx = win_x + (l + w_card * 0.5) / scale
        cy = win_y + (t + h_card * 0.5) / scale
        logging.info(f"找到深色卡片中心 ({cx:.0f}, {cy:.0f})")
        if dry_run:
            return True
        cg_click(cx, cy)
        time.sleep(2.5)
        final = screenshot()
        save_debug(final, "04_final.png", debug)
        final_win = crop_window(final, rect, scale)
        if find_claimed_button(final_win):
            logging.info("验证: 检测到'今日已领'，签到成功")
            return True
        logging.warning("点击卡片后未检出'今日已领'，签到结果存疑")
        return False

    # 12. 全部检测器落空 — 安全中止（不随机盲点）
    logging.error("导航后仍未找到签到入口；安全中止")
    logging.error("请用 --debug 排查: bash /Users/sep/.workbuddy/scripts/wb_checkin.sh --debug")
    logging.error("调试截图保存到: /tmp/wb_checkin_debug/")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="WorkBuddy macOS 签到脚本 (v3.6 侧边栏导航)"
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='调试模式: 保存各阶段截图到 /tmp/wb_checkin_debug/'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='只检测按钮位置，不执行点击'
    )
    args = parser.parse_args()

    setup_logging(args.debug)

    try:
        success = run_checkin(debug=args.debug, dry_run=args.dry_run)
        if success:
            logging.info("签到流程结束")
        else:
            logging.error("签到失败")
            sys.exit(1)
    except Exception as e:
        logging.error(f"异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
