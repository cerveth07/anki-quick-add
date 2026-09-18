# -*- coding: utf-8 -*-
"""Focused regressions for user-visible Anki Quick Add behavior.

These checks exercise the controller/adapter with fakes only. The Windows Qt
workflow still launches the real PySide6 UI and captures screenshots first.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anki import (
    AddResult,
    AnkiAdapter,
    AnkiOfflineError,
    Capabilities,
    CheckResult,
    DuplicateChecker,
)
from card import CardModel
from config import Config
from controller import QuickAddController, State


CONFIG = Config(
    deck_name="Default",
    note_type="Basic",
    front_field="Front",
    back_field="Back",
    anki_connect_url="http://127.0.0.1:8765",
)


class FakeView:
    def __init__(self) -> None:
        self.connection = ("", "")
        self.status = ("", "")
        self.enabled = (False, True)
        self.card = ("", "")
        self.clear_card_calls = 0
        self.clear_preview_calls = 0
        self.focus_calls = 0
        self.deck = ""
        self.deck_choices: tuple[str, ...] = ()

    def set_connection(self, text: str, level: str = "info") -> None:
        self.connection = (text, level)

    def set_status(self, text: str, level: str = "info") -> None:
        self.status = (text, level)

    def set_card(self, front: str, back: str, hint: str = "") -> None:
        self.card = (front, back)

    def clear_preview(self) -> None:
        self.clear_preview_calls += 1
        self.card = ("", "")

    def clear_card(self) -> None:
        self.clear_card_calls += 1
        self.card = ("", "")

    def focus_paste(self) -> None:
        self.focus_calls += 1

    def set_enabled(self, add_enabled: bool, editable: bool) -> None:
        self.enabled = (add_enabled, editable)

    def set_deck(self, name: str) -> None:
        self.deck = name

    def set_deck_choices(self, names) -> None:
        self.deck_choices = tuple(names)


class FakeAdapter:
    def __init__(self, verify) -> None:
        self.config = CONFIG
        self._verify = verify

    def verify_capabilities(self, deck_name: str | None = None):
        return self._verify(deck_name)


class FakeChecker:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def check(self, card, deck):
        return CheckResult("READY", "ok")

    def add(self, card, deck):
        return AddResult("ADDED", "ok", note_id=1, deck=deck)


def immediate(job, done) -> None:
    try:
        done(job(), None)
    except Exception as exc:  # mirror the Qt threaded executor contract
        done(None, exc)


class ManualExecutor:
    def __init__(self) -> None:
        self.pending: list[tuple[object, object]] = []

    def __call__(self, job, done) -> None:
        self.pending.append((job, done))

    def complete(self, index: int = 0) -> None:
        job, done = self.pending.pop(index)
        try:
            done(job(), None)
        except Exception as exc:
            done(None, exc)


def controller_for(view: FakeView, verify=lambda _deck: Capabilities(6, True, True, True)):
    return QuickAddController(
        FakeChecker(FakeAdapter(verify)),
        view=view,
        schedule=lambda _delay, fn: fn(),
        executor=immediate,
        debounce_ms=0,
    )


def test_offline_result_updates_connection_badge() -> None:
    view = FakeView()
    ctl = controller_for(view)
    view.set_connection("Anki 已连接", "ok")
    ctl.card = CardModel("front", "back")
    ctl._apply_check(CheckResult("OFFLINE", "未连接 Anki"))
    assert view.connection == ("Anki 未连接", "warn"), view.connection

    view.set_connection("Anki 已连接", "ok")
    ctl._on_add_done(AddResult("OFFLINE", "添加失败：未连接 Anki", deck="Default"))
    assert view.connection == ("Anki 未连接", "warn"), view.connection


def test_clear_is_blocked_while_adding() -> None:
    view = FakeView()
    ctl = controller_for(view)
    ctl.card = CardModel("front", "back")
    ctl.paste_text = '{"front":"front","back":"back"}'
    ctl.state = State.ADDING
    ctl.clear_clicked()
    assert ctl.state is State.ADDING, ctl.state
    assert ctl.card.front == "front" and ctl.card.back == "back", ctl.card
    assert view.clear_card_calls == 0, view.clear_card_calls


def test_stale_capability_result_cannot_overwrite_newer_state() -> None:
    view = FakeView()
    executor = ManualExecutor()

    def verify(_deck):
        raise AnkiOfflineError("offline")

    ctl = QuickAddController(
        FakeChecker(FakeAdapter(verify)),
        view=view,
        schedule=lambda _delay, fn: fn(),
        executor=executor,
        debounce_ms=0,
    )
    ctl.startup()
    assert len(executor.pending) == 1
    ctl.paste_changed("not-json")
    assert ctl.state is State.INVALID, ctl.state
    executor.complete()
    assert ctl.state is State.INVALID, ctl.state
    assert view.status[0].startswith("⚠ 无法解析 JSON"), view.status


def test_invalid_deck_still_populates_recovery_choices() -> None:
    view = FakeView()

    def verify(_deck):
        return Capabilities(
            version=6,
            deck_ok=False,
            note_type_ok=True,
            fields_ok=True,
            problem="牌组不存在：Missing",
            decks=("Default", "Other"),
        )

    ctl = controller_for(view, verify)
    ctl.startup()
    assert ctl.state is State.ERROR, ctl.state
    assert view.connection == ("Anki 已连接", "ok"), view.connection
    assert view.deck_choices == ("Default", "Other"), view.deck_choices


def test_null_add_then_disconnect_is_offline_not_generic_error() -> None:
    def transport(action: str, **_params):
        if action == "addNote":
            return None
        if action == "canAddNotes":
            raise AnkiOfflineError("offline during duplicate recheck")
        raise AssertionError(action)

    checker = DuplicateChecker(AnkiAdapter(CONFIG, transport=transport))
    result = checker.add(CardModel("front", "back"), "Default")
    assert result.state == "OFFLINE", result


TESTS = (
    test_offline_result_updates_connection_badge,
    test_clear_is_blocked_while_adding,
    test_stale_capability_result_cannot_overwrite_newer_state,
    test_invalid_deck_still_populates_recovery_choices,
    test_null_add_then_disconnect_is_offline_not_generic_error,
)


def main() -> int:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print("Runtime behavior regressions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
