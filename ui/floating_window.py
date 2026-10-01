"""A non-modal floating capture surface sharing the main window's controller."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QMenu, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from card import CardError, parse_card
from controller import State
from ui.theme import FLOAT_CONTROL_SIZE, FLOAT_EDITOR_SIZE, FLOAT_META_SIZE, FLOAT_TITLE_SIZE


FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
STYLE = """
QFrame#floatSurface {
    background: #FAFCFF; border: 1px solid #D9E2EE; border-radius: 14px;
}
QWidget { color: #253248; }
QLabel { background: transparent; border: none; }
QLabel#floatMuted { color: #748095; }
QTextEdit { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 8px; padding: 8px; }
QTextEdit:focus { border: 1px solid #6D9FF3; }
QComboBox { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 7px; padding: 5px 28px 5px 8px; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("__FLOAT_CHEVRON__"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #FFFFFF; color: #253248; border: 1px solid #DEE5EF;
    selection-background-color: #EAF0FA; selection-color: #253248; padding: 4px; }
QPushButton { background: transparent; border: none; border-radius: 7px; padding: 0px 10px; min-height: 0px; }
QPushButton#floatAdd, QPushButton#floatIgnore { min-height: 36px; max-height: 36px; }
QPushButton:hover { background: #EAF0FA; }
QPushButton#floatRetry { color: #367DEB; padding: 0px 6px; }
QPushButton:disabled { color: #8191A8; }
QPushButton#floatAdd { color: white; background: #367DEB; }
QPushButton#floatAdd:hover { background: #246BDC; }
QPushButton#floatAdd:disabled { color: #8191A8; background: #E3EAF4; }
QCheckBox#floatAutoAdd { spacing: 5px; background: transparent; }
QCheckBox#floatAutoAdd::indicator { width: 28px; height: 16px; }
QCheckBox#floatAutoAdd::indicator:unchecked { image: url("__AUTO_OFF__"); }
QCheckBox#floatAutoAdd::indicator:checked { image: url("__AUTO_ON__"); }
QCheckBox#floatAutoAdd:focus { color: #367DEB; }
QFrame#floatToast { background: #F2FBF6; border: 1px solid #D1E8DA; border-radius: 12px; }
"""
STYLE = STYLE.replace("__FLOAT_CHEVRON__", (Path(__file__).resolve().parent / "icons" / "chevron-down-muted.svg").as_posix())
STYLE = STYLE.replace("__AUTO_OFF__", (Path(__file__).resolve().parent / "icons" / "switch-off.svg").as_posix())
STYLE = STYLE.replace("__AUTO_ON__", (Path(__file__).resolve().parent / "icons" / "switch-on.svg").as_posix())


def pixel_font(source: QFont, size: int) -> QFont:
    """Keep the existing font family/face while using one logical-pixel scale."""
    font = QFont(source)
    font.setPixelSize(size)
    return font


def prepare_window(widget: QWidget) -> None:
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def shadow(panel: QWidget) -> None:
    effect = QGraphicsDropShadowEffect(panel)
    effect.setBlurRadius(22)
    effect.setOffset(0, 4)
    effect.setColor(QColor(35, 57, 89, 35))
    panel.setGraphicsEffect(effect)


def clamp_position(point: QPoint, widget: QWidget, area) -> QPoint:
    return QPoint(
        max(area.left(), min(point.x(), area.right() - widget.width() + 1)),
        max(area.top(), min(point.y(), area.bottom() - widget.height() + 1)),
    )


class FloatingOrb(QWidget):
    clicked = Signal()
    moved = Signal()
    menu_requested = Signal(QPoint)

    def __init__(self):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        prepare_window(self)
        self.setWindowTitle("Anki Quick Add · 悬浮球")
        self.setFixedSize(66, 66)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击预览词卡 · 拖动移动 · 右键打开菜单")
        self.setAccessibleName("Anki 悬浮制卡")
        self.count = 0
        self.automatic = False
        self.attention = False
        self._press = None
        self._origin = QPoint()
        self._dragged = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(self.width() / 72, self.height() / 72)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(34, 77, 138, 22))
        painter.drawEllipse(QRectF(8, 10, 56, 56))
        gradient = QLinearGradient(12, 8, 56, 62)
        gradient.setColorAt(0, QColor("#74B4FF"))
        gradient.setColorAt(1, QColor("#246BDD"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#D8EBFF"), 1.5))
        painter.drawEllipse(QRectF(10, 8, 52, 52))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#FFFFFF"), 2.3))
        painter.drawRoundedRect(QRectF(25, 27, 21, 17), 3, 3)
        painter.drawRoundedRect(QRectF(29, 22, 19, 17), 3, 3)
        if self.count:
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor("#245DC0"))
            painter.drawEllipse(QRectF(47, 1, 23, 23))
            font = QFont(self.font())
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(QRectF(47, 1, 23, 23), Qt.AlignmentFlag.AlignCenter, str(self.count) if self.count < 100 else "99+")

        if self.automatic:
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor("#D35D45" if self.attention else "#36A86B"))
            painter.drawEllipse(QRectF(48, 47, 12, 12))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        delta = event.globalPosition().toPoint() - self._press
        if delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragged = True
        if self._dragged:
            screen = QApplication.screenAt(event.globalPosition().toPoint()) or self.screen()
            self.move(clamp_position(self._origin + delta, self, screen.availableGeometry()))
            self.moved.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if self._dragged:
                self.moved.emit()
            else:
                self.clicked.emit()
            self._press = None


class FloatingStatusLabel(QLabel):
    """Keep status in one row; expose the complete message on hover."""

    def __init__(self):
        super().__init__()
        self._message = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28)

    def set_status(self, message, display=None):
        self._message = message if display is None else display
        self.setToolTip(message)
        self._update_text()

    def _update_text(self):
        self.setText(self.fontMetrics().elidedText(
            self._message, Qt.TextElideMode.ElideRight, self.contentsRect().width()
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()


class FloatingPanel(QWidget):
    def __init__(self, owner):
        super().__init__(None, FLAGS)
        prepare_window(self)
        self.setWindowTitle("Anki Quick Add · 词卡预览")
        self.setStyleSheet(STYLE)
        self.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_CONTROL_SIZE))
        self.setFixedSize(360, 510)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 16)
        surface = QFrame(self)
        surface.setObjectName("floatSurface")
        shadow(surface)
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("快速制卡")
        title.setObjectName("floatTitle")
        title.setFont(pixel_font(owner.main.fonts["cn_medium"], FLOAT_TITLE_SIZE))
        header.addWidget(title)
        self.auto_add_switch = QCheckBox("自动添加")
        self.auto_add_switch.setObjectName("floatAutoAdd")
        self.auto_add_switch.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        self.auto_add_switch.setAccessibleName("自动添加")
        self.auto_add_switch.setMinimumHeight(28)
        self.auto_add_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_add_switch.setToolTip("新复制的词卡通过校验和查重后直接加入当前牌组，不弹出预览或成功提示")
        self.auto_add_switch.toggled.connect(owner.set_auto_add)
        header.addWidget(self.auto_add_switch)
        header.addStretch()
        self.main_button = QPushButton("↗")
        self.main_button.setFixedSize(28, 28)
        self.main_button.setToolTip("打开主窗口")
        self.main_button.setAccessibleName("打开主窗口")
        self.main_button.clicked.connect(owner.open_main)
        header.addWidget(self.main_button)
        self.collapse_button = QPushButton("−")
        self.collapse_button.setFixedSize(28, 28)
        self.collapse_button.setToolTip("收起预览，继续监听")
        self.collapse_button.setAccessibleName("收起预览")
        self.collapse_button.clicked.connect(self.hide)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.queue_label = QLabel()
        title.setToolTip("复制词卡 JSON 即可识别；连续复制会进入待添加队列")
        self.queue_label.setObjectName("floatMuted")
        self.queue_label.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        layout.addWidget(self.queue_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("牌组"))
        self.deck_box = QComboBox()
        self.deck_box.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_CONTROL_SIZE))
        self.deck_box.setAccessibleName("目标牌组")
        self.deck_box.currentTextChanged.connect(owner.select_deck)
        row.addWidget(self.deck_box, 1)
        layout.addLayout(row)
        self.model_label = QLabel("笔记类型：" + owner.main.controller.checker.adapter.config.note_type)
        self.model_label.setObjectName("floatMuted")
        self.model_label.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        layout.addWidget(self.model_label)
        front_label = QLabel("正面")
        front_label.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        front_label.setObjectName("floatMuted")
        layout.addWidget(front_label)
        self.front_box = QTextEdit()
        self.front_box.setObjectName("floatFront")
        self.front_box.setAcceptRichText(False)
        front_font = pixel_font(owner.main.fonts["front"], FLOAT_EDITOR_SIZE)
        front_font.setVariableAxis(QFont.Tag("wght"), 400)
        self.front_box.setFont(front_font)
        self.front_box.setFixedHeight(76)
        self.front_box.setPlaceholderText("输入正面内容")
        self.front_box.setAccessibleName("卡片正面")
        self.front_box.textChanged.connect(lambda: owner.edit_front(self.front_box.toPlainText()))
        layout.addWidget(self.front_box)
        back_label = QLabel("背面")
        back_label.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        back_label.setObjectName("floatMuted")
        layout.addWidget(back_label)
        self.back_box = QTextEdit()
        self.back_box.setAcceptRichText(False)
        self.back_box.setFont(pixel_font(owner.main.fonts["back"], FLOAT_EDITOR_SIZE))
        self.back_box.setPlaceholderText("输入背面内容")
        self.back_box.setAccessibleName("卡片背面")
        self.back_box.setMinimumHeight(100)
        self.back_box.textChanged.connect(lambda: owner.edit_back(self.back_box.toPlainText()))
        layout.addWidget(self.back_box, 1)
        self.status_label = FloatingStatusLabel()
        self.status_label.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_META_SIZE))
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_label, 1)
        self.retry_button = QPushButton("重试连接")
        self.retry_button.setObjectName("floatRetry")
        self.retry_button.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_CONTROL_SIZE))
        self.retry_button.setFixedHeight(28)
        self.retry_button.setToolTip("重新连接 Anki 并查重")
        self.retry_button.clicked.connect(owner.main.controller.retry_connection)
        status_row.addWidget(self.retry_button)
        layout.addLayout(status_row)
        actions = QHBoxLayout()
        self.ignore_button = QPushButton("丢弃")
        self.ignore_button.setObjectName("floatIgnore")
        self.ignore_button.clicked.connect(owner.ignore)
        actions.addWidget(self.ignore_button)
        actions.addStretch()
        self.add_button = QPushButton("添加到 Anki")
        self.add_button.setObjectName("floatAdd")
        self.add_button.setFont(pixel_font(owner.main.fonts["cn_medium"], FLOAT_CONTROL_SIZE))
        self.add_button.clicked.connect(owner.submit)
        self.ignore_button.setFont(pixel_font(owner.main.fonts["cn"], FLOAT_CONTROL_SIZE))
        for button in (self.ignore_button, self.add_button):
            button.setFixedHeight(36)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)

    def hideEvent(self, event):
        self.deck_box.hidePopup()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.deck_box.hidePopup()
        super().closeEvent(event)


class FloatingToast(QWidget):
    def __init__(self, fonts):
        super().__init__(None, FLAGS | Qt.WindowType.WindowDoesNotAcceptFocus)
        prepare_window(self)
        self.setStyleSheet(STYLE)
        self.setFont(pixel_font(fonts["cn"], 14))
        self.setFixedSize(254, 92)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 16)
        panel = QFrame(self)
        panel.setObjectName("floatToast")
        shadow(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 8, 14, 8)
        title = QLabel("✓ 已添加到 Anki")
        title.setFont(pixel_font(fonts["cn_medium"], 14))
        title.setStyleSheet("color: #208347;")
        layout.addWidget(title)
        self.detail = QLabel()
        self.detail.setObjectName("floatMuted")
        self.detail.setFont(pixel_font(fonts["cn"], 12))
        layout.addWidget(self.detail)
        root.addWidget(panel)


@dataclass
class PendingCard:
    raw: str
    key: tuple[str, str]
    auto_add: bool = False


class FloatingCapture(QObject):
    """Capture valid cards only while floating mode is on; never replace a draft."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.enabled = False
        self.auto_add = False
        self.auto_added_count = 0
        self.auto_skipped_count = 0
        self.closed = False
        self.queue = deque()
        self.active = None
        self._last_text = ""
        self._last_key = None
        self._loading = False
        self._finishing = False
        self._submitted_title = ""
        self._submitted_automatically = False
        self.orb = FloatingOrb()
        self.panel = FloatingPanel(self)
        self.toast = FloatingToast(main.fonts)
        self.orb.clicked.connect(self.toggle_preview)
        self.orb.moved.connect(self.reposition)
        self.orb.menu_requested.connect(self.show_menu)
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.read_clipboard)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(400)
        self.poll_timer.timeout.connect(self.read_clipboard)
        self.success_timer = QTimer(self)
        self.success_timer.setSingleShot(True)
        self.success_timer.timeout.connect(self._after_success)
        self.auto_submit_timer = QTimer(self)
        self.auto_submit_timer.setSingleShot(True)
        self.auto_submit_timer.timeout.connect(self._submit_auto)
        self._restore_position()

    @property
    def pending_count(self):
        return len(self.queue) + int(self.active is not None)

    def _restore_position(self):
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry()
        point = QPoint(area.right() - self.orb.width() - 16, area.center().y())
        saved = self.main._state.get("floating_position")
        if isinstance(saved, list) and len(saved) == 2 and all(type(v) is int for v in saved):
            point = QPoint(*saved)
            screen = QApplication.screenAt(point) or screen
            area = screen.availableGeometry()
        self.orb.move(clamp_position(point, self.orb, area))

    def enable(self):
        if self.closed:
            return
        self.enabled = True
        self.main.prompt_popover.hide()
        self.main.connection_popover.hide()
        self.main.hide()
        self.orb.show()
        self.poll_timer.start()
        self._load_next()
        self.read_clipboard()
        self.sync()

    def open_main(self):
        self.enabled = False
        self.set_auto_add(False)
        self.poll_timer.stop()
        self.success_timer.stop()
        self.orb.hide()
        self.panel.hide()
        self.toast.hide()
        self.persist_position()
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()
        self.main.focus_paste()

    def persist_position(self):
        self.main._save_state(floating_position=[self.orb.x(), self.orb.y()])

    def read_clipboard(self):
        if not self.enabled or self.closed:
            return
        raw = self.clipboard.text()
        if raw == self._last_text:
            return
        self._last_text = raw
        try:
            card = parse_card(raw)
        except CardError:
            return
        key = (card.front, card.back)
        if key == self._last_key:
            return
        self._last_key = key
        if (self.active and key == self.active.key) or any(item.key == key for item in self.queue):
            return
        self.queue.append(PendingCard(raw, key, auto_add=self.auto_add))
        self._load_next()
        self.sync()

    def _load_next(self):
        if not self.enabled or self.closed or self.active or self._finishing or self.success_timer.isActive():
            return
        ctl = self.main.controller
        if ctl.state is State.ADDING:
            return
        if ctl.paste_text.strip() or ctl.card.front or ctl.card.back:
            self.active = PendingCard(ctl.paste_text, (ctl.card.front, ctl.card.back))
        elif self.queue:
            self.active = self.queue.popleft()
            self._loading = True
            self.main._set_text(self.main.paste_box, self.active.raw)
            ctl.paste_changed(self.active.raw)
            self._loading = False
        if self.active:
            self.sync()
            self.reposition()
            if not (self.auto_add and self.active.auto_add):
                self.panel.show()  # WA_ShowWithoutActivating: never steal the source app's focus.

    def sync(self):
        if self.closed:
            return
        ctl = self.main.controller
        if self.enabled and not self.active and not self._finishing and (ctl.card.front or ctl.card.back):
            self.active = PendingCard(ctl.paste_text, (ctl.card.front, ctl.card.back))
        for editor, text in ((self.panel.front_box, ctl.card.front), (self.panel.back_box, ctl.card.back)):
            if editor.toPlainText() != text:
                blocker = QSignalBlocker(editor)
                editor.setPlainText(text)
                del blocker
        names = list(ctl.deck_names)
        if ctl.deck and ctl.deck not in names:
            names.append(ctl.deck)
        if [self.panel.deck_box.itemText(i) for i in range(self.panel.deck_box.count())] != names:
            blocker = QSignalBlocker(self.panel.deck_box)
            self.panel.deck_box.clear()
            self.panel.deck_box.addItems(names)
            del blocker
        blocker = QSignalBlocker(self.panel.deck_box)
        self.panel.deck_box.setCurrentText(ctl.deck)
        del blocker
        editing = ctl.state is not State.ADDING
        self.panel.front_box.setReadOnly(not editing)
        self.panel.back_box.setReadOnly(not editing)
        self.panel.deck_box.setEnabled(editing)
        self.panel.ignore_button.setEnabled(editing and self.active is not None)
        self.panel.retry_button.setEnabled(editing)
        self.panel.add_button.setEnabled(ctl.state is State.READY and self.main.add_button.isEnabled())
        self.panel.add_button.setText("正在添加…" if not editing else "添加到 Anki")
        if self.auto_add:
            if self.active and not self.active.auto_add:
                summary = f"待添加 {self.pending_count} 张 · 需手动确认"
            elif ctl.state in (State.ERROR, State.ANKI_OFFLINE):
                summary = f"待处理 {self.pending_count} 张 · 自动添加已暂停"
            else:
                summary = f"本次已自动添加 {self.auto_added_count} 张 · 待处理 {self.pending_count} 张"
        else:
            summary = f"待添加 {self.pending_count} 张 · 可直接编辑"
        self.panel.queue_label.setText(summary)
        self.panel.queue_label.setVisible(self.auto_add or bool(self.pending_count))
        connection = self.main.connection_button
        message = ctl.message or connection.label.text()
        self.panel.status_label.set_status(
            message, "✓ 已添加到 Anki" if ctl.state is State.SUCCESS else None
        )
        color = "#D35D45" if ctl.state in (State.ERROR, State.INVALID, State.DUPLICATE, State.ANKI_OFFLINE) or connection.property("level") == "warn" else "#28834F" if ctl.state is State.READY else "#748095"
        self.panel.status_label.setStyleSheet(f"color: {color};")
        self.orb.count = self.pending_count
        self.orb.automatic = self.auto_add
        self.orb.attention = bool(self.active and ctl.state in (State.ERROR, State.ANKI_OFFLINE))
        if not self.enabled:
            tooltip = "剪贴板监听已暂停 · 右键恢复"
        elif self.auto_add:
            tooltip = f"自动添加已开启 · 目标牌组：{ctl.deck}\n本次已添加 {self.auto_added_count} 张 · 跳过重复 {self.auto_skipped_count} 张"
            if self.orb.attention:
                tooltip += "\n自动添加已暂停：" + ctl.message + " · 点击小球处理"
            elif self.active and not self.active.auto_add:
                tooltip += "\n已有草稿待手动确认 · 点击小球处理"
        else:
            tooltip = "点击预览词卡 · 拖动移动 · 右键打开菜单"
        self.orb.setToolTip(tooltip)
        self.orb.update()
        if ctl.state is State.ADDING:
            self._submitted_title = ctl.card.front
        if self.active and not self._loading and not self._finishing:
            if ctl.state is State.SUCCESS:
                self._finishing = True
                QTimer.singleShot(0, self._complete)
            elif self.enabled and self.auto_add and self.active.auto_add and ctl.state is State.READY:
                if not self.auto_submit_timer.isActive():
                    self.auto_submit_timer.start(0)
            elif self.enabled and self.auto_add and self.active.auto_add and ctl.state is State.DUPLICATE:
                self._finishing = True
                QTimer.singleShot(0, self._skip_duplicate)
            elif ctl.state is State.EMPTY:
                self.active = None
                QTimer.singleShot(0, self._load_next)

    def _complete(self):
        if self.closed:
            return
        quiet = self._submitted_automatically
        self._submitted_automatically = False
        self.active = None
        self._finishing = False
        if quiet:
            self.auto_added_count += 1
        self.sync()
        if self.enabled and quiet:
            self.panel.hide()
            QTimer.singleShot(0, self._load_next)
        elif self.enabled:
            self.panel.hide()
            detail = self._submitted_title + " · " + self.main.controller.deck
            self.toast.detail.setText(self.toast.detail.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, 196))
            self.toast.detail.setToolTip(detail)
            self.reposition()
            self.toast.show()
            self.success_timer.start(2000)

    def _after_success(self):
        self.toast.hide()
        self._load_next()

    def set_auto_add(self, enabled):
        self.auto_add = bool(enabled)
        self.auto_submit_timer.stop()
        blocker = QSignalBlocker(self.panel.auto_add_switch)
        self.panel.auto_add_switch.setChecked(self.auto_add)
        del blocker
        if self.auto_add:
            # The current clipboard and existing drafts predate this opt-in.
            self._last_text = self.clipboard.text()
            self.panel.hide()
            self.toast.hide()
            self.success_timer.stop()
            self._load_next()
        else:
            if self.active:
                self.active.auto_add = False
            for item in self.queue:
                item.auto_add = False
        self.sync()

    def _submit_auto(self):
        ctl = self.main.controller
        if (self.enabled and not self.closed and self.auto_add and self.active
                and self.active.auto_add and ctl.state is State.READY):
            self.submit(automatic=True)

    def _skip_duplicate(self):
        if self.closed:
            return
        if not (self.enabled and self.auto_add and self.active and self.active.auto_add
                and self.main.controller.state is State.DUPLICATE):
            self._finishing = False
            self.sync()
            self._load_next()
            return
        self.auto_skipped_count += 1
        self.active = None
        self.main.controller.clear_clicked()
        self._finishing = False
        self.sync()
        self._load_next()

    def submit(self, automatic=False):
        if self.main.controller.state is State.READY:
            self._submitted_automatically = automatic
        self.main.controller.add_clicked()

    def ignore(self):
        if self.main.controller.state is State.ADDING:
            return
        self.active = None
        self.main.controller.clear_clicked()
        self.panel.hide()
        self._load_next()
        self.sync()

    def _keep_manual_draft(self):
        if self.active:
            self.active.auto_add = False
        self.auto_submit_timer.stop()

    def edit_front(self, text):
        self._keep_manual_draft()
        self.main._set_text(self.main.front_box, text)
        self.main.controller.front_changed(text)

    def edit_back(self, text):
        self._keep_manual_draft()
        self.main._set_text(self.main.back_box, text)
        self.main.controller.back_changed(text)

    def select_deck(self, name):
        if name:
            self.main._on_deck_selected(name)

    def toggle_preview(self):
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.success_timer.stop()
            self.toast.hide()
            self._load_next()
            self.sync()
            self.reposition()
            self.panel.show()

    def reposition(self):
        screen = QApplication.screenAt(self.orb.geometry().center()) or self.orb.screen()
        area = screen.availableGeometry()
        for widget in (self.panel, self.toast):
            x = self.orb.x() - widget.width() + 4
            if x < area.left():
                x = self.orb.x() + self.orb.width() - 4
            y = self.orb.y() + self.orb.height() // 2 - widget.height() // 2
            widget.move(clamp_position(QPoint(x, y), widget, area))

    def show_menu(self, point):
        menu = QMenu(self.orb)
        menu.addAction("打开主窗口", self.open_main)
        menu.addAction("暂停监听" if self.poll_timer.isActive() else "恢复监听", self.toggle_listening)
        menu.addSeparator()
        menu.addAction("退出", self.main.close)
        menu.exec(point)

    def toggle_listening(self):
        # Clipboard signal and timer obey the same pause state.
        self.enabled = not self.enabled
        if self.enabled:
            self.poll_timer.start()
            self.read_clipboard()
            self._load_next()
            self.orb.setToolTip("点击预览词卡 · 拖动移动 · 右键打开菜单")
        else:
            self.poll_timer.stop()
            self.auto_submit_timer.stop()
        self.sync()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.panel.deck_box.hidePopup()
        self.enabled = False
        self.poll_timer.stop()
        self.success_timer.stop()
        self.auto_submit_timer.stop()
        self.clipboard.dataChanged.disconnect(self.read_clipboard)
        self.persist_position()
        for widget in (self.orb, self.panel, self.toast):
            widget.close()
