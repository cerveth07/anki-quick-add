# -*- coding: utf-8 -*-
"""Optional image payload for main-window cards.

The JSON card format stays strictly front/back.  This immutable attachment
travels beside a CardModel, not inside the user-provided JSON.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


@dataclass(frozen=True)
class ImageAttachment:
    png: bytes
    width: int
    height: int

    def __post_init__(self) -> None:
        if not self.png or len(self.png) > MAX_IMAGE_BYTES:
            raise ValueError("图片文件为空或超过 10 MB")
        if self.width <= 0 or self.height <= 0 or self.width * self.height > MAX_IMAGE_PIXELS:
            raise ValueError("图片尺寸超出允许范围")
        if not self.png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("图片必须是有效的 PNG 数据")

    @property
    def filename(self) -> str:
        digest = hashlib.sha256(self.png).hexdigest()[:24]
        return f"aqa_{digest}.png"

    def picture(self, back_field: str) -> dict:
        """AnkiConnect addNote.picture (no separate media transaction required)."""
        return {
            "data": base64.b64encode(self.png).decode("ascii"),
            "filename": self.filename,
            "fields": [back_field],
        }
