from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from backend.jsonsafe import portnum_name


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


def _app_name_from_message(msg: Dict[str, Any]) -> Optional[str]:
    app = msg.get("app")
    if isinstance(app, str) and app:
        return app
    return portnum_name(msg.get("portnum"))


def _order_dir(order: str) -> str:
    return "DESC" if str(order).lower() == "desc" else "ASC"


def _limit_clause(limit: int, offset: Optional[int] = None) -> Tuple[str, Tuple]:
    limit = int(limit)
    if limit <= 0:
        return "", ()
    if offset is None:
        return "LIMIT ?", (limit,)
    return "LIMIT ? OFFSET ?", (limit, max(0, int(offset)))
