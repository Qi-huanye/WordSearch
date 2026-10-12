from copy import deepcopy

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QKeySequenceEdit, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QScrollArea, QStackedWidget,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget, QFrame)

from . import theme
from .ai import PRESETS
from .native import dark_title_bar, qt_to_physical
from .storage import Profile
from .theme import ComboBox, IconButton, Segmented, Toggle, app_icon, button, glyph_icon, icon_font, line, make_menu

FONT_SIZES = (14, 16, 18)


def label(text, name=""):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    return item


def to_pixmap(image):
    rgba = image.convert("RGBA")
    picture = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4,
                     QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(picture)


def confirm(parent, title, text, action):
    box = QMessageBox(QMessageBox.Icon.NoIcon, title, text, parent=parent)
    accept = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    dark_title_bar(box)
    box.exec()
    return box.clickedButton() is accept


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
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(5, 6, 8, 120))
        if self.start is not None:
            rect = QRect(self.start, self.end or self.start).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(rect, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawRect(rect)
        screen = QApplication.primaryScreen().geometry().translated(-self.geometry().topLeft())
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(14)
        p.setFont(font)
        text = self.title + "  ·  Esc 取消"
        width = QFontMetrics(font).horizontalAdvance(text) + 36
        pill = QRectF(screen.center().x() - width / 2, screen.top() + 28, width, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.RAISED))
        p.drawRoundedRect(pill, 19, 19)
        p.setPen(QColor(theme.TEXT))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

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


class EditDialog(QDialog):
    """Correct OCR output, then look the text up again in the word card."""

    submitted = Signal(str, str)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("修正原文")
        self.setWindowIcon(app_icon())
        self.resize(460, 320)
        self.capture = None
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(10)
        self.term = QLineEdit()
        self.term.setPlaceholderText("单词或短语")
        self.term.setAccessibleName("目标单词")
        self.term.returnPressed.connect(self.submit)
        box.addWidget(self.term)
        self.context = QPlainTextEdit()
        self.context.setPlaceholderText("所在段落")
        self.context.setAccessibleName("当前上下文")
        box.addWidget(self.context, 1)
        actions = QHBoxLayout()
        self.preview_button = button("查看截图", self.show_capture, "Ghost")
        actions.addWidget(self.preview_button)
        actions.addStretch()
        actions.addWidget(button("取消", self.reject))
        actions.addWidget(button("重新查询", self.submit, "Primary"))
        box.addLayout(actions)

    def open_with(self, term, context, capture):
        self.term.setText(term)
        self.context.setPlainText(context)
        self.capture = capture
        self.preview_button.setVisible(capture is not None)
        dark_title_bar(self)
        self.show()
        self.raise_()
        self.activateWindow()
        self.term.setFocus()

    def submit(self):
        term, context = self.term.text().strip(), self.context.toPlainText().strip()
        if term or context:
            self.accept()
            self.submitted.emit(term, context)

    def show_capture(self):
        if self.capture is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("取词截图")
        box = QVBoxLayout(dialog)
        box.setContentsMargins(12, 12, 12, 12)
        picture = QLabel()
        picture.setPixmap(self.capture.scaled(1000, 650, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        box.addWidget(picture)
        dark_title_bar(dialog)
        dialog.exec()


class WordDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 60)

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(option.rect).adjusted(0, 2, 0, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.HOVER if selected else theme.SURFACE))
            painter.drawRoundedRect(rect, 8, 8)
        text = rect.adjusted(14, 9, -44, -9)
        title = QFont()
        title.setFamilies(["Segoe UI", "Microsoft YaHei UI"])
        title.setPixelSize(15)
        title.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         QFontMetrics(title).elidedText(row["term"], Qt.TextElideMode.ElideRight, int(text.width())))
        caption = QFont(title)
        caption.setPixelSize(12)
        caption.setWeight(QFont.Weight.Normal)
        painter.setFont(caption)
        painter.setPen(QColor(theme.MUTED))
        context = " ".join(row["context"].split())
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                         QFontMetrics(caption).elidedText(context, Qt.TextElideMode.ElideRight, int(text.width())))
        if row["favorite"] or hovered:
            painter.setFont(icon_font(14))
            painter.setPen(QColor(theme.ACCENT if row["favorite"] else theme.FAINT))
            painter.drawText(QRectF(rect.right() - 40, rect.top(), 32, rect.height()), Qt.AlignmentFlag.AlignCenter,
                             theme.STAR_FILLED if row["favorite"] else theme.STAR)
        painter.restore()


class WordList(QListWidget):
    favorite_clicked = Signal(int)
    delete_pressed = Signal()
    menu_requested = Signal(QPoint)

    def __init__(self):
        super().__init__()
        self.setItemDelegate(WordDelegate(self))
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if (index.isValid() and event.button() == Qt.MouseButton.LeftButton
                and event.position().x() > self.viewport().width() - 44):
            self.favorite_clicked.emit(index.row())
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            self.setCurrentRow(index.row())
            self.menu_requested.emit(event.globalPos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
        else:
            super().keyPressEvent(event)


class SettingRow(QWidget):
    def __init__(self, title, *controls, stretch=True):
        super().__init__()
        box = QHBoxLayout(self)
        box.setContentsMargins(14, 8, 10, 8)
        box.setSpacing(6)
        caption = QLabel(title)
        caption.setFixedWidth(92)
        box.addWidget(caption)
        if not stretch:
            box.addStretch()
        for index, control in enumerate(controls):
            box.addWidget(control, 1 if stretch and index == 0 else 0)


def group(*rows):
    frame = QFrame()
    frame.setObjectName("Group")
    box = QVBoxLayout(frame)
    box.setContentsMargins(0, 4, 0, 4)
    box.setSpacing(0)
    for index, row in enumerate(rows):
        if index:
            box.addWidget(line())
        box.addWidget(row)
    return frame


class MainWindow(QWidget):
    lookup_requested = Signal(str, object)
    record_opened = Signal(object)
    record_changed = Signal()
    settings_requested = Signal(object)
    test_requested = Signal(object)
    models_requested = Signal(object)

    HISTORY, SETTINGS = range(2)

    def __init__(self, settings, store):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("Window")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.settings = settings
        self.store = store
        self.rows = []
        self.setWindowTitle("WordSearch")
        self.setWindowIcon(app_icon())
        self.resize(420, 620)
        self.setMinimumSize(380, 480)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages)
        self.pages.addWidget(self.build_library())
        self.pages.addWidget(self.build_settings())
        self.toast = label("", "Toast")
        self.toast.setParent(self)
        self.toast.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast.hide)
        self.settings_applied(settings)

    # 生词本

    def build_library(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(16, 16, 16, 12)
        box.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("搜索或查词")
        self.search.setAccessibleName("搜索或查词")
        self.search.addAction(glyph_icon(theme.SEARCH, theme.MUTED, 14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_history)
        self.search.returnPressed.connect(self.submit_search)
        top.addWidget(self.search, 1)
        settings = IconButton(theme.SETTINGS, "设置", size=38, pixels=16)
        settings.clicked.connect(lambda: self.open_page(self.SETTINGS))
        top.addWidget(settings)
        box.addLayout(top)
        filters = QHBoxLayout()
        self.filter = Segmented(["全部", "收藏"])
        self.filter.changed.connect(self.refresh_history)
        filters.addWidget(self.filter)
        filters.addStretch()
        more = IconButton(theme.MORE, "更多", size=32, pixels=15)
        more.clicked.connect(lambda: self.library_menu(more))
        filters.addWidget(more)
        box.addLayout(filters)
        self.history_list = WordList()
        self.history_list.setAccessibleName("生词本")
        self.history_list.itemActivated.connect(self.open_history)
        self.history_list.favorite_clicked.connect(self.favorite_row)
        self.history_list.delete_pressed.connect(self.delete_history)
        self.history_list.menu_requested.connect(self.row_menu)
        box.addWidget(self.history_list, 1)
        self.empty = QWidget()
        empty = QVBoxLayout(self.empty)
        empty.addStretch()
        mark = label(theme.STAR, "Glyph")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(mark)
        self.empty_title = label("", "Title")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.empty_title)
        self.empty_caption = label("", "Muted")
        self.empty_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(self.empty_caption)
        empty.addStretch(2)
        box.addWidget(self.empty, 1)
        return page

    def library_menu(self, anchor):
        menu = make_menu(self)
        menu.addAction("导出 CSV", self.export_history)
        menu.addSeparator()
        menu.addAction("清空生词本", self.clear_history)
        menu.exec(anchor.mapToGlobal(QPoint(anchor.width() - 150, anchor.height() + 4)))

    def row_menu(self, point):
        row = self.current_history()
        if not row:
            return
        menu = make_menu(self)
        menu.addAction("打开", self.open_history)
        menu.addAction("取消收藏" if row["favorite"] else "收藏", self.favorite_history)
        menu.addSeparator()
        menu.addAction("删除", self.delete_history)
        menu.exec(point)

    def submit_search(self):
        if term := self.search.text().strip():
            self.lookup_requested.emit(term, self.search.mapToGlobal(QPoint(self.search.width() // 2, 0)))

    def current_history(self):
        index = self.history_list.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def refresh_history(self, *args):
        selected = self.current_history()
        query = self.search.text().strip()
        self.rows = self.store.words(query, self.filter.index() == 1)
        self.history_list.clear()
        for row in self.rows:
            item = QListWidgetItem(row["term"])
            item.setData(Qt.ItemDataRole.UserRole, dict(row))
            item.setToolTip(row["created"].replace("T", " ")[:16])
            self.history_list.addItem(item)
        index = next((i for i, row in enumerate(self.rows) if selected and row["id"] == selected["id"]), -1)
        self.history_list.setCurrentRow(index)
        if query:
            self.empty_title.setText(f"没有“{query}”")
            self.empty_caption.setText("按回车查词")
        elif self.filter.index() == 1:
            self.empty_title.setText("还没有收藏")
            self.empty_caption.setText("点词卡上的星标收藏单词")
        else:
            self.empty_title.setText("还没有生词")
            self.empty_caption.setText(f"在游戏中指向单词，按 {self.settings.lookup_hotkey}")
        self.history_list.setVisible(bool(self.rows))
        self.empty.setVisible(not self.rows)

    def open_history(self, *args):
        if row := self.current_history():
            self.record_opened.emit(row)

    def favorite_row(self, index):
        self.history_list.setCurrentRow(index)
        self.favorite_history()

    def favorite_history(self):
        if row := self.current_history():
            self.store.favorite(row["id"], not row["favorite"])
            self.refresh_history()
            self.record_changed.emit()

    def delete_history(self):
        if row := self.current_history():
            self.store.delete_word(row["term"])
            self.refresh_history()
            self.record_changed.emit()

    def clear_history(self):
        if confirm(self, "清空生词本", "删除全部单词与收藏？此操作无法撤销。", "清空"):
            self.store.delete()
            self.refresh_history()
            self.record_changed.emit()

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出生词本", "WordSearch.csv", "CSV 文件 (*.csv)")
        if path:
            try:
                self.store.export(path, self.store.words())
                self.set_status("已导出", temporary=True)
            except OSError:
                self.set_status("无法写入这个文件，请关闭占用它的程序后重试。")

    # 设置

    def build_settings(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 12, 16, 4)
        back = IconButton(theme.BACK, "返回", size=34, pixels=14)
        back.clicked.connect(lambda: self.open_page(self.HISTORY))
        head.addWidget(back)
        head.addWidget(label("设置", "Title"))
        head.addStretch()
        outer.addLayout(head)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(16, 0, 16, 16)
        box.setSpacing(0)
        self.draft = deepcopy(self.settings)
        self._editing_index = -1

        self.profile_picker = ComboBox()
        self.profile_picker.setAccessibleName("AI 服务")
        self.profile_picker.currentIndexChanged.connect(self.edit_profile)
        add = IconButton(theme.ADD, "添加服务", size=32)
        add.clicked.connect(lambda: self.preset_menu(add))
        remove = IconButton(theme.DELETE, "删除服务", size=32)
        remove.clicked.connect(self.remove_profile)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://api.example.com/v1")
        self.url.setAccessibleName("API 地址")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setAccessibleName("API Key")
        self.model = ComboBox()
        self.model.setEditable(True)
        self.model.setAccessibleName("模型")
        self.models_button = IconButton(theme.REFRESH, "读取模型列表", size=32, pixels=13)
        self.models_button.clicked.connect(lambda: self.models_requested.emit(self.form_profile()))
        self.auto_translate = Toggle()
        self.auto_translate.setAccessibleName("自动翻译段落")
        self.auto_query = Toggle()
        self.auto_query.setAccessibleName("取词后 AI 解释")
        box.addWidget(label("AI 服务", "Section"))
        box.addWidget(group(SettingRow("服务", self.profile_picker, add, remove),
                            SettingRow("地址", self.url),
                            SettingRow("Key", self.api_key),
                            SettingRow("模型", self.model, self.models_button),
                            SettingRow("自动翻译段落", self.auto_translate, stretch=False),
                            SettingRow("取词后 AI 解释", self.auto_query, stretch=False)))
        test = QHBoxLayout()
        test.setContentsMargins(0, 6, 0, 0)
        test.addStretch()
        self.test_button = button("测试连接", lambda: self.test_requested.emit(self.form_profile()), "Ghost")
        test.addWidget(self.test_button)
        box.addLayout(test)

        self.lookup_key = QKeySequenceEdit()
        self.select_key = QKeySequenceEdit()
        self.region_key = QKeySequenceEdit()
        for editor in (self.lookup_key, self.select_key, self.region_key):
            editor.setMaximumSequenceLength(1)
        self.region_status = label("", "Muted")
        self.region_clear = button("清除", self.clear_region, "Ghost")
        box.addWidget(label("快捷键", "Section"))
        box.addWidget(group(SettingRow("取词", self.lookup_key),
                            SettingRow("框选", self.select_key),
                            SettingRow("对白区域", self.region_key),
                            SettingRow("固定区域", self.region_status, self.region_clear)))

        self.font_size = Segmented(["小", "中", "大"])
        box.addWidget(label("显示", "Section"))
        box.addWidget(group(SettingRow("词卡字号", self.font_size, stretch=False)))
        box.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 10, 16, 14)
        footer.addStretch()
        footer.addWidget(button("保存", self.submit_settings, "Primary"))
        outer.addLayout(footer)
        return page

    def preset_menu(self, anchor):
        menu = make_menu(self)
        for name in PRESETS:
            menu.addAction(name, lambda name=name: self.add_profile(name))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def load_settings_form(self):
        settings = self.settings
        self.draft = deepcopy(settings)
        self.lookup_key.setKeySequence(QKeySequence(settings.lookup_hotkey))
        self.select_key.setKeySequence(QKeySequence(settings.select_hotkey))
        self.region_key.setKeySequence(QKeySequence(settings.region_hotkey))
        self.font_size.set_index(min(range(len(FONT_SIZES)), key=lambda i: abs(FONT_SIZES[i] - settings.font_size)))
        self.auto_translate.setChecked(settings.auto_translate)
        self.auto_query.setChecked(settings.auto_query)
        self.update_region_status()
        self.refresh_profile_picker(next((i for i, p in enumerate(self.draft.profiles) if p.id == settings.profile.id), 0))

    def update_region_status(self):
        self.region_status.setText("已设置" if self.draft.region else "未设置")
        self.region_clear.setVisible(bool(self.draft.region))

    def form_profile(self):
        index = max(0, self.profile_picker.currentIndex())
        original = self.draft.profiles[index]
        return Profile(original.name, self.url.text().strip(), self.model.currentText().strip(),
                       self.api_key.text().strip(), original.id)

    def save_form(self):
        if 0 <= self._editing_index < len(self.draft.profiles):
            original = self.draft.profiles[self._editing_index]
            self.draft.profiles[self._editing_index] = Profile(original.name, self.url.text().strip(),
                                                               self.model.currentText().strip(),
                                                               self.api_key.text().strip(), original.id)

    def edit_profile(self, index):
        if index < 0:
            return
        self.save_form()
        self._editing_index = index
        profile = self.draft.profiles[index]
        self.url.setText(profile.base_url)
        self.api_key.setText(profile.key)
        self.model.clear()
        self.model.addItem(profile.model)

    def refresh_profile_picker(self, selection=0):
        self.profile_picker.blockSignals(True)
        self.profile_picker.clear()
        for profile in self.draft.profiles:
            self.profile_picker.addItem(profile.name)
        self.profile_picker.setCurrentIndex(selection)
        self.profile_picker.blockSignals(False)
        self._editing_index = -1
        self.edit_profile(selection)

    def add_profile(self, preset):
        self.save_form()
        url, model = PRESETS[preset]
        names = {p.name for p in self.draft.profiles}
        name = preset if preset not in names else next(f"{preset} {i}" for i in range(2, 100) if f"{preset} {i}" not in names)
        self.draft.profiles.append(Profile(name, url, model, ""))
        self.refresh_profile_picker(len(self.draft.profiles) - 1)
        self.api_key.setFocus()

    def remove_profile(self):
        if len(self.draft.profiles) == 1:
            self.set_status("至少保留一个服务", temporary=True)
            return
        self.draft.profiles.pop(self.profile_picker.currentIndex())
        self.refresh_profile_picker()

    def clear_region(self):
        self.draft.region = None
        self.update_region_status()

    def submit_settings(self):
        self.save_form()
        for name, editor in [("lookup_hotkey", self.lookup_key), ("select_hotkey", self.select_key),
                             ("region_hotkey", self.region_key)]:
            setattr(self.draft, name, editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText))
        self.draft.font_size = FONT_SIZES[self.font_size.index()]
        self.draft.auto_query = self.auto_query.isChecked()
        self.draft.auto_translate = self.auto_translate.isChecked()
        self.draft.selected_id = self.draft.profiles[self.profile_picker.currentIndex()].id
        self.settings_requested.emit(deepcopy(self.draft))

    def settings_applied(self, settings):
        self.settings = settings
        self.load_settings_form()

    # 窗口

    def open_page(self, index):
        if index == self.SETTINGS and (self.pages.currentIndex() != self.SETTINGS or not self.isVisible()):
            self.load_settings_form()
        self.pages.setCurrentIndex(index)
        self.refresh_history()
        dark_title_bar(self)
        self.show()
        self.raise_()
        self.activateWindow()
        if index == self.HISTORY:
            self.search.setFocus()

    def set_status(self, text, temporary=False):
        self.toast_timer.stop()
        if not text:
            self.toast.hide()
            return
        self.toast.setText(text)
        self.toast.setWordWrap(False)
        self.toast.adjustSize()
        if self.toast.width() > self.width() - 40:
            self.toast.setWordWrap(True)
            self.toast.setFixedWidth(self.width() - 40)
            self.toast.adjustSize()
        else:
            self.toast.setMinimumWidth(0)
            self.toast.setMaximumWidth(16777215)
        self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 64)
        self.toast.raise_()
        self.toast.show()
        self.toast_timer.start(2500 if temporary else 6000)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.hide()
        event.ignore()
