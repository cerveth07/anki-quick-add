# -*- coding: utf-8 -*-
"""The PySide6/Qt6 Widgets view for Anki Quick Add."""
from __future__ import annotations

import logging
import os
import re
from typing import Callable

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from controller import QuickAddController, short_deck
from ui.qt_executor import QtThreadedExecutor
from ui.theme import (
    GAP_LABEL,
    GAP_PANEL,
    GAP_SECTION,
    GAP_STATUS_TO_HEADER,
    GAP_TIGHT,
    MUTED,
    PAGE_BOTTOM,
    PAGE_SIDE,
    PREVIEW_MIN_BACK,
    PREVIEW_MIN_GROUP,
    PREVIEW_PAD_X,
    PREVIEW_PAD_Y,
    PROMPT_POPOVER_HEIGHT,
    PROMPT_POPOVER_WIDTH,
    STATUS_BADGE_DOT_GAP,
    STATUS_BADGE_DOT_SIZE,
    STATUS_BADGE_HEIGHT,
    STATUS_BADGE_PAD_X,
    TEXT,
    WARN,
    load_fonts,
)
from ui.widgets import FocusSurface, PromptButton, ReplacePasteTextEdit, StatusDot

log = logging.getLogger("aqa")

APP_TITLE = "Anki Quick Add"
DEFAULT_GEOMETRY = "980x700"
MIN_WINDOW_WIDTH = 500
MIN_WINDOW_HEIGHT = 700
FORMAT_PROMPT_STATE_KEY = "format_prompt"
DECK_PLACEHOLDER = "请连接 Anki 后选择牌组"

KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")

LEGACY_FORMAT_PROMPT = (
    "请把要做的卡片输出成 JSON，严格遵守：\n"
    "1. 只输出一个 JSON 对象，不要解释、不要多余文字；\n"
    "2. 只能有 front 和 back 两个字段，值都是字符串，都不能为空；\n"
    "3. back 里用 \\n 表示换行，不要用 Markdown 语法；\n"
    "4. 不要输出 deck、model、tags、allowDuplicate 等字段。\n"
    "示例：\n"
    '{"front":"部外者","back":"假名：ぶがいしゃ\\n词性：名词\\n释义：局外人；非内部成员\\n例句：彼は部外者だ。"}'
)

FORMAT_PROMPT = (
    "你负责日语字词与句子查询，严格按以下规则回答。\n"
    "1. 单个日语词\n"
    "如果用户输入的是单个日语词，只输出一个 JSON 对象，不要解释或添加其他文字。\n"
    "格式：\n"
    '{"front":"単語","back":"假名：たんご\\n词性：名词\\n释义：单词；词语\\n例句：この単語の意味を調べる。＝查询这个单词的意思。"}\n'
    "要求：\n"
    "- 只能有 front 和 back 两个字段，且都不能为空。\n"
    "- back 内使用 \\n 换行，不使用 Markdown。\n"
    "- 不输出 deck、model、tags、allowDuplicate 等其他字段。\n"
    "- 惯用语、固定搭配可以整体作为 front。\n"
    "\n"
    "2. 输入的是变形后的词\n"
    "如果用户输入的是活用或变形形式，front 必须使用词典形/原形，而不是用户输入的变形形式。\n"
    "\n"
    "3. 输入的是完整句子\n"
    "如果用户输入的是完整日语句子，不要把整个回答写成 JSON。\n"
    "首先输出：\n"
    "假名：整句完整读音\n"
    "完整翻译：自然、准确的中文翻译\n"
    "必要时可以简短解释影响理解的语法或语感。\n"
    "然后选择句中真正值得学习的重点词、惯用语或重要语法，每项分别制作 JSON 卡片。\n"
    "不要为了凑数量选择过于基础的词。\n"
    "\n"
    "4. 输入的是截图\n"
    "读取截图中的日语文本后，先判断内容属于：\n"
    "- 单个词：按“单个日语词”规则输出 JSON。\n"
    "- 完整句子：按“完整句子”规则，输出假名、完整翻译和重点词 JSON。\n"
    "\n"
    "5. 其他日语问题\n"
    "如果用户询问的是构词结构、语法原理、词义辨析、语感差异等，而不是单纯要求查询一个词，则正常使用中文解释，不强制使用 JSON。"
)


def _prepare_editor(editor: QTextEdit) -> None:
    editor.setAcceptRichText(False)
    editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    editor.setFrameStyle(QFrame.Shape.NoFrame)
    editor.document().setDocumentMargin(0.0)
    editor.setTabChangesFocus(False)


class PromptPopover(QWidget):
    """Anchored, non-modal prompt editor shown beside the prompt button."""

    def __init__(self, parent: QWidget, fonts, prompt: str):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("promptPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(PROMPT_POPOVER_WIDTH, PROMPT_POPOVER_HEIGHT)

        root = QVBoxLayout(self)
        # Keep a generous translucent gutter around the panel so the shadow
        # fades out fully before the popup window edge. This avoids the dark
        # clipped rim seen on Windows while retaining a light floating depth.
        root.setContentsMargins(12, 10, 12, 16)
        root.setSpacing(0)

        panel = QFrame(self)
        panel.setObjectName("promptSurface")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(32, 36, 44, 20))
        panel.setGraphicsEffect(shadow)
        self._shadow = shadow

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(GAP_PANEL, GAP_PANEL, GAP_PANEL, GAP_PANEL)
        layout.setSpacing(0)

        title = QLabel("制卡提示词", panel)
        title.setObjectName("sectionTitle")
        title.setFont(fonts["cn_medium"])
        layout.addWidget(title)
        layout.addSpacing(10)

        self.prompt_box = QTextEdit(panel)
        self.prompt_box.setObjectName("promptEdit")
        self.prompt_box.setFont(fonts["cn"])
        self.prompt_box.setAcceptRichText(False)
        self.prompt_box.setPlainText(prompt)
        _prepare_editor(self.prompt_box)
        layout.addWidget(self.prompt_box, 1)
        layout.addSpacing(12)

        button_row = QWidget(panel)
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.copy_button = self._link_button("复制制卡提示词", button_row)
        self.reset_button = self._link_button("恢复默认", button_row)
        self.copy_button.setFont(fonts["button"])
        self.reset_button.setFont(fonts["button"])

        icon_dir = os.path.join(getattr(parent, "_bundle_dir", ""), "ui", "icons")
        copy_icon = QIcon(os.path.join(icon_dir, "clipboard.svg"))
        reset_icon = QIcon(os.path.join(icon_dir, "reset.svg"))
        if not copy_icon.isNull():
            self.copy_button.setIcon(copy_icon)
            self.copy_button.setIconSize(QSize(15, 15))
        if not reset_icon.isNull():
            self.reset_button.setIcon(reset_icon)
            self.reset_button.setIconSize(QSize(15, 15))

        self._caret = QLabel(self)
        self._caret.setObjectName("promptCaret")
        self._caret.setFixedSize(QSize(18, 10))
        self._caret.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        caret_icon = QIcon(os.path.join(icon_dir, "popover-caret.svg"))
        if not caret_icon.isNull():
            self._caret.setPixmap(caret_icon.pixmap(QSize(18, 10)))
        else:
            self._caret.hide()

        button_layout.addWidget(self.copy_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.reset_button)
        layout.addWidget(button_row)

        root.addWidget(panel)

    @staticmethod
    def _link_button(text: str, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("linkButton")
        return button

    def show_anchored(self, anchor: QWidget) -> None:
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        bottom_right = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
        screen = QApplication.screenAt(bottom_right) or QApplication.primaryScreen()
        if screen is None:
            self.move(bottom_right.x() - self.width(), bottom_right.y() + 8)
        else:
            available = screen.availableGeometry()
            x = bottom_right.x() - self.width()
            y = bottom_right.y() + 8
            if y + self.height() > available.bottom() + 1:
                y = top_left.y() - self.height() - 8
            x = max(available.left() + 8, min(x, available.right() - self.width() + 1 - 8))
            y = max(available.top() + 8, min(y, available.bottom() - self.height() + 1 - 8))
            self.move(x, y)
        self.show()
        self.raise_()

        shown_below = self.y() >= bottom_right.y()
        if shown_below and not self._caret.pixmap().isNull():
            anchor_center_x = top_left.x() + anchor.width() // 2
            local_center_x = anchor_center_x - self.x()
            caret_x = max(18, min(
                local_center_x - self._caret.width() // 2,
                self.width() - self._caret.width() - 18,
            ))
            self._caret.move(caret_x, 1)
            self._caret.show()
            self._caret.raise_()
        else:
            self._caret.hide()

        self.prompt_box.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        callback = getattr(parent, "_set_prompt_open_state", None)
        if callback is not None:
            callback(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        parent = self.parentWidget()
        callback = getattr(parent, "_set_prompt_open_state", None)
        if callback is not None:
            callback(False)


class MainWindow(QMainWindow):
    """Qt view implementing the controller's small view protocol."""

    def __init__(self, checker, state: dict, save_state: Callable[..., None], bundle_dir: str, executor=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self._save_state = save_state
        self._state = state
        self._bundle_dir = bundle_dir
        app_icon_path = os.path.join(bundle_dir, "assets", "anki-quick-add.ico")
        app_icon = QIcon(app_icon_path)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._closing = False
        self.default_deck = checker.adapter.config.deck_name
        self.executor = executor or QtThreadedExecutor()
        self.fonts, self.font_notes = load_fonts(bundle_dir)
        saved_format_prompt = state.get(FORMAT_PROMPT_STATE_KEY)
        if (
            isinstance(saved_format_prompt, str)
            and saved_format_prompt.strip()
            and saved_format_prompt.strip() != LEGACY_FORMAT_PROMPT.strip()
        ):
            self.format_prompt = saved_format_prompt
        else:
            self.format_prompt = FORMAT_PROMPT
        self._programmatic = 0
        self._format_prompt_save_timer = QTimer(self)
        self._format_prompt_save_timer.setSingleShot(True)
        self._format_prompt_save_timer.timeout.connect(self._persist_format_prompt)

        saved_deck = state.get("deck")
        initial_deck = (
            saved_deck.strip()
            if isinstance(saved_deck, str) and saved_deck.strip()
            else self.default_deck
        )
        self.controller = QuickAddController(
            checker,
            view=self,
            schedule=lambda delay, callback: QTimer.singleShot(delay, callback),
            executor=self.executor,
            initial_deck=initial_deck,
        )
        self._build()

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._sync_focus)

    def _build(self) -> None:
        root = QWidget(self)
        root.setObjectName("rootWidget")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(PAGE_SIDE, 20, PAGE_SIDE, PAGE_BOTTOM)
        outer.setSpacing(0)
        self.outer_layout = outer

        self._build_connection_badge(outer)

        outer.addSpacing(GAP_STATUS_TO_HEADER)
        header = QWidget(root)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self.paste_title = QLabel("制卡内容", header)
        self.paste_title.setObjectName("sectionTitle")
        self.paste_title.setFont(self.fonts["cn_medium"])
        header_layout.addWidget(self.paste_title, 1, Qt.AlignmentFlag.AlignVCenter)
        document_icon = os.path.join(self._bundle_dir, "ui", "icons", "document.svg")
        chevron_down = os.path.join(self._bundle_dir, "ui", "icons", "chevron-down.svg")
        chevron_up = os.path.join(self._bundle_dir, "ui", "icons", "chevron-up.svg")
        self.format_button = PromptButton(
            "制卡提示词", self.fonts["button"], document_icon, chevron_down, chevron_up, header
        )
        self.format_button.setMinimumWidth(144)
        self.format_button.setFixedHeight(40)
        self.format_button.clicked.connect(self.toggle_format)
        header_layout.addWidget(self.format_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(header)

        outer.addSpacing(GAP_LABEL)
        self.paste_surface = FocusSurface("pasteSurface", root)
        self.paste_surface.setFixedHeight(148)
        paste_layout = QVBoxLayout(self.paste_surface)
        paste_layout.setContentsMargins(1, 1, 1, 1)
        paste_layout.setSpacing(0)
        self.paste_box = ReplacePasteTextEdit(self.paste_surface)
        self.paste_box.setObjectName("pasteEdit")
        self.paste_box.setFont(self.fonts["cn"])
        self.paste_box.setPlaceholderText("粘贴制卡 JSON / 文本…")
        _prepare_editor(self.paste_box)
        paste_layout.addWidget(self.paste_box)
        outer.addWidget(self.paste_surface)

        self.status_label = QLabel(root)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setFont(self.fonts["small"])
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        outer.addWidget(self.status_label)

        self.hint_label = QLabel(root)
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setFont(self.fonts["small"])
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(False)
        outer.addWidget(self.hint_label)

        self.prompt_popover = self._build_prompt_popover()
        self.format_prompt_box = self.prompt_popover.prompt_box

        outer.addSpacing(GAP_SECTION)
        preview_head = QWidget(root)
        preview_head_layout = QHBoxLayout(preview_head)
        preview_head_layout.setContentsMargins(0, 0, 0, 0)
        preview_head_layout.setSpacing(GAP_TIGHT)
        self.preview_title = QLabel("卡片预览", preview_head)
        self.preview_title.setObjectName("sectionTitle")
        self.preview_title.setFont(self.fonts["cn_medium"])
        preview_head_layout.addWidget(self.preview_title, 0, Qt.AlignmentFlag.AlignVCenter)
        preview_editable = QLabel("可直接修改", preview_head)
        preview_editable.setObjectName("mutedLabel")
        preview_editable.setFont(self.fonts["small"])
        preview_head_layout.addWidget(preview_editable, 0, Qt.AlignmentFlag.AlignVCenter)
        preview_head_layout.addStretch(1)
        outer.addWidget(preview_head)
        outer.addSpacing(GAP_TIGHT)

        self.preview_surface = FocusSurface("previewSurface", root)
        self.preview_surface.setMinimumHeight(PREVIEW_MIN_GROUP)
        self.preview_surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout = QVBoxLayout(self.preview_surface)
        preview_layout.setContentsMargins(PREVIEW_PAD_X, PREVIEW_PAD_Y, PREVIEW_PAD_X, PREVIEW_PAD_Y)
        preview_layout.setSpacing(0)

        preview_layout.addWidget(self._preview_heading("正面", "accentFront"))
        self.front_box = QTextEdit(self.preview_surface)
        self.front_box.setObjectName("frontEdit")
        self.front_box.setFont(self.fonts["cn"])
        self.front_box.setPlaceholderText("在此输入卡片正面内容…")
        _prepare_editor(self.front_box)
        self.front_box.setFixedHeight(50)
        preview_layout.addWidget(self.front_box)

        preview_layout.addSpacing(PREVIEW_PAD_Y)
        divider = QFrame(self.preview_surface)
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        preview_layout.addWidget(divider)
        preview_layout.addSpacing(PREVIEW_PAD_Y)

        preview_layout.addWidget(self._preview_heading("背面", "accentBack"))
        self.back_box = QTextEdit(self.preview_surface)
        self.back_box.setObjectName("backEdit")
        self.back_box.setFont(self.fonts["back"])
        self.back_box.setPlaceholderText("在此输入卡片背面内容…")
        _prepare_editor(self.back_box)
        self.back_box.setMinimumHeight(PREVIEW_MIN_BACK)
        self.back_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.back_box, 1)
        outer.addWidget(self.preview_surface, 1)

        outer.addSpacing(GAP_SECTION)
        self._build_action_row(outer, root)

        self._wire_editor(self.paste_box, self.controller.paste_changed)
        self._wire_editor(self.front_box, self.controller.front_changed, self._retag_front)
        self._wire_editor(self.back_box, self.controller.back_changed, self._retag_back)

        self._ctrl_enter = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._ctrl_enter.activated.connect(self.controller.add_clicked)
        self._ctrl_enter_alt = QShortcut(QKeySequence("Ctrl+Enter"), self)
        self._ctrl_enter_alt.activated.connect(self.controller.add_clicked)
        self.set_connection("Anki 连接中…", "info")
        self.set_enabled(False, True)
        QTimer.singleShot(0, self.focus_paste)

    def _build_connection_badge(self, outer: QVBoxLayout) -> None:
        self.connection_badge = QFrame()
        self.connection_badge.setObjectName("statusBadge")
        self.connection_badge.setFixedHeight(STATUS_BADGE_HEIGHT)
        self.connection_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        badge_layout = QHBoxLayout(self.connection_badge)
        badge_layout.setContentsMargins(STATUS_BADGE_PAD_X, 1, STATUS_BADGE_PAD_X, 1)
        badge_layout.setSpacing(STATUS_BADGE_DOT_GAP)
        self.connection_dot = StatusDot(self.connection_badge)
        self.connection_dot.setFixedSize(STATUS_BADGE_DOT_SIZE, STATUS_BADGE_DOT_SIZE)
        badge_layout.addWidget(self.connection_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.connection_label = QLabel("Anki 连接中…", self.connection_badge)
        self.connection_label.setFont(self.fonts["cn"])
        badge_layout.addWidget(self.connection_label, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self.connection_badge, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_action_row(self, outer: QVBoxLayout, parent: QWidget) -> None:
        action = QWidget(parent)
        action.setFixedHeight(46)
        row = QHBoxLayout(action)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP_TIGHT)

        self.deck_label = QLabel("牌组", action)
        self.deck_label.setFont(self.fonts["cn"])
        row.addWidget(self.deck_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.deck_surface = FocusSurface("deckSurface", action)
        self.deck_surface.setFixedHeight(46)
        self.deck_surface.setMinimumWidth(120)
        self.deck_surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        deck_layout = QHBoxLayout(self.deck_surface)
        deck_layout.setContentsMargins(12, 2, 4, 2)
        deck_layout.setSpacing(8)
        self.deck_icon = QLabel(self.deck_surface)
        self.deck_icon.setObjectName("deckIcon")
        self.deck_icon.setFixedSize(16, 16)
        deck_icon_path = os.path.join(self._bundle_dir, "ui", "icons", "deck.svg")
        deck_icon = QIcon(deck_icon_path)
        if not deck_icon.isNull():
            self.deck_icon.setPixmap(deck_icon.pixmap(QSize(16, 16)))
        self.deck_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        deck_layout.addWidget(self.deck_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.deck_box = QComboBox(self.deck_surface)
        self.deck_box.setObjectName("deckCombo")
        self.deck_box.setFont(self.fonts["cn"])
        self.deck_box.setEditable(False)
        self.deck_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        deck_arrow_path = os.path.join(self._bundle_dir, "ui", "icons", "chevron-down-muted.svg").replace("\\", "/")
        self.deck_box.setStyleSheet(
            f'QComboBox#deckCombo::down-arrow {{ image: url("{deck_arrow_path}"); width: 10px; height: 10px; }}'
        )
        self.deck_box.addItem(DECK_PLACEHOLDER)
        self.deck_box.currentTextChanged.connect(self._on_deck_selected)
        deck_layout.addWidget(self.deck_box, 1)
        row.addWidget(self.deck_surface, 1)

        self.clear_button = QPushButton("清空", action)
        self.clear_button.setObjectName("secondaryButton")
        self.clear_button.setFont(self.fonts["button"])
        self.clear_button.setFixedHeight(46)
        self.clear_button.setMinimumWidth(94)
        self.clear_button.clicked.connect(self.controller.clear_clicked)
        row.addWidget(self.clear_button, 0)

        self.add_button = QPushButton("加入 Anki", action)
        self.add_button.setObjectName("primaryButton")
        self.add_button.setFont(self.fonts["button"])
        self.add_button.setFixedHeight(46)
        self.add_button.setMinimumWidth(144)
        self.add_button.clicked.connect(self.controller.add_clicked)
        row.addWidget(self.add_button, 0)
        outer.addWidget(action)

    def _preview_heading(self, text: str, accent_name: str) -> QWidget:
        row = QWidget(self.preview_surface)
        row.setFixedHeight(32)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        marker = QFrame(row)
        marker.setObjectName(accent_name)
        marker.setFixedSize(6, 24)
        layout.addWidget(marker, 0, Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text, row)
        label.setFont(self.fonts["section"])
        layout.addWidget(label, 1, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _build_prompt_popover(self) -> PromptPopover:
        popover = PromptPopover(self, self.fonts, self.format_prompt)
        popover.prompt_box.textChanged.connect(self._schedule_format_prompt_save)
        popover.copy_button.clicked.connect(self._copy_prompt)
        popover.reset_button.clicked.connect(self._reset_format_prompt)
        return popover

    def _wire_editor(self, editor: QTextEdit, handler, retag=None) -> None:
        def changed() -> None:
            if self._programmatic:
                return
            value = editor.toPlainText()
            if retag is not None:
                retag(value)
            handler(value)
        editor.textChanged.connect(changed)

    @staticmethod
    def _qt_index(text: str, index: int) -> int:
        index = max(0, min(int(index), len(text)))
        return len(text[:index].encode("utf-16-le")) // 2

    def _apply_font_ranges(self, editor: QTextEdit, ranges: list[tuple[int, int, QFont]]) -> None:
        blocker = QSignalBlocker(editor)
        self._programmatic += 1
        try:
            saved = editor.textCursor()
            maximum = max(0, editor.document().characterCount() - 1)
            text = editor.toPlainText()
            for start, end, font in ranges:
                start = min(self._qt_index(text, start), maximum)
                end = min(self._qt_index(text, end), maximum)
                if end <= start:
                    continue
                cursor = QTextCursor(editor.document())
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setFont(font)
                cursor.mergeCharFormat(fmt)
            editor.setTextCursor(saved)
        finally:
            self._programmatic -= 1
            del blocker

    def _retag_front(self, value: str) -> None:
        font = self.fonts["front"] if CJK_RE.search(value) else self.fonts["latin"]
        self._apply_font_ranges(self.front_box, [(0, len(value), font)])

    def _retag_back(self, value: str) -> None:
        ranges: list[tuple[int, int, QFont]] = []
        offset = 0
        for line in value.split("\n"):
            ranges.append((offset, offset + len(line), self.fonts["jp"] if KANA_RE.search(line) else self.fonts["cn"]))
            offset += len(line) + 1
        self._apply_font_ranges(self.back_box, ranges)

    def _set_text(self, editor: QTextEdit, value: str, font: QFont | None = None) -> None:
        previous_read_only = editor.isReadOnly()
        editor.setReadOnly(False)
        self._programmatic += 1
        blocker = QSignalBlocker(editor)
        try:
            editor.setPlainText(value)
            if font is not None and value:
                cursor = QTextCursor(editor.document())
                cursor.setPosition(0)
                cursor.setPosition(self._qt_index(value, len(value)), QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setFont(font)
                cursor.mergeCharFormat(fmt)
        finally:
            del blocker
            self._programmatic -= 1
            editor.setReadOnly(previous_read_only)

    def _set_prompt_open_state(self, opened: bool) -> None:
        self.format_button.set_open(opened)

    def toggle_format(self) -> None:
        if self.prompt_popover.isVisible():
            self.prompt_popover.hide()
        else:
            self.show_prompt_popover()

    def show_prompt_popover(self) -> None:
        self.prompt_popover.show_anchored(self.format_button)

    def _schedule_format_prompt_save(self) -> None:
        self._format_prompt_save_timer.start(500)

    def _persist_format_prompt(self) -> None:
        value = self.format_prompt_box.toPlainText().strip()
        if value:
            self.format_prompt = value
            self._save_state(format_prompt=value)

    def _reset_format_prompt(self) -> None:
        self._programmatic += 1
        blocker = QSignalBlocker(self.format_prompt_box)
        try:
            self.format_prompt_box.setPlainText(FORMAT_PROMPT)
            self.format_prompt = FORMAT_PROMPT
        finally:
            del blocker
            self._programmatic -= 1
        self._save_state(format_prompt=FORMAT_PROMPT)
        self._copy_to_clipboard(FORMAT_PROMPT, "✓ 已恢复默认格式要求")

    def _copy_to_clipboard(self, text: str, note: str) -> None:
        QApplication.clipboard().setText(text)
        self.set_status(note, "ok")

    def _copy_prompt(self) -> None:
        text = self.format_prompt_box.toPlainText().strip() or FORMAT_PROMPT
        self._copy_to_clipboard(text, "✓ 已复制格式要求：直接粘给 ChatGPT 即可")

    def set_status(self, text: str, level: str = "info") -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {WARN if level == 'warn' else MUTED if level == 'info' else '#36A86B'};")
        hidden_connection_status = text.startswith(("⚠ 未连接 Anki", "正在连接 Anki…"))
        visible = bool(text) and not hidden_connection_status
        self.status_label.setVisible(visible)
        if text.startswith(("⚠ 无法解析", "⚠ JSON 顶层")):
            self.show_prompt_popover()

    def set_connection(self, text: str, level: str = "info") -> None:
        level = level if level in {"ok", "warn", "info"} else "info"
        self.connection_label.setText(text)
        self.connection_badge.setProperty("level", level)
        self.connection_dot.set_level(level)
        style = self.connection_badge.style()
        style.unpolish(self.connection_badge)
        style.polish(self.connection_badge)
        self.connection_badge.adjustSize()

    def set_card(self, front: str, back: str, hint: str = "") -> None:
        self._set_text(self.front_box, front, self.fonts["front"] if CJK_RE.search(front) else self.fonts["latin"])
        self._set_text(self.back_box, back)
        self._retag_back(back)
        self._show_hint(hint)

    def clear_preview(self) -> None:
        self._set_text(self.front_box, "")
        self._set_text(self.back_box, "")
        self._show_hint("")

    def clear_card(self) -> None:
        self._set_text(self.paste_box, "")
        self.clear_preview()

    def _show_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    def set_deck(self, name: str) -> None:
        index = self.deck_box.findText(name, Qt.MatchFlag.MatchExactly)
        if index < 0:
            self.deck_box.addItem(name)
            index = self.deck_box.findText(name, Qt.MatchFlag.MatchExactly)
        blocker = QSignalBlocker(self.deck_box)
        self.deck_box.setCurrentIndex(index)
        del blocker
        non_default = bool(self.default_deck and name != self.default_deck)
        self.deck_label.setText("牌组（非默认）" if non_default else "牌组")
        self.deck_label.setStyleSheet(f"color: {WARN if non_default else TEXT};")
        self.add_button.setText(f"加入 {short_deck(name)}" if non_default else "加入 Anki")

    def set_deck_choices(self, names) -> None:
        values = sorted(str(name) for name in names)
        target = self.controller.deck if self.controller.deck in values else (values[0] if values else "")
        blocker = QSignalBlocker(self.deck_box)
        self.deck_box.clear()
        self.deck_box.addItems(values)
        if target:
            index = self.deck_box.findText(target, Qt.MatchFlag.MatchExactly)
            if index >= 0:
                self.deck_box.setCurrentIndex(index)
        del blocker

    def focus_paste(self) -> None:
        self.paste_box.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, lambda: self.paste_box.setFocus(Qt.FocusReason.OtherFocusReason))

    def set_enabled(self, add_enabled: bool, editable: bool) -> None:
        self.add_button.setEnabled(add_enabled)
        for editor in (self.paste_box, self.front_box, self.back_box):
            editor.setReadOnly(not editable)
        self.deck_box.setEnabled(editable)

    def _on_deck_selected(self, name: str) -> None:
        name = (name or "").strip()
        if not name or name == self.controller.deck:
            return
        self.controller.set_deck(name)
        self._save_state(deck=name)

    def _sync_focus(self, _old=None, new=None) -> None:
        focused = new or QApplication.focusWidget()
        self.paste_surface.set_focused(
            bool(focused and (focused is self.paste_box or self.paste_box.isAncestorOf(focused)))
        )
        self.preview_surface.set_focused(
            bool(
                focused
                and (
                    focused is self.front_box
                    or self.front_box.isAncestorOf(focused)
                    or focused is self.back_box
                    or self.back_box.isAncestorOf(focused)
                )
            )
        )
        self.deck_surface.set_focused(
            bool(focused and (focused is self.deck_box or self.deck_box.isAncestorOf(focused)))
        )

    def _geometry_string(self) -> str:
        rect = self.geometry()
        return f"{rect.width()}x{rect.height()}{rect.x():+d}{rect.y():+d}"

    def closeEvent(self, event) -> None:
        self._closing = True
        try:
            self._persist_format_prompt()
            self._save_state(geometry=self._geometry_string(), deck=self.controller.deck, format_prompt=self.format_prompt)
        except Exception:
            log.exception("保存窗口状态失败")
        self.prompt_popover.hide()
        self.executor.shutdown()
        event.accept()
