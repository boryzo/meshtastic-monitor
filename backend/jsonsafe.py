from __future__ import annotations

import base64
import time
from typing import Any, Dict, Optional


def now_epoch() -> int:
    return int(time.time())


def clamp_str(value: Any, max_len: int = 400) -> Optional[str]:
    if value is None:
        return None
    try:
        out = str(value)
    except Exception:
        return None
    if len(out) > max_len:
        return out[:max_len] + "…"
    return out


def b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def quality_bucket(snr: Any) -> Optional[str]:
    if snr is None:
        return None
    try:
        s = float(snr)
    except Exception:
        return None

    if s >= 0:
        return "good"
    if s >= -7:
        return "ok"
    if s >= -12:
        return "weak"
    return "bad"


def json_safe_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a Meshtastic 'packet' dict into a small, JSON-serializable model.
    Ensures there are no bytes objects in the result.
    """
    decoded = packet.get("decoded") or {}

    portnum = decoded.get("portnum")
    text = decoded.get("text")

    payload = decoded.get("payload")
    payload_b64 = (
        b64_encode(payload) if isinstance(payload, (bytes, bytearray)) else None
    )

    msg: Dict[str, Any] = {
        "rxTime": packet.get("rxTime"),
        "fromId": packet.get("fromId"),
        "toId": packet.get("toId"),
        "snr": packet.get("snr"),
        "rssi": packet.get("rssi"),
        "hopLimit": packet.get("hopLimit"),
        "channel": packet.get("channel"),
        "portnum": portnum,
        "text": clamp_str(text, 1000),
        "payload_b64": payload_b64,
    }

    # Avoid accidentally passing through bytes under different keys
    for key, value in list(msg.items()):
        if isinstance(value, (bytes, bytearray)):
            msg[key] = b64_encode(value)

    return msg


def node_entry(node_id: str, node: Dict[str, Any]) -> Dict[str, Any]:
    user = node.get("user") or {}
    last_heard = node.get("lastHeard")
    snr = node.get("snr")
    hops_away = _int_or_none(node.get("hopsAway"))

    age_sec = None
    if isinstance(last_heard, (int, float)) and last_heard > 0:
        age_sec = max(0, now_epoch() - int(last_heard))

    return {
        "id": node_id,
        "short": clamp_str(user.get("shortName"), 40),
        "long": clamp_str(user.get("longName"), 80),
        "snr": snr,
        "hopsAway": hops_away,
        "lastHeard": last_heard,
        "ageSec": age_sec,
        "quality": quality_bucket(snr),
    }


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None
