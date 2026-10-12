"""Take a current game frame with Windows cursor capture disabled."""

import logging
import sys
import threading

from PIL import Image, ImageGrab

from .native import window_capture_bounds


def game_frame(box, window):
    from windows_capture import WindowsCapture

    bounds = window_capture_bounds(window)
    # Border control is unavailable on older Windows 10 builds. Keep their
    # cursor-free capture instead of failing over to an ordinary screenshot.
    border = False if sys.getwindowsversion().build >= 20348 else None
    capture = WindowsCapture(cursor_capture=False, draw_border=border, window_hwnd=window)
    finished = threading.Event()
    result = {}

    @capture.event
    def on_frame_arrived(frame, control):
        try:
            if (frame.width, frame.height) != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
                raise RuntimeError("游戏窗口大小已改变，请重新取词。")
            image = Image.frombytes("RGB", (frame.width, frame.height), frame.frame_buffer.tobytes(), "raw", "BGRX")
            result["image"] = image.crop((box[0] - bounds[0], box[1] - bounds[1],
                                          box[2] - bounds[0], box[3] - bounds[1]))
        except Exception as exc:
            result["error"] = exc
        finally:
            control.stop()
            finished.set()

    @capture.event
    def on_closed():
        finished.set()

    control = capture.start_free_threaded()
    try:
        if not finished.wait(2):
            raise RuntimeError("游戏截图超时，请重试。")
        if "error" in result:
            raise result["error"]
        if "image" not in result:
            raise RuntimeError("游戏窗口已关闭，无法截图。")
        control.wait()
        logging.info("Game captured with WGC; cursor_capture=False draw_border=%s", border)
        return result["image"]
    finally:
        if not control.is_finished():
            control.stop()


def grab(box, window=None):
    if window:
        try:
            return game_frame(box, window)
        except Exception as exc:
            logging.warning("WGC unavailable; using screen capture: %s", type(exc).__name__)
    return ImageGrab.grab(bbox=box, all_screens=True)
