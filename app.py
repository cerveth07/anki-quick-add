# -*- coding: utf-8 -*-
"""Anki Quick Add Qt entry point.

The controller, parser and Anki adapter remain the business core.  This file
owns only process/runtime concerns: Qt startup, single-instance activation,
logging and the compatible ``window.json`` state file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from anki import AnkiAdapter, DuplicateChecker
from config import load_config
from ui.main_window import (
    APP_TITLE,
    DEFAULT_GEOMETRY,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    MainWindow,
)
from ui.theme import application_stylesheet

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WINDOW_MANAGER_MAX_SIZE = 10000
GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)?([+-]\d+)?$")
# Older Qt builds serialized a negative coordinate as ``+-1920`` because they
# prefixed ``+`` unconditionally. Accept that one historical form so an
# existing multi-monitor window.json repairs itself on the next clean save.
LEGACY_SIGNED_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")


BUNDLE_DIR = os.path.abspath(getattr(sys, "_MEIPASS", APP_DIR))

log = logging.getLogger("aqa")


# --------------------------------------------------------------------------- runtime
def state_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "AnkiQuickAdd")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return APP_DIR
    return path


def setup_logging() -> str:
    path = os.path.join(state_dir(), "aqa.log")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(handler, "_aqa_file", False) for handler in root.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler._aqa_file = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    return path


def acquire_single_instance() -> tuple[object, bool]:
    """Return ``(mutex handle, already running)`` on Windows."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Global\\AnkiQuickAdd")
        return handle, kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return None, False


def activate_existing_window() -> None:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        log.exception("激活已有窗口失败")


def _state_path() -> str:
    return os.path.join(state_dir(), "window.json")


def load_state() -> dict:
    """Load compatible geometry/deck/prompt state; malformed state is ignored."""
    try:
        with open(_state_path(), "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(
    geometry: str | None = None,
    deck: str | None = None,
    format_prompt: str | None = None,
) -> None:
    data = load_state()
    if geometry and _parse_geometry(geometry) is not None:
        data["geometry"] = geometry
    if deck:
        data["deck"] = deck
    if format_prompt is not None:
        data["format_prompt"] = format_prompt
    try:
        with open(_state_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
    except OSError:
        log.exception("保存窗口状态失败")


def _parse_geometry(value: object) -> tuple[int, int, int | None, int | None] | None:
    if not isinstance(value, str):
        return None
    match = GEOMETRY_RE.fullmatch(value)
    if not match:
        match = LEGACY_SIGNED_GEOMETRY_RE.fullmatch(value)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if not (MIN_WINDOW_WIDTH <= width <= WINDOW_MANAGER_MAX_SIZE):
        return None
    if not (MIN_WINDOW_HEIGHT <= height <= WINDOW_MANAGER_MAX_SIZE):
        return None
    x = int(match.group(3)) if match.group(3) is not None else None
    y = int(match.group(4)) if match.group(4) is not None else None
    if (x is None) != (y is None):
        return None
    return width, height, x, y


def _recoverable_position(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Keep the saved size, but move windows whose title area is no longer reachable.

    Negative coordinates are legitimate in multi-monitor layouts.  A position
    is preserved whenever the upper strip of the saved window still intersects
    a current screen.  If a monitor was removed, only x/y are repaired; width
    and height are never clamped to the available work area.
    """
    screens = QApplication.screens()
    if not screens:
        return x, y

    candidate_title = QRect(x, y, max(1, width), 40)
    for screen in screens:
        available = screen.availableGeometry()
        intersection = candidate_title.intersected(available)
        if intersection.width() >= min(96, max(1, width)) and intersection.height() >= 24:
            return x, y

    primary = QApplication.primaryScreen() or screens[0]
    available = primary.availableGeometry()
    safe_x = available.left() + max(12, (available.width() - width) // 2 if width <= available.width() else 12)
    safe_y = available.top() + 12
    return safe_x, safe_y


def apply_geometry(window: MainWindow, value: str | None) -> None:
    parsed = _parse_geometry(value)
    # Migrate the previous shipped default once so existing users actually
    # receive the new landscape visual layout instead of staying at 840x1100.
    if parsed is not None and parsed[:2] == (840, 1100):
        parsed = None
    parsed = parsed or _parse_geometry(DEFAULT_GEOMETRY)
    if parsed is None:
        window.resize(840, 1100)
        return
    width, height, x, y = parsed
    if x is None or y is None:
        window.resize(width, height)
        return
    safe_x, safe_y = _recoverable_position(x, y, width, height)
    window.setGeometry(safe_x, safe_y, width, height)


# --------------------------------------------------------------------------- main
def main() -> int:
    log_path = setup_logging()
    log.info("%s 启动，日志：%s", APP_TITLE, log_path)

    _mutex, already_running = acquire_single_instance()
    if already_running:
        log.info("已有实例在运行，激活后退出")
        activate_existing_window()
        return 0

    config, warnings = load_config()
    for warning in warnings:
        log.warning("config: %s", warning)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app_icon_path = os.path.join(BUNDLE_DIR, "assets", "anki-quick-add.ico")
    app_icon = QIcon(app_icon_path)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    app.setStyleSheet(application_stylesheet())
    app.setQuitOnLastWindowClosed(True)

    checker = DuplicateChecker(AnkiAdapter(config))
    state = load_state()
    window = MainWindow(
        checker,
        state=state,
        save_state=save_state,
        bundle_dir=BUNDLE_DIR,
    )
    apply_geometry(window, state.get("geometry"))
    window.show()
    QTimer.singleShot(0, window.focus_paste)
    QTimer.singleShot(0, window._sync_focus)
    QTimer.singleShot(50, window.controller.startup)
    QTimer.singleShot(400, lambda: log.info("窗口实际 geometry=%s", window.geometry()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
