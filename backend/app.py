from __future__ import annotations
if __name__ == "__main__" and __package__ is None:
    # Allow `python backend/app.py` by ensuring repo root is on sys.path *before* imports.
    import sys
    from pathlib import Path as _Path
    _repo_root = str(_Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from flask import Flask, Response, jsonify, request
from backend.jsonsafe import node_entry, now_epoch, radio_entry
from backend.mesh_service import MeshService
from backend.tcp_relay import TcpRelay
from backend.stats_db import StatsDB
from backend.config_store import resolve_config_path, update_config

TEXT_MESSAGE_APP = "TEXT_MESSAGE_APP"
def _get_env_int(name: str, default: int) -> int:
    return _parse_int(os.getenv(name), default)
def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse integer value '%s': %s", value, e)
        return default
def _get_env_float(name: str, default: float) -> float:
    return _parse_float(os.getenv(name), default)
def _parse_float(value: Optional[str], default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse float value '%s': %s", value, e)
        return default
def _parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    if value is None or value == "":
        return default
    v = str(value).strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return default
def _parse_bool_value(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return None
def _base_status_payload(cfg: Any, configured: bool, mesh_service: Any) -> Dict[str, Any]:
    return {
        "ok": True,
        "configured": configured,
        "meshHost": (cfg.mesh_host or None),
        "meshPort": cfg.mesh_port,
        "connected": bool(mesh_service.is_connected()),
        "lastError": mesh_service.last_error(),
    }
def _is_configured(cfg: Any) -> bool:
    return bool(cfg.mesh_host)
def _split_nodes(
    nodes: Dict[str, Dict[str, Any]],
) -> Tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    direct: list[Dict[str, Any]] = []
    relayed: list[Dict[str, Any]] = []
    for node_id, node in nodes.items():
        entry = node_entry(str(node_id), node)
        if entry.get("snr") is None:
            entry.pop("quality", None)
            relayed.append(entry)
        else:
            direct.append(entry)
    _sort_nodes_by_freshness(direct)
    _sort_nodes_by_freshness(relayed)
    return direct, relayed
def _sort_nodes_by_freshness(items: list[Dict[str, Any]]) -> None:
    def sort_key(item: Dict[str, Any]) -> Tuple[int, int]:
        age = item.get("ageSec")
        if age is None:
            return (1, 10**12)
        return (0, int(age))
    items.sort(key=sort_key)
def _parse_history_query() -> Tuple[int, Optional[int], str]:
    limit_raw = request.args.get("limit")
    since_raw = request.args.get("since")
    order = request.args.get("order", "desc")
    limit = _parse_int(limit_raw, 500)
    since = _parse_int(since_raw, 0) if since_raw not in {None, ""} else None
    return limit, since, order
def _default_frontend_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "frontend"
    if candidate.exists():
        return candidate
    try:
        from importlib import resources

        return Path(resources.files("frontend"))
    except (ImportError, AttributeError, TypeError) as e:
        logger.warning("Failed to load frontend from package resources: %s", e)
        return candidate


class StatsCache:
    def __init__(
        self,
        *,
        stats_db: Any,
        interval_sec: int,
        hours: int,
        nodes_days: int,
        local_id_fn: Optional[Any] = None,
    ) -> None:
        self._stats_db = stats_db
        self._interval_sec = max(1, int(interval_sec))
        self._hours = max(1, int(hours))
        self._nodes_days = max(1, int(nodes_days))
        self._local_id_fn = local_id_fn
        self._lock = threading.Lock()
        self._summary = None
        self._status_latest = None
        self._status_series: list[Dict[str, Any]] = []
        self._last_error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        self.refresh()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._wake.wait(self._interval_sec):
                self._wake.clear()
                if self._stop.is_set():
                    return
            self.refresh()

    def refresh(self) -> None:
        try:
            local_id = None
            if callable(self._local_id_fn):
                try:
                    local_id = self._local_id_fn()
                except (AttributeError, TypeError, RuntimeError) as e:
                    logger.warning("Failed to get local node ID: %s", e)
                    local_id = None
            summary = self._stats_db.summary(
                hours=self._hours,
                nodes_days=self._nodes_days,
                local_node_id=local_id,
            )
            status_series = []
            status_latest = None
            try:
                status_series = self._stats_db.list_status_reports(limit=120, order="asc")
                if status_series:
                    status_latest = status_series[-1]
            except (ValueError, RuntimeError, OSError) as e:
                logger.warning("Failed to load status reports: %s", e)
                status_series = []
            with self._lock:
                self._summary = summary
                self._status_latest = status_latest
                self._status_series = status_series
                self._last_error = None
        except Exception as e:
            logging.getLogger("meshtastic_monitor.stats").warning("Stats refresh failed: %s", e, exc_info=True)
            with self._lock:
                self._last_error = f"{type(e).__name__}: {e}"

    def get_snapshot(self) -> Tuple[Any, Any, list[Dict[str, Any]], Optional[str]]:
        with self._lock:
            return self._summary, self._status_latest, list(self._status_series), self._last_error

    def update_interval_minutes(self, minutes: int) -> None:
        minutes = int(minutes)
        if minutes < 1:
            raise ValueError("statsCacheMinutes must be >= 1")
        self._interval_sec = minutes * 60
        self._wake.set()

    def interval_minutes(self) -> int:
        return max(1, int(self._interval_sec // 60))


def create_app(
    *,
    mesh_service: Optional[Any] = None,
    frontend_dir: Optional[Path] = None,
    stats_db: Optional[Any] = None,
) -> Flask:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    frontend_path = frontend_dir or _default_frontend_path()
    app = Flask(
        __name__,
        static_folder=str(frontend_path),
        static_url_path="/static",
    )
    # Service init
    if mesh_service is None:
        mesh_host = os.getenv("MESH_HOST", "").strip()
        mesh_port = _get_env_int("MESH_PORT", 4403)
        nodes_refresh_sec = _get_env_int("NODES_REFRESH_SEC", 5)
        max_messages = _get_env_int("MAX_MESSAGES", 200)
        relay_enabled = _parse_bool_env(os.getenv("RELAY_ENABLED"), False)
        relay_host = os.getenv("RELAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
        relay_port = _get_env_int("RELAY_PORT", 4403)
        relay: Optional[TcpRelay] = None
        connect_host: Optional[str] = None
        connect_port: Optional[int] = None
        if relay_enabled:
            if not mesh_host:
                logging.warning("Relay enabled but mesh host is not configured; relay disabled")
            else:
                try:
                    relay = TcpRelay(
                        relay_host,
                        relay_port,
                        mesh_host,
                        mesh_port,
                    )
                    relay.start()
                    connect_host = "127.0.0.1" if relay_host in {"0.0.0.0", "::", ""} else relay_host
                    connect_port = relay.listen_port
                except (OSError, RuntimeError, ValueError) as e:
                    logging.warning("Failed to start TCP relay: %s", e, exc_info=True)
        if stats_db is None:
            stats_path = os.getenv("STATS_DB_PATH", "meshmon.db").strip()
            if stats_path.lower() not in {"", "off", "none", "disabled"}:
                history_interval = _get_env_int("NODES_HISTORY_INTERVAL_SEC", 60)
                status_interval = _get_env_int("STATUS_HISTORY_INTERVAL_SEC", 60)
                stats_db = StatsDB(
                    stats_path,
                    nodes_history_interval_sec=history_interval,
                    status_history_interval_sec=status_interval,
                )
        mesh_service = MeshService(
            mesh_host,
            mesh_port,
            connect_host=connect_host,
            connect_port=connect_port,
            relay=relay,
            nodes_refresh_sec=nodes_refresh_sec,
            max_messages=max_messages,
            stats_db=stats_db,
            mesh_http_port=_get_env_int("MESH_HTTP_PORT", 80),
            status_ttl_sec=_get_env_int("STATUS_TTL_SEC", 5),
            sms_enabled=_parse_bool_env(os.getenv("SMS_ENABLED"), False),
            sms_api_url=os.getenv("SMS_API_URL", "").strip(),
            sms_api_key=os.getenv("SMS_API_KEY", "").strip(),
            sms_phone=os.getenv("SMS_PHONE", "").strip(),
            sms_allow_from_ids=os.getenv("SMS_ALLOW_FROM_IDS", "").strip(),
            sms_allow_types=os.getenv("SMS_ALLOW_TYPES", "").strip(),
            sms_timeout_sec=_get_env_float("SMS_TIMEOUT_SEC", 4.0),
        )
        mesh_service.start()
    elif stats_db is not None:
        try:
            if getattr(mesh_service, "_stats_db", None) is None:
                setattr(mesh_service, "_stats_db", stats_db)
        except Exception:
            pass
    stats_cache = None
    if stats_db is not None:
        stats_cache_minutes = _get_env_int("STATS_CACHE_MINUTES", 30)

        def _get_local_id() -> Optional[str]:
            getter = getattr(mesh_service, "get_radio_snapshot", None)
            if callable(getter):
                try:
                    return _local_node_id(getter())
                except Exception:
                    return None
            return None

        stats_cache = StatsCache(
            stats_db=stats_db,
            interval_sec=max(1, stats_cache_minutes) * 60,
            hours=_get_env_int("STATS_WINDOW_HOURS", 24),
            nodes_days=_get_env_int("STATS_NODES_DAYS", 7),
            local_id_fn=_get_local_id,
        )
        stats_cache.start()
    # --- frontend routes
    @app.get("/")
    def index() -> Response:
        return app.send_static_file("index.html")
    # --- API
    @app.get("/api/health")
    def api_health():
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        payload = _base_status_payload(cfg, configured, mesh_service)
        payload["generatedAt"] = now_epoch()
        return jsonify(payload)
    @app.get("/api/config")
    def api_config_get():
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        sms_cfg = {
            "enabled": False,
            "apiUrl": None,
            "phone": None,
            "apiKeySet": False,
        }
        relay_cfg = {
            "enabled": False,
            "listenHost": None,
            "listenPort": None,
        }
        getter = getattr(mesh_service, "get_sms_config", None)
        if callable(getter):
            try:
                result = getter()
                if isinstance(result, dict):
                    sms_cfg.update(result)
            except Exception:
                pass
        relay_getter = getattr(mesh_service, "get_relay_stats", None)
        if callable(relay_getter):
            try:
                result = relay_getter()
                if isinstance(result, dict):
                    relay_cfg["enabled"] = bool(result.get("enabled"))
                    relay_cfg["listenHost"] = result.get("listenHost")
                    relay_cfg["listenPort"] = result.get("listenPort")
            except Exception:
                pass
        stats_cfg = {
            "cacheMinutes": stats_cache.interval_minutes()
            if stats_cache is not None
            else _get_env_int("STATS_CACHE_MINUTES", 30),
        }
        cfg_path = os.getenv("MESHMON_CONFIG", "").strip() or None
        return jsonify(
            {
                "ok": True,
                "configured": configured,
                "meshHost": (cfg.mesh_host or None),
                "meshPort": cfg.mesh_port,
                "sms": sms_cfg,
                "relay": relay_cfg,
                "stats": stats_cfg,
                "configPath": cfg_path,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/status")
    def api_status():
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        status = None
        getter = getattr(mesh_service, "get_status_snapshot", None)
        if callable(getter):
            try:
                status = getter()
            except Exception:
                status = None
        report_ok = bool(status.get("ok")) if isinstance(status, dict) else False
        report = status.get("report") if isinstance(status, dict) else None
        report_status = status.get("status") if isinstance(status, dict) else None
        report_error = status.get("error") if isinstance(status, dict) else None
        report_fetched_at = status.get("fetchedAt") if isinstance(status, dict) else None
        report_url = status.get("url") if isinstance(status, dict) else None
        payload = _base_status_payload(cfg, configured, mesh_service)
        payload.update(
            {
                "reportOk": report_ok,
                "reportStatus": report_status,
                "report": report,
                "reportError": report_error,
                "reportFetchedAt": report_fetched_at,
                "reportUrl": report_url,
            }
        )
        payload["generatedAt"] = now_epoch()
        return jsonify(payload)
    @app.get("/api/relay")
    def api_relay():
        getter = getattr(mesh_service, "get_relay_stats", None)
        relay_stats = None
        if callable(getter):
            try:
                relay_stats = getter()
            except Exception:
                relay_stats = None
        if not isinstance(relay_stats, dict):
            relay_stats = {"enabled": False, "clientCount": 0, "clients": []}
        relay_stats["generatedAt"] = now_epoch()
        return jsonify(relay_stats)
    @app.get("/api/nodes")
    def api_nodes():
        include_observed_raw = request.args.get("includeObserved", "1")
        include_observed = str(include_observed_raw).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
        nodes = mesh_service.get_nodes_snapshot()
        direct, relayed = _split_nodes(nodes)
        mesh_count = len(nodes)
        observed_count = 0
        observed_added = 0
        if include_observed and stats_db is not None:
            known_fn = getattr(stats_db, "known_node_entries", None)
            if callable(known_fn):
                try:
                    known = list(known_fn())
                except Exception:
                    known = []
                observed_count = len(known)
                existing_ids = {n.get("id") for n in direct + relayed}
                for entry in known:
                    node_id = entry.get("id")
                    if not node_id or node_id in existing_ids:
                        continue
                    if entry.get("snr") is None:
                        entry.pop("quality", None)
                        relayed.append(entry)
                    else:
                        direct.append(entry)
                    existing_ids.add(node_id)
                    observed_added += 1
                _sort_nodes_by_freshness(direct)
                _sort_nodes_by_freshness(relayed)
        return jsonify(
            {
                "total": len(direct) + len(relayed),
                "meshCount": mesh_count,
                "observedCount": observed_count,
                "observedAdded": observed_added,
                "includeObserved": include_observed,
                "direct": direct,
                "relayed": relayed,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/nodes/history")
    def api_nodes_history():
        if stats_db is None:
            return jsonify({"ok": False, "error": "stats disabled", "generatedAt": now_epoch()}), 503
        node_id = request.args.get("nodeId")
        limit, since, order = _parse_history_query()
        try:
            history = stats_db.list_node_history(
                node_id=node_id, limit=limit, since=since, order=order
            )
        except Exception:
            history = []
        return jsonify(
            {
                "ok": True,
                "count": len(history),
                "items": history,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/messages")
    def api_messages():
        limit_raw = request.args.get("limit")
        offset_raw = request.args.get("offset")
        order = request.args.get("order", "asc")
        limit = _parse_int(limit_raw, 200)
        offset = _parse_int(offset_raw, 0)
        # Newest last (chronological) by default
        if stats_db is not None and hasattr(stats_db, "list_messages"):
            try:
                return jsonify(
                    stats_db.list_messages(
                        limit=limit,
                        offset=offset,
                        order=order,
                        app=TEXT_MESSAGE_APP,
                    )
                )
            except Exception:
                pass
        # Fallback to in-memory messages
        msgs = [m for m in mesh_service.get_messages() if m.get("app") == TEXT_MESSAGE_APP]
        if limit > 0:
            if order and str(order).lower() == "desc":
                msgs = list(reversed(msgs))
            msgs = msgs[: int(limit)]
        return jsonify(msgs)

    @app.get("/api/diag")
    def api_diag():
        limit_raw = request.args.get("limit")
        limit = _parse_int(limit_raw, 50)
        getter = getattr(mesh_service, "get_diag_snapshot", None)
        items: list = []
        if callable(getter):
            try:
                items = getter(limit=limit)
            except Exception:
                items = []
        return jsonify({"items": items, "generatedAt": now_epoch()})
    @app.get("/api/channels")
    def api_channels():
        channels = mesh_service.get_channels_snapshot()
        return jsonify(
            {
                "total": len(channels),
                "channels": channels,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/radio")
    def api_radio():
        node = None
        getter = getattr(mesh_service, "get_radio_snapshot", None)
        if callable(getter):
            try:
                node = getter()
            except Exception:
                node = None
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        return jsonify(
            {
                "ok": True,
                "configured": configured,
                "connected": bool(mesh_service.is_connected()),
                "node": radio_entry(node) if isinstance(node, dict) else None,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/device/config")
    def api_device_config():
        include_raw = request.args.get("includeSecrets", "0")
        include_secrets = str(include_raw).strip().lower() in {"1", "true", "yes", "y", "on"}
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        getter = getattr(mesh_service, "get_device_config", None)
        device = None
        if callable(getter):
            try:
                device = getter(include_secrets=include_secrets)
            except (AttributeError, TypeError, RuntimeError, OSError) as e:
                logger.warning("Failed to get device config: %s", e)
                device = None
        if device is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "device config not available",
                        "configured": configured,
                        "connected": bool(mesh_service.is_connected()),
                        "secretsIncluded": include_secrets,
                        "generatedAt": now_epoch(),
                    }
                ),
                503,
            )
        return jsonify(
            {
                "ok": True,
                "configured": configured,
                "connected": bool(mesh_service.is_connected()),
                "secretsIncluded": include_secrets,
                "device": device,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/node/<path:node_id>")
    def api_node(node_id: str):
        node_id = str(node_id or "").strip()
        if not node_id:
            return jsonify({"ok": False, "error": "node id required"}), 400
        node = None
        try:
            nodes = mesh_service.get_nodes_snapshot()
            node = nodes.get(node_id)
        except (AttributeError, TypeError, KeyError) as e:
            logger.warning("Failed to get node %s: %s", node_id, e)
            node = None
        stats = None
        if stats_db is not None:
            try:
                stats = stats_db.get_node_stats(node_id)
            except Exception:
                stats = None
        if node is None and stats is None:
            return jsonify({"ok": False, "error": "node not found"}), 404
        return jsonify(
            {
                "ok": True,
                "node": node_entry(node_id, node) if isinstance(node, dict) else None,
                "stats": stats,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/node/<path:node_id>/history")
    def api_node_history(node_id: str):
        node_id = str(node_id or "").strip()
        if not node_id:
            return jsonify({"ok": False, "error": "node id required"}), 400
        if stats_db is None:
            return jsonify({"ok": False, "error": "stats disabled", "generatedAt": now_epoch()}), 503
        limit, since, order = _parse_history_query()
        try:
            history = stats_db.list_node_history(
                node_id=node_id, limit=limit, since=since, order=order
            )
        except Exception:
            history = []
        return jsonify(
            {
                "ok": True,
                "nodeId": node_id,
                "count": len(history),
                "items": history,
                "generatedAt": now_epoch(),
            }
        )
    @app.get("/api/stats")
    def api_stats():
        if stats_db is None:
            return jsonify({"ok": False, "error": "stats disabled", "generatedAt": now_epoch()})
        summary = None
        status_latest = None
        status_series = []
        if stats_cache is not None:
            summary, status_latest, status_series, _ = stats_cache.get_snapshot()
            if summary is None:
                stats_cache.refresh()
                summary, status_latest, status_series, _ = stats_cache.get_snapshot()
        if summary is None:
            hours = _get_env_int("STATS_WINDOW_HOURS", 24)
            nodes_days = _get_env_int("STATS_NODES_DAYS", 7)
            local_id = None
            getter = getattr(mesh_service, "get_radio_snapshot", None)
            if callable(getter):
                try:
                    local_id = _local_node_id(getter())
                except Exception:
                    local_id = None
            summary = stats_db.summary(hours=hours, nodes_days=nodes_days, local_node_id=local_id)
        hours = _get_env_int("STATS_WINDOW_HOURS", 24)
        messages_last_hour = summary.messages_last_hour
        messages_window = summary.messages_window
        hourly_window = summary.hourly_window
        getter = getattr(stats_db, "get_message_window", None)
        if callable(getter):
            try:
                msg_window = getter(hours=hours)
                messages_last_hour = int(msg_window.get("lastHour") or 0)
                messages_window = int(msg_window.get("window") or 0)
                hourly_window = msg_window.get("hourlyWindow") or []
            except Exception:
                pass
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        try:
            status_series = stats_db.list_status_reports(limit=120, order="asc")
            status_latest = status_series[-1] if status_series else None
        except Exception:
            status_series = []
            status_latest = None
        return jsonify(
            {
                "ok": True,
                "dbPath": summary.db_path,
                "generatedAt": summary.generated_at,
                "configured": configured,
                "meshHost": (cfg.mesh_host or None),
                "meshPort": cfg.mesh_port,
                "connected": bool(mesh_service.is_connected()),
                "lastError": mesh_service.last_error(),
                "counters": summary.counters,
                "messages": {
                    "lastHour": messages_last_hour,
                    "windowHours": summary.window_hours,
                    "window": messages_window,
                    "hourlyWindow": hourly_window,
                },
                "apps": {
                    "counts": summary.app_counts,
                    "requestsToMe": summary.app_requests_to_me,
                    "requesters": summary.app_requesters,
                },
                "nodes": {
                    "topFrom": summary.top_from,
                    "topTo": summary.top_to,
                    "mostVisible": summary.nodes_visible,
                    "zeroHop": summary.nodes_zero_hops,
                    "snrStats": summary.nodes_snr_stats,
                    "flaky": summary.nodes_flaky,
                    "windowDays": summary.nodes_window_days,
                    "historyIntervalSec": summary.nodes_history_interval_sec,
                },
                "events": summary.recent_events,
                "status": {
                    "latest": status_latest,
                    "series": status_series,
                },
            }
        )
    @app.post("/api/send")
    def api_send():
        body = request.get_json(silent=True) or {}
        text = body.get("text")
        to = body.get("to")
        channel = body.get("channel")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"ok": False, "error": "text is required"}), 400
        to_clean = None
        if isinstance(to, str) and to.strip():
            to_clean = to.strip()
        channel_clean: Optional[int] = None
        if channel is not None:
            try:
                channel_clean = int(channel)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "channel must be an int"}), 400
            if channel_clean < 0:
                return jsonify({"ok": False, "error": "channel must be >= 0"}), 400
        try:
            mesh_service.send_text(text.strip(), to_clean, channel=channel_clean)
            recorder = getattr(mesh_service, "record_outgoing_text", None)
            if callable(recorder):
                try:
                    recorder(text.strip(), to_clean, channel_clean)
                except (AttributeError, TypeError, RuntimeError) as e:
                    logger.warning("Failed to record outgoing text: %s", e)
                    pass
            if stats_db is not None:
                stats_db.record_send(ok=True)
                if stats_cache is not None:
                    stats_cache.refresh()
            return jsonify({"ok": True})
        except Exception as e:
            if stats_db is not None:
                stats_db.record_send(ok=False, error=f"{type(e).__name__}: {e}")
                if stats_cache is not None:
                    stats_cache.refresh()
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    @app.post("/api/config")
    def api_config():
        """
        Optional runtime reconfiguration. Useful for local UI settings without editing env vars.
        """
        body = request.get_json(silent=True) or {}
        mesh_host = body.get("meshHost")
        mesh_port = body.get("meshPort")
        sms_enabled = body.get("smsEnabled")
        sms_api_url = body.get("smsApiUrl")
        sms_api_key = body.get("smsApiKey")
        sms_phone = body.get("smsPhone")
        sms_allow_from_ids = body.get("smsAllowFromIds")
        sms_allow_types = body.get("smsAllowTypes")
        relay_enabled = body.get("relayEnabled")
        relay_host = body.get("relayHost")
        relay_port = body.get("relayPort")
        stats_cache_minutes = body.get("statsCacheMinutes")
        kwargs: Dict[str, Any] = {}
        sms_kwargs: Dict[str, Any] = {}
        relay_kwargs: Dict[str, Any] = {}
        stats_kwargs: Dict[str, Any] = {}
        if mesh_host is not None:
            if not isinstance(mesh_host, str) or not mesh_host.strip():
                return jsonify({"ok": False, "error": "meshHost must be a non-empty string"}), 400
            kwargs["mesh_host"] = mesh_host.strip()
        if mesh_port is not None:
            try:
                kwargs["mesh_port"] = int(mesh_port)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "meshPort must be an int"}), 400
        if sms_enabled is not None:
            parsed = _parse_bool_value(sms_enabled)
            if parsed is None:
                return jsonify({"ok": False, "error": "smsEnabled must be a boolean"}), 400
            sms_kwargs["enabled"] = parsed
        if sms_api_url is not None:
            if not isinstance(sms_api_url, str):
                return jsonify({"ok": False, "error": "smsApiUrl must be a string"}), 400
            url = sms_api_url.strip()
            # Basic URL validation
            if url and not url.startswith(("http://", "https://")):
                return jsonify({"ok": False, "error": "smsApiUrl must start with http:// or https://"}), 400
            sms_kwargs["api_url"] = url
        if sms_api_key is not None:
            if not isinstance(sms_api_key, str):
                return jsonify({"ok": False, "error": "smsApiKey must be a string"}), 400
            sms_kwargs["api_key"] = sms_api_key.strip()
        if sms_phone is not None:
            if not isinstance(sms_phone, str):
                return jsonify({"ok": False, "error": "smsPhone must be a string"}), 400
            phone = sms_phone.strip()
            # Phone number validation: must contain at least one digit and only valid chars
            if phone:
                if not any(c.isdigit() for c in phone):
                    return jsonify({"ok": False, "error": "smsPhone must contain at least one digit"}), 400
                if not all(c.isdigit() or c in "+-() " for c in phone):
                    return jsonify({"ok": False, "error": "smsPhone contains invalid characters"}), 400
                # Remove spaces to check structure
                phone_no_spaces = phone.replace(" ", "")
                if not phone_no_spaces or phone_no_spaces in ["++", "+-", "-+", "--"]:
                    return jsonify({"ok": False, "error": "smsPhone has invalid format"}), 400
            sms_kwargs["phone"] = phone
        if sms_allow_from_ids is not None:
            if not isinstance(sms_allow_from_ids, str):
                return jsonify({"ok": False, "error": "smsAllowFromIds must be a string"}), 400
            sms_kwargs["allow_from_ids"] = sms_allow_from_ids.strip()
        if sms_allow_types is not None:
            if not isinstance(sms_allow_types, str):
                return jsonify({"ok": False, "error": "smsAllowTypes must be a string"}), 400
            sms_kwargs["allow_types"] = sms_allow_types.strip()
        if relay_enabled is not None:
            parsed = _parse_bool_value(relay_enabled)
            if parsed is None:
                return jsonify({"ok": False, "error": "relayEnabled must be a boolean"}), 400
            relay_kwargs["enabled"] = parsed
        if relay_host is not None:
            if not isinstance(relay_host, str):
                return jsonify({"ok": False, "error": "relayHost must be a string"}), 400
            relay_kwargs["listen_host"] = relay_host.strip() or "0.0.0.0"
        if relay_port is not None:
            try:
                relay_port_val = int(relay_port)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "relayPort must be an int"}), 400
            if relay_port_val <= 0 or relay_port_val > 65535:
                return jsonify({"ok": False, "error": "relayPort must be 1..65535"}), 400
            relay_kwargs["listen_port"] = relay_port_val
        if stats_cache_minutes is not None:
            try:
                stats_cache_val = int(stats_cache_minutes)
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": "statsCacheMinutes must be an int"}), 400
            if stats_cache_val < 1:
                return jsonify({"ok": False, "error": "statsCacheMinutes must be >= 1"}), 400
            stats_kwargs["stats_cache_minutes"] = stats_cache_val
        if not kwargs and not sms_kwargs and not relay_kwargs and not stats_kwargs:
            return jsonify({"ok": False, "error": "no config fields provided"}), 400
        try:
            if kwargs:
                mesh_service.reconfigure(**kwargs)
            if sms_kwargs:
                updater = getattr(mesh_service, "update_sms_config", None)
                if callable(updater):
                    updater(**sms_kwargs)
            if relay_kwargs:
                updater = getattr(mesh_service, "update_relay_config", None)
                if callable(updater):
                    updater(**relay_kwargs)
            if stats_kwargs and stats_cache is not None:
                stats_cache.update_interval_minutes(int(stats_kwargs["stats_cache_minutes"]))
                os.environ["STATS_CACHE_MINUTES"] = str(stats_kwargs["stats_cache_minutes"])
            config_path_raw = os.getenv("MESHMON_CONFIG", "").strip()
            if config_path_raw:
                updates: Dict[str, Dict[str, Any]] = {}
                if kwargs:
                    cfg = mesh_service.get_config()
                    updates["mesh"] = {"host": cfg.mesh_host, "port": str(cfg.mesh_port)}
                if sms_kwargs:
                    sms_updates: Dict[str, Any] = {}
                    if "enabled" in sms_kwargs:
                        sms_updates["enabled"] = "true" if sms_kwargs["enabled"] else "false"
                    if "api_url" in sms_kwargs:
                        sms_updates["api_url"] = sms_kwargs["api_url"]
                    if "api_key" in sms_kwargs:
                        sms_updates["api_key"] = sms_kwargs["api_key"]
                    if "phone" in sms_kwargs:
                        sms_updates["phone"] = sms_kwargs["phone"]
                    if "allow_from_ids" in sms_kwargs:
                        sms_updates["allow_from_ids"] = sms_kwargs["allow_from_ids"]
                    if "allow_types" in sms_kwargs:
                        sms_updates["allow_types"] = sms_kwargs["allow_types"]
                    updates["sms"] = sms_updates
                if relay_kwargs:
                    relay_updates: Dict[str, Any] = {}
                    if "enabled" in relay_kwargs:
                        relay_updates["enabled"] = "true" if relay_kwargs["enabled"] else "false"
                    if "listen_host" in relay_kwargs:
                        relay_updates["listen_host"] = relay_kwargs["listen_host"]
                    if "listen_port" in relay_kwargs:
                        relay_updates["listen_port"] = str(relay_kwargs["listen_port"])
                    updates["relay"] = relay_updates
                if stats_kwargs:
                    updates["stats"] = {"stats_cache_minutes": str(stats_kwargs["stats_cache_minutes"])}
                if updates:
                    update_config(resolve_config_path(config_path_raw), updates)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    return app
def _local_node_id(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    user = node.get("user")
    if isinstance(user, dict):
        val = user.get("id")
        if isinstance(val, str) and val:
            return val
    val = node.get("id")
    if isinstance(val, str) and val:
        return val
    num = node.get("num") or node.get("nodeNum")
    if isinstance(num, (int, float)) and num >= 0:
        try:
            return f"!{int(num):08x}"
        except Exception:
            return None
    return None
def main() -> None:
    http_port = _get_env_int("HTTP_PORT", 8080)
    app = create_app()
    app.run(host="0.0.0.0", port=http_port, debug=False, threaded=True)
if __name__ == "__main__":
    main()
