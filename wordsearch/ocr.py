import asyncio
import math
import re
from dataclasses import dataclass

from PIL import Image, ImageGrab, ImageOps

from .native import monitor_at


@dataclass
class Capture:
    image: Image.Image
    origin: tuple
    pointer: tuple | None
    selection: tuple | None = None
    fixed_context: bool = False


@dataclass
class Recognition:
    term: str
    context: str
    words: list
    region: tuple
    boxes: tuple = ()


def capture_near(pointer, settings):
    x, y = pointer
    info = monitor_at(x, y)
    m = info.monitor
    fixed_context = bool(settings.region and settings.region[0] <= x <= settings.region[0] + settings.region[2]
                         and settings.region[1] <= y <= settings.region[1] + settings.region[3])
    if fixed_context:
        left, top, width, height = settings.region
    else:
        width, height = settings.crop_width, settings.crop_height
        left = max(m.left, min(x - width // 2, m.right - width))
        top = max(m.top, min(y - height // 2, m.bottom - height))
    right, bottom = min(left + width, m.right), min(top + height, m.bottom)
    left, top = max(left, m.left), max(top, m.top)
    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    return Capture(image, (left, top), (x - left, y - top), fixed_context=fixed_context)


def capture_region(region, settings):
    sx, sy, sw, sh = region
    monitor = monitor_at(sx + sw // 2, sy + sh // 2).monitor
    fixed_context = bool(settings.region and settings.region[0] <= sx and settings.region[1] <= sy
                         and sx + sw <= settings.region[0] + settings.region[2] and sy + sh <= settings.region[1] + settings.region[3])
    if fixed_context:
        left, top, width, height = settings.region
    else:
        left, top = max(monitor.left, sx - 300), max(monitor.top, sy - 180)
        right, bottom = min(monitor.right, sx + sw + 300), min(monitor.bottom, sy + sh + 180)
        width, height = right - left, bottom - top
    return Capture(ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True),
                   (left, top), None, (sx - left, sy - top, sw, sh), fixed_context=fixed_context)


async def _recognize(capture):
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    language = next((x for x in OcrEngine.available_recognizer_languages if x.language_tag.lower().startswith("en")), None)
    engine = OcrEngine.try_create_from_language(language) if language else None
    if engine is None:
        raise RuntimeError("本机缺少英文 OCR。请在 Windows 设置 → 时间和语言 → 语言和区域中安装英语语言包及基本输入功能。")
    maximum = OcrEngine.max_image_dimension
    image = capture.image.convert("RGB")
    scale = min(2.0, maximum / max(image.size))
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    # High contrast improves the serif dialogue font on the dark game panel.
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    if sum(gray.resize((1, 1)).getdata()) < 127:
        gray = ImageOps.invert(gray)
    image = gray.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(image.tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, image.width, image.height, BitmapAlphaMode.IGNORE)
    bitmap.copy_from_buffer(writer.detach_buffer())
    result = await engine.recognize_async(bitmap)
    lines = []
    words = []
    for line_id, line in enumerate(result.lines):
        line_words = []
        for word in line.words:
            rect = word.bounding_rect
            item = {"text": word.text, "x": rect.x / scale, "y": rect.y / scale,
                    "w": rect.width / scale, "h": rect.height / scale, "line": line_id}
            words.append(item)
            line_words.append(item)
        if line_words:
            lines.append({"text": line.text, "words": line_words,
                          "left": min(w["x"] for w in line_words),
                          "right": max(w["x"] + w["w"] for w in line_words),
                          "top": min(w["y"] for w in line_words),
                          "bottom": max(w["y"] + w["h"] for w in line_words), "id": line_id})
    bitmap.close()
    writer.close()
    if not words:
        raise RuntimeError("没有识别到英文文字。请指向文字后重试，或使用框选；也可以手动输入。")
    if capture.pointer is None:
        sx, sy, sw, sh = capture.selection or (0, 0, *capture.image.size)
        selected_words = [w for w in words if sx <= w['x'] + w['w'] / 2 <= sx + sw
                          and sy <= w['y'] + w['h'] / 2 <= sy + sh]
        if not selected_words:
            raise RuntimeError("选框内没有识别到英文，请重新框选或手动输入。")
        selected_ids = {w['line'] for w in selected_words}
        selected_text = "\n".join(" ".join(w['text'] for w in selected_words if w['line'] == line_id)
                                  for line_id in sorted(selected_ids))
        term = selected_text if len(selected_words) <= 8 else ""
        if term:
            focus = next(line for line in lines if line['id'] == min(selected_ids))
            relevant = lines if capture.fixed_context else [line for line in lines if abs(line['top'] - focus['top']) <= 280
                        and min(line['right'], focus['right']) > max(line['left'], focus['left']) + 20]
            context = "\n".join(line['text'] for line in relevant)
        else:
            context = selected_text
    else:
        px, py = capture.pointer

        def distance(w):
            dx = max(w["x"] - px, 0, px - w["x"] - w["w"])
            dy = max(w["y"] - py, 0, py - w["y"] - w["h"])
            return math.hypot(dx, dy)

        selected = min(words, key=distance)
        selected_words = [selected]
        if distance(selected) > 55:
            raise RuntimeError("鼠标附近没有找到单词，请把鼠标移到字母上重试，或框选文字。")
        term = selected["text"].strip('.,!?;:\"“”()[]{}—–')
        focus = next(line for line in lines if line["id"] == selected["line"])
        # Column overlap excludes inventory labels on the left of the dialogue.
        nearby = lines if capture.fixed_context else [line for line in lines if abs(line["top"] - focus["top"]) <= 280
                  and min(line["right"], focus["right"]) > max(line["left"], focus["left"]) + 20]
        context = "\n".join(line["text"] for line in nearby)
    width, height = capture.image.size
    boxes = tuple((capture.origin[0] + w["x"], capture.origin[1] + w["y"], w["w"], w["h"])
                  for w in selected_words)
    return Recognition(term, context, words, (*capture.origin, width, height), boxes)


def recognize(capture):
    return asyncio.run(_recognize(capture))
