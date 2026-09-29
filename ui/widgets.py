# -*- coding: utf-8 -*-
"""Small standard Qt widgets used by the main window."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QKeySequence
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit


class FocusSurface(QFrame):
    """A QFrame whose dynamic property drives its QSS focus border."""

    def __init__(self, object_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("focused", False)

    def set_focused(self, focused: bool) -> None:
        focused = bool(focused)
        if bool(self.property("focused")) == focused:
            return
        self.setProperty("focused", focused)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()


class ReplacePasteTextEdit(QTextEdit):
    """Paste editor where Ctrl+V replaces the complete input document."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(True)
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            # Selecting before delegating keeps the normal Qt clipboard path,
            # including text-only MIME conversion and undo support.
            self.selectAll()
            super().keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source: QMimeData) -> None:
        self.selectAll()
        super().insertFromMimeData(source)


class PromptButton(QPushButton):
    """Standard QPushButton with separate document and SVG chevron visuals.

    The border/background/hover/pressed states remain entirely owned by Qt's
    style system.  Child labels only supply the two icons and button text.
    """

    def __init__(
        self,
        text: str,
        font: QFont,
        document_icon_path: str,
        chevron_down_path: str,
        chevron_up_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("promptButton")
        self.setText("")
        self.setAccessibleName(text)
        self._down_icon = QIcon(chevron_down_path)
        self._up_icon = QIcon(chevron_up_path)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 6, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        document = QLabel(self)
        document.setObjectName("promptButtonIcon")
        document.setFixedSize(16, 16)
        document.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QIcon(document_icon_path)
        if not icon.isNull():
            document.setPixmap(icon.pixmap(QSize(16, 16)))
        document.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(document, 0, Qt.AlignmentFlag.AlignVCenter)

        label = QLabel(text, self)
        label.setObjectName("promptButtonText")
        label.setFont(font)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._chevron = QLabel(self)
        self._chevron.setObjectName("promptButtonChevron")
        self._chevron.setFixedSize(10, 10)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set_open(False)

    def set_open(self, opened: bool) -> None:
        icon = self._up_icon if opened else self._down_icon
        if icon.isNull():
            self._chevron.clear()
        else:
            self._chevron.setPixmap(icon.pixmap(QSize(10, 10)))


class StatusDot(QFrame):
    """A stylesheet-owned status dot; no custom painting is needed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusDot")
        self.setFixedSize(10, 10)
        self.setProperty("level", "warn")

    def set_level(self, level: str) -> None:
        level = level if level in {"ok", "warn", "info"} else "warn"
        self.setProperty("level", level)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

class ConnectionButton(QPushButton):
    """Clickable Anki connection status control with dot, label and chevron."""

    def __init__(
        self,
        font: QFont,
        chevron_down_path: str,
        chevron_up_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("connectionButton")
        self.setText("")
        self.setAccessibleName("Anki 连接状态")
        self._down_icon = QIcon(chevron_down_path)
        self._up_icon = QIcon(chevron_up_path)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 0, 9, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.dot = StatusDot(self)
        self.dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel("Anki 连接中…", self)
        self.label.setObjectName("connectionButtonText")
        self.label.setFont(font)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._chevron = QLabel(self)
        self._chevron.setObjectName("connectionButtonChevron")
        self._chevron.setFixedSize(10, 10)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_open(False)
        self.set_connection("Anki 连接中…", "info")

    def sizeHint(self) -> QSize:
        layout = self.layout()
        if layout is None:
            return super().sizeHint()
        hint = layout.sizeHint()
        hint.setHeight(max(hint.height(), super().sizeHint().height()))
        return hint

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_connection(self, text: str, level: str) -> None:
        level = level if level in {"ok", "warn", "info"} else "info"
        self.label.setText(text)
        self.setAccessibleName(text)
        self.setProperty("level", level)
        self.dot.set_level(level)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.adjustSize()
        self.update()

    def set_open(self, opened: bool) -> None:
        icon = self._up_icon if opened else self._down_icon
        if icon.isNull():
            self._chevron.clear()
        else:
            self._chevron.setPixmap(icon.pixmap(QSize(10, 10)))

