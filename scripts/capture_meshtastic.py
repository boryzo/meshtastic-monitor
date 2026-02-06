from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


def _sanitize(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_write(path: Path, _name: str, data: Any) -> None:
    cleaned = _sanitize(data)
    path.write_text(json.dumps(cleaned, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture raw Meshtastic data for fixtures.")
    parser.add_argument("--host", required=True, help="Meshtastic TCP host/IP")
    parser.add_argument("--port", type=int, default=4403, help="Meshtastic TCP port (default: 4403)")
    parser.add_argument("--seconds", type=int, default=15, help="Capture duration")
    parser.add_argument("--max-packets", type=int, default=50, help="Max packets to collect")
    parser.add_argument("--out", default="backend/tests/fixtures/live", help="Output directory")
    args = parser.parse_args()

    from meshtastic.tcp_interface import TCPInterface  # type: ignore
    from pubsub import pub  # type: ignore

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    packets: List[Dict[str, Any]] = []

    def on_receive(packet=None, interface=None, **kwargs) -> None:  # noqa: ANN001
        pkt = packet if packet is not None else kwargs.get("packet")
        if isinstance(pkt, dict):
            packets.append(pkt)

    try:
        iface = TCPInterface(args.host, args.port)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to connect: {exc}", file=sys.stderr)
        return 1

    try:
        pub.subscribe(on_receive, "meshtastic.receive")
        start = time.time()
        while time.time() - start < args.seconds and len(packets) < args.max_packets:
            time.sleep(0.1)
    except Exception as exc:  # noqa: BLE001
        print(f"Capture error: {exc}", file=sys.stderr)
    finally:
        nodes = getattr(iface, "nodes", None)
        local_node = getattr(iface, "localNode", None)
        channels = getattr(iface, "channels", None)

        _safe_write(out_dir / "packets.json", "PACKETS", packets)
        _safe_write(out_dir / "nodes.json", "NODES", nodes)
        _safe_write(out_dir / "local_node.json", "LOCAL_NODE", local_node)
        _safe_write(out_dir / "channels.json", "CHANNELS", channels)

        try:
            iface.close()
        except Exception as exc:  # noqa: BLE001
            print(f"Close error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
