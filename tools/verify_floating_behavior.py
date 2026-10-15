"""Exercise floating capture against the real Qt view and a fake Anki backend."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from anki import AddResult, AnkiOfflineError, Capabilities, CheckResult
from config import Config
from controller import State
from ui.floating_window import PendingCard
from ui.main_window import MainWindow, FORMAT_PROMPT, IMAGE_FORMAT_PROMPT
from ui.theme import (
    FLOAT_CONTROL_SIZE, FLOAT_EDITOR_SIZE, FLOAT_META_SIZE,
    FLOAT_TITLE_SIZE, application_stylesheet,
)


APP = QApplication.instance() or QApplication([])
APP.setStyleSheet(application_stylesheet())
CONFIG = Config("日语", "问答题", "正面", "背面", "http://127.0.0.1:8765")


def raw(front="木漏れ日", back="こもれび\n从树叶缝隙洒下的阳光"):
    return json.dumps({"front": front, "back": back}, ensure_ascii=False)


class FakeChecker:
    def __init__(self):
        self.adapter = self
        self.config = CONFIG
        self.added = []
        self.attachments = []
        self.offline = False
        self.fail_add = False

    def verify_capabilities(self, deck):
        if self.offline:
            raise AnkiOfflineError("offline")
        return Capabilities(6, True, True, True, decks=("日语", "Other"))

    def check(self, card, deck):
        if self.offline:
            return CheckResult("OFFLINE", "未连接 Anki")
        if (card.front, deck) in self.added:
            return CheckResult("DUPLICATE", "该词已存在")
        return CheckResult("READY", "可添加")

    def add(self, card, deck, attachment=None):
        if self.fail_add:
            return AddResult("ERROR", "添加失败", deck=deck)
        self.attachments.append(attachment)
        self.added.append((card.front, deck))
        return AddResult("ADDED", "添加成功", note_id=len(self.added), deck=deck)


class Executor:
    def __init__(self):
        self.defer = False
        self.jobs = []

    def __call__(self, job, done):
        if self.defer:
            self.jobs.append((job, done))
        else:
            self.deliver(job, done)

    @staticmethod
    def deliver(job, done):
        try:
            result = job()
        except Exception as error:
            done(None, error)
        else:
            done(result, None)

    def finish(self):
        job, done = self.jobs.pop(0)
        self.deliver(job, done)

    def shutdown(self):
        pass


class MemoryClipboard:
    """Deterministic copy fixtures; real Windows transport has separate tests."""

    def __init__(self):
        self.value = ""
        # Keep the original signal so shutdown disconnects the real registration.
        self.dataChanged = APP.clipboard().dataChanged

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value


class FloatingTests(unittest.TestCase):
    def setUp(self):
        APP.clipboard().setText("")
        self.checker = FakeChecker()
        self.executor = Executor()
        self.saved = {}
        self.window = MainWindow(self.checker, {}, self.saved.update, str(ROOT), executor=self.executor)
        self.window.controller._schedule = lambda delay, callback: callback()
        self.window.controller.startup()
        self.floating = self.window.floating
        self.floating.clipboard = MemoryClipboard()
        self.floating.enable()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        APP.processEvents()

    def copy(self, value):
        self.floating.clipboard.setText(value)
        self.floating.read_clipboard()
        APP.processEvents()

    def test_main_json_fold_preserves_input_and_opens_for_errors(self):
        self.floating.open_main()
        APP.clipboard().setText(raw())
        APP.processEvents()
        self.assertFalse(self.window.paste_surface.isVisible())
        self.assertEqual(self.window.paste_summary.text(), "已解析")
        self.window.back_box.clear()
        self.assertFalse(self.window.paste_surface.isVisible())
        self.window.back_box.setPlainText("こもれび\n从树叶缝隙洒下的阳光")
        self.window.paste_toggle.click()
        self.window.paste_box.setFocus()
        self.window.paste_box.setPlainText(raw() + " ")
        self.assertTrue(self.window.paste_surface.isVisible())
        self.assertIs(APP.focusWidget(), self.window.paste_box)
        self.window.paste_toggle.click()
        self.window.paste_box.setPlainText('{"front":')
        self.assertTrue(self.window.paste_surface.isVisible())
        self.assertEqual(self.window.paste_summary.text(), "需修正")
        self.assertFalse(self.window.add_button.isEnabled())
        self.window.clear_button.click()
        self.assertEqual(self.window.paste_box.toPlainText(), "")
        self.assertTrue(self.window.paste_surface.isVisible())

    def test_main_success_notice_survives_next_card_and_expires_independently(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("上一张"))
        self.executor.defer = True
        self.window.add_button.click()
        self.assertFalse(self.window.clear_button.isEnabled())
        APP.clipboard().setText(raw("下一张"))
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertTrue(self.window.success_notice.isVisible())
        self.assertIn("上一张", self.window.success_label.toolTip())
        self.assertEqual(self.window.controller.card.front, "下一张")
        self.assertIn("可添加", self.window.status_label.toolTip())
        self.assertFalse(self.window.paste_surface.isVisible())
        self.window._success_timer.start(1)
        QTest.qWait(20)
        self.assertFalse(self.window.success_notice.isVisible())
        self.assertEqual(self.window.controller.card.front, "下一张")
        self.checker.fail_add = True
        self.window.add_button.click()
        self.assertFalse(self.window.success_notice.isVisible())
        self.assertEqual(self.window.controller.card.front, "下一张")

    def test_main_discard_button_advances_only_one_card_and_resets_final_status(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.add_button.click()
        APP.clipboard().setText(raw("二"))
        APP.clipboard().setText(raw("三"))
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.window.clear_button.text(), "丢弃当前卡")
        self.assertIn("1", self.window.queue_label.text())
        self.window.clear_button.click()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "三")
        self.assertIn("0", self.window.queue_label.text())
        self.assertTrue(self.window.queue_label.isVisible())
        self.window.clear_button.click()
        self.settle_auto()
        self.assertFalse(self.window.queue_label.isVisible())
        self.assertEqual(self.window.clear_button.text(), "清空")
        self.assertEqual(self.window.controller.card.front, "")
        self.assertEqual(self.checker.added, [("一", "日语")])

    def test_main_compact_layout_contains_long_content_at_minimum_size(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("长词" * 30, "例句和释义\n" * 100))
        self.window._submitted_front = "长词" * 100
        self.window._submitted_deck = "长牌组" * 60
        self.window._show_success()
        self.floating.active = PendingCard(
            self.window.controller.paste_text,
            (self.window.controller.card.front, self.window.controller.card.back),
        )
        self.floating.sync()
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.window._attach_image(image)
        self.window.set_deck("父牌组::" + "很长的目标牌组" * 30)
        self.window.set_status("⚠ 添加失败：" + "请检查连接和配置；" * 100, "warn")
        for size in ((500, 700), (980, 700), (980, 900)):
            for expanded in (False, True):
                with self.subTest(size=size, expanded=expanded):
                    self.window.resize(*size)
                    self.window._set_json_expanded(expanded)
                    APP.processEvents()
                    self.assertEqual(self.window.size().toTuple(), size)
                    root = self.window.centralWidget()
                    controls = [self.window.paste_toggle, self.window.preview_surface,
                                self.window.action_row, self.window.success_notice,
                                self.window.image_action_group, self.window.status_label,
                                self.window.queue_label]
                    if expanded:
                        controls.append(self.window.paste_surface)
                    for widget in controls:
                        top_left = widget.mapTo(root, QPoint(0, 0))
                        self.assertGreaterEqual(top_left.y(), 0)
                        self.assertGreaterEqual(top_left.x(), 0)
                        self.assertLessEqual(top_left.y() + widget.height(), root.height())
                        self.assertLessEqual(top_left.x() + widget.width(), root.width())
                    self.assertGreaterEqual(self.window.paste_toggle.height(), 36)
                    self.assertGreaterEqual(self.window.back_box.height(), 86)
                    for button in (self.window.image_paste_button, self.window.image_source_button):
                        self.assertTrue(self.window.image_action_group.rect().contains(button.geometry()))
                    self.assertLessEqual(self.window.add_button.fontMetrics().horizontalAdvance(
                        self.window.add_button.text()), self.window.add_button.width() - 28)
                    self.assertGreater(self.window.back_box.verticalScrollBar().maximum(), 0)
                    self.assertGreater(self.window.front_box.verticalScrollBar().maximum(), 0)
                    self.assertLess(self.window.preview_surface.geometry().bottom(),
                                    self.window.action_row.geometry().top())
                    self.assertLessEqual(self.window.success_notice.height(), 38)
        self.window.success_close_button.click()
        self.assertFalse(self.window.success_notice.isVisible())
        self.assertFalse(self.window._success_timer.isActive())

    def test_main_copies_during_add_are_processed_in_order(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.controller.add_clicked()
        APP.clipboard().setText(raw("二"))
        APP.clipboard().setText(raw("三"))
        self.assertEqual(self.window.controller.card.front, "一")
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.checker.added, [("一", "日语")])
        self.window.controller.add_clicked()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "三")
        self.assertEqual(self.checker.added, [("一", "日语"), ("二", "日语")])

    def test_reconnect_then_add_does_not_unlock_an_inflight_submission(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.connection_popover.reconnect_button.click()
        self.window.controller.add_clicked()
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertFalse(self.window.add_button.isEnabled())
        self.executor.finish()  # connection check
        self.executor.finish()  # duplicate check
        self.assertEqual(self.window.controller.state, State.READY)
        self.window.controller.add_clicked()
        self.window.connection_popover.reconnect_button.click()
        APP.clipboard().setText(raw("二"))
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertEqual(self.window.controller.state, State.ADDING)
        self.assertTrue(self.window.paste_box.isReadOnly())
        self.assertEqual(self.window.controller.card.front, "一")
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.checker.added, [("一", "日语")])

    def test_last_queued_card_remains_protected_after_edit_and_mode_switch(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.controller.add_clicked()
        APP.clipboard().setText(raw("二"))
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(len(self.floating.queue), 0)
        self.window.paste_box.setPlainText(raw("二", "人工修改"))
        self.floating.enable()
        self.floating.open_main()
        APP.clipboard().setText(raw("三"))
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.window.controller.card.back, "人工修改")
        self.assertEqual(len(self.floating.queue), 1)
        self.window.controller.clear_clicked()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "三")

    def test_image_survives_json_formatting_and_transient_parse_errors(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一", "释义"))
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.window._attach_image(image)
        attachment = self.window.attachment_for_add()
        for content in (raw("一", "释义") + " ",
                        '{ "back": "释义", "front": "一" }',
                        '```json\n' + raw("一", "释义") + '\n```',
                        '{"front":', raw("一", "释义")):
            self.window.paste_box.setPlainText(content)
            self.assertIs(self.window.attachment_for_add(), attachment)
        self.window.back_box.setPlainText("修订释义")
        self.window.paste_box.setPlainText(raw("一", "修订释义") + " ")
        self.assertIs(self.window.attachment_for_add(), attachment)
        self.window.paste_box.setPlainText('{"front":')
        self.window.paste_box.setPlainText(raw("二", "新卡"))
        self.assertIsNone(self.window.attachment_for_add())

    def test_image_is_cleared_when_json_draft_is_erased(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.window._attach_image(image)
        self.window.paste_box.clear()
        self.assertIsNone(self.window.attachment_for_add())

    def test_main_failed_add_keeps_image_and_waiting_cards_until_retry(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.window._attach_image(image)
        attachment = self.window.attachment_for_add()
        self.checker.fail_add = True
        self.executor.defer = True
        self.window.controller.add_clicked()
        APP.clipboard().setText(raw("二"))
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        APP.clipboard().setText(raw("三"))
        self.assertEqual(self.window.controller.card.front, "一")
        self.assertIs(self.window.attachment_for_add(), attachment)
        self.assertIn("2", self.window.queue_label.text())
        self.checker.fail_add = False
        self.window.controller.retry_connection()
        self.window.controller.add_clicked()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.checker.attachments, [attachment])
        self.assertIsNone(self.window.attachment_for_add())
        self.window.controller.clear_clicked()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "三")

    def test_main_deferred_copy_survives_switch_to_floating(self):
        self.floating.open_main()
        self.floating.clipboard = APP.clipboard()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.controller.add_clicked()
        APP.clipboard().setText(raw("二"))
        self.floating.enable()
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertTrue(self.floating.success_timer.isActive())
        self.floating.success_timer.stop()
        self.floating._after_success()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.floating.pending_count, 1)
        self.assertEqual(self.checker.added, [("一", "日语")])

    def test_backspace_invalid_json_does_not_open_prompt(self):
        self.floating.open_main()
        APP.clipboard().setText(raw())
        self.window.paste_toggle.click()
        editor = self.window.paste_box
        editor.setFocus()
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.INVALID)
        self.assertFalse(self.window.prompt_popover.isVisible())
        self.assertIs(APP.focusWidget(), editor)
        self.assertIn("无法解析", self.window.status_label.text())

    def test_entering_floating_does_not_reload_main_submitted_clipboard(self):
        self.floating.open_main()
        self.floating.clipboard = APP.clipboard()
        APP.clipboard().setText(raw("一"))
        self.window.controller.add_clicked()
        self.settle_auto()
        self.floating.enable()
        self.settle_auto()
        self.assertEqual(self.floating.pending_count, 0)
        self.assertEqual(self.window.controller.card.front, "")
        self.assertFalse(self.floating.panel.isVisible())

    def test_discard_then_recopy_same_card_is_allowed_without_poll_replay(self):
        self.floating.clipboard = APP.clipboard()
        APP.clipboard().setText(raw("一"))
        self.floating.ignore()
        self.settle_auto()
        self.floating.read_clipboard()
        self.assertEqual(self.floating.pending_count, 0)
        APP.clipboard().setText(raw("一"))
        self.settle_auto()
        self.assertEqual(self.floating.pending_count, 1)
        self.floating.ignore()
        self.settle_auto()
        APP.clipboard().setText("ordinary text")
        APP.clipboard().setText('```json\n' + raw("一") + '\n```')
        self.settle_auto()
        self.assertEqual(self.floating.pending_count, 1)

    def test_edit_after_reconnect_updates_badge_and_rechecks_capabilities(self):
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.copy(raw("一"))
        self.checker.offline = False
        with patch.object(self.checker, "verify_capabilities", wraps=self.checker.verify_capabilities) as verify:
            self.floating.panel.front_box.setPlainText("二")
            self.assertTrue(verify.called)
        self.assertEqual(self.window.controller.state, State.READY)
        self.assertEqual(self.window.connection_button.property("level"), "ok")
        self.assertFalse(self.floating.panel.retry_button.isVisible())

    def test_reconnect_edit_does_not_reuse_old_duplicate_check(self):
        self.copy(raw("一"))
        self.checker.offline = True
        self.window.controller.retry_connection()
        self.checker.offline = False
        self.checker.added.append(("一", "日语"))
        self.floating.panel.back_box.setPlainText("修改释义")
        self.assertEqual(self.window.controller.state, State.DUPLICATE)
        self.assertEqual(self.window.connection_button.property("level"), "ok")

    def test_reset_prompt_preserves_clipboard(self):
        self.floating.open_main()
        for mode in ("text", "image"):
            self.window._switch_format_prompt_mode(mode)
            self.window.format_prompt_box.setPlainText("custom")
            APP.clipboard().setText("keep-this")
            self.window._reset_format_prompt()
            self.assertEqual(APP.clipboard().text(), "keep-this")

    def test_main_clear_allows_explicit_recopy_but_not_poll_replay(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.window.controller.clear_clicked()
        self.window._capture_main_clipboard()
        self.assertEqual(self.window.controller.card.front, "")
        APP.clipboard().setText(raw("一"))
        self.assertEqual(self.window.controller.card.front, "一")

    def test_main_deferred_copy_deduplicates_and_recovers_by_polling(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("一"))
        self.executor.defer = True
        self.window.controller.add_clicked()
        clipboard = APP.clipboard()
        clipboard.blockSignals(True)
        try:
            clipboard.setText(raw("二"))
        finally:
            clipboard.blockSignals(False)
        self.window._capture_main_clipboard()
        clipboard.setText('```json\n' + raw("二") + '\n```')
        clipboard.setText("ordinary text")
        clipboard.setText('{"front":"incomplete"}')
        self.assertEqual(len(self.floating.queue), 1)
        self.executor.defer = False
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(len(self.floating.queue), 0)

    def test_reconnect_edit_still_blocks_invalid_anki_configuration(self):
        self.copy(raw("一"))
        self.checker.offline = True
        self.window.controller.retry_connection()
        self.checker.offline = False
        invalid = Capabilities(6, True, False, False, problem="笔记类型不存在")
        with patch.object(self.checker, "verify_capabilities", return_value=invalid):
            self.floating.panel.back_box.setPlainText("修改释义")
        self.assertEqual(self.window.controller.state, State.ERROR)
        self.assertFalse(self.floating.panel.add_button.isEnabled())
        self.assertEqual(self.checker.added, [])

    def test_state_save_preserves_previous_file_on_write_or_replace_failure(self):
        import app as runtime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window.json"
            original = '{"format_prompt":"old","custom":"preserve"}'
            path.write_text(original, encoding="utf-8")

            def partial_write(data, handle, **kwargs):
                handle.write('{"format_prompt":')
                raise OSError("simulated interrupted write")

            with patch.object(runtime, "_state_path", return_value=str(path)):
                with patch.object(runtime.json, "dump", side_effect=partial_write):
                    runtime.save_state(format_prompt="new")
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                with patch.object(runtime.os, "replace", side_effect=OSError("replace denied")):
                    runtime.save_state(format_prompt="new")
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(list(Path(directory).iterdir()), [path])
                runtime.save_state(format_prompt="new", image_format_prompt="image", format_prompt_mode="image")
                self.assertEqual(runtime.load_state(), {
                    "format_prompt": "new", "custom": "preserve",
                    "image_format_prompt": "image", "format_prompt_mode": "image",
                })

    def test_main_window_autofills_valid_clipboard_json_without_submitting(self):
        self.floating.open_main()
        clipboard = APP.clipboard()
        clipboard.setText(raw("木漏れ日", "树叶间的阳光"))
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        self.assertEqual(self.window.controller.card.back, "树叶间的阳光")
        self.assertEqual(self.window.paste_box.toPlainText(), raw("木漏れ日", "树叶间的阳光"))
        self.assertEqual(self.checker.added, [])
        clipboard.setText("一段普通文本")
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        clipboard.setText('{"front":"wrong"}')
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        clipboard.setText("ＨＥＬＬＯ")
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        clipboard.setText(chr(96)*3 + "json\n" + raw("振り向く", "回头") + "\n" + chr(96)*3)
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "振り向く")
        self.assertEqual(self.window.controller.card.back, "回头")
        self.assertEqual(self.checker.added, [])

    def test_main_clipboard_ignores_semantic_repeats_and_keeps_editor_focus(self):
        self.floating.open_main()
        clipboard = APP.clipboard()
        clipboard.setText(raw("一", "释义"))
        APP.processEvents()
        self.window.back_box.setFocus()
        self.window.back_box.setPlainText("人工补充")
        self.assertIs(APP.focusWidget(), self.window.back_box)
        original = self.window.paste_box.toPlainText()
        clipboard.setText(chr(96)*3 + "json\n" + raw("一", "释义") + "\n" + chr(96)*3)
        APP.processEvents()
        self.assertEqual(self.window.paste_box.toPlainText(), original)
        self.assertEqual(self.window.controller.card.back, "人工补充")
        self.assertIs(APP.focusWidget(), self.window.back_box)
        clipboard.setText(raw("二", "释义二"))
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertIs(APP.focusWidget(), self.window.back_box)

    def test_main_clipboard_handles_image_first_without_auto_image_capture(self):
        self.floating.open_main()
        self.window.clear_card()
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("#CCBBAA"))
        APP.clipboard().setImage(image)
        APP.processEvents()
        self.assertIsNone(self.window.attachment_for_add())
        self.window.image_paste_button.click()
        self.assertIsNotNone(self.window.attachment_for_add())
        APP.clipboard().setText(raw("猫", "ねこ"))
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "猫")
        self.assertIsNotNone(self.window.attachment_for_add())
        self.assertEqual(self.checker.added, [])

    def test_main_clipboard_is_suspended_during_floating_and_restored_on_return(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("主窗口原词"))
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "主窗口原词")
        self.floating.enable()
        APP.clipboard().setText(raw("浮动时复制"))
        APP.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.window.controller.card.front, "主窗口原词")
        self.floating.open_main()
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "主窗口原词")
        APP.clipboard().setText(raw("返回后复制"))
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "主窗口原词")
        self.assertEqual(len(self.floating.queue), 1)
        self.window.controller.clear_clicked()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "返回后复制")

    def test_deck_label_no_longer_warns_for_valid_non_default_deck(self):
        self.floating.open_main()
        self.window.set_deck("Other")
        self.assertEqual(self.window.deck_label.text(), "牌组")
        self.assertNotIn("非默认", self.window.deck_label.text())
        self.assertEqual(self.window.add_button.text(), "加入 Other")
        self.window.set_deck("日语")
        self.assertEqual(self.window.deck_label.text(), "牌组")
        self.assertEqual(self.window.add_button.text(), "加入 Anki")

    def test_prompt_presets_default_to_text_with_image_template_available(self):
        self.floating.open_main()
        popup = self.window.prompt_popover
        self.assertEqual(self.window.format_prompt_mode, "text")
        self.assertTrue(popup.mode_buttons["text"].isChecked())
        self.assertFalse(popup.mode_buttons["image"].isChecked())
        self.assertEqual(popup.prompt_box.toPlainText(), FORMAT_PROMPT)
        popup.mode_buttons["image"].click()
        self.assertEqual(self.window.format_prompt_mode, "image")
        self.assertTrue(popup.mode_buttons["image"].isChecked())
        self.assertEqual(popup.prompt_box.toPlainText(), IMAGE_FORMAT_PROMPT)
        self.assertIn("只能包含 front 和 back", IMAGE_FORMAT_PROMPT)
        self.assertIn("先生成一张独立图片", IMAGE_FORMAT_PROMPT)
        self.assertEqual(self.saved["format_prompt_mode"], "image")

    def test_prompt_presets_keep_custom_edits_and_copy_active_mode(self):
        self.floating.open_main()
        popup = self.window.prompt_popover
        text_a = "纯文本自定义提示词 A"
        image_b = "智能配图自定义提示词 B"
        popup.prompt_box.setPlainText(text_a)
        popup.mode_buttons["image"].click()
        self.assertEqual(self.saved["format_prompt"], text_a)
        popup.prompt_box.setPlainText(image_b)
        popup.mode_buttons["text"].click()
        self.assertEqual(self.saved["image_format_prompt"], image_b)
        self.assertEqual(popup.prompt_box.toPlainText(), text_a)
        self.window._copy_prompt()
        self.assertEqual(APP.clipboard().text(), text_a)
        popup.mode_buttons["image"].click()
        self.assertEqual(popup.prompt_box.toPlainText(), image_b)
        self.window._copy_prompt()
        self.assertEqual(APP.clipboard().text(), image_b)

    def test_prompt_reset_only_affects_current_preset(self):
        self.floating.open_main()
        popup = self.window.prompt_popover
        popup.prompt_box.setPlainText("纯文本预设自定义")
        popup.mode_buttons["image"].click()
        popup.prompt_box.setPlainText("智能配图预设自定义")
        popup.reset_button.click()
        self.assertEqual(popup.prompt_box.toPlainText(), IMAGE_FORMAT_PROMPT)
        self.assertEqual(self.saved["image_format_prompt"], IMAGE_FORMAT_PROMPT)
        popup.mode_buttons["text"].click()
        self.assertEqual(popup.prompt_box.toPlainText(), "纯文本预设自定义")
        self.assertEqual(self.saved["format_prompt"], "纯文本预设自定义")

    def test_prompt_persistence_restores_two_modes_without_overwriting_legacy_text(self):
        self.floating.open_main()
        saved = {}
        w = MainWindow(
            FakeChecker(),
            {"format_prompt": "旧版纯文本已编辑", "image_format_prompt": "图片自定义",
             "format_prompt_mode": "image"},
            saved.update, str(ROOT), executor=Executor(),
        )
        try:
            self.assertEqual(w.format_prompt_mode, "image")
            self.assertEqual(w.prompt_popover.prompt_box.toPlainText(), "图片自定义")
            w.prompt_popover.mode_buttons["text"].click()
            self.assertEqual(w.prompt_popover.prompt_box.toPlainText(), "旧版纯文本已编辑")
            w.prompt_popover.mode_buttons["image"].click()
            self.assertEqual(w.prompt_popover.prompt_box.toPlainText(), "图片自定义")
            w.close()
            self.assertEqual(saved["format_prompt"], "旧版纯文本已编辑")
            self.assertEqual(saved["image_format_prompt"], "图片自定义")
            self.assertEqual(saved["format_prompt_mode"], "image")
        finally:
            if not w._closing:
                w.close()

    def test_prompt_empty_copy_does_not_replace_clipboard(self):
        self.floating.open_main()
        popup = self.window.prompt_popover
        popup.prompt_box.setPlainText("")
        APP.clipboard().setText("keep-this")
        self.window._copy_prompt()
        self.assertEqual(APP.clipboard().text(), "keep-this")

    def test_prompt_popover_second_click_closes_without_reopening(self):
        self.floating.open_main()
        button = self.window.format_button
        popup = self.window.prompt_popover
        self.assertFalse(popup.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertTrue(popup.isVisible())
        # With Qt.Popup the native outside press can dismiss the popup before
        # the opener's click is replayed. The real button click must not reopen.
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        QTest.qWait(100)
        self.assertFalse(popup.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertTrue(popup.isVisible())

    def test_prompt_popup_opener_press_disables_mouse_replay_only_for_anchor(self):
        self.floating.open_main()
        popup = self.window.prompt_popover
        button = self.window.format_button
        button.click()
        APP.processEvents()
        self.assertTrue(popup.isVisible())
        # Send an outside press targeting the opener to the Popup itself.
        # Its default QWidget handler closes the popup; the handler must first
        # mark this press as not eligible for replay.
        anchor_global = button.mapToGlobal(button.rect().center())
        outside_local = popup.mapFromGlobal(anchor_global)
        self.assertFalse(popup.rect().contains(outside_local))
        QTest.mousePress(popup, Qt.MouseButton.LeftButton, pos=outside_local)
        APP.processEvents()
        self.assertFalse(popup.isVisible())
        QTest.mouseRelease(popup, Qt.MouseButton.LeftButton, pos=outside_local)
        APP.processEvents()
        self.assertFalse(popup.isVisible())
        button.click()
        APP.processEvents()
        self.assertTrue(popup.isVisible())
        self.window.paste_box.setFocus()
        APP.processEvents()
        popup.hide()
        self.assertFalse(popup.isVisible())

    def test_main_toolbar_split_button_and_prompt_visual_contract(self):
        self.floating.open_main()
        window = self.window
        window.resize(980, 740)
        APP.processEvents()
        self.assertEqual(window.floating_button.objectName(), "floatingModeButton")
        self.assertFalse(window.floating_button.icon().isNull())
        self.assertEqual(window.floating_button.height(), 36)
        self.assertEqual(window.floating_button.font().pixelSize(), 14)
        self.assertEqual(window.format_button.objectName(), "promptButton")
        self.assertEqual(window.format_button.height(), 36)
        self.assertEqual(window.format_button.findChild(QLabel, "promptButtonText").font().pixelSize(), 14)
        self.assertIsNone(window.format_button.findChild(QLabel, "promptButtonIcon"))

        self.assertEqual(window.image_action_group.objectName(), "imageSplitGroup")
        self.assertEqual(window.image_action_group.height(), 36)
        self.assertEqual(window.image_paste_button.objectName(), "imageSplitPaste")
        self.assertEqual(window.image_paste_button.font().pixelSize(), 14)
        self.assertIs(window.image_source_button.menu(), window.image_source_menu)
        self.assertEqual(window.image_source_button.popupMode(), QToolButton.ToolButtonPopupMode.InstantPopup)
        self.assertEqual(window.image_source_button.accessibleName(), "选择图片来源")
        self.assertEqual([action.text() for action in window.image_source_menu.actions()],
                         ["选择本地图片…"])
        self.assertFalse(window.image_source_button.icon().isNull())
        self.assertFalse(window.image_remove_button.isVisible())

        # The real 500px minimum-width window must keep primary image actions
        # exposed. The optional editability hint is hidden only after attach.
        window.resize(500, 700)
        APP.processEvents()
        self.assertTrue(window.image_action_group.isVisible())
        self.assertTrue(window.image_paste_button.isVisible())
        self.assertTrue(window.image_source_button.isVisible())
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("#AABBCC"))
        APP.clipboard().setImage(image)
        window.image_paste_button.click()
        APP.processEvents()
        self.assertIsNotNone(window.attachment_for_add())
        self.assertTrue(window.image_thumb.isVisible())
        self.assertTrue(window.image_remove_button.isVisible())
        self.assertFalse(window.preview_editable_hint.isVisible())
        window.image_remove_button.click()
        self.assertTrue(window.preview_editable_hint.isVisible())

    def test_main_window_image_clipboard_and_submit(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("振り向く"))
        APP.processEvents()
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("#AABBCC"))
        APP.clipboard().setImage(image)
        self.window.image_paste_button.click()
        attached = self.window.attachment_for_add()
        self.assertIsNotNone(attached)
        self.assertTrue(attached.png.startswith(b"\x89PNG"))
        self.assertTrue(self.window.image_thumb.isVisible())
        self.assertTrue(self.window.image_remove_button.isVisible())
        self.window.controller.add_clicked()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("振り向く", "日语")])
        self.assertEqual(self.checker.attachments, [attached])
        self.assertIsNone(self.window.attachment_for_add())
        self.assertFalse(self.window.image_remove_button.isVisible())

    def test_image_before_first_json_is_preserved(self):
        self.floating.open_main()
        image = QImage(22, 25, QImage.Format.Format_RGB32)
        image.fill(QColor("#445566"))
        APP.clipboard().setImage(image)
        self.window.image_paste_button.click()
        attachment = self.window.attachment_for_add()
        self.assertIsNotNone(attachment)
        self.window.paste_box.setPlainText(raw("海"))
        APP.processEvents()
        self.assertEqual(self.window.attachment_for_add(), attachment)
        self.window.controller.add_clicked()
        APP.processEvents()
        self.assertEqual(self.checker.attachments, [attachment])

    def test_main_window_file_picker_imports_picture(self):
        self.floating.open_main()
        image = QImage(64, 40, QImage.Format.Format_RGB32)
        image.fill(QColor("#00AABB"))
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "sample.png")
            self.assertTrue(image.save(path, "PNG"))
            with patch("ui.main_window.QFileDialog.getOpenFileName", return_value=(path, "图片")):
                self.window.image_file_action.trigger()
        attachment = self.window.attachment_for_add()
        self.assertIsNotNone(attachment)
        self.assertEqual((attachment.width, attachment.height), (64, 40))
        self.assertEqual(attachment.filename[:4], "aqa_")
        self.assertFalse(self.window.image_thumb.icon().isNull())
        self.window.image_remove_button.click()
        self.assertIsNone(self.window.attachment_for_add())

    def test_image_draft_blocks_floating_mode_and_keeps_content(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("山"))
        image = QImage(30, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("#FFFFFF"))
        APP.clipboard().setImage(image)
        self.window.image_paste_button.click()
        self.assertIsNotNone(self.window.attachment_for_add())
        self.floating.enable()
        self.assertFalse(self.floating.enabled)
        self.assertTrue(self.window.isVisible())
        self.assertIsNotNone(self.window.attachment_for_add())
        self.window.image_remove_button.click()
        self.assertIsNone(self.window.attachment_for_add())
        self.floating.enable()
        self.assertTrue(self.floating.enabled)

    def test_new_json_draft_clears_old_picture(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("猫"))
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("#112233"))
        APP.clipboard().setImage(image)
        self.window.image_paste_button.click()
        self.assertIsNotNone(self.window.attachment_for_add())
        self.window.paste_box.setPlainText(raw("犬"))
        self.assertIsNone(self.window.attachment_for_add())

    def test_image_add_failure_keeps_attachment_for_retry(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("森"))
        image = QImage(28, 28, QImage.Format.Format_RGB32)
        image.fill(QColor("#345678"))
        APP.clipboard().setImage(image)
        self.window.image_paste_button.click()
        attachment = self.window.attachment_for_add()
        self.checker.fail_add = True
        self.window.controller.add_clicked()
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.ERROR)
        self.assertEqual(self.window.attachment_for_add(), attachment)
        self.assertEqual(self.checker.added, [])
        self.checker.fail_add = False
        self.window.controller.add_clicked()  # Retry must re-check before sending.
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.READY)
        self.window.controller.add_clicked()
        APP.processEvents()
        self.assertEqual(self.checker.attachments, [attachment])
        self.assertIsNone(self.window.attachment_for_add())

    def test_capture_while_main_hidden_requires_confirmation(self):
        self.copy(raw())
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.floating.panel.isVisible())
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        self.assertEqual(self.floating.pending_count, 1)
        self.assertEqual(self.checker.added, [])
        self.assertTrue(self.floating.panel.add_button.isEnabled())
        self.assertGreaterEqual(self.floating.panel.back_box.height(), 100)

    def test_compact_panel_type_scale_and_matched_window_controls(self):
        panel = self.floating.panel
        self.copy(raw())
        self.assertEqual((panel.width(), panel.height()), (360, 510))
        title = panel.findChild(QLabel, "floatTitle")
        self.assertIsNotNone(title)
        self.assertEqual(title.font().pixelSize(), FLOAT_TITLE_SIZE)
        self.assertEqual(panel.auto_add_switch.font().pixelSize(), FLOAT_CONTROL_SIZE)
        self.assertEqual(panel.front_box.font().pixelSize(), FLOAT_EDITOR_SIZE)
        self.assertEqual(panel.back_box.font().pixelSize(), FLOAT_EDITOR_SIZE)
        self.assertEqual(panel.status_label.font().pixelSize(), FLOAT_META_SIZE)
        self.assertEqual((FLOAT_TITLE_SIZE, FLOAT_CONTROL_SIZE, FLOAT_META_SIZE, FLOAT_EDITOR_SIZE),
                         (16, 14, 12, 14))
        self.assertFalse(panel.main_button.icon().isNull())
        self.assertFalse(panel.collapse_button.icon().isNull())
        self.assertEqual(panel.main_button.size(), panel.collapse_button.size())
        self.assertEqual(panel.main_button.iconSize(), panel.collapse_button.iconSize())
        self.assertEqual(panel.main_button.accessibleName(), "返回主窗口")
        self.assertEqual(panel.collapse_button.accessibleName(), "收起为悬浮球")
        self.assertFalse(panel.retry_button.isVisible())

    def test_compact_panel_window_actions_keep_floating_session(self):
        self.copy(raw())
        panel = self.floating.panel
        panel.collapse_button.click()
        self.assertFalse(panel.isVisible())
        self.assertTrue(self.floating.orb.isVisible())
        self.assertTrue(self.floating.poll_timer.isActive())
        self.floating.toggle_preview()
        self.assertTrue(panel.isVisible())
        panel.main_button.click()
        self.assertFalse(self.floating.enabled)
        self.assertTrue(self.window.isVisible())

    def test_unrelated_clipboard_and_semantic_repeats_are_ignored(self):
        self.copy("ordinary text")
        self.copy('{"front":"only front"}')
        self.assertEqual(self.floating.pending_count, 0)
        self.copy(raw())
        self.copy("ordinary text")
        self.copy('```json\n' + raw() + '\n```')
        self.assertEqual(self.floating.pending_count, 1)

    def test_copies_queue_without_overwriting_edits(self):
        self.copy(raw("一"))
        self.floating.panel.back_box.setPlainText("edited meaning")
        self.copy(raw("二"))
        self.copy(raw("三"))
        self.copy(raw("二"))
        self.assertEqual(self.window.controller.card.front, "一")
        self.assertEqual(self.window.controller.card.back, "edited meaning")
        self.assertEqual(self.floating.pending_count, 3)
        self.floating.ignore()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.floating.pending_count, 2)

    def test_success_toast_and_next_card(self):
        self.copy(raw("一"))
        self.copy(raw("二"))
        self.floating.submit()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("一", "日语")])
        self.assertTrue(self.floating.toast.isVisible())
        self.assertIn("一", self.floating.toast.detail.text())
        self.assertFalse(self.floating.panel.isVisible())
        self.assertEqual(self.floating.pending_count, 1)
        self.floating.success_timer.stop()
        self.floating._after_success()
        self.assertEqual(self.window.controller.card.front, "二")
        self.assertEqual(self.checker.added, [("一", "日语")])

    def test_add_in_flight_locks_editing_and_preserves_new_copies(self):
        self.copy(raw("一"))
        self.executor.defer = True
        self.floating.submit()
        self.floating.submit()
        self.floating.ignore()
        self.copy(raw("二"))
        self.assertEqual(self.window.controller.state, State.ADDING)
        self.assertEqual(len(self.executor.jobs), 1)
        self.assertTrue(self.floating.panel.front_box.isReadOnly())
        self.assertFalse(self.floating.panel.ignore_button.isEnabled())
        self.assertFalse(self.floating.panel.add_button.isEnabled())
        self.assertEqual(self.floating.pending_count, 2)
        self.executor.finish()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("一", "日语")])
        self.assertEqual(self.floating.pending_count, 1)

    def test_failure_keeps_contents_and_can_retry(self):
        self.copy(raw())
        self.checker.fail_add = True
        self.floating.submit()
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.ERROR)
        self.assertEqual(self.floating.panel.front_box.toPlainText(), "木漏れ日")
        self.assertEqual(self.floating.pending_count, 1)
        self.assertFalse(self.floating.toast.isVisible())
        self.assertFalse(self.floating.panel.add_button.isEnabled())
        self.assertTrue(self.floating.panel.retry_button.isEnabled())
        self.checker.fail_add = False
        self.floating.panel.retry_button.click()  # reconnect and recheck, no write yet
        self.assertEqual(self.window.controller.state, State.READY)
        self.floating.submit()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])

    def test_empty_floating_reconnect_clears_stale_offline_state(self):
        # Regression: main indicator becomes green after retry, but the
        # empty floating editor used to keep its old "未连接 Anki" status.
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.window.controller.startup()
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.ANKI_OFFLINE)
        self.floating.toggle_preview()
        self.assertTrue(self.floating.panel.isVisible())
        self.assertTrue(self.floating.panel.retry_button.isVisible())
        self.assertIn("未连接 Anki", self.floating.panel.status_label.text())
        self.checker.offline = False
        self.floating.panel.retry_button.click()
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.EMPTY)
        self.assertEqual(self.window.connection_button.property("level"), "ok")
        self.assertIn("Anki 已连接", self.floating.panel.status_label.text())
        self.assertFalse(self.floating.panel.retry_button.isVisible())
        self.assertFalse(self.floating.panel.add_button.isEnabled())

    def test_floating_empty_status_tracks_shared_connection_badge(self):
        self.floating.toggle_preview()
        self.assertEqual(self.window.controller.state, State.EMPTY)
        self.window.set_connection("Anki 未连接", "warn")
        self.assertIn("Anki 未连接", self.floating.panel.status_label.text())
        self.assertTrue(self.floating.panel.retry_button.isVisible())
        self.window.set_connection("Anki 已连接", "ok")
        self.assertIn("Anki 已连接", self.floating.panel.status_label.text())
        self.assertFalse(self.floating.panel.retry_button.isVisible())
        self.assertEqual(self.window.controller.state, State.EMPTY)

    def test_retry_reconnect_does_not_clear_incomplete_draft(self):
        self.floating.open_main()
        self.window.front_box.setPlainText("未完成")
        self.assertEqual(self.window.controller.state, State.INVALID)
        message = self.window.controller.message
        self.window.controller.retry_connection()
        APP.processEvents()
        self.assertEqual(self.window.controller.state, State.INVALID)
        self.assertEqual(self.window.controller.message, message)

    def test_offline_card_remains_editable(self):
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.copy(raw())
        self.assertEqual(self.window.controller.state, State.ANKI_OFFLINE)
        self.assertFalse(self.floating.panel.front_box.isReadOnly())
        self.assertFalse(self.floating.toast.isVisible())
        self.assertEqual(self.checker.added, [])
        self.assertEqual(self.floating.panel.add_button.text(), "添加到 Anki")
        self.assertFalse(self.floating.panel.add_button.isEnabled())
        self.assertTrue(self.floating.panel.retry_button.isEnabled())
        self.checker.offline = False
        self.floating.panel.retry_button.click()
        self.assertEqual(self.window.controller.state, State.READY)
        self.assertTrue(self.floating.panel.add_button.isEnabled())
        self.assertEqual(self.floating.panel.front_box.toPlainText(), "木漏れ日")
        self.assertEqual(self.checker.added, [])

    def test_switching_views_shares_edits_and_deck(self):
        self.copy(raw())
        self.floating.panel.front_box.setPlainText("木漏れ日・編集")
        self.floating.panel.deck_box.setCurrentText("Other")
        self.assertEqual(self.window.front_box.toPlainText(), "木漏れ日・編集")
        self.assertEqual(self.window.controller.deck, "Other")
        self.floating.open_main()
        self.copy(raw("new copy must be ignored"))
        self.assertEqual(self.window.controller.card.front, "木漏れ日・編集")
        self.assertFalse(self.floating.poll_timer.isActive())
        self.assertTrue(self.window.isVisible())
        self.assertIn("floating_position", self.saved)

    def test_existing_manual_draft_is_not_replaced(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("draft"))
        self.floating.clipboard.setText(raw("new"))
        self.floating.enable()
        APP.processEvents()
        self.assertEqual(self.window.controller.card.front, "draft")
        self.assertEqual(self.floating.pending_count, 2)

    def test_pause_blocks_both_signal_and_poll_capture(self):
        self.floating.toggle_listening()
        self.copy(raw())
        self.floating.read_clipboard()
        self.assertEqual(self.floating.pending_count, 0)
        self.floating.toggle_listening()
        self.assertEqual(self.floating.pending_count, 1)

    def test_popup_stays_on_screen_at_both_edges(self):
        area = self.floating.orb.screen().availableGeometry()
        for point in (area.topLeft(), QPoint(area.right() - 72, area.bottom() - 72)):
            self.floating.orb.move(point)
            self.floating.reposition()
            self.assertTrue(area.contains(self.floating.panel.geometry()))
            self.assertTrue(area.contains(self.floating.toast.geometry()))
        self.assertTrue(self.floating.panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(self.floating.orb.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def settle_auto(self):
        # Allow queued submit/completion callbacks to run on separate event turns.
        for _ in range(12):
            APP.processEvents()

    def test_auto_add_toggle_is_off_by_default_and_adds_without_popups(self):
        self.assertFalse(self.floating.auto_add)
        self.assertFalse(self.floating.panel.auto_add_switch.isChecked())
        self.floating.toggle_preview()
        self.floating.panel.auto_add_switch.click()
        self.assertTrue(self.floating.auto_add)
        self.copy(raw())
        self.settle_auto()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
        self.assertEqual(self.floating.pending_count, 0)
        self.assertEqual(self.floating.auto_added_count, 1)
        self.assertFalse(self.floating.panel.isVisible())
        self.assertFalse(self.floating.toast.isVisible())
        self.assertFalse(self.floating.success_timer.isActive())
        self.assertIn("本次已添加 1 张", self.floating.orb.toolTip())

    def test_auto_add_filters_invalid_and_repeated_copies(self):
        self.floating.set_auto_add(True)
        for value in ('ordinary text', '{"front":"missing back"}', '{"front":8,"back":"x"}', '[]'):
            self.copy(value)
        self.assertEqual(self.checker.added, [])
        self.copy(raw())
        self.settle_auto()
        self.copy('ordinary text')
        self.copy('```json\n' + raw() + '\n```')
        self.settle_auto()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
        self.assertFalse(self.floating.panel.isVisible())

    def test_auto_add_serializes_queued_copies_and_skips_existing_notes(self):
        self.checker.added.append(("重复", "日语"))
        self.floating.set_auto_add(True)
        self.executor.defer = True
        self.copy(raw("一"))
        self.copy(raw("重复"))
        self.copy(raw("二"))
        self.assertEqual(self.floating.pending_count, 3)
        for _ in range(30):
            if self.executor.jobs:
                self.assertEqual(len(self.executor.jobs), 1)
                self.executor.finish()
            self.settle_auto()
            if not self.floating.pending_count:
                break
        self.assertEqual(self.checker.added, [("重复", "日语"), ("一", "日语"), ("二", "日语")])
        self.assertEqual(self.floating.auto_added_count, 2)
        self.assertEqual(self.floating.auto_skipped_count, 1)
        self.assertEqual(self.floating.pending_count, 0)
        self.assertFalse(self.floating.panel.isVisible())
        self.assertFalse(self.floating.toast.isVisible())

    def test_turning_auto_off_during_check_requires_manual_confirmation(self):
        self.floating.set_auto_add(True)
        self.executor.defer = True
        self.copy(raw())
        self.floating.set_auto_add(False)
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.window.controller.state, State.READY)
        self.assertEqual(self.checker.added, [])
        self.floating.set_auto_add(True)  # Re-enabling doesn't add an older draft.
        self.settle_auto()
        self.assertEqual(self.checker.added, [])
        self.floating.submit()
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])

    def test_auto_add_failure_retains_queue_and_retries_only_on_request(self):
        self.floating.set_auto_add(True)
        self.checker.fail_add = True
        self.copy(raw("一"))
        self.settle_auto()
        self.copy(raw("二"))
        self.settle_auto()
        self.assertEqual(self.window.controller.state, State.ERROR)
        self.assertEqual(self.floating.pending_count, 2)
        self.assertEqual(self.checker.added, [])
        self.assertFalse(self.floating.panel.isVisible())
        self.assertFalse(self.floating.toast.isVisible())
        self.assertTrue(self.floating.orb.attention)
        self.checker.fail_add = False
        self.settle_auto()
        self.assertEqual(self.checker.added, [])
        self.floating.toggle_preview()
        self.floating.panel.retry_button.click()
        self.settle_auto()
        self.assertEqual(self.checker.added, [("一", "日语"), ("二", "日语")])
        self.assertEqual(self.floating.pending_count, 0)

    def test_auto_add_offline_preserves_content_and_current_deck(self):
        self.floating.select_deck("Other")
        self.floating.set_auto_add(True)
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.copy(raw())
        self.settle_auto()
        self.assertEqual(self.window.controller.state, State.ANKI_OFFLINE)
        self.assertEqual(self.floating.pending_count, 1)
        self.assertEqual(self.checker.added, [])
        self.assertFalse(self.floating.panel.isVisible())
        self.checker.offline = False
        self.window.controller.retry_connection()
        self.settle_auto()
        self.assertEqual(self.checker.added, [("木漏れ日", "Other")])

    def test_enabling_auto_preserves_existing_manual_draft_and_queue(self):
        self.copy(raw("已有草稿"))
        self.copy(raw("已有队列"))
        self.floating.set_auto_add(True)
        self.copy(raw("新复制"))
        self.settle_auto()
        self.assertEqual(self.checker.added, [])
        self.assertEqual(self.floating.pending_count, 3)
        self.floating.ignore()
        self.settle_auto()
        self.assertEqual(self.window.controller.card.front, "已有队列")
        self.assertEqual(self.checker.added, [])
        self.floating.ignore()
        self.settle_auto()
        self.assertEqual(self.checker.added, [("新复制", "日语")])

    def test_editing_auto_card_cancels_automatic_submission(self):
        self.floating.set_auto_add(True)
        self.executor.defer = True
        self.copy(raw())
        self.floating.toggle_preview()
        self.floating.panel.back_box.setPlainText("我修改的释义")
        for _ in range(5):
            if self.executor.jobs:
                self.executor.finish()
            self.settle_auto()
        self.assertEqual(self.checker.added, [])
        self.assertEqual(self.window.controller.card.back, "我修改的释义")
        self.assertFalse(self.floating.active.auto_add)

    def test_pause_or_returning_to_main_cancels_auto_before_submission(self):
        self.floating.set_auto_add(True)
        self.executor.defer = True
        self.copy(raw())
        self.floating.toggle_listening()
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.checker.added, [])
        self.floating.toggle_listening()
        self.settle_auto()
        self.assertEqual(self.window.controller.state, State.ADDING)
        self.floating.set_auto_add(False)  # Already dispatched writes finish once.
        self.executor.finish()
        self.settle_auto()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
        self.assertFalse(self.floating.toast.isVisible())
        self.floating.set_auto_add(True)
        self.copy(raw("留给主窗口"))
        self.floating.open_main()
        self.executor.finish()
        self.settle_auto()
        self.assertFalse(self.floating.auto_add)
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])

    def test_discard_during_duplicate_callback_does_not_stall_auto_queue(self):
        self.checker.added.append(("重复", "日语"))
        self.floating.set_auto_add(True)
        self.executor.defer = True
        self.copy(raw("重复"))
        self.copy(raw("下一张"))
        self.executor.finish()
        self.floating.ignore()  # Cancel before the queued duplicate skip executes.
        for _ in range(20):
            self.settle_auto()
            if self.executor.jobs:
                self.executor.finish()
        self.assertEqual(self.checker.added, [("重复", "日语"), ("下一张", "日语")])
        self.assertEqual(self.floating.pending_count, 0)

    def assert_popup_is_closed(self, combo, action):
        combo.window().show()
        combo.window().activateWindow()
        QTest.qWait(100)
        combo.showPopup()
        APP.processEvents()
        popup = combo.view().window()
        self.assertTrue(popup.isVisible())
        action()
        # Wait beyond the old rollout: it must never resurrect a hidden popup.
        QTest.qWait(400)
        self.assertFalse(popup.isVisible())
        self.assertFalse(any(widget.isVisible() and widget.metaObject().className() == "QRollEffect"
                             for widget in APP.topLevelWidgets()))

    def test_deck_popup_closes_when_preview_is_collapsed_or_closed(self):
        self.assertFalse(APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo))
        self.floating.toggle_preview()
        self.assert_popup_is_closed(self.floating.panel.deck_box, self.floating.panel.collapse_button.click)
        self.floating.toggle_preview()
        self.assert_popup_is_closed(self.floating.panel.deck_box, self.floating.panel.close)

    def test_deck_popup_closes_on_mode_switch_and_repeated_shutdown(self):
        self.floating.toggle_preview()
        self.assert_popup_is_closed(self.floating.panel.deck_box, self.floating.open_main)
        self.floating.enable()
        self.floating.toggle_preview()
        self.assert_popup_is_closed(self.floating.panel.deck_box, lambda: self.floating.set_auto_add(True))
        self.floating.toggle_preview()
        self.assert_popup_is_closed(self.floating.panel.deck_box, self.floating.shutdown)
        self.floating.shutdown()  # Cleanup is idempotent, including signal disconnect.

    def test_main_deck_popup_closes_on_floating_switch_and_app_exit(self):
        self.floating.open_main()
        self.assert_popup_is_closed(self.window.deck_box, self.floating.enable)
        self.floating.open_main()
        self.assert_popup_is_closed(self.window.deck_box, self.window.close)

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "windows",
        "Windows foreground integration requires the native Windows Qt backend",
    )
    def test_external_clipboard_copy_does_not_steal_windows_focus(self):
        self._exercise_external_clipboard(automatic=False)

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "windows",
        "Windows foreground integration requires the native Windows Qt backend",
    )
    def test_auto_external_clipboard_adds_without_stealing_focus(self):
        self._exercise_external_clipboard(automatic=True)

    def _exercise_external_clipboard(self, automatic):
        self.floating.clipboard = APP.clipboard()
        APP.clipboard().setText("")
        if automatic:
            self.floating.set_auto_add(True)
        source = QWidget()
        layout = QVBoxLayout(source)
        editor = QLineEdit("继续阅读和输入")
        layout.addWidget(editor)
        source.show()
        source.activateWindow()
        editor.setFocus()
        QTest.qWait(200)
        try:
            self.assertIs(APP.activeWindow(), source)
            env = dict(os.environ, AQA_TEST_CARD=raw())
            script = (
                'import os; from PySide6.QtCore import QTimer; '
                'from PySide6.QtWidgets import QApplication; '
                'app=QApplication([]); app.clipboard().setText(os.environ["AQA_TEST_CARD"]); '
                'QTimer.singleShot(300,app.quit); app.exec()'
            )
            process = subprocess.Popen([sys.executable, "-c", script], env=env)
            for _ in range(100):
                QTest.qWait(50)
                if process.poll() is not None and (self.checker.added if automatic else self.floating.pending_count):
                    break
            self.assertEqual(process.wait(timeout=5), 0)
            if automatic:
                self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
                self.assertFalse(self.floating.panel.isVisible())
                self.assertFalse(self.floating.toast.isVisible())
            else:
                self.assertEqual(self.floating.pending_count, 1)
            self.assertIs(APP.activeWindow(), source)
            self.assertIs(APP.focusWidget(), editor)
        finally:
            source.close()


if __name__ == "__main__":
    if sys.platform == "win32" and os.environ.get("AQA_QT_TEST_MODE") is None:
        offscreen_env = dict(os.environ, QT_QPA_PLATFORM="offscreen", AQA_QT_TEST_MODE="offscreen")
        result = subprocess.run([sys.executable, __file__], env=offscreen_env)
        if result.returncode:
            raise SystemExit(result.returncode)

        native_env = dict(os.environ, QT_QPA_PLATFORM="windows", AQA_QT_TEST_MODE="native")
        result = subprocess.run(
            [
                sys.executable,
                __file__,
                "FloatingTests.test_external_clipboard_copy_does_not_steal_windows_focus",
                "FloatingTests.test_auto_external_clipboard_adds_without_stealing_focus",
            ],
            env=native_env,
        )
        raise SystemExit(result.returncode)

    unittest.main()
