"""Automatic dialogue translation, independent of word explanation requests."""

import hashlib
import logging
import re
import time
from collections import OrderedDict
from copy import deepcopy

from PySide6.QtCore import QObject, Signal

from .ai import AiClient, Cancelled, endpoint, headers
from .alignment import TranslationAligner
from .marked_translation import combined_messages, marked_translation


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
        self.aligner = TranslationAligner(jobs, self)

    def key(self, context, profile, game=""):
        return (profile.id, profile.base_url.strip(), profile.model.strip(),
                hashlib.sha256(profile.key.encode()).digest(), game, normalized_context(context))

    def remember(self, context, profile, text, game=""):
        key = self.key(context, profile, game)
        self.cache[key] = text
        self.cache.move_to_end(key)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)

    def remember_marked(self, context, profile, text, term, source_start, span, game=""):
        self.remember(context, profile, text, game)
        if term.strip():
            self.aligner.remember(self.key(context, profile, game), term, source_start, text, span)

    def cancel(self, announce=False):
        self.generation += 1
        self.aligner.cancel()
        if self.client:
            self.client.cancel()
            self.changed.emit(self.context, "已停止翻译" if announce else "", False)
        self.client = None
        self.current_key = None

    def align(self, term, context, profile, game="", enabled=True, source_start=-1):
        key = self.key(context, profile, game)
        translation = self.cache.get(key)
        if translation and term.strip():
            self.aligner.request(key, term, context, translation, profile, game, enabled, source_start)

    def request(self, context, profile, enabled=True, game="", term="", source_start=-1):
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
        raw = ""
        started = time.monotonic()
        self.changed.emit(context, "翻译中…", True)
        logging.info("Automatic context translation started; combined=%s", bool(term.strip()))

        def chunk(text):
            nonlocal raw
            if token == self.generation:
                raw += text
                self.output, span = marked_translation(raw)
                self.changed.emit(context, self.output, True)
                if span:
                    self.aligner.changed.emit(context, self.output, term, source_start, *span)

        def done(answer):
            if token != self.generation:
                return
            self.client = None
            text, span = marked_translation(answer, final=True)
            self.remember_marked(context, profile, text, term, source_start, span, game)
            self.store.add("", context, text, profile, "translate", game)
            self.changed.emit(context, text, False)
            logging.info("Automatic context translation completed; combined=%s matched=%s elapsed_ms=%d",
                         bool(term.strip()), span is not None, round((time.monotonic() - started) * 1000))

        def error(exc):
            if token != self.generation:
                return
            self.client = None
            if isinstance(exc, Cancelled):
                text = "已停止翻译"
            else:
                text = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "翻译失败，请重试"
            self.changed.emit(context, text, False)

        self.jobs.start(lambda emit: client.complete(profile, combined_messages(term, context, game, source_start), emit),
                        done, error, chunk)
