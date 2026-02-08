from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, Optional


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, where: str = "", params: Iterable[Any] = ()) -> int:
    sql = "SELECT COUNT(*) AS c FROM messages"
    if where:
        sql += f" WHERE {where}"
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row["c"]) if row else 0


def _min_max(conn: sqlite3.Connection, col: str) -> Dict[str, Optional[int]]:
    row = conn.execute(
        f"SELECT MIN({col}) AS min_val, MAX({col}) AS max_val FROM messages"
    ).fetchone()
    if not row:
        return {"min": None, "max": None}
    return {
        "min": int(row["min_val"]) if row["min_val"] is not None else None,
        "max": int(row["max_val"]) if row["max_val"] is not None else None,
    }


def _top_counts(conn: sqlite3.Connection, col: str, limit: int) -> list[Dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT
          COALESCE(CAST({col} AS TEXT), '(none)') AS key,
          COUNT(*) AS c
        FROM messages
        GROUP BY {col}
        ORDER BY c DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{"key": r["key"], "count": int(r["c"])} for r in rows]


def _recent_messages(
    conn: sqlite3.Connection,
    *,
    with_text: bool,
    with_payload: bool,
    limit: int,
) -> list[Dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if with_text:
        clauses.append("text IS NOT NULL AND TRIM(text) != ''")
    else:
        clauses.append("(text IS NULL OR TRIM(text) = '')")
    if with_payload:
        clauses.append("payload_b64 IS NOT NULL AND payload_b64 != ''")
    sql = (
        "SELECT rx_time, from_id, to_id, app, portnum, channel, text, payload_b64, error "
        "FROM messages"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY rx_time DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out: list[Dict[str, Any]] = []
    for r in rows:
        text = r["text"]
        text_str = None
        if isinstance(text, str):
            t = text.strip()
            text_str = t[:120] + ("…" if len(t) > 120 else "") if t else ""
        out.append(
            {
                "rxTime": r["rx_time"],
                "fromId": r["from_id"],
                "toId": r["to_id"],
                "app": r["app"],
                "portnum": r["portnum"],
                "channel": r["channel"],
                "text": text_str,
                "payload": r["payload_b64"] is not None and r["payload_b64"] != "",
                "error": r["error"],
            }
        )
    return out


def _fmt_epoch(ts: Optional[int]) -> str:
    if ts is None:
        return "n/a"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


def main() -> int:
    default_db = os.getenv("STATS_DB_PATH", "meshmon.db").strip()
    parser = argparse.ArgumentParser(description="Analyze Meshtastic monitor stats DB.")
    parser.add_argument("--db", default=default_db, help="Path to stats DB (default: meshmon.db)")
    parser.add_argument("--limit", type=int, default=10, help="How many sample rows to show")
    args = parser.parse_args()

    db_path = args.db
    if not db_path or db_path.lower() == "off":
        print("Stats DB is disabled (STATS_DB_PATH=off).", file=sys.stderr)
        return 2
    if not os.path.exists(db_path):
        print(f"DB file not found: {db_path}", file=sys.stderr)
        return 1

    conn = _connect(db_path)
    try:
        if not _table_exists(conn, "messages"):
            print("Table 'messages' not found in DB.", file=sys.stderr)
            return 3

        total = _count(conn)
        with_text = _count(conn, "text IS NOT NULL AND TRIM(text) != ''")
        with_payload = _count(conn, "payload_b64 IS NOT NULL AND payload_b64 != ''")
        with_error = _count(conn, "error IS NOT NULL AND error != ''")
        empty_text = _count(conn, "(text IS NULL OR TRIM(text) = '')")

        time_range = _min_max(conn, "rx_time")

        print(f"DB: {db_path}")
        print(f"Total messages: {total}")
        print(f"With text: {with_text}")
        print(f"No text: {empty_text}")
        print(f"With payload: {with_payload}")
        print(f"With error: {with_error}")
        print(f"Time range: {time_range['min']} -> {time_range['max']}")
        print(f"Time range (local): {_fmt_epoch(time_range['min'])} -> {_fmt_epoch(time_range['max'])}")

        print("\nTop apps:")
        for item in _top_counts(conn, "app", 10):
            print(f"- {item['key']}: {item['count']}")

        print("\nTop portnum:")
        for item in _top_counts(conn, "portnum", 10):
            print(f"- {item['key']}: {item['count']}")

        print("\nTop channels:")
        for item in _top_counts(conn, "channel", 10):
            print(f"- {item['key']}: {item['count']}")

        limit = max(0, int(args.limit))
        if limit:
            print(f"\nRecent with text (limit={limit}):")
            for r in _recent_messages(conn, with_text=True, with_payload=False, limit=limit):
                print(
                    f"- {r['rxTime']} {r['fromId']} -> {r['toId']} "
                    f"app={r['app']} portnum={r['portnum']} ch={r['channel']} "
                    f"text={r['text']!r}"
                )

            print(f"\nRecent without text but with payload (limit={limit}):")
            for r in _recent_messages(conn, with_text=False, with_payload=True, limit=limit):
                print(
                    f"- {r['rxTime']} {r['fromId']} -> {r['toId']} "
                    f"app={r['app']} portnum={r['portnum']} ch={r['channel']} "
                    f"payload={r['payload']} error={r['error']!r}"
                )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
