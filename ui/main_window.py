# -*- coding: utf-8 -*-
"""The PySide6/Qt6 Widgets view for Anki Quick Add."""
from __future__ import annotations

import logging
import os
import re
from typing import Callable

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QImageReader,
    QPixmap,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QToolButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_launcher import (
    AnkiLaunchError,
    find_anki_executable,
    is_valid_anki_executable,
    launch_anki,
)
from controller import QuickAddController, State, short_deck
from card import CardError, parse_card
from image_attachment import ImageAttachment, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
from ui.floating_window import FloatingCapture
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
    STATUS_BADGE_HEIGHT,
    TEXT,
    WARN,
    load_fonts,
)
from ui.widgets import ConnectionButton, FocusSurface, PromptButton, ReplacePasteTextEdit

log = logging.getLogger("aqa")

APP_TITLE = "Anki Quick Add"
DEFAULT_GEOMETRY = "980x700"
MIN_WINDOW_WIDTH = 500
MIN_WINDOW_HEIGHT = 700
FORMAT_PROMPT_STATE_KEY = "format_prompt"
IMAGE_FORMAT_PROMPT_STATE_KEY = "image_format_prompt"
FORMAT_PROMPT_MODE_STATE_KEY = "format_prompt_mode"
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


# Keep the text-only prompt unchanged: existing users may have edited it.
# The separate image preset never adds image keys to Anki's front/back JSON.
IMAGE_FORMAT_PROMPT = r"""你负责日语字词与句子查询，生成可以直接导入 Anki Quick Add 的日语词卡。
本模式启用智能配图：只在图片能显著帮助理解或记忆词义时，才尝试生成独立图片。

1. 单个日语词
如果用户输入单个日语词，必须生成一个独立、可复制的标准 JSON 对象。
格式：
{"front":"単語","back":"假名：たんご\n词性：名词\n释义：单词；词语\n例句：この単語の意味を調べる。 查询这个单词的意思。"}
要求：
- 只能包含 front 和 back 两个非空字符串字段，不加 image、deck、model、tags、allowDuplicate 等字段。
- back 中的换行使用 JSON 转义符 \n，保证 JSON 可直接解析。
- front 使用词典形；惯用语和固定搭配可以整体作为 front。
- 例句自然、准确，附中文翻译；必要时可补充搭配、辨析或词源。

2. 智能配图判断
优先考虑动作、姿势、空间关系、日本文化特有物品与场景、难以仅靠释义理解的具体概念，以及能明显区分词义的视觉信息。
普通基础词汇、抽象概念、助词、语法，以及图片容易误导的词汇，默认不配图。
不要为了配图而配图；准确性始终优先。

适合配图时：
- 必须实际调用可用的图片生成工具；不能仅描述图片或输出图片提示词。
- 图片只呈现对应物体、动作或场景。严禁任何文字、假名、注释、JSON、水印、词卡布局或教学海报。
- 主体清晰、背景简洁，优先 3:2 横向构图；动作词突出动作，空间词突出相对位置。
- 先生成一张独立图片，再另行输出唯一一个 JSON 代码块，以便通过代码块按钮单独复制文字。
- JSON 代码块内仍严格只有 front/back，图片本身不写入 JSON。

不适合配图或图片生成失败时：
- 只输出一个原生 JSON 对象，不加 Markdown 围栏、说明、占位图片或虚构链接。
- 不要声称已经生成实际并不存在的图片。

3. 输入的是变形后的词
front 必须使用词典形／原形；back 说明所见变形及其语法构成。配图如有必要，应准确表现当前语境中的含义。

4. 输入的是完整句子
先输出：
假名：整句完整读音
完整翻译：自然、准确的中文翻译
必要时简要解释影响理解的语法或语感。
再选择真正值得学习的重点词、惯用语或重要语法，每项分别生成独立的 front/back JSON；不要凑数量。
默认不为多个重点词分别生图，以免图片和 JSON 对应关系混乱；只有用户明确指定某个词时才单独为该词生成图片。

5. 输入的是截图
先识别截图中的日语文本：单词按第 1–3 条处理，完整句子按第 4 条处理。不要直接把截图当作新生成的配图。

6. 其他日语问题
构词结构、语法原理、词义辨析、语感等问题，正常使用中文解释，不强制 JSON，除非图片确实有助于解释，否则不生图。

输出时确保 JSON 独立、合法且可复制；图片不得与词卡文字合成为一张图。
"""


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

    def __init__(self, parent: QWidget, fonts, prompt: str, mode: str = "text"):
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

        # The two presets share the existing editor; their values and
        # persistence are kept separate by MainWindow.
        mode_surface = QFrame(panel)
        mode_surface.setObjectName("promptModeSurface")
        mode_layout = QHBoxLayout(mode_surface)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(3)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for mode_key, label in (("text", "纯文本"), ("image", "智能配图")):
            button = QPushButton(label, mode_surface)
            button.setObjectName("promptModeButton")
            button.setFont(fonts["button"])
            button.setCheckable(True)
            button.setFixedHeight(30)
            button.setAccessibleName(label + "制卡模式")
            self.mode_group.addButton(button)
            self.mode_buttons[mode_key] = button
            mode_layout.addWidget(button)
        layout.addWidget(mode_surface)
        layout.addSpacing(10)
        self.set_mode(mode)

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

    def set_mode(self, mode: str) -> None:
        for name, button in self.mode_buttons.items():
            button.setChecked(name == mode)

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
        # The flag is per dismissing mouse press, not permanent: ordinary
        # outside clicks should still be delivered to the clicked widget.
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, False)
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

    def mousePressEvent(self, event) -> None:
        # Qt.Popup closes on an outside mouse press. When that press hits the
        # button which opened us, Qt may replay it to the button and immediately
        # reopen the popup on mouse release. Prevent replay only for this button;
        # clicks elsewhere retain normal outside-click behavior.
        parent = self.parentWidget()
        trigger = getattr(parent, "format_button", None)
        if trigger is not None and trigger.isVisible():
            click_in_trigger = trigger.mapFromGlobal(event.globalPosition().toPoint())
            if trigger.rect().contains(click_in_trigger):
                self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        super().mousePressEvent(event)

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


class ConnectionPopover(QWidget):
    """Compact anchored menu of actions for the current connection state."""

    def __init__(self, parent: QWidget, fonts, bundle_dir: str):
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("connectionPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(220)

        root = QVBoxLayout(self)
        # Hidden menu rows must not retain the previous window minimum height.
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.setContentsMargins(10, 8, 10, 14)
        root.setSpacing(0)

        panel = QFrame(self)
        panel.setObjectName("connectionSurface")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(32, 36, 44, 20))
        panel.setGraphicsEffect(shadow)
        self._shadow = shadow

        panel_layout = QVBoxLayout(panel)
        self._menu_layout = panel_layout
        panel_layout.setContentsMargins(4, 4, 4, 4)
        panel_layout.setSpacing(0)

        self.status_detail = QLabel(panel)
        self.status_detail.setObjectName("connectionDetail")
        self.status_detail.setFont(fonts["small"])
        self.status_detail.setWordWrap(True)
        self.status_detail.setContentsMargins(12, 6, 12, 8)
        panel_layout.addWidget(self.status_detail)

        action_font = QFont(fonts["button"])
        self.open_anki_button = QPushButton("↗  打开 Anki", panel)
        self.reconnect_button = QPushButton("↻  重新检测连接", panel)
        for button in (self.open_anki_button, self.reconnect_button):
            button.setObjectName("connectionMenuItem")
            button.setFont(action_font)
            button.setFixedHeight(40)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            panel_layout.addWidget(button)

        root.addWidget(panel)

        self._caret = QLabel(self)
        self._caret.setObjectName("connectionCaret")
        self._caret.setFixedSize(QSize(18, 10))
        self._caret.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        caret_icon = QIcon(os.path.join(bundle_dir, "ui", "icons", "popover-caret.svg"))
        if not caret_icon.isNull():
            self._caret.setPixmap(caret_icon.pixmap(QSize(18, 10)))
        else:
            self._caret.hide()

        self.set_connection("Anki 连接中…", "info")

    def set_connection(self, text: str, level: str) -> None:
        level = level if level in {"ok", "warn", "info"} else "info"
        detail = "AnkiConnect 响应异常，请重新检测" if level == "warn" and "异常" in text else ""
        self.status_detail.setText(detail)
        self.status_detail.setVisible(bool(detail))
        self.open_anki_button.setVisible(level == "warn")
        self.reconnect_button.setEnabled(level != "info")
        self.reconnect_button.setText(
            ("正在启动 Anki…" if "启动" in text else "正在检测…")
            if level == "info" else "↻  重新检测连接"
        )
        self._menu_layout.invalidate()
        self._menu_layout.activate()
        self.layout().invalidate()
        self.adjustSize()
        if self.isVisible():
            self.show_anchored(self.parentWidget().connection_button)

    def show_anchored(self, anchor: QWidget) -> None:
        self.adjustSize()
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        bottom_right = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height()))
        screen = QApplication.screenAt(bottom_right) or QApplication.primaryScreen()

        x = top_left.x()
        y = bottom_right.y() + 8
        if screen is not None:
            available = screen.availableGeometry()
            if x + self.width() > available.right() + 1:
                x = available.right() - self.width() + 1 - 8
            if y + self.height() > available.bottom() + 1:
                y = top_left.y() - self.height() - 8
            x = max(available.left() + 8, x)
            y = max(available.top() + 8, y)

        self.move(x, y)
        self.show()
        self.raise_()

        shown_below = self.y() >= bottom_right.y()
        pixmap = self._caret.pixmap()
        if shown_below and pixmap is not None and not pixmap.isNull():
            anchor_center_x = top_left.x() + anchor.width() // 2
            local_center_x = anchor_center_x - self.x()
            caret_x = max(
                18,
                min(
                    local_center_x - self._caret.width() // 2,
                    self.width() - self._caret.width() - 18,
                ),
            )
            self._caret.move(caret_x, 1)
            self._caret.show()
            self._caret.raise_()
        else:
            self._caret.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        callback = getattr(parent, "_set_connection_open_state", None)
        if callback is not None:
            callback(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        parent = self.parentWidget()
        callback = getattr(parent, "_set_connection_open_state", None)
        if callback is not None:
            callback(False)


class MainWindow(QMainWindow):
    """Qt view implementing the controller's small view protocol."""

    def __init__(self, checker, state: dict, save_state: Callable[..., None], bundle_dir: str, executor=None, parent=None):
        super().__init__(parent)
        # A combo rollout can finish after its owner hides and resurrect the popup.
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self._save_state = save_state
        self._state = state
        saved_anki_executable = state.get("anki_executable")
        self._anki_executable = (
            saved_anki_executable.strip()
            if isinstance(saved_anki_executable, str) and saved_anki_executable.strip()
            else ""
        )
        self._anki_launch_pending = False
        self._bundle_dir = bundle_dir
        app_icon_path = os.path.join(bundle_dir, "assets", "anki-quick-add.ico")
        app_icon = QIcon(app_icon_path)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self._closing = False
        self.floating = None
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
        saved_image_prompt = state.get(IMAGE_FORMAT_PROMPT_STATE_KEY)
        self.image_format_prompt = (
            saved_image_prompt if isinstance(saved_image_prompt, str)
            else IMAGE_FORMAT_PROMPT
        )
        self.format_prompts = {
            "text": self.format_prompt,
            "image": self.image_format_prompt,
        }
        saved_mode = state.get(FORMAT_PROMPT_MODE_STATE_KEY)
        self.format_prompt_mode = saved_mode if saved_mode in ("text", "image") else "text"
        self._programmatic = 0
        self._image_attachment: ImageAttachment | None = None
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
        self.floating = FloatingCapture(self)

        # Mirror floating mode's clipboard capture while the main window is
        # visible. Qt's signal offers immediate updates; polling covers the
        # occasional missed external clipboard notification on Windows.
        self._main_clipboard_last_text = QApplication.clipboard().text()
        self._main_clipboard_timer = QTimer(self)
        self._main_clipboard_timer.setInterval(400)
        self._main_clipboard_timer.timeout.connect(self._capture_main_clipboard)

        app = QApplication.instance()
        if app is not None:
            app.clipboard().dataChanged.connect(self._capture_main_clipboard)
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
        self.connection_popover = self._build_connection_popover()

        outer.addSpacing(GAP_STATUS_TO_HEADER)
        header = QWidget(root)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self.paste_title = QLabel("制卡内容", header)
        self.paste_title.setObjectName("sectionTitle")
        self.paste_title.setFont(self.fonts["cn_medium"])
        header_layout.addWidget(self.paste_title, 1, Qt.AlignmentFlag.AlignVCenter)
        icon_dir = os.path.join(self._bundle_dir, "ui", "icons")
        toolbar_font = QFont(self.fonts["button"])
        toolbar_font.setPixelSize(14)
        self.format_button = PromptButton(
            "制卡提示词", toolbar_font,
            os.path.join(icon_dir, "chevron-down-muted.svg"),
            os.path.join(icon_dir, "chevron-up-muted.svg"), header,
        )
        self.format_button.setMinimumWidth(110)
        self.format_button.setFixedHeight(36)
        self.format_button.clicked.connect(self.toggle_format)

        self.floating_button = QPushButton("悬浮制卡", header)
        self.floating_button.setObjectName("floatingModeButton")
        self.floating_button.setFont(toolbar_font)
        self.floating_button.setFixedHeight(36)
        self.floating_button.setIcon(QIcon(os.path.join(icon_dir, "floating-mode.svg")))
        self.floating_button.setIconSize(QSize(16, 16))
        self.floating_button.setToolTip("切换到悬浮小球，自动监听复制的词卡 JSON")
        self.floating_button.clicked.connect(lambda: self.floating.enable())
        header_layout.addWidget(self.floating_button)
        header_layout.addSpacing(8)
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
        self.preview_editable_hint = preview_editable
        preview_head_layout.addStretch(1)

        # Compact toolbar: an optional thumbnail without stealing Back-editor height.
        self.image_thumb = QPushButton(preview_head)
        self.image_thumb.setObjectName("imagePreviewButton")
        self.image_thumb.setFixedSize(32, 32)
        self.image_thumb.setStyleSheet(
            "QPushButton#imagePreviewButton { background: transparent; border: none; padding: 0px;"
            " min-height: 32px; max-height: 32px; min-width: 32px; max-width: 32px; }"
            "QPushButton#imagePreviewButton:hover { background: #EDF2FB; border-radius: 5px; }"
        )
        self.image_thumb.setAccessibleName("查看图片大图")
        self.image_thumb.setToolTip("点击查看图片大图")
        self.image_thumb.clicked.connect(self._show_image_preview)
        self.image_thumb.setVisible(False)
        preview_head_layout.addWidget(self.image_thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        # Secondary image actions share one split surface, not two full buttons.
        # Main segment: paste immediately. Arrow: choose an image file instead.
        self.image_remove_button = QPushButton("移除", preview_head)
        self.image_remove_button.setObjectName("imageRemoveAction")
        self.image_remove_button.setFont(toolbar_font)
        self.image_remove_button.setFixedHeight(32)
        self.image_remove_button.setToolTip("移除图片附件，不清空文字草稿")
        self.image_remove_button.clicked.connect(self._clear_image)
        self.image_remove_button.setVisible(False)
        preview_head_layout.addWidget(self.image_remove_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.image_action_group = QFrame(preview_head)
        self.image_action_group.setObjectName("imageSplitGroup")
        self.image_action_group.setFixedHeight(36)
        image_row = QHBoxLayout(self.image_action_group)
        image_row.setContentsMargins(1, 1, 1, 1)
        image_row.setSpacing(0)

        icon_dir = os.path.join(self._bundle_dir, "ui", "icons")
        self.image_paste_button = QPushButton("粘贴图片", self.image_action_group)
        self.image_paste_button.setObjectName("imageSplitPaste")
        self.image_paste_button.setFont(toolbar_font)
        self.image_paste_button.setIcon(QIcon(os.path.join(icon_dir, "clipboard-muted.svg")))
        self.image_paste_button.setIconSize(QSize(16, 16))
        self.image_paste_button.setFixedHeight(34)
        self.image_paste_button.setToolTip("从剪贴板粘贴图片（不会替换词卡文字）")
        self.image_paste_button.clicked.connect(self._paste_image_from_clipboard)
        image_row.addWidget(self.image_paste_button)

        image_divider = QFrame(self.image_action_group)
        image_divider.setObjectName("imageSplitDivider")
        image_divider.setFixedSize(1, 20)
        image_row.addWidget(image_divider)

        self.image_source_button = QToolButton(self.image_action_group)
        self.image_source_button.setObjectName("imageSplitMenu")
        self.image_source_button.setFixedSize(32, 34)
        self.image_source_button.setIcon(QIcon(os.path.join(icon_dir, "chevron-down-muted.svg")))
        self.image_source_button.setIconSize(QSize(12, 12))
        self.image_source_button.setAccessibleName("选择图片来源")
        self.image_source_button.setToolTip("更多图片来源")
        self.image_source_menu = QMenu(self.image_source_button)
        self.image_source_menu.setObjectName("imageSourceMenu")
        self.image_file_action = self.image_source_menu.addAction("选择本地图片…")
        self.image_file_action.triggered.connect(self._choose_image)
        self.image_source_button.setMenu(self.image_source_menu)
        self.image_source_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        image_row.addWidget(self.image_source_button)

        preview_head_layout.addWidget(
            self.image_action_group, 0, Qt.AlignmentFlag.AlignVCenter
        )
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

        self._wire_editor(self.paste_box, self._on_paste_changed)
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
        icon_dir = os.path.join(self._bundle_dir, "ui", "icons")
        self.connection_button = ConnectionButton(
            self.fonts["cn"],
            os.path.join(icon_dir, "chevron-down-muted.svg"),
            os.path.join(icon_dir, "chevron-up.svg"),
            self,
        )
        self.connection_button.setFixedHeight(STATUS_BADGE_HEIGHT)
        self.connection_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.connection_button.clicked.connect(self.toggle_connection)
        outer.addWidget(self.connection_button, 0, Qt.AlignmentFlag.AlignLeft)

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

    def _build_connection_popover(self) -> ConnectionPopover:
        popover = ConnectionPopover(self, self.fonts, self._bundle_dir)
        popover.reconnect_button.clicked.connect(popover.hide)
        popover.reconnect_button.clicked.connect(self.controller.retry_connection)
        popover.open_anki_button.clicked.connect(popover.hide)
        popover.open_anki_button.clicked.connect(self.open_anki)
        return popover

    def _build_prompt_popover(self) -> PromptPopover:
        popover = PromptPopover(
            self, self.fonts,
            self.format_prompts[self.format_prompt_mode],
            self.format_prompt_mode,
        )
        popover.prompt_box.textChanged.connect(self._schedule_format_prompt_save)
        for mode, button in popover.mode_buttons.items():
            button.clicked.connect(
                lambda _checked=False, key=mode: self._switch_format_prompt_mode(key)
            )
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

    def _set_connection_open_state(self, opened: bool) -> None:
        self.connection_button.set_open(opened)

    def toggle_connection(self) -> None:
        if self.connection_popover.isVisible():
            self.connection_popover.hide()
            return
        if self.prompt_popover.isVisible():
            self.prompt_popover.hide()
        self.show_connection_popover()

    def show_connection_popover(self) -> None:
        self.connection_popover.show_anchored(self.connection_button)

    def toggle_format(self) -> None:
        if self.prompt_popover.isVisible():
            self.prompt_popover.hide()
        else:
            if self.connection_popover.isVisible():
                self.connection_popover.hide()
            self.show_prompt_popover()

    def show_prompt_popover(self) -> None:
        self.prompt_popover.show_anchored(self.format_button)

    def _choose_anki_executable(self) -> str | None:
        start_dir = os.path.dirname(self._anki_executable) if self._anki_executable else ""
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择 Anki 程序",
            start_dir,
            "Anki (anki.exe);;应用程序 (*.exe)",
        )
        if not path:
            return None
        if not is_valid_anki_executable(path):
            self.set_status("⚠ 请选择 Anki 的 anki.exe", "warn")
            return None
        return path

    def open_anki(self) -> None:
        if self._anki_launch_pending:
            return

        path = find_anki_executable(self._anki_executable)
        if path is None:
            if self._anki_executable:
                self._anki_executable = ""
                self._save_state(anki_executable="")
            path = self._choose_anki_executable()
            if path is None:
                return

        if path != self._anki_executable:
            self._anki_executable = path
            self._save_state(anki_executable=path)

        try:
            launch_anki(path)
        except AnkiLaunchError as exc:
            self.set_status(f"⚠ 无法启动 Anki：{exc}", "warn")
            return

        self._anki_launch_pending = True
        self.set_connection("正在启动 Anki…", "info")
        if self.connection_popover.isVisible():
            self.connection_popover.hide()

        # Complete this explicit user action with one delayed probe. There is
        # no background polling and no focus-triggered automatic reconnect.
        QTimer.singleShot(3500, self._probe_after_anki_launch)

    def _probe_after_anki_launch(self) -> None:
        self._anki_launch_pending = False
        self.controller.retry_connection()

    def _schedule_format_prompt_save(self) -> None:
        self._format_prompt_save_timer.start(500)

    def _persist_format_prompt(self) -> None:
        # Persist exactly the active editor, not whichever preset was active
        # when a delayed save timer was originally scheduled.
        mode = self.format_prompt_mode
        value = self.format_prompt_box.toPlainText()
        self.format_prompts[mode] = value
        if mode == "image":
            self.image_format_prompt = value
            self._save_state(image_format_prompt=value)
        else:
            self.format_prompt = value
            self._save_state(format_prompt=value)

    def _switch_format_prompt_mode(self, mode: str) -> None:
        if mode not in ("text", "image") or mode == self.format_prompt_mode:
            return
        self._format_prompt_save_timer.stop()
        self._persist_format_prompt()  # save pending edits BEFORE switching
        self.format_prompt_mode = mode
        blocker = QSignalBlocker(self.format_prompt_box)
        try:
            self.format_prompt_box.setPlainText(self.format_prompts[mode])
        finally:
            del blocker
        self.prompt_popover.set_mode(mode)
        self._save_state(format_prompt_mode=mode)

    def _reset_format_prompt(self) -> None:
        mode = self.format_prompt_mode
        template = IMAGE_FORMAT_PROMPT if mode == "image" else FORMAT_PROMPT
        self._format_prompt_save_timer.stop()
        blocker = QSignalBlocker(self.format_prompt_box)
        try:
            self.format_prompt_box.setPlainText(template)
        finally:
            del blocker
        self.format_prompts[mode] = template
        if mode == "image":
            self.image_format_prompt = template
            self._save_state(image_format_prompt=template)
        else:
            self.format_prompt = template
            self._save_state(format_prompt=template)
        self._copy_to_clipboard(template, "✓ 已恢复当前模式默认提示词")

    def _copy_to_clipboard(self, text: str, note: str) -> None:
        QApplication.clipboard().setText(text)
        self.set_status(note, "ok")

    def _copy_prompt(self) -> None:
        text = self.format_prompt_box.toPlainText().strip()
        if not text:
            self.set_status("⚠ 当前提示词为空，请先填写内容", "warn")
            return
        self._format_prompt_save_timer.stop()
        self._persist_format_prompt()
        self._copy_to_clipboard(text, "✓ 已复制当前模式提示词，可粘贴给 ChatGPT")

    # ------------------------------------------------------------------ main-window clipboard quick fill
    def _capture_main_clipboard(self) -> None:
        # Never compete with the floating queue, accept stale clipboard data
        # on window activation, or modify an in-flight Anki submission.
        if (
            self._closing
            or not self.isVisible()
            or (self.floating is not None and self.floating.enabled)
        ):
            return
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None or mime.hasImage():
            return  # Image copies are handled only by the explicit image tool.
        raw = clipboard.text()
        if raw == self._main_clipboard_last_text:
            return
        self._main_clipboard_last_text = raw
        if not raw or len(raw) > 1_000_000 or self.controller.state is State.ADDING:
            return
        try:
            card = parse_card(raw)
        except CardError:
            return  # Copying normal text never clears or corrupts a card.
        # Compare against the original pasted JSON as well as the editable
        # fields: manually corrected cards must survive re-copying the same
        # JSON with different whitespace or Markdown fencing.
        try:
            original = parse_card(self.controller.paste_text)
        except CardError:
            original = None
        if original is not None and (card.front, card.back) == (
            original.front, original.back
        ):
            return
        if (card.front, card.back) == (
            self.controller.card.front, self.controller.card.back
        ):
            return
        self.paste_box.setPlainText(raw)
        # _wire_editor delivers this through the existing parser, preview,
        # deck check and image-reset path. No card is submitted automatically.

    # ------------------------------------------------------------------ image attachments
    def _on_paste_changed(self, raw: str) -> None:
        # Preserve image-before-JSON, but never attach a previous image to a
        # newly pasted draft. Direct Front/Back edits do not clear attachments.
        previous = self.controller.paste_text
        had_text_draft = bool(
            self.controller.card.front.strip() or self.controller.card.back.strip()
        )
        if self._image_attachment is not None and raw != previous and (previous.strip() or had_text_draft):
            self._clear_image()
        self.controller.paste_changed(raw)

    def _refresh_image_controls(self, preview: QImage | None = None) -> None:
        attached = self._image_attachment is not None
        if attached and preview is not None:
            pixmap = QPixmap.fromImage(preview)
            self.image_thumb.setIcon(QIcon(pixmap))
            self.image_thumb.setIconSize(QSize(30, 30))
        if not attached:
            self.image_thumb.setIcon(QIcon())
        self.image_thumb.setVisible(attached)
        self.image_remove_button.setVisible(attached)
        # The preview heading stays readable at the 500px minimum window width.
        # The editability hint is redundant once a picture thumbnail is shown.
        self.preview_editable_hint.setVisible(not attached)
        if attached:
            attachment = self._image_attachment
            self.image_thumb.setToolTip(
                f"图片已附加到背面：{attachment.width}×{attachment.height} · "
                f"{len(attachment.png) / 1024:.0f} KB"
            )

    def _show_image_preview(self) -> None:
        if self._image_attachment is None:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(self._image_attachment.png, "PNG"):
            self.set_status("⚠ 图片预览失败：图片数据无法解码", "warn")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("卡片图片预览")
        area = QApplication.primaryScreen().availableGeometry()
        target = QSize(min(680, int(area.width() * 0.8)), min(460, int(area.height() * 0.7)))
        label = QLabel(dialog)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pixmap.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(label)
        dialog.exec()

    def _clear_image(self) -> None:
        self._image_attachment = None
        self._refresh_image_controls()

    def _attach_image(self, image: QImage) -> None:
        if image.isNull():
            raise ValueError("剪贴板或文件中没有可用图片")
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError("图片像素数量超过限制（2500 万像素）")
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise ValueError("无法编码图片")
        if not image.save(buffer, "PNG"):
            raise ValueError("图片转换为 PNG 失败")
        data = bytes(buffer.data())
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片过大（PNG 超过 10 MB）")
        self._image_attachment = ImageAttachment(data, width, height)
        self._refresh_image_controls(image)
        self.set_status("✓ 图片已附加到背面，添加到 Anki 时一并提交", "ok")

    def _paste_image_from_clipboard(self) -> None:
        if self.controller.state is State.ADDING:
            return
        clipboard = QApplication.clipboard()
        if not clipboard.mimeData().hasImage():
            self.set_status("⚠ 剪贴板没有可用的图片，请先点击 ChatGPT 图片的复制按钮", "warn")
            return
        try:
            self._attach_image(clipboard.image())
        except (ValueError, OSError) as exc:
            self.set_status(f"⚠ 图片导入失败：{exc}", "warn")

    def _choose_image(self) -> None:
        if self.controller.state is State.ADDING:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择卡片配图", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)",
        )
        if not path:
            return
        try:
            if os.path.getsize(path) > 15 * 1024 * 1024:
                raise ValueError("图片源文件超过 15 MB")
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() * size.height() > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素数量超过限制（2500 万像素）")
            image = reader.read()
            if image.isNull():
                raise ValueError(reader.errorString() or "不支持此图片格式")
            self._attach_image(image)
        except (ValueError, OSError) as exc:
            self.set_status(f"⚠ 图片导入失败：{exc}", "warn")

    def attachment_for_add(self) -> ImageAttachment | None:
        # The controller is shared with floating mode, which remains text-only.
        if self.floating is not None and self.floating.enabled:
            return None
        return self._image_attachment

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
        self.connection_button.set_connection(text, level)
        if hasattr(self, "connection_popover"):
            self.connection_popover.set_connection(text, level)
        # The floating status can fall back to the main connection badge when
        # there is no card. A connection-only retry does not necessarily call
        # set_enabled(), so refresh the floating view here as well.
        if self.floating is not None:
            self.floating.sync()

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
        self._clear_image()

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
        # Choosing another deck is ordinary navigation, not a warning.
        self.deck_label.setText("牌组")
        self.deck_label.setStyleSheet("")
        # Keep the destination in the submit label as an unobtrusive cue.
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
        if not self.isVisible():
            return
        self.paste_box.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, lambda: self.paste_box.setFocus(Qt.FocusReason.OtherFocusReason) if self.isVisible() else None)

    def set_enabled(self, add_enabled: bool, editable: bool) -> None:
        self.add_button.setEnabled(add_enabled)
        for button in (self.image_paste_button, self.image_source_button, self.image_remove_button):
            button.setEnabled(editable)
        for editor in (self.paste_box, self.front_box, self.back_box):
            editor.setReadOnly(not editable)
        self.deck_box.setEnabled(editable)
        if self.floating is not None:
            self.floating.sync()

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Old content copied while we were hidden should not be mistaken for a
        # newly copied card (notably when returning from floating mode).
        self._main_clipboard_last_text = QApplication.clipboard().text()
        self._main_clipboard_timer.start()

    def hideEvent(self, event) -> None:
        if hasattr(self, "deck_box"):
            self.deck_box.hidePopup()
        self._main_clipboard_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.deck_box.hidePopup()
        self._closing = True
        self._main_clipboard_timer.stop()
        QApplication.clipboard().dataChanged.disconnect(self._capture_main_clipboard)
        try:
            self._format_prompt_save_timer.stop()
            self._persist_format_prompt()
            self._save_state(
                geometry=self._geometry_string(),
                deck=self.controller.deck,
                format_prompt=self.format_prompt,
                image_format_prompt=self.image_format_prompt,
                format_prompt_mode=self.format_prompt_mode,
            )
        except Exception:
            log.exception("保存窗口状态失败")
        self.prompt_popover.hide()
        self.connection_popover.hide()
        if self.floating is not None:
            self.floating.shutdown()
        self.executor.shutdown()
        event.accept()
        QApplication.instance().quit()
