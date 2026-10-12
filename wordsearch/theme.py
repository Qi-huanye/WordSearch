"""Shared look for every WordSearch window: palette, stylesheet and small painted controls."""

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QMenu, QPushButton

BG = "#1d1e22"
SURFACE = "#26272c"
RAISED = "#2f3136"
HOVER = "#383a40"
BORDER = "#34363c"
TEXT = "#ececef"
MUTED = "#8e919a"
FAINT = "#5c5f67"
ACCENT = "#f0a35e"
ON_ACCENT = "#1d1e22"
MARK_BG = "#5b4029"
MARK_FG = "#ffdcb8"
STATUS = "#e6b58a"

# Segoe Fluent Icons (Windows 11) and Segoe MDL2 Assets (Windows 10) share these code points.
SEARCH, SETTINGS, BACK, MORE = "\ue721", "\ue713", "\ue72b", "\ue712"
VOLUME, STAR, STAR_FILLED = "\ue767", "\ue734", "\ue735"
ADD, DELETE, REFRESH, STOP, SEND = "\ue710", "\ue74d", "\ue72c", "\ue71a", "\ue724"
CHEVRON_DOWN, CHEVRON_UP = "\ue70d", "\ue70e"

STYLE = f"""
* {{ font-family:'Segoe UI','Microsoft YaHei UI'; }}
QWidget {{ color:{TEXT}; font-size:13px; }}
QWidget#Window, QDialog, QMessageBox {{ background:{BG}; }}
QLabel#Title {{ font-size:16px; font-weight:600; }}
QLabel#Section {{ color:{MUTED}; font-size:12px; font-weight:600; padding:16px 4px 6px 4px; }}
QLabel#Muted, QLabel#Caption {{ color:{MUTED}; }}
QLabel#Caption {{ font-size:12px; }}
QLabel#Status {{ color:{STATUS}; font-size:12px; }}
QLabel#Toast {{ background:{HOVER}; border:1px solid {BORDER}; border-radius:8px; padding:8px 14px; }}
QFrame#Group {{ background:{SURFACE}; border-radius:10px; }}
QFrame#Line {{ background:{BORDER}; border:0; min-height:1px; max-height:1px; }}
QLineEdit, QPlainTextEdit, QComboBox {{ background:{BG}; border:1px solid {BORDER}; border-radius:6px;
  padding:6px 10px; selection-background-color:{MARK_BG}; }}
QLineEdit#Search {{ background:{SURFACE}; border-color:{SURFACE}; border-radius:8px; padding:8px 10px; font-size:14px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QLineEdit#Search:focus {{ border-color:{ACCENT}; }}
QComboBox {{ padding-right:28px; }}
QComboBox:editable {{ padding-left:5px; }}
QComboBox QLineEdit {{ background:transparent; border:0; padding:0; }}
QComboBox::drop-down {{ border:0; width:28px; }}
QComboBox::down-arrow {{ image:none; }}
QComboBox QAbstractItemView {{ background:{RAISED}; border:1px solid {BORDER}; outline:0; padding:4px;
  selection-background-color:{HOVER}; }}
QPushButton {{ background:{RAISED}; border:1px solid {BORDER}; border-radius:6px; padding:6px 14px; }}
QPushButton:hover {{ background:{HOVER}; }}
QPushButton:disabled {{ color:{FAINT}; }}
QPushButton#Primary {{ background:{ACCENT}; border:0; color:{ON_ACCENT}; font-weight:600; padding:7px 20px; }}
QPushButton#Primary:hover {{ background:#f5b67b; }}
QPushButton#Ghost, QPushButton#Chip {{ background:transparent; border:0; color:{MUTED}; padding:4px 8px; }}
QPushButton#Ghost:hover, QPushButton#Chip:hover {{ background:{RAISED}; color:{TEXT}; }}
QPushButton#Chip {{ font-size:12px; padding:3px 7px; border-radius:5px; }}
QPushButton#Link {{ background:transparent; border:0; color:{MUTED}; padding:2px 0; text-align:left; }}
QPushButton#Link:hover {{ color:{TEXT}; }}
QLabel#Glyph {{ font-family:'Segoe Fluent Icons','Segoe MDL2 Assets'; font-size:34px; color:{FAINT}; }}
QFrame#Segmented {{ background:{SURFACE}; border-radius:8px; }}
QPushButton#Segment {{ background:transparent; border:0; border-radius:6px; padding:4px 14px; color:{MUTED}; }}
QPushButton#Segment:hover {{ color:{TEXT}; }}
QPushButton#Segment:checked {{ background:{HOVER}; color:{TEXT}; }}
QListView {{ background:transparent; border:0; outline:0; }}
QTextBrowser {{ background:transparent; border:0; }}
QScrollArea {{ background:transparent; border:0; }}
QScrollArea > QWidget > QWidget {{ background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#3a3c42; border-radius:3px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:#4a4c53; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QToolTip {{ background:{RAISED}; color:{TEXT}; border:1px solid {BORDER}; padding:4px 8px; }}
QMenu {{ background:{RAISED}; border:1px solid {BORDER}; border-radius:8px; padding:4px; }}
QMenu::item {{ padding:7px 28px 7px 12px; border-radius:5px; }}
QMenu::item:selected {{ background:{HOVER}; }}
QMenu::item:disabled {{ color:{FAINT}; }}
QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 6px; }}
QLabel#Word {{ font-size:22px; font-weight:600; }}
QLabel#Phonetic {{ color:{MUTED}; font-size:13px; }}
QLabel#Pos {{ color:{MUTED}; background:{RAISED}; border-radius:4px; padding:1px 6px; font-size:11px; }}
QTextBrowser#Quote {{ border-left:2px solid #45474e; padding-left:10px; }}
"""


def icon_font(pixels):
    font = QFont()
    font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
    font.setPixelSize(pixels)
    return font


def glyph_icon(glyph, color=MUTED, pixels=14):
    picture = QPixmap(pixels * 2, pixels * 2)
    picture.setDevicePixelRatio(2)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(icon_font(pixels))
    painter.setPen(QColor(color))
    painter.drawText(QRectF(0, 0, pixels, pixels), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return QIcon(picture)


def app_icon():
    picture = QPixmap(64, 64)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ACCENT))
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setPen(QPen(QColor(ON_ACCENT), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(15, 14, 25, 25)
    painter.drawLine(37, 37, 48, 48)
    painter.end()
    return QIcon(picture)


def make_menu(parent=None):
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return menu


def button(text, callback=None, kind=""):
    item = QPushButton(text)
    if kind:
        item.setObjectName(kind)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        item.clicked.connect(callback)
    return item


def line():
    item = QFrame()
    item.setObjectName("Line")
    return item


class IconButton(QAbstractButton):
    """A flat glyph button; checkable buttons show their checked glyph in the accent colour."""

    def __init__(self, glyph, tip="", parent=None, size=28, pixels=14, checked_glyph=None):
        super().__init__(parent)
        self.glyph, self.checked_glyph, self.pixels = glyph, checked_glyph or glyph, pixels
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.active = False

    def sizeHint(self):
        return self.size()

    def set_active(self, value):
        self.active = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.isEnabled() and self.underMouse()
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HOVER if self.isDown() else RAISED))
            painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        checked = self.isCheckable() and self.isChecked()
        color = FAINT if not self.isEnabled() else ACCENT if checked or self.active else TEXT if hovered else MUTED
        painter.setFont(icon_font(self.pixels))
        painter.setPen(QColor(color))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.checked_glyph if checked else self.glyph)


class Toggle(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(38, 22)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT if self.isChecked() else "#43464d"))
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        painter.drawPath(track)
        painter.setBrush(QColor("#ffffff" if self.isChecked() else "#c9cbd1"))
        painter.drawEllipse(QRectF(19 if self.isChecked() else 3, 3, 16, 16))


class Segmented(QFrame):
    changed = Signal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        for index, text in enumerate(labels):
            item = button(text, kind="Segment")
            item.setCheckable(True)
            self.group.addButton(item, index)
            layout.addWidget(item)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def index(self):
        return self.group.checkedId()

    def set_index(self, index):
        self.group.button(index).setChecked(True)

    def button(self, index):
        return self.group.button(index)


class ComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setFont(icon_font(10))
        painter.setPen(QColor(MUTED))
        painter.drawText(QRect(self.width() - 28, 0, 22, self.height()), Qt.AlignmentFlag.AlignCenter, CHEVRON_DOWN)
