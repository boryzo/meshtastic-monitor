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


class StatsDB:
    """
    Very small SQLite-backed stats store.

    Designed to be safe to call from multiple threads (single connection + lock).
    """

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect(self.path)
        self._init_schema()

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

        entries: List[tuple[str, Optional[str], Optional[str], Optional[int], Optional[int], Optional[float]]] = []
        for node_id, node in nodes.items():
            node_id_str = str(node_id)
            if not node_id_str:
                continue
            user = (node.get("user") or {}) if isinstance(node, dict) else {}
            short = clamp_str(user.get("shortName"), 40)
            long = clamp_str(user.get("longName"), 80)

            hops_away = _to_int_or_none(node.get("hopsAway")) if isinstance(node, dict) else None

            last_heard = node.get("lastHeard") if isinstance(node, dict) else None
            if isinstance(last_heard, (int, float)) and last_heard > 0:
                last_heard_int: Optional[int] = int(last_heard)
            else:
                last_heard_int = None

            snr = _to_float_or_none(node.get("snr") if isinstance(node, dict) else None)
            entries.append((node_id_str, short, long, hops_away, last_heard_int, snr))

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
                        hops_away,
                        last_heard,
                        snr,
                        last_heard,
                        node_id,
                    )
                    for (node_id, short, long, hops_away, last_heard, snr) in entries
                ],
            )

    def known_node_entries(self) -> List[Dict[str, Any]]:
        now = now_epoch()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                  node_id,
                  short,
                  long,
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
            out.append(
                {
                    "id": str(r["node_id"]),
                    "short": r["short"],
                    "long": r["long"],
                    "snr": snr,
                    "hopsAway": r["hops_away"],
                    "lastHeard": last,
                    "ageSec": age,
                    "quality": quality_bucket(snr),
                }
            )

        return out

    # ---- readers
    def summary(self, *, hours: int = 24, top_limit: int = 8, event_limit: int = 12) -> StatsSummary:
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

    def _ensure_node_counts_columns(self) -> None:
        existing = {
            str(r["name"])
            for r in self._conn.execute("PRAGMA table_info(node_counts)").fetchall()
        }

        wanted: Dict[str, str] = {
            "short": "TEXT",
            "long": "TEXT",
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

    def _add_event(self, event: str, detail: Optional[str]) -> None:
        self._conn.execute(
            "INSERT INTO events(ts, event, detail) VALUES(?, ?, ?)",
            (now_epoch(), event, detail),
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
