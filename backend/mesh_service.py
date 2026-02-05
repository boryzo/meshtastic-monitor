from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.jsonsafe import json_safe_packet, now_epoch

logger = logging.getLogger(__name__)


@dataclass
class MeshConfig:
    transport: str
    mesh_host: str
    mesh_port: int = 4403
    mqtt_host: str = "mqtt.meshtastic.org"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_tls: bool = False
    mqtt_root_topic: Optional[str] = None


def _normalize_transport(value: Any) -> str:
    t = str(value or "tcp").strip().lower()
    if t in {"tcp", "meshtastic-tcp"}:
        return "tcp"
    if t in {"mqtt", "meshtastic-mqtt"}:
        return "mqtt"
    raise ValueError("transport must be 'tcp' or 'mqtt'")


class MeshService:
    """
    Maintains a Meshtastic connection (TCP or MQTT) in a background thread.
    Stores:
      - nodeDB snapshot (raw dict from meshtastic)
      - JSON-safe message ring buffer (thin model)
    """

    def __init__(
        self,
        mesh_host: str,
        mesh_port: int = 4403,
        *,
        transport: str = "tcp",
        mqtt_host: str = "mqtt.meshtastic.org",
        mqtt_port: int = 1883,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        mqtt_tls: bool = False,
        mqtt_root_topic: Optional[str] = None,
        nodes_refresh_sec: int = 5,
        max_messages: int = 200,
        stats_db: Optional[Any] = None,
    ) -> None:
        mesh_host = str(mesh_host or "").strip()
        mesh_port_int = int(mesh_port)
        if mesh_port_int <= 0 or mesh_port_int > 65535:
            raise ValueError("meshPort must be 1..65535")

        mqtt_host = str(mqtt_host or "").strip() or "mqtt.meshtastic.org"
        mqtt_port_int = int(mqtt_port)
        if mqtt_port_int <= 0 or mqtt_port_int > 65535:
            raise ValueError("mqttPort must be 1..65535")

        self._cfg = MeshConfig(
            transport=_normalize_transport(transport),
            mesh_host=mesh_host,
            mesh_port=mesh_port_int,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port_int,
            mqtt_username=(str(mqtt_username).strip() if mqtt_username else None),
            mqtt_password=(str(mqtt_password) if mqtt_password else None),
            mqtt_tls=bool(mqtt_tls),
            mqtt_root_topic=(str(mqtt_root_topic).strip() if mqtt_root_topic else None),
        )
        self._nodes_refresh_sec = max(1, int(nodes_refresh_sec))
        self._max_messages = max(1, int(max_messages))
        self._stats_db = stats_db

        self._iface: Any = None
        self._iface_lock = threading.Lock()

        self._nodes_cache: Dict[str, Dict[str, Any]] = {}
        self._nodes_lock = threading.Lock()

        self._messages_cache: List[Dict[str, Any]] = []
        self._messages_lock = threading.Lock()

        self._channels_cache: List[Dict[str, Any]] = []
        self._channels_lock = threading.Lock()

        self._connected = False
        self._connected_lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_error_lock = threading.Lock()

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)

        self._pubsub_subscribed = False
        self._pubsub_lock = threading.Lock()

    # ---- lifecycle
    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._disconnect()

    # ---- config
    def get_config(self) -> MeshConfig:
        cfg = self._cfg
        return MeshConfig(
            transport=cfg.transport,
            mesh_host=cfg.mesh_host,
            mesh_port=cfg.mesh_port,
            mqtt_host=cfg.mqtt_host,
            mqtt_port=cfg.mqtt_port,
            mqtt_username=cfg.mqtt_username,
            mqtt_password=cfg.mqtt_password,
            mqtt_tls=cfg.mqtt_tls,
            mqtt_root_topic=cfg.mqtt_root_topic,
        )

    def reconfigure(
        self,
        *,
        transport: Optional[str] = None,
        mesh_host: Optional[str] = None,
        mesh_port: Optional[int] = None,
        mqtt_host: Optional[str] = None,
        mqtt_port: Optional[int] = None,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        mqtt_tls: Optional[bool] = None,
        mqtt_root_topic: Optional[str] = None,
    ) -> None:
        cfg = self._cfg

        next_transport = cfg.transport if transport is None else _normalize_transport(transport)

        next_mesh_host = cfg.mesh_host
        if mesh_host is not None:
            mesh_host_clean = str(mesh_host).strip()
            if not mesh_host_clean:
                raise ValueError("meshHost must be non-empty")
            next_mesh_host = mesh_host_clean

        next_mesh_port = cfg.mesh_port
        if mesh_port is not None:
            port_int = int(mesh_port)
            if port_int <= 0 or port_int > 65535:
                raise ValueError("meshPort must be 1..65535")
            next_mesh_port = port_int

        next_mqtt_host = cfg.mqtt_host
        if mqtt_host is not None:
            mqtt_host_clean = str(mqtt_host).strip()
            if not mqtt_host_clean:
                raise ValueError("mqttHost must be non-empty")
            next_mqtt_host = mqtt_host_clean

        next_mqtt_port = cfg.mqtt_port
        if mqtt_port is not None:
            port_int = int(mqtt_port)
            if port_int <= 0 or port_int > 65535:
                raise ValueError("mqttPort must be 1..65535")
            next_mqtt_port = port_int

        next_mqtt_username = cfg.mqtt_username
        if mqtt_username is not None:
            u = str(mqtt_username).strip()
            next_mqtt_username = u if u else None

        next_mqtt_password = cfg.mqtt_password
        if mqtt_password is not None:
            p = str(mqtt_password)
            next_mqtt_password = p if p else None

        next_mqtt_tls = cfg.mqtt_tls if mqtt_tls is None else bool(mqtt_tls)

        next_mqtt_root_topic = cfg.mqtt_root_topic
        if mqtt_root_topic is not None:
            t = str(mqtt_root_topic).strip()
            next_mqtt_root_topic = t if t else None

        if next_transport == "mqtt" and not next_mqtt_host:
            raise ValueError("mqttHost must be set when transport='mqtt'")

        changed = (
            (next_transport != cfg.transport)
            or (next_mesh_host != cfg.mesh_host)
            or (next_mesh_port != cfg.mesh_port)
            or (next_mqtt_host != cfg.mqtt_host)
            or (next_mqtt_port != cfg.mqtt_port)
            or (next_mqtt_username != cfg.mqtt_username)
            or (next_mqtt_password != cfg.mqtt_password)
            or (next_mqtt_tls != cfg.mqtt_tls)
            or (next_mqtt_root_topic != cfg.mqtt_root_topic)
        )

        self._cfg = MeshConfig(
            transport=next_transport,
            mesh_host=next_mesh_host,
            mesh_port=next_mesh_port,
            mqtt_host=next_mqtt_host,
            mqtt_port=next_mqtt_port,
            mqtt_username=next_mqtt_username,
            mqtt_password=next_mqtt_password,
            mqtt_tls=next_mqtt_tls,
            mqtt_root_topic=next_mqtt_root_topic,
        )

        if changed:
            logger.info(
                "Reconfiguring mesh transport=%s tcp=%s:%s mqtt=%s:%s",
                next_transport,
                next_mesh_host,
                next_mesh_port,
                next_mqtt_host,
                next_mqtt_port,
            )
            self._record_mesh_event("disconnect", "reconfigure")
            self._disconnect()

    # ---- health/data accessors
    def is_connected(self) -> bool:
        with self._connected_lock:
            return self._connected

    def last_error(self) -> Optional[str]:
        with self._last_error_lock:
            return self._last_error

    def get_nodes_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._nodes_lock:
            return dict(self._nodes_cache)

    def get_messages(self) -> List[Dict[str, Any]]:
        with self._messages_lock:
            return list(self._messages_cache)

    def get_channels_snapshot(self) -> List[Dict[str, Any]]:
        with self._channels_lock:
            return list(self._channels_cache)

    def get_radio_snapshot(self) -> Optional[Dict[str, Any]]:
        iface = self._get_iface()
        if iface is None:
            return None
        try:
            getter = getattr(iface, "getMyNodeInfo", None)
            if callable(getter):
                return getter()
        except Exception:
            return None
        return None

    def get_device_config(self, *, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        iface = self._get_iface()
        if iface is None:
            return None

        try:
            from meshtastic.util import message_to_json  # type: ignore
        except Exception:
            return None

        local = getattr(iface, "localNode", None)
        if local is None:
            return None

        def pb_to_dict(pb: Any) -> Optional[Dict[str, Any]]:
            if pb is None:
                return None
            try:
                return json.loads(message_to_json(pb))
            except Exception:
                return None

        local_config = pb_to_dict(getattr(local, "localConfig", None))
        module_config = pb_to_dict(getattr(local, "moduleConfig", None))

        channels: List[Dict[str, Any]] = []
        chan_list = getattr(local, "channels", None)
        if isinstance(chan_list, (list, tuple)):
            for ch in chan_list:
                d = pb_to_dict(ch)
                if isinstance(d, dict):
                    channels.append(d)

        if not include_secrets:
            channels = [_redact_secrets(c) for c in channels]

        metadata = pb_to_dict(getattr(iface, "metadata", None))
        my_info = pb_to_dict(getattr(iface, "myInfo", None))

        return {
            "localConfig": local_config,
            "moduleConfig": module_config,
            "channels": channels,
            "metadata": metadata,
            "myInfo": my_info,
        }

    # ---- actions
    def send_text(self, text: str, to: Optional[str] = None, channel: Optional[int] = None) -> None:
        text = str(text or "").strip()
        if not text:
            raise ValueError("text is required")

        iface = self._get_iface()
        if iface is None:
            raise RuntimeError("not connected to mesh")

        try:
            if channel is None:
                if to:
                    iface.sendText(text, destinationId=to)
                else:
                    iface.sendText(text)
                return

            ch_idx = int(channel)
            if to:
                iface.sendText(text, destinationId=to, channelIndex=ch_idx)
            else:
                iface.sendText(text, channelIndex=ch_idx)
            return
        except TypeError:
            # Some versions use a different param name
            if channel is None:
                if to:
                    iface.sendText(text, destination=to)
                else:
                    iface.sendText(text)
                return
            ch_idx = int(channel)
            if to:
                iface.sendText(text, destination=to, channel=ch_idx)
            else:
                iface.sendText(text, channel=ch_idx)
            return
        except Exception:
            # Let other errors bubble up
            raise

    # ---- internal
    def _set_connected(self, connected: bool) -> None:
        with self._connected_lock:
            self._connected = connected

    def _set_error(self, message: Optional[str]) -> None:
        with self._last_error_lock:
            self._last_error = message

    def _get_iface(self) -> Any:
        with self._iface_lock:
            return self._iface

    def _target_str(self) -> str:
        cfg = self._cfg
        if cfg.transport == "mqtt":
            return f"mqtt {cfg.mqtt_host}:{cfg.mqtt_port}"
        return f"tcp {cfg.mesh_host}:{cfg.mesh_port}"

    def _create_mqtt_iface(self) -> Any:
        cfg = self._cfg

        try:
            from meshtastic.mqtt_interface import MQTTInterface  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Meshtastic MQTT support not available in this environment"
            ) from e

        attempts: List[Dict[str, Any]] = [
            {
                "hostname": cfg.mqtt_host,
                "portNumber": cfg.mqtt_port,
                "username": cfg.mqtt_username,
                "password": cfg.mqtt_password,
                "useTls": cfg.mqtt_tls,
                "rootTopic": cfg.mqtt_root_topic,
            },
            {
                "hostname": cfg.mqtt_host,
                "port": cfg.mqtt_port,
                "username": cfg.mqtt_username,
                "password": cfg.mqtt_password,
                "tls": cfg.mqtt_tls,
                "rootTopic": cfg.mqtt_root_topic,
            },
            {
                "mqttServer": cfg.mqtt_host,
                "mqttPort": cfg.mqtt_port,
                "username": cfg.mqtt_username,
                "password": cfg.mqtt_password,
            },
            {
                "server": cfg.mqtt_host,
                "port": cfg.mqtt_port,
                "username": cfg.mqtt_username,
                "password": cfg.mqtt_password,
            },
            {"hostname": cfg.mqtt_host, "portNumber": cfg.mqtt_port},
            {"hostname": cfg.mqtt_host},
        ]

        last_err: Optional[Exception] = None
        for kwargs in attempts:
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            try:
                return MQTTInterface(**kwargs)
            except TypeError as e:
                last_err = e
                continue

        if last_err is not None:
            raise last_err
        raise RuntimeError("failed to create MQTT interface")

    def _connect(self) -> Any:
        with self._iface_lock:
            if self._iface is not None:
                return self._iface

            # Lazy imports so tests can run without meshtastic installed.
            try:
                from meshtastic.tcp_interface import TCPInterface  # type: ignore
                from pubsub import pub  # type: ignore
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "Meshtastic dependencies missing. Install backend/requirements.txt"
                ) from e

            with self._pubsub_lock:
                if not self._pubsub_subscribed:
                    pub.subscribe(self._on_receive, "meshtastic.receive")
                    self._pubsub_subscribed = True

            logger.info("Connecting to mesh (%s)", self._target_str())
            cfg = self._cfg

            if cfg.transport == "mqtt":
                iface = self._create_mqtt_iface()
            else:
                try:
                    iface = TCPInterface(
                        hostname=cfg.mesh_host, portNumber=cfg.mesh_port
                    )
                except TypeError:
                    # Older meshtastic versions may not accept portNumber
                    iface = TCPInterface(hostname=cfg.mesh_host)

            self._iface = iface
            return iface

    def _disconnect(self) -> None:
        with self._iface_lock:
            iface = self._iface
            self._iface = None

        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass

        self._set_connected(False)

    def _record_mesh_event(self, event: str, detail: Optional[str] = None) -> None:
        if self._stats_db is None:
            return
        try:
            self._stats_db.record_mesh_event(event, detail)
        except Exception:
            pass

    def _on_receive(self, packet, interface) -> None:  # noqa: ANN001
        try:
            msg = json_safe_packet(packet)
        except Exception as e:
            msg = {
                "rxTime": now_epoch(),
                "fromId": None,
                "toId": None,
                "snr": None,
                "rssi": None,
                "hopLimit": None,
                "channel": None,
                "portnum": None,
                "text": None,
                "payload_b64": None,
                "error": f"failed_to_decode_packet: {type(e).__name__}: {e}",
            }

        with self._messages_lock:
            self._messages_cache.append(msg)
            if len(self._messages_cache) > self._max_messages:
                self._messages_cache[:] = self._messages_cache[-self._max_messages :]

        if self._stats_db is not None:
            try:
                self._stats_db.record_message(msg)
            except Exception:
                pass

    def _refresh_nodes(self, iface: Any) -> None:
        nodes = iface.nodes  # dict
        if not isinstance(nodes, dict):
            return
        with self._nodes_lock:
            self._nodes_cache.clear()
            self._nodes_cache.update(nodes)

        if self._stats_db is not None:
            try:
                self._stats_db.record_nodes_snapshot(nodes)
            except Exception:
                pass

    def _refresh_channels(self, iface: Any) -> None:
        channels_out: List[Dict[str, Any]] = []

        try:
            local = getattr(iface, "localNode", None)
            preset = _channel_preset_name(local)
            chan_list = getattr(local, "channels", None) if local is not None else None
            if isinstance(chan_list, (list, tuple)):
                for i, ch in enumerate(chan_list):
                    entry = _channel_entry(i, ch)
                    if entry is not None:
                        if preset and not entry.get("preset"):
                            entry["preset"] = preset
                        channels_out.append(entry)
        except Exception:
            channels_out = []

        with self._channels_lock:
            self._channels_cache = channels_out

    def _worker(self) -> None:
        backoff_sec = 1.0
        last_nodes_refresh = 0.0
        was_connected = False

        while not self._stop.is_set():
            try:
                cfg = self._cfg
                if cfg.transport == "tcp" and not cfg.mesh_host:
                    self._set_connected(False)
                    self._set_error("not_configured: set meshHost")
                    time.sleep(0.5)
                    continue

                iface = self._connect()
                if not was_connected:
                    self._record_mesh_event("connect", self._target_str())
                    was_connected = True
                self._set_connected(True)
                self._set_error(None)
                backoff_sec = 1.0

                # Nodes refresh loop
                now = time.time()
                if now - last_nodes_refresh >= self._nodes_refresh_sec:
                    self._refresh_nodes(iface)
                    self._refresh_channels(iface)
                    last_nodes_refresh = now

                time.sleep(0.2)
            except Exception as e:
                self._set_connected(False)
                self._set_error(f"{type(e).__name__}: {e}")
                logger.warning("Mesh worker error: %s", self._last_error)
                if was_connected:
                    self._record_mesh_event("disconnect", self._last_error)
                    was_connected = False
                self._record_mesh_event("error", self._last_error)
                self._disconnect()

                time.sleep(backoff_sec)
                backoff_sec = min(30.0, backoff_sec * 1.7)


class FakeMeshService:
    """
    Small in-memory fake for tests and local UI dev without a Meshtastic node.
    """

    def __init__(self) -> None:
        self._cfg = MeshConfig(
            transport="tcp",
            mesh_host="localhost",
            mesh_port=4403,
            mqtt_host="mqtt.meshtastic.org",
            mqtt_port=1883,
            mqtt_username=None,
            mqtt_password=None,
            mqtt_tls=False,
            mqtt_root_topic=None,
        )
        self._connected = False
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._messages: List[Dict[str, Any]] = []
        self._channels: List[Dict[str, Any]] = []
        self._radio: Optional[Dict[str, Any]] = None
        self._device_config: Optional[Dict[str, Any]] = None
        self.sent: List[Tuple[str, Optional[str], Optional[int]]] = []

    def start(self) -> None:  # noqa: D401
        self._connected = True

    def get_config(self) -> MeshConfig:
        return self._cfg

    def reconfigure(
        self,
        *,
        transport: Optional[str] = None,
        mesh_host: Optional[str] = None,
        mesh_port: Optional[int] = None,
        mqtt_host: Optional[str] = None,
        mqtt_port: Optional[int] = None,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        mqtt_tls: Optional[bool] = None,
        mqtt_root_topic: Optional[str] = None,
    ) -> None:
        cfg = self._cfg
        self._cfg = MeshConfig(
            transport=cfg.transport if transport is None else _normalize_transport(transport),
            mesh_host=cfg.mesh_host if mesh_host is None else str(mesh_host),
            mesh_port=cfg.mesh_port if mesh_port is None else int(mesh_port),
            mqtt_host=cfg.mqtt_host if mqtt_host is None else str(mqtt_host),
            mqtt_port=cfg.mqtt_port if mqtt_port is None else int(mqtt_port),
            mqtt_username=cfg.mqtt_username if mqtt_username is None else mqtt_username,
            mqtt_password=cfg.mqtt_password if mqtt_password is None else mqtt_password,
            mqtt_tls=cfg.mqtt_tls if mqtt_tls is None else bool(mqtt_tls),
            mqtt_root_topic=cfg.mqtt_root_topic if mqtt_root_topic is None else mqtt_root_topic,
        )

    def is_connected(self) -> bool:
        return self._connected

    def last_error(self) -> Optional[str]:
        return None

    def get_nodes_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._nodes)

    def get_messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    def get_channels_snapshot(self) -> List[Dict[str, Any]]:
        return list(self._channels)

    def get_radio_snapshot(self) -> Optional[Dict[str, Any]]:
        return dict(self._radio) if isinstance(self._radio, dict) else None

    def get_device_config(self, *, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        if not isinstance(self._device_config, dict):
            return None
        if include_secrets:
            return dict(self._device_config)
        return _redact_secrets(self._device_config)

    def send_text(self, text: str, to: Optional[str] = None, channel: Optional[int] = None) -> None:
        if not text:
            raise ValueError("text is required")
        ch = int(channel) if channel is not None else None
        self.sent.append((text, to, ch))

    # helpers for tests
    def seed_nodes(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        self._nodes = dict(nodes)

    def seed_messages(self, messages: List[Dict[str, Any]]) -> None:
        self._messages = list(messages)

    def seed_channels(self, channels: List[Dict[str, Any]]) -> None:
        self._channels = list(channels)

    def seed_radio(self, node: Dict[str, Any]) -> None:
        self._radio = dict(node)

    def seed_device_config(self, cfg: Dict[str, Any]) -> None:
        self._device_config = dict(cfg)


def _channel_entry(index: int, channel: Any) -> Optional[Dict[str, Any]]:
    """
    Create a small JSON-safe channel entry.

    Intentionally does NOT expose PSK/crypto material.
    """
    if channel is None:
        return None

    name = _get_path(channel, "settings.name", "name")
    role = _get_path(channel, "role")
    enabled = _get_path(channel, "enabled", "isEnabled")

    role_str: Optional[str]
    if role is None:
        role_str = None
    elif isinstance(role, str):
        role_str = role
    else:
        role_str = getattr(role, "name", None) or str(role)

    enabled_bool: Optional[bool]
    if isinstance(enabled, bool):
        enabled_bool = enabled
    elif isinstance(role_str, str) and role_str.strip().upper() == "DISABLED":
        enabled_bool = False
    else:
        enabled_bool = None

    name_str = str(name).strip() if isinstance(name, str) else None
    if name_str == "":
        name_str = None

    return {
        "index": int(index),
        "name": name_str,
        "role": role_str,
        "enabled": enabled_bool,
    }


def _channel_preset_name(local: Any) -> Optional[str]:
    if local is None:
        return None
    try:
        lc = getattr(local, "localConfig", None)
        if lc is None:
            return None
        if isinstance(lc, dict):
            val = lc.get("lora") or lc.get("loraConfig") or lc.get("lora_config")
            if isinstance(val, dict):
                preset = val.get("modemPreset") or val.get("modem_preset")
                if isinstance(preset, str):
                    return preset
                name = getattr(preset, "name", None)
                if isinstance(name, str):
                    return name
                return None
            preset = lc.get("modemPreset") or lc.get("modem_preset")
            if isinstance(preset, str):
                return preset
            name = getattr(preset, "name", None)
            if isinstance(name, str):
                return name
            return None

        lora = getattr(lc, "lora", None) or getattr(lc, "loraConfig", None) or getattr(lc, "lora_config", None)
        if lora is not None:
            preset = getattr(lora, "modemPreset", None) or getattr(lora, "modem_preset", None)
            name = getattr(preset, "name", None)
            if isinstance(preset, str):
                return preset
            if isinstance(name, str):
                return name
        preset = getattr(lc, "modemPreset", None) or getattr(lc, "modem_preset", None)
        if isinstance(preset, str):
            return preset
        name = getattr(preset, "name", None)
        if isinstance(name, str):
            return name
    except Exception:
        return None
    return None


def _redact_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() == "psk":
                out[k] = "***redacted***"
            else:
                out[k] = _redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [_redact_secrets(v) for v in obj]
    return obj


def _get_path(obj: Any, *paths: str) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if cur is None:
                ok = False
                break
            if isinstance(cur, dict):
                if part not in cur:
                    ok = False
                    break
                cur = cur.get(part)
            else:
                if not hasattr(cur, part):
                    ok = False
                    break
                cur = getattr(cur, part)
        if ok:
            return cur
    return None
