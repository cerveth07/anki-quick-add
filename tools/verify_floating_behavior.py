"""Exercise floating capture against the real Qt view and a fake Anki backend."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from anki import AddResult, AnkiOfflineError, Capabilities, CheckResult
from config import Config
from controller import State
from ui.main_window import MainWindow
from ui.theme import application_stylesheet


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

    def add(self, card, deck):
        if self.fail_add:
            return AddResult("ERROR", "添加失败", deck=deck)
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
        self.floating.enable()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        APP.processEvents()

    def copy(self, value):
        APP.clipboard().setText(value)
        APP.processEvents()

    def test_capture_while_main_hidden_requires_confirmation(self):
        self.copy(raw())
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.floating.panel.isVisible())
        self.assertEqual(self.window.controller.card.front, "木漏れ日")
        self.assertEqual(self.floating.pending_count, 1)
        self.assertEqual(self.checker.added, [])
        self.assertTrue(self.floating.panel.add_button.isEnabled())
        self.assertGreaterEqual(self.floating.panel.back_box.height(), 100)

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
        self.checker.fail_add = False
        self.floating.submit()  # reconnect and recheck, no write yet
        self.assertEqual(self.window.controller.state, State.READY)
        self.floating.submit()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])

    def test_offline_card_remains_editable(self):
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.copy(raw())
        self.assertEqual(self.window.controller.state, State.ANKI_OFFLINE)
        self.assertFalse(self.floating.panel.front_box.isReadOnly())
        self.assertFalse(self.floating.toast.isVisible())
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
        APP.clipboard().setText(raw("new"))
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

    @unittest.skipUnless(sys.platform == "win32", "Windows foreground integration")
    def test_external_clipboard_copy_does_not_steal_windows_focus(self):
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
                if process.poll() is not None and self.floating.pending_count:
                    break
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(self.floating.pending_count, 1)
            self.assertIs(APP.activeWindow(), source)
            self.assertIs(APP.focusWidget(), editor)
        finally:
            source.close()


if __name__ == "__main__":
    unittest.main()
