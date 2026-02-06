from __future__ import annotations

from backend.jsonsafe import clamp_str, json_safe_packet, node_entry, quality_bucket, radio_entry


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


def test_json_safe_packet_maps_rx_snr_and_rx_rssi():
    pkt = {
        "rxTime": 1,
        "fromId": "!a",
        "toId": "!b",
        "rxSnr": -5,
        "rxRssi": -110,
        "hopLimit": 3,
        "decoded": {"portnum": 3, "text": "hi", "payload": b"", "requestId": 7, "wantResponse": True},
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
        "snr": -1,
        "rssi": -90,
        "decoded": {"portnum": 5, "text": "", "payload": b""},
    }
    out = json_safe_packet(pkt)
    assert out["app"] == "ROUTING_APP"


def test_json_safe_packet_encodes_bytes_in_other_fields():
    pkt = {"rxTime": 1, "fromId": b"\x01", "toId": "!b", "decoded": {"payload": b"\x02"}}
    out = json_safe_packet(pkt)
    assert out["fromId"] == "AQ=="
    assert out["payload_b64"] == "Ag=="


def test_json_safe_packet_reads_channel_index_aliases():
    pkt = {"rxTime": 1, "channelIndex": 0, "decoded": {"portnum": 1}}
    out = json_safe_packet(pkt)
    assert out["channel"] == 0

    pkt2 = {"rxTime": 1, "decoded": {"channelIndex": 2, "portnum": 1}}
    out2 = json_safe_packet(pkt2)
    assert out2["channel"] == 2


def test_node_entry_shapes_quality_and_strings():
    node = {
        "user": {"shortName": "SN", "longName": "Long Name", "role": "CLIENT", "hwModel": "TBEAM"},
        "firmwareVersion": "2.4.0",
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
    assert out["role"] == "CLIENT"
    assert out["hwModel"] == "TBEAM"
    assert out["firmware"] == "2.4.0"


def test_node_entry_supports_snake_case_keys():
    node = {
        "user": {"short_name": "SN", "long_name": "Long Name", "role": "ROUTER", "hw_model": "HELTEC_V3"},
        "snr": -3,
        "hops_away": 3,
        "last_heard": 100,
    }
    out = node_entry("!abcd", node)
    assert out["short"] == "SN"
    assert out["long"] == "Long Name"
    assert out["hopsAway"] == 3
    assert out["lastHeard"] == 100
    assert out["role"] == "ROUTER"
    assert out["hwModel"] == "HELTEC_V3"


def test_node_entry_reads_firmware_from_user_and_ignores_version_dict():
    node = {
        "user": {
            "shortName": "SN",
            "longName": "Long Name",
            "role": "CLIENT",
            "hwModel": "TBEAM",
            "firmwareVersion": "2.7.1",
        },
        "snr": 1.0,
        "lastHeard": 100,
    }
    out = node_entry("!abcd", node)
    assert out["firmware"] == "2.7.1"

    node2 = {
        "user": {"shortName": "SN", "longName": "Long Name"},
        "firmwareVersion": {"major": 2, "minor": 6, "patch": 0},
        "snr": 1.0,
        "lastHeard": 100,
    }
    out2 = node_entry("!abcd", node2)
    assert out2["firmware"] is None


def test_node_entry_leaves_hops_empty_when_missing():
    node = {
        "user": {"shortName": "SN", "longName": "Long Name"},
        "snr": 1.0,
        "lastHeard": 100,
    }
    out = node_entry("!abcd", node)
    assert out["hopsAway"] is None
    assert out["role"] == "CLIENT"


def test_clamp_str_limits_length_and_handles_bad_str():
    assert clamp_str("a" * 10, 5) == "aaaaa…"

    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("nope")

    assert clamp_str(BadStr()) is None


def test_radio_entry_converts_position_integer_lat_lon():
    node = {
        "user": {"id": "!me", "shortName": "ME", "longName": "Me"},
        "snr": 1,
        "lastHeard": 100,
        "position": {"latitudeI": 123456789, "longitudeI": -123456789, "altitude": 120},
    }
    out = radio_entry(node)
    pos = out["position"]
    assert pos is not None
    assert round(pos["latitude"], 7) == 12.3456789
    assert round(pos["longitude"], 7) == -12.3456789
    assert pos["altitude"] == 120.0
