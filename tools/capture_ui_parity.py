# -*- coding: utf-8 -*-
"""Capture repeatable Qt UI parity screenshots on a Windows dev machine.

Run after installing PySide6. No Anki connection is required. Output is written
under artifacts/ui-parity/current so GUI changes stay reviewable in-repo.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import application_stylesheet


@dataclass
class FakeConfig:
    deck_name: str = ""


class FakeAdapter:
    config = FakeConfig()


class FakeChecker:
    adapter = FakeAdapter()


class NoopExecutor:
    def __call__(self, job, done):
        return None

    def shutdown(self):
        return None


def capture(widget, path: Path) -> None:
    """Capture the actual top-level window, including native frame when possible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    screen = widget.screen() or (app.primaryScreen() if app is not None else None)
    if screen is not None and widget.isWindow():
        frame = widget.frameGeometry()
        margin = 18
        image = screen.grabWindow(
            0,
            frame.x() - margin,
            frame.y() - margin,
            frame.width() + margin * 2,
            frame.height() + margin * 2,
        )
        if not image.isNull() and image.save(str(path)):
            return
    image = widget.grab()
    if not image.save(str(path)):
        raise RuntimeError(f"failed to save screenshot: {path}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(application_stylesheet())
    out = ROOT / "artifacts" / "ui-parity" / "current"

    window = MainWindow(
        FakeChecker(),
        state={},
        save_state=lambda **_kwargs: None,
        bundle_dir=str(ROOT),
        executor=NoopExecutor(),
    )
    window.resize(980, 700)
    window.show()
    window.add_button.setEnabled(True)  # visual-reference state; production logic is unchanged
    app.processEvents()
    capture(window, out / "01-empty.png")

    window.paste_box.setFocus()
    app.processEvents()
    capture(window, out / "02-paste-focus.png")

    window.front_box.setFocus()
    app.processEvents()
    capture(window, out / "03-preview-focus.png")

    window.show_prompt_popover()
    app.processEvents()
    capture(window.prompt_popover, out / "04-prompt-popover.png")
    capture(window, out / "05-acceptance.png")
    window.prompt_popover.hide()

    window.set_connection("Anki 未连接", "warn")
    app.processEvents()
    capture(window, out / "06-connection-disconnected.png")

    window.show_connection_popover()
    app.processEvents()
    capture(window, out / "07-connection-popover.png")
    window.connection_popover.hide()

    print(f"Captured UI parity screenshots to: {out}")
    QTimer.singleShot(0, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
