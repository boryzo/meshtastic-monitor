from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from backend.jsonsafe import clamp_str

logger = logging.getLogger(__name__)

_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
    "ÆæßÉ "
    "!\"#¤%&'()*+,-./"
    "0123456789"
    ":;<=>?"
    "¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "ÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyz"
    "äöñüà"
)
_GSM7_EXTENDED = set("^{}\\[~]|€\f")

_PL_MAP = {
    "ą": "a",
    "ć": "c",
    "ę": "e",
    "ł": "l",
    "ń": "n",
    "ó": "o",
    "ś": "s",
    "ż": "z",
    "ź": "z",
    "Ą": "A",
    "Ć": "C",
    "Ę": "E",
    "Ł": "L",
    "Ń": "N",
    "Ó": "O",
    "Ś": "S",
    "Ż": "Z",
    "Ź": "Z",
}


def _gsm7_normalize(text: str) -> str:
    if not text:
        return ""
    out = text
    for src, dst in _PL_MAP.items():
        out = out.replace(src, dst)
    out = out.replace("→", "->")
    return out


def _gsm7_sanitize(text: str) -> str:
    if not text:
        return ""
    normalized = _gsm7_normalize(text)
    return "".join(ch for ch in normalized if ch in _GSM7_BASIC)


class SmsRelay:
    def __init__(
        self,
        *,
        enabled: bool = False,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        phone: Optional[str] = None,
        allow_from_ids: Optional[str] = None,
        allow_types: Optional[str] = None,
        timeout_sec: float = 4.0,
    ) -> None:
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._api_url = (api_url or "").strip()
        self._api_key = (api_key or "").strip()
        self._phone = (phone or "").strip()
        self._allow_from_ids_raw = (allow_from_ids or "").strip()
        self._allow_types_raw = (allow_types or "").strip()
        self._allow_from_ids = _parse_allow_from_ids(self._allow_from_ids_raw)
        self._allow_types = _parse_allow_types(self._allow_types_raw)
        self._timeout_sec = max(1.0, float(timeout_sec))

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "apiUrl": self._api_url or None,
                "phone": self._phone or None,
                "apiKeySet": bool(self._api_key),
                "allowFromIds": self._allow_from_ids_raw or "ALL",
                "allowTypes": self._allow_types_raw or "ALL",
            }

    def update_config(
        self,
        *,
        enabled: Optional[bool] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        phone: Optional[str] = None,
        allow_from_ids: Optional[str] = None,
        allow_types: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if api_url is not None:
                self._api_url = str(api_url or "").strip()
            if api_key is not None:
                self._api_key = str(api_key or "").strip()
            if phone is not None:
                self._phone = str(phone or "").strip()
            if allow_from_ids is not None:
                self._allow_from_ids_raw = str(allow_from_ids or "").strip()
                self._allow_from_ids = _parse_allow_from_ids(self._allow_from_ids_raw)
            if allow_types is not None:
                self._allow_types_raw = str(allow_types or "").strip()
                self._allow_types = _parse_allow_types(self._allow_types_raw)
            if timeout_sec is not None:
                self._timeout_sec = max(1.0, float(timeout_sec))

    def send_message(self, msg: Dict[str, Any]) -> None:
        if not self._is_allowed(msg):
            return
        message = self._format_message(msg)
        if not message:
            return
        self._send_async(message)

    def _format_message(self, msg: Dict[str, Any]) -> str:
        from_id = msg.get("fromId") or "?"
        to_id = msg.get("toId") or "?"
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            return ""
        body = text.strip()
        combined = f"{from_id}->{to_id}: {body}"
        combined = _gsm7_sanitize(combined).strip()
        return clamp_str(combined, 300) or combined[:300]

    def _ready(self) -> bool:
        with self._lock:
            return (
                self._enabled
                and bool(self._api_url)
                and bool(self._api_key)
                and bool(self._phone)
            )

    def _is_allowed(self, msg: Dict[str, Any]) -> bool:
        with self._lock:
            allow_from = self._allow_from_ids
            allow_types = self._allow_types
        from_id_raw = msg.get("fromId") or ""
        from_id = str(from_id_raw).strip().lower()
        if allow_from and from_id not in allow_from:
            logger.info("SMS relay skipped (reason=from_id, from=%s)", from_id_raw or "—")
            return False
        msg_types = _message_types(msg)
        if allow_types and msg_types.isdisjoint(allow_types):
            logger.info(
                "SMS relay skipped (reason=type, from=%s, types=%s)",
                from_id_raw or "—",
                ",".join(sorted(msg_types)),
            )
            return False
        return True

    def _send_async(self, message: str) -> None:
        if not self._ready():
            return

        def _send() -> None:
            start = time.time()
            with self._lock:
                api_url = self._api_url
                api_key = self._api_key
                phone = self._phone
                timeout = self._timeout_sec

            if not api_url or not api_key or not phone:
                return

            params = {
                "api_key": api_key,
                "phone": phone,
                "message": message,
            }
            logger.info("SMS relay dispatch (msg=%s)", message)
            qs = urllib.parse.urlencode(params, doseq=False, safe="")
            url = api_url + ("&" if "?" in api_url else "?") + qs

            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    status = getattr(resp, "status", None) or getattr(resp, "code", None)
                    body = resp.read()
                duration_ms = int((time.time() - start) * 1000)
                snippet = _snippet(body, api_key)
                if snippet:
                    logger.info("SMS relay sent (status=%s, ms=%s, resp=%s)", status, duration_ms, snippet)
                else:
                    logger.info("SMS relay sent (status=%s, ms=%s)", status, duration_ms)
            except urllib.error.HTTPError as e:
                duration_ms = int((time.time() - start) * 1000)
                try:
                    body = e.read()
                except Exception:
                    body = b""
                snippet = _snippet(body, api_key)
                if snippet:
                    logger.warning("SMS relay failed (http=%s, ms=%s, resp=%s)", e.code, duration_ms, snippet)
                else:
                    logger.warning("SMS relay failed (http=%s, ms=%s)", e.code, duration_ms)
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                logger.warning("SMS relay failed (%s, ms=%s)", type(e).__name__, duration_ms)

        threading.Thread(target=_send, daemon=True).start()


def _snippet(payload: Any, api_key: str, max_len: int = 200) -> str:
    if not payload:
        return ""
    try:
        if isinstance(payload, (bytes, bytearray)):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = str(payload)
    except Exception:
        return ""
    text = text.strip()
    if not text:
        return ""
    text = _redact_api_key(text, api_key)
    text = _redact_urls(text)
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _redact_api_key(text: str, api_key: str) -> str:
    if not api_key:
        return text
    safe = "***"
    if api_key in text:
        text = text.replace(api_key, safe)
    try:
        encoded = urllib.parse.quote_plus(api_key)
        if encoded and encoded in text:
            text = text.replace(encoded, safe)
    except Exception:
        pass
    return text


_URL_RE = re.compile(r"https?://\\S+", re.IGNORECASE)


def _redact_urls(text: str) -> str:
    return _URL_RE.sub("***", text)


def _split_list(value: str) -> list[str]:
    return [v for v in re.split(r"[\\s,;]+", value) if v]


def _parse_allow_from_ids(raw: str) -> Optional[set[str]]:
    if not raw:
        return None
    items = [v.strip() for v in _split_list(raw) if v.strip()]
    if not items:
        return None
    if any(v.upper() == "ALL" for v in items):
        return None
    return {v.lower() for v in items}


def _parse_allow_types(raw: str) -> Optional[set[str]]:
    if not raw:
        return None
    items = [v.strip() for v in _split_list(raw) if v.strip()]
    if not items:
        return None
    if any(v.upper() == "ALL" for v in items):
        return None
    out: set[str] = set()
    for item in items:
        up = item.upper()
        if up.isdigit():
            out.add(f"PORTNUM:{int(up)}")
        else:
            out.add(up)
    return out


def _message_types(msg: Dict[str, Any]) -> set[str]:
    types: set[str] = set()
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        types.add("TEXT")
    app = msg.get("app")
    if isinstance(app, str) and app.strip():
        types.add(app.strip().upper())
    port = msg.get("portnum")
    if port is not None:
        try:
            pn = int(port)
            types.add(f"PORTNUM:{pn}")
        except Exception:
            pass
    if not types:
        types.add("UNKNOWN")
    return types
