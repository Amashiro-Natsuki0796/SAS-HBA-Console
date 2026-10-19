"""轻量配置持久化（JSON，位于 XDG 配置目录）。"""

from __future__ import annotations

import json
import os
import tempfile

DEFAULTS = {
    "theme": "dark",
    "language": "zh_CN",
    "auto_refresh": True,
    "auto_refresh_seconds": 60,
    "wake_on_smart": False,
    "window_width": 1500,
    "window_height": 880,
    "paned_position": 990,
}

ALLOWED_INTERVALS = (30, 60, 120, 300)


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "sas-hba-console")


def config_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def _legacy_config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "lsi9207-console", "settings.json")


def load() -> dict:
    data = dict(DEFAULTS)
    path = config_path()
    if not os.path.exists(path):
        # 从 1.x 的配置目录平滑迁移
        legacy = _legacy_config_path()
        if os.path.exists(legacy):
            path = legacy
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                    data[key] = value
    except (OSError, ValueError):
        pass
    if data["theme"] not in ("dark", "light"):
        data["theme"] = "dark"
    if data["auto_refresh_seconds"] not in ALLOWED_INTERVALS:
        data["auto_refresh_seconds"] = 60
    data["window_width"] = min(max(int(data["window_width"]), 1100), 3840)
    data["window_height"] = min(max(int(data["window_height"]), 700), 2160)
    data["paned_position"] = min(max(int(data["paned_position"]), 700), 2400)
    return data


def save(data: dict) -> None:
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass
