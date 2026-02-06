from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_SMS_API_URL = ""
DEFAULT_CONFIG_FILENAME = "meshmon.ini"


def resolve_config_path(raw: Optional[str] = None) -> Path:
    value = (raw or os.getenv("MESHMON_CONFIG") or "").strip()
    path = Path(value) if value else Path.cwd() / DEFAULT_CONFIG_FILENAME
    path = path.expanduser()
    if path.exists() and path.is_dir():
        path = path / DEFAULT_CONFIG_FILENAME
    return path


def default_config() -> Dict[str, Dict[str, str]]:
    return {
        "mesh": {
            "host": "",
            "port": "4403",
        },
        "http": {
            "port": "8880",
        },
        "sms": {
            "enabled": "false",
            "api_url": DEFAULT_SMS_API_URL,
            "api_key": "",
            "phone": "",
            "allow_from_ids": "ALL",
            "allow_types": "ALL",
        },
        "stats": {
            "nodes_history_interval_sec": "60",
        },
    }


def ensure_config(path: Path) -> None:
    if path.exists():
        return
    cfg = configparser.ConfigParser()
    for section, values in default_config().items():
        cfg[section] = dict(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        cfg.write(f)


def load_config(path: Path) -> configparser.ConfigParser:
    ensure_config(path)
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    return cfg


def update_config(path: Path, updates: Dict[str, Dict[str, Any]]) -> configparser.ConfigParser:
    cfg = load_config(path)
    for section, values in updates.items():
        if section not in cfg:
            cfg[section] = {}
        for key, value in values.items():
            if value is None:
                continue
            cfg[section][key] = str(value)
    with path.open("w", encoding="utf-8") as f:
        cfg.write(f)
    return cfg


def get_value(cfg: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    if cfg.has_option(section, key):
        return cfg.get(section, key).strip()
    return default


def get_bool(cfg: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    if cfg.has_option(section, key):
        try:
            return cfg.getboolean(section, key)
        except Exception:
            return default
    return default
