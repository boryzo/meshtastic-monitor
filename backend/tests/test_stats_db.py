from __future__ import annotations

import sqlite3

import pytest

import backend.stats_db as stats_db

FIXED_NOW = 200
from backend.stats_db import StatsDB


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(stats_db, "now_epoch", lambda: FIXED_NOW)


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
            "portnum": "POSITION_APP",
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
            "portnum": "TELEMETRY_APP",
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
            "portnum": "NODEINFO_APP",
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
            "portnum": "ROUTING_APP",
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
    now = FIXED_NOW
    base = now - (now % 3600)
    db.record_message(
        {
            "rxTime": base + 10,
            "fromId": "!a",
            "toId": "!b",
            "snr": None,
            "rssi": None,
            "portnum": "POSITION_APP",
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
            "portnum": "POSITION_APP",
            "text": "y",
            "payload_b64": None,
        }
    )

    summary = db.summary()
    hours = [h["hour"] for h in summary.hourly_window]
    assert base in hours


def test_stats_db_record_nodes_snapshot_and_known_entries():
    db = StatsDB(":memory:")
    now = FIXED_NOW

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"shortName": "S1", "longName": "Node One", "role": "CLIENT", "hwModel": "TBEAM"},
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
    assert entries["!n1"]["firmware"] is None
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
            "portnum": "POSITION_APP",
            "text": "hi",
            "payload_b64": None,
        }
    )
    entries2 = {e["id"]: e for e in db.known_node_entries()}
    assert entries2["!n1"]["quality"] == "ok"


def test_stats_db_get_node_stats():
    db = StatsDB(":memory:")
    now = FIXED_NOW

    db.record_nodes_snapshot(
        {
            "!n1": {
                "user": {"shortName": "S1", "longName": "Node One", "role": "CLIENT", "hwModel": "TBEAM"},
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
            "portnum": "POSITION_APP",
            "text": "hi",
            "payload_b64": None,
        }
    )

    stats = db.get_node_stats("!n1")
    assert stats is not None
    assert stats["id"] == "!n1"
    assert stats["role"] == "CLIENT"
    assert stats["hwModel"] == "TBEAM"
    assert stats["firmware"] is None
    assert stats["fromCount"] >= 1


def test_stats_db_message_history_persists():
    db = StatsDB(":memory:")
    now = FIXED_NOW

    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -100,
            "hopLimit": 3,
            "channel": 0,
            "portnum": "POSITION_APP",
            "text": "hi",
            "payload_b64": None,
        }
    )

    msgs = db.list_messages(limit=10, order="asc")
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hi"
    assert msgs[0]["channel"] == 0


def test_stats_db_list_messages_order_and_offset():
    db = StatsDB(":memory:")
    for rx in [1, 2, 3]:
        db.record_message(
            {
                "rxTime": rx,
                "fromId": f"!n{rx}",
                "toId": "!x",
                "snr": 1,
                "rssi": -90,
                "portnum": "POSITION_APP",
                "text": f"m{rx}",
                "payload_b64": None,
            }
        )

    asc = db.list_messages(limit=2, offset=1, order="asc")
    assert [m["rxTime"] for m in asc] == [2, 3]

    desc = db.list_messages(limit=2, offset=0, order="desc")
    assert [m["rxTime"] for m in desc] == [3, 2]


def test_stats_db_list_messages_app_filter():
    db = StatsDB(":memory:")
    db.record_message(
        {
            "rxTime": 1,
            "fromId": "!n1",
            "toId": "!x",
            "snr": 1,
            "rssi": -90,
            "app": "TEXT_MESSAGE_APP",
            "text": "hello",
            "payload_b64": None,
        }
    )
    db.record_message(
        {
            "rxTime": 2,
            "fromId": "!n2",
            "toId": "!x",
            "snr": 1,
            "rssi": -90,
            "app": "POSITION_APP",
            "text": "pos",
            "payload_b64": None,
        }
    )

    msgs = db.list_messages(limit=10, order="asc", app="TEXT_MESSAGE_APP")
    assert len(msgs) == 1
    assert msgs[0]["text"] == "hello"
    assert msgs[0]["app"] == "TEXT_MESSAGE_APP"


def test_stats_db_node_history_records_quality():
    db = StatsDB(":memory:")
    now = FIXED_NOW

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
    now = FIXED_NOW

    db.record_message(
        {
            "rxTime": now,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -100,
            "portnum": "POSITION_APP",
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
            "wifi": {"ip": "192.0.2.10", "rssi": -68},
        }
    )
    items = db.list_status_reports(limit=10, order="asc")
    assert items
    latest = items[-1]
    assert latest["batteryPercent"] == 100
    assert latest["batteryVoltageMv"] == 4325
    assert latest["channelUtilization"] == 13.2149991989136
    assert latest["utilizationTx"] == 0.287805557250977
    assert latest["wifiIp"] == "192.0.2.10"
    assert latest["wifiRssi"] == -68


def test_stats_db_status_history_interval_blocks_frequent_writes():
    db = StatsDB(":memory:", status_history_interval_sec=60)
    report = {"power": {"battery_percent": 50}}
    db.record_status_report(report)
    db.record_status_report(report)
    items = db.list_status_reports(limit=10, order="asc")
    assert len(items) == 1


def test_stats_db_list_status_reports_ordering():
    db = StatsDB(":memory:", status_history_interval_sec=0)
    for pct in [10, 20, 30]:
        db.record_status_report({"power": {"battery_percent": pct}})

    asc = db.list_status_reports(limit=3, order="asc")
    desc = db.list_status_reports(limit=3, order="desc")
    assert [i["batteryPercent"] for i in asc] == [10, 20, 30]
    assert [i["batteryPercent"] for i in desc] == [30, 20, 10]


def test_stats_db_record_message_defaults_time_and_tracks_text_payload():
    db = StatsDB(":memory:")
    db.record_message(
        {
            "rxTime": None,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -100,
            "portnum": "POSITION_APP",
            "text": "hi",
            "payload_b64": "AA==",
        }
    )

    msgs = db.list_messages(limit=10, order="asc")
    assert msgs[0]["rxTime"] == FIXED_NOW

    summary = db.summary()
    assert summary.counters["messages_total"] == 1
    assert summary.counters["messages_text"] == 1
    assert summary.counters["messages_payload"] == 1


def test_stats_db_record_message_stores_error_and_request_flags():
    db = StatsDB(":memory:")
    db.record_message(
        {
            "rxTime": 123,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -90,
            "portnum": "POSITION_APP",
            "text": "hello",
            "wantResponse": True,
            "error": "bad news",
        }
    )

    msg = db.list_messages(limit=10, order="asc")[0]
    assert msg["wantResponse"] is True
    assert msg["requestId"] is None
    assert msg["error"] == "bad news"


def test_stats_db_list_messages_limit_zero_returns_all():
    db = StatsDB(":memory:")
    for rx in [1, 2, 3]:
        db.record_message(
            {
                "rxTime": rx,
                "fromId": f"!n{rx}",
                "toId": "!x",
                "snr": 1,
                "rssi": -90,
                "portnum": "POSITION_APP",
                "text": f"m{rx}",
                "payload_b64": None,
            }
        )

    msgs = db.list_messages(limit=0, order="asc")
    assert [m["rxTime"] for m in msgs] == [1, 2, 3]


def test_stats_db_node_history_respects_interval(monkeypatch):
    db = StatsDB(":memory:", nodes_history_interval_sec=60)
    times = iter([1000, 1001, 1100])
    monkeypatch.setattr(stats_db, "now_epoch", lambda: next(times))

    nodes = {
        "!n1": {"user": {"shortName": "S1"}, "snr": 1, "lastHeard": 999, "hopsAway": 1}
    }
    db.record_nodes_snapshot(nodes)
    db.record_nodes_snapshot(nodes)
    db.record_nodes_snapshot(nodes)

    items = db.list_node_history(node_id="!n1", limit=10, order="asc")
    assert len(items) == 2
    assert [i["ts"] for i in items] == [1000, 1100]


def test_stats_db_list_node_history_since_order_and_limit(monkeypatch):
    db = StatsDB(":memory:", nodes_history_interval_sec=0)
    times = iter([100, 200, 300])
    monkeypatch.setattr(stats_db, "now_epoch", lambda: next(times))

    nodes = {"!n1": {"user": {"shortName": "S1"}, "snr": 1, "lastHeard": 90}}
    db.record_nodes_snapshot(nodes)
    db.record_nodes_snapshot(nodes)
    db.record_nodes_snapshot(nodes)

    items = db.list_node_history(node_id="!n1", since=200, order="asc", limit=1)
    assert [i["ts"] for i in items] == [200]

    all_items = db.list_node_history(node_id="!n1", since=200, order="asc", limit=0)
    assert [i["ts"] for i in all_items] == [200, 300]


def test_stats_db_record_status_report_ignores_non_dict():
    db = StatsDB(":memory:", status_history_interval_sec=0)
    db.record_status_report(["nope"])
    assert db.list_status_reports(limit=10, order="asc") == []


def test_stats_db_list_status_reports_since_and_limit_zero(monkeypatch):
    db = StatsDB(":memory:", status_history_interval_sec=0)
    times = iter([100, 200, 300])
    monkeypatch.setattr(stats_db, "now_epoch", lambda: next(times))
    db.record_status_report({"power": {"battery_percent": 10}})
    db.record_status_report({"power": {"battery_percent": 20}})
    db.record_status_report({"power": {"battery_percent": 30}})

    items = db.list_status_reports(since=200, order="asc", limit=0)
    assert [i["batteryPercent"] for i in items] == [20, 30]


def test_stats_db_summary_app_requests_excludes_local_node():
    db = StatsDB(":memory:")
    db.record_message(
        {
            "rxTime": 100,
            "fromId": "!me",
            "toId": "^all",
            "snr": 1,
            "rssi": -90,
            "portnum": "POSITION_APP",
            "text": "x",
            "requestId": 1,
        }
    )
    db.record_message(
        {
            "rxTime": 101,
            "fromId": "!other",
            "toId": "!me",
            "snr": 1,
            "rssi": -90,
            "portnum": "POSITION_APP",
            "text": "x",
            "requestId": 2,
        }
    )

    summary = db.summary(local_node_id="!me")
    reqs = {(r["app"], r["fromId"], r["toId"]) for r in summary.app_requests_to_me}
    assert ("POSITION_APP", "!me", "^all") not in reqs
    assert ("POSITION_APP", "!other", "!me") in reqs


def test_stats_db_record_mesh_event_unknown_key():
    db = StatsDB(":memory:")
    db.record_mesh_event("custom", "x")
    summary = db.summary()
    assert summary.counters["mesh_custom"] == 1
    assert summary.recent_events[-1]["event"] == "mesh_custom"


def test_stats_db_get_node_stats_empty_returns_none():
    db = StatsDB(":memory:")
    assert db.get_node_stats("") is None
