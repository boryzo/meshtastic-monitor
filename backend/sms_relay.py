from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from backend.jsonsafe import clamp_str

logger = logging.getLogger(__name__)


class SmsRelay:
    def __init__(
        self,
        *,
        enabled: bool = False,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        phone: Optional[str] = None,
        timeout_sec: float = 4.0,
    ) -> None:
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._api_url = (api_url or "").strip()
        self._api_key = (api_key or "").strip()
        self._phone = (phone or "").strip()
        self._timeout_sec = max(1.0, float(timeout_sec))

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "apiUrl": self._api_url or None,
                "phone": self._phone or None,
                "apiKeySet": bool(self._api_key),
            }

    def update_config(
        self,
        *,
        enabled: Optional[bool] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        phone: Optional[str] = None,
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
            if timeout_sec is not None:
                self._timeout_sec = max(1.0, float(timeout_sec))

    def send_message(self, msg: Dict[str, Any]) -> None:
        message = self._format_message(msg)
        self._send_async(message)

    def _format_message(self, msg: Dict[str, Any]) -> str:
        from_id = msg.get("fromId") or "?"
        to_id = msg.get("toId") or "?"
        text = msg.get("text")
        if isinstance(text, str) and text.strip():
            body = text.strip()
        else:
            port = msg.get("portnum")
            body = f"port {port if port is not None else '—'}"
        combined = f"{from_id}→{to_id}: {body}"
        return clamp_str(combined, 300) or combined[:300]

    def _ready(self) -> bool:
        with self._lock:
            return (
                self._enabled
                and bool(self._api_url)
                and bool(self._api_key)
                and bool(self._phone)
            )

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
