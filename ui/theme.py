# -*- coding: utf-8 -*-
"""Centralized Qt theme tokens and font loading.

The Qt view intentionally delegates ordinary borders, radii, focus rings and
button states to Qt's widget/style system.  No application-owned border
painting is used.
"""
from __future__ import annotations

import logging
import os

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

log = logging.getLogger("aqa")

# Palette -----------------------------------------------------------------
BG = "#F7F8FA"
SURFACE = "#FFFFFF"
INPUT_BG = "#FFFFFF"
CODE_BG = "#F8FAFE"
BORDER = "#DCE1E8"
PANEL_SHADOW = "#E6E9EE"
DIVIDER = "#E8EBF0"
TEXT = "#20242C"
MUTED = "#717784"
FAINT = "#969DAA"
PLACEHOLDER = "#969DAA"
PRIMARY = "#3F73E8"
PRIMARY_HOVER = "#3567D6"
PRIMARY_PRESSED = "#2E5BC2"
PRIMARY_TEXT = "#FFFFFF"
ADD_BUTTON = "#4F8FF7"
ADD_BUTTON_HOVER = "#3F82EE"
ADD_BUTTON_PRESSED = "#3476DC"
NEUTRAL_BG = "#FFFFFF"
DISABLED_BG = "#DDE2EA"
DISABLED_TEXT = "#8A919E"
SELECTION_BG = "#DDE8FF"
SCROLLBAR = "#C4C4C4"
SCROLLBAR_HOVER = "#A8A8A8"
OK = "#36A86B"
OFFLINE_DOT = "#A5A29D"
WARN = "#F05C48"
CONNECTING = "#E49A32"
CONNECTED_BG = "#EDF8F1"
DISCONNECTED_BG = "#F2F1EE"
CONNECTING_BG = "#FFF6E8"
FRONT_ACCENT = "#4678F5"
BACK_ACCENT = "#49C7BE"
FORMAT_SURFACE = "#FFFFFF"
FOCUS = "#7598EA"

# Layout ------------------------------------------------------------------
PAGE_SIDE = 30
PAGE_TOP = 20
PAGE_BOTTOM = 20
GAP_SECTION = 26
# The connection badge sits closer to the first section than later sections.
GAP_STATUS_TO_HEADER = 10
GAP_LABEL = 10
GAP_TIGHT = 8
GAP_PANEL = 14
TEXT_PAD_X = 14
TEXT_PAD_Y = 10
PREVIEW_PAD_X = 12
PREVIEW_PAD_Y = 10
PREVIEW_MIN_BACK = 86
PANEL_RADIUS = 10
CONTROL_RADIUS = 8
BUTTON_RADIUS = 8
ACTION_PANEL_HEIGHT = 46
STATUS_BADGE_HEIGHT = 40
STATUS_BADGE_PAD_X = 14
STATUS_BADGE_DOT_SIZE = 11
STATUS_BADGE_DOT_GAP = 8
# Keep the preview usable while allowing the current compact minimum window.
PREVIEW_MIN_GROUP = 270
PROMPT_POPOVER_WIDTH = 410
PROMPT_POPOVER_HEIGHT = 360

# These files are fetched from pinned upstream releases by tools/build_windows.ps1
# and bundled by PyInstaller.  Source runs still degrade gracefully to installed
# system fonts when the files have not been fetched yet.
BUNDLED_FONT_FILES = (
    "Inter-VF.ttf",
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Medium.otf",
    "NotoSansJP-VF.ttf",
)

FONT_CHAIN = {
    "cn": ["Source Han Sans SC", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI"],
    "cn_medium": ["Source Han Sans SC", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI"],
    "section": ["Source Han Sans SC", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI"],
    "button": ["Source Han Sans SC", "Noto Sans SC", "Segoe UI Variable", "Segoe UI"],
    "jp": ["Noto Sans JP", "Yu Gothic UI", "Meiryo", "Segoe UI"],
    "latin": ["Inter", "Segoe UI Variable", "Segoe UI", "Tahoma"],
    "mono": ["Consolas", "Cascadia Mono", "Courier New"],
    "front": ["Noto Sans JP", "Yu Gothic UI", "Meiryo", "Segoe UI"],
    "back": ["Source Han Sans SC", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI"],
    "small": ["Source Han Sans SC", "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI"],
}

SIZES = {
    "cn": 12,
    "cn_medium": 15,
    "section": 14,
    "button": 12,
    "jp": 12,
    "latin": 10,
    "mono": 10,
    "front": 18,
    "back": 12,
    "small": 11,
}


ROLE_BUNDLED_FONT = {
    "cn": "SourceHanSansSC-Regular.otf",
    "cn_medium": "SourceHanSansSC-Medium.otf",
    "section": "SourceHanSansSC-Medium.otf",
    "button": "SourceHanSansSC-Regular.otf",
    "back": "SourceHanSansSC-Regular.otf",
    "small": "SourceHanSansSC-Regular.otf",
    "jp": "NotoSansJP-VF.ttf",
    "front": "NotoSansJP-VF.ttf",
    "latin": "Inter-VF.ttf",
}


def _load_bundled_fonts(bundle_dir: str) -> tuple[list[str], dict[str, list[str]]]:
    notes: list[str] = []
    loaded: dict[str, list[str]] = {}
    for filename in BUNDLED_FONT_FILES:
        path = os.path.join(bundle_dir, "fonts", filename)
        if not os.path.isfile(path):
            notes.append(f"{filename}=missing")
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            notes.append(f"{filename}=load-failed")
            continue
        families = list(QFontDatabase.applicationFontFamilies(font_id))
        loaded[filename] = families
        notes.append(f"{filename}=" + ",".join(families))
    return notes, loaded


def _dedupe_families(families: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for family in families:
        key = family.casefold()
        if not family or key in seen:
            continue
        seen.add(key)
        result.append(family)
    return result


def load_fonts(bundle_dir: str) -> tuple[dict[str, QFont], list[str]]:
    """Load bundled fonts privately and log the family Qt actually resolves.

    The Windows build downloads pinned open-source TTFs before PyInstaller runs,
    so a packaged EXE does not depend on machine-installed CJK fonts.  When
    running directly from a source checkout without fetched fonts, the explicit
    fallback chain keeps the application usable and the log makes that fallback
    observable instead of silent.
    """
    notes, loaded_families = _load_bundled_fonts(bundle_dir)

    values: dict[str, QFont] = {}
    for role, fallback_families in FONT_CHAIN.items():
        bundled_file = ROLE_BUNDLED_FONT.get(role)
        bundled_families = loaded_families.get(bundled_file, []) if bundled_file else []
        families = _dedupe_families([*bundled_families, *fallback_families])

        font = QFont()
        font.setFamilies(families)
        font.setPointSize(SIZES[role])
        # Use real static Source Han Sans faces instead of asking Qt to
        # synthesize an intermediate weight from one variable face. This is
        # important for even CJK stem weight on Windows at small UI sizes.
        if role in {"cn_medium", "section"}:
            font.setWeight(QFont.Weight.Medium)
        else:
            font.setWeight(QFont.Weight.Normal)
        values[role] = font

    for role, font in values.items():
        info = QFontInfo(font)
        notes.append(
            f"role:{role}=family:{info.family()},size:{info.pointSizeF():g},weight:{int(font.weight())}"
        )
    log.info("字体解析: %s", " | ".join(notes))
    return values, notes


def application_stylesheet() -> str:
    """Return the complete widget stylesheet."""
    return f"""
QWidget#rootWidget {{
    background: {BG};
    color: {TEXT};
}}
QWidget#promptPopover {{
    background: transparent;
    color: {TEXT};
}}

QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel#sectionTitle {{
    color: {TEXT};
}}
QLabel#promptButtonText {{
    color: {PRIMARY};
    background: transparent;
    border: none;
}}
QLabel#mutedLabel, QLabel#hintLabel, QLabel#statusLabel {{
    color: {MUTED};
}}
QLabel#placeholderLabel {{
    color: {PLACEHOLDER};
}}

QFrame#statusBadge {{
    background: {DISCONNECTED_BG};
    border: 1px solid {DISCONNECTED_BG};
    border-radius: {STATUS_BADGE_HEIGHT // 2}px;
}}
QFrame#statusBadge[level="ok"] {{
    background: {CONNECTED_BG};
    border-color: {CONNECTED_BG};
}}
QFrame#statusBadge[level="info"] {{
    background: {CONNECTING_BG};
    border-color: {CONNECTING_BG};
}}
QFrame#statusDot {{
    background: {OFFLINE_DOT};
    border: none;
    border-radius: {STATUS_BADGE_DOT_SIZE // 2}px;
}}
QFrame#statusDot[level="ok"] {{
    background: {OK};
}}
QFrame#statusDot[level="info"] {{
    background: {CONNECTING};
}}


QPushButton#connectionButton {{
    color: {TEXT};
    background: {DISCONNECTED_BG};
    border: 1px solid {DISCONNECTED_BG};
    border-radius: {STATUS_BADGE_HEIGHT // 2}px;
    min-height: {STATUS_BADGE_HEIGHT}px;
    max-height: {STATUS_BADGE_HEIGHT}px;
    padding: 0px;
}}
QPushButton#connectionButton[level="ok"] {{
    background: {CONNECTED_BG};
    border-color: {CONNECTED_BG};
}}
QPushButton#connectionButton[level="info"] {{
    background: {CONNECTING_BG};
    border-color: {CONNECTING_BG};
}}
QPushButton#connectionButton:hover {{
    border-color: {BORDER};
}}
QPushButton#connectionButton:pressed {{
    border-color: {FOCUS};
}}
QLabel#connectionButtonText {{
    color: {TEXT};
}}
QWidget#connectionPopover {{
    background: transparent;
    color: {TEXT};
}}
QFrame#connectionSurface {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {PANEL_RADIUS}px;
}}
QLabel#connectionDetail {{
    color: {MUTED};
}}


QPushButton#connectionActionButton {{
    color: {TEXT};
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px 8px;
    text-align: left;
}}
QPushButton#connectionActionButton:hover {{
    background: #F5F6F8;
}}
QPushButton#connectionActionButton:pressed {{
    background: #ECEEF2;
}}
QPushButton#connectionActionButton:disabled {{
    color: {DISABLED_TEXT};
    background: transparent;
}}

QFrame#pasteSurface, QFrame#deckSurface {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {CONTROL_RADIUS}px;
}}
QFrame#previewSurface, QFrame#promptSurface {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {PANEL_RADIUS}px;
}}
QFrame#pasteSurface[focused="true"], QFrame#previewSurface[focused="true"],
QFrame#deckSurface[focused="true"] {{
    border-color: {FOCUS};
}}
QFrame#promptSurface {{
    background: {FORMAT_SURFACE};
    border-color: {BORDER};
}}
QFrame#divider {{
    background: {DIVIDER};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}
QFrame#accentFront {{
    background: {FRONT_ACCENT};
    border: none;
    border-radius: 3px;
}}
QFrame#accentBack {{
    background: {BACK_ACCENT};
    border: none;
    border-radius: 3px;
}}

QTextEdit {{
    background: transparent;
    color: {TEXT};
    border: none;
    outline: none;
    selection-background-color: {SELECTION_BG};
    selection-color: {TEXT};
    padding: 0px;
}}
QTextEdit#pasteEdit {{
    padding: {TEXT_PAD_Y}px {TEXT_PAD_X}px;
}}
QTextEdit#frontEdit {{
    padding: 2px 0px;
}}
QTextEdit#backEdit {{
    padding: 2px 0px;
}}
QTextEdit#promptEdit {{
    background: {INPUT_BG};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: {TEXT_PAD_Y}px {TEXT_PAD_X}px;
}}
QAbstractScrollArea::corner {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px 1px 2px 0px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {SCROLLBAR};
    min-height: 28px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {SCROLLBAR_HOVER};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
    height: 0px;
}}

QPushButton {{
    color: {TEXT};
    background: {NEUTRAL_BG};
    border: 1px solid {BORDER};
    border-radius: {BUTTON_RADIUS}px;
    padding: 0px 14px;
    min-height: 38px;
}}
QPushButton:hover {{
    background: #F5F6F8;
}}
QPushButton:pressed {{
    background: #ECEEF2;
}}
QPushButton:disabled {{
    color: {DISABLED_TEXT};
    background: {DISABLED_BG};
    border-color: {DISABLED_BG};
}}
QPushButton#promptButton {{
    color: {PRIMARY};
    background: {SURFACE};
    border-color: #D5DFF4;
    padding: 0px;
    min-height: 40px;
    max-height: 40px;
}}
QPushButton#promptButton:hover {{
    background: #F5F8FE;
    border-color: #B8C9EF;
}}
QPushButton#promptButton:pressed {{
    background: #EAF0FD;
}}
QPushButton#primaryButton {{
    color: {PRIMARY_TEXT};
    background: {ADD_BUTTON};
    border-color: {ADD_BUTTON};
    padding: 0px 18px;
}}
QPushButton#primaryButton:hover {{
    background: {ADD_BUTTON_HOVER};
    border-color: {ADD_BUTTON_HOVER};
}}
QPushButton#primaryButton:pressed {{
    background: {ADD_BUTTON_PRESSED};
    border-color: {ADD_BUTTON_PRESSED};
    padding-top: 1px;
    padding-bottom: 0px;
}}
QPushButton#primaryButton:disabled {{
    color: {DISABLED_TEXT};
    background: {DISABLED_BG};
    border-color: {DISABLED_BG};
}}
QPushButton#linkButton {{
    color: {PRIMARY};
    background: transparent;
    border: none;
    border-radius: 5px;
    min-height: 28px;
    padding: 0px 6px;
}}
QPushButton#linkButton:hover {{
    color: {TEXT};
    background: #EAF0FD;
}}
QPushButton#linkButton:pressed {{
    background: #DDE8FF;
}}

QComboBox#deckCombo {{
    color: {TEXT};
    background: transparent;
    border: none;
    padding: 0px 10px 0px 0px;
    min-height: 40px;
}}
QComboBox#deckCombo:disabled {{
    color: {DISABLED_TEXT};
}}
QComboBox#deckCombo::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox#deckCombo QAbstractItemView {{
    color: {TEXT};
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {SELECTION_BG};
    selection-color: {TEXT};
    padding: 4px;
}}

QToolTip {{
    color: {TEXT};
    background: {SURFACE};
    border: 1px solid {BORDER};
}}
"""