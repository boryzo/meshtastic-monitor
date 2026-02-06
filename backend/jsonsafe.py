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
def _first_value(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj:
            val = obj.get(key)
            if val is not None:
                return val
    return None
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
    snr = _first_value(packet, "snr", "rxSnr")
    rssi = _first_value(packet, "rssi", "rxRssi")
    request_id = _first_value(decoded, "requestId", "request_id")
    want_response = _first_value(decoded, "wantResponse", "want_response")
    channel = _first_value(packet, "channel", "channelIndex", "channel_index")
    if channel is None:
        channel = _first_value(decoded, "channel", "channelIndex", "channel_index")
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
    last_heard = _first_value(node, "lastHeard", "last_heard")
    snr = _first_value(node, "snr", "rxSnr")
    hops_away = _int_or_none(_first_value(node, "hopsAway", "hops_away"))
    age_sec = None
    if isinstance(last_heard, (int, float)) and last_heard > 0:
        age_sec = max(0, now_epoch() - int(last_heard))
    return {
        "id": node_id,
        "short": fields["short"],
        "long": fields["long"],
        "role": fields["role"],
        "hwModel": fields["hwModel"],
        "firmware": clamp_str(firmware_from_node(node), 80),
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
    device = node.get("deviceMetrics") or node.get("device_metrics") or {}
    position = node.get("position") or {}
    hops_away = base.get("hopsAway")
    if hops_away is None:
        hops_away = 1
    return {
        **base,
        "hopsAway": hops_away,
        "nodeNum": _int_or_none(node.get("num") or node.get("nodeNum")),
        "isFavorite": _bool_or_none(node.get("isFavorite") or node.get("is_favorite")),
        "isIgnored": _bool_or_none(node.get("isIgnored") or node.get("is_ignored")),
        "isMuted": _bool_or_none(node.get("isMuted") or node.get("is_muted")),
        "isKeyManuallyVerified": _bool_or_none(
            node.get("isKeyManuallyVerified") or node.get("is_key_manually_verified")
        ),
        "channel": _int_or_none(node.get("channel")),
        "batteryLevel": _float_or_none(device.get("batteryLevel") or device.get("battery_level")),
        "voltage": _float_or_none(device.get("voltage")),
        "channelUtilization": _float_or_none(
            device.get("channelUtilization") or device.get("channel_utilization")
        ),
        "airUtilTx": _float_or_none(device.get("airUtilTx") or device.get("air_util_tx")),
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
        "short": clamp_str(user.get("shortName") or user.get("short_name"), 40),
        "long": clamp_str(user.get("longName") or user.get("long_name"), 80),
        "role": clamp_str(role_val, 40),
        "hwModel": clamp_str(
            user.get("hwModel") or user.get("hw_model") or user.get("hwmodel"), 40
        ),
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
    num = node.get("num") or node.get("nodeNum")
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
    if lat is None and pos.get("latitudeI") is not None:
        lat_i = _float_or_none(pos.get("latitudeI"))
        lat = lat_i * 1e-7 if lat_i is not None else None
    lon = _float_or_none(pos.get("longitude"))
    if lon is None and pos.get("longitudeI") is not None:
        lon_i = _float_or_none(pos.get("longitudeI"))
        lon = lon_i * 1e-7 if lon_i is not None else None
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
    try:
        val = int(portnum)
    except Exception:
        return None
    # Common apps we care about.
    if val == 3:
        return "POSITION_APP"
    if val == 4:
        return "NODEINFO_APP"
    if val == 5:
        return "ROUTING_APP"
    if val == 0x43:
        return "TELEMETRY_APP"
    return None
def _get_path_dict(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur
def firmware_from_node(node: Dict[str, Any]) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    paths = [
        "firmwareVersion",
        "firmware_version",
        "firmware",
        "user.firmwareVersion",
        "user.firmware_version",
        "user.firmware",
        "metadata.firmwareVersion",
        "metadata.firmware_version",
        "deviceMetadata.firmwareVersion",
        "deviceMetadata.firmware_version",
        "device_metadata.firmwareVersion",
        "device_metadata.firmware_version",
    ]
    for path in paths:
        val = _get_path_dict(node, path)
        normalized = _normalize_firmware_value(val)
        if normalized:
            return normalized
    return None
def _normalize_firmware_value(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        out = val.strip()
        return out or None
    return None
