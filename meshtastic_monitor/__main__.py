from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

from backend.app import main as run_app
from backend.config_store import (
    DEFAULT_SMS_API_URL,
    get_bool,
    get_value,
    load_config,
    resolve_config_path,
    update_config,
)


_DEFAULT_LOG_FILE = "meshmon.log"


def _prompt(value: str, label: str) -> str:
    if value.strip():
        return value
    try:
        return input(label).strip()
    except EOFError:
        return value


def _resolve_log_path(raw: str | None) -> Path:
    value = (raw or os.getenv("MESHMON_LOG_FILE") or os.getenv("LOG_FILE") or "").strip()
    if not value:
        return Path.cwd() / _DEFAULT_LOG_FILE

    p = Path(value).expanduser()
    if value.endswith(("/", os.sep)):
        return p / _DEFAULT_LOG_FILE
    if p.exists() and p.is_dir():
        return p / _DEFAULT_LOG_FILE
    return p


def _parse_bool(value: str | None) -> Optional[bool]:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _coalesce_str(*values: str | None) -> str:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
        else:
            return str(v)
    return ""


def _configure_logging(*, log_level: str | None, log_file: str | None) -> None:
    level_name = (log_level or os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    handlers: list[logging.Handler] = []
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    handlers.append(stream)

    log_path = _resolve_log_path(log_file)
    file_err: Exception | None = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except Exception as e:  # pragma: no cover
        file_err = e

    logging.basicConfig(level=level, handlers=handlers, force=True)

    log = logging.getLogger("meshtastic_monitor")
    if file_err is None:
        log.info("Logging to %s", str(log_path))
    else:  # pragma: no cover
        log.warning("Failed to open log file %s (%s). Logging to console only.", str(log_path), file_err)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Meshtastic Monitor")
    parser.add_argument("--config", dest="config_path", help="Config file path (default: ./meshmon.ini)")
    parser.add_argument("--host", "--mesh-host", dest="mesh_host", help="Meshtastic host/IP")
    parser.add_argument("--mesh-port", dest="mesh_port", help="Meshtastic TCP port (default 4403)")
    parser.add_argument("--http-port", dest="http_port", help="HTTP port for web app (default 8880)")
    parser.add_argument(
        "--log-file",
        dest="log_file",
        help=f"Log file path (default: ./{_DEFAULT_LOG_FILE})",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--nodes-history-interval",
        dest="nodes_history_interval",
        help="Node history sample interval in seconds (default 60)",
    )
    parser.add_argument(
        "--stats-cache-minutes",
        dest="stats_cache_minutes",
        help="Stats cache refresh interval in minutes (default 30)",
    )
    parser.add_argument("--sms-api-url", dest="sms_api_url", help="SMS API base URL")
    parser.add_argument("--sms-api-key", dest="sms_api_key", help="SMS API key")
    parser.add_argument("--sms-phone", dest="sms_phone", help="SMS destination phone")
    parser.add_argument("--relay-host", dest="relay_host", help="TCP relay listen host (default 0.0.0.0)")
    parser.add_argument("--relay-port", dest="relay_port", help="TCP relay listen port (default 4403)")
    parser.add_argument(
        "--sms-allow-from",
        dest="sms_allow_from",
        help="Allowed sender IDs for SMS relay (comma-separated or ALL)",
    )
    parser.add_argument(
        "--sms-allow-types",
        dest="sms_allow_types",
        help="Allowed message types for SMS relay (comma-separated or ALL)",
    )
    parser.add_argument("--relay-enabled", dest="relay_enabled", action="store_true", help="Enable TCP relay")
    parser.add_argument("--relay-disabled", dest="relay_enabled", action="store_false", help="Disable TCP relay")
    parser.add_argument("--sms-enabled", dest="sms_enabled", action="store_true", help="Enable SMS relay")
    parser.add_argument("--sms-disabled", dest="sms_enabled", action="store_false", help="Disable SMS relay")
    parser.set_defaults(sms_enabled=None)
    parser.set_defaults(relay_enabled=None)
    args = parser.parse_args(argv)

    _configure_logging(log_level=args.log_level, log_file=args.log_file)

    config_path = resolve_config_path(args.config_path)
    cfg = load_config(config_path)

    mesh_host_cfg = get_value(cfg, "mesh", "host", "")
    mesh_port_cfg = get_value(cfg, "mesh", "port", "4403")
    http_port_cfg = get_value(cfg, "http", "port", "8880")
    relay_enabled_cfg = get_bool(cfg, "relay", "enabled", False)
    relay_host_cfg = get_value(cfg, "relay", "listen_host", "0.0.0.0")
    relay_port_cfg = get_value(cfg, "relay", "listen_port", "4403")
    nodes_history_cfg = get_value(cfg, "stats", "nodes_history_interval_sec", "60")
    stats_cache_cfg = get_value(cfg, "stats", "stats_cache_minutes", "30")
    sms_enabled_cfg = get_bool(cfg, "sms", "enabled", False)
    sms_api_url_cfg = get_value(cfg, "sms", "api_url", DEFAULT_SMS_API_URL)
    sms_api_key_cfg = get_value(cfg, "sms", "api_key", "")
    sms_phone_cfg = get_value(cfg, "sms", "phone", "")
    sms_allow_from_cfg = get_value(cfg, "sms", "allow_from_ids", "ALL")
    sms_allow_types_cfg = get_value(cfg, "sms", "allow_types", "ALL")

    mesh_host = _coalesce_str(args.mesh_host, os.getenv("MESH_HOST"), mesh_host_cfg)
    mesh_host = _prompt(mesh_host, "Meshtastic host/IP: ")
    mesh_port = _coalesce_str(args.mesh_port, os.getenv("MESH_PORT"), mesh_port_cfg) or "4403"
    http_port = _coalesce_str(args.http_port, os.getenv("HTTP_PORT"), http_port_cfg) or "8880"
    nodes_history = _coalesce_str(
        args.nodes_history_interval, os.getenv("NODES_HISTORY_INTERVAL_SEC"), nodes_history_cfg
    ) or "60"
    stats_cache_minutes = _coalesce_str(
        args.stats_cache_minutes, os.getenv("STATS_CACHE_MINUTES"), stats_cache_cfg
    ) or "30"

    relay_enabled_env = _parse_bool(os.getenv("RELAY_ENABLED"))
    relay_enabled = args.relay_enabled if args.relay_enabled is not None else relay_enabled_env
    if relay_enabled is None:
        relay_enabled = relay_enabled_cfg
    relay_host = _coalesce_str(args.relay_host, os.getenv("RELAY_HOST"), relay_host_cfg) or "0.0.0.0"
    relay_port = _coalesce_str(args.relay_port, os.getenv("RELAY_PORT"), relay_port_cfg) or "4403"

    sms_enabled_env = _parse_bool(os.getenv("SMS_ENABLED"))
    sms_enabled = args.sms_enabled if args.sms_enabled is not None else sms_enabled_env
    if sms_enabled is None:
        sms_enabled = sms_enabled_cfg
    sms_api_url = _coalesce_str(args.sms_api_url, os.getenv("SMS_API_URL"), sms_api_url_cfg)
    sms_api_key = _coalesce_str(args.sms_api_key, os.getenv("SMS_API_KEY"), sms_api_key_cfg)
    sms_phone = _coalesce_str(args.sms_phone, os.getenv("SMS_PHONE"), sms_phone_cfg)
    sms_allow_from_ids = _coalesce_str(
        args.sms_allow_from, os.getenv("SMS_ALLOW_FROM_IDS"), sms_allow_from_cfg
    )
    sms_allow_types = _coalesce_str(
        args.sms_allow_types, os.getenv("SMS_ALLOW_TYPES"), sms_allow_types_cfg
    )

    os.environ["MESH_HOST"] = mesh_host
    os.environ["MESH_PORT"] = str(mesh_port)
    os.environ["HTTP_PORT"] = str(http_port)
    os.environ["NODES_HISTORY_INTERVAL_SEC"] = str(nodes_history)
    os.environ["STATS_CACHE_MINUTES"] = str(stats_cache_minutes)
    os.environ["RELAY_ENABLED"] = "1" if relay_enabled else "0"
    os.environ["RELAY_HOST"] = str(relay_host)
    os.environ["RELAY_PORT"] = str(relay_port)
    os.environ["MESHMON_CONFIG"] = str(config_path)
    os.environ["SMS_ENABLED"] = "1" if sms_enabled else "0"
    os.environ["SMS_API_URL"] = str(sms_api_url or "")
    os.environ["SMS_API_KEY"] = str(sms_api_key or "")
    os.environ["SMS_PHONE"] = str(sms_phone or "")
    os.environ["SMS_ALLOW_FROM_IDS"] = str(sms_allow_from_ids or "")
    os.environ["SMS_ALLOW_TYPES"] = str(sms_allow_types or "")
    if args.log_level:
        os.environ["LOG_LEVEL"] = str(args.log_level)

    update_config(
        config_path,
        {
            "mesh": {"host": mesh_host, "port": str(mesh_port)},
            "http": {"port": str(http_port)},
            "relay": {
                "enabled": "true" if relay_enabled else "false",
                "listen_host": relay_host,
                "listen_port": str(relay_port),
            },
            "stats": {
                "nodes_history_interval_sec": str(nodes_history),
                "stats_cache_minutes": str(stats_cache_minutes),
            },
            "sms": {
                "enabled": "true" if sms_enabled else "false",
                "api_url": sms_api_url,
                "api_key": sms_api_key,
                "phone": sms_phone,
                "allow_from_ids": sms_allow_from_ids,
                "allow_types": sms_allow_types,
            },
        },
    )

    run_app()


if __name__ == "__main__":
    main(sys.argv[1:])
