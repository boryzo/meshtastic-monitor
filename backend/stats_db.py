from __future__ import annotations
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from backend.jsonsafe import clamp_str, node_user_fields, now_epoch, quality_bucket
from backend.jsonsafe import _float_or_none as _to_float_or_none, _int_or_none as _to_int_or_none
from backend.stats_utils import (
    _app_name_from_message,
    _bool_from_int,
    _bool_to_int,
    _limit_clause,
    _order_dir,
    _to_str_or_none,
)
@dataclass(frozen=True)
class StatsSummary:
    db_path: str
    window_hours: int
    generated_at: int
    counters: Dict[str, int]
    messages_last_hour: int
    messages_window: int
    hourly_window: List[Dict[str, Any]]
    top_from: List[Dict[str, Any]]
    top_to: List[Dict[str, Any]]
    nodes_window_days: int
    nodes_history_interval_sec: int
    nodes_visible: List[Dict[str, Any]]
    nodes_zero_hops: List[Dict[str, Any]]
    nodes_snr_stats: List[Dict[str, Any]]
    nodes_flaky: List[Dict[str, Any]]
    recent_events: List[Dict[str, Any]]
    app_counts: List[Dict[str, Any]]
    app_requests_to_me: List[Dict[str, Any]]
    app_requesters: List[Dict[str, Any]]
class StatsDB:
    """Very small SQLite-backed stats store (thread-safe single connection)."""
    def __init__(
        self,
        path: str,
        *,
        nodes_history_interval_sec: int = 60,
        status_history_interval_sec: int = 60,
    ) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect(self.path)
        self._init_schema()
        self._nodes_history_interval_sec = max(0, int(nodes_history_interval_sec))
        self._last_nodes_history_ts: Optional[int] = None
        self._status_history_interval_sec = max(0, int(status_history_interval_sec))
        self._last_status_history_ts: Optional[int] = None
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
    def _fetchall(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()
    # ---- recorders
    def record_message(self, msg: Dict[str, Any]) -> None:
        ts = msg.get("rxTime")
        if not isinstance(ts, (int, float)) or ts <= 0:
            ts = now_epoch()
        ts = int(ts)
        from_id = msg.get("fromId")
        to_id = msg.get("toId")
        snr = msg.get("snr")
        rssi = msg.get("rssi")
        has_text = bool((msg.get("text") or "").strip()) if isinstance(msg.get("text"), str) else False
        has_payload = msg.get("payload_b64") is not None
        hour = ts - (ts % 3600)
        with self._lock, self._conn:  # transaction
            self._incr_counter("messages_total", 1)
            if has_text:
                self._incr_counter("messages_text", 1)
            if has_payload:
                self._incr_counter("messages_payload", 1)
            self._ensure_hour(hour)
            self._conn.execute(
                "UPDATE hourly_counts SET messages = messages + 1, with_text = with_text + ?, with_payload = with_payload + ? WHERE hour = ?",
                (1 if has_text else 0, 1 if has_payload else 0, hour),
            )
            if isinstance(from_id, str) and from_id:
                self._ensure_node(from_id)
                self._conn.execute(
                    "UPDATE node_counts SET from_count = from_count + 1, last_rx = ?, last_snr = ?, last_rssi = ? WHERE node_id = ?",
                    (ts, _to_float_or_none(snr), _to_float_or_none(rssi), from_id),
                )
            if isinstance(to_id, str) and to_id:
                self._ensure_node(to_id)
                self._conn.execute("UPDATE node_counts SET to_count = to_count + 1 WHERE node_id = ?", (to_id,))
            self._record_app_appearance(msg)
            self._record_app_request(msg)
            self._store_message(msg, ts)
    def list_messages(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        order: str = "asc",
        app: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit_sql, limit_params = _limit_clause(limit, offset)
        order_dir = _order_dir(order)
        where: List[str] = []
        params: List[Any] = []
        app_val = _to_str_or_none(app)
        if app_val:
            where.append("app = ?")
            params.append(app_val)
        where_sql = f"WHERE {' AND '.join(where)} " if where else ""
        sql = (
            "SELECT m.rx_time, m.from_id, m.to_id, m.snr, m.rssi, m.hop_limit, m.channel, m.portnum, "
            "m.app, m.request_id, m.want_response, m.text, m.payload_b64, m.error, "
            "nf.short AS from_short, nf.long AS from_long, nt.short AS to_short, nt.long AS to_long "
            "FROM messages m "
            "LEFT JOIN node_counts nf ON nf.node_id = m.from_id "
            "LEFT JOIN node_counts nt ON nt.node_id = m.to_id "
            f"{where_sql}ORDER BY m.rx_time {order_dir}, m.id {order_dir} {limit_sql}"
        )
        rows = self._fetchall(sql, tuple(params) + limit_params)
        return [{"rxTime": r["rx_time"], "fromId": r["from_id"], "toId": r["to_id"], "snr": r["snr"], "rssi": r["rssi"], "hopLimit": r["hop_limit"], "channel": r["channel"], "portnum": r["portnum"], "app": r["app"], "requestId": r["request_id"], "wantResponse": _bool_from_int(r["want_response"]), "text": r["text"], "payload_b64": r["payload_b64"], "error": r["error"], "fromShort": r["from_short"], "fromLong": r["from_long"], "toShort": r["to_short"], "toLong": r["to_long"]} for r in rows]
    def record_send(self, ok: bool, error: Optional[str] = None) -> None:
        with self._lock, self._conn:
            self._incr_counter("send_total", 1)
            self._incr_counter("send_ok" if ok else "send_error", 1)
            if not ok and error:
                self._add_event("send_error", clamp_str(error, 600))
    def record_mesh_event(self, event: str, detail: Optional[str] = None) -> None:
        event = (event or "").strip().lower()
        if not event:
            return
        detail_clean = clamp_str(detail, 600) if detail else None
        with self._lock, self._conn:
            if event == "connect":
                self._incr_counter("mesh_connect", 1)
            elif event == "disconnect":
                self._incr_counter("mesh_disconnect", 1)
            elif event == "error":
                self._incr_counter("mesh_error", 1)
            else:
                self._incr_counter(f"mesh_{event}", 1)
            self._add_event(f"mesh_{event}", detail_clean)
    def record_nodes_snapshot(self, nodes: Dict[str, Dict[str, Any]]) -> None:
        if not nodes:
            return
        snapshot_ts = now_epoch()
        should_store_history = True
        if self._nodes_history_interval_sec > 0:
            last_ts = self._last_nodes_history_ts
            if last_ts is not None and snapshot_ts - last_ts < self._nodes_history_interval_sec:
                should_store_history = False
        entries: List[tuple] = []
        history_entries: List[tuple] = []
        for node_id, node in nodes.items():
            node_id_str = str(node_id)
            if not node_id_str:
                continue
            fields = node_user_fields(node)
            short = fields["short"]
            long = fields["long"]
            role = fields["role"]
            hw_model = fields["hwModel"]
            firmware = None
            hops_away = _to_int_or_none(node.get("hopsAway")) if isinstance(node, dict) else None
            last_heard = node.get("lastHeard") if isinstance(node, dict) else None
            if isinstance(last_heard, (int, float)) and last_heard > 0:
                last_heard_int: Optional[int] = int(last_heard)
            else:
                last_heard_int = None
            snr = _to_float_or_none(node.get("snr") if isinstance(node, dict) else None)
            entries.append(
                (
                    node_id_str,
                    short,
                    long,
                    role,
                    hw_model,
                    firmware,
                    hops_away,
                    last_heard_int,
                    snr,
                )
            )
            if should_store_history:
                history_entries.append(
                    (
                        snapshot_ts,
                        node_id_str,
                        snr,
                        quality_bucket(snr),
                        hops_away,
                        last_heard_int,
                    )
                )
        if not entries:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO node_counts(node_id, from_count, to_count, last_rx, last_snr, last_rssi) VALUES(?, 0, 0, NULL, NULL, NULL)",
                [(e[0],) for e in entries],
            )
            self._conn.executemany(
                "UPDATE node_counts SET short = COALESCE(?, short), long = COALESCE(?, long), role = COALESCE(?, role), hw_model = COALESCE(?, hw_model), firmware = COALESCE(?, firmware), hops_away = COALESCE(?, hops_away), last_heard = COALESCE(?, last_heard), last_snr = COALESCE(last_snr, ?), last_rx = COALESCE(last_rx, ?) WHERE node_id = ?",
                [(short, long, role, hw_model, firmware, hops_away, last_heard, snr, last_heard, node_id) for (node_id, short, long, role, hw_model, firmware, hops_away, last_heard, snr) in entries],
            )
            if history_entries:
                self._conn.executemany(
                    "INSERT INTO node_history(ts, node_id, snr, quality, hops_away, last_heard) VALUES(?, ?, ?, ?, ?, ?)",
                    history_entries,
                )
                self._last_nodes_history_ts = snapshot_ts
    def record_status_report(self, report: Dict[str, Any]) -> None:
        if not isinstance(report, dict):
            return
        ts = now_epoch()
        if self._status_history_interval_sec > 0:
            last_ts = self._last_status_history_ts
            if last_ts is not None and ts - last_ts < self._status_history_interval_sec:
                return
        airtime = report.get("airtime") or {}
        power = report.get("power") or {}
        memory = report.get("memory") or {}
        wifi = report.get("wifi") or {}
        radio = report.get("radio") or {}
        device = report.get("device") or {}
        rx_log = airtime.get("rx_log") if isinstance(airtime, dict) else None
        tx_log = airtime.get("tx_log") if isinstance(airtime, dict) else None
        rx_all_log = airtime.get("rx_all_log") if isinstance(airtime, dict) else None
        def first_list(val: Any) -> Optional[int]:
            return _to_int_or_none(val[0]) if isinstance(val, (list, tuple)) and val else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO status_reports(ts, channel_utilization, utilization_tx, seconds_since_boot, rx_log, tx_log, rx_all_log, battery_percent, battery_voltage_mv, is_charging, has_usb, has_battery, heap_free, heap_total, fs_free, fs_total, wifi_rssi, wifi_ip, radio_frequency, lora_channel, reboot_counter) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    _to_float_or_none(airtime.get("channel_utilization")),
                    _to_float_or_none(airtime.get("utilization_tx")),
                    _to_int_or_none(airtime.get("seconds_since_boot")),
                    first_list(rx_log),
                    first_list(tx_log),
                    first_list(rx_all_log),
                    _to_float_or_none(power.get("battery_percent")),
                    _to_int_or_none(power.get("battery_voltage_mv")),
                    _bool_to_int(power.get("is_charging")),
                    _bool_to_int(power.get("has_usb")),
                    _bool_to_int(power.get("has_battery")),
                    _to_int_or_none(memory.get("heap_free")),
                    _to_int_or_none(memory.get("heap_total")),
                    _to_int_or_none(memory.get("fs_free")),
                    _to_int_or_none(memory.get("fs_total")),
                    _to_int_or_none(wifi.get("rssi")),
                    _to_str_or_none(wifi.get("ip")),
                    _to_float_or_none(radio.get("frequency")),
                    _to_int_or_none(radio.get("lora_channel")),
                    _to_int_or_none(device.get("reboot_counter")),
                ),
            )
            self._last_status_history_ts = ts
    def known_node_entries(self) -> List[Dict[str, Any]]:
        now = now_epoch()
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, short, long, role, hw_model, firmware, hops_away, last_heard, last_rx, last_snr FROM node_counts WHERE node_id LIKE '!%' AND (from_count > 0 OR to_count > 0 OR last_rx IS NOT NULL OR last_heard IS NOT NULL)"
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            last = r["last_heard"] if r["last_heard"] is not None else r["last_rx"]
            age = None
            if isinstance(last, (int, float)) and last > 0:
                age = max(0, now - int(last))
            snr = r["last_snr"]
            hops_away = r["hops_away"]
            out.append({"id": str(r["node_id"]), "short": r["short"], "long": r["long"], "role": r["role"], "hwModel": r["hw_model"], "firmware": r["firmware"], "snr": snr, "hopsAway": hops_away, "lastHeard": last, "ageSec": age, "quality": quality_bucket(snr)})
        return out
    def list_node_history(
        self,
        *,
        node_id: Optional[str] = None,
        limit: int = 500,
        since: Optional[int] = None,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        order_dir = _order_dir(order)
        params: List[Any] = []
        where: List[str] = []
        if isinstance(node_id, str) and node_id.strip():
            where.append("node_id = ?")
            params.append(node_id.strip())
        if since is not None:
            where.append("ts >= ?")
            params.append(int(since))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        limit_sql, limit_params = _limit_clause(limit)
        params.extend(limit_params)
        sql = (
            "SELECT ts, node_id, snr, quality, hops_away, last_heard "
            f"FROM node_history {where_sql} ORDER BY ts {order_dir}, id {order_dir} {limit_sql}"
        )
        rows = self._fetchall(sql, tuple(params))
        return [{"ts": int(r["ts"]), "id": str(r["node_id"]), "snr": r["snr"], "quality": r["quality"], "hopsAway": r["hops_away"], "lastHeard": r["last_heard"]} for r in rows]
    def list_status_reports(
        self,
        *,
        limit: int = 200,
        since: Optional[int] = None,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        order_dir = _order_dir(order)
        params: List[Any] = []
        where = []
        if since is not None:
            where.append("ts >= ?")
            params.append(int(since))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        limit_sql, limit_params = _limit_clause(limit)
        params.extend(limit_params)
        sql = (
            "SELECT ts, channel_utilization, utilization_tx, seconds_since_boot, rx_log, tx_log, rx_all_log, "
            "battery_percent, battery_voltage_mv, is_charging, has_usb, has_battery, heap_free, heap_total, "
            "fs_free, fs_total, wifi_rssi, wifi_ip, radio_frequency, lora_channel, reboot_counter "
            f"FROM status_reports {where_sql} ORDER BY ts {order_dir}, id {order_dir} {limit_sql}"
        )
        rows = self._fetchall(sql, tuple(params))
        return [{"ts": int(r["ts"]), "channelUtilization": r["channel_utilization"], "utilizationTx": r["utilization_tx"], "secondsSinceBoot": r["seconds_since_boot"], "rxLog": r["rx_log"], "txLog": r["tx_log"], "rxAllLog": r["rx_all_log"], "batteryPercent": r["battery_percent"], "batteryVoltageMv": r["battery_voltage_mv"], "isCharging": _bool_from_int(r["is_charging"]), "hasUsb": _bool_from_int(r["has_usb"]), "hasBattery": _bool_from_int(r["has_battery"]), "heapFree": r["heap_free"], "heapTotal": r["heap_total"], "fsFree": r["fs_free"], "fsTotal": r["fs_total"], "wifiRssi": r["wifi_rssi"], "wifiIp": r["wifi_ip"], "radioFrequency": r["radio_frequency"], "loraChannel": r["lora_channel"], "rebootCounter": r["reboot_counter"]} for r in rows]

    def get_message_window(self, *, hours: int = 24) -> Dict[str, Any]:
        hours = max(1, int(hours))
        now = now_epoch()
        since_window = now - hours * 3600
        since_1h = now - 3600
        with self._lock:
            hourly_window = self._get_hourly(since_window)
            hourly_1 = self._get_hourly(since_1h)
        messages_last_hour = sum(int(r.get("messages") or 0) for r in hourly_1)
        messages_window = sum(int(r.get("messages") or 0) for r in hourly_window)
        return {
            "windowHours": hours,
            "lastHour": messages_last_hour,
            "window": messages_window,
            "hourlyWindow": hourly_window,
        }
    def get_node_stats(self, node_id: str) -> Optional[Dict[str, Any]]:
        node_id = str(node_id or "").strip()
        if not node_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT node_id, short, long, role, hw_model, firmware, hops_away, last_heard, last_rx, last_snr, last_rssi, from_count, to_count FROM node_counts WHERE node_id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        now = now_epoch()
        last = row["last_heard"] if row["last_heard"] is not None else row["last_rx"]
        age = None
        if isinstance(last, (int, float)) and last > 0:
            age = max(0, now - int(last))
        snr = row["last_snr"]
        hops_away = row["hops_away"]
        return {"id": str(row["node_id"]), "short": row["short"], "long": row["long"], "role": row["role"], "hwModel": row["hw_model"], "firmware": row["firmware"], "snr": snr, "rssi": row["last_rssi"], "hopsAway": hops_away, "lastHeard": row["last_heard"], "lastRx": row["last_rx"], "ageSec": age, "quality": quality_bucket(snr), "fromCount": int(row["from_count"]), "toCount": int(row["to_count"])}
    # ---- readers
    def summary(
        self,
        *,
        hours: int = 24,
        top_limit: int = 8,
        event_limit: int = 12,
        local_node_id: Optional[str] = None,
        nodes_days: int = 7,
    ) -> StatsSummary:
        hours = max(1, int(hours))
        top_limit = max(1, int(top_limit))
        event_limit = max(1, int(event_limit))
        nodes_days = max(1, int(nodes_days))
        now = now_epoch()
        since_window = now - hours * 3600
        since_1h = now - 1 * 3600
        since_nodes = now - nodes_days * 86400
        window_seconds = max(0, now - since_nodes)
        with self._lock:
            counters = self._get_counters()
            hourly_window = self._get_hourly(since_window)
            hourly_1 = self._get_hourly(since_1h)
            top_from = self._get_top(kind="from", limit=top_limit)
            top_to = self._get_top(kind="to", limit=top_limit)
            nodes_visible = self._get_node_visibility(since_nodes, window_seconds, limit=top_limit)
            nodes_zero_hops = self._get_node_zero_hops(since_nodes, limit=top_limit)
            nodes_snr_stats = self._get_node_snr_stats(since_nodes, limit=top_limit)
            nodes_flaky = self._get_node_flaky(since_nodes, limit=top_limit)
            events = self._get_events(limit=event_limit)
            app_counts = self._get_app_counts()
            app_requests_to_me = self._get_app_requests_to_me(local_node_id)
            app_requesters = self._get_top_requesters(since_nodes, limit=top_limit, local_node_id=local_node_id)
        return StatsSummary(
            db_path=self.path,
            window_hours=hours,
            generated_at=now,
            counters=counters,
            messages_last_hour=sum(int(r.get("messages") or 0) for r in hourly_1),
            messages_window=sum(int(r.get("messages") or 0) for r in hourly_window),
            hourly_window=hourly_window,
            top_from=top_from,
            top_to=top_to,
            nodes_window_days=nodes_days,
            nodes_history_interval_sec=int(self._nodes_history_interval_sec),
            nodes_visible=nodes_visible,
            nodes_zero_hops=nodes_zero_hops,
            nodes_snr_stats=nodes_snr_stats,
            nodes_flaky=nodes_flaky,
            recent_events=events,
            app_counts=app_counts,
            app_requests_to_me=app_requests_to_me,
            app_requesters=app_requesters,
        )
    # ---- internals
    def _connect(self, path: str) -> sqlite3.Connection:
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        return conn
    def _init_schema(self) -> None:
        ddls = [
            "CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS hourly_counts (hour INTEGER PRIMARY KEY, messages INTEGER NOT NULL, with_text INTEGER NOT NULL, with_payload INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS node_counts (node_id TEXT PRIMARY KEY, from_count INTEGER NOT NULL, to_count INTEGER NOT NULL, last_rx INTEGER, last_snr REAL, last_rssi REAL)",
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, event TEXT NOT NULL, detail TEXT)",
            "CREATE TABLE IF NOT EXISTS app_counts (app TEXT PRIMARY KEY, total INTEGER NOT NULL, requests INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS app_requests (app TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, count INTEGER NOT NULL, last_ts INTEGER NOT NULL, PRIMARY KEY(app, from_id, to_id))",
            "CREATE TABLE IF NOT EXISTS node_history (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, node_id TEXT NOT NULL, snr REAL, quality TEXT, hops_away INTEGER, last_heard INTEGER)",
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, rx_time INTEGER NOT NULL, from_id TEXT, to_id TEXT, snr REAL, rssi REAL, hop_limit INTEGER, channel INTEGER, portnum INTEGER, app TEXT, request_id INTEGER, want_response INTEGER, text TEXT, payload_b64 TEXT, error TEXT)",
            "CREATE TABLE IF NOT EXISTS status_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, channel_utilization REAL, utilization_tx REAL, seconds_since_boot INTEGER, rx_log INTEGER, tx_log INTEGER, rx_all_log INTEGER, battery_percent REAL, battery_voltage_mv INTEGER, is_charging INTEGER, has_usb INTEGER, has_battery INTEGER, heap_free INTEGER, heap_total INTEGER, fs_free INTEGER, fs_total INTEGER, wifi_rssi INTEGER, wifi_ip TEXT, radio_frequency REAL, lora_channel INTEGER, reboot_counter INTEGER)",
        ]
        idx = [
            "CREATE INDEX IF NOT EXISTS idx_node_history_ts ON node_history(ts)",
            "CREATE INDEX IF NOT EXISTS idx_node_history_node_ts ON node_history(node_id, ts)",
            "CREATE INDEX IF NOT EXISTS idx_messages_rx_time ON messages(rx_time)",
            "CREATE INDEX IF NOT EXISTS idx_status_reports_ts ON status_reports(ts)",
        ]
        with self._lock, self._conn:
            for ddl in ddls:
                self._conn.execute(ddl)
            self._ensure_node_counts_columns()
            for ddl in idx:
                self._conn.execute(ddl)
    def _ensure_node_counts_columns(self) -> None:
        existing = {
            str(r["name"])
            for r in self._conn.execute("PRAGMA table_info(node_counts)").fetchall()
        }
        wanted: Dict[str, str] = {
            "short": "TEXT",
            "long": "TEXT",
            "role": "TEXT",
            "hw_model": "TEXT",
            "firmware": "TEXT",
            "hops_away": "INTEGER",
            "last_heard": "INTEGER",
        }
        # SQL reserved keywords to prevent conflicts
        sql_reserved = {
            "abort", "action", "add", "after", "all", "alter", "analyze", "and", "as",
            "asc", "attach", "autoincrement", "before", "begin", "between", "by", "cascade",
            "case", "cast", "check", "collate", "column", "commit", "conflict", "constraint",
            "create", "cross", "current", "current_date", "current_time", "current_timestamp",
            "database", "default", "deferrable", "deferred", "delete", "desc", "detach",
            "distinct", "do", "drop", "each", "else", "end", "escape", "except", "exclude",
            "exclusive", "exists", "explain", "fail", "filter", "first", "following", "for",
            "foreign", "from", "full", "glob", "group", "groups", "having", "if", "ignore",
            "immediate", "in", "index", "indexed", "initially", "inner", "insert", "instead",
            "intersect", "into", "is", "isnull", "join", "key", "last", "left", "like",
            "limit", "match", "natural", "no", "not", "nothing", "notnull", "null", "nulls",
            "of", "offset", "on", "or", "order", "others", "outer", "over", "partition",
            "plan", "pragma", "preceding", "primary", "query", "raise", "range", "recursive",
            "references", "regexp", "reindex", "release", "rename", "replace", "restrict",
            "returning", "right", "rollback", "row", "rows", "savepoint", "select", "set",
            "table", "temp", "temporary", "then", "ties", "to", "transaction", "trigger",
            "unbounded", "union", "unique", "update", "using", "vacuum", "values", "view",
            "virtual", "when", "where", "window", "with", "without",
        }
        # Whitelist validation to prevent SQL injection in ALTER TABLE statements
        allowed_types = {"TEXT", "INTEGER"}
        for col, ddl in wanted.items():
            if col in existing:
                continue
            # Validate column name structure
            if not col or not col.replace("_", "").isalnum():
                logger.warning("Skipping invalid column name: %s", col)
                continue
            # Prevent names starting/ending with underscore or having consecutive underscores
            if col.startswith("_") or col.endswith("_") or "__" in col:
                logger.warning("Skipping column name with invalid underscore placement: %s", col)
                continue
            # Check against SQL reserved keywords
            if col.lower() in sql_reserved:
                logger.warning("Skipping reserved SQL keyword as column name: %s", col)
                continue
            # Validate DDL type
            if ddl not in allowed_types:
                logger.warning("Skipping invalid DDL type for column %s: %s", col, ddl)
                continue
            # Safe to use f-string here since col and ddl are validated
            self._conn.execute(f"ALTER TABLE node_counts ADD COLUMN {col} {ddl}")
    def _incr_counter(self, key: str, delta: int) -> None:
        self._conn.execute("INSERT OR IGNORE INTO counters(key, value) VALUES(?, 0)", (key,))
        self._conn.execute("UPDATE counters SET value = value + ? WHERE key = ?", (int(delta), key))
    def _ensure_hour(self, hour: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO hourly_counts(hour, messages, with_text, with_payload) VALUES(?, 0, 0, 0)",
            (int(hour),),
        )
    def _ensure_node(self, node_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO node_counts(node_id, from_count, to_count, last_rx, last_snr, last_rssi) VALUES(?, 0, 0, NULL, NULL, NULL)",
            (node_id,),
        )
    def _store_message(self, msg: Dict[str, Any], ts: int) -> None:
        try:
            self._conn.execute(
                "INSERT INTO messages(rx_time, from_id, to_id, snr, rssi, hop_limit, channel, portnum, app, request_id, want_response, text, payload_b64, error) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(ts),
                    _to_str_or_none(msg.get("fromId")),
                    _to_str_or_none(msg.get("toId")),
                    _to_float_or_none(msg.get("snr")),
                    _to_float_or_none(msg.get("rssi")),
                    _to_int_or_none(msg.get("hopLimit")),
                    _to_int_or_none(msg.get("channel")),
                    _to_int_or_none(msg.get("portnum")),
                    _to_str_or_none(msg.get("app")),
                    _to_int_or_none(msg.get("requestId")),
                    _bool_to_int(msg.get("wantResponse")),
                    clamp_str(msg.get("text"), 4000),
                    _to_str_or_none(msg.get("payload_b64")),
                    clamp_str(msg.get("error"), 600),
                ),
            )
        except Exception:
            pass
    def _add_event(self, event: str, detail: Optional[str]) -> None:
        self._conn.execute("INSERT INTO events(ts, event, detail) VALUES(?, ?, ?)", (now_epoch(), event, detail))
    def _record_app_appearance(self, msg: Dict[str, Any]) -> None:
        app = _app_name_from_message(msg)
        if not app:
            return
        has_request_id = msg.get("requestId") is not None
        wants_response = msg.get("wantResponse") is True
        is_request = has_request_id or wants_response
        self._conn.execute(
            "INSERT OR IGNORE INTO app_counts(app, total, requests) VALUES(?, 0, 0)", (app,)
        )
        self._conn.execute(
            "UPDATE app_counts SET total = total + 1, requests = requests + ? WHERE app = ?",
            (1 if is_request else 0, app),
        )
    def _record_app_request(self, msg: Dict[str, Any]) -> None:
        app = _app_name_from_message(msg)
        if not app:
            return
        has_request_id = msg.get("requestId") is not None
        wants_response = msg.get("wantResponse") is True
        if not (has_request_id or wants_response):
            return
        from_id = msg.get("fromId")
        if not isinstance(from_id, str) or not from_id:
            return
        to_id = msg.get("toId")
        to_id_norm = str(to_id).strip() if isinstance(to_id, str) and to_id else "^all"
        ts = msg.get("rxTime")
        if not isinstance(ts, (int, float)) or ts <= 0:
            ts = now_epoch()
        self._conn.execute(
            "INSERT OR IGNORE INTO app_requests(app, from_id, to_id, count, last_ts) VALUES(?, ?, ?, 0, ?)",
            (app, from_id, to_id_norm, int(ts)),
        )
        self._conn.execute(
            "UPDATE app_requests SET count = count + 1, last_ts = ? WHERE app = ? AND from_id = ? AND to_id = ?",
            (int(ts), app, from_id, to_id_norm),
        )
    def _get_counters(self) -> Dict[str, int]:
        rows = self._conn.execute("SELECT key, value FROM counters").fetchall()
        return {str(r["key"]): int(r["value"]) for r in rows}
    def _get_hourly(self, since_epoch: int) -> List[Dict[str, Any]]:
        since_hour = int(since_epoch) - (int(since_epoch) % 3600)
        rows = self._conn.execute(
            "SELECT hour, messages, with_text, with_payload FROM hourly_counts WHERE hour >= ? ORDER BY hour ASC",
            (since_hour,),
        ).fetchall()
        return [{"hour": int(r["hour"]), "messages": int(r["messages"]), "with_text": int(r["with_text"]), "with_payload": int(r["with_payload"])} for r in rows]
    def _get_top(self, *, kind: str, limit: int) -> List[Dict[str, Any]]:
        if kind == "from":
            order_col = "from_count"
        else:
            order_col = "to_count"
        rows = self._conn.execute(
            f"SELECT node_id, short, long, from_count, to_count, last_rx, last_snr, last_rssi FROM node_counts ORDER BY {order_col} DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            count = int(r[order_col])
            if count <= 0:
                continue
            out.append({"id": str(r["node_id"]), "short": r["short"], "long": r["long"], "count": count, "lastRx": r["last_rx"], "lastSnr": r["last_snr"], "lastRssi": r["last_rssi"]})
        return out
    def _expected_snapshots(self, window_seconds: int) -> Optional[int]:
        interval = int(self._nodes_history_interval_sec)
        if interval <= 0:
            return None
        return max(1, int(window_seconds / interval))
    def _availability_pct(self, count: int, expected: Optional[int]) -> Optional[float]:
        if expected is None or expected <= 0:
            return None
        pct = (float(count) / float(expected)) * 100.0
        return min(100.0, round(pct, 1))
    def _node_history_seconds(self, count: int) -> Optional[int]:
        interval = int(self._nodes_history_interval_sec)
        if interval <= 0:
            return None
        return int(count) * interval
    def _get_node_visibility(self, since_epoch: int, window_seconds: int, *, limit: int) -> List[Dict[str, Any]]:
        expected = self._expected_snapshots(window_seconds)
        rows = self._conn.execute(
            "SELECT nh.node_id, COUNT(*) AS cnt, nc.short, nc.long "
            "FROM node_history nh "
            "LEFT JOIN node_counts nc ON nc.node_id = nh.node_id "
            "WHERE nh.ts >= ? "
            "GROUP BY nh.node_id "
            "ORDER BY cnt DESC "
            "LIMIT ?",
            (int(since_epoch), int(limit)),
        ).fetchall()
        out = []
        for r in rows:
            count = int(r["cnt"])
            if count <= 0:
                continue
            out.append(
                {
                    "id": str(r["node_id"]),
                    "short": r["short"],
                    "long": r["long"],
                    "snapshots": count,
                    "seconds": self._node_history_seconds(count),
                    "expectedSnapshots": expected,
                    "availabilityPct": self._availability_pct(count, expected),
                }
            )
        return out
    def _get_node_zero_hops(self, since_epoch: int, *, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT nh.node_id, COUNT(*) AS cnt, nc.short, nc.long "
            "FROM node_history nh "
            "LEFT JOIN node_counts nc ON nc.node_id = nh.node_id "
            "WHERE nh.ts >= ? AND nh.hops_away = 0 "
            "GROUP BY nh.node_id "
            "ORDER BY cnt DESC "
            "LIMIT ?",
            (int(since_epoch), int(limit)),
        ).fetchall()
        out = []
        for r in rows:
            count = int(r["cnt"])
            if count <= 0:
                continue
            out.append(
                {
                    "id": str(r["node_id"]),
                    "short": r["short"],
                    "long": r["long"],
                    "snapshots": count,
                    "seconds": self._node_history_seconds(count),
                }
            )
        return out
    def _get_node_snr_stats(self, since_epoch: int, *, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT nh.node_id, MIN(nh.snr) AS min_snr, AVG(nh.snr) AS avg_snr, "
            "MAX(nh.snr) AS max_snr, COUNT(nh.snr) AS samples, nc.short, nc.long "
            "FROM node_history nh "
            "LEFT JOIN node_counts nc ON nc.node_id = nh.node_id "
            "WHERE nh.ts >= ? AND nh.snr IS NOT NULL "
            "GROUP BY nh.node_id "
            "ORDER BY samples DESC, avg_snr DESC "
            "LIMIT ?",
            (int(since_epoch), int(limit)),
        ).fetchall()
        return [
            {
                "id": str(r["node_id"]),
                "short": r["short"],
                "long": r["long"],
                "minSnr": r["min_snr"],
                "avgSnr": r["avg_snr"],
                "maxSnr": r["max_snr"],
                "samples": int(r["samples"]),
            }
            for r in rows
            if r["samples"] is not None and int(r["samples"]) > 0
        ]
    def _get_node_flaky(self, since_epoch: int, *, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT node_id, hops_away FROM node_history WHERE ts >= ? ORDER BY node_id, ts, id",
            (int(since_epoch),),
        ).fetchall()
        changes: Dict[str, int] = {}
        prev: Dict[str, Optional[int]] = {}
        for r in rows:
            node_id = str(r["node_id"])
            hops = r["hops_away"]
            if hops is None:
                continue
            try:
                hops_val = int(hops)
            except Exception:
                continue
            last = prev.get(node_id)
            if last is not None and hops_val != last:
                changes[node_id] = changes.get(node_id, 0) + 1
            prev[node_id] = hops_val
        if not changes:
            return []
        ids = list(changes.keys())
        placeholders = ",".join("?" for _ in ids)
        names: Dict[str, Dict[str, Any]] = {}
        for r in self._conn.execute(
            f"SELECT node_id, short, long FROM node_counts WHERE node_id IN ({placeholders})",
            tuple(ids),
        ).fetchall():
            names[str(r["node_id"])] = {"short": r["short"], "long": r["long"]}
        out = []
        for node_id, count in sorted(changes.items(), key=lambda kv: kv[1], reverse=True)[: int(limit)]:
            info = names.get(node_id, {})
            out.append(
                {
                    "id": node_id,
                    "short": info.get("short"),
                    "long": info.get("long"),
                    "hopChanges": int(count),
                }
            )
        return out
    def _get_events(self, *, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, event, detail FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        # return oldest->newest for easier reading
        rows = list(reversed(rows))
        return [{"ts": int(r["ts"]), "event": str(r["event"]), "detail": r["detail"]} for r in rows]
    def _get_app_counts(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT app, total, requests FROM app_counts ORDER BY total DESC"
        ).fetchall()
        return [{"app": str(r["app"]), "total": int(r["total"]), "requests": int(r["requests"])} for r in rows]
    def _get_app_requests_to_me(self, local_node_id: Optional[str]) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where = "to_id = '^all'"
        if isinstance(local_node_id, str) and local_node_id.strip():
            where = "to_id IN (?, '^all')"
            params.append(local_node_id.strip())
        rows = self._conn.execute(
            f"SELECT app, from_id, to_id, count, last_ts FROM app_requests WHERE {where} ORDER BY count DESC, last_ts DESC",
            tuple(params),
        ).fetchall()
        out = []
        for r in rows:
            from_id = str(r["from_id"])
            if isinstance(local_node_id, str) and local_node_id.strip() and from_id == local_node_id:
                continue
            out.append({"app": str(r["app"]), "fromId": from_id, "toId": str(r["to_id"]), "count": int(r["count"]), "lastTs": int(r["last_ts"])})
        return out
    def _get_top_requesters(
        self, since_epoch: int, *, limit: int, local_node_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [int(since_epoch)]
        where = "m.rx_time >= ? AND m.from_id IS NOT NULL AND (m.request_id IS NOT NULL OR m.want_response = 1)"
        if isinstance(local_node_id, str) and local_node_id.strip():
            where += " AND m.from_id != ?"
            params.append(local_node_id.strip())
        rows = self._conn.execute(
            "SELECT m.from_id, COUNT(*) AS cnt, MAX(m.rx_time) AS last_ts, nc.short, nc.long "
            "FROM messages m "
            "LEFT JOIN node_counts nc ON nc.node_id = m.from_id "
            f"WHERE {where} "
            "GROUP BY m.from_id "
            "ORDER BY cnt DESC, last_ts DESC "
            "LIMIT ?",
            tuple(params + [int(limit)]),
        ).fetchall()
        return [
            {
                "id": str(r["from_id"]),
                "short": r["short"],
                "long": r["long"],
                "count": int(r["cnt"]),
                "lastTs": int(r["last_ts"]) if r["last_ts"] is not None else None,
            }
            for r in rows
            if r["cnt"] is not None and int(r["cnt"]) > 0
        ]
