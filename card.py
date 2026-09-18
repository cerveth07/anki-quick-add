# -*- coding: utf-8 -*-
"""CardModel + JSON 解析 + 校验。

本模块只处理**纯文本**：不含 HTML 转换、不含 GUI 依赖、不含网络依赖，因此可以独立测试。
ChatGPT 输出 → 纯文本卡片，这一层是唯一的入口。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# 允许的围栏语言标记（`\`\`\`json` / 空标记）
_FENCE_TAGS = ("", "json", "jsonc", "json5")


class CardError(ValueError):
    """解析/校验失败。str(exc) 可直接显示给用户。"""


@dataclass
class CardModel:
    front: str = ""
    back: str = ""
    ignored_keys: tuple[str, ...] = field(default_factory=tuple)

    def is_complete(self) -> bool:
        return bool(self.front.strip()) and bool(self.back.strip())


def _strip_fence(text: str) -> str:
    """整段内容被 ``` 围栏包住时剥掉围栏，否则原样返回。"""
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return stripped
    body = stripped[3:-3]
    newline = body.find("\n")
    if newline != -1:
        tag = body[:newline].strip().lower()
        if tag in _FENCE_TAGS:
            body = body[newline + 1:]
    return body.strip()


def parse_card(raw: str) -> CardModel:
    """把粘贴内容解析为 CardModel；失败抛 CardError。"""
    text = (raw or "").lstrip("\ufeff").strip()  # BOM：Windows 剪贴板常见
    if not text:
        raise CardError("粘贴内容为空")

    text = _strip_fence(text)
    if not text:
        raise CardError("粘贴内容为空")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardError(f"无法解析 JSON：{exc.msg}（第 {exc.lineno} 行）") from None

    if not isinstance(data, dict):
        raise CardError('JSON 顶层必须是对象，例如 {"front": "...", "back": "..."}')

    ignored = tuple(str(k) for k in data if k not in ("front", "back"))

    values = {}
    for key in ("front", "back"):
        if key not in data:
            raise CardError(f"缺少 {key}")
        value = data[key]
        if not isinstance(value, str):
            raise CardError(f"{key} 必须是字符串")
        if not value.strip():
            raise CardError(f"{key} 为空")
        # strip 后入库：Anki 判重不去首尾空白，肉眼同词会绕过判重（上游 issue #5184）
        values[key] = value.strip()

    return CardModel(front=values["front"], back=values["back"], ignored_keys=ignored)
