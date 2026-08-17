#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 每日签到自动领取积分脚本 (macOS 版 v3.2)
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

# v3.2: 点击头像后弹出下拉菜单，需要点击"Buddy 加油站"条目
# 才能打开签到卡片。菜单从头像向上展开，"Buddy 加油站"是第3个条目
# 相对窗口位置 (x_ratio, y_ratio)
# 2026-08-17 实测: 文本中心在窗口 x≈110, y≈278 points
MENU_BUDDY_RATIO = (0.11, 0.37)

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


def run_checkin(debug=False, dry_run=False):
    """执行签到流程。返回 True 表示流程完成。"""
    logging.info("=" * 50)
    logging.info("WorkBuddy 每日签到开始 (v3.2 截屏分析模式)")

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

    # 6. 查找"立即领取"深色按钮（新版浅色卡片）
    dark_btn = find_dark_button(win_img)
    if dark_btn:
        db_l, db_t, db_r, db_b = dark_btn
        db_w = db_r - db_l
        db_h = db_b - db_t
        click_x = win_x + (db_l + db_w * 0.5) / scale
        click_y = win_y + (db_t + db_h * 0.5) / scale
        logging.info(f"找到深色'立即领取'按钮 ({click_x:.0f}, {click_y:.0f})")
        if debug:
            overlay = [
                {'kind': 'rect', 'bbox': (db_l, db_t, db_r, db_b), 'color': 'red'},
            ]
            save_debug(win_img.copy(), "02_window_dark_btn.png", debug, overlay=overlay)
        if not dry_run:
            cg_click(click_x, click_y)
            time.sleep(2.5)
            after_img = screenshot()
            save_debug(after_img, "03_after_click.png", debug)
            after_win = crop_window(after_img, rect, scale)
            if images_different(win_img, after_win, threshold=DIFF_THRESHOLD):
                logging.info("检测到窗口变化，查找弹窗按钮...")
                btn = find_bright_button(after_img, win_rect=rect, scale=scale)
                if btn:
                    bx = btn[0] / scale
                    by = btn[1] / scale
                    logging.info(f"找到弹窗按钮，点击 ({bx:.0f}, {by:.0f})")
                    cg_click(bx, by)
                    time.sleep(2)
                    final = screenshot()
                    save_debug(final, "04_final.png", debug)
                    logging.info("签到流程完成")
                else:
                    logging.info("未找到弹窗按钮，检查是否已直接签到...")
                    final_win = crop_window(after_img, rect, scale)
                    if find_claimed_button(final_win):
                        logging.info("验证: 检测到'今日已领'状态，签到成功")
                    else:
                        logging.info("未找到弹窗按钮，可能已直接签到")
            else:
                logging.info("点击后窗口无变化，可能未命中按钮")
        else:
            logging.info("Dry run 模式，不执行点击")
        return True

    # 7. 查找"今日已领"灰色按钮
    claimed = find_claimed_button(win_img)
    if claimed:
        logging.info("检测到'今日已领'状态，今日已签到，无需操作")
        if debug:
            overlay = [
                {'kind': 'rect', 'bbox': claimed, 'color': 'green'},
            ]
            save_debug(win_img.copy(), "02_window_claimed.png", debug, overlay=overlay)
        return True

    # 8. 查找旧版深色"Buddy加油站"卡片
    card = find_dark_card(win_img)
    if card:
        card_l, card_t, card_r, card_b = card
        card_w = card_r - card_l
        card_h = card_b - card_t
        logging.info(
            f"找到旧版深色卡片: ({card_l},{card_t})-({card_r},{card_b}) px, "
            f"大小 {card_w}x{card_h} px"
        )
        if debug:
            overlay = [
                {'kind': 'rect', 'bbox': (card_l, card_t, card_r, card_b), 'color': 'red'},
            ]
            save_debug(win_img.copy(), "02_window_detected.png", debug, overlay=overlay)

        if dry_run:
            logging.info("Dry run 模式，不执行点击")
            return True

        # 依次尝试点击目标区域内的不同位置（中心 + 左右偏移）
        click_offsets = [0.50, 0.35, 0.65]
        for i, ox in enumerate(click_offsets):
            btn_px = card_l + int(card_w * ox)
            btn_py = card_t + int(card_h * 0.5)
            click_x = win_x + btn_px / scale
            click_y = win_y + btn_py / scale

            logging.info(
                f"尝试 {i+1}/{len(click_offsets)}: "
                f"目标内 x={ox:.0%} → 屏幕 ({click_x:.0f}, {click_y:.0f})"
            )

            cg_click(click_x, click_y)
            time.sleep(2.5)

            # 截屏验证（只比较 WorkBuddy 窗口区域，避免全屏其他 UI 干扰）
            after_img = screenshot()
            save_debug(after_img, f"03_after_click_{i+1}.png", debug)
            after_win = crop_window(after_img, rect, scale)

            if images_different(win_img, after_win, threshold=DIFF_THRESHOLD):
                logging.info("检测到 WorkBuddy 窗口变化，查找弹窗按钮...")

                # 查找弹窗中的亮色按钮（v3.2: 限制在窗口范围内）
                btn = find_bright_button(after_img, win_rect=rect, scale=scale)
                if btn:
                    bx = btn[0] / scale
                    by = btn[1] / scale
                    logging.info(f"找到弹窗按钮，点击 ({bx:.0f}, {by:.0f})")
                    cg_click(bx, by)
                    time.sleep(2)
                    final = screenshot()
                    save_debug(final, "04_final.png", debug)
                    logging.info("签到流程完成（已点击弹窗按钮）")
                else:
                    logging.info("未找到弹窗按钮，可能已直接签到")
                return True

            logging.info(f"位置 {ox:.0%} 无反应，尝试下一个位置")

        logging.info("所有卡片位置均无反应，可能今日已签到或卡片不可点击")
        return True

    # 9. 回退流程: 未检测到任何已知按钮/卡片，尝试点击头像→菜单→签到卡片
    logging.info("未找到签到卡片，可能卡片不可见；回退点击头像区域")
    avatar_x = win_x + win_w * 0.08
    avatar_y = win_y + win_h * 0.95
    logging.info(f"回退策略: 点击头像区域 ({avatar_x:.0f}, {avatar_y:.0f})")
    if not dry_run:
        cg_click(avatar_x, avatar_y)
        time.sleep(2.5)

        # v3.2: 点击头像后弹出下拉菜单，需要点击"Buddy 加油站"条目
        menu_x = win_x + win_w * MENU_BUDDY_RATIO[0]
        menu_y = win_y + win_h * MENU_BUDDY_RATIO[1]
        logging.info(f"点击下拉菜单'Buddy 加油站' ({menu_x:.0f}, {menu_y:.0f})")
        cg_click(menu_x, menu_y)
        time.sleep(3)

        # 重新截屏检测
        after_menu = screenshot()
        save_debug(after_menu, "03_after_menu.png", debug)
        after_win = crop_window(after_menu, rect, scale)
        save_debug(after_win, "03_after_menu_window.png", debug)

        # v3.2: 先检查深色"立即领取"按钮（新版未签到状态）
        dark_btn2 = find_dark_button(after_win)
        if dark_btn2:
            db2_l, db2_t, db2_r, db2_b = dark_btn2
            db2_w = db2_r - db2_l
            db2_h = db2_b - db2_t
            click2_x = win_x + (db2_l + db2_w * 0.5) / scale
            click2_y = win_y + (db2_t + db2_h * 0.5) / scale
            logging.info(f"菜单后找到深色'立即领取'按钮 ({click2_x:.0f}, {click2_y:.0f})")
            cg_click(click2_x, click2_y)
            time.sleep(2.5)
            after_card = screenshot()
            save_debug(after_card, "04_after_card.png", debug)
            btn = find_bright_button(after_card, win_rect=rect, scale=scale)
            if btn:
                bx = btn[0] / scale
                by = btn[1] / scale
                logging.info(f"找到弹窗按钮，点击 ({bx:.0f}, {by:.0f})")
                cg_click(bx, by)
                time.sleep(2)
                logging.info("签到流程完成（回退: 头像→菜单→深色按钮→弹窗）")
            else:
                logging.info("未找到弹窗按钮，可能已直接签到")
            return True

        # 检查是否已签到（v3.2: 卡片可能是"今日已领"状态）
        claimed = find_claimed_button(after_win)
        if claimed:
            logging.info("检测到'今日已领'状态，今日已签到，无需操作")
            if debug:
                overlay = [
                    {'kind': 'rect', 'bbox': claimed, 'color': 'green'},
                ]
                save_debug(after_win.copy(), "03_after_menu_claimed.png", debug, overlay=overlay)
            return True

        # 查找旧版签到卡片
        card2 = find_dark_card(after_win)
        if card2:
            logging.info("点击菜单后检测到签到卡片")
            c2_l, c2_t, c2_r, c2_b = card2
            c2_w = c2_r - c2_l
            c2_h = c2_b - c2_t
            click2_x = win_x + (c2_l + c2_w * 0.5) / scale
            click2_y = win_y + (c2_t + c2_h * 0.5) / scale
            logging.info(f"点击卡片中心 ({click2_x:.0f}, {click2_y:.0f})")
            cg_click(click2_x, click2_y)
            time.sleep(2.5)

            after_card = screenshot()
            save_debug(after_card, "04_after_card.png", debug)
            btn = find_bright_button(after_card, win_rect=rect, scale=scale)
            if btn:
                bx = btn[0] / scale
                by = btn[1] / scale
                logging.info(f"找到弹窗按钮，点击 ({bx:.0f}, {by:.0f})")
                cg_click(bx, by)
                time.sleep(2)
                final = screenshot()
                save_debug(final, "05_final.png", debug)

                final_win = crop_window(final, rect, scale)
                if find_claimed_button(final_win):
                    logging.info("验证: 检测到'今日已领'状态，签到成功")
                else:
                    logging.info("签到流程完成（回退: 头像→菜单→卡片→弹窗）")
            else:
                logging.info("未找到弹窗按钮，检查是否已签到...")
                final_win = crop_window(after_card, rect, scale)
                if find_claimed_button(final_win):
                    logging.info("验证: 检测到'今日已领'状态，签到成功（无需弹窗）")
                else:
                    logging.info("未找到弹窗按钮，可能已直接签到")
        else:
            logging.info("点击菜单后仍未找到签到卡片")
            # 最后尝试: 在窗口范围内搜索亮色按钮
            btn = find_bright_button(after_menu, win_rect=rect, scale=scale)
            if btn:
                bx = btn[0] / scale
                by = btn[1] / scale
                logging.info(f"窗口内找到亮色按钮，点击 ({bx:.0f}, {by:.0f})")
                cg_click(bx, by)
                time.sleep(2)
                final = screenshot()
                save_debug(final, "04_final.png", debug)
                logging.info("回退流程完成（窗口内亮色按钮）")
            else:
                logging.info("回退流程完成，未找到可点击目标")
    else:
        logging.info("Dry run 模式，不执行回退点击")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="WorkBuddy macOS 签到脚本 (v3.2 截屏分析模式)"
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
