from __future__ import annotations

import argparse
import os
import sys

from backend.app import main as run_app


def _prompt(value: str, label: str) -> str:
    if value.strip():
        return value
    try:
        return input(label).strip()
    except EOFError:
        return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Meshtastic Monitor")
    parser.add_argument("--host", "--mesh-host", dest="mesh_host", help="Meshtastic host/IP")
    parser.add_argument("--mesh-port", dest="mesh_port", help="Meshtastic TCP port (default 4403)")
    parser.add_argument("--http-port", dest="http_port", help="HTTP port for web app (default 8880)")
    parser.add_argument(
        "--nodes-history-interval",
        dest="nodes_history_interval",
        help="Node history sample interval in seconds (default 60)",
    )
    args = parser.parse_args(argv)

    mesh_host = _prompt(args.mesh_host or os.getenv("MESH_HOST", ""), "Meshtastic host/IP: ")
    mesh_port = args.mesh_port or os.getenv("MESH_PORT", "4403")
    http_port = args.http_port or os.getenv("HTTP_PORT", "8880")
    nodes_history = args.nodes_history_interval or os.getenv("NODES_HISTORY_INTERVAL_SEC", "60")

    os.environ["MESH_HOST"] = mesh_host
    os.environ["MESH_PORT"] = str(mesh_port)
    os.environ["HTTP_PORT"] = str(http_port)
    os.environ["NODES_HISTORY_INTERVAL_SEC"] = str(nodes_history)

    run_app()


if __name__ == "__main__":
    main(sys.argv[1:])
