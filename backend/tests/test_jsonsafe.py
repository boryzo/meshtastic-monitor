from __future__ import annotations

from backend.jsonsafe import clamp_str, json_safe_packet, node_entry, quality_bucket


def test_quality_bucket_ranges():
    assert quality_bucket(0) == "good"
    assert quality_bucket(0.1) == "good"
    assert quality_bucket(-0.0001) == "ok"
    assert quality_bucket(-6.9) == "ok"
    assert quality_bucket(-7) == "ok"
    assert quality_bucket(-7.0001) == "weak"
    assert quality_bucket(-11.9) == "weak"
    assert quality_bucket(-12) == "weak"
    assert quality_bucket(-12.0001) == "bad"


def test_json_safe_packet_encodes_bytes_payload():
    pkt = {
        "rxTime": 1,
        "fromId": "!a",
        "toId": "!b",
        "snr": -1,
        "rssi": -100,
        "decoded": {"portnum": 3, "text": "hi", "payload": b"\x01\x02"},
    }
    out = json_safe_packet(pkt)
    assert out["text"] == "hi"
    assert out["payload_b64"] == "AQI="
    # nothing should be bytes
    assert not any(isinstance(v, (bytes, bytearray)) for v in out.values())

def test_json_safe_packet_encodes_bytes_in_other_fields():
    pkt = {"rxTime": 1, "fromId": b"\x01", "toId": "!b", "decoded": {"payload": b"\x02"}}
    out = json_safe_packet(pkt)
    assert out["fromId"] == "AQ=="
    assert out["payload_b64"] == "Ag=="


def test_node_entry_shapes_quality_and_strings():
    node = {
        "user": {"shortName": "SN", "longName": "Long Name"},
        "snr": -3,
        "hopsAway": "2",
        "lastHeard": 100,
    }
    out = node_entry("!abcd", node)
    assert out["id"] == "!abcd"
    assert out["quality"] == "ok"
    assert out["hopsAway"] == 2
    assert out["short"] == "SN"
    assert out["long"] == "Long Name"


def test_clamp_str_limits_length_and_handles_bad_str():
    assert clamp_str("a" * 10, 5) == "aaaaa…"

    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("nope")

    assert clamp_str(BadStr()) is None
