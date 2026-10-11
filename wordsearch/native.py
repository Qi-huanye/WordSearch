import base64
import ctypes
import re
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]


user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
kernel32.LocalFree.restype = wintypes.HLOCAL
crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]


def enable_dpi():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass


def cursor_position():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def foreground_game():
    return bool(game_window())


def game_window():
    window = user32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(window, title, len(title))
    return window if "disco elysium" in title.value.lower() else None


def window_capture_bounds(window):
    rect = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(window, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        if not user32.GetWindowRect(window, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
    return rect.left, rect.top, rect.right, rect.bottom


def monitor_at(x, y):
    handle = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info


def qt_to_physical(point, screen):
    # The matching display's native origin avoids multiplying virtual origins.
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    found = []

    @callback_type
    def callback(handle, hdc, rect, data):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        if info.device == screen.name():
            found.append(info.monitor)
        return True

    user32.EnumDisplayMonitors(None, None, callback, 0)
    geometry = screen.geometry()
    ratio = screen.devicePixelRatio()
    origin_x = found[0].left if found else geometry.x()
    origin_y = found[0].top if found else geometry.y()
    return (round(origin_x + (point.x() - geometry.x()) * ratio),
            round(origin_y + (point.y() - geometry.y()) * ratio))


def protect(text):
    if not text:
        return ""
    raw = text.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if not crypt32.CryptProtectData(ctypes.byref(incoming), "WordSearch API Key", None,
                                   None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return base64.b64encode(ctypes.string_at(outgoing.data, outgoing.size)).decode("ascii")
    finally:
        kernel32.LocalFree(outgoing.data)


def unprotect(encoded):
    if not encoded:
        return ""
    raw = base64.b64decode(encoded)
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(incoming), None, None, None,
                                     None, 1, ctypes.byref(outgoing)):
        raise ValueError("无法解密此 API Key，请在当前 Windows 用户下重新填写。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size).decode("utf-8")
    finally:
        kernel32.LocalFree(outgoing.data)


def parse_hotkey(text):
    parts = [part.strip().upper() for part in text.split("+")]
    modifiers = 0x4000
    keys = []
    for part in parts:
        if part in ("CTRL", "CONTROL"):
            modifiers |= 2
        elif part == "ALT":
            modifiers |= 1
        elif part == "SHIFT":
            modifiers |= 4
        else:
            keys.append(part)
    if len(keys) != 1 or not modifiers & 7:
        raise ValueError("快捷键需要 Ctrl / Alt / Shift 加一个字母、数字或 F1–F12。")
    key = keys[0]
    if re.fullmatch(r"[A-Z0-9]", key):
        return modifiers, ord(key)
    if re.fullmatch(r"F([1-9]|1[0-2])", key):
        return modifiers, 0x70 + int(key[1:]) - 1
    raise ValueError("快捷键的主键请使用字母、数字或 F1–F12。")


def exclude_from_capture(widget):
    user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11)
