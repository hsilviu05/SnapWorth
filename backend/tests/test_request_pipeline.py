"""The middleware stack, driven at the ASGI level.

`TestClient` cannot show the property tested here: its `receive`, once the
body is sent, waits rather than reporting a disconnect, so a scan the client
abandoned looks exactly like one it is still waiting for. These tests call
`main.app` with a `receive` they control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import notify  # noqa: E402
import observability  # noqa: E402
from tests.images import padded_image_bytes  # noqa: E402

client = TestClient(main.app)


class Driven:
    """One request run through `main.app`, and what came of it."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.chunks_read = 0
        self.receive_calls = 0

    def json(self) -> dict:
        return json.loads(self.body)


def drive(method: str, path: str, *, headers: dict[str, str] | None = None,
          chunks: Iterator[bytes] = iter(()),
          then: str = "disconnect") -> Driven:
    """Run one request through the whole stack, streaming `chunks` lazily.

    Each chunk is its own `http.request` message, the way uvicorn delivers a
    chunked body. The generator is only advanced when the app asks for more,
    so `chunks_read` is how much of the body the server actually took.

    `then` is what `receive` answers once the body is exhausted:
    "disconnect" returns `http.disconnect` at once, as uvicorn does for a
    client that has already gone; "wait" blocks, as it does for one that is
    still there.
    """
    result = Driven()
    source = iter(chunks)
    # One chunk of lookahead, which is what `more_body` needs. `chunks_read`
    # counts only what was handed to the app.
    pending: bytes | None = next(source, None)
    body_done = False
    forever = asyncio.Event()

    async def receive() -> Message:
        nonlocal pending, body_done
        result.receive_calls += 1
        if not body_done:
            chunk = pending or b""
            if pending is not None:
                result.chunks_read += 1
            pending = next(source, None)
            body_done = pending is None
            return {"type": "http.request", "body": chunk, "more_body": not body_done}
        if then == "disconnect":
            return {"type": "http.disconnect"}
        await forever.wait()
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {k.decode().lower(): v.decode()
                              for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            result.body += message.get("body", b"")

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in (headers or {}).items()],
        "client": ("203.0.113.7", 50000), "server": ("testserver", 80),
        "state": {}, "extensions": {},
    }
    asyncio.run(main.app(scope, receive, send))
    return result


# ── The disconnect check sees a real disconnect ──────────────────────────────

MOCK_RESPONSE_JSON = {
    "item_name": "Patagonia Better Sweater Fleece Jacket",
    "brand": "Patagonia",
    "category": "clothing",
    "est_value_low_usd": 45.0,
    "est_value_high_usd": 75.0,
    "confidence": "High",
    "reasoning": "Recognisable fleece.",
    "best_platform": "Poshmark",
    "listing_title": "Patagonia Better Sweater Fleece",
    "listing_description": "Great used condition.",
}


def _scan_body() -> tuple[dict[str, str], bytes]:
    request = httpx.Request(
        "POST", "http://testserver/scan",
        files={"file": ("scan.jpg", padded_image_bytes("JPEG", 1024), "image/jpeg")})
    body = request.read()
    return ({"content-type": request.headers["content-type"],
             "content-length": str(len(body)),
             "x-device-id": "disconnect-test"}, body)


class TestAbandonedScansAreNotCharged:
    """`request.is_disconnected()` could never return True.

    Every `BaseHTTPMiddleware` layer wraps `receive` in a task group, and
    `is_disconnected` asks with an already-cancelled scope — so the wrapper
    was cancelled before it could relay anything, and the answer was always
    "still connected". The existing test patched the method, so the suite
    never saw it. Nothing is patched here: the disconnect is a real
    `http.disconnect` message from `receive`.
    """

    def _run(self, then: str, monkeypatch) -> tuple[Driven, list, list]:
        counted: list[str] = []
        completed: list[dict] = []
        monkeypatch.setattr(notify, "count_scan", lambda tier: counted.append(tier))
        monkeypatch.setattr(notify, "scan_completed", lambda **kw: completed.append(kw))
        main._rate_store.clear()
        main._ip_rate_store.clear()
        response = MagicMock()
        response.text = json.dumps(MOCK_RESPONSE_JSON)
        headers, body = _scan_body()
        with patch("main._model") as model:
            model.generate_content_async = AsyncMock(return_value=response)
            r = drive("POST", "/scan", headers=headers, chunks=iter([body]), then=then)
        return r, counted, completed

    def test_a_client_that_has_gone_is_not_charged(self, monkeypatch):
        r, counted, completed = self._run("disconnect", monkeypatch)
        assert r.status == 200, (r.status, r.body[:300])
        assert completed == [], "charged and announced a find nobody saw"
        assert counted == ["free"], "the billed call is still in $/scan"

    def test_a_client_that_is_still_there_is_charged(self, monkeypatch):
        r, counted, completed = self._run("wait", monkeypatch)
        assert r.status == 200, (r.status, r.body[:300])
        assert len(completed) == 1 and counted == []

    def test_no_middleware_can_hide_the_disconnect_again(self):
        """`@app.middleware("http")` is `BaseHTTPMiddleware`; so is subclassing it.

        Either one, anywhere in the stack, silently turns the check above back
        into `False`. This names the cause, where the behavioural test above
        would only say that a free user was charged.
        """
        offenders = [m.cls for m in main.app.user_middleware
                     if isinstance(m.cls, type) and issubclass(m.cls, BaseHTTPMiddleware)]
        assert offenders == [], offenders


# ── What the pure-ASGI rewrite must still do ────────────────────────────────

class TestTheRewrittenLayersStillWork:
    def test_the_request_id_is_echoed(self):
        r = client.get("/health/live", headers={"X-Request-ID": "abc123"})
        assert r.headers["X-Request-ID"] == "abc123"

    def test_a_request_id_is_minted_when_none_is_sent(self):
        r = client.get("/health/live")
        assert len(r.headers["X-Request-ID"]) == 16

    def test_an_unsafe_inbound_id_is_replaced(self):
        r = client.get("/health/live", headers={"X-Request-ID": "x" * 65})
        assert r.headers["X-Request-ID"] != "x" * 65

    def test_security_headers_are_on_every_response(self):
        for r in (client.get("/health/live"), client.get("/no-such-route")):
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["X-Frame-Options"] == "DENY"
            assert r.headers["Strict-Transport-Security"].startswith("max-age=")

    def test_the_access_line_carries_status_and_duration(self, caplog):
        with caplog.at_level(logging.INFO, logger="snapworth.access"):
            client.get("/health/live")
        records = [r for r in caplog.records if r.name == "snapworth.access"]
        assert records, "no access line"
        line = records[-1]
        assert getattr(line, "status") == 200
        assert getattr(line, "path") == "/health/live"
        assert isinstance(getattr(line, "duration_ms"), float)

    def test_an_exception_is_logged_and_the_context_reset(self, caplog):
        async def failing_app(scope, receive, send):
            raise RuntimeError("kaboom")

        async def receive():  # pragma: no cover — never asked
            return {"type": "http.disconnect"}

        async def send(message):  # pragma: no cover — nothing is sent
            pass

        layer = observability.RequestContextMiddleware(failing_app)
        scope = {"type": "http", "method": "GET", "path": "/x", "headers": [
            (b"traceparent", b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")]}

        # Read back in the same task: the layer now runs the app in the
        # caller's context rather than in a task of its own, so a missed reset
        # would be visible here.
        async def scenario() -> tuple[str, str]:
            with pytest.raises(RuntimeError):
                await layer(scope, receive, send)
            return observability.request_id_var.get(), observability.trace_id_var.get()

        with caplog.at_level(logging.ERROR, logger="snapworth.access"):
            rid, trace = asyncio.run(scenario())
        assert any(r.getMessage() == "request failed" for r in caplog.records)
        assert (rid, trace) == ("-", "")
