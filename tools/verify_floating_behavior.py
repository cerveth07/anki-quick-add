"""Exercise floating capture against the real Qt view and a fake Anki backend."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def shutdown(self, on_finished=None):
        if on_finished:
            on_finished()


class FloatingTests(unittest.TestCase):
    def setUp(self):
        APP.clipboard().setText("")
        self.checker = FakeChecker()
        self.executor = Executor()
        self.saved = {}
        self.window = MainWindow(self.checker, {"floating_quick_add": False}, self.saved.update, str(ROOT), executor=self.executor)
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
        self.assertFalse(self.floating.panel.isVisible())
        self.floating.toggle_preview()
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
        self.floating.toggle_preview()
        self.floating.submit()
        APP.processEvents()
        self.assertEqual(self.checker.added, [("一", "日语")])
        self.assertTrue(self.floating.toast.isVisible())
        self.assertIn("一", self.floating.toast.detail.text())
        self.assertTrue(self.floating.panel.isVisible())
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
        self.assertEqual(self.floating.pending_count, 1)
        self.copy(raw("new after entering"))
        self.assertEqual(self.floating.pending_count, 2)

    def test_pause_blocks_both_signal_and_poll_capture(self):
        self.floating.toggle_listening()
        self.copy(raw())
        self.floating.read_clipboard()
        self.assertEqual(self.floating.pending_count, 0)
        self.floating.toggle_listening()
        self.assertEqual(self.floating.pending_count, 0)
        self.copy(raw("new after resuming"))
        self.assertEqual(self.floating.pending_count, 1)

    def drain(self):
        for _ in range(12):
            APP.processEvents()

    def test_quick_mode_adds_without_opening_preview(self):
        self.floating.set_quick_add(True)
        self.copy(raw())
        self.drain()
        self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
        self.assertFalse(self.floating.panel.isVisible())
        self.assertEqual(self.floating.orb.count, 0)
        self.assertTrue(self.floating.toast.isVisible())
        self.assertIn("已添加", self.floating.toast.title.text())
        self.assertIn("日语", self.floating.orb.toolTip())
        self.assertTrue(self.saved["floating_quick_add"])

    def test_default_is_quick_and_setting_restores_confirmation(self):
        self.window.close()
        self.window = MainWindow(self.checker, {}, self.saved.update, str(ROOT), executor=Executor())
        self.assertTrue(self.window.floating.quick_add)
        self.window.close()
        self.window = MainWindow(self.checker, {"floating_quick_add": False}, self.saved.update, str(ROOT), executor=Executor())
        self.assertFalse(self.window.floating.quick_add)

    def test_real_state_writer_persists_mode_and_preserves_existing_settings(self):
        import app as entrypoint

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "window.json")
            with patch.object(entrypoint, "_state_path", return_value=path):
                entrypoint.save_state(deck="日语", floating_position=[100, 200], format_prompt="my prompt")
                with patch.object(self.window, "_save_state", entrypoint.save_state):
                    self.floating.set_quick_add(False)
                    entrypoint.save_state(geometry="900x700+10+20")
                    saved = entrypoint.load_state()
                    self.assertIs(saved["floating_quick_add"], False)
                    self.assertEqual(saved["deck"], "日语")
                    self.assertEqual(saved["format_prompt"], "my prompt")
                    self.assertEqual(saved["floating_position"], [100, 200])
                    self.floating.set_quick_add(True)
                    self.assertIs(entrypoint.load_state()["floating_quick_add"], True)

    def test_quick_mode_does_not_import_old_clipboard_or_draft(self):
        self.floating.open_main()
        self.window.paste_box.setPlainText(raw("draft"))
        APP.clipboard().setText(raw("old clipboard"))
        self.floating.set_quick_add(True)
        self.floating.enable()
        self.drain()
        self.assertEqual(self.checker.added, [])
        self.assertEqual(self.window.controller.card.front, "draft")
        self.assertEqual(self.floating.pending_count, 1)
        self.assertFalse(self.floating.panel.isVisible())

    def test_quick_mode_ignores_old_clipboard_without_a_draft(self):
        self.floating.open_main()
        APP.clipboard().setText(raw("old"))
        self.floating.set_quick_add(True)
        self.floating.enable()
        self.drain()
        self.assertEqual(self.floating.pending_count, 0)
        self.assertEqual(self.checker.added, [])
        self.copy(raw("new"))
        self.drain()
        self.assertEqual(self.checker.added, [("new", "日语")])

    def test_quick_mode_serializes_copies_and_prevents_double_submit(self):
        self.floating.set_quick_add(True)
        self.executor.defer = True
        self.copy(raw("一"))
        self.copy(raw("二"))
        self.copy(raw("三"))
        self.copy(raw("二"))
        self.assertEqual(self.floating.pending_count, 3)
        self.executor.finish()  # first duplicate check
        self.drain()
        self.assertEqual(self.window.controller.state, State.ADDING)
        self.floating.submit()
        self.assertEqual(len(self.executor.jobs), 1)
        for _ in range(5):  # add first, then check/add each following card
            self.executor.finish()
            self.drain()
            self.assertLessEqual(len(self.executor.jobs), 1)
        self.assertEqual(self.checker.added, [("一", "日语"), ("二", "日语"), ("三", "日语")])
        self.assertEqual(self.floating.orb.count, 0)
        self.assertFalse(self.floating.panel.isVisible())

    def test_quick_mode_duplicate_is_skipped_and_queue_continues(self):
        self.checker.added.append(("一", "日语"))
        self.floating.set_quick_add(True)
        self.copy(raw("一"))
        self.drain()
        self.assertIn("已跳过", self.floating.toast.title.text())
        self.assertEqual(self.floating.pending_count, 0)
        self.copy(raw("二"))
        self.drain()
        self.assertEqual(self.checker.added, [("一", "日语"), ("二", "日语")])

    def test_quick_mode_add_time_duplicate_is_also_skipped(self):
        self.floating.set_quick_add(True)
        self.checker.add = lambda card, deck: AddResult("DUPLICATE", "该词已存在", deck=deck)
        self.copy(raw())
        self.drain()
        self.assertEqual(self.floating.pending_count, 0)
        self.assertIn("已跳过", self.floating.toast.title.text())

    def test_quick_mode_failure_is_retained_without_retry_loop(self):
        self.checker.fail_add = True
        self.floating.set_quick_add(True)
        self.copy(raw("一"))
        self.drain()
        self.assertEqual(self.window.controller.state, State.ERROR)
        self.assertEqual(self.floating.orb.count, 1)
        self.assertIn("需要处理", self.floating.toast.title.text())
        self.assertFalse(self.floating.panel.isVisible())
        self.checker.fail_add = False
        self.copy(raw("二"))
        self.drain()
        self.assertEqual(self.checker.added, [])
        self.assertEqual(self.floating.pending_count, 2)
        self.floating.toggle_preview()
        self.floating.submit()  # reconnect/check
        self.floating.submit()
        self.drain()
        self.assertEqual(self.checker.added, [("一", "日语")])
        self.assertEqual(self.window.controller.card.front, "二")
        self.floating.collapse_preview()
        self.drain()
        self.assertEqual(self.checker.added, [("一", "日语"), ("二", "日语")])

    def test_open_preview_cancels_scheduled_auto_add_and_preserves_edits(self):
        self.floating.set_quick_add(True)
        APP.clipboard().setText(raw("一"))
        self.floating.read_clipboard()
        self.floating.toggle_preview()
        self.drain()
        self.assertEqual(self.checker.added, [])
        self.floating.panel.back_box.setPlainText("edited")
        self.copy(raw("二"))
        self.floating.collapse_preview()
        self.drain()
        self.assertEqual(self.window.controller.card.back, "edited")
        self.assertEqual(self.floating.pending_count, 2)
        self.assertEqual(self.checker.added, [])

    def test_collapsing_confirmation_mode_never_enables_auto_add(self):
        self.copy(raw())
        self.floating.toggle_preview()
        self.floating.collapse_preview()
        self.drain()
        self.assertFalse(self.floating.quick_add)
        self.assertEqual(self.checker.added, [])

    def test_switch_to_confirmation_or_pause_cancels_scheduled_auto_add(self):
        self.floating.set_quick_add(True)
        APP.clipboard().setText(raw())
        self.floating.read_clipboard()
        self.floating.set_quick_add(False)
        self.drain()
        self.assertEqual(self.checker.added, [])
        self.floating.set_quick_add(True)
        self.floating.toggle_listening()
        self.drain()
        self.assertEqual(self.checker.added, [])

    def test_quick_mode_offline_keeps_card_and_does_not_resume_automatically(self):
        self.checker.offline = True
        self.window.controller._capabilities_ok = None
        self.floating.set_quick_add(True)
        self.copy(raw())
        self.drain()
        self.assertEqual(self.window.controller.state, State.ANKI_OFFLINE)
        self.assertEqual(self.floating.orb.count, 1)
        self.checker.offline = False
        self.window.controller.retry_connection()
        self.drain()
        self.assertEqual(self.checker.added, [])

    def test_shutdown_cancels_scheduled_auto_add(self):
        self.floating.set_quick_add(True)
        APP.clipboard().setText(raw())
        self.floating.read_clipboard()
        self.floating.shutdown()
        self.drain()
        self.assertEqual(self.checker.added, [])

    def test_closing_orb_exits_instead_of_leaving_hidden_listener(self):
        self.floating.orb.close()
        APP.processEvents()
        self.assertTrue(self.window._closing)
        self.assertTrue(self.floating.closed)
        self.assertFalse(self.floating.poll_timer.isActive())
        self.assertFalse(self.floating.orb.isVisible())

    def test_native_preview_close_keeps_orb_and_selected_mode(self):
        self.copy(raw())
        self.floating.toggle_preview()
        self.floating.panel.close()
        APP.processEvents()
        self.assertFalse(self.floating.panel.isVisible())
        self.assertTrue(self.floating.orb.isVisible())
        self.assertFalse(self.window._closing)
        self.assertFalse(self.floating.quick_add)

    def test_real_worker_shutdown_stays_visible_until_request_finishes(self):
        script = '''
import json, sys
from threading import Event
sys.path.insert(0, "tools")
from PySide6.QtCore import QTimer
from verify_floating_behavior import APP, FakeChecker, ROOT
from ui.main_window import MainWindow
from ui.qt_executor import QtThreadedExecutor
executor=QtThreadedExecutor()
started, release, completed=Event(), Event(), Event()
callbacks, queued_runs=[], []
window=MainWindow(FakeChecker(), {}, lambda **kw: None, str(ROOT), executor=executor)
window.floating.enable()
def job():
    started.set()
    release.wait(1)
    completed.set()
executor(job, lambda value,error: callbacks.append(value))
assert started.wait(1)
executor(lambda: queued_runs.append(True), lambda value,error: callbacks.append(value))
observed={}
def inspect_and_release():
    observed["waiting_visible"]=window.isVisible() and window._closing
    observed["orb_closed"]=not window.floating.orb.isVisible()
    observed["exit_message"]="正在退出" in window.status_label.text()
    release.set()
QTimer.singleShot(0, window.floating.orb.close)
QTimer.singleShot(150, inspect_and_release)
QTimer.singleShot(3000, APP.quit)
APP.exec()
observed["completed_before_exit"]=completed.is_set()
observed["callbacks_after_close"]=len(callbacks)
observed["queued_jobs_ran"]=len(queued_runs)
print(json.dumps(observed),flush=True)
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(observed.get("waiting_visible"), observed)
        self.assertTrue(observed.get("orb_closed"), observed)
        self.assertTrue(observed.get("exit_message"), observed)
        self.assertTrue(observed["completed_before_exit"], observed)
        self.assertEqual(observed["callbacks_after_close"], 0)
        self.assertEqual(observed["queued_jobs_ran"], 0)

    def test_real_executor_delivers_values_and_errors_on_gui_thread(self):
        from threading import get_ident
        from ui.qt_executor import QtThreadedExecutor

        executor = QtThreadedExecutor()
        results = []
        gui_thread = get_ident()
        executor(lambda: (42, get_ident()),
                 lambda value, error: results.append((value, error, get_ident())))
        executor(lambda: 1 / 0,
                 lambda value, error: results.append((value, error, get_ident())))
        for _ in range(100):
            if len(results) == 2:
                break
            QTest.qWait(10)
        executor.shutdown()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0][0], 42)
        self.assertNotEqual(results[0][0][1], gui_thread)
        self.assertIsNone(results[0][1])
        self.assertIsInstance(results[1][1], ZeroDivisionError)
        self.assertEqual([item[2] for item in results], [gui_thread, gui_thread])

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
        self._assert_external_clipboard_focus(quick=False)

    @unittest.skipUnless(sys.platform == "win32", "Windows foreground integration")
    def test_external_quick_add_does_not_steal_windows_focus(self):
        self._assert_external_clipboard_focus(quick=True)

    def _assert_external_clipboard_focus(self, *, quick):
        self.floating.set_quick_add(quick)
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
                handled = bool(self.checker.added) if quick else bool(self.floating.pending_count)
                if process.poll() is not None and handled:
                    break
            self.assertEqual(process.wait(timeout=5), 0)
            if quick:
                self.assertEqual(self.checker.added, [("木漏れ日", "日语")])
                self.assertEqual(self.floating.pending_count, 0)
            else:
                self.assertEqual(self.floating.pending_count, 1)
            self.assertFalse(self.floating.panel.isVisible())
            self.assertIs(APP.activeWindow(), source)
            self.assertIs(APP.focusWidget(), editor)
        finally:
            source.close()


if __name__ == "__main__":
    unittest.main()
