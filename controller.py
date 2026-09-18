# -*- coding: utf-8 -*-
"""状态机 + 竞态控制（tkinter 无关，可离线测试）。

职责（任务书 §11–§17）：
- 粘贴区任何变化 → 作废结论 → 解析 → 校验 → 查重 → 状态；
- 正面变化 → **立即**进入 CHECKING（按钮立刻不可点）→ debounce 后才发请求；
- 在途结果用 generation 作废，防止旧结果覆盖新 front；
- ADDING 期间锁定编辑区，防双击/连续 Ctrl+Enter 重复提交；
- 成功才清空，任何失败都保留内容。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Optional

from anki import AnkiError, AnkiOfflineError, DuplicateChecker
from card import CardError, CardModel, parse_card

log = logging.getLogger("aqa")


class State(str, Enum):
    EMPTY = "EMPTY"
    PARSING = "PARSING"
    INVALID = "INVALID"
    CHECKING = "CHECKING"
    READY = "READY"
    DUPLICATE = "DUPLICATE"
    ADDING = "ADDING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    ANKI_OFFLINE = "ANKI_OFFLINE"


def snapshot(card: CardModel) -> CardModel:
    return CardModel(front=card.front, back=card.back)


def short_deck(name: str, limit: int = 20) -> str:
    """显示用牌组名：太长时保留末级，避免把按钮/状态栏撑爆。"""
    if len(name) <= limit:
        return name
    leaf = name.split("::")[-1]
    if len(leaf) + 3 <= limit:
        return "…::" + leaf
    return "…" + leaf[-(limit - 1):]


class QuickAddController:
    def __init__(
        self,
        checker: DuplicateChecker,
        view,
        schedule: Optional[Callable[[int, Callable[[], None]], None]] = None,
        executor: Optional[Callable[[Callable[[], object], Callable], None]] = None,
        debounce_ms: int = 250,
        initial_deck: str | None = None,
    ):
        self.checker = checker
        self.view = view
        self._schedule = schedule or (lambda _delay, fn: fn())
        self._executor = executor or (lambda job, done: done(job(), None))
        self.debounce_ms = debounce_ms

        self.card = CardModel()
        self.paste_text = ""
        self.state = State.EMPTY
        self.message = ""

        self.default_deck = checker.adapter.config.deck_name
        self.deck = (initial_deck or self.default_deck).strip() or self.default_deck
        self.deck_names: tuple[str, ...] = ()

        self._gen = 0
        self._debounce_token = 0
        self._last_check: Optional[tuple[str, object]] = None
        self._capabilities_ok: Optional[bool] = None

    def set_deck(self, name: str) -> None:
        name = (name or "").strip()
        if not name or name == self.deck:
            return
        if self.state is State.ADDING:
            log.info("提交中，忽略换牌组：%s", name)
            return
        log.info("切换牌组：%s → %s", self.deck, name)
        self.deck = name
        self._capabilities_ok = None
        self._last_check = None
        self._invalidate()
        self.view.set_deck(name)
        if self.card.is_complete():
            self._ensure_capabilities(self._schedule_check)

    def _set_state(self, state: State, message: str) -> None:
        self.state = state
        self.message = message
        log.debug("state=%s message=%s", state.value, message)
        self.view.set_status(message, self._level(state))
        self.view.set_enabled(self._is_add_enabled(state), state is not State.ADDING)

    def _is_add_enabled(self, state: State) -> bool:
        if state is State.READY:
            return True
        if state in (State.ERROR, State.ANKI_OFFLINE):
            return self.card.is_complete()
        return False

    @staticmethod
    def _level(state: State) -> str:
        if state in (State.READY, State.SUCCESS):
            return "ok"
        if state in (State.INVALID, State.DUPLICATE, State.ERROR, State.ANKI_OFFLINE):
            return "warn"
        return "info"

    def startup(self) -> None:
        gen = self._gen

        def done() -> None:
            if gen != self._gen:
                return
            if self.card.front.strip():
                self._schedule_check()
            else:
                self._set_state(State.EMPTY, "")

        self._ensure_capabilities(done)

    def paste_changed(self, raw: str) -> None:
        self.paste_text = raw
        self._invalidate()

        if not raw.strip():
            self.card = CardModel()
            self.view.clear_preview()
            self._set_state(State.EMPTY, "")
            return

        try:
            card = parse_card(raw)
        except CardError as exc:
            log.info("parse failure: %s", exc)
            self.card = CardModel()
            self.view.clear_preview()
            self._set_state(State.INVALID, "⚠ " + self._describe_parse_error(exc))
            return

        self.card = card
        hint = ""
        if card.ignored_keys:
            hint = "已忽略未知字段：" + "、".join(card.ignored_keys)
        self.view.set_card(card.front, card.back, hint)
        self._ensure_capabilities(self._schedule_check)

    @staticmethod
    def _describe_parse_error(exc: CardError) -> str:
        text = str(exc)
        if "Extra data" in text:
            return "无法解析 JSON：粘贴区可能还残留上一张内容（Extra data）"
        if text.startswith(("无法解析 JSON", "JSON 顶层", "粘贴内容为空")):
            return text
        return f"无法解析 JSON：{text}"

    def front_changed(self, text: str) -> None:
        self.card.front = text
        self._after_edit(recheck=True)

    def back_changed(self, text: str) -> None:
        self.card.back = text
        self._after_edit(recheck=False)

    def _after_edit(self, recheck: bool) -> None:
        if self.state is State.ADDING:
            return
        self._invalidate()
        if not self.card.front.strip():
            self._set_state(State.INVALID, "⚠ 正面为空")
            return
        if not self.card.back.strip():
            self._set_state(State.INVALID, "⚠ 背面为空")
            return
        if recheck:
            self._schedule_check()
            return
        if self._last_check and self._last_check[0] == self.card.front:
            self._apply_check(self._last_check[1])
        else:
            self._schedule_check()

    def _schedule_check(self) -> None:
        self._set_state(State.CHECKING, "正在查重…")
        token = self._debounce_token

        def fire() -> None:
            if token != self._debounce_token:
                return
            self._start_check()

        self._schedule(self.debounce_ms, fire)

    def _start_check(self) -> None:
        self._gen += 1
        gen = self._gen
        card = snapshot(self.card)

        def job():
            return self.checker.check(card, self.deck)

        def done(result, error) -> None:
            if gen != self._gen:
                log.debug("丢弃过期查重结果：%r", card.front)
                return
            if error is not None:
                self._capabilities_ok = None
                log.exception("查重异常", exc_info=error)
                self._set_state(State.ERROR, f"⚠ 查重失败：{error}")
                return
            self._last_check = (card.front, result)
            self._apply_check(result)

        self._executor(job, done)

    def _apply_check(self, result) -> None:
        if result.state == "READY":
            self._set_state(State.READY, f"✓ {result.message}")
        elif result.state == "DUPLICATE":
            self._set_state(State.DUPLICATE, f"⚠ {result.message}")
        elif result.state == "OFFLINE":
            self._capabilities_ok = None
            self.view.set_connection("Anki 未连接", "warn")
            self._set_state(State.ANKI_OFFLINE, "⚠ 未连接 Anki")
        elif result.state == "EMPTY":
            self._set_state(State.INVALID, "⚠ 正面为空")
        else:
            self._capabilities_ok = None
            self._set_state(State.ERROR, f"⚠ {result.message}")

    def _ensure_capabilities(self, then: Callable[[], None]) -> None:
        if self._capabilities_ok:
            then()
            return

        request_gen = self._gen
        request_deck = self.deck
        self.view.set_connection("Anki 连接中…", "info")
        self._set_state(State.CHECKING, "正在连接 Anki…")

        def job():
            try:
                return "ok", self.checker.adapter.verify_capabilities(request_deck)
            except AnkiOfflineError as exc:
                return "offline", exc
            except AnkiError as exc:
                return "error", exc

        def done(result, error) -> None:
            if request_gen != self._gen or request_deck != self.deck:
                log.debug("丢弃过期能力校验结果：deck=%s", request_deck)
                return
            if error is not None:
                self._capabilities_ok = None
                self.view.set_connection("Anki 连接异常", "warn")
                log.exception("能力校验异常", exc_info=error)
                self._set_state(State.ERROR, f"⚠ 检查 Anki 配置失败：{error}")
                return
            kind, payload = result
            if kind == "offline":
                self._capabilities_ok = None
                self.view.set_connection("Anki 未连接", "warn")
                self._set_state(State.ANKI_OFFLINE, "⚠ 未连接 Anki")
                return
            if kind == "error":
                self._capabilities_ok = None
                self.view.set_connection("Anki 连接异常", "warn")
                self._set_state(State.ERROR, f"⚠ {payload}")
                return

            self.view.set_connection("Anki 已连接", "ok")
            if payload.decks:
                self.deck_names = payload.decks
                selected_deck = self.deck if self.deck in payload.decks else payload.decks[0]
                if selected_deck != self.deck:
                    log.info("使用 Anki 中的可用牌组：%s", selected_deck)
                    self.deck = selected_deck
                self.view.set_deck_choices(payload.decks)
                self.view.set_deck(self.deck)
            if payload.ok():
                self._capabilities_ok = True
                then()
            else:
                self._capabilities_ok = None
                log.warning("能力校验未通过：%s", payload.problem)
                self._set_state(State.ERROR, f"⚠ {payload.problem}")

        self._executor(job, done)

    def add_clicked(self) -> None:
        if self.state in (State.ERROR, State.ANKI_OFFLINE) and self.card.is_complete():
            log.info("失败后重试：重新探测 Anki 并查重")
            self._ensure_capabilities(self._schedule_check)
            return
        if self.state is not State.READY:
            return
        if not self.card.is_complete():
            return
        self._invalidate()
        card = snapshot(self.card)
        self._set_state(State.ADDING, "正在加入…")

        def job():
            return self.checker.add(card, self.deck)

        def done(result, error) -> None:
            if error is not None:
                log.exception("添加异常", exc_info=error)
                self._set_state(State.ERROR, f"⚠ 添加失败：{error}")
                return
            self._on_add_done(result)

        self._executor(job, done)

    def _on_add_done(self, result) -> None:
        if result.state == "ADDED":
            self._invalidate()
            self._last_check = None
            self.card = CardModel()
            self.paste_text = ""
            self.view.clear_card()
            self.view.focus_paste()
            text = f"✓ {result.message}"
            if result.deck:
                text += f" → {short_deck(result.deck, 42)}"
            self._set_state(State.SUCCESS, text)
        elif result.state == "DUPLICATE":
            self._set_state(State.DUPLICATE, f"⚠ {result.message}")
        elif result.state == "OFFLINE":
            self._capabilities_ok = None
            self.view.set_connection("Anki 未连接", "warn")
            self._set_state(State.ANKI_OFFLINE, f"⚠ {result.message}")
        else:
            self._capabilities_ok = None
            self._set_state(State.ERROR, f"⚠ {result.message}")

    def clear_clicked(self) -> None:
        if self.state is State.ADDING:
            log.info("提交中，忽略清空")
            return
        self._invalidate()
        self._last_check = None
        self.card = CardModel()
        self.paste_text = ""
        self.view.clear_card()
        self.view.focus_paste()
        self._set_state(State.EMPTY, "")

    def _invalidate(self) -> None:
        self._gen += 1
        self._debounce_token += 1
