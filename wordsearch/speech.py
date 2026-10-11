import logging

from PySide6.QtCore import QObject, QTimer, Signal


class Speaker(QObject):
    failed = Signal(str)
    playing = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.voice = None
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.poll)

    def speak(self, text, accent="us"):
        if not text.strip():
            return
        try:
            if self.voice is None:
                import pythoncom
                from win32com.client.dynamic import Dispatch
                pythoncom.CoInitialize()
                self.voice = Dispatch("SAPI.SpVoice")
            language = 0x804 if accent == "zh" else (0x809 if accent == "gb" else 0x409)
            voices = self.voice.GetVoices()
            exact, fallback = [], []
            for index in range(voices.Count):
                token = voices.Item(index)
                languages = [int(code, 16) for code in token.GetAttribute("Language").split(";")]
                if language in languages:
                    exact.append(token)
                if any(code & 0x3ff == language & 0x3ff for code in languages):
                    fallback.append(token)
            candidates = exact or fallback
            if not candidates:
                raise RuntimeError("未安装中文语音" if accent == "zh" else "未安装英语语音")
            self.voice.Voice = candidates[0]
            self.voice.Speak(text, 19)  # Async, purge previous speech, plain text.
            logging.info("System speech started")
            self.playing.emit(True)
            self.timer.start()
        except Exception as exc:
            self.timer.stop()
            self.playing.emit(False)
            self.failed.emit(str(exc) if isinstance(exc, RuntimeError) else "朗读无法开始，请检查系统语音")

    def poll(self):
        try:
            if self.voice.Status.RunningState != 2:
                self.timer.stop()
                self.playing.emit(False)
                logging.info("System speech finished")
        except Exception:
            self.timer.stop()
            self.playing.emit(False)

    def stop(self):
        self.timer.stop()
        if self.voice:
            self.voice.Speak("", 19)
        self.playing.emit(False)
