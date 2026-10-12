import logging
import sys
import threading
import time
from copy import deepcopy
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .ai import AiClient, Cancelled, endpoint, headers, messages_for, system_prompt
from .native import cursor_position, enable_dpi, foreground_game, game_window, parse_hotkey, qt_to_physical, user32
from .ocr import capture_near, capture_region, recognize
from .storage import DATA_DIR, Profile, Store
from .theme import STYLE, app_icon, make_menu
from .ui import EditDialog, MainWindow, SelectionOverlay, to_pixmap
from .dictionary import Dictionary
from .popup import WordHighlight, WordPopup
from .speech import Speaker
from .translation import ContextTranslator, normalized_context
from .mouse import MouseObserver
from .marked_translation import combined_messages, marked_translation
from . import __version__


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
        self.editor = EditDialog()
        self.dictionary = Dictionary()
        self.popup = WordPopup(self.settings)
        self.translator = ContextTranslator(self.jobs, self.store, self)
        self.translator.changed.connect(self.render_translation)
        self.translator.aligner.changed.connect(self.render_alignment)
        self.highlight = WordHighlight()
        self.highlight_boxes = ()
        self.speaker = Speaker(self)
        self.speaker.failed.connect(self.popup.set_status)
        self.speaker.playing.connect(self.popup.set_speaking)
        self.lookup_point = QCursor.pos()
        # The word and paragraph on the card; AI queries and corrections start from here.
        self.term = self.context = ""
        self.capture_image = None
        self.capture_source = ""
        self.capture_window = None
        self.capturing = False
        self.offline_record = None
        self.record_id = None
        self.record_favorite = False
        self.busy = False
        self.ai_client = None
        self.generation = 0
        self.ocr_generation = 0
        self.messages = []
        self.last_record_id = None
        self.output = ""
        self.base_output = ""
        self.query_mode = "lookup"
        self.overlay = None
        self.hotkeys = Hotkeys(self.handle_hotkey)
        self.popup.visibility_changed.connect(self.hotkeys.escape)
        self.popup.visibility_changed.connect(lambda visible: self.highlight.hide() if not visible else None)
        application.installNativeEventFilter(self.hotkeys)
        self.window.lookup_requested.connect(self.lookup_text)
        self.window.record_opened.connect(self.open_record)
        self.window.record_changed.connect(self.sync_record_state)
        self.window.settings_requested.connect(self.save_settings)
        self.window.test_requested.connect(self.test_profile)
        self.window.models_requested.connect(self.read_models)
        self.editor.submitted.connect(self.apply_edit)
        self.popup.query_requested.connect(self.query)
        self.popup.followup_requested.connect(self.followup)
        self.popup.settings_requested.connect(self.show_settings)
        self.popup.history_requested.connect(self.show_history)
        self.popup.manual_requested.connect(lambda term: self.lookup_text(term, self.popup.anchor))
        self.popup.stop_requested.connect(self.stop)
        self.popup.speech_requested.connect(self.speaker.speak)
        self.popup.edit_requested.connect(self.show_editor)
        self.popup.favorite_requested.connect(self.favorite_word)
        self.popup.dictionary_requested.connect(self.restore_dictionary)
        self.popup.dismissed.connect(self.dismiss_popup)
        self.mouse = MouseObserver(self)
        self.mouse.clicked.connect(self.outside_click)
        application.aboutToQuit.connect(self.mouse.close)
        self.highlight_timer = QTimer(self)
        self.highlight_timer.setInterval(120)
        self.highlight_timer.timeout.connect(self.update_highlight)
        self.highlight_timer.start()
        self.tray = QSystemTrayIcon(app_icon(), application)
        self.tray_menu = None
        self.tray.activated.connect(lambda reason: self.show_history() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.update_tray()
        self.tray.show()
        application.aboutToQuit.connect(self.hotkeys.clear)
        try:
            self.hotkeys.apply(self.settings)
        except ValueError as exc:
            self.show_settings()
            self.window.set_status(str(exc))
        else:
            self.tray.showMessage("WordSearch", f"已在后台运行 · {self.settings.lookup_hotkey} 取词", app_icon(), 3000)
        if self.store.notice:
            self.show_settings()
            self.window.set_status(self.store.notice)
        logging.info("WordSearch %s started; OCR and dictionary run locally", __version__)

    def update_tray(self):
        settings = self.settings
        menu = make_menu()
        menu.addAction(f"框选文字\t{settings.select_hotkey}", lambda: self.select(False))
        menu.addAction(f"设定对白区域\t{settings.region_hotkey}", lambda: self.select(True))
        menu.addSeparator()
        menu.addAction("生词本", self.show_history)
        menu.addAction("设置", self.show_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        if self.tray_menu:
            self.tray_menu.deleteLater()
        self.tray_menu = menu
        self.tray.setToolTip(f"WordSearch · {settings.lookup_hotkey} 取词")

    def render_translation(self, context, text, busy):
        if normalized_context(context) != normalized_context(self.popup.context):
            return
        if text:
            self.popup.restore_translation(text)
        self.popup.set_translation_busy(busy)
        if text and not busy:
            self.translator.align(self.popup.term, context, self.settings.profile,
                                  self.popup.game_name, self.settings.auto_translate, self.popup.source_start)

    def render_alignment(self, context, translation, term, source_start, start, end):
        if (normalized_context(context) == normalized_context(self.popup.context)
                and term.casefold().strip() == self.popup.term.casefold().strip()
                and source_start == self.popup.source_start
                and self.popup.translation_text.startswith(translation)):
            self.popup.highlight_translation(start, end)

    def show_editor(self):
        self.popup.hide()
        self.editor.open_with(self.term, self.context, to_pixmap(self.capture_image) if self.capture_image else None)

    def show_settings(self):
        self.popup.hide()
        self.window.open_page(MainWindow.SETTINGS)

    def show_history(self):
        self.popup.hide()
        self.window.open_page(MainWindow.HISTORY)

    def start_card(self, anchor=None):
        """Forget the previous card's query state before showing a new card."""
        self.reset_query()
        self.invalidate_ocr()
        self.capturing = False
        if anchor is not None:
            self.lookup_point = anchor

    def lookup_text(self, term, anchor=None):
        self.start_card(anchor if anchor is not None else QCursor.pos())
        self.capture_image = None
        self.present_definition(term, "")

    def apply_edit(self, term, context):
        self.start_card()
        self.present_definition(term, context, self.popup.game_name)

    def open_record(self, row):
        # Let the activating click reach the outside-click observer before the card appears.
        QTimer.singleShot(150, lambda: self.show_record(row))

    def show_record(self, row):
        self.start_card(QCursor.pos())
        self.capture_image = None
        self.term, self.context = row["term"], row["context"]
        self.popup.set_definition(self.dictionary.lookup(row["term"]), row["context"], self.lookup_point, row["id"])
        self.popup.set_game_source(row["game"])
        if row["provider"] != "ECDICT":
            self.popup.set_mode(row["mode"])
            self.popup.set_ai(row["answer"], complete=False)
        else:
            self.offline_record = (row["term"], row["context"], row["id"])
            # Reuse a translation cached in this session; never start a request for old records.
            self.translator.request(row["context"], self.settings.profile, False, row["game"], row["term"])
        self.record_id = row["id"]
        self.record_favorite = bool(row["favorite"])
        self.popup.set_favorite(self.record_favorite)

    def dismiss_popup(self):
        self.translator.cancel()
        self.speaker.stop()
        self.highlight_boxes = ()
        self.highlight.hide()
        self.invalidate_ocr()
        self.capturing = False
        self.stop(announce=False)

    @Slot(int, int)
    def outside_click(self, x, y):
        if not self.popup.isVisible() or QApplication.activePopupWidget() is not None:
            return
        screen = self.popup.screen()
        frame = self.popup.frameGeometry()
        left, top = qt_to_physical(frame.topLeft(), screen)
        ratio = screen.devicePixelRatio()
        if not (left <= x < left + frame.width() * ratio and top <= y < top + frame.height() * ratio):
            self.popup.dismiss()

    def update_highlight(self):
        if self.capturing:
            return
        if not foreground_game() or not self.popup.isVisible():
            self.highlight.hide()
        elif self.highlight_boxes and not self.highlight.isVisible():
            self.highlight.set_boxes(self.highlight_boxes)
            self.popup.raise_()

    def favorite_word(self):
        # Favourites belong to the word, so the button works during AI queries too.
        term = self.term.strip()
        if not term:
            return
        value = not self.store.is_favorite(term)
        if not self.store.favorite_term(term, value) and value:
            # The word was deleted from the library while its card stayed open.
            definition = self.dictionary.lookup(term)
            self.store.add(term, self.context, definition.markdown() or "本地词典没有收录此表达。",
                           Profile("ECDICT", "", "offline"), game=self.popup.game_name)
            self.store.favorite_term(term, True)
        self.record_favorite = value
        self.popup.set_favorite(value)
        self.refresh_window()

    def sync_record_state(self):
        row = self.store.record(self.record_id) if self.record_id else None
        if not row:
            self.record_id = None
            self.popup.followup_button.setEnabled(False)
        self.record_favorite = self.store.is_favorite(self.term)
        self.popup.set_favorite(self.record_favorite)

    def refresh_window(self):
        if self.window.isVisible():
            self.window.refresh_history()

    def restore_dictionary(self):
        self.stop(announce=False)
        self.messages = []
        translation = self.popup.translation_text
        self.present_definition(self.term, self.context, self.popup.game_name, self.popup.source_start)
        if translation:
            self.popup.restore_translation(translation)

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

    def invalidate_ocr(self):
        self.ocr_generation += 1
        return self.ocr_generation

    def lookup(self):
        window = game_window()
        self.capture_source = "Disco Elysium" if foreground_game() else ""
        self.stop(announce=False)
        self.highlight_boxes = ()
        self.highlight.hide()
        token = self.invalidate_ocr()
        pointer = cursor_position()
        self.lookup_point = QCursor.pos()
        self.capturing = True
        self.window.hide()
        self.popup.hide()

        def capture():
            if token != self.ocr_generation:
                return
            try:
                self.run_capture(lambda: capture_near(pointer, self.settings, window), token)
            except Exception as exc:
                self.ocr_error(exc, token)

        QTimer.singleShot(160, capture)

    def select(self, fixed_region=False):
        self.capture_window = game_window()
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
        QTimer.singleShot(160, self.overlay.show)

    def selection_done(self, region, fixed_region):
        if fixed_region:
            self.settings.region = region
            self.store.save(self.settings)
            self.window.draft.region = region
            self.window.update_region_status()
            self.tray.showMessage("WordSearch", "对白区域已保存", app_icon(), 2000)
            return
        token = self.invalidate_ocr()
        self.capturing = True
        self.lookup_point = QCursor.pos()

        def capture():
            if token != self.ocr_generation:
                return
            try:
                window = self.capture_window
                self.run_capture(lambda: capture_region(region, self.settings, window), token)
            except Exception as exc:
                self.ocr_error(exc, token)

        QTimer.singleShot(200, capture)

    def run_capture(self, capture, token):
        def run(emit):
            image = capture()
            return image, recognize(image)

        def done(result):
            if token == self.ocr_generation:
                image, recognition = result
                self.capture_image = image.image
                self.ocr_done(recognition, token)

        self.jobs.start(run, done, lambda error: self.ocr_error(error, token))

    def ocr_done(self, result, token):
        if token != self.ocr_generation:
            return
        self.capturing = False
        self.messages = []
        self.present_definition(result.term, result.context, self.capture_source, result.source_start)
        self.highlight_boxes = result.boxes
        if foreground_game():
            self.highlight.set_boxes(self.highlight_boxes)
            self.popup.raise_()
        profile = self.settings.profile
        configured = bool(profile.key) or profile.base_url.startswith(("http://localhost:", "http://127.0.0.1:"))
        if self.settings.auto_query and configured:
            self.query("lookup" if result.term else "translate")

    def present_definition(self, term, context, source="", source_start=-1):
        self.term, self.context = term, context
        definition = self.dictionary.lookup(term)
        answer = definition.markdown() if definition.entries else "本地词典没有收录此表达。可使用 AI 语境解释当前文本。"
        existing = self.store.record(self.offline_record[2]) if self.offline_record and self.offline_record[:2] == (term, context) else None
        if existing and existing["game"] != source:
            existing = None
        if existing:
            self.record_id = existing["id"]
        else:
            self.record_id = self.store.add(term, context, answer, Profile("ECDICT", "", "offline"), game=source)
            self.offline_record = (term, context, self.record_id)
        self.record_favorite = self.store.is_favorite(term)
        self.popup.set_definition(definition, context, self.lookup_point, self.record_id, source_start=source_start)
        self.popup.set_game_source(source)
        self.popup.set_favorite(self.record_favorite)
        self.translator.request(context, self.settings.profile, self.settings.auto_translate, source, term, source_start)
        self.refresh_window()

    def ocr_error(self, error, token):
        if token != self.ocr_generation:
            return
        self.reset_query()
        self.capturing = False
        self.term = self.context = ""
        text = str(error) if isinstance(error, RuntimeError) else f"OCR 无法完成（{type(error).__name__}），请重试或手动输入。"
        # Fall back to the card's input field at the lookup position.
        self.popup.show_home()
        self.popup.anchor = self.lookup_point
        self.popup.set_status(text)
        self.popup.show()
        self.popup.position_card()
        self.popup.raise_()

    def set_busy(self, value):
        self.busy = value
        self.popup.set_busy(value)

    def stop(self, announce=True):
        if announce:
            self.translator.cancel(announce=True)
        if self.ai_client:
            self.ai_client.cancel()
        self.generation += 1
        self.set_busy(False)
        if announce:
            self.popup.set_status("已停止")

    def reset_query(self):
        self.translator.cancel()
        self.highlight_boxes = ()
        self.highlight.hide()
        self.stop(announce=False)
        self.messages = []
        self.last_record_id = None
        self.record_id = None

    def query(self, mode):
        term, context = self.term, self.context
        if mode == "lookup" and not term.strip():
            self.popup.set_status("没有可解释的单词。")
            return
        if mode == "translate" and not context.strip():
            self.popup.set_status("没有可翻译的段落；右键「修正原文」可补充。")
            return
        source_start = self.popup.source_start if (self.popup.term, self.popup.context) == (term, context) else -1
        self.messages = (combined_messages(term, context, self.popup.game_name, source_start) if mode == "translate"
                         else messages_for(term, context, mode, self.popup.game_name))
        self.query_mode = mode
        self.base_output = ""
        self.record_id = None
        self.begin_query(term, context, deepcopy(self.settings.profile))

    def followup(self, text):
        if self.busy:
            return
        if not self.messages or not self.record_id or self.record_id != self.last_record_id:
            self.popup.set_status("请先完成一次 AI 查询，再继续追问。")
            return
        self.messages[0] = {"role": "system", "content": system_prompt("followup", self.popup.game_name, self.popup.context)}
        self.messages.append({"role": "user", "content": text})
        previous = self.popup.translation_text if self.query_mode == "translate" else self.popup.ai_text
        self.base_output = previous + "\n\n---\n\n追问：" + text + "\n\n"
        self.begin_query(self.term, self.context, deepcopy(self.settings.profile))

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
            headers(profile)
            if not profile.model:
                raise ValueError("请先在设置中填写模型名。")
        except ValueError as exc:
            self.show_settings()
            self.window.set_status(str(exc))
            return
        if (self.popup.term, self.popup.context) != (term, context):
            self.popup.set_definition(self.dictionary.lookup(term), context, self.lookup_point, None, show=False)
            self.popup.set_game_source(source)
        self.popup.prepare_ai(self.query_mode)
        self.output = ""
        self.record_id = None
        self.popup.set_ai(self.base_output + "查询中…", mode=self.query_mode)
        self.set_busy(True)
        messages = deepcopy(self.messages)
        combined = self.query_mode == "translate" and not self.base_output
        source_start = self.popup.source_start
        raw = ""
        started = time.monotonic()
        if combined:
            logging.info("Manual context translation started; combined=%s", bool(term))

        def chunk(text):
            nonlocal raw
            if token == self.generation:
                raw += text
                self.output, span = marked_translation(raw) if combined else (raw, None)
                self.popup.set_ai(self.base_output + self.output, mode=self.query_mode)
                if span:
                    self.popup.highlight_translation(*span)

        def done(answer):
            if token != self.generation:
                return
            if combined:
                answer, span = marked_translation(answer, final=True)
                self.translator.remember_marked(context, profile, answer, term, source_start, span, source)
                logging.info("Manual context translation completed; combined=%s matched=%s elapsed_ms=%d",
                             bool(term), span is not None, round((time.monotonic() - started) * 1000))
            self.messages.append({"role": "assistant", "content": answer})
            output = self.base_output + answer
            self.popup.set_ai(output, complete=True, mode=self.query_mode)
            if self.query_mode == "translate":
                self.translator.align(term, context, profile, source, source_start=source_start)
            self.record_id = self.store.add(term, context, output, profile, self.query_mode, source)
            self.last_record_id = self.record_id
            self.record_favorite = self.store.is_favorite(term)
            self.popup.set_favorite(self.record_favorite)
            self.set_busy(False)
            self.ai_client = None
            self.refresh_window()

        def error(exc):
            if token != self.generation:
                return
            self.set_busy(False)
            if not isinstance(exc, Cancelled):
                if not self.output:
                    self.popup.set_ai(self.base_output + "查询未完成", mode=self.query_mode)
                self.popup.set_status(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "查询无法完成，请检查服务配置并重试。")
            self.ai_client = None

        self.jobs.start(lambda emit: client.complete(profile, messages, emit), done, error, chunk)

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
        self.update_tray()
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
            controller.show_history()
            client.disconnectFromServer()

    server.newConnection.connect(incoming)
    return app.exec()
