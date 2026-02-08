from __future__ import annotations

from backend.tcp_relay import TcpRelay


class _FakeSock:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self.fail_send = fail_send

    def sendall(self, data: bytes) -> None:
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


def test_tcp_relay_broadcasts_and_forwards():
    relay = TcpRelay("127.0.0.1", 0, "upstream", 4403)
    upstream = _FakeSock()
    relay._upstream_socket = upstream  # test helper

    c1 = _FakeSock()
    c2 = _FakeSock()
    relay._clients = {c1, c2}  # test helper
    relay._client_info = {c1: {"addr": "1.1.1.1", "port": 123, "connectedAt": 1, "lastSeen": 1}}

    relay._broadcast(b"hello")
    assert c1.sent == [b"hello"]
    assert c2.sent == [b"hello"]

    relay._send_upstream(b"ping")
    assert upstream.sent == [b"ping"]

    stats = relay.get_stats()
    assert stats["clientCount"] == 1
    assert stats["clients"][0]["addr"] == "1.1.1.1"


def test_tcp_relay_removes_broken_client():
    relay = TcpRelay("127.0.0.1", 0, "upstream", 4403)
    ok = _FakeSock()
    bad = _FakeSock(fail_send=True)
    relay._clients = {ok, bad}  # test helper
    relay._client_info = {ok: {"addr": "2.2.2.2", "port": 1, "connectedAt": 1, "lastSeen": 1}}

    relay._broadcast(b"data")
    assert ok in relay._clients
    assert bad not in relay._clients


def test_tcp_relay_update_upstream_closes_socket():
    relay = TcpRelay("127.0.0.1", 0, "upstream", 4403)
    upstream = _FakeSock()
    relay._upstream_socket = upstream  # test helper

    relay.update_upstream("next", 4403)
    assert upstream.closed is True
