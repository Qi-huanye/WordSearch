import asyncio
import math
import re
from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageOps

from .native import monitor_at
from .capture import grab


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
    source_start: int = -1


CHOICE_START = re.compile(r"^\s*(?:\d{1,2}|[Il])\s*[.)]\s*[-—–]?")
SPEAKER_START = re.compile(r"^[A-Z][A-Z .’'\-]{2,60}\s+[—–-]\s*")


def text_rows(lines):
    """Join OCR fragments on the same physical row, including choice numbers."""
    rows = []
    for line in sorted(lines, key=lambda item: (item["top"], item["left"])):
        target = None
        for row in reversed(rows):
            overlap = min(row["bottom"], line["bottom"]) - max(row["top"], line["top"])
            height = min(row["bottom"] - row["top"], line["bottom"] - line["top"])
            gap = max(row["left"] - line["right"], line["left"] - row["right"], 0)
            if overlap >= height * .5 and gap <= height * 3.5:
                target = row
                break
        if target is None:
            rows.append(dict(line, words=list(line["words"]), ids={line["id"]}))
        else:
            target["words"].extend(line["words"])
            target["ids"].add(line["id"])
            for edge in ("left", "top"):
                target[edge] = min(target[edge], line[edge])
            for edge in ("right", "bottom"):
                target[edge] = max(target[edge], line[edge])
    for row in rows:
        row["words"].sort(key=lambda word: word["x"])
        row["text"] = " ".join(word["text"] for word in row["words"])
    return sorted(rows, key=lambda row: (row["top"], row["left"]))


def paragraphs(rows):
    if not rows:
        return []
    height = median(row["bottom"] - row["top"] for row in rows)
    blocks = []
    for row in rows:
        previous = blocks[-1][-1] if blocks else None
        new_block = previous is None or CHOICE_START.match(row["text"]) or SPEAKER_START.match(row["text"])
        if previous is not None:
            overlap = min(row["right"], previous["right"]) - max(row["left"], previous["left"])
            new_block = new_block or row["top"] - previous["bottom"] > height * .8 or overlap < 20
        if new_block:
            blocks.append([])
        blocks[-1].append(row)
    return blocks


def selected_context(rows, selected_words, expand, with_offset=False):
    selected_ids = {word["line"] for word in selected_words}
    if expand:
        focus = next(row for row in rows if row["ids"] & selected_ids)
        # The fixed capture area may contain several paragraphs or columns.
        rows = [row for row in rows if row["ids"] & selected_ids or
                min(row["right"], focus["right"]) - max(row["left"], focus["left"]) > 20]
    selected_keys = {id(word) for word in selected_words}
    texts = []
    source_start = -1
    for block in paragraphs(rows):
        if not any(row["ids"] & selected_ids for row in block):
            continue
        parts = []
        for row in block:
            row_words = row["words"] if expand else [word for word in row["words"] if id(word) in selected_keys]
            if not row_words:
                continue
            if source_start < 0:
                for index, word in enumerate(row_words):
                    if id(word) in selected_keys:
                        source_start = (sum(len(text) + 2 for text in texts)
                                        + sum(len(part) + 1 for part in parts)
                                        + sum(len(item["text"]) + 1 for item in row_words[:index]))
                        break
            parts.append(" ".join(word["text"] for word in row_words))
        texts.append(" ".join(part for part in parts if part))
    context = "\n\n".join(texts)
    return (context, source_start) if with_offset else context


def capture_near(pointer, settings, window=None):
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
    image = grab((left, top, right, bottom), window)
    return Capture(image, (left, top), (x - left, y - top), fixed_context=fixed_context)


def capture_region(region, settings, window=None):
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
    return Capture(grab((left, top, left + width, top + height), window),
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
    rows = text_rows(lines)
    if capture.pointer is None:
        sx, sy, sw, sh = capture.selection or (0, 0, *capture.image.size)
        selected_words = [w for w in words if sx <= w['x'] + w['w'] / 2 <= sx + sw
                          and sy <= w['y'] + w['h'] / 2 <= sy + sh]
        if not selected_words:
            raise RuntimeError("选框内没有识别到英文，请重新框选或手动输入。")
        selected_text = selected_context(rows, selected_words, expand=False)
        term = selected_text.strip(' \n.,!?;:\"“”()[]{}—–') if len(selected_words) <= 8 else ""
        context, source_start = selected_context(rows, selected_words, expand=True, with_offset=True) if term else (selected_text, -1)
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
        context, source_start = selected_context(rows, selected_words, expand=True, with_offset=True)
    if term and source_start >= 0:
        source_start += len(selected_words[0]["text"]) - len(selected_words[0]["text"].lstrip(' \n.,!?;:\"“”()[]{}—–'))
    width, height = capture.image.size
    boxes = tuple((capture.origin[0] + w["x"], capture.origin[1] + w["y"], w["w"], w["h"])
                  for w in selected_words)
    return Recognition(term, context, words, (*capture.origin, width, height), boxes, source_start)


def recognize(capture):
    return asyncio.run(_recognize(capture))
