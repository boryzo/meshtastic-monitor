from __future__ import annotations

import json
from pathlib import Path

import backend.jsonsafe as jsonsafe
from backend.jsonsafe import clamp_str, json_safe_packet, node_entry, quality_bucket, radio_entry

LIVE_FIXTURES = Path(__file__).parent / "fixtures" / "live"


def _load_live_nodes():
    return json.loads((LIVE_FIXTURES / "nodes.json").read_text())


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
        "rxSnr": -1,
        "rxRssi": -100,
        "decoded": {"portnum": "POSITION_APP", "text": "hi", "payload": b"\x01\x02"},
    }
    out = json_safe_packet(pkt)
    assert out["text"] == "hi"
    assert out["payload_b64"] == "AQI="
    # nothing should be bytes
    assert not any(isinstance(v, (bytes, bytearray)) for v in out.values())


def test_json_safe_packet_maps_rx_snr_and_rx_rssi():
    pkt = {
        "rxTime": 1,
        "fromId": "!a",
        "toId": "!b",
        "rxSnr": -5,
        "rxRssi": -110,
        "hopLimit": 3,
        "decoded": {
            "portnum": "POSITION_APP",
            "text": "hi",
            "payload": b"",
            "requestId": 7,
            "wantResponse": True,
        },
    }
    out = json_safe_packet(pkt)
    assert out["snr"] == -5
    assert out["rssi"] == -110
    assert out["app"] == "POSITION_APP"
    assert out["requestId"] == 7
    assert out["wantResponse"] is True


def test_json_safe_packet_maps_routing_app():
    pkt = {
        "rxTime": 2,
        "fromId": "!a",
        "toId": "!b",
        "rxSnr": -1,
        "rxRssi": -90,
        "decoded": {"portnum": "ROUTING_APP", "text": "", "payload": b""},
    }
    out = json_safe_packet(pkt)
    assert out["app"] == "ROUTING_APP"


def test_json_safe_packet_encodes_bytes_in_other_fields():
    pkt = {"rxTime": 1, "fromId": b"\x01", "toId": "!b", "decoded": {"payload": b"\x02"}}
    out = json_safe_packet(pkt)
    assert out["fromId"] == "AQ=="
    assert out["payload_b64"] == "Ag=="


def test_json_safe_packet_reads_channel():
    pkt = {"rxTime": 1, "channel": 0, "decoded": {"portnum": "POSITION_APP"}}
    out = json_safe_packet(pkt)
    assert out["channel"] == 0


def test_node_entry_live_node(monkeypatch):
    nodes = _load_live_nodes()
    node_id = "!04c54144"
    node = nodes[node_id]
    monkeypatch.setattr(jsonsafe, "now_epoch", lambda: 1770402510)
    out = node_entry(node_id, node)
    assert out["id"] == node_id
    assert out["short"] == "_RE_"
    assert out["long"] == "REST 2"
    assert out["role"] == "ROUTER"
    assert out["hwModel"] == "HELTEC_V4"
    assert out["snr"] == 1.25
    assert out["hopsAway"] == 1
    assert out["lastHeard"] == 1770402450
    assert out["ageSec"] == 60
    assert out["quality"] == "good"
    assert out["firmware"] is None


def test_node_entry_defaults_role_to_client(monkeypatch):
    nodes = _load_live_nodes()
    node_id = "!04c573f4"
    node = nodes[node_id]
    monkeypatch.setattr(jsonsafe, "now_epoch", lambda: 1770402570)
    out = node_entry(node_id, node)
    assert out["role"] == "CLIENT"
    assert out["snr"] == -2.75
    assert out["quality"] == "ok"
    assert out["ageSec"] == 3


def test_clamp_str_limits_length_and_handles_bad_str():
    assert clamp_str("a" * 10, 5) == "aaaaa…"

    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("nope")

    assert clamp_str(BadStr()) is None


def test_radio_entry_live_position_and_metrics(monkeypatch):
    nodes = _load_live_nodes()
    node_id = "!06da6f70"
    node = nodes[node_id]
    monkeypatch.setattr(jsonsafe, "now_epoch", lambda: 1770400910)
    out = radio_entry(node)
    assert out["id"] == node_id
    assert out["short"] == "Wojt"
    assert out["long"] == "Wojt ☀ ML3843"
    assert out["role"] == "CLIENT_BASE"
    assert out["hopsAway"] == 2
    assert out["batteryLevel"] == 48.0
    assert out["voltage"] == 3.71
    assert out["channelUtilization"] == 17.591667
    assert out["airUtilTx"] == 0.61169446
    pos = out["position"]
    assert pos == {
        "latitude": 54.3227904,
        "longitude": 18.6187776,
        "altitude": 52.0,
    }
