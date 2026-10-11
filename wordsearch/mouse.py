"""Observe outside clicks without consuming or synthesizing mouse input."""

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from .native import kernel32, user32


class MouseEvent(ctypes.Structure):
    _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t)]


HookCallback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HookCallback, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class MouseObserver(QObject):
    clicked = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stopping = threading.Event()
        self.thread_id = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="WordSearch-Mouse")
        self.thread.start()

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        # Create the thread queue before close() can post WM_QUIT.
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

        @HookCallback
        def observe(code, kind, data):
            if code >= 0 and kind == 0x201 and not self.stopping.is_set():
                point = ctypes.cast(data, ctypes.POINTER(MouseEvent)).contents.point
                self.clicked.emit(point.x, point.y)
            return user32.CallNextHookEx(None, code, kind, data)

        hook = user32.SetWindowsHookExW(14, observe, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            logging.error("Outside-click observer unavailable: winerror=%d", ctypes.get_last_error())
            return
        try:
            while not self.stopping.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook)

    def close(self):
        self.stopping.set()
        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        self.thread.join(timeout=1)
