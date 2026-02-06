from __future__ import annotations

import time

import pytest

from backend.mesh_service import MeshService


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
    svc = MeshService("192.168.8.137:4403", 4403, mesh_http_port=80)
    assert svc._status_url() == "http://192.168.8.137/json/report"


def test_status_url_uses_host_port_when_diff():
    svc = MeshService("192.168.8.137:8081", 4403, mesh_http_port=80)
    assert svc._status_url() == "http://192.168.8.137:8081/json/report"
