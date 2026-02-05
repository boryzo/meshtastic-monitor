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
        }
    )
    db.record_message(
        {
            "rxTime": 10_900,
            "fromId": "!a",
            "toId": "!c",
            "snr": 0.5,
            "rssi": -90,
            "portnum": 3,
            "text": "",
            "payload_b64": "AQI=",
        }
    )

    db.record_send(ok=True)
    db.record_send(ok=False, error="no route")

    summary = db.summary()
    assert summary.counters["mesh_connect"] == 1
    assert summary.counters["mesh_disconnect"] == 1
    assert summary.counters["messages_total"] == 2
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
                "user": {"shortName": "S1", "longName": "Node One"},
                "hopsAway": 3,
                "lastHeard": now - 10,
                "snr": 2,
            },
            "!n2": {
                "user": {"shortName": "S2", "longName": "Node Two"},
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
    assert entries["!n2"]["quality"] is None

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

    for col in ["short", "long", "hops_away", "last_heard"]:
        assert col in cols
