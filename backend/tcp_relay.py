from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)


class TcpRelay:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        *,
        connect_timeout: float = 2.0,
        read_buf: int = 4096,
        accept_backlog: int = 16,
    ) -> None:
        listen_host = (listen_host or "0.0.0.0").strip() or "0.0.0.0"
        listen_port = int(listen_port)
        if listen_port < 0 or listen_port > 65535:
            raise ValueError("listen_port must be 0..65535")

        upstream_host = (upstream_host or "").strip()
        if not upstream_host:
            raise ValueError("upstream_host must be set")
        upstream_port = int(upstream_port)
        if upstream_port <= 0 or upstream_port > 65535:
            raise ValueError("upstream_port must be 1..65535")

        self._listen_host = listen_host
        self._listen_port = listen_port
        self._listen_actual_port: Optional[int] = None
        self._accept_backlog = max(1, int(accept_backlog))
        self._read_buf = max(256, int(read_buf))
        self._connect_timeout = max(0.5, float(connect_timeout))

        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._upstream_target_lock = threading.Lock()

        self._server_socket: Optional[socket.socket] = None
        self._upstream_socket: Optional[socket.socket] = None
        self._upstream_lock = threading.Lock()

        self._clients: Set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._client_info: dict[socket.socket, dict] = {}
        self._started_at: Optional[int] = None

        self._stop = threading.Event()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._upstream_thread = threading.Thread(target=self._upstream_loop, daemon=True)

    @property
    def listen_port(self) -> int:
        return self._listen_actual_port if self._listen_actual_port is not None else self._listen_port

    @property
    def listen_host(self) -> str:
        return self._listen_host

    def start(self) -> None:
        if self._server_socket is not None:
            return

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._listen_host, self._listen_port))
        server.listen(self._accept_backlog)
        server.settimeout(1.0)
        self._server_socket = server
        self._listen_actual_port = server.getsockname()[1]
        self._started_at = int(time.time())

        logger.info(
            "TCP relay listening on %s:%s (upstream %s:%s)",
            self._listen_host,
            self._listen_actual_port,
            self._upstream_host,
            self._upstream_port,
        )

        self._accept_thread.start()
        self._upstream_thread.start()

    def stop(self) -> None:
        self._stop.set()
        server = self._server_socket
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        self._server_socket = None
        self._close_upstream()
        self._close_all_clients()

    def update_upstream(self, host: str, port: int) -> None:
        host = (host or "").strip()
        if not host:
            raise ValueError("upstream host is required")
        port = int(port)
        if port <= 0 or port > 65535:
            raise ValueError("upstream port must be 1..65535")
        with self._upstream_target_lock:
            changed = host != self._upstream_host or port != self._upstream_port
            self._upstream_host = host
            self._upstream_port = port
        if changed:
            logger.info("TCP relay upstream updated to %s:%s", host, port)
            self._close_upstream()

    def get_stats(self) -> dict:
        with self._clients_lock:
            clients = list(self._client_info.values())
        with self._upstream_lock:
            upstream_connected = self._upstream_socket is not None
        host, port = self._get_upstream_target()
        return {
            "enabled": True,
            "listenHost": self._listen_host,
            "listenPort": self.listen_port,
            "upstreamHost": host,
            "upstreamPort": port,
            "upstreamConnected": upstream_connected,
            "clientCount": len(clients),
            "clients": sorted(clients, key=lambda c: c.get("connectedAt") or 0),
            "startedAt": self._started_at,
        }

    # ---- internal loops
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            server = self._server_socket
            if server is None:
                break
            try:
                client, _addr = server.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            client.settimeout(1.0)
            now = int(time.time())
            addr = _format_addr(_addr)
            with self._clients_lock:
                self._clients.add(client)
                self._client_info[client] = {
                    "addr": addr[0],
                    "port": addr[1],
                    "connectedAt": now,
                    "lastSeen": now,
                }
                count = len(self._clients)
            logger.info("TCP relay client connected %s:%s (clients=%s)", addr[0], addr[1], count)
            t = threading.Thread(target=self._client_loop, args=(client,), daemon=True)
            t.start()

    def _upstream_loop(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            if self._upstream_socket is None:
                host, port = self._get_upstream_target()
                try:
                    sock = socket.create_connection((host, port), timeout=self._connect_timeout)
                    sock.settimeout(1.0)
                except Exception as e:
                    logger.warning("TCP relay upstream connect failed (%s:%s): %s", host, port, e)
                    time.sleep(backoff)
                    backoff = min(5.0, backoff * 1.7)
                    continue
                with self._upstream_lock:
                    self._upstream_socket = sock
                logger.info("TCP relay upstream connected (%s:%s)", host, port)
                backoff = 0.5

            sock = self._upstream_socket
            if sock is None:
                continue
            try:
                data = sock.recv(self._read_buf)
                if not data:
                    raise ConnectionError("upstream closed")
                self._broadcast(data)
            except socket.timeout:
                continue
            except Exception:
                self._close_upstream()

    def _client_loop(self, client: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data = client.recv(self._read_buf)
                if not data:
                    break
                self._touch_client(client)
                self._send_upstream(data)
            except socket.timeout:
                continue
            except Exception:
                break
        self._remove_client(client)

    # ---- IO helpers
    def _get_upstream_target(self) -> Tuple[str, int]:
        with self._upstream_target_lock:
            return self._upstream_host, self._upstream_port

    def _send_upstream(self, data: bytes) -> None:
        with self._upstream_lock:
            sock = self._upstream_socket
            if sock is None:
                return
            try:
                sock.sendall(data)
            except Exception:
                self._close_upstream()

    def _broadcast(self, data: bytes) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(data)
            except Exception:
                self._remove_client(client)

    def _remove_client(self, client: socket.socket) -> None:
        info = None
        count = 0
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)
            info = self._client_info.pop(client, None)
            count = len(self._clients)
        try:
            client.close()
        except Exception:
            pass
        if info:
            logger.info(
                "TCP relay client disconnected %s:%s (clients=%s)",
                info.get("addr"),
                info.get("port"),
                count,
            )

    def _close_all_clients(self) -> None:
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
            self._client_info.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass

    def _close_upstream(self) -> None:
        with self._upstream_lock:
            sock = self._upstream_socket
            self._upstream_socket = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _touch_client(self, client: socket.socket) -> None:
        now = int(time.time())
        with self._clients_lock:
            info = self._client_info.get(client)
            if info is not None:
                info["lastSeen"] = now


def _format_addr(addr: tuple) -> tuple[str, int]:
    try:
        host, port = addr[0], int(addr[1])
        return str(host), port
    except Exception:
        return "unknown", 0
