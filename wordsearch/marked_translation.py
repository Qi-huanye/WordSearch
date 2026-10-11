"""Translate and mark one equivalent in a single, streamable response."""

import json

from .ai import messages_for

OPEN, CLOSE = "<ws>", "</ws>"

MARK_PROMPT = """本次翻译同时标记目标词在译文中的中文对应。
目标位置是原文从 0 开始的字符起点，只标该位置的目标词；-1 表示手动输入，含义不明确时不标记。
用 <ws> 和 </ws> 包裹对应的最短自然中文片段，全篇至多一对。标记内仅放译文原本应有的文字。
区分修饰词和中心词，例如 nautical knot 可译为 <ws>航海</ws>结（目标 nautical），或航海<ws>结</ws>（目标 knot）。
不要把整个复合词或句子标为修饰词的译义，不为方便标记而改变自然译法。
未提供目标词、目标词未出现在原文或没有可确定的对应时，输出普通译文，不加标记。
除这对内部标记外，只输出中文译文；不输出 JSON、代码围栏、标题、对齐说明或额外文字。"""


def combined_messages(term, context, game="", source_start=-1):
    messages = messages_for("", context, "translate", game)
    if not term.strip():
        return messages
    messages[0]["content"] += "\n" + MARK_PROMPT
    messages[1]["content"] = "请翻译当前文本并标记目标词对应，保留段落。\n" + json.dumps(
        {"目标词": term, "目标位置": source_start, "当前文本": context}, ensure_ascii=False)
    return messages


def marked_translation(raw, final=False):
    # Hold split tags until the next streaming chunk so markup never flashes.
    raw = raw.lstrip()
    for length in range(max(len(OPEN), len(CLOSE)) - 1, 0, -1):
        suffix = raw[-length:]
        if suffix and any(tag.startswith(suffix) for tag in (OPEN, CLOSE)):
            raw = raw[:-length]
            break
    span = None
    if raw.count(OPEN) == raw.count(CLOSE) == 1:
        opening, closing = raw.find(OPEN), raw.find(CLOSE)
        if closing > opening + len(OPEN):
            span = opening, closing - len(OPEN)
    text = raw.replace(OPEN, "").replace(CLOSE, "")
    if final:
        text = text.rstrip()
    if span and (span[1] > len(text) or not text[span[0]:span[1]].strip()):
        span = None
    return text, span
