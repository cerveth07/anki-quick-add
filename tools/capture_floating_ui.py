"""Capture actual floating widgets; the backend is a fake and writes no notes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from verify_floating_behavior import APP, Executor, FakeChecker, raw
from ui.main_window import MainWindow


def main():
    output = ROOT / "artifacts" / "floating-ui"
    output.mkdir(parents=True, exist_ok=True)
    extra_font = os.environ.get("AQA_PREVIEW_CN_FONT")
    if extra_font:
        QFontDatabase.addApplicationFont(extra_font)
    APP.clipboard().setText("")
    window = MainWindow(FakeChecker(), {}, lambda **_values: None, str(ROOT), executor=Executor())
    window.controller._schedule = lambda _delay, callback: callback()
    window.controller.startup()
    floating = window.floating
    floating.enable()
    APP.processEvents()
    idle = floating.orb.grab()
    APP.clipboard().setText(raw())
    APP.processEvents()
    pending_panel = floating.panel.grab()
    pending_orb = floating.orb.grab()
    pending_panel.save(str(output / "02-preview.png"))
    idle.save(str(output / "01-orb.png"))
    floating.submit()
    APP.processEvents()
    success = floating.toast.grab()
    success.save(str(output / "03-success.png"))

    image = QImage(960, 624, QImage.Format.Format_ARGB32)
    image.fill(QColor("#F1F5FA"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    family = window.fonts["cn"].family()
    painter.setFont(QFont(family, 20, QFont.Weight.DemiBold))
    painter.setPen(QColor("#253248"))
    painter.drawText(36, 50, "Anki Quick Add · 悬浮制卡")
    painter.setFont(QFont(family, 11))
    painter.setPen(QColor("#748095"))
    painter.drawText(36, 78, "实际 Qt 界面 · 浅色磨砂风格 · 复制后确认添加")
    painter.setPen(QColor("#253248"))
    painter.setFont(QFont(family, 13, QFont.Weight.DemiBold))
    painter.drawText(36, 126, "待确认")
    painter.drawPixmap(QPoint(24, 136), pending_panel)
    painter.drawPixmap(QPoint(382, 310), pending_orb)
    painter.drawText(500, 126, "小球待机")
    painter.drawPixmap(QPoint(510, 150), idle)
    painter.setFont(QFont(family, 11))
    painter.setPen(QColor("#748095"))
    painter.drawText(500, 247, "拖动移动 · 点击预览 · 右键暂停或退出")
    painter.drawText(500, 274, "普通文本不会触发；连续复制的词卡进入队列")
    painter.setFont(QFont(family, 13, QFont.Weight.DemiBold))
    painter.setPen(QColor("#253248"))
    painter.drawText(500, 354, "添加成功")
    painter.drawPixmap(QPoint(488, 370), success)
    painter.drawPixmap(QPoint(744, 379), idle)
    painter.setFont(QFont(family, 11))
    painter.setPen(QColor("#748095"))
    painter.drawText(500, 490, "成功提示显示 2 秒，然后处理下一张待确认卡片")
    painter.drawText(500, 517, "添加失败保留内容，可重连后重试")
    painter.end()
    image.save(str(output / "floating-preview.png"))
    window.close()
    print(output)


if __name__ == "__main__":
    main()
