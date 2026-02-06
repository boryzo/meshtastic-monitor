from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import backend.stats_db as stats_db
from backend.jsonsafe import json_safe_packet
from backend.mesh_service import _channel_entry
from backend.stats_db import StatsDB

FIXED_NOW = 200
FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


def load_expected(name: str):
    return json.loads((EXPECTED / name).read_text())


def load_py(name: str):
    path = FIXTURES / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.DATA


def test_json_safe_packet_with_payload_snapshot():
    packet = load_py("packet_with_payload.py")
    out = json_safe_packet(packet)
    assert out == load_expected("json_safe_packet_with_payload.json")


def test_json_safe_packet_channel_index_snapshot():
    packet = load_json("packet_channel_index.json")
    out = json_safe_packet(packet)
    assert out == load_expected("json_safe_packet_channel_index.json")


def test_json_safe_packet_fromid_bytes_snapshot():
    packet = load_py("packet_fromid_bytes.py")
    out = json_safe_packet(packet)
    assert out == load_expected("json_safe_packet_fromid_bytes.json")




def test_channel_entry_primary_snapshot():
    channel = load_json("channel_primary.json")
    out = _channel_entry(channel["index"], channel)
    assert out == load_expected("channel_entry_primary.json")


def test_status_report_full_snapshot(monkeypatch):
    monkeypatch.setattr(stats_db, "now_epoch", lambda: FIXED_NOW)
    db = StatsDB(":memory:", status_history_interval_sec=0)
    report = load_json("status_report_full.json")
    db.record_status_report(report)
    items = db.list_status_reports(limit=1, order="asc")
    assert items == [load_expected("status_report_full.json")]


def test_status_report_partial_snapshot(monkeypatch):
    monkeypatch.setattr(stats_db, "now_epoch", lambda: FIXED_NOW)
    db = StatsDB(":memory:", status_history_interval_sec=0)
    report = load_json("status_report_partial.json")
    db.record_status_report(report)
    items = db.list_status_reports(limit=1, order="asc")
    assert items == [load_expected("status_report_partial.json")]


def test_status_report_empty_logs_snapshot(monkeypatch):
    monkeypatch.setattr(stats_db, "now_epoch", lambda: FIXED_NOW)
    db = StatsDB(":memory:", status_history_interval_sec=0)
    report = load_json("status_report_empty_logs.json")
    db.record_status_report(report)
    items = db.list_status_reports(limit=1, order="asc")
    assert items == [load_expected("status_report_empty_logs.json")]
