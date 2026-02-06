from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from backend.app import main as run_app


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
    args = parser.parse_args(argv)

    _configure_logging(log_level=args.log_level, log_file=args.log_file)

    mesh_host = _prompt(args.mesh_host or os.getenv("MESH_HOST", ""), "Meshtastic host/IP: ")
    mesh_port = args.mesh_port or os.getenv("MESH_PORT", "4403")
    http_port = args.http_port or os.getenv("HTTP_PORT", "8880")
    nodes_history = args.nodes_history_interval or os.getenv("NODES_HISTORY_INTERVAL_SEC", "60")

    os.environ["MESH_HOST"] = mesh_host
    os.environ["MESH_PORT"] = str(mesh_port)
    os.environ["HTTP_PORT"] = str(http_port)
    os.environ["NODES_HISTORY_INTERVAL_SEC"] = str(nodes_history)
    if args.log_level:
        os.environ["LOG_LEVEL"] = str(args.log_level)

    run_app()


if __name__ == "__main__":
    main(sys.argv[1:])
