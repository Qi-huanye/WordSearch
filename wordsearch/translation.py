"""Automatic dialogue translation, independent of word explanation requests."""

import hashlib
import logging
import re
from collections import OrderedDict
from copy import deepcopy

from PySide6.QtCore import QObject, Signal

from .ai import AiClient, Cancelled, endpoint, headers, messages_for


def normalized_context(context):
    # OCR sometimes drops the isolated dash between the speaker and dialogue.
    # Preserve punctuation inside words and the original text sent to the API.
    context = re.sub(r"(?<!\S)[—–-](?!\S)", "", context)
    return "\n\n".join(re.sub(r"\s+", " ", paragraph).strip()
                       for paragraph in re.split(r"\n\s*\n", context.strip()))


class ContextTranslator(QObject):
    changed = Signal(str, str, bool)

    def __init__(self, jobs, store, parent=None):
        super().__init__(parent)
        self.jobs, self.store = jobs, store
        self.cache = OrderedDict()
        self.client = None
        self.generation = 0
        self.current_key = None
        self.context = self.output = ""

    def key(self, context, profile, game=""):
        return (profile.id, profile.base_url.strip(), profile.model.strip(),
                hashlib.sha256(profile.key.encode()).digest(), game, normalized_context(context))

    def remember(self, context, profile, text, game=""):
        key = self.key(context, profile, game)
        self.cache[key] = text
        self.cache.move_to_end(key)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)

    def cancel(self, announce=False):
        self.generation += 1
        if self.client:
            self.client.cancel()
            self.changed.emit(self.context, "已停止翻译" if announce else "", False)
        self.client = None
        self.current_key = None

    def request(self, context, profile, enabled=True, game=""):
        key = self.key(context, profile, game)
        if not normalized_context(context):
            self.cancel()
            return
        if key in self.cache:
            if self.current_key != key:
                self.cancel()
            self.cache.move_to_end(key)
            self.changed.emit(context, self.cache[key], False)
            logging.info("Context translation reused")
            return
        if not enabled:
            self.cancel()
            self.changed.emit(context, "待翻译", False)
            return
        if self.client and self.current_key == key:
            self.changed.emit(context, self.output or "翻译中…", True)
            return
        self.cancel()
        try:
            endpoint(profile.base_url)
            headers(profile)
            if not profile.model.strip():
                raise ValueError("未选择模型")
        except ValueError:
            self.changed.emit(context, "未配置 API", False)
            return
        profile = deepcopy(profile)
        token = self.generation
        self.current_key, self.context, self.output = key, context, ""
        client = self.client = AiClient()
        self.changed.emit(context, "翻译中…", True)
        logging.info("Automatic context translation started")

        def chunk(text):
            if token == self.generation:
                self.output += text
                self.changed.emit(context, self.output, True)

        def done(answer):
            if token != self.generation:
                return
            self.client = None
            self.remember(context, profile, answer, game)
            self.store.add("", context, answer, profile, "translate", game)
            self.changed.emit(context, answer, False)
            logging.info("Automatic context translation completed")

        def error(exc):
            if token != self.generation:
                return
            self.client = None
            if isinstance(exc, Cancelled):
                text = "已停止翻译"
            else:
                text = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "翻译失败，请重试"
            self.changed.emit(context, text, False)

        self.jobs.start(lambda emit: client.complete(profile, messages_for("", context, "translate", game), emit),
                        done, error, chunk)
