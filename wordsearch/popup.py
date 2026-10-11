import html
import re

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .native import monitor_at
from .ui import app_icon

POPUP_STYLE = """
QWidget#WordCard { background:transparent; border:0; }
QWidget#WordCard QWidget { background:transparent; border:0; color:#f1f1f1; font-family:'Segoe UI','Microsoft YaHei UI'; font-size:16px; }
QWidget#WordCard QLabel#Word { font-size:24px; font-weight:700; }
QWidget#WordCard QLabel#Phonetic { color:#bcbfc0; font-size:14px; }
QWidget#WordCard QLabel#PartOfSpeech { color:#d5d6d7; background:#303336; border-radius:2px; padding:1px 4px; font-size:12px; }
QWidget#WordCard QPushButton, QWidget#WordCard QToolButton { background:transparent; border:0; border-radius:2px; color:#c6c8c9; padding:3px; }
QWidget#WordCard QPushButton:hover, QWidget#WordCard QToolButton:hover { background:#35383b; color:#ffffff; }
QWidget#WordCard QPushButton:disabled, QWidget#WordCard QToolButton:disabled { color:#73777a; }
QWidget#WordCard QToolButton#Bookmark { padding:0; background:transparent; }
QWidget#WordCard QPushButton#SectionTitle { text-align:left; font-size:18px; padding:0; }
QWidget#WordCard QPushButton#Explain { text-align:left; font-size:14px; padding:4px 5px; }
QWidget#WordCard QPushButton#ContextToggle { background:#2b2e30; text-align:left; border-radius:3px; }
QWidget#WordCard QPushButton#ContextToggle:hover { background:#323538; }
QWidget#WordCard QTextBrowser { background:transparent; color:#f1f1f1; border:0; padding:0; }
QWidget#WordCard QTextBrowser#Context { background:#303336; border:1px solid #292c2e; border-radius:3px; padding:12px; color:#c8ccce; }
QWidget#WordCard QLineEdit { background:#303336; border:1px solid #45494c; color:#eeeeee; padding:7px; font-size:15px; border-radius:3px; }
QWidget#WordCard QFrame#Divider { background:#c0c2c3; border:0; min-height:1px; max-height:1px; }
QWidget#WordCard QLabel#Status { color:#e0ba98; font-size:14px; }
QWidget#WordCard QScrollBar:vertical { background:transparent; width:6px; }
QWidget#WordCard QScrollBar::handle:vertical { background:#55595c; border-radius:3px; min-height:24px; }
QWidget#WordCard QScrollBar::add-line:vertical, QWidget#WordCard QScrollBar::sub-line:vertical { height:0; }
QWidget#WordCard QScrollBar::add-page:vertical, QWidget#WordCard QScrollBar::sub-page:vertical { background:transparent; }
QWidget#WordCard QMenu { background:#2c2f31; color:#eeeeee; border:1px solid #4a4d50; padding:5px; }
QWidget#WordCard QMenu::item { color:#eeeeee; padding:8px 22px; }
QWidget#WordCard QMenu::item:selected { background:#3d4144; }
"""

POS = {"n": "名词", "a": "形容词", "adj": "形容词", "v": "动词", "vt": "及物动词",
       "vi": "不及物动词", "adv": "副词", "ad": "副词", "r": "副词", "prep": "介词",
       "pron": "代词", "conj": "连词", "num": "数词", "interj": "感叹词", "int": "感叹词",
       "aux": "助动词", "art": "冠词"}
PREFIX = re.compile(r"^([a-z]+)\.\s*", re.I)


class CardDivider(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Divider")
        self.setFixedHeight(1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#c0c2c3"))


def card_icon(name, active=False):
    picture = QPixmap(32, 32)
    picture.fill(Qt.GlobalColor.transparent)
    p = QPainter(picture)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#ff7659" if name == "bookmark" else ("#ffffff" if active else "#bfc2c4"))
    p.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == "bookmark":
        path = QPainterPath(QPointF(1, 0))
        for x, y in [(31, 0), (31, 31), (25, 31), (16, 24), (7, 31), (1, 31), (1, 0)]:
            path.lineTo(x, y)
        if active:
            p.setBrush(color)
        p.drawPath(path)
    elif name == "speaker":
        path = QPainterPath(QPointF(5, 13))
        for x, y in [(11, 13), (17, 8), (17, 24), (11, 19), (5, 19), (5, 13)]:
            path.lineTo(x, y)
        p.drawPath(path)
        p.drawArc(16, 9, 9, 14, -60 * 16, 120 * 16)
        p.drawArc(17, 5, 14, 22, -60 * 16, 120 * 16)
    elif name == "sparkle":
        for cx, cy, size in [(13, 18, 7), (24, 8, 4)]:
            path = QPainterPath(QPointF(cx, cy - size))
            for x, y in [(cx + size, cy), (cx, cy + size), (cx - size, cy), (cx, cy - size)]:
                path.lineTo(x, y)
            p.drawPath(path)
    elif name == "stop":
        p.setBrush(color)
        p.drawRoundedRect(9, 9, 14, 14, 1, 1)
    elif name == "pin":
        p.drawLine(10, 6, 22, 6)
        p.drawLine(12, 6, 12, 16)
        p.drawLine(20, 6, 20, 16)
        p.drawLine(8, 18, 24, 18)
        p.drawLine(16, 18, 16, 28)
    p.end()
    return QIcon(picture)


def clean_sense(text, chinese=True):
    lines = []
    for line in text.splitlines():
        match = PREFIX.match(line)
        if chinese and match and match[1].lower() in POS:
            line = ("" if not lines else POS[match[1].lower()] + " · ") + line[match.end():]
        lines.append((line.replace(", ", "，") if chinese else line).strip())
    return "\n".join(lines)


class WordHighlight(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus |
                         Qt.WindowType.WindowTransparentForInput)
        self.setWindowTitle("WordSearch · 单词高亮")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.boxes = []

    def set_boxes(self, boxes):
        if not boxes:
            self.hide()
            return
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        info = monitor_at(int(left), int(top))
        screen = next((s for s in QApplication.screens() if s.name() == info.device), QApplication.primaryScreen())
        ratio, geometry = screen.devicePixelRatio(), screen.geometry()
        self.setGeometry(round(geometry.left() + (left - info.monitor.left) / ratio),
                         round(geometry.top() + (top - info.monitor.top) / ratio),
                         max(1, round((right - left) / ratio)), max(1, round((bottom - top) / ratio)))
        self.boxes = [QRectF((x-left)/ratio, (y-top)/ratio, w/ratio, h/ratio) for x,y,w,h in boxes]
        self.update()
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        for box in self.boxes:
            painter.fillRect(box, QColor(223, 216, 27, 135))


class WordPopup(QWidget):
    query_requested = Signal(str)
    followup_requested = Signal(str)
    edit_requested = Signal()
    history_requested = Signal()
    manual_requested = Signal(str)
    settings_requested = Signal()
    favorite_requested = Signal()
    dictionary_requested = Signal()
    speech_requested = Signal(str, str)
    stop_requested = Signal()
    dismissed = Signal()
    visibility_changed = Signal(bool)

    def __init__(self, settings):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.settings = settings
        self.setWindowTitle("WordSearch · 鼠标词典")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("WordCard")
        self.setStyleSheet(POPUP_STYLE)
        self.setFixedWidth(min(580, QApplication.primaryScreen().availableGeometry().width() - 24))
        self.anchor = QPoint(48, 90)
        self.held = self.busy = self.showing_ai = self.followup_open = False
        self.ai_complete = False
        self.translation_busy = False
        self.definition = None
        self.term = self.context = self.ai_text = self.translation_text = self.lookup_text = ""
        self.mode = "dictionary"
        self.drag_offset = None
        self.pointer_top = False
        self.pointer_x = self.width() // 2
        self.audio_button = None
        box = QVBoxLayout(self)
        box.setContentsMargins(32, 26, 32, 34)
        box.setSpacing(18)
        self.favorite = self.icon_button("bookmark", "收藏当前单词", self.favorite_requested.emit, 26)
        self.favorite.setCheckable(True)
        self.favorite.setObjectName("Bookmark")
        self.favorite.setFixedSize(26, 30)
        self.favorite.move(self.width() - 53, 0)
        self.translation_section = QWidget()
        translation_layout = QVBoxLayout(self.translation_section)
        translation_layout.setContentsMargins(0, 0, 0, 0)
        translation_layout.setSpacing(9)
        translation_head = QHBoxLayout()
        translation_head.setSpacing(8)
        title = QLabel("语境翻译")
        title.setStyleSheet("font-size:18px;color:#c9cccd")
        translation_head.addWidget(title)
        self.translate_button = self.icon_button("sparkle", "翻译当前对白", lambda: self.request_ai("translate"))
        translation_head.addWidget(self.translate_button)
        translation_head.addWidget(self.divider(), 1)
        self.stop_button = self.icon_button("stop", "停止查询", self.stop_requested.emit)
        self.stop_button.hide()
        translation_head.addWidget(self.stop_button)
        translation_layout.addLayout(translation_head)
        translation_indent = QHBoxLayout()
        translation_indent.setContentsMargins(16, 0, 0, 0)
        self.translation_body = self.browser("语境翻译结果")
        translation_indent.addWidget(self.translation_body)
        translation_layout.addLayout(translation_indent)
        box.addWidget(self.translation_section)
        dictionary_head = QHBoxLayout()
        dictionary_head.setSpacing(8)
        self.dictionary_button = QPushButton("ECDICT")
        self.dictionary_button.setObjectName("SectionTitle")
        self.dictionary_button.setToolTip("显示本地词典")
        self.dictionary_button.clicked.connect(self.dictionary_requested.emit)
        dictionary_head.addWidget(self.dictionary_button)
        self.ai_button = self.icon_button("sparkle", "解释当前单词", lambda: self.request_ai("lookup"))
        dictionary_head.addWidget(self.ai_button)
        dictionary_head.addWidget(self.divider(), 1)
        box.addLayout(dictionary_head)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(16, 0, 0, 0)
        body.setSpacing(9)
        heading = QHBoxLayout()
        heading.setSpacing(6)
        self.word = QLabel("查词")
        self.word.setObjectName("Word")
        self.word.setWordWrap(True)
        self.word.setAccessibleName("词卡目标词")
        self.word.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.word)
        self.uk_audio = self.icon_button("speaker", "朗读单词（英式语音优先）", lambda: self.speak_word("gb"))
        self.us_audio = self.icon_button("speaker", "朗读单词（美式语音优先）", lambda: self.speak_word("us"))
        heading.addWidget(self.uk_audio)
        heading.addWidget(self.us_audio)
        heading.addStretch(1)
        body.addLayout(heading)
        metadata = QHBoxLayout()
        metadata.setSpacing(8)
        self.phonetic = QLabel()
        self.phonetic.setObjectName("Phonetic")
        self.part = QLabel()
        self.part.setObjectName("PartOfSpeech")
        metadata.addWidget(self.phonetic)
        metadata.addWidget(self.part)
        metadata.addStretch()
        body.addLayout(metadata)
        self.home_row = QWidget()
        home = QHBoxLayout(self.home_row)
        home.setContentsMargins(0, 0, 0, 0)
        self.home_input = QLineEdit()
        self.home_input.setPlaceholderText("单词或短语")
        self.home_input.setAccessibleName("输入查询词")
        self.home_input.returnPressed.connect(self.submit_manual)
        home.addWidget(self.home_input, 1)
        search = QPushButton("查词")
        search.clicked.connect(self.submit_manual)
        home.addWidget(search)
        body.addWidget(self.home_row)
        self.content = self.browser("词典释义")
        body.addWidget(self.content)
        self.more_senses = QPushButton("更多释义 ⌄")
        self.more_senses.setObjectName("Explain")
        self.more_senses.setMinimumHeight(30)
        self.more_senses.setCheckable(True)
        self.more_senses.toggled.connect(self.expand_senses)
        body.addWidget(self.more_senses, 0, Qt.AlignmentFlag.AlignLeft)
        self.explain_button = QPushButton("解释")
        self.explain_button.setIcon(card_icon("sparkle"))
        self.explain_button.setIconSize(QSize(16, 16))
        self.explain_button.setObjectName("Explain")
        self.explain_button.setMinimumHeight(30)
        self.explain_button.clicked.connect(self.explain)
        body.addWidget(self.explain_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.context_button = QPushButton("上下文")
        self.context_button.setObjectName("ContextToggle")
        self.context_button.setStyleSheet("text-align:left;font-size:14px;padding-left:16px")
        self.context_button.setCheckable(True)
        self.context_button.setFixedHeight(44)
        context_heading = QHBoxLayout(self.context_button)
        context_heading.setContentsMargins(16, 0, 16, 0)
        self.context_arrow = QLabel("⌄")
        self.context_arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        context_heading.addStretch()
        context_heading.addWidget(self.context_arrow)
        self.context_button.toggled.connect(self.expand_context)
        body.addWidget(self.context_button)
        self.original = self.browser("当前原句")
        self.original.setObjectName("Context")
        self.original.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context_audio = self.icon_button("speaker", "朗读原句", self.speak_context)
        self.context_audio.setParent(self.original)
        self.game_name = ""
        self.game_label = QLabel(self.original)
        self.game_label.setStyleSheet("font-size:12px;color:#969da1;background:transparent")
        self.game_label.setAccessibleName("来源应用")
        self.game_label.hide()
        body.addWidget(self.original)
        self.followup_row = QWidget()
        follow = QHBoxLayout(self.followup_row)
        follow.setContentsMargins(0, 0, 0, 0)
        self.followup = QLineEdit()
        self.followup.setPlaceholderText("追问")
        self.followup.setAccessibleName("词卡追问")
        self.followup.returnPressed.connect(self.submit_followup)
        follow.addWidget(self.followup, 1)
        self.followup_button = QPushButton("发送")
        self.followup_button.clicked.connect(self.submit_followup)
        follow.addWidget(self.followup_button)
        body.addWidget(self.followup_row)
        self.hint = QLabel()
        self.hint.setObjectName("Status")
        self.hint.setWordWrap(True)
        body.addWidget(self.hint)
        box.addWidget(self.body)
        self.show_home()

    def browser(self, name):
        item = QTextBrowser()
        item.setAccessibleName(name)
        item.setOpenLinks(False)
        item.setOpenExternalLinks(False)
        item.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        font = QFont("Segoe UI")
        font.setPixelSize(max(16, self.settings.font_size))
        item.document().setDefaultFont(font)
        item.document().setDocumentMargin(0)
        return item

    def divider(self):
        return CardDivider()

    def icon_button(self, name, title, handler, size=16):
        item = QToolButton(self)
        item.setIcon(card_icon(name))
        item.setIconSize(QSize(size, size))
        item.setFixedSize(22, 24)
        item.setToolTip(title)
        item.setAccessibleName(title)
        item.clicked.connect(handler)
        return item

    def set_held(self, value):
        self.held = value

    def open_menu(self, point=None):
        menu = QMenu(self)
        for text, action in [("编辑原文", self.edit_requested.emit), ("生词本", self.history_requested.emit),
                             ("设置", self.settings_requested.emit), ("本地词典", self.dictionary_requested.emit)]:
            menu.addAction(text, action)
        pin = menu.addAction("固定词卡")
        pin.setCheckable(True)
        pin.setChecked(self.held)
        pin.toggled.connect(self.set_held)
        menu.addAction("收起", self.dismiss)
        menu.exec(point or self.mapToGlobal(QPoint(self.width() - 30, 30)))

    def contextMenuEvent(self, event):
        self.open_menu(event.globalPos())

    def set_favorite(self, value):
        self.favorite.setChecked(value)
        self.favorite.setIcon(card_icon("bookmark", value))
        self.favorite.setToolTip("取消收藏" if value else "收藏当前单词")

    def set_mode(self, mode):
        self.mode = mode

    def request_ai(self, mode):
        self.set_mode(mode)
        self.query_requested.emit(mode if self.term or mode == "translate" else "translate")

    def prepare_ai(self, mode):
        self.set_mode(mode)
        self.ai_complete = False
        self.followup_row.hide()
        self.set_status("")

    def show_home(self):
        self.definition = None
        self.ai_complete = False
        self.term = self.context = self.ai_text = self.lookup_text = self.translation_text = ""
        self.word.setText("查词")
        self.home_row.show()
        self.translation_section.hide()
        self.phonetic.hide()
        self.part.hide()
        for widget in (self.original, self.context_button, self.followup_row, self.more_senses,
                       self.content, self.explain_button, self.uk_audio, self.us_audio):
            widget.hide()
        self.ai_button.setEnabled(False)
        self.favorite.setEnabled(False)
        self.dictionary_button.setEnabled(False)
        self.home_input.clear()
        self.set_status("")
        self.position_card()
        self.move(48, 90)

    def set_definition(self, definition, context, point, record_id, show=True):
        self.definition = definition
        self.term, self.context = definition.term, context
        self.ai_text = self.lookup_text = self.translation_text = ""
        self.ai_complete = False
        self.followup_open = False
        self.translation_busy = False
        self.word.setText(self.term or "对白")
        self.home_row.hide()
        self.content.show()
        self.uk_audio.setVisible(bool(self.term))
        self.us_audio.setVisible(bool(self.term))
        self.translation_section.setVisible(bool(context))
        self.translation_body.setPlainText("待翻译")
        self.translation_body.setVisible(bool(context))
        self.resize_browser(self.translation_body, 35, 200)
        first = definition.entries[0] if definition.entries and definition.kind != "phrase" else None
        self.phonetic.setText(f"/{first.phonetic.strip('/')}/" if first and first.phonetic else "")
        self.phonetic.setVisible(bool(self.phonetic.text()))
        match = PREFIX.match(first.translation) if first else None
        self.part.setText(POS.get(match[1].lower(), "") if match else "")
        self.part.setVisible(bool(self.part.text()))
        escaped = html.escape(context).replace("\n", "<br>")
        if self.term:
            escaped = re.sub(re.escape(html.escape(self.term)), lambda m: f"<span style='background:#999822;color:#ffffff'>{m[0]}</span>", escaped, flags=re.I)
        self.original.setHtml("<i>" + escaped + "</i>")
        self.game_name = ""
        self.game_label.hide()
        self.context_button.setChecked(False)
        self.context_button.setVisible(bool(context))
        self.original.hide()
        self.more_senses.blockSignals(True)
        self.more_senses.setChecked(False)
        self.more_senses.blockSignals(False)
        self.favorite.setEnabled(bool(record_id))
        self.set_favorite(False)
        self.dictionary_button.setEnabled(True)
        self.ai_button.setEnabled(bool(self.term))
        self.translate_button.setEnabled(bool(context))
        self.explain_button.setEnabled(bool(self.term or context))
        self.explain_button.show()
        self.followup_row.hide()
        self.set_status("")
        self.anchor = point
        self.show_dictionary()
        if show:
            self.show()
            self.raise_()
            QTimer.singleShot(0, self.position_card)

    def show_dictionary(self):
        self.showing_ai = False
        self.set_mode("dictionary")
        self.dictionary_button.setText("ECDICT")
        self.followup_row.hide()
        parts = []
        entries = self.definition.entries if self.definition else []
        expanded = self.more_senses.isChecked()
        needs_more = bool(self.definition and self.definition.kind == "form")
        display_entries = entries if expanded or not needs_more else entries[:1]
        for index, item in enumerate(display_entries):
            if self.definition.kind == "phrase" or index:
                parts.append(f"<p style='margin:5px 0; font-size:18px; font-weight:600'>{html.escape(item.word)}</p>")
            chinese, english = clean_sense(item.translation), clean_sense(item.definition, False)
            lines = chinese.splitlines()
            if not expanded and (len(lines) > 3 or len(chinese) > 280 or len(english) > 400):
                needs_more = True
                chinese = "\n".join(lines[:3])
                if len(chinese) > 280:
                    chinese = chinese[:277] + "…"
                english = "\n".join(english.splitlines()[:2])
                if len(english) > 400:
                    english = english[:397] + "…"
            if chinese:
                parts.append("<p style='margin:0 0 9px'>" + html.escape(chinese).replace("\n", "<br>") + "</p>")
            if english:
                parts.append("<p style='margin:0;color:#d0d2d3'>" + html.escape(english).replace("\n", "<br>") + "</p>")
        self.content.setHtml("".join(parts) or "未收录")
        self.more_senses.setVisible(needs_more or expanded)
        self.more_senses.setText("收起释义 ⌃" if expanded else "更多释义 ⌄")
        self.resize_browser(self.content, 35, 280 if expanded else 180)
        self.position_card()

    def expand_senses(self, value):
        if not self.showing_ai:
            self.show_dictionary()

    def expand_context(self, value):
        self.context_arrow.setText("⌃" if value else "⌄")
        if value:
            self.resize_browser(self.original, 80, 180, padding=44)
        self.original.setVisible(value and bool(self.context))
        self.adjustSize()
        self.position_card()
        if value:
            QTimer.singleShot(0, self.update_context_controls)

    def set_ai(self, text, complete=False, mode=None):
        if mode is not None:
            self.set_mode(mode)
        self.ai_text = text
        self.ai_complete = complete
        if self.mode == "translate":
            self.translation_text = text
            self.translation_body.setPlainText(text)
            self.translation_body.show()
            self.resize_browser(self.translation_body, 35, 200)
        else:
            self.showing_ai = True
            self.lookup_text = text
            self.dictionary_button.setText("语境释义")
            self.more_senses.hide()
            self.content.setMarkdown(text)
            self.resize_browser(self.content, 50, 280)
        self.followup_button.setEnabled(complete and not self.busy)
        self.followup_row.setVisible(self.followup_open and complete)
        self.position_card()

    def restore_translation(self, text):
        self.translation_text = text
        self.translation_body.setPlainText(text)
        self.translation_body.setVisible(bool(text))
        self.resize_browser(self.translation_body, 35, 200)
        self.position_card()

    def explain(self):
        if self.ai_text and not self.busy and self.followup_button.isEnabled():
            self.followup_open = not self.followup_open
            self.followup_row.setVisible(self.followup_open)
            if self.followup_open:
                self.followup.setFocus()
            self.position_card()
        else:
            self.request_ai("lookup" if self.term else "translate")

    def resize_browser(self, browser, minimum, maximum, padding=4):
        browser.ensurePolished()
        font = QFont("Segoe UI")
        font.setPixelSize(max(16, self.settings.font_size))
        browser.document().setDefaultFont(font)
        cursor = QTextCursor(browser.document())
        cursor.select(QTextCursor.SelectionType.Document)
        formatting = QTextCharFormat()
        formatting.setFontFamilies(["Segoe UI", "Microsoft YaHei UI"])
        formatting.setFontPointSize(max(16, self.settings.font_size) * 0.75)
        cursor.mergeCharFormat(formatting)
        browser.document().setTextWidth(self.width() - 84)
        browser.setFixedHeight(max(minimum, min(maximum, int(browser.document().size().height()) + padding)))
        self.adjustSize()

    def position_card(self):
        screen = QApplication.screenAt(self.anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        for container in (self.translation_section, self.body):
            container.layout().invalidate()
            container.layout().activate()
        self.layout().invalidate()
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())
        excess = self.height() - area.height() + 24
        for browser in (self.original, self.translation_body, self.content):
            if excess > 0 and not browser.isHidden():
                reduction = min(excess, max(0, browser.height() - 45))
                browser.setFixedHeight(browser.height() - reduction)
                excess -= reduction
        self.setFixedHeight(self.layout().sizeHint().height())
        if self.held and self.isVisible():
            self.update()
            return
        x = max(area.left() + 8, min(self.anchor.x() - self.width() // 2, area.right() - self.width() - 8))
        y = self.anchor.y() - self.height() - 20
        self.pointer_top = y < area.top() + 8
        if self.pointer_top:
            y = self.anchor.y() + 22
        y = max(area.top() + 8, min(y, area.bottom() - self.height() - 8))
        self.pointer_x = max(24, min(self.width() - 24, self.anchor.x() - x))
        self.move(x, y)
        self.update()
        if self.original.isVisible():
            QTimer.singleShot(0, self.update_context_controls)

    def set_busy(self, value):
        self.busy = value
        self.ai_button.setEnabled(not value and bool(self.term))
        self.translate_button.setEnabled(not value and not self.translation_busy and bool(self.context))
        self.explain_button.setEnabled(not value)
        self.followup_button.setEnabled(not value and self.ai_complete)
        self.stop_button.setVisible(value or self.translation_busy)
        self.position_card()

    def set_translation_busy(self, value):
        self.translation_busy = value
        self.translate_button.setEnabled(not value and not self.busy and bool(self.context))
        self.stop_button.setVisible(value or self.busy)
        self.position_card()

    def set_status(self, text):
        self.hint.setText(text)
        self.hint.setVisible(bool(text))
        self.position_card()

    def speak_word(self, accent):
        self.audio_button = self.uk_audio if accent == "gb" else self.us_audio
        self.speech_requested.emit(self.term, accent)

    def speak_context(self):
        self.audio_button = self.context_audio
        self.speech_requested.emit(self.context, "us")

    def set_game_source(self, name):
        self.game_name = name
        self.game_label.setText(name)

    def update_context_controls(self):
        if self.original.isVisible():
            self.context_audio.move(self.original.width() - 36, 12)
            self.context_audio.show()
            self.game_label.adjustSize()
            self.game_label.move(self.original.width() - self.game_label.width() - 16, self.original.height() - 24)
            self.game_label.setVisible(bool(self.game_name))

    def set_speaking(self, value):
        if self.audio_button:
            self.audio_button.setIcon(card_icon("speaker", value))

    def submit_followup(self):
        if self.followup_button.isEnabled() and (text := self.followup.text().strip()):
            self.followup.clear()
            self.followup_requested.emit(text)

    def submit_manual(self):
        if text := self.home_input.text().strip():
            self.manual_requested.emit(text)

    def dismiss(self):
        self.hide()
        self.dismissed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#44474a"), 1))
        painter.setBrush(QColor("#2c2f31"))
        top = 8 if self.pointer_top else 0
        rect = QRectF(0.5, top + 0.5, self.width() - 1, self.height() - 9)
        painter.drawRoundedRect(rect, 6, 6)
        y = 8 if self.pointer_top else self.height() - 8
        path = QPainterPath(QPointF(self.pointer_x - 8, y))
        path.lineTo(self.pointer_x, 0 if self.pointer_top else self.height())
        path.lineTo(self.pointer_x + 8, y)
        painter.drawPath(path)
        for divider in self.findChildren(CardDivider):
            if divider.isVisible():
                point = divider.mapTo(self, QPoint(0, 0))
                painter.fillRect(point.x(), point.y(), divider.width(), 1, QColor("#c0c2c3"))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 70:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        self.drag_offset = None

    def closeEvent(self, event):
        self.dismiss()
        event.ignore()

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit(False)
