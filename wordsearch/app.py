import ctypes
import logging
import sys
import threading
import time
from copy import deepcopy
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .ai import AiClient, Cancelled, endpoint, messages_for, system_prompt
from .native import cursor_position, enable_dpi, foreground_game, parse_hotkey, park_cursor, restore_cursor, user32
from .ocr import capture_near, capture_region, recognize
from .storage import DATA_DIR, Profile, Store
from .ui import MainWindow, SelectionOverlay, STYLE, app_icon
from .dictionary import Dictionary
from .popup import WordHighlight, WordPopup
from .speech import Speaker
from .translation import ContextTranslator, normalized_context


class Jobs(QObject):
    completed = Signal(int, object)
    failed = Signal(int, object)
    chunk = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.counter = 0
        self.callbacks = {}
        self.completed.connect(self.on_complete)
        self.failed.connect(self.on_error)
        self.chunk.connect(self.on_chunk)

    def start(self, action, success, error, chunk=None):
        self.counter += 1
        token = self.counter
        self.callbacks[token] = (success, error, chunk)

        def run():
            try:
                result = action(lambda value: self.chunk.emit(token, value))
                self.completed.emit(token, result)
            except Exception as exc:
                logging.error("Background operation failed: %s", type(exc).__name__)
                self.failed.emit(token, exc)

        threading.Thread(target=run, daemon=True, name=f"WordSearch-{token}").start()
        return token

    @Slot(int, object)
    def on_complete(self, token, result):
        callbacks = self.callbacks.pop(token, None)
        if callbacks:
            callbacks[0](result)

    @Slot(int, object)
    def on_error(self, token, error):
        callbacks = self.callbacks.pop(token, None)
        if callbacks:
            callbacks[1](error)

    @Slot(int, str)
    def on_chunk(self, token, text):
        callbacks = self.callbacks.get(token)
        if callbacks and callbacks[2]:
            callbacks[2](text)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = []

    def apply(self, settings):
        keys = [settings.lookup_hotkey, settings.select_hotkey, settings.region_hotkey]
        parsed = [parse_hotkey(key) for key in keys]
        if len(set(parsed)) != len(parsed):
            raise ValueError("三个快捷键需要互不相同。")
        self.clear()
        for index, (modifiers, key) in enumerate(parsed, 1):
            if not user32.RegisterHotKey(None, index, modifiers, key):
                self.clear()
                raise ValueError(f"快捷键 {keys[index - 1]} 被其他程序占用，请在设置中更换。")
            self.registered.append(index)

    def clear(self):
        for identifier in self.registered:
            user32.UnregisterHotKey(None, identifier)
        self.registered.clear()

    def escape(self, active):
        if active and 99 not in self.registered:
            if user32.RegisterHotKey(None, 99, 0x4000, 0x1B):
                self.registered.append(99)
        elif not active and 99 in self.registered:
            user32.UnregisterHotKey(None, 99)
            self.registered.remove(99)

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            native_message = wintypes.MSG.from_address(int(message))
            if native_message.message == 0x312 and native_message.wParam in self.registered:
                identifier = int(native_message.wParam)
                QTimer.singleShot(0, lambda: self.callback(identifier))
                return True, 0
        return False, 0


class Controller(QObject):
    def __init__(self, application):
        super().__init__()
        self.application = application
        self.store = Store()
        self.settings = self.store.load()
        self.jobs = Jobs()
        self.window = MainWindow(self.settings, self.store)
        self.dictionary = Dictionary()
        self.popup = WordPopup(self.settings)
        self.translator = ContextTranslator(self.jobs, self.store, self)
        self.translator.changed.connect(self.render_translation)
        self.highlight = WordHighlight()
        self.highlight_boxes = ()
        self.speaker = Speaker(self)
        self.speaker.failed.connect(self.popup.set_status)
        self.speaker.playing.connect(self.popup.set_speaking)
        self.lookup_point = QCursor.pos()
        self.lookup_auto = False
        self.capture_source = ""
        self.capturing = False
        self.offline_record = None
        self.hover_point = cursor_position()
        self.hover_since = time.monotonic()
        self.hover_triggered = None
        self.card_dirty = False
        self.history_card = False
        self.completed_translation_key = None
        self.lookup_pointer = self.hover_point
        self.capture_parking = None
        self.ai_client = None
        self.generation = 0
        self.ocr_generation = 0
        self.messages = []
        self.last_record_id = None
        self.last_query = None
        self.output = ""
        self.base_output = ""
        self.query_mode = "lookup"
        self.overlay = None
        self.hotkeys = Hotkeys(self.handle_hotkey)
        self.popup.visibility_changed.connect(self.hotkeys.escape)
        self.popup.visibility_changed.connect(lambda visible: self.highlight.hide() if not visible else None)
        application.installNativeEventFilter(self.hotkeys)
        self.window.lookup_requested.connect(self.lookup_countdown)
        self.window.selection_requested.connect(self.select)
        self.window.query_requested.connect(self.query)
        self.window.followup_requested.connect(self.followup)
        self.window.stop_requested.connect(self.stop)
        self.window.settings_requested.connect(self.save_settings)
        self.window.test_requested.connect(self.test_profile)
        self.window.models_requested.connect(self.read_models)
        self.window.profile_changed.connect(self.select_profile)
        self.window.history_opened.connect(self.reset_query)
        self.window.term.textEdited.connect(self.reset_query)
        self.window.context.textChanged.connect(self.reset_query)
        self.window.dictionary_requested.connect(self.manual_dictionary)
        self.window.history_loaded.connect(self.sync_history)
        self.window.record_changed.connect(self.sync_record_state)
        self.popup.query_requested.connect(self.query)
        self.popup.followup_requested.connect(self.followup)
        self.popup.settings_requested.connect(self.show_settings)
        self.popup.history_requested.connect(self.show_history)
        self.popup.manual_requested.connect(self.lookup_text)
        self.popup.stop_requested.connect(self.stop)
        self.popup.speech_requested.connect(self.speaker.speak)
        self.popup.edit_requested.connect(self.show_editor)
        self.popup.favorite_requested.connect(self.favorite_word)
        self.popup.dictionary_requested.connect(self.restore_dictionary)
        self.popup.dismissed.connect(self.dismiss_popup)
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(120)
        self.hover_timer.timeout.connect(self.hover_tick)
        self.hover_timer.start()
        self.tray = QSystemTrayIcon(app_icon(), application)
        self.tray.setToolTip("WordSearch · 游戏查词")
        menu = QMenu()
        for title, callback in [("打开查词", self.show), ("框选文字", lambda: self.select(False)),
                                ("生词本", self.show_history), ("设置", self.show_settings), ("退出 WordSearch", self.quit)]:
            action = QAction(title, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        application.aboutToQuit.connect(self.hotkeys.clear)
        self.window.move(48, 90)
        self.popup.show()
        try:
            self.hotkeys.apply(self.settings)
            self.hotkeys.escape(self.popup.isVisible())
        except ValueError as exc:
            self.show_settings()
            self.window.set_status(str(exc))
        if self.store.notice:
            self.window.set_status(self.store.notice)
        logging.info("WordSearch 1.4.0 started; OCR and dictionary run locally")

    def render_translation(self, context, text, busy):
        if normalized_context(context) != normalized_context(self.popup.context):
            return
        if text:
            self.completed_translation_key = None
            self.popup.restore_translation(text)
        self.popup.set_translation_busy(busy)

    def show(self):
        if self.card_dirty:
            term, context = self.window.term.text().strip(), self.window.context.toPlainText()
            if term or context:
                self.present_definition(term, context, self.popup.game_name)
            else:
                self.popup.show_home()
            self.card_dirty = False
        self.popup.show()
        self.popup.raise_()
        completed_translation = (self.popup.ai_complete
                                 and self.completed_translation_key == self.translator.key(self.popup.context, self.settings.profile, self.popup.game_name))
        if not self.history_card and not completed_translation and not (self.window.busy and self.query_mode == "translate"):
            self.translator.request(self.popup.context, self.settings.profile, self.settings.auto_translate, self.popup.game_name)

    def show_editor(self):
        self.popup.hide()
        self.window.show_lookup()
        self.window.activateWindow()

    def show_settings(self):
        self.popup.hide()
        self.window.tabs.setCurrentIndex(2)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_history(self):
        self.popup.hide()
        self.window.tabs.setCurrentIndex(1)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def lookup_text(self, term):
        self.window.term.setText(term)
        self.window.context.clear()
        self.manual_dictionary()

    def dismiss_popup(self):
        self.translator.cancel()
        self.speaker.stop()
        self.highlight_boxes = ()
        self.highlight.hide()
        self.invalidate_ocr()
        self.capturing = False
        self.hover_triggered = cursor_position()
        self.stop(announce=False)

    def hover_tick(self):
        if self.capturing:
            if self.lookup_auto and (not foreground_game() or not self.hover_position_matches()):
                self.invalidate_ocr()
                self.capturing = False
                self.hover_point = cursor_position()
                self.hover_since = time.monotonic()
                self.hover_triggered = None
            return
        if not foreground_game() or not self.popup.isVisible():
            self.highlight.hide()
        elif self.highlight_boxes and not self.highlight.isVisible():
            self.highlight.set_boxes(self.highlight_boxes)
            self.popup.raise_()
        if (not self.settings.hover_lookup or self.capturing or self.popup.held or self.window.busy
                or self.window.isVisible() or (self.overlay and self.overlay.isVisible()) or not foreground_game()):
            return
        if self.popup.isVisible() and self.popup.frameGeometry().contains(QCursor.pos()):
            return
        point = cursor_position()
        if self.settings.region:
            x, y, width, height = self.settings.region
            if not (x <= point[0] <= x + width and y <= point[1] <= y + height):
                return
        if abs(point[0] - self.hover_point[0]) + abs(point[1] - self.hover_point[1]) > 5:
            self.hover_point = point
            self.hover_since = time.monotonic()
            return
        if self.hover_triggered and abs(point[0] - self.hover_triggered[0]) + abs(point[1] - self.hover_triggered[1]) < 18:
            return
        if time.monotonic() - self.hover_since >= 0.65:
            self.hover_triggered = point
            self.lookup(auto=True)

    def hover_position_matches(self):
        point = cursor_position()
        return point == self.capture_parking or abs(point[0] - self.lookup_pointer[0]) + abs(point[1] - self.lookup_pointer[1]) <= 18

    def sync_history(self, row):
        self.translator.cancel()
        self.history_card = True
        self.highlight_boxes = ()
        self.highlight.hide()
        self.popup.set_definition(self.dictionary.lookup(row["term"]), row["context"], self.lookup_point, row["id"], show=False)
        self.popup.set_game_source(row["game"])
        if row["provider"] != "ECDICT":
            self.popup.set_mode(row["mode"])
            self.popup.set_ai(row["answer"], complete=False)
        else:
            self.offline_record = (row["term"], row["context"], row["id"])
        self.popup.set_favorite(bool(row["favorite"]))
        self.popup.hide()
        self.card_dirty = False

    def favorite_word(self):
        self.window.toggle_favorite()
        self.popup.set_favorite(self.window.record_favorite)

    def sync_record_state(self):
        row = self.store.record(self.window.record_id) if self.window.record_id else None
        if not row:
            self.window.record_id = None
            self.window.followup_button.setEnabled(False)
            self.popup.followup_button.setEnabled(False)
        self.window.record_favorite = bool(row and row["favorite"])
        self.window.favorite_button.setEnabled(bool(row))
        self.window.favorite_button.setText("★ 已收藏" if self.window.record_favorite else "☆ 收藏")
        self.popup.favorite.setEnabled(bool(row))
        self.popup.set_favorite(self.window.record_favorite)

    def restore_dictionary(self):
        self.stop(announce=False)
        self.messages = []
        translation = self.popup.translation_text
        source = self.popup.game_name
        self.present_definition(self.window.term.text().strip(), self.window.context.toPlainText(), source)
        if translation:
            self.popup.restore_translation(translation)
        self.window.followup_button.setEnabled(False)

    def manual_dictionary(self):
        term = self.window.term.text().strip()
        if not term:
            self.window.set_status("请输入需要查询的单词。")
            return
        self.reset_query()
        self.highlight_boxes = ()
        self.highlight.hide()
        self.invalidate_ocr()
        self.window.preview_button.setEnabled(False)
        self.lookup_point = QCursor.pos()
        self.window.hide()
        self.present_definition(term, self.window.context.toPlainText(), self.popup.game_name)

    def handle_hotkey(self, identifier):
        if identifier == 99:
            self.popup.dismiss()
            return
        if self.overlay and self.overlay.isVisible():
            return
        if identifier == 1:
            self.lookup()
        else:
            self.select(identifier == 3)

    def lookup_countdown(self):
        self.window.set_status("1.5 秒后取词，请把鼠标移到游戏中的英文上。")
        self.window.hide()
        self.popup.hide()
        QTimer.singleShot(1500, self.lookup)

    def invalidate_ocr(self):
        self.ocr_generation += 1
        return self.ocr_generation

    def lookup(self, auto=False):
        self.capture_source = "Disco Elysium" if foreground_game() else ""
        self.stop(announce=False)
        self.highlight_boxes = ()
        self.highlight.hide()
        token = self.invalidate_ocr()
        pointer = cursor_position()
        self.lookup_pointer = pointer
        self.hover_triggered = pointer
        self.hover_point = pointer
        self.hover_since = time.monotonic()
        self.lookup_point = QCursor.pos()
        self.lookup_auto = auto
        self.capturing = True
        self.window.hide()
        self.popup.hide()
        parking = park_cursor(pointer)
        self.capture_parking = parking
        self.window.set_status("正在本地识别单词与上下文…")

        def capture():
            if token != self.ocr_generation:
                restore_cursor(pointer, parking)
                return
            try:
                image = capture_near(pointer, self.settings)
                restore_cursor(pointer, parking)
                self.run_ocr(image, token)
            except Exception as exc:
                restore_cursor(pointer, parking)
                self.ocr_error(exc, token)

        QTimer.singleShot(160, capture)

    def select(self, fixed_region=False):
        self.capture_source = "Disco Elysium" if foreground_game() else ""
        self.stop(announce=False)
        self.highlight_boxes = ()
        self.highlight.hide()
        self.invalidate_ocr()
        self.capturing = False
        self.window.hide()
        self.popup.hide()
        if self.overlay:
            self.overlay.close()
        self.overlay = SelectionOverlay("设定对白区域" if fixed_region else "框选英文文字")
        self.overlay.selected.connect(lambda region: self.selection_done(region, fixed_region))
        self.overlay.cancelled.connect(self.show)
        QTimer.singleShot(160, self.overlay.show)

    def selection_done(self, region, fixed_region):
        if fixed_region:
            self.settings.region = region
            self.store.save(self.settings)
            self.window.draft.region = region
            self.window.apply_font()
            self.window.set_status("对白区域已保存", temporary=True)
            self.show()
        else:
            token = self.invalidate_ocr()
            self.capturing = True
            self.lookup_auto = False
            self.lookup_point = QCursor.pos()
            pointer = cursor_position()
            self.hover_point = pointer
            self.hover_triggered = pointer
            self.hover_since = time.monotonic()
            parking = park_cursor(pointer)

            def capture():
                if token != self.ocr_generation:
                    restore_cursor(pointer, parking)
                    return
                try:
                    image = capture_region(region, self.settings)
                    restore_cursor(pointer, parking)
                    self.run_ocr(image, token)
                except Exception as exc:
                    restore_cursor(pointer, parking)
                    self.ocr_error(exc, token)

            QTimer.singleShot(200, capture)

    def run_ocr(self, image, token):
        self.window.set_capture(image.image)
        self.jobs.start(lambda emit: recognize(image), lambda result: self.ocr_done(result, token),
                        lambda error: self.ocr_error(error, token))

    def ocr_done(self, result, token):
        if token != self.ocr_generation:
            return
        self.capturing = False
        if self.lookup_auto and (not foreground_game() or not self.hover_position_matches()):
            return
        self.window.term.setText(result.term)
        context_blocked = self.window.context.blockSignals(True)
        self.window.context.setPlainText(result.context)
        self.window.context.blockSignals(context_blocked)
        self.window.record_id = None
        self.window.favorite_button.setEnabled(False)
        self.window.followup_button.setEnabled(False)
        self.messages = []
        self.present_definition(result.term, result.context, self.capture_source)
        self.highlight_boxes = result.boxes
        if foreground_game():
            self.highlight.set_boxes(self.highlight_boxes)
            self.popup.raise_()
        profile = self.settings.profile
        configured = bool(profile.key) or profile.base_url.startswith(("http://localhost:", "http://127.0.0.1:"))
        if self.settings.auto_query and configured and not self.lookup_auto:
            self.query("lookup" if result.term else "translate")

    def present_definition(self, term, context, source=""):
        self.card_dirty = False
        self.history_card = False
        self.completed_translation_key = None
        definition = self.dictionary.lookup(term)
        answer = definition.markdown() if definition.entries else "本地词典没有收录此表达。可使用 AI 语境解释当前文本。"
        self.window.answer.setMarkdown(answer)
        existing = self.store.record(self.offline_record[2]) if self.offline_record and self.offline_record[:2] == (term, context) else None
        if existing and existing["game"] != source:
            existing = None
        if existing:
            self.window.record_id = existing["id"]
        else:
            self.window.record_id = self.store.add(term, context, answer, Profile("ECDICT", "", "offline"), game=source)
            self.offline_record = (term, context, self.window.record_id)
        self.window.record_favorite = bool(existing and existing["favorite"])
        self.window.favorite_button.setEnabled(True)
        self.window.favorite_button.setText("★ 已收藏" if self.window.record_favorite else "☆ 收藏")
        self.window.set_status("")
        self.window.followup_row.hide()
        self.popup.set_definition(definition, context, self.lookup_point, self.window.record_id)
        self.popup.set_game_source(source)
        self.popup.set_favorite(self.window.record_favorite)
        self.translator.request(context, self.settings.profile, self.settings.auto_translate, source)

    def ocr_error(self, error, token):
        if token != self.ocr_generation:
            return
        self.capturing = False
        text = str(error) if isinstance(error, RuntimeError) else f"OCR 无法完成（{type(error).__name__}），请重试或手动输入。"
        self.window.set_status(text)
        if not self.lookup_auto:
            self.popup.set_status(text)
            self.show()

    def stop(self, announce=True):
        if announce:
            self.translator.cancel(announce=True)
        if self.ai_client:
            self.ai_client.cancel()
        self.generation += 1
        self.window.set_busy(False)
        self.popup.set_busy(False)
        if announce:
            self.window.set_status("已停止", temporary=True)
            self.popup.set_status("已停止")

    def reset_query(self, *args):
        self.translator.cancel()
        self.highlight_boxes = ()
        self.highlight.hide()
        was_busy = self.window.busy
        self.stop(announce=False)
        self.messages = []
        self.last_record_id = None
        self.last_query = None
        self.window.record_id = None
        self.window.favorite_button.setEnabled(False)
        self.window.followup_button.setEnabled(False)
        self.window.followup_row.hide()
        self.card_dirty = True
        if self.window.isVisible():
            self.popup.hide()
        if was_busy:
            self.window.set_status("当前文本已切换，旧查询已停止。")

    def query(self, mode):
        term = self.window.term.text().strip()
        context = self.window.context.toPlainText().strip()
        if mode == "lookup" and not term:
            self.window.set_status("请输入单词或短语，或点击整句解释。")
            return
        if mode == "translate" and not context:
            self.window.set_status("请先识别或粘贴一段对白。")
            return
        self.messages = messages_for(term, context, mode, self.popup.game_name)
        self.query_mode = mode
        self.base_output = ""
        self.window.record_id = None
        self.window.favorite_button.setEnabled(False)
        self.begin_query(term, context, deepcopy(self.settings.profile))

    def followup(self, text):
        if self.window.busy:
            return
        if not self.messages or not self.window.record_id or self.window.record_id != self.last_record_id:
            self.window.set_status("请先完成一次 AI 查词，再继续追问。")
            return
        if self.last_query != (self.window.term.text().strip(), self.window.context.toPlainText().strip()):
            self.window.set_status("单词或上下文已修改，请先重新查词，再继续追问。")
            return
        self.messages[0] = {"role": "system", "content": system_prompt("followup", self.popup.game_name, self.popup.context)}
        self.messages.append({"role": "user", "content": text})
        self.base_output = self.window.answer.toPlainText() + "\n\n---\n\n追问：" + text + "\n\n"
        self.window.followup.clear()
        self.begin_query(self.window.term.text().strip(), self.window.context.toPlainText().strip(), deepcopy(self.settings.profile))

    def begin_query(self, term, context, profile):
        source = self.popup.game_name
        if self.query_mode == "translate":
            self.translator.cancel()
        self.stop(announce=False)
        token = self.generation
        self.ai_client = AiClient()
        client = self.ai_client
        try:
            endpoint(profile.base_url)
            from .ai import headers
            headers(profile)
            if not profile.model:
                raise ValueError("请先在设置中填写模型名。")
        except ValueError as exc:
            self.window.set_status(str(exc))
            self.show_settings()
            return
        if (self.popup.term, self.popup.context) != (term, context):
            self.popup.set_definition(self.dictionary.lookup(term), context, self.lookup_point, None, show=False)
            self.popup.set_game_source(source)
        self.popup.prepare_ai(self.query_mode)
        self.completed_translation_key = None
        self.history_card = False
        self.card_dirty = False
        self.output = ""
        self.window.record_id = None
        self.window.favorite_button.setEnabled(False)
        self.popup.favorite.setEnabled(False)
        self.window.answer.setMarkdown(self.base_output + "正在联系 AI…")
        self.popup.set_ai(self.base_output + "查询中…", mode=self.query_mode)
        self.window.followup_row.hide()
        self.window.set_busy(True)
        self.popup.set_busy(True)
        self.window.set_status("")
        messages = deepcopy(self.messages)

        def chunk(text):
            if token == self.generation:
                self.output += text
                self.window.answer.setMarkdown(self.base_output + self.output)
                self.popup.set_ai(self.base_output + self.output, mode=self.query_mode)
                scrollbar = self.window.answer.verticalScrollBar()
                if scrollbar.maximum() - scrollbar.value() < 160:
                    scrollbar.setValue(scrollbar.maximum())

        def done(answer):
            if token != self.generation:
                return
            self.messages.append({"role": "assistant", "content": answer})
            output = self.base_output + answer
            self.window.answer.setMarkdown(output)
            self.popup.set_ai(output, complete=True, mode=self.query_mode)
            if self.query_mode == "translate":
                self.completed_translation_key = self.translator.key(context, profile, source)
            if self.query_mode == "translate" and not self.base_output:
                self.translator.remember(context, profile, answer, source)
            self.window.record_id = self.store.add(term, context, output, profile, self.query_mode, source)
            self.last_record_id = self.window.record_id
            self.last_query = (term, context)
            self.window.record_favorite = False
            self.window.favorite_button.setEnabled(True)
            self.window.favorite_button.setText("☆ 收藏")
            self.popup.favorite.setEnabled(True)
            self.popup.set_favorite(False)
            self.window.set_busy(False)
            self.popup.set_busy(False)
            self.window.followup_row.show()
            self.window.set_status("")
            self.ai_client = None

        def error(exc):
            if token != self.generation:
                return
            self.window.set_busy(False)
            self.popup.set_busy(False)
            if isinstance(exc, Cancelled):
                self.window.set_status("已停止当前查询。")
            else:
                self.window.set_status(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "查询无法完成，请检查服务配置并重试。")
                self.popup.set_status(self.window.status.text())
                if not self.output:
                    self.window.answer.setMarkdown(self.base_output + "### 查询未完成\n\n上方单词和上下文已经保留。请检查设置或稍后重试。")
            self.ai_client = None

        self.jobs.start(lambda emit: client.complete(profile, messages, emit), done, error, chunk)

    def select_profile(self, identifier):
        self.translator.cancel()
        self.settings.selected_id = identifier
        self.store.save(self.settings)
        self.window.draft.selected_id = identifier
        self.window.set_status("")
        if self.popup.isVisible():
            self.translator.request(self.popup.context, self.settings.profile, self.settings.auto_translate, self.popup.game_name)

    def save_settings(self, settings):
        previous = self.settings
        try:
            for profile in settings.profiles:
                endpoint(profile.base_url)
            self.hotkeys.apply(settings)
            self.hotkeys.escape(self.popup.isVisible())
            self.store.save(settings)
        except (ValueError, OSError) as exc:
            try:
                self.hotkeys.apply(previous)
            except ValueError:
                pass
            self.window.set_status(str(exc))
            return
        self.settings = settings
        self.translator.cancel()
        self.popup.settings = settings
        self.window.settings_applied(settings)
        self.window.set_status("已保存", temporary=True)

    def test_profile(self, profile):
        self.window.test_button.setEnabled(False)
        self.window.set_status("正在向所选模型发送连接测试…")

        def success(answer):
            self.window.test_button.setEnabled(True)
            self.window.set_status(f"连接成功：{profile.name} / {profile.model}。")

        def error(exc):
            self.window.test_button.setEnabled(True)
            self.window.set_status(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "连接未成功，请检查接口与模型。")

        self.jobs.start(lambda emit: AiClient().complete(profile, [{"role": "user", "content": "Reply with OK."}], emit, True), success, error)

    def read_models(self, profile):
        self.window.models_button.setEnabled(False)
        self.window.set_status("正在读取服务模型列表…")

        def success(models):
            self.window.models_button.setEnabled(True)
            current_profile = self.window.form_profile()
            if (current_profile.id, current_profile.base_url, current_profile.key) != (profile.id, profile.base_url, profile.key):
                self.window.set_status(f"{profile.name} 的模型列表已返回；当前配置已切换，请重新读取。")
                return
            current = self.window.model.currentText()
            self.window.model.clear()
            self.window.model.addItems(models)
            if current:
                self.window.model.setCurrentText(current)
            self.window.set_status(f"读取到 {len(models)} 个模型，也可手动填写。")

        def error(exc):
            self.window.models_button.setEnabled(True)
            self.window.set_status(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "未读取到模型，可直接填写。")

        self.jobs.start(lambda emit: AiClient().models(profile), success, error)

    def quit(self):
        self.translator.cancel()
        self.stop(False)
        self.speaker.stop()
        self.highlight.hide()
        self.hotkeys.clear()
        self.tray.hide()
        self.popup.hide()
        self.application.quit()


def main():
    enable_dpi()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA_DIR / "app.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = QApplication(sys.argv)
    app.setApplicationName("WordSearch")
    app.setOrganizationName("WordSearch")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    socket = QLocalSocket()
    socket.connectToServer("WordSearchDesktop")
    if socket.waitForConnected(250):
        socket.write(b"show")
        socket.waitForBytesWritten(250)
        return 0
    QLocalServer.removeServer("WordSearchDesktop")
    server = QLocalServer()
    server.listen("WordSearchDesktop")
    controller = Controller(app)

    def incoming():
        client = server.nextPendingConnection()
        if client:
            controller.show()
            client.disconnectFromServer()

    server.newConnection.connect(incoming)
    return app.exec()
