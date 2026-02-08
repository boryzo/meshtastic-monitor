from __future__ import annotations

import io
import urllib.error
import urllib.parse

import backend.sms_relay as sms_relay


class _ImmediateThread:
    def __init__(self, target, daemon=None):  # noqa: D401, ANN001
        self._target = target

    def start(self) -> None:
        self._target()


class _DummyResponse:
    def __init__(self, status: int = 200, body: bytes = b"OK") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):  # noqa: ANN001
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


def _freeze_time(monkeypatch, values):
    it = iter(values)
    last = values[-1] if values else 0.0

    def _next():  # noqa: ANN001
        nonlocal last
        try:
            last = next(it)
        except StopIteration:
            pass
        return last

    monkeypatch.setattr(sms_relay.time, "time", _next)


def test_sms_relay_sends_and_logs_without_url_or_key(monkeypatch, caplog):
    api_url = "https://sms.example/send"
    api_key = "SECRETKEY"
    phone = "600000000"

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        assert api_key in url
        assert api_url in url
        return _DummyResponse(status=200, body=b"OK")

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)
    _freeze_time(monkeypatch, [1000.0, 1000.25])

    relay = sms_relay.SmsRelay(
        enabled=True,
        api_url=api_url,
        api_key=api_key,
        phone=phone,
        timeout_sec=2.0,
    )

    caplog.set_level("INFO")
    relay.send_message({"fromId": "!a", "toId": "!b", "text": "hello"})

    log_text = "\n".join(r.message for r in caplog.records)
    assert "SMS relay dispatch" in log_text
    assert "hello" in log_text
    assert "SMS relay sent" in log_text
    assert api_url not in log_text
    assert api_key not in log_text


def test_sms_relay_redacts_key_from_response(monkeypatch, caplog):
    api_url = "https://sms.example/send"
    api_key = "SECRETKEY"
    phone = "600000000"
    encoded = urllib.parse.quote_plus(api_key)
    body = f"ok key={api_key} enc={encoded}".encode("utf-8")

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        return _DummyResponse(status=200, body=body)

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)
    _freeze_time(monkeypatch, [1000.0, 1000.1])

    relay = sms_relay.SmsRelay(
        enabled=True,
        api_url=api_url,
        api_key=api_key,
        phone=phone,
    )

    caplog.set_level("INFO")
    relay.send_message({"fromId": "!a", "toId": "!b", "text": "hello"})

    log_text = "\n".join(r.message for r in caplog.records)
    assert "SMS relay sent" in log_text
    assert api_key not in log_text
    assert encoded not in log_text


def test_sms_relay_http_error_logs_without_key(monkeypatch, caplog):
    api_url = "https://sms.example/send"
    api_key = "SECRETKEY"
    phone = "600000000"
    body = f"err {api_key}".encode("utf-8")

    err = urllib.error.HTTPError(
        api_url,
        500,
        "server error",
        hdrs=None,
        fp=io.BytesIO(body),
    )

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        raise err

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)
    _freeze_time(monkeypatch, [1000.0, 1000.2])

    relay = sms_relay.SmsRelay(
        enabled=True,
        api_url=api_url,
        api_key=api_key,
        phone=phone,
    )

    caplog.set_level("WARNING")
    relay.send_message({"fromId": "!a", "toId": "!b", "text": "hello"})

    log_text = "\n".join(r.message for r in caplog.records)
    assert "SMS relay failed" in log_text
    assert api_key not in log_text


def test_sms_relay_disabled_does_not_call_gateway(monkeypatch):
    called = {"ok": False}

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        called["ok"] = True
        return _DummyResponse()

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)

    relay = sms_relay.SmsRelay(enabled=False, api_url="x", api_key="y", phone="z")
    relay.send_message({"fromId": "!a", "toId": "!b", "text": "hello"})

    assert called["ok"] is False


def test_sms_relay_filters_by_from_id(monkeypatch):
    called = {"ok": False}

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        called["ok"] = True
        return _DummyResponse()

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)

    relay = sms_relay.SmsRelay(
        enabled=True,
        api_url="https://sms.example/send",
        api_key="k",
        phone="p",
        allow_from_ids="!allow",
    )
    relay.send_message({"fromId": "!deny", "toId": "!b", "text": "hello"})
    assert called["ok"] is False


def test_sms_relay_filters_by_type(monkeypatch):
    called = {"ok": False}

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        called["ok"] = True
        return _DummyResponse()

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)

    relay = sms_relay.SmsRelay(
        enabled=True,
        api_url="https://sms.example/send",
        api_key="k",
        phone="p",
        allow_types="TEXT",
    )
    relay.send_message(
        {"fromId": "!a", "toId": "!b", "portnum": "ROUTING_APP", "app": "ROUTING_APP", "text": ""}
    )
    assert called["ok"] is False

    relay.update_config(allow_types="ROUTING_APP")
    relay.send_message(
        {"fromId": "!a", "toId": "!b", "portnum": "ROUTING_APP", "app": "ROUTING_APP", "text": ""}
    )
    assert called["ok"] is False


def test_sms_relay_gsm7_sanitize_removes_non_gsm7():
    text = "Zażółć gęślą jaźń 😀 → €"
    sanitized = sms_relay._gsm7_sanitize(text)
    assert "ż" not in sanitized
    assert "ó" not in sanitized
    assert "ł" not in sanitized
    assert "ę" not in sanitized
    assert "ś" not in sanitized
    assert "ź" not in sanitized
    assert "ń" not in sanitized
    assert "😀" not in sanitized
    assert "→" not in sanitized
    assert "€" in sanitized
    assert "Zazolc" in sanitized
    assert all(
        ch in sms_relay._GSM7_BASIC or ch in sms_relay._GSM7_EXTENDED for ch in sanitized
    )


def test_sms_relay_format_message_uses_gsm7_only():
    relay = sms_relay.SmsRelay(enabled=True, api_url="x", api_key="y", phone="z")
    msg = {"fromId": "!a", "toId": "!b", "text": "Cześć 😀 €"}
    formatted = relay._format_message(msg)
    assert "->" in formatted
    assert "😀" not in formatted
    assert "ć" not in formatted
    assert "€" in formatted
    assert "Czesc" in formatted


def test_sms_relay_skips_when_no_text(monkeypatch):
    called = {"ok": False}

    def fake_urlopen(url, timeout=None):  # noqa: ANN001
        called["ok"] = True
        return _DummyResponse()

    monkeypatch.setattr(sms_relay.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sms_relay.threading, "Thread", _ImmediateThread)

    relay = sms_relay.SmsRelay(enabled=True, api_url="x", api_key="y", phone="z")
    relay.send_message({"fromId": "!a", "toId": "!b", "text": ""})
    assert called["ok"] is False
