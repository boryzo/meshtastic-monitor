from __future__ import annotations

import json
import time

import pytest

from backend.mesh_service import MeshService
from backend.stats_db import StatsDB
import backend.mesh_service as mesh_service


def test_mesh_service_tcp_not_configured_does_not_crash():
    svc = MeshService("", 4403, nodes_refresh_sec=1, max_messages=10)
    svc.start()
    time.sleep(0.25)
    assert svc.is_connected() is False
    assert (svc.last_error() or "").startswith("not_configured:")
    svc.stop()


def test_mesh_service_reconfigure_validates_mesh_port():
    svc = MeshService("", 4403)

    with pytest.raises(ValueError):
        svc.reconfigure(mesh_port=0)

    with pytest.raises(ValueError):
        svc.reconfigure(mesh_port=70000)


def test_status_url_ignores_tcp_port_in_host():
    svc = MeshService("192.0.2.10:4403", 4403, mesh_http_port=80)
    assert svc._status_url() == "http://192.0.2.10/json/report"


def test_status_url_uses_host_port_when_diff():
    svc = MeshService("192.0.2.10:8081", 4403, mesh_http_port=80)
    assert svc._status_url() == "http://192.0.2.10:8081/json/report"


def test_status_fetch_populates_report_and_stats(monkeypatch):
    report = {"power": {"battery_percent": 99}, "wifi": {"ip": "192.168.1.10"}}
    payload = json.dumps({"status": "ok", "data": report}).encode("utf-8")

    class _DummyResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: D401, ANN001
            return False

    def _fake_urlopen(url, timeout=None, context=None):  # noqa: ANN001
        assert url == "http://192.0.2.10/json/report"
        return _DummyResponse(payload)

    monkeypatch.setattr(mesh_service.urllib.request, "urlopen", _fake_urlopen)

    db = StatsDB(":memory:", status_history_interval_sec=0)
    svc = MeshService("192.0.2.10", 4403, mesh_http_port=80, stats_db=db)
    snap = svc.get_status_snapshot(force=True)

    assert snap["ok"] is True
    assert snap["status"] == "ok"
    assert snap["report"]["power"]["battery_percent"] == 99
    assert snap["report"]["wifi"]["ip"] == "192.168.1.10"
    assert snap["url"] == "http://192.0.2.10/json/report"

    items = db.list_status_reports(limit=1, order="asc")
    assert items
    assert items[0]["batteryPercent"] == 99
    assert items[0]["wifiIp"] == "192.168.1.10"
