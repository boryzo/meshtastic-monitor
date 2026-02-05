from __future__ import annotations

import sqlite3

from backend.jsonsafe import now_epoch
from backend.stats_db import StatsDB


def test_stats_db_counts_and_top_nodes():
    db = StatsDB(":memory:")

    db.record_mesh_event("connect", "mesh-host:4403")
    db.record_mesh_event("disconnect", "bye")

    db.record_message(
        {
            "rxTime": 10_800,  # 3h
            "fromId": "!a",
            "toId": "!b",
            "snr": -1,
            "rssi": -100,
            "portnum": 3,
            "text": "hi",
            "payload_b64": None,
            "requestId": 12,
            "wantResponse": True,
        }
    )
    db.record_message(
        {
            "rxTime": 10_900,
            "fromId": "!a",
            "toId": "!c",
            "snr": 0.5,
            "rssi": -90,
            "portnum": 0x43,
            "text": "",
            "payload_b64": "AQI=",
        }
    )
    db.record_message(
        {
            "rxTime": 10_950,
            "fromId": "!b",
            "toId": "!a",
            "snr": 0,
            "rssi": -80,
            "app": "NODEINFO_APP",
            "portnum": 4,
            "text": "",
            "payload_b64": None,
        }
    )
    db.record_message(
        {
            "rxTime": 10_980,
            "fromId": "!c",
            "toId": "^all",
            "snr": -2,
            "rssi": -95,
            "portnum": 5,
            "text": "",
            "payload_b64": None,
            "wantResponse": True,
        }
    )

    db.record_send(ok=True)
    db.record_send(ok=False, error="no route")

    summary = db.summary(local_node_id="!b")
    assert summary.counters["mesh_connect"] == 1
    assert summary.counters["mesh_disconnect"] == 1
    assert summary.counters["messages_total"] == 4
    assert summary.counters["messages_text"] == 1
    assert summary.counters["messages_payload"] == 1
    assert summary.counters["send_total"] == 2
    assert summary.counters["send_ok"] == 1
    assert summary.counters["send_error"] == 1

    assert summary.top_from[0]["id"] == "!a"
    assert summary.top_from[0]["count"] == 2
    assert summary.top_to[0]["count"] == 1
    assert {n["id"] for n in summary.top_to} >= {"!b", "!c"}

    assert len(summary.recent_events) >= 3  # connect, disconnect, send_error event

    apps = {a["app"]: a for a in summary.app_counts}
    assert apps["POSITION_APP"]["total"] == 1
    assert apps["POSITION_APP"]["requests"] == 1
    assert apps["TELEMETRY_APP"]["total"] == 1
    assert apps["NODEINFO_APP"]["total"] == 1
    assert apps["ROUTING_APP"]["total"] == 1

    # Requests to me should include direct + broadcast requests
    reqs = {(r["app"], r["fromId"], r["toId"]): r for r in summary.app_requests_to_me}
    assert ("POSITION_APP", "!a", "!b") in reqs
    assert ("ROUTING_APP", "!c", "^all") in reqs


def test_stats_db_hourly_buckets_are_rounded_to_hour():
    db = StatsDB(":memory:")
    now = now_epoch()
    base = now - (now % 3600)
    db.record_message(
        {
            "rxTime": base + 10,
            "fromId": "!a",
            "toId": "!b",
            "snr": None,
            "rssi": None,
            "portnum": 1,
            "text": "x",
            "payload_b64": None,
        }
    )
    db.record_message(
        {
            "rxTime": base + 59,
            "fromId": "!a",
            "toId": "!b",
            "snr": None,
            "rssi": None,
            "portnum": 1,
            "text": "y",
            "payload_b64": None,
        }
    )

    summary = db.summary()
    hours = [h["hour"] for h in summary.hourly_window]
    assert base in hours


def test_stats_db_record_nodes_snapshot_and_known_entries():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"shortName": "S1", "longName": "Node One", "role": "CLIENT", "hwModel": "TBEAM"},
                "firmwareVersion": "2.4.0",
                "hopsAway": 3,
                "lastHeard": now - 10,
                "snr": 2,
            },
            "!n2": {
                "user": {"shortName": "S2", "longName": "Node Two", "role": "ROUTER", "hwModel": "HELTEC_V3"},
                "hopsAway": 1,
                "lastHeard": now - 20,
                "snr": None,
            },
        }
    )

    entries = {e["id"]: e for e in db.known_node_entries()}
    assert set(entries) >= {"!n1", "!n2"}
    assert entries["!n1"]["hopsAway"] == 3
    assert entries["!n1"]["quality"] == "good"
    assert entries["!n1"]["role"] == "CLIENT"
    assert entries["!n1"]["hwModel"] == "TBEAM"
    assert entries["!n1"]["firmware"] == "2.4.0"
    assert entries["!n2"]["quality"] is None
    assert entries["!n2"]["role"] == "ROUTER"
    assert entries["!n2"]["hwModel"] == "HELTEC_V3"

    # Messages should update last_snr for "from" nodes
    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": -3,
            "rssi": -100,
            "portnum": 1,
            "text": "hi",
            "payload_b64": None,
        }
    )
    entries2 = {e["id"]: e for e in db.known_node_entries()}
    assert entries2["!n1"]["quality"] == "ok"


def test_stats_db_get_node_stats():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"shortName": "S1", "longName": "Node One", "role": "CLIENT", "hwModel": "TBEAM"},
                "firmwareVersion": "2.4.0",
                "hopsAway": 2,
                "lastHeard": now - 10,
                "snr": 1,
            }
        }
    )
    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": -3,
            "rssi": -100,
            "portnum": 1,
            "text": "hi",
            "payload_b64": None,
        }
    )

    stats = db.get_node_stats("!n1")
    assert stats is not None
    assert stats["id"] == "!n1"
    assert stats["role"] == "CLIENT"
    assert stats["hwModel"] == "TBEAM"
    assert stats["firmware"] == "2.4.0"
    assert stats["fromCount"] >= 1


def test_stats_db_record_nodes_snapshot_supports_snake_case_keys():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"short_name": "S1", "long_name": "Node One", "role": "CLIENT", "hw_model": "TBEAM"},
                "device_metadata": {"firmware_version": "2.5.1"},
                "hops_away": 2,
                "last_heard": now - 10,
                "snr": 1,
            }
        }
    )

    entries = {e["id"]: e for e in db.known_node_entries()}
    assert entries["!n1"]["short"] == "S1"
    assert entries["!n1"]["long"] == "Node One"
    assert entries["!n1"]["hopsAway"] == 2
    assert entries["!n1"]["role"] == "CLIENT"
    assert entries["!n1"]["hwModel"] == "TBEAM"
    assert entries["!n1"]["firmware"] == "2.5.1"


def test_stats_db_record_nodes_snapshot_reads_firmware_from_user_and_ignores_version_dict():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {
                    "shortName": "S1",
                    "longName": "Node One",
                    "role": "CLIENT",
                    "hwModel": "TBEAM",
                    "firmwareVersion": "2.7.1",
                },
                "lastHeard": now - 10,
                "snr": 1,
            },
            "!n2": {
                "user": {"shortName": "S2", "longName": "Node Two"},
                "firmwareVersion": {"major": 2, "minor": 6, "patch": 0},
                "lastHeard": now - 10,
                "snr": 1,
            },
        }
    )

    entries = {e["id"]: e for e in db.known_node_entries()}
    assert entries["!n1"]["firmware"] == "2.7.1"
    assert entries["!n2"]["firmware"] is None


def test_stats_db_message_history_persists():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -100,
            "hopLimit": 3,
            "channel": 0,
            "portnum": 1,
            "text": "hi",
            "payload_b64": None,
        }
    )

    msgs = db.list_messages(limit=10, order="asc")
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hi"
    assert msgs[0]["channel"] == 0


def test_stats_db_node_history_records_quality():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"shortName": "S1", "longName": "Node One"},
                "snr": -3,
                "hopsAway": 2,
                "lastHeard": now - 10,
            }
        }
    )

    items = db.list_node_history(node_id="!n1", limit=10, order="desc")
    assert items
    assert items[0]["id"] == "!n1"
    assert items[0]["quality"] == "ok"


def test_stats_db_known_entries_leaves_hops_away_empty_when_missing():
    db = StatsDB(":memory:")
    now = now_epoch()

    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -100,
            "portnum": 1,
            "text": "hi",
            "payload_b64": None,
        }
    )

    entries = {e["id"]: e for e in db.known_node_entries()}
    assert entries["!n1"]["snr"] == 1
    assert entries["!n1"]["hopsAway"] is None


def test_stats_db_schema_migration_adds_columns(tmp_path):
    path = tmp_path / "stats.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE node_counts (
          node_id TEXT PRIMARY KEY,
          from_count INTEGER NOT NULL,
          to_count INTEGER NOT NULL,
          last_rx INTEGER,
          last_snr REAL,
          last_rssi REAL
        )
        """
    )
    conn.commit()
    conn.close()

    StatsDB(str(path)).close()

    conn2 = sqlite3.connect(path)
    cols = [r[1] for r in conn2.execute("PRAGMA table_info(node_counts)").fetchall()]
    conn2.close()

    for col in ["short", "long", "role", "hw_model", "firmware", "hops_away", "last_heard"]:
        assert col in cols


def test_stats_db_records_status_report():
    db = StatsDB(":memory:", status_history_interval_sec=0)
    db.record_status_report(
        {
            "airtime": {
                "channel_utilization": 13.2149991989136,
                "seconds_since_boot": 1253,
                "rx_all_log": [6581],
                "rx_log": [97426],
                "tx_log": [10361],
                "utilization_tx": 0.287805557250977,
            },
            "device": {"reboot_counter": 58},
            "memory": {"fs_free": 1019904, "fs_total": 1048576, "heap_free": 35488, "heap_total": 281216},
            "power": {"battery_percent": 100, "battery_voltage_mv": 4325, "has_battery": True, "has_usb": True, "is_charging": True},
            "radio": {"frequency": 869.525024414062, "lora_channel": 1},
            "wifi": {"ip": "192.168.8.137", "rssi": -68},
        }
    )
    items = db.list_status_reports(limit=10, order="asc")
    assert items
    latest = items[-1]
    assert latest["batteryPercent"] == 100
    assert latest["batteryVoltageMv"] == 4325
    assert latest["channelUtilization"] == 13.2149991989136
    assert latest["utilizationTx"] == 0.287805557250977
    assert latest["wifiIp"] == "192.168.8.137"
    assert latest["wifiRssi"] == -68
