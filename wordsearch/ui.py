from copy import deepcopy

from PySide6.QtCore import QPoint, QRect, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QKeySequenceEdit, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QFileDialog, QDialog, QScrollArea, QSplitter)

from .ai import PRESETS
from .native import qt_to_physical
from .storage import Profile

STYLE = """
QWidget { background:#252525; color:#eeeeee; font-family:'Microsoft YaHei UI'; font-size:13px; }
QMainWindow { background:#252525; }
QLabel#Brand { color:#eeeeee; font-family:'Segoe UI'; font-size:20px; font-weight:600; }
QLabel#Subtle { color:#b1b1b1; font-size:12px; }
QLabel#Status { color:#b8cbbb; font-size:12px; padding:4px 0; }
QTabWidget::pane { border:0; }
QTabBar::tab { color:#aaaaaa; background:#252525; padding:10px 17px; border-bottom:2px solid #414141; }
QTabBar::tab:selected { color:#eeeeee; border-bottom:2px solid #a8c2b0; }
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QKeySequenceEdit, QListWidget {
 background:#303030; border:1px solid #494949; border-radius:5px; padding:7px; selection-background-color:#4f6657; }
QLineEdit:focus, QPlainTextEdit:focus { border:1px solid #a8c2b0; }
QPushButton { background:#383838; border:1px solid #4a4a4a; border-radius:5px; padding:8px 11px; }
QPushButton:hover { background:#464646; }
QPushButton:disabled { color:#777777; background:#303030; }
QPushButton#Primary { background:#a8c2b0; color:#202620; border:0; font-weight:600; }
QPushButton#Primary:hover { background:#c0d5c7; }
QListWidget::item { padding:9px; border-bottom:1px solid #414141; }
QListWidget::item:selected { background:#455047; }
QCheckBox { spacing:8px; }
QCheckBox::indicator { width:16px; height:16px; }
QToolTip { background:#354251; color:#edf1f5; border:1px solid #718198; }
QScrollBar:vertical { background:#303030; width:9px; }
QScrollBar::handle:vertical { background:#626262; border-radius:4px; min-height:25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


def button(text, callback=None, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    if callback:
        b.clicked.connect(callback)
    return b


def label(text, subtle=False):
    item = QLabel(text)
    item.setWordWrap(True)
    if subtle:
        item.setObjectName("Subtle")
    return item


def app_icon():
    image = QPixmap(64, 64)
    image.fill(QColor("#171b22"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#efa868"), 5))
    painter.drawRoundedRect(12, 10, 39, 43, 5, 5)
    painter.drawLine(31, 12, 31, 51)
    painter.end()
    return QIcon(image)


class SelectionOverlay(QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, title):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("WordSearch · " + title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.title = title
        self.start = None
        self.end = None
        self.finished = False
        geometry = QRect()
        for screen in QApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(5, 9, 15, 110))
        if self.start is not None:
            rect = QRect(self.start, self.end or self.start).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(rect, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor("#efa868"), 2))
            p.drawRect(rect)
        p.fillRect(30, 30, 540, 58, QColor("#202631"))
        p.setPen(QColor("#efa868"))
        p.setFont(QFont("Microsoft YaHei UI", 14))
        p.drawText(QRect(45, 40, 510, 38), Qt.AlignmentFlag.AlignVCenter,
                   self.title + " · 拖动框选，Esc 取消")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.end = event.position().toPoint()
        a, b = self.mapToGlobal(self.start), self.mapToGlobal(self.end)
        sa = QApplication.screenAt(a)
        sb = QApplication.screenAt(b)
        if not sa or not sb:
            self.close()
            return
        ax, ay = qt_to_physical(a, sa)
        bx, by = qt_to_physical(b, sb)
        if abs(bx - ax) < 8 or abs(by - ay) < 8:
            self.start = None
            self.end = None
            self.update()
            return
        region = [min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)]
        self.finished = True
        self.hide()
        self.selected.emit(region)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        if not self.finished:
            self.cancelled.emit()
        event.accept()


class MainWindow(QMainWindow):
    lookup_requested = Signal()
    selection_requested = Signal(bool)
    query_requested = Signal(str)
    followup_requested = Signal(str)
    stop_requested = Signal()
    settings_requested = Signal(object)
    test_requested = Signal(object)
    models_requested = Signal(object)
    profile_changed = Signal(str)
    history_opened = Signal()
    dictionary_requested = Signal()
    history_loaded = Signal(object)
    record_changed = Signal()

    def __init__(self, settings, store):
        super().__init__()
        self.settings = settings
        self.store = store
        self.record_id = None
        self.record_favorite = False
        self.busy = False
        self.rows = []
        self.capture_pixmap = None
        self.setWindowTitle("WordSearch · 游戏查词")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(600, 670)
        self.setMinimumSize(520, 560)
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 12)
        outer.setSpacing(9)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.status = label("")
        self.status.setObjectName("Status")
        self.status.hide()
        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(lambda: self.set_status(""))
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self.build_lookup()
        self.build_history()
        self.build_settings()
        self.reload_profiles()
        self.apply_font()
        self.refresh_history()
        self.tabs.currentChanged.connect(lambda i: self.refresh_history() if i == 1 else None)

    def build_lookup(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 4)
        actions = QHBoxLayout()
        actions.addWidget(button("鼠标取词", self.lookup_requested.emit))
        actions.addWidget(button("框选文字", lambda: self.selection_requested.emit(False)))
        actions.addWidget(button("对白区域", lambda: self.selection_requested.emit(True)))
        box.addLayout(actions)
        self.term = QLineEdit()
        self.term.setPlaceholderText("单词或短语")
        self.term.setAccessibleName("目标单词")
        self.term.returnPressed.connect(self.dictionary_requested.emit)
        box.addWidget(self.term)
        context_heading = QHBoxLayout()
        context_heading.addWidget(label("上下文"), 1)
        self.preview_button = button("原画面", self.show_capture)
        self.preview_button.setEnabled(False)
        context_heading.addWidget(self.preview_button)
        box.addLayout(context_heading)
        self.context = QPlainTextEdit()
        self.context.setPlaceholderText("原文")
        self.context.setAccessibleName("当前上下文")
        self.context.setFixedHeight(128)
        box.addWidget(self.context)
        controls = QHBoxLayout()
        controls.addWidget(button("本地词典", self.dictionary_requested.emit, True))
        self.query_button = button("AI 语境", lambda: self.query_requested.emit("lookup"))
        self.translate_button = button("整句", lambda: self.query_requested.emit("translate"))
        self.stop_button = button("停止", self.stop_requested.emit)
        self.stop_button.hide()
        self.favorite_button = button("☆", self.toggle_favorite)
        self.favorite_button.setEnabled(False)
        for control in (self.query_button, self.translate_button, self.stop_button, self.favorite_button):
            controls.addWidget(control)
        box.addLayout(controls)
        self.active_profile = QComboBox()
        self.active_profile.setAccessibleName("当前 AI 服务")
        self.active_profile.currentIndexChanged.connect(self.choose_profile)
        box.addWidget(self.active_profile)
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        self.answer.setOpenLinks(False)
        self.answer.setAccessibleName("释义")
        box.addWidget(self.answer, 1)
        self.followup_row = QWidget()
        follow = QHBoxLayout(self.followup_row)
        follow.setContentsMargins(0, 0, 0, 0)
        self.followup = QLineEdit()
        self.followup.setPlaceholderText("追问")
        self.followup.setAccessibleName("追问内容")
        self.followup.returnPressed.connect(self.submit_followup)
        self.followup_button = button("追问", self.submit_followup)
        self.followup_button.setEnabled(False)
        follow.addWidget(self.followup, 1)
        follow.addWidget(self.followup_button)
        box.addWidget(self.followup_row)
        self.followup_row.hide()
        self.tabs.addTab(page, "查词")

    def build_history(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 4)
        filters = QHBoxLayout()
        self.history_query = QLineEdit()
        self.history_query.setPlaceholderText("搜索单词或原句")
        self.history_query.textChanged.connect(self.refresh_history)
        self.only_favorites = QCheckBox("仅收藏")
        self.only_favorites.toggled.connect(self.refresh_history)
        filters.addWidget(self.history_query, 1)
        filters.addWidget(self.only_favorites)
        box.addLayout(filters)
        self.history_list = QListWidget()
        self.history_list.setAccessibleName("查询历史")
        self.history_list.currentRowChanged.connect(self.show_history)
        self.history_detail = QTextBrowser()
        self.history_detail.setOpenExternalLinks(False)
        self.history_detail.setOpenLinks(False)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.history_list)
        split.addWidget(self.history_detail)
        split.setSizes([185, 365])
        box.addWidget(split, 1)
        actions = QHBoxLayout()
        for text, handler in [("打开", self.open_history), ("收藏 / 取消", self.favorite_history),
                              ("删除", self.delete_history), ("导出", self.export_history)]:
            actions.addWidget(button(text, handler))
        box.addLayout(actions)
        clear = button("清空", self.clear_history)
        actions.addWidget(clear)
        self.tabs.addTab(page, "生词本")

    def build_settings(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 4)
        self.draft = deepcopy(self.settings)
        self.profile_picker = QComboBox()
        self.profile_picker.currentIndexChanged.connect(self.edit_profile)
        profiles = QHBoxLayout()
        profiles.addWidget(self.profile_picker, 1)
        profiles.addWidget(button("新增", self.add_profile))
        profiles.addWidget(button("移除", self.remove_profile))
        box.addLayout(profiles)
        form = QFormLayout()
        form.setSpacing(9)
        self.preset = QComboBox()
        self.preset.addItems(["选择预设…"] + list(PRESETS))
        self.preset.activated.connect(self.apply_preset)
        self.profile_name = QLineEdit()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://api.example.com/v1")
        self.url.setToolTip("支持 Chat Completions 接口，也可填写完整接口地址。")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("API Key")
        self.api_key.setToolTip("由 Windows 当前用户加密保存。")
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setAccessibleName("模型名")
        for title, widget in [("预设", self.preset), ("名称", self.profile_name),
                              ("API 地址", self.url), ("API Key", self.api_key), ("模型", self.model)]:
            widget.setAccessibleName(title)
            form.addRow(title, widget)
        box.addLayout(form)
        api_buttons = QHBoxLayout()
        self.models_button = button("读取模型", lambda: self.models_requested.emit(self.form_profile()))
        self.test_button = button("测试连接", lambda: self.test_requested.emit(self.form_profile()))
        self.test_button.setToolTip("发送少量文字，使用所选服务的额度。")
        api_buttons.addWidget(self.models_button)
        api_buttons.addWidget(self.test_button)
        box.addLayout(api_buttons)
        self.lookup_key = QKeySequenceEdit(QKeySequence(self.settings.lookup_hotkey))
        self.select_key = QKeySequenceEdit(QKeySequence(self.settings.select_hotkey))
        self.region_key = QKeySequenceEdit(QKeySequence(self.settings.region_hotkey))
        for editor in (self.lookup_key, self.select_key, self.region_key):
            editor.setMaximumSequenceLength(1)
        general = QFormLayout()
        general.setSpacing(8)
        general.addRow("鼠标取词", self.lookup_key)
        general.addRow("框选文字", self.select_key)
        general.addRow("对白区域", self.region_key)
        self.font_size = QSpinBox()
        self.font_size.setRange(12, 24)
        self.font_size.setValue(self.settings.font_size)
        general.addRow("释义字号", self.font_size)
        box.addLayout(general)
        self.hover_lookup = QCheckBox("鼠标悬停取词")
        self.hover_lookup.setChecked(self.settings.hover_lookup)
        box.addWidget(self.hover_lookup)
        self.auto_translate = QCheckBox("自动语境翻译")
        self.auto_translate.setToolTip("使用所选 API；同一段对白换词时复用译文。")
        self.auto_translate.setChecked(self.settings.auto_translate)
        box.addWidget(self.auto_translate)
        self.auto_query = QCheckBox("快捷键取词后自动查询 AI")
        self.auto_query.setToolTip("快捷键取词后同时请求 AI 单词释义。")
        self.auto_query.setChecked(self.settings.auto_query)
        box.addWidget(self.auto_query)
        region = QHBoxLayout()
        self.region_status = label("")
        region.addWidget(self.region_status, 1)
        region.addWidget(button("清除区域", self.clear_region))
        box.addLayout(region)
        box.addWidget(button("保存", self.submit_settings, True))
        box.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "设置")
        self._editing_index = -1
        self.refresh_profile_picker()

    def apply_font(self):
        for browser in (self.answer, self.history_detail):
            font = QFont("Microsoft YaHei UI")
            font.setPixelSize(self.settings.font_size)
            browser.document().setDefaultFont(font)
        self.region_status.setText("对白区域：" + ("已设置" if self.settings.region else "未设置"))

    def choose_profile(self, index):
        if index >= 0:
            self.profile_changed.emit(self.active_profile.itemData(index))

    def reload_profiles(self):
        self.active_profile.blockSignals(True)
        self.active_profile.clear()
        for p in self.settings.profiles:
            self.active_profile.addItem(f"{p.name}  ·  {p.model or '尚未选择模型'}", p.id)
        self.active_profile.setCurrentIndex(max(0, self.active_profile.findData(self.settings.profile.id)))
        self.active_profile.blockSignals(False)

    def form_profile(self):
        index = self.profile_picker.currentIndex()
        original = self.draft.profiles[max(0, index)]
        return Profile(self.profile_name.text().strip() or "自定义服务", self.url.text().strip(),
                       self.model.currentText().strip(), self.api_key.text().strip(), original.id)

    def save_form(self):
        if 0 <= self._editing_index < len(self.draft.profiles):
            self.draft.profiles[self._editing_index] = self.form_profile_for(self._editing_index)

    def form_profile_for(self, index):
        return Profile(self.profile_name.text().strip() or "自定义服务", self.url.text().strip(),
                       self.model.currentText().strip(), self.api_key.text().strip(), self.draft.profiles[index].id)

    def edit_profile(self, index):
        if not hasattr(self, "_editing_index") or index < 0:
            return
        self.save_form()
        self._editing_index = index
        profile = self.draft.profiles[index]
        self.profile_name.setText(profile.name)
        self.url.setText(profile.base_url)
        self.api_key.setText(profile.key)
        self.model.clear()
        self.model.addItem(profile.model)
        self.preset.setCurrentIndex(0)

    def refresh_profile_picker(self, selection=0):
        self.profile_picker.blockSignals(True)
        self.profile_picker.clear()
        for profile in self.draft.profiles:
            self.profile_picker.addItem(profile.name)
        self.profile_picker.setCurrentIndex(selection)
        self.profile_picker.blockSignals(False)
        self._editing_index = -1
        self.edit_profile(selection)

    def add_profile(self):
        self.save_form()
        self.draft.profiles.append(Profile("新服务", "", ""))
        self.refresh_profile_picker(len(self.draft.profiles) - 1)

    def remove_profile(self):
        if len(self.draft.profiles) == 1:
            self.set_status("至少保留一个服务配置。")
            return
        self.draft.profiles.pop(self.profile_picker.currentIndex())
        self.refresh_profile_picker()

    def apply_preset(self, index):
        name = self.preset.itemText(index)
        if name in PRESETS:
            url, model = PRESETS[name]
            self.profile_name.setText(name)
            self.url.setText(url)
            self.model.setCurrentText(model)
            self.api_key.clear()

    def clear_region(self):
        self.draft.region = None
        self.region_status.setText("对白区域：待清除")

    def submit_settings(self):
        self.save_form()
        self.draft.lookup_hotkey = self.lookup_key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.draft.select_hotkey = self.select_key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.draft.region_hotkey = self.region_key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.draft.font_size = self.font_size.value()
        self.draft.auto_query = self.auto_query.isChecked()
        self.draft.auto_translate = self.auto_translate.isChecked()
        self.draft.hover_lookup = self.hover_lookup.isChecked()
        self.draft.selected_id = self.draft.profiles[self.profile_picker.currentIndex()].id
        self.settings_requested.emit(deepcopy(self.draft))

    def settings_applied(self, settings):
        self.settings = settings
        self.draft = deepcopy(settings)
        self.reload_profiles()
        self.refresh_profile_picker(max(0, next((i for i, p in enumerate(settings.profiles) if p.id == settings.profile.id), 0)))
        self.apply_font()

    def set_status(self, text, temporary=False):
        self.status_timer.stop()
        self.status.setText(text)
        self.status.setVisible(bool(text))
        if text and temporary:
            self.status_timer.start(5000)

    def set_capture(self, image):
        rgba = image.convert("RGBA")
        picture = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4,
                         QImage.Format.Format_RGBA8888).copy()
        self.capture_pixmap = QPixmap.fromImage(picture)
        self.preview_button.setEnabled(True)

    def show_capture(self):
        if self.capture_pixmap is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("原画面")
        box = QVBoxLayout(dialog)
        picture = QLabel()
        picture.setPixmap(self.capture_pixmap.scaled(1000, 650, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
        box.addWidget(picture)
        box.addWidget(button("关闭", dialog.accept))
        dialog.adjustSize()
        available = self.screen().availableGeometry()
        dialog.move(max(available.left(), min(self.frameGeometry().center().x() - dialog.width() // 2,
                    available.right() - dialog.width())),
                    max(available.top(), min(self.frameGeometry().center().y() - dialog.height() // 2,
                    available.bottom() - dialog.height())))
        dialog.exec()

    def show_lookup(self):
        self.tabs.setCurrentIndex(0)
        self.show()
        self.raise_()

    def set_busy(self, busy):
        self.busy = busy
        self.query_button.setEnabled(not busy)
        self.translate_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.stop_button.setVisible(busy)
        self.followup_button.setEnabled(not busy and bool(self.record_id))

    def submit_followup(self):
        text = self.followup.text().strip()
        if text and self.followup_button.isEnabled():
            self.followup_requested.emit(text)

    def toggle_favorite(self):
        if self.record_id:
            self.record_favorite = not self.record_favorite
            self.store.favorite(self.record_id, self.record_favorite)
            self.favorite_button.setText("★ 已收藏" if self.record_favorite else "☆ 收藏")
            self.record_changed.emit()

    def current_history(self):
        index = self.history_list.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def refresh_history(self, *args):
        self.rows = self.store.history(self.history_query.text(), self.only_favorites.isChecked())
        self.history_list.clear()
        for row in self.rows:
            self.history_list.addItem(f"{'★ ' if row['favorite'] else ''}{row['term'] or '整句翻译'}")
        if not self.rows:
            self.history_detail.clear()
        else:
            self.history_list.setCurrentRow(0)

    def show_history(self, index):
        row = self.current_history()
        if row:
            answer = row["answer"].removesuffix("\n\n---\n\nECDICT · 本地英汉双解词典")
            self.history_detail.setMarkdown(answer + "\n\n---\n\n" + row["context"])

    def open_history(self):
        row = self.current_history()
        if row:
            self.history_opened.emit()
            self.term.setText(row["term"])
            self.context.setPlainText(row["context"])
            self.answer.setMarkdown(row["answer"].removesuffix("\n\n---\n\nECDICT · 本地英汉双解词典"))
            self.record_id = row["id"]
            self.record_favorite = bool(row["favorite"])
            self.favorite_button.setEnabled(True)
            self.favorite_button.setText("★ 已收藏" if row["favorite"] else "☆ 收藏")
            self.followup_button.setEnabled(False)
            self.tabs.setCurrentIndex(0)
            self.set_status("")
            self.history_loaded.emit(row)

    def favorite_history(self):
        row = self.current_history()
        if row:
            self.store.favorite(row["id"], not row["favorite"])
            self.refresh_history()
            self.record_changed.emit()

    def delete_history(self):
        row = self.current_history()
        if row and QMessageBox.question(self, "删除记录", "删除这条查询记录？") == QMessageBox.StandardButton.Yes:
            self.store.delete(row["id"])
            self.refresh_history()
            self.record_changed.emit()

    def clear_history(self):
        if QMessageBox.question(self, "清空历史", "删除全部查询记录与收藏？此操作无法撤销。") == QMessageBox.StandardButton.Yes:
            self.store.delete()
            self.refresh_history()
            self.record_id = None
            self.favorite_button.setEnabled(False)
            self.record_changed.emit()

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出生词本", "WordSearch.csv", "CSV 文件 (*.csv)")
        if path:
            try:
                self.store.export(path, self.store.history(self.history_query.text(), self.only_favorites.isChecked()))
                self.set_status("已导出 CSV。")
            except OSError:
                self.set_status("无法写入此文件，请关闭 Excel 中的同名文件或更换保存位置。")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

