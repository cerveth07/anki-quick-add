"""A non-modal floating capture surface sharing the main window's controller."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPoint, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QMenu, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from card import CardError, parse_card
from controller import State


FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
STYLE = """
QFrame#floatSurface {
    background: rgba(250, 252, 255, 248); border: 1px solid #D9E2EE; border-radius: 14px;
}
QWidget { color: #253248; }
QLabel { background: transparent; border: none; }
QLabel#floatMuted { color: #748095; }
QTextEdit { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 8px; padding: 8px; }
QTextEdit:focus { border: 1px solid #6D9FF3; }
QComboBox { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 7px; padding: 5px 8px; }
QPushButton { background: transparent; border: none; border-radius: 7px; padding: 0px 10px; min-height: 0px; }
QPushButton#floatAdd, QPushButton#floatIgnore, QPushButton#floatRetry { min-height: 36px; max-height: 36px; }
QPushButton:hover { background: #EAF0FA; }
QPushButton#floatAdd { color: white; background: #367DEB; }
QPushButton#floatAdd:hover { background: #246BDC; }
QPushButton#floatAdd:disabled { color: #8191A8; background: #E3EAF4; }
QFrame#floatToast { background: #F2FBF6; border: 1px solid #D1E8DA; border-radius: 12px; }
"""


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
        self.setFixedSize(72, 72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击预览词卡 · 拖动移动 · 右键打开菜单")
        self.setAccessibleName("Anki 悬浮制卡")
        self.count = 0
        self._press = None
        self._origin = QPoint()
        self._dragged = False

    def paintEvent(self, event):
        painter = QPainter(self)
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


class FloatingPanel(QWidget):
    def __init__(self, owner):
        super().__init__(None, FLAGS)
        prepare_window(self)
        self.setWindowTitle("Anki Quick Add · 词卡预览")
        self.setStyleSheet(STYLE)
        self.setFont(pixel_font(owner.main.fonts["cn"], 14))
        self.setFixedSize(360, 502)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 16)
        surface = QFrame(self)
        surface.setObjectName("floatSurface")
        shadow(surface)
        root.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        header = QHBoxLayout()
        title = QLabel("Anki Quick Add")
        title.setObjectName("floatTitle")
        title.setFont(pixel_font(owner.main.fonts["cn_medium"], 16))
        header.addWidget(title, 1)
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
        self.collapse_button.clicked.connect(owner.collapse_preview)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.queue_label = QLabel("复制词卡 JSON 即可识别")
        self.queue_label.setObjectName("floatMuted")
        self.queue_label.setFont(pixel_font(owner.main.fonts["cn"], 12))
        layout.addWidget(self.queue_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("牌组"))
        self.deck_box = QComboBox()
        self.deck_box.setAccessibleName("目标牌组")
        self.deck_box.currentTextChanged.connect(owner.select_deck)
        row.addWidget(self.deck_box, 1)
        layout.addLayout(row)
        self.model_label = QLabel("笔记类型：" + owner.main.controller.checker.adapter.config.note_type)
        self.model_label.setObjectName("floatMuted")
        self.model_label.setFont(pixel_font(owner.main.fonts["cn"], 12))
        layout.addWidget(self.model_label)
        front_label = QLabel("正面")
        front_label.setFont(pixel_font(owner.main.fonts["cn"], 12))
        front_label.setObjectName("floatMuted")
        layout.addWidget(front_label)
        self.front_box = QTextEdit()
        self.front_box.setObjectName("floatFront")
        self.front_box.setAcceptRichText(False)
        front_font = pixel_font(owner.main.fonts["front"], 22)
        front_font.setVariableAxis(QFont.Tag("wght"), 400)
        self.front_box.setFont(front_font)
        self.front_box.setFixedHeight(62)
        self.front_box.setPlaceholderText("卡片正面")
        self.front_box.setAccessibleName("卡片正面")
        self.front_box.textChanged.connect(lambda: owner.edit_front(self.front_box.toPlainText()))
        layout.addWidget(self.front_box)
        back_label = QLabel("背面")
        back_label.setFont(pixel_font(owner.main.fonts["cn"], 12))
        back_label.setObjectName("floatMuted")
        layout.addWidget(back_label)
        self.back_box = QTextEdit()
        self.back_box.setAcceptRichText(False)
        self.back_box.setFont(pixel_font(owner.main.fonts["back"], 14))
        self.back_box.setPlaceholderText("卡片背面")
        self.back_box.setAccessibleName("卡片背面")
        self.back_box.setMinimumHeight(100)
        self.back_box.textChanged.connect(lambda: owner.edit_back(self.back_box.toPlainText()))
        layout.addWidget(self.back_box, 1)
        self.status_label = QLabel()
        self.status_label.setFont(pixel_font(owner.main.fonts["cn"], 12))
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(40)
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.ignore_button = QPushButton("忽略")
        self.ignore_button.setObjectName("floatIgnore")
        self.ignore_button.clicked.connect(owner.ignore)
        actions.addWidget(self.ignore_button)
        self.retry_button = QPushButton("重连")
        self.retry_button.setObjectName("floatRetry")
        self.retry_button.setToolTip("重新连接 Anki 并查重")
        self.retry_button.clicked.connect(owner.main.controller.retry_connection)
        actions.addWidget(self.retry_button)
        actions.addStretch()
        self.add_button = QPushButton("添加到 Anki")
        self.add_button.setObjectName("floatAdd")
        self.add_button.setFont(pixel_font(owner.main.fonts["cn_medium"], 14))
        self.add_button.clicked.connect(owner.submit)
        for button in (self.ignore_button, self.retry_button, self.add_button):
            button.setFixedHeight(36)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)


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
        self.title = QLabel("✓ 已添加到 Anki")
        self.title.setFont(pixel_font(fonts["cn_medium"], 14))
        self.title.setStyleSheet("color: #208347;")
        layout.addWidget(self.title)
        self.detail = QLabel()
        self.detail.setObjectName("floatMuted")
        self.detail.setFont(pixel_font(fonts["cn"], 12))
        layout.addWidget(self.detail)
        root.addWidget(panel)


@dataclass
class PendingCard:
    raw: str
    key: tuple[str, str]
    automatic: bool = False


class FloatingCapture(QObject):
    """Capture valid cards only while floating mode is on; never replace a draft."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.enabled = False
        self.closed = False
        self.queue = deque()
        self.active = None
        self._last_text = ""
        self._last_key = None
        self._loading = False
        self._finishing = False
        self._submitted_title = ""
        self.quick_add = main._state.get("floating_quick_add", True) is not False
        self._action_scheduled = False
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
        self._baseline_clipboard()
        self.enabled = True
        self.main.prompt_popover.hide()
        self.main.connection_popover.hide()
        self.main.hide()
        self.orb.show()
        self.poll_timer.start()
        self._load_next()
        self.sync()

    def open_main(self):
        self.enabled = False
        if self.active:
            self.active.automatic = False
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

    def _baseline_clipboard(self):
        # Starting/resuming listening must not import stale clipboard contents.
        self._last_text = self.clipboard.text()
        try:
            card = parse_card(self._last_text)
            self._last_key = (card.front, card.back)
        except CardError:
            self._last_key = None

    def set_quick_add(self, enabled):
        self.quick_add = bool(enabled)
        self.main._save_state(floating_quick_add=self.quick_add)
        self.sync()

    def collapse_preview(self):
        self.panel.hide()
        self.sync()

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
        self.queue.append(PendingCard(raw, key, automatic=True))
        self._load_next()
        self.sync()

    def _load_next(self):
        if not self.enabled or self.closed or self.active or self._finishing:
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
        self.panel.add_button.setEnabled(self.main.add_button.isEnabled())
        self.panel.add_button.setText("正在添加…" if not editing else "重试连接" if ctl.state in (State.ERROR, State.ANKI_OFFLINE) else "添加到 Anki")
        mode = "快速添加" if self.quick_add else "确认后添加"
        count = f"待处理 {self.pending_count} 张" if self.pending_count else "复制词卡 JSON 即可识别"
        self.panel.queue_label.setText(f"{mode} · {count}")
        self.panel.status_label.setText(ctl.message or "")
        color = "#D35D45" if ctl.state in (State.ERROR, State.INVALID, State.DUPLICATE, State.ANKI_OFFLINE) else "#28834F" if ctl.state is State.READY else "#748095"
        self.panel.status_label.setStyleSheet(f"color: {color};")
        self.orb.count = self.pending_count
        self.orb.setToolTip(
            f"{mode} · 牌组：{ctl.deck}\n待处理 {self.pending_count} 张 · 点击查看 · 右键切换模式"
            if self.enabled else "剪贴板监听已暂停 · 右键恢复"
        )
        self.orb.update()
        if ctl.state is State.ADDING:
            self._submitted_title = ctl.card.front
        if self.active and not self._loading and not self._finishing:
            if ctl.state is State.SUCCESS:
                self._finishing = True
                QTimer.singleShot(0, self._complete)
            elif ctl.state is State.EMPTY:
                self.active = None
                QTimer.singleShot(0, self._load_next)
            elif self.enabled and self.quick_add and self.active.automatic and not self.panel.isVisible():
                if ctl.state in (State.ERROR, State.ANKI_OFFLINE, State.INVALID):
                    # Leave failed cards for a deliberate retry, without a retry loop.
                    self.active.automatic = False
                    self._notify("⚠ 需要处理", ctl.message, warning=True)
                elif ctl.state in (State.READY, State.DUPLICATE) and not self._action_scheduled:
                    self._action_scheduled = True
                    QTimer.singleShot(0, self._auto_action)

    def _auto_action(self):
        self._action_scheduled = False
        if (self.closed or not self.enabled or not self.quick_add or self.panel.isVisible()
                or not self.active or not self.active.automatic or self._finishing):
            return
        ctl = self.main.controller
        if ctl.state is State.READY:
            ctl.add_clicked()
        elif ctl.state is State.DUPLICATE:
            title = ctl.card.front
            self._finishing = True
            ctl.clear_clicked()
            self.active = None
            self._finishing = False
            self._notify("已存在，已跳过", title)
            self._load_next()
            self.sync()

    def _notify(self, title, detail, *, warning=False):
        if not self.enabled or self.closed:
            return
        self.toast.title.setText(title)
        self.toast.title.setStyleSheet(f"color: {'#D35D45' if warning else '#208347'};")
        self.toast.detail.setText(self.toast.detail.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, 196))
        self.toast.detail.setToolTip(detail)
        self.reposition()
        self.toast.show()
        self.success_timer.start(2000)

    def _complete(self):
        if self.closed:
            return
        self.active = None
        self._finishing = False
        self.sync()
        if self.enabled:
            detail = self._submitted_title + " · " + self.main.controller.deck
            self._notify("✓ 已添加到 Anki", detail)
            self._load_next()
            if not self.pending_count:
                self.panel.hide()

    def _after_success(self):
        self.toast.hide()

    def submit(self):
        self.main.controller.add_clicked()

    def ignore(self):
        if self.main.controller.state is State.ADDING:
            return
        self.active = None
        self.main.controller.clear_clicked()
        self._load_next()
        self.sync()
        if not self.pending_count:
            self.panel.hide()

    def edit_front(self, text):
        if self.active:
            self.active.automatic = False
        self.main._set_text(self.main.front_box, text)
        self.main.controller.front_changed(text)

    def edit_back(self, text):
        if self.active:
            self.active.automatic = False
        self.main._set_text(self.main.back_box, text)
        self.main.controller.back_changed(text)

    def select_deck(self, name):
        if name:
            self.main._on_deck_selected(name)

    def toggle_preview(self):
        if self.panel.isVisible():
            self.collapse_preview()
        else:
            self.success_timer.stop()
            self.toast.hide()
            # Show before loading, so opening a preview pauses any queued auto action.
            self.reposition()
            self.panel.show()
            self._load_next()
            self.sync()

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
        deck = menu.addAction("目标牌组：" + self.main.controller.deck)
        deck.setEnabled(False)
        group = QActionGroup(menu)
        for label, quick in (("快速添加", True), ("确认后添加", False)):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.quick_add == quick)
            group.addAction(action)
            action.triggered.connect(lambda _checked, value=quick: self.set_quick_add(value))
        menu.addSeparator()
        menu.addAction("打开主窗口", self.open_main)
        menu.addAction("暂停监听" if self.poll_timer.isActive() else "恢复监听", self.toggle_listening)
        menu.addSeparator()
        menu.addAction("退出", self.main.close)
        menu.exec(point)

    def toggle_listening(self):
        # Clipboard signal and timer obey the same pause state.
        self.enabled = not self.enabled
        if self.enabled:
            self._baseline_clipboard()
            self.poll_timer.start()
            self._load_next()
        else:
            self.poll_timer.stop()
        self.sync()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.enabled = False
        self.poll_timer.stop()
        self.success_timer.stop()
        self.clipboard.dataChanged.disconnect(self.read_clipboard)
        self.persist_position()
        for widget in (self.orb, self.panel, self.toast):
            widget.close()
