# -*- coding: utf-8 -*-
"""Anki Adapter + 查重。

这是本程序**唯一**与 AnkiConnect 通信的地方：
- 纯文本 → Anki HTML 的转换只在这里发生（CardModel 始终是纯文本）；
- 所有 HTTP / AnkiConnect 错误都收敛成 AnkiError 子类，GUI 只消费人类可读文案；
- 查重用 canAddNotes（Anki 原生判定），不拼查询串、不做转义、不使用 findNotes。

测试时可注入 transport，无需真实 Anki。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from card import CardModel
from config import Config


# --------------------------------------------------------------------------
# 错误分层
# --------------------------------------------------------------------------
class AnkiError(Exception):
    """Anki 层错误基类；str(exc) 可直接显示。"""


class AnkiOfflineError(AnkiError):
    """连不上 Anki（未启动 / 插件未装 / 超时）。"""


class AnkiResponseError(AnkiError):
    """连上了但响应异常（error 字段 / HTTP 状态码 / 非法 JSON）。"""


class AnkiConfigError(AnkiError):
    """部署侧配置错误（牌组 / 笔记类型 / 字段不匹配）。"""


# --------------------------------------------------------------------------
# 纯文本 → Anki 字段
# --------------------------------------------------------------------------
_MULTI_NEWLINE = re.compile(r"\r\n|\r|\n")


def to_anki_html(text: str) -> str:
    """纯文本转 Anki 字段 HTML：先转义 & < >，再把换行转成 <br>。

    Anki 字段按 HTML 渲染，裸换行会被折叠成空格（本库既有卡片约定即 <br>）。
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MULTI_NEWLINE.sub("<br>", escaped)


def build_note(config: Config, card: CardModel, deck_name: str | None = None) -> dict:
    """构造 AnkiConnect note 载荷。

    deck 只能来自本机：默认取配置，或用户在界面上手选（deck_name）。
    绝不接受输入 JSON 里的 deck 字段（§7.2）。
    """
    return {
        "deckName": deck_name or config.deck_name,
        "modelName": config.note_type,
        "fields": {
            config.front_field: to_anki_html(card.front),
            config.back_field: to_anki_html(card.back),
        },
        "options": {"allowDuplicate": False},
        "tags": [],
    }


# --------------------------------------------------------------------------
# HTTP transport（可替换）
# --------------------------------------------------------------------------
def _http_invoke(url: str, timeout: float, action: str, **params) -> Any:
    body = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 到达了服务但状态码异常
        raise AnkiResponseError(f"AnkiConnect HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise AnkiOfflineError(f"未连接 Anki（{reason}）") from None
    except ValueError as exc:
        raise AnkiResponseError(f"AnkiConnect 响应不是合法 JSON（{exc}）") from None

    if not isinstance(payload, dict):
        raise AnkiResponseError("AnkiConnect 响应格式异常")
    if payload.get("error"):
        raise AnkiResponseError(str(payload["error"]))
    return payload.get("result")


# --------------------------------------------------------------------------
# 结果类型
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Capabilities:
    """使用前的配置校验结果。"""

    version: int
    deck_ok: bool
    note_type_ok: bool
    fields_ok: bool
    problem: str = ""  # 空串表示校验通过
    actual_fields: tuple[str, ...] = ()
    decks: tuple[str, ...] = ()  # 全量牌组名，供界面下拉使用

    def ok(self) -> bool:
        return not self.problem


@dataclass(frozen=True)
class CheckResult:
    """查重结果。state: READY / DUPLICATE / OFFLINE / ERROR / EMPTY"""

    state: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class AddResult:
    """添加结果。state: ADDED / DUPLICATE / OFFLINE / ERROR / CONFIG"""

    state: str
    message: str
    note_id: int | None = None
    detail: str = ""
    deck: str = ""  # 实际写入的牌组，供界面在成功文案里显示


# --------------------------------------------------------------------------
# Adapter
# --------------------------------------------------------------------------
class AnkiAdapter:
    def __init__(self, config: Config, transport: Callable[..., Any] | None = None):
        self.config = config
        if transport is None:
            transport = lambda action, **params: _http_invoke(  # noqa: E731
                config.anki_connect_url, config.http_timeout, action, **params
            )
        self._transport = transport

    # --- 基础接口（全部经 transport，便于测试） ---
    def version(self) -> int:
        return int(self._transport("version"))

    def deck_names(self) -> tuple[str, ...]:
        return tuple(self._transport("deckNames") or ())

    def model_names(self) -> tuple[str, ...]:
        return tuple(self._transport("modelNames") or ())

    def model_field_names(self, model: str) -> tuple[str, ...]:
        return tuple(self._transport("modelFieldNames", modelName=model) or ())

    def can_add_note(self, note: dict) -> bool:
        result = self._transport("canAddNotes", notes=[note])
        return bool(result and result[0])

    def add_note(self, note: dict) -> int | None:
        """返回 note id；重复或未成功时为 None（由调用方复核）。"""
        result = self._transport("addNote", note=note)
        return int(result) if result else None

    # --- 能力校验 ---
    def verify_capabilities(self, deck_name: str | None = None) -> Capabilities:
        """校验 version / 牌组 / 笔记类型 / 字段。连接问题原样抛 AnkiOfflineError。"""
        config = self.config
        deck = (deck_name or config.deck_name).strip()
        version = self.version()
        deck_names = self.deck_names()
        deck_ok = bool(deck_names) and (not deck or deck in deck_names)
        model_names = self.model_names()
        note_type_ok = config.note_type in model_names
        actual_fields: tuple[str, ...] = ()
        fields_ok = False
        if note_type_ok:
            actual_fields = self.model_field_names(config.note_type)
            fields_ok = (
                config.front_field in actual_fields and config.back_field in actual_fields
            )

        problem = ""
        if not deck_names:
            problem = "Anki 中没有可用牌组"
        elif deck and not deck_ok:
            problem = f"牌组不存在：{deck}"
        elif not note_type_ok:
            problem = f"笔记类型不存在：{config.note_type}"
        elif not fields_ok:
            problem = (
                f"字段不匹配：{config.note_type} 的字段是 {'/'.join(actual_fields)}，"
                f"配置需要 {config.front_field}/{config.back_field}"
            )
        return Capabilities(
            version=version,
            deck_ok=deck_ok,
            note_type_ok=note_type_ok,
            fields_ok=fields_ok,
            problem=problem,
            actual_fields=actual_fields,
            decks=deck_names,
        )


# --------------------------------------------------------------------------
# 查重 / 添加
# --------------------------------------------------------------------------
class DuplicateChecker:
    """查重与添加的唯一入口（GUI 不直接碰 adapter 的写接口）。

    deck_name 由用户在界面选择的牌组决定；None 表示用配置里的默认牌组。
    注意：Anki 判重与牌组无关（同 Note Type 全库判重，已实测），所以换牌组
    理论上不改变查重结论——但本程序仍在换牌组后重查一次，见 controller.set_deck。
    """

    def __init__(self, adapter: AnkiAdapter):
        self.adapter = adapter

    def check(self, card: CardModel, deck_name: str | None = None) -> CheckResult:
        """解析成功后调用：返回 READY / DUPLICATE / OFFLINE / ERROR。"""
        if not card.front.strip():
            return CheckResult("EMPTY", "正面为空")
        note = build_note(self.adapter.config, card, deck_name)
        try:
            addable = self.adapter.can_add_note(note)
        except AnkiOfflineError as exc:
            return CheckResult("OFFLINE", "未连接 Anki", str(exc))
        except AnkiError as exc:
            return CheckResult("ERROR", f"查重失败：{exc}", str(exc))
        if addable:
            # 口径必须显式：判重范围是整个 collection（同 Note Type），不是"当前牌组"
            return CheckResult("READY", "未发现重复（全库判重）")
        return CheckResult("DUPLICATE", f"Anki 中已存在「{card.front}」（全库判重，可能在别的牌组）")

    def add(self, card: CardModel, deck_name: str | None = None) -> AddResult:
        """添加卡片。成功才允许调用方清空；任何失败路径都不清空。"""
        deck = deck_name or self.adapter.config.deck_name
        note = build_note(self.adapter.config, card, deck_name)
        try:
            note_id = self.adapter.add_note(note)
        except AnkiOfflineError as exc:
            return AddResult("OFFLINE", "添加失败：未连接 Anki", detail=str(exc), deck=deck)
        except AnkiError as exc:
            return AddResult("ERROR", f"添加失败：{exc}", detail=str(exc), deck=deck)

        if note_id:
            return AddResult("ADDED", f"已加入「{card.front}」", note_id=note_id, deck=deck)

        # result == null：AnkiConnect 文档只保证"非成功"，不专指重复 → 复核一次
        try:
            addable = self.adapter.can_add_note(note)
        except AnkiOfflineError as exc:
            return AddResult(
                "OFFLINE",
                "添加失败：未连接 Anki",
                detail=str(exc),
                deck=deck,
            )
        except AnkiError as exc:
            return AddResult(
                "ERROR",
                "添加失败：Anki 未返回 note ID，且复核失败",
                detail=str(exc),
                deck=deck,
            )
        if not addable:
            return AddResult("DUPLICATE", f"已存在，未添加：{card.front}", deck=deck)
        return AddResult("ERROR", "添加失败：Anki 未返回 note ID", deck=deck)
