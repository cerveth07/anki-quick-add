"""Capture the main-window image workflow using a fake Anki adapter.

No real Anki collection is modified. Output: artifacts/image-ui/main-with-picture.png.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtGui import QColor, QImage
from verify_floating_behavior import APP, Executor, FakeChecker, raw
from ui.main_window import MainWindow


def main() -> None:
    output = ROOT / "artifacts" / "image-ui"
    output.mkdir(parents=True, exist_ok=True)
    APP.clipboard().setText("")
    window = MainWindow(FakeChecker(), {}, lambda **_kw: None, str(ROOT), executor=Executor())
    window.controller._schedule = lambda _delay, callback: callback()
    window.controller.startup()
    window.resize(980, 740)
    window.show()
    window.paste_box.setPlainText(raw("振り向く", "ふりむく\n回头；转身回望"))
    image = QImage(480, 320, QImage.Format.Format_RGB32)
    image.fill(QColor("#B8C7DF"))
    APP.clipboard().setImage(image)
    window.image_paste_button.click()
    APP.processEvents()
    target = output / "main-with-picture.png"
    window.grab().save(str(target))
    window.close()
    print(target)


if __name__ == "__main__":
    main()
