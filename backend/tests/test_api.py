from __future__ import annotations

import pytest

from backend.app import create_app
from backend.mesh_service import FakeMeshService
from backend.stats_db import StatsDB

FIXED_NOW = 200


@pytest.fixture()
def client():
    svc = FakeMeshService()
    svc.start()
    svc.seed_nodes(
        {
            "!direct": {
                "user": {"shortName": "D", "longName": "Direct", "role": "CLIENT", "hwModel": "TBEAM"},
                "snr": 1,
                "hopsAway": 1,
                "lastHeard": FIXED_NOW,
            },
            "!relay": {
                "user": {"shortName": "R", "longName": "Relayed", "role": "ROUTER", "hwModel": "HELTEC_V3"},
                "snr": None,
                "hopsAway": 2,
                "lastHeard": FIXED_NOW - 10,
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
                "portnum": "TEXT_MESSAGE_APP",
                "app": "TEXT_MESSAGE_APP",
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
    stats_db.record_nodes_snapshot(svc.get_nodes_snapshot())
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
            "portnum": "TEXT_MESSAGE_APP",
            "app": "TEXT_MESSAGE_APP",
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


def test_status_endpoint_returns_report():
    svc = FakeMeshService()
    svc.start()
    svc.seed_status_report(
        {
            "power": {"battery_percent": 99, "battery_voltage_mv": 4100},
            "wifi": {"ip": "192.168.1.10", "rssi": -65},
        }
    )
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.get("/api/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["reportOk"] is True
    assert body["report"]["power"]["battery_percent"] == 99
    assert body["report"]["wifi"]["ip"] == "192.168.1.10"


def test_status_endpoint_handles_missing_report():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.get("/api/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["reportOk"] is False
    assert body["report"] is None


def test_health_configured_false_when_mesh_host_empty():
    svc = FakeMeshService()
    svc.start()
    svc.reconfigure(mesh_host="")
    stats_db = StatsDB(":memory:")

    app = create_app(mesh_service=svc, stats_db=stats_db)
    app.testing = True
    c = app.test_client()

    h = c.get("/api/health").get_json()
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
    assert direct["role"] == "CLIENT"
    assert direct["hwModel"] == "TBEAM"
    assert direct["firmware"] is None
    assert relayed["role"] == "ROUTER"
    assert relayed["hwModel"] == "HELTEC_V3"

    # Relayed nodes omit quality
    assert relayed.get("quality") is None


def test_nodes_leaves_hops_away_empty_when_missing():
    svc = FakeMeshService()
    svc.start()
    svc.seed_nodes(
        {
            "!direct": {
                "user": {"shortName": "D", "longName": "Direct"},
                "snr": 1,
                "lastHeard": FIXED_NOW,
            },
        }
    )

    stats_db = StatsDB(":memory:")
    app = create_app(mesh_service=svc, stats_db=stats_db)
    app.testing = True
    c = app.test_client()

    body = c.get("/api/nodes?includeObserved=0").get_json()
    direct = next(n for n in body["direct"] if n["id"] == "!direct")
    assert direct["hopsAway"] is None


def test_radio_endpoint_returns_node():
    svc = FakeMeshService()
    svc.start()
    svc.seed_radio(
        {
            "user": {"id": "!me", "shortName": "ME", "longName": "My Radio", "hwModel": "TBEAM"},
            "snr": 2.5,
            "lastHeard": FIXED_NOW,
        }
    )
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    body = c.get("/api/radio").get_json()
    assert body["ok"] is True
    assert body["node"]["id"] == "!me"
    assert body["node"]["hopsAway"] == 1


def test_radio_endpoint_returns_none_when_unavailable():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    body = c.get("/api/radio").get_json()
    assert body["ok"] is True
    assert body["node"] is None


def test_device_config_endpoint_redacts_psk_by_default():
    svc = FakeMeshService()
    svc.start()
    svc.seed_device_config(
        {
            "localConfig": {"foo": 1},
            "moduleConfig": {"bar": 2},
            "channels": [{"index": 0, "name": "Primary", "psk": "abcd"}],
            "metadata": {"hwModel": "TBEAM"},
            "myInfo": {"id": "!me"},
        }
    )
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    body = c.get("/api/device/config").get_json()
    assert body["ok"] is True
    assert body["secretsIncluded"] is False
    assert body["device"]["channels"][0]["psk"] == "***redacted***"

    body2 = c.get("/api/device/config?includeSecrets=1").get_json()
    assert body2["ok"] is True
    assert body2["secretsIncluded"] is True
    assert body2["device"]["channels"][0]["psk"] == "abcd"


def test_device_config_endpoint_returns_503_when_missing():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.get("/api/device/config")
    assert res.status_code == 503
    body = res.get_json()
    assert body["ok"] is False
    assert "error" in body


def test_node_details_endpoint_returns_node_and_stats(client):
    res = client.get("/api/node/!direct")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["node"]["id"] == "!direct"
    assert body["stats"] is not None


def test_node_details_endpoint_404_when_missing(client):
    res = client.get("/api/node/!missing")
    assert res.status_code == 404


def test_nodes_include_observed_toggle(client):
    res = client.get("/api/nodes?includeObserved=0")
    assert res.status_code == 200
    body = res.get_json()
    assert body["includeObserved"] is False
    assert body["meshCount"] == 2
    assert body["observedAdded"] == 0
    assert body["total"] == 2


def test_nodes_history_endpoint(client):
    res = client.get("/api/nodes/history?limit=10")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "items" in body

    res2 = client.get("/api/node/!direct/history?limit=5")
    assert res2.status_code == 200
    body2 = res2.get_json()
    assert body2["ok"] is True
    assert body2["nodeId"] == "!direct"


def test_nodes_history_returns_503_when_stats_disabled():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=None)
    app.testing = True
    c = app.test_client()

    res = c.get("/api/nodes/history")
    assert res.status_code == 503
    assert res.get_json()["ok"] is False

    res2 = c.get("/api/node/!x/history")
    assert res2.status_code == 503
    assert res2.get_json()["ok"] is False


def test_messages_returns_list(client):
    res = client.get("/api/messages")
    assert res.status_code == 200
    body = res.get_json()
    assert isinstance(body, list)
    assert body[0]["text"] == "hi"

    res2 = client.get("/api/messages?limit=1")
    assert res2.status_code == 200
    body2 = res2.get_json()
    assert len(body2) == 1


def test_messages_pagination_and_order():
    svc = FakeMeshService()
    svc.start()
    db = StatsDB(":memory:")
    for rx in [10, 20, 30]:
        db.record_message(
            {
                "rxTime": rx,
                "fromId": f"!n{rx}",
                "toId": "!x",
                "snr": 1,
                "rssi": -100,
                "portnum": "TEXT_MESSAGE_APP",
                "app": "TEXT_MESSAGE_APP",
                "text": f"m{rx}",
                "payload_b64": None,
            }
        )
    app = create_app(mesh_service=svc, stats_db=db)
    app.testing = True
    c = app.test_client()

    asc = c.get("/api/messages?limit=2&offset=1&order=asc").get_json()
    assert [m["rxTime"] for m in asc] == [20, 30]

    desc = c.get("/api/messages?limit=2&offset=0&order=desc").get_json()
    assert [m["rxTime"] for m in desc] == [30, 20]


def test_messages_fallback_when_stats_disabled():
    svc = FakeMeshService()
    svc.start()
    svc.seed_messages(
        [
            {"rxTime": 1, "fromId": "!a", "toId": "!b", "text": "hi", "app": "TEXT_MESSAGE_APP"},
            {"rxTime": 2, "fromId": "!c", "toId": "!d", "text": "yo", "app": "TEXT_MESSAGE_APP"},
        ]
    )
    app = create_app(mesh_service=svc, stats_db=None)
    app.testing = True
    c = app.test_client()

    body = c.get("/api/messages").get_json()
    assert len(body) == 2
    assert body[0]["text"] == "hi"
    assert body[1]["text"] == "yo"


def test_messages_filters_non_text_app():
    svc = FakeMeshService()
    svc.start()
    db = StatsDB(":memory:")
    db.record_message(
        {
            "rxTime": 1,
            "fromId": "!a",
            "toId": "!b",
            "snr": -1,
            "rssi": -100,
            "app": "TEXT_MESSAGE_APP",
            "text": "hello",
            "payload_b64": None,
        }
    )
    db.record_message(
        {
            "rxTime": 2,
            "fromId": "!c",
            "toId": "!d",
            "snr": -2,
            "rssi": -101,
            "app": "POSITION_APP",
            "text": "pos",
            "payload_b64": None,
        }
    )
    app = create_app(mesh_service=svc, stats_db=db)
    app.testing = True
    c = app.test_client()

    body = c.get("/api/messages?order=asc").get_json()
    assert len(body) == 1
    assert body[0]["text"] == "hello"
    assert body[0]["app"] == "TEXT_MESSAGE_APP"


def test_messages_include_node_names_when_known():
    svc = FakeMeshService()
    svc.start()
    db = StatsDB(":memory:")
    db.record_nodes_snapshot(
        {
            "!n1": {"user": {"shortName": "S1", "longName": "Node One"}},
        }
    )
    db.record_message(
        {
            "rxTime": 1,
            "fromId": "!n1",
            "toId": "!n2",
            "snr": 1,
            "rssi": -90,
            "app": "TEXT_MESSAGE_APP",
            "text": "hi",
            "payload_b64": None,
        }
    )
    app = create_app(mesh_service=svc, stats_db=db)
    app.testing = True
    c = app.test_client()

    msg = c.get("/api/messages").get_json()[0]
    assert msg["fromShort"] == "S1"
    assert msg["fromLong"] == "Node One"
    assert msg["toShort"] is None
    assert msg["toLong"] is None

def test_stats_ok(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "counters" in body
    assert body["counters"]["messages_total"] >= 1
    assert "apps" in body
    assert "counts" in body["apps"]
    assert "requestsToMe" in body["apps"]


def test_stats_returns_503_when_disabled():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=None)
    app.testing = True
    c = app.test_client()

    res = c.get("/api/stats")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is False


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


def test_send_channel_ok():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.post("/api/send", json={"text": "hello", "channel": 1})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    assert svc.sent[-1] == ("hello", None, 1)


def test_send_channel_invalid():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.post("/api/send", json={"text": "hello", "channel": "bad"})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False

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


def test_config_get_includes_sms_defaults(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["sms"]["enabled"] is False
    assert body["sms"]["apiKeySet"] is False
    assert body["sms"]["allowFromIds"] == "ALL"
    assert body["sms"]["allowTypes"] == "ALL"
    assert body["relay"]["enabled"] is False
    assert body["relay"]["listenHost"] in {None, "0.0.0.0"}
    assert body["relay"]["listenPort"] in {None, 4403}


def test_config_updates_sms_settings():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.post(
        "/api/config",
        json={
            "smsEnabled": True,
            "smsApiUrl": "https://example.invalid/sms",
            "smsApiKey": "secret",
            "smsPhone": "600000000",
            "smsAllowFromIds": "!abcd1234",
            "smsAllowTypes": "TEXT,3",
        },
    )
    assert res.status_code == 200
    sms = svc.get_sms_config()
    assert sms["enabled"] is True
    assert sms["apiUrl"] == "https://example.invalid/sms"
    assert sms["phone"] == "600000000"
    assert sms["apiKeySet"] is True
    assert sms["allowFromIds"] == "!abcd1234"
    assert sms["allowTypes"] == "TEXT,3"

def test_config_updates_relay_settings():
    svc = FakeMeshService()
    svc.start()
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.post(
        "/api/config",
        json={
            "relayEnabled": True,
            "relayHost": "0.0.0.0",
            "relayPort": 4404,
        },
    )
    assert res.status_code == 200
    relay = svc.get_relay_stats()
    assert relay["enabled"] is True
    assert relay["listenHost"] == "0.0.0.0"
    assert relay["listenPort"] == 4404

def test_relay_status_endpoint():
    svc = FakeMeshService()
    svc.start()
    svc._relay_enabled = True
    svc._relay_host = "0.0.0.0"
    svc._relay_port = 4403
    svc._relay_clients = [{"addr": "10.0.0.2", "port": 1234, "connectedAt": 1, "lastSeen": 2}]
    app = create_app(mesh_service=svc, stats_db=StatsDB(":memory:"))
    app.testing = True
    c = app.test_client()

    res = c.get("/api/relay")
    assert res.status_code == 200
    body = res.get_json()
    assert body["enabled"] is True
    assert body["clientCount"] == 1
    assert body["clients"][0]["addr"] == "10.0.0.2"

def test_config_rejects_bad_sms_types(client):
    res = client.post("/api/config", json={"smsEnabled": "maybe"})
    assert res.status_code == 400


def test_config_rejects_bad_types_and_values(client):
    res = client.post("/api/config", json={"meshHost": 123})
    assert res.status_code == 400

    res2 = client.post("/api/config", json={"meshPort": "abc"})
    assert res2.status_code == 400
