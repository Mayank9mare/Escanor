"""
state.py

Small choices made from the HUD or menubar that should survive a
restart (which voice is on), kept in ~/.escanor/state.json. Settings you
edit by hand stay in config.toml; this file is Escanor's to rewrite.
"""
from __future__ import annotations
import json
from typing import Any
from . import config


def _path():
    return config.data_dir() / "state.json"


def get(key: str, default: Any = None) -> Any:
    try:
        return json.loads(_path().read_text()).get(key, default)
    except (OSError, ValueError):
        return default


def set(key: str, value: Any) -> None:  # noqa: A001 -- reads as state.set(...)
    path = _path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
