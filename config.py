# -*- coding: utf-8 -*-
"""AQA 唯一配置源。

v1 只有这一份配置：不读取 anki-bridge/config.json，不做 GUI 设置页，不做多环境配置。
文件缺失/损坏 → 用内置默认值，程序照常启动（只记日志、不阻塞）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

# In a frozen build prefer a user-edited config next to the EXE, then
# fall back to the bundled default under PyInstaller's runtime directory.
# In source mode config.json remains next to config.py.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _bundled_config = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "config.json")
    _external_config = os.path.join(APP_DIR, "config.json")
    CONFIG_PATH = _external_config if os.path.exists(_external_config) else _bundled_config
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "deck_name": "",
    "note_type": "问答题",
    "front_field": "正面",
    "back_field": "背面",
    "anki_connect_url": "http://127.0.0.1:8765",
}


@dataclass(frozen=True)
class Config:
    deck_name: str
    note_type: str
    front_field: str
    back_field: str
    anki_connect_url: str
    http_timeout: float = 5.0


def load_config(path: str | None = None) -> tuple[Config, list[str]]:
    """读取配置，返回 (Config, 警告列表)。任何异常都不抛出，降级为默认值。"""
    path = path or CONFIG_PATH
    warnings: list[str] = []
    raw: dict = {}

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:  # utf-8-sig：容忍 BOM
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded
            else:
                warnings.append("config.json 顶层不是对象，已使用内置默认值")
        except Exception as exc:  # 损坏的配置不应阻止程序启动
            warnings.append(f"config.json 读取失败，已使用内置默认值（{exc}）")

    values = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if key == "deck_name":
            if not isinstance(value, str):
                if key in raw:
                    warnings.append("配置项 deck_name 非法，已改为空；连接 Anki 后从真实牌组中选择")
                value = ""
            values[key] = value.strip()
            continue
        if not isinstance(value, str) or not value.strip():
            if key in raw:
                warnings.append(f"配置项 {key} 非法，已使用默认值：{default}")
            value = default
        values[key] = value.strip()

    unknown = [k for k in raw if k not in DEFAULTS]
    if unknown:
        warnings.append("config.json 中未知配置项已忽略：" + ", ".join(sorted(unknown)))

    return Config(**values), warnings
