from __future__ import annotations

import time

import pytest

from backend.app import create_app
from backend.mesh_service import FakeMeshService
from backend.stats_db import StatsDB


@pytest.fixture()
def client():
    svc = FakeMeshService()
    svc.start()
    svc.seed_nodes(
        {
            "!direct": {
                "user": {"shortName": "D", "longName": "Direct"},
                "snr": 1,
                "hopsAway": 1,
                "lastHeard": int(time.time()),
            },
            "!relay": {
                "user": {"shortName": "R", "longName": "Relayed"},
                "snr": None,
                "hopsAway": 2,
                "lastHeard": int(time.time()) - 10,
            },
        }
    )
    svc.seed_messages(
        [
            {
                "rxTime": 1,
                "fromId": "!a",
                "toId": "!b",
                "snr": -1,
                "rssi": -100,
                "hopLimit": 3,
                "channel": 0,
                "portnum": 3,
                "text": "hi",
                "payload_b64": None,
            }
        ]
    )
    svc.seed_channels(
        [
            {"index": 0, "name": "Primary", "role": "PRIMARY", "enabled": True},
            {"index": 1, "name": "Ops", "role": "SECONDARY", "enabled": True},
        ]
    )

    stats_db = StatsDB(":memory:")
    # Seed one message into stats so /api/stats has something interesting.
    stats_db.record_message(
        {
            "rxTime": 1,
            "fromId": "!a",
            "toId": "!b",
            "snr": -1,
            "rssi": -100,
            "hopLimit": 3,
            "channel": 0,
            "portnum": 3,
            "text": "hi",
            "payload_b64": None,
        }
    )

    app = create_app(mesh_service=svc, stats_db=stats_db)
    app.testing = True
    return app.test_client()


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "meshHost" in body
    assert "connected" in body
    assert "lastError" in body


def test_health_configured_false_when_mesh_host_empty():
    svc = FakeMeshService()
    svc.start()
    svc.reconfigure(mesh_host="")
    stats_db = StatsDB(":memory:")

    app = create_app(mesh_service=svc, stats_db=stats_db)
    app.testing = True
    c = app.test_client()

    h = c.get("/api/health").get_json()
    assert h["transport"] == "tcp"
    assert h["configured"] is False
    assert h["meshHost"] is None


def test_frontend_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in (res.headers.get("Content-Type") or "")

    res2 = client.get("/static/app.js")
    assert res2.status_code == 200


def test_channels(client):
    res = client.get("/api/channels")
    assert res.status_code == 200
    body = res.get_json()
    assert body["total"] == 2
    assert {c["name"] for c in body["channels"]} == {"Primary", "Ops"}


def test_nodes_split_direct_and_relayed(client):
    res = client.get("/api/nodes")
    assert res.status_code == 200
    body = res.get_json()
    assert body["meshCount"] == 2
    assert body["includeObserved"] is True
    assert body["total"] >= 2
    assert {n["id"] for n in body["direct"]} >= {"!direct"}
    assert {n["id"] for n in body["relayed"]} >= {"!relay"}

    # Hops from mesh snapshot should be present
    direct = next(n for n in body["direct"] if n["id"] == "!direct")
    relayed = next(n for n in body["relayed"] if n["id"] == "!relay")
    assert direct["hopsAway"] == 1
    assert relayed["hopsAway"] == 2

    # Relayed nodes omit quality
    assert relayed.get("quality") is None


def test_nodes_include_observed_toggle(client):
    res = client.get("/api/nodes?includeObserved=0")
    assert res.status_code == 200
    body = res.get_json()
    assert body["includeObserved"] is False
    assert body["meshCount"] == 2
    assert body["observedAdded"] == 0
    assert body["total"] == 2


def test_messages_returns_list(client):
    res = client.get("/api/messages")
    assert res.status_code == 200
    body = res.get_json()
    assert isinstance(body, list)
    assert body[0]["text"] == "hi"

def test_stats_ok(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "counters" in body
    assert body["counters"]["messages_total"] >= 1


def test_send_requires_text(client):
    res = client.post("/api/send", json={})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_send_ok(client):
    res = client.post("/api/send", json={"text": "hello", "to": "!abcd"})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    # send stats should reflect the call
    res2 = client.get("/api/stats")
    body = res2.get_json()
    assert body["counters"]["send_total"] >= 1


def test_config_requires_at_least_one_field(client):
    res = client.post("/api/config", json={})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_config_mesh_port_only_ok(client):
    res = client.post("/api/config", json={"meshPort": 4404})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_config_ok(client):
    res = client.post("/api/config", json={"meshHost": "mesh-host", "meshPort": 4403})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_config_mqtt_ok_and_visible_in_health(client):
    res = client.post(
        "/api/config",
        json={
            "transport": "mqtt",
            "mqttHost": "broker.example",
            "mqttPort": 1884,
            "mqttUsername": "u",
            "mqttPassword": "p",
            "mqttTls": True,
            "mqttRootTopic": "msh/#",
        },
    )
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    h = client.get("/api/health").get_json()
    assert h["transport"] == "mqtt"
    assert h["mqttHost"] == "broker.example"
    assert h["mqttPort"] == 1884
    assert h["mqttUsername"] == "u"
    assert h["mqttTls"] is True
    assert h["mqttPasswordSet"] is True


def test_config_rejects_bad_types_and_values(client):
    res = client.post("/api/config", json={"transport": 123})
    assert res.status_code == 400

    res2 = client.post("/api/config", json={"transport": "nope"})
    assert res2.status_code == 400

    res3 = client.post("/api/config", json={"mqttTls": 123})
    assert res3.status_code == 400

    res4 = client.post("/api/config", json={"mqttPort": "abc"})
    assert res4.status_code == 400
