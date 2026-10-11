"""Locate a word's equivalent in an existing translation without rewriting it."""

import json
import logging
import re
from collections import OrderedDict
from copy import deepcopy

from PySide6.QtCore import QObject, Signal

from .ai import AiClient, Cancelled, COMMON_PROMPT
from .games import game_hint


ALIGNMENT_PROMPT = """你负责中英文本中的词义对齐。用户提供原文、目标词和已经完成的中文译文。
只在给定译文中定位目标词对应的最短自然连续片段，不重新翻译，不改写译文，不输出解释。
结合原文语境选择对应片段，不要把整个句子标为一个词的译义。复合词中的修饰词可以对应中文词的一部分。
目标位置是原文中的字符起点（从 0 开始）；只对齐该处的目标词。同词出现多次时必须区分每次的含义。
目标位置为 -1 时表示手动输入；若同词有多个不同含义且无法确定选择的是哪一次，返回 target 为 null。
只返回 JSON 对象：{"target":"译文中原样存在的连续文字","occurrence":1}。
occurrence 是该片段在给定译文中从左到右第几次出现，从 1 开始。保留片段的原始文字和标点。
若目标词未出现在原文、没有独立可定位的中文对应，或无法确定，返回 {"target":null,"occurrence":1}。
不要输出代码围栏、标题、英文词义或任何额外文字。"""


def alignment_messages(term, context, translation, game, source_start=-1):
    data = {"目标词": term, "目标位置": source_start, "原文": context, "已有译文": translation}
    return [{"role": "system", "content": ALIGNMENT_PROMPT + "\n" + COMMON_PROMPT + game_hint(game, context)},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]


def alignment_span(answer, translation):
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(), flags=re.I)
    try:
        data = json.loads(answer)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    target, occurrence = data.get("target"), data.get("occurrence", 1)
    if not isinstance(target, str) or not target or len(target) > 120:
        return None
    if type(occurrence) is not int or occurrence < 1:
        return None
    matches = list(re.finditer(re.escape(target), translation))
    return matches[occurrence - 1].span() if occurrence <= len(matches) else None


class TranslationAligner(QObject):
    changed = Signal(str, str, str, int, int, int)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.cache = OrderedDict()
        self.client = self.current_key = None
        self.generation = 0

    def cancel(self):
        self.generation += 1
        if self.client:
            self.client.cancel()
        self.client = self.current_key = None

    def remember(self, translation_key, term, source_start, translation, span):
        key = (translation_key, term.casefold().strip(), source_start, translation)
        self.cache[key] = span
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)

    def request(self, translation_key, term, context, translation, profile, game, enabled=True, source_start=-1):
        key = (translation_key, term.casefold().strip(), source_start, translation)
        if key in self.cache:
            if key != self.current_key:
                self.cancel()
            self.cache.move_to_end(key)
            start, end = self.cache[key] or (-1, -1)
            self.changed.emit(context, translation, term, source_start, start, end)
            logging.info("Translation alignment reused")
            return
        if not enabled or not term.strip():
            self.cancel()
            return
        if self.client and self.current_key == key:
            return
        self.cancel()
        token = self.generation
        client = self.client = AiClient()
        self.current_key = key
        profile = deepcopy(profile)

        def done(answer):
            if token != self.generation:
                return
            self.client = None
            span = alignment_span(answer, translation)
            self.remember(translation_key, term, source_start, translation, span)
            self.changed.emit(context, translation, term, source_start, *(span or (-1, -1)))
            logging.info("Translation alignment completed: matched=%s", span is not None)

        def error(exc):
            if token != self.generation:
                return
            self.client = self.current_key = None
            if not isinstance(exc, Cancelled):
                logging.info("Translation alignment unavailable: %s", type(exc).__name__)

        messages = alignment_messages(term, context, translation, game, source_start)
        self.jobs.start(lambda emit: client.complete(profile, messages, emit), done, error)
