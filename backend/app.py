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
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from flask import Flask, Response, jsonify, request
from backend.jsonsafe import node_entry, now_epoch, radio_entry
from backend.mesh_service import MeshService
from backend.stats_db import StatsDB
def _get_env_int(name: str, default: int) -> int:
    return _parse_int(os.getenv(name), default)
def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default
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
    except Exception:
        return candidate


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
            nodes_refresh_sec=nodes_refresh_sec,
            max_messages=max_messages,
            stats_db=stats_db,
            mesh_http_port=_get_env_int("MESH_HTTP_PORT", 80),
            status_ttl_sec=_get_env_int("STATUS_TTL_SEC", 5),
        )
        mesh_service.start()
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
                return jsonify(stats_db.list_messages(limit=limit, offset=offset, order=order))
            except Exception:
                pass
        # Fallback to in-memory messages
        msgs = mesh_service.get_messages()
        if limit > 0:
            if order and str(order).lower() == "desc":
                msgs = list(reversed(msgs))
            msgs = msgs[: int(limit)]
        return jsonify(msgs)
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
            except Exception:
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
        except Exception:
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
        hours = _get_env_int("STATS_WINDOW_HOURS", 24)
        local_id = None
        getter = getattr(mesh_service, "get_radio_snapshot", None)
        if callable(getter):
            try:
                local_id = _local_node_id(getter())
            except Exception:
                local_id = None
        summary = stats_db.summary(hours=hours, local_node_id=local_id)
        cfg = mesh_service.get_config()
        configured = _is_configured(cfg)
        status_series = []
        status_latest = None
        try:
            status_series = stats_db.list_status_reports(limit=120, order="asc")
            if status_series:
                status_latest = status_series[-1]
        except Exception:
            status_series = []
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
                    "lastHour": summary.messages_last_hour,
                    "windowHours": summary.window_hours,
                    "window": summary.messages_window,
                    "hourlyWindow": summary.hourly_window,
                },
                "apps": {
                    "counts": summary.app_counts,
                    "requestsToMe": summary.app_requests_to_me,
                },
                "nodes": {
                    "topFrom": summary.top_from,
                    "topTo": summary.top_to,
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
            except Exception:
                return jsonify({"ok": False, "error": "channel must be an int"}), 400
            if channel_clean < 0:
                return jsonify({"ok": False, "error": "channel must be >= 0"}), 400
        try:
            mesh_service.send_text(text.strip(), to_clean, channel=channel_clean)
            if stats_db is not None:
                stats_db.record_send(ok=True)
            return jsonify({"ok": True})
        except Exception as e:
            if stats_db is not None:
                stats_db.record_send(ok=False, error=f"{type(e).__name__}: {e}")
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    @app.post("/api/config")
    def api_config():
        """
        Optional runtime reconfiguration. Useful for local UI settings without editing env vars.
        """
        body = request.get_json(silent=True) or {}
        mesh_host = body.get("meshHost")
        mesh_port = body.get("meshPort")
        kwargs: Dict[str, Any] = {}
        if mesh_host is not None:
            if not isinstance(mesh_host, str) or not mesh_host.strip():
                return jsonify({"ok": False, "error": "meshHost must be a non-empty string"}), 400
            kwargs["mesh_host"] = mesh_host.strip()
        if mesh_port is not None:
            try:
                kwargs["mesh_port"] = int(mesh_port)
            except Exception:
                return jsonify({"ok": False, "error": "meshPort must be an int"}), 400
        if not kwargs:
            return jsonify({"ok": False, "error": "no config fields provided"}), 400
        try:
            mesh_service.reconfigure(**kwargs)
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
