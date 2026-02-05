from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.jsonsafe import clamp_str, now_epoch, quality_bucket


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
    recent_events: List[Dict[str, Any]]
    app_counts: List[Dict[str, Any]]
    app_requests_to_me: List[Dict[str, Any]]


class StatsDB:
    """
    Very small SQLite-backed stats store.

    Designed to be safe to call from multiple threads (single connection + lock).
    """

    def __init__(self, path: str, *, nodes_history_interval_sec: int = 60) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect(self.path)
        self._init_schema()
        self._nodes_history_interval_sec = max(0, int(nodes_history_interval_sec))
        self._last_nodes_history_ts: Optional[int] = None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

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
                """
                UPDATE hourly_counts
                SET
                  messages = messages + 1,
                  with_text = with_text + ?,
                  with_payload = with_payload + ?
                WHERE hour = ?
                """,
                (1 if has_text else 0, 1 if has_payload else 0, hour),
            )

            if isinstance(from_id, str) and from_id:
                self._ensure_node(from_id)
                self._conn.execute(
                    """
                    UPDATE node_counts
                    SET
                      from_count = from_count + 1,
                      last_rx = ?,
                      last_snr = ?,
                      last_rssi = ?
                    WHERE node_id = ?
                    """,
                    (ts, _to_float_or_none(snr), _to_float_or_none(rssi), from_id),
                )

            if isinstance(to_id, str) and to_id:
                self._ensure_node(to_id)
                self._conn.execute(
                    """
                    UPDATE node_counts
                    SET
                      to_count = to_count + 1
                    WHERE node_id = ?
                    """,
                    (to_id,),
                )

            self._record_app_appearance(msg)
            self._record_app_request(msg)
            self._store_message(msg, ts)

    def list_messages(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        order: str = "asc",
    ) -> List[Dict[str, Any]]:
        limit = int(limit)
        offset = max(0, int(offset))
        order_dir = "DESC" if str(order).lower() == "desc" else "ASC"

        with self._lock:
            if limit <= 0:
                rows = self._conn.execute(
                    f"""
                    SELECT rx_time, from_id, to_id, snr, rssi, hop_limit, channel, portnum,
                           app, request_id, want_response, text, payload_b64, error
                    FROM messages
                    ORDER BY rx_time {order_dir}, id {order_dir}
                    """,
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"""
                    SELECT rx_time, from_id, to_id, snr, rssi, hop_limit, channel, portnum,
                           app, request_id, want_response, text, payload_b64, error
                    FROM messages
                    ORDER BY rx_time {order_dir}, id {order_dir}
                    LIMIT ? OFFSET ?
                    """,
                    (int(limit), int(offset)),
                ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "rxTime": r["rx_time"],
                    "fromId": r["from_id"],
                    "toId": r["to_id"],
                    "snr": r["snr"],
                    "rssi": r["rssi"],
                    "hopLimit": r["hop_limit"],
                    "channel": r["channel"],
                    "portnum": r["portnum"],
                    "app": r["app"],
                    "requestId": r["request_id"],
                    "wantResponse": _bool_from_int(r["want_response"]),
                    "text": r["text"],
                    "payload_b64": r["payload_b64"],
                    "error": r["error"],
                }
            )
        return out

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
        entries: List[
            tuple[
                str,
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[int],
                Optional[int],
                Optional[float],
            ]
        ] = []
        history_entries: List[
            tuple[
                int,
                str,
                Optional[float],
                Optional[str],
                Optional[int],
                Optional[int],
            ]
        ] = []
        for node_id, node in nodes.items():
            node_id_str = str(node_id)
            if not node_id_str:
                continue
            user = (node.get("user") or {}) if isinstance(node, dict) else {}
            short = clamp_str(user.get("shortName") or user.get("short_name"), 40)
            long = clamp_str(user.get("longName") or user.get("long_name"), 80)
            role = clamp_str(_role_str(user.get("role")), 40) if isinstance(user, dict) else None
            hw_model = clamp_str(user.get("hwModel") or user.get("hw_model") or user.get("hwmodel"), 40)
            firmware = clamp_str(_firmware_from_node(node), 80)

            hops_away = _to_int_or_none(node.get("hopsAway")) if isinstance(node, dict) else None
            if hops_away is None and isinstance(node, dict):
                hops_away = _to_int_or_none(node.get("hops_away"))

            last_heard = node.get("lastHeard") if isinstance(node, dict) else None
            if last_heard is None and isinstance(node, dict):
                last_heard = node.get("last_heard")
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
                """
                INSERT OR IGNORE INTO node_counts(node_id, from_count, to_count, last_rx, last_snr, last_rssi)
                VALUES(?, 0, 0, NULL, NULL, NULL)
                """,
                [(e[0],) for e in entries],
            )
            self._conn.executemany(
                """
                UPDATE node_counts
                SET
                  short = COALESCE(?, short),
                  long = COALESCE(?, long),
                  role = COALESCE(?, role),
                  hw_model = COALESCE(?, hw_model),
                  firmware = COALESCE(?, firmware),
                  hops_away = COALESCE(?, hops_away),
                  last_heard = COALESCE(?, last_heard),
                  last_snr = COALESCE(last_snr, ?),
                  last_rx = COALESCE(last_rx, ?)
                WHERE node_id = ?
                """,
                [
                    (
                        short,
                        long,
                        role,
                        hw_model,
                        firmware,
                        hops_away,
                        last_heard,
                        snr,
                        last_heard,
                        node_id,
                    )
                    for (
                        node_id,
                        short,
                        long,
                        role,
                        hw_model,
                        firmware,
                        hops_away,
                        last_heard,
                        snr,
                    ) in entries
                ],
            )
            if history_entries:
                self._conn.executemany(
                    """
                    INSERT INTO node_history(
                      ts, node_id, snr, quality, hops_away, last_heard
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    history_entries,
                )
                self._last_nodes_history_ts = snapshot_ts

    def known_node_entries(self) -> List[Dict[str, Any]]:
        now = now_epoch()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                  node_id,
                  short,
                  long,
                  role,
                  hw_model,
                  firmware,
                  hops_away,
                  last_heard,
                  last_rx,
                  last_snr
                FROM node_counts
                WHERE
                  node_id LIKE '!%'
                  AND (from_count > 0 OR to_count > 0 OR last_rx IS NOT NULL OR last_heard IS NOT NULL)
                """
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            last = r["last_heard"] if r["last_heard"] is not None else r["last_rx"]
            age = None
            if isinstance(last, (int, float)) and last > 0:
                age = max(0, now - int(last))

            snr = r["last_snr"]
            hops_away = r["hops_away"]
            out.append(
                {
                    "id": str(r["node_id"]),
                    "short": r["short"],
                    "long": r["long"],
                    "role": r["role"],
                    "hwModel": r["hw_model"],
                    "firmware": r["firmware"],
                    "snr": snr,
                    "hopsAway": hops_away,
                    "lastHeard": last,
                    "ageSec": age,
                    "quality": quality_bucket(snr),
                }
            )

        return out

    def list_node_history(
        self,
        *,
        node_id: Optional[str] = None,
        limit: int = 500,
        since: Optional[int] = None,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        limit = int(limit)
        order_dir = "DESC" if str(order).lower() == "desc" else "ASC"
        params: List[Any] = []
        where: List[str] = []

        if isinstance(node_id, str) and node_id.strip():
            where.append("node_id = ?")
            params.append(node_id.strip())
        if since is not None:
            where.append("ts >= ?")
            params.append(int(since))

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        limit_sql = "" if limit <= 0 else "LIMIT ?"
        if limit > 0:
            params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT ts, node_id, snr, quality, hops_away, last_heard
                FROM node_history
                {where_sql}
                ORDER BY ts {order_dir}, id {order_dir}
                {limit_sql}
                """,
                tuple(params),
            ).fetchall()

        return [
            {
                "ts": int(r["ts"]),
                "id": str(r["node_id"]),
                "snr": r["snr"],
                "quality": r["quality"],
                "hopsAway": r["hops_away"],
                "lastHeard": r["last_heard"],
            }
            for r in rows
        ]

    def get_node_stats(self, node_id: str) -> Optional[Dict[str, Any]]:
        node_id = str(node_id or "").strip()
        if not node_id:
            return None

        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                  node_id,
                  short,
                  long,
                  role,
                  hw_model,
                  firmware,
                  hops_away,
                  last_heard,
                  last_rx,
                  last_snr,
                  last_rssi,
                  from_count,
                  to_count
                FROM node_counts
                WHERE node_id = ?
                """,
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

        return {
            "id": str(row["node_id"]),
            "short": row["short"],
            "long": row["long"],
            "role": row["role"],
            "hwModel": row["hw_model"],
            "firmware": row["firmware"],
            "snr": snr,
            "rssi": row["last_rssi"],
            "hopsAway": hops_away,
            "lastHeard": row["last_heard"],
            "lastRx": row["last_rx"],
            "ageSec": age,
            "quality": quality_bucket(snr),
            "fromCount": int(row["from_count"]),
            "toCount": int(row["to_count"]),
        }

    # ---- readers
    def summary(
        self,
        *,
        hours: int = 24,
        top_limit: int = 8,
        event_limit: int = 12,
        local_node_id: Optional[str] = None,
    ) -> StatsSummary:
        hours = max(1, int(hours))
        top_limit = max(1, int(top_limit))
        event_limit = max(1, int(event_limit))

        now = now_epoch()
        since_window = now - hours * 3600
        since_1h = now - 1 * 3600

        with self._lock:
            counters = self._get_counters()
            hourly_window = self._get_hourly(since_window)
            hourly_1 = self._get_hourly(since_1h)
            top_from = self._get_top(kind="from", limit=top_limit)
            top_to = self._get_top(kind="to", limit=top_limit)
            events = self._get_events(limit=event_limit)
            app_counts = self._get_app_counts()
            app_requests_to_me = self._get_app_requests_to_me(local_node_id)

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
            recent_events=events,
            app_counts=app_counts,
            app_requests_to_me=app_requests_to_me,
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
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                  key TEXT PRIMARY KEY,
                  value INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hourly_counts (
                  hour INTEGER PRIMARY KEY,
                  messages INTEGER NOT NULL,
                  with_text INTEGER NOT NULL,
                  with_payload INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS node_counts (
                  node_id TEXT PRIMARY KEY,
                  from_count INTEGER NOT NULL,
                  to_count INTEGER NOT NULL,
                  last_rx INTEGER,
                  last_snr REAL,
                  last_rssi REAL
                )
                """
            )
            self._ensure_node_counts_columns()
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL,
                  event TEXT NOT NULL,
                  detail TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_counts (
                  app TEXT PRIMARY KEY,
                  total INTEGER NOT NULL,
                  requests INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_requests (
                  app TEXT NOT NULL,
                  from_id TEXT NOT NULL,
                  to_id TEXT NOT NULL,
                  count INTEGER NOT NULL,
                  last_ts INTEGER NOT NULL,
                  PRIMARY KEY(app, from_id, to_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS node_history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts INTEGER NOT NULL,
                  node_id TEXT NOT NULL,
                  snr REAL,
                  quality TEXT,
                  hops_away INTEGER,
                  last_heard INTEGER
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_history_ts ON node_history(ts)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_history_node_ts ON node_history(node_id, ts)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  rx_time INTEGER NOT NULL,
                  from_id TEXT,
                  to_id TEXT,
                  snr REAL,
                  rssi REAL,
                  hop_limit INTEGER,
                  channel INTEGER,
                  portnum INTEGER,
                  app TEXT,
                  request_id INTEGER,
                  want_response INTEGER,
                  text TEXT,
                  payload_b64 TEXT,
                  error TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_rx_time ON messages(rx_time)"
            )

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

        for col, ddl in wanted.items():
            if col in existing:
                continue
            self._conn.execute(f"ALTER TABLE node_counts ADD COLUMN {col} {ddl}")

    def _incr_counter(self, key: str, delta: int) -> None:
        self._conn.execute("INSERT OR IGNORE INTO counters(key, value) VALUES(?, 0)", (key,))
        self._conn.execute("UPDATE counters SET value = value + ? WHERE key = ?", (int(delta), key))

    def _ensure_hour(self, hour: int) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO hourly_counts(hour, messages, with_text, with_payload)
            VALUES(?, 0, 0, 0)
            """,
            (int(hour),),
        )

    def _ensure_node(self, node_id: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO node_counts(node_id, from_count, to_count, last_rx, last_snr, last_rssi)
            VALUES(?, 0, 0, NULL, NULL, NULL)
            """,
            (node_id,),
        )

    def _store_message(self, msg: Dict[str, Any], ts: int) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO messages(
                  rx_time, from_id, to_id, snr, rssi, hop_limit, channel, portnum,
                  app, request_id, want_response, text, payload_b64, error
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
        self._conn.execute(
            "INSERT INTO events(ts, event, detail) VALUES(?, ?, ?)",
            (now_epoch(), event, detail),
        )

    def _record_app_appearance(self, msg: Dict[str, Any]) -> None:
        app = _app_name_from_message(msg)
        if not app:
            return
        has_request_id = msg.get("requestId") is not None
        wants_response = msg.get("wantResponse") is True
        is_request = has_request_id or wants_response

        self._conn.execute(
            "INSERT OR IGNORE INTO app_counts(app, total, requests) VALUES(?, 0, 0)",
            (app,),
        )
        self._conn.execute(
            """
            UPDATE app_counts
            SET
              total = total + 1,
              requests = requests + ?
            WHERE app = ?
            """,
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
            """
            UPDATE app_requests
            SET
              count = count + 1,
              last_ts = ?
            WHERE app = ? AND from_id = ? AND to_id = ?
            """,
            (int(ts), app, from_id, to_id_norm),
        )

    def _get_counters(self) -> Dict[str, int]:
        rows = self._conn.execute("SELECT key, value FROM counters").fetchall()
        return {str(r["key"]): int(r["value"]) for r in rows}

    def _get_hourly(self, since_epoch: int) -> List[Dict[str, Any]]:
        since_hour = int(since_epoch) - (int(since_epoch) % 3600)
        rows = self._conn.execute(
            """
            SELECT hour, messages, with_text, with_payload
            FROM hourly_counts
            WHERE hour >= ?
            ORDER BY hour ASC
            """,
            (since_hour,),
        ).fetchall()
        return [
            {
                "hour": int(r["hour"]),
                "messages": int(r["messages"]),
                "with_text": int(r["with_text"]),
                "with_payload": int(r["with_payload"]),
            }
            for r in rows
        ]

    def _get_top(self, *, kind: str, limit: int) -> List[Dict[str, Any]]:
        if kind == "from":
            order_col = "from_count"
        else:
            order_col = "to_count"
        rows = self._conn.execute(
            f"""
            SELECT node_id, from_count, to_count, last_rx, last_snr, last_rssi
            FROM node_counts
            ORDER BY {order_col} DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            count = int(r[order_col])
            if count <= 0:
                continue
            out.append(
                {
                    "id": str(r["node_id"]),
                    "count": count,
                    "lastRx": r["last_rx"],
                    "lastSnr": r["last_snr"],
                    "lastRssi": r["last_rssi"],
                }
            )
        return out

    def _get_events(self, *, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, event, detail FROM events ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        # return oldest->newest for easier reading
        rows = list(reversed(rows))
        return [{"ts": int(r["ts"]), "event": str(r["event"]), "detail": r["detail"]} for r in rows]

    def _get_app_counts(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT app, total, requests FROM app_counts ORDER BY total DESC"
        ).fetchall()
        return [
            {
                "app": str(r["app"]),
                "total": int(r["total"]),
                "requests": int(r["requests"]),
            }
            for r in rows
        ]

    def _get_app_requests_to_me(self, local_node_id: Optional[str]) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where = "to_id = '^all'"
        if isinstance(local_node_id, str) and local_node_id.strip():
            where = "to_id IN (?, '^all')"
            params.append(local_node_id.strip())

        rows = self._conn.execute(
            f"""
            SELECT app, from_id, to_id, count, last_ts
            FROM app_requests
            WHERE {where}
            ORDER BY count DESC, last_ts DESC
            """,
            tuple(params),
        ).fetchall()

        out = []
        for r in rows:
            from_id = str(r["from_id"])
            if isinstance(local_node_id, str) and local_node_id.strip() and from_id == local_node_id:
                continue
            out.append(
                {
                    "app": str(r["app"]),
                    "fromId": from_id,
                    "toId": str(r["to_id"]),
                    "count": int(r["count"]),
                    "lastTs": int(r["last_ts"]),
                }
            )
        return out


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _role_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    try:
        return str(value)
    except Exception:
        return None


def _firmware_from_node(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    paths = [
        "firmwareVersion",
        "firmware_version",
        "firmware",
        "user.firmwareVersion",
        "user.firmware_version",
        "user.firmware",
        "metadata.firmwareVersion",
        "metadata.firmware_version",
        "deviceMetadata.firmwareVersion",
        "deviceMetadata.firmware_version",
        "device_metadata.firmwareVersion",
        "device_metadata.firmware_version",
    ]
    for path in paths:
        cur = node
        ok = True
        for part in path.split("."):
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(part)
        if ok:
            normalized = _normalize_firmware_value(cur)
            if normalized:
                return normalized
    return None


def _to_str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        out = value.strip()
        return out or None
    try:
        out = str(value).strip()
        return out or None
    except Exception:
        return None


def _bool_to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        try:
            return 1 if int(value) != 0 else 0
        except Exception:
            return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return 1
        if v in {"0", "false", "no", "n", "off"}:
            return 0
    return None


def _bool_from_int(value: Any) -> Optional[bool]:
    if value is None:
        return None
    try:
        return bool(int(value))
    except Exception:
        return None


def _normalize_firmware_value(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        out = val.strip()
        return out or None
    return None


def _app_name_from_message(msg: Dict[str, Any]) -> Optional[str]:
    app = msg.get("app")
    if isinstance(app, str) and app:
        return app
    portnum = msg.get("portnum")
    try:
        val = int(portnum)
    except Exception:
        return None
    if val == 3:
        return "POSITION_APP"
    if val == 4:
        return "NODEINFO_APP"
    if val == 5:
        return "ROUTING_APP"
    if val == 0x43:
        return "TELEMETRY_APP"
    return None
