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
    snr = packet.get("rxSnr")
    rssi = packet.get("rxRssi")
    request_id = decoded.get("requestId")
    want_response = decoded.get("wantResponse")
    channel = packet.get("channel")
    app = portnum_name(portnum)
    msg: Dict[str, Any] = {
        "rxTime": packet.get("rxTime"),
        "fromId": packet.get("fromId"),
        "toId": packet.get("toId"),
        "snr": snr,
        "rssi": rssi,
        "hopLimit": packet.get("hopLimit"),
        "channel": channel,
        "portnum": portnum,
        "app": app,
        "requestId": _int_or_none(request_id) if request_id is not None else None,
        "wantResponse": _bool_or_none(want_response),
        "text": clamp_str(text, 1000),
        "payload_b64": payload_b64,
    }
    # Avoid accidentally passing through bytes under different keys
    for key, value in list(msg.items()):
        if isinstance(value, (bytes, bytearray)):
            msg[key] = b64_encode(value)
    return msg
def node_entry(node_id: str, node: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(node, dict):
        node = {}
    fields = node_user_fields(node)
    last_heard = node.get("lastHeard")
    snr = node.get("snr")
    hops_away = _int_or_none(node.get("hopsAway"))
    age_sec = None
    if isinstance(last_heard, (int, float)) and last_heard > 0:
        age_sec = max(0, now_epoch() - int(last_heard))
    return {
        "id": node_id,
        "short": fields["short"],
        "long": fields["long"],
        "role": fields["role"],
        "hwModel": fields["hwModel"],
        "firmware": None,
        "snr": snr,
        "hopsAway": hops_away,
        "lastHeard": last_heard,
        "ageSec": age_sec,
        "quality": quality_bucket(snr),
    }
def radio_entry(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a JSON-safe snapshot for the local (my) radio.
    """
    node_id = _node_id_from_node(node)
    base = node_entry(node_id or "", node)
    device = node.get("deviceMetrics") or {}
    position = node.get("position") or {}
    hops_away = base.get("hopsAway")
    if hops_away is None:
        hops_away = 1
    return {
        **base,
        "hopsAway": hops_away,
        "nodeNum": _int_or_none(node.get("num")),
        "isFavorite": _bool_or_none(node.get("isFavorite")),
        "isIgnored": _bool_or_none(node.get("isIgnored")),
        "isMuted": _bool_or_none(node.get("isMuted")),
        "isKeyManuallyVerified": _bool_or_none(
            node.get("isKeyManuallyVerified")
        ),
        "channel": _int_or_none(node.get("channel")),
        "batteryLevel": _float_or_none(device.get("batteryLevel")),
        "voltage": _float_or_none(device.get("voltage")),
        "channelUtilization": _float_or_none(device.get("channelUtilization")),
        "airUtilTx": _float_or_none(device.get("airUtilTx")),
        "position": _position_entry(position),
    }
def node_user_fields(node: Any) -> Dict[str, Optional[str]]:
    if not isinstance(node, dict):
        return {"short": None, "long": None, "role": "CLIENT", "hwModel": None}
    user = node.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    role_val = role_str(user.get("role"))
    if not role_val:
        role_val = "CLIENT"
    return {
        "short": clamp_str(user.get("shortName"), 40),
        "long": clamp_str(user.get("longName"), 80),
        "role": clamp_str(role_val, 40),
        "hwModel": clamp_str(user.get("hwModel"), 40),
    }
def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None
def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
def _bool_or_none(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return None
def _node_id_from_node(node: Dict[str, Any]) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    user = node.get("user")
    if isinstance(user, dict):
        val = user.get("id")
        if isinstance(val, str) and val:
            return val
    val = node.get("id")
    if isinstance(val, str) and val:
        return val
    num = node.get("num")
    if isinstance(num, (int, float)) and num >= 0:
        try:
            return f"!{int(num):08x}"
        except Exception:
            return None
    return None
def _position_entry(pos: Any) -> Optional[Dict[str, float]]:
    if not isinstance(pos, dict):
        return None
    lat = _float_or_none(pos.get("latitude"))
    lon = _float_or_none(pos.get("longitude"))
    alt = _float_or_none(pos.get("altitude"))
    if lat is None and lon is None and alt is None:
        return None
    out: Dict[str, float] = {}
    if lat is not None:
        out["latitude"] = lat
    if lon is not None:
        out["longitude"] = lon
    if alt is not None:
        out["altitude"] = alt
    return out
def role_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    try:
        return str(value)
    except Exception:
        return None
def portnum_name(portnum: Any) -> Optional[str]:
    if portnum is None:
        return None
    if isinstance(portnum, str):
        return portnum
    return None
