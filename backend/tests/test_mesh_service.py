from __future__ import annotations

import time

import pytest

from backend.mesh_service import MeshService


def test_mesh_service_tcp_not_configured_does_not_crash():
    svc = MeshService("", 4403, transport="tcp", nodes_refresh_sec=1, max_messages=10)
    svc.start()
    time.sleep(0.25)
    assert svc.is_connected() is False
    assert (svc.last_error() or "").startswith("not_configured:")
    svc.stop()


def test_mesh_service_reconfigure_validates_transport_and_ports():
    svc = MeshService("", 4403)

    with pytest.raises(ValueError):
        svc.reconfigure(transport="bad")

    with pytest.raises(ValueError):
        svc.reconfigure(mesh_port=0)

    with pytest.raises(ValueError):
        svc.reconfigure(mesh_port=70000)

    with pytest.raises(ValueError):
        svc.reconfigure(mqtt_port=0)

    with pytest.raises(ValueError):
        svc.reconfigure(mqtt_port=70000)

    with pytest.raises(ValueError):
        svc.reconfigure(transport="mqtt", mqtt_host="")

