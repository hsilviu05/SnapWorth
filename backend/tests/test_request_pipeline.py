"""The middleware stack, driven at the ASGI level.

`TestClient` cannot show either property tested here. It reads a streamed
request body into memory and hands the app one message, so it cannot tell
"refused after 64 KB" from "refused after the whole body"; and its `receive`,
once the body is sent, waits rather than reporting a disconnect, so a scan the
client abandoned looks exactly like one it is still waiting for. These tests
call `main.app` with a `receive` they control and count what it was asked for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import typing
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from annotated_types import MaxLen
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import entitlements  # noqa: E402
import main  # noqa: E402
import metrics  # noqa: E402
import levers  # noqa: E402
import notify  # noqa: E402
import opsstats  # noqa: E402
import observability  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from tests.conftest import build_deps  # noqa: E402
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


def repeated(chunk: bytes, times: int) -> Iterator[bytes]:
    for _ in range(times):
        yield chunk


# ── A chunked body is bounded as it arrives ──────────────────────────────────

# Every route that reads a JSON body. Each was reachable with an unbounded
# chunked body: the old guard read only the declared `content-length`.
JSON_ROUTES = [
    "/apple/notifications",
    "/apple/notifications/sandbox",
    "/auth/attest",
    "/auth/refresh",
    "/auth/entitlement",
    "/listing",
    "/referral/status",
    "/referral/claim",
]

_CHUNK = 16 * 1024


class TestChunkedBodiesAreCapped:
    @pytest.mark.parametrize("path", JSON_ROUTES)
    def test_an_oversize_chunked_json_body_gets_413_and_is_not_read(self, path):
        """The finding: a chunked body carried no length, so nothing bounded it.

        A 200 MB chunked JSON body peaked at about 1.2 GB before the schema
        answered 422, on a single worker. Offered 64 MB here, the server must
        stop within one chunk of the route's cap.
        """
        limit = main._body_limit(path)
        r = drive("POST", path,
                  headers={"content-type": "application/json",
                           "transfer-encoding": "chunked"},
                  chunks=repeated(b" " * _CHUNK, 4096))
        assert r.status == 413, (path, r.status, r.body[:200])
        assert "too large" in r.json()["detail"]
        assert r.chunks_read * _CHUNK <= limit + _CHUNK, (
            f"{path} read {r.chunks_read * _CHUNK} bytes against a {limit} cap")

    def test_a_chunked_upload_is_refused_before_auth_runs(self):
        """The same body on /scan was received in full and *then* answered 401.

        FastAPI resolves the multipart form before the auth dependency, so the
        cap has to act inside the read or it never acts before auth.
        """
        build_deps(enforce=True)
        chunk = 1024 * 1024
        prefix = (b"--b\r\nContent-Disposition: form-data; name=\"file\"; "
                  b"filename=\"a.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")

        def body() -> Iterator[bytes]:
            yield prefix
            yield from repeated(b"\x00" * chunk, 64)

        r = drive("POST", "/scan",
                  headers={"content-type": "multipart/form-data; boundary=b",
                           "transfer-encoding": "chunked"},
                  chunks=body())
        assert r.status == 413, (r.status, r.body[:200])
        assert (r.chunks_read - 1) * chunk <= main.MAX_REQUEST_BYTES + chunk

    def test_a_route_that_takes_no_body_never_reads_one(self):
        """/auth/challenge has no body parameter, so FastAPI never asks for
        the body at all — there is nothing for the cap to stop."""
        r = drive("POST", "/auth/challenge",
                  headers={"transfer-encoding": "chunked"},
                  chunks=repeated(b" " * _CHUNK, 4096))
        assert r.status == 200, (r.status, r.body[:200])
        assert r.chunks_read <= 1

    def test_a_declared_oversize_length_is_refused_without_reading(self):
        r = drive("POST", "/auth/refresh",
                  headers={"content-type": "application/json",
                           "content-length": str(main.MAX_JSON_BODY_BYTES + 1)},
                  chunks=repeated(b" " * _CHUNK, 8))
        assert r.status == 413
        assert r.receive_calls == 0, "the body was read before being refused"

    def test_a_chunked_body_under_the_cap_is_served_normally(self):
        """The cap must not break chunked uploads that are allowed."""
        payload = json.dumps({"device_id": "chunked-ok"}).encode()
        r = drive("POST", "/referral/status",
                  headers={"content-type": "application/json",
                           "transfer-encoding": "chunked"},
                  chunks=iter([payload[:5], payload[5:]]))
        assert r.status != 413
        assert r.status is not None and r.status < 500, (r.status, r.body[:200])

    def test_scan_keeps_the_request_ceiling(self):
        """The per-route caps are small; /scan's is still the one above the
        route's own 10 MB, so an oversized photo gets the route's 400."""
        assert main._body_limit("/scan") == main.MAX_REQUEST_BYTES
        assert main._body_limit("/scan") > main.MAX_UPLOAD_BYTES

    def test_every_route_gets_a_cap(self):
        """An unknown or future route falls to the small cap, not to none."""
        assert main._body_limit("/something-new") == main.MAX_JSON_BODY_BYTES

    @pytest.mark.parametrize("path, model", [
        ("/auth/attest", auth.AttestRequest),
        ("/auth/refresh", auth.AssertRequest),
        ("/auth/entitlement", auth.EntitlementRequest),
        ("/apple/notifications", main.AppleNotification),
        ("/apple/notifications/sandbox", main.AppleNotification),
    ])
    def test_the_caps_sit_above_what_the_schemas_allow(self, path, model):
        """A cap below the largest body a schema accepts refuses a real client.

        Summed from the models' own bounds, so a field that grows fails here
        rather than in production. The margin covers keys and quoting, and
        Swift's `JSONEncoder` writing "/" as "\\/", which base64 contains about
        once in 64 characters.
        """
        largest = sum(bound.max_length for field in model.model_fields.values()
                      for bound in field.metadata if isinstance(bound, MaxLen))
        assert main._body_limit(path) >= largest * 1.1, (path, largest)

    def test_the_refusal_is_counted_and_headed(self):
        """Still inside `SecurityHeaders` and `RecordMetrics`."""
        def count() -> float:
            return metrics.http_requests.value(
                endpoint="/auth/attest", method="POST", status_class="4xx")

        before = count()
        r = drive("POST", "/auth/attest",
                  headers={"content-type": "application/json",
                           "transfer-encoding": "chunked"},
                  chunks=repeated(b" " * _CHUNK, 64))
        assert r.status == 413
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert count() == before + 1


# ── Both notification routes get the notification cap ────────────────────────

NOTIFICATION_ROUTES = ["/apple/notifications", "/apple/notifications/sandbox"]


class TestNotificationRoutesShareTheCap:
    """The Sandbox route (#198) arrived after the per-route caps (#188), and
    `_body_limit` named the Production path exactly. So Sandbox fell to the
    64 KiB default, below its own schema's 64 KiB `signedPayload` plus keys,
    and a Version 1 body — which carries the whole receipt — stopped as a bare
    413 before the handler could say which App Store Connect setting is wrong.
    """

    @pytest.mark.parametrize("path", NOTIFICATION_ROUTES)
    def test_each_gets_the_notification_cap(self, path):
        assert main._body_limit(path) == main.MAX_NOTIFICATION_BODY_BYTES

    def test_every_route_that_takes_a_notification_is_listed(self):
        """A third route taking `AppleNotification` would repeat the Sandbox
        route's gap; this names it instead of waiting for a V1 body to."""
        taking = sorted(
            route.path for route in main.app.routes
            if isinstance(route, APIRoute)
            and main.AppleNotification in typing.get_type_hints(route.endpoint).values())
        assert taking == NOTIFICATION_ROUTES
        assert all(main._body_limit(path) == main.MAX_NOTIFICATION_BODY_BYTES
                   for path in taking)

    @pytest.mark.parametrize("path", NOTIFICATION_ROUTES)
    def test_a_large_version_1_body_reaches_the_handler_s_error(self, path, monkeypatch):
        """Apple's V1 body carries `unified_receipt.latest_receipt`, the whole
        base64 receipt, which grows with a subscriber's history. Above 64 KiB,
        Sandbox answered 413 where Production named the mistake."""
        monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_BOUNDED)
        main._ip_rate_store.clear()
        body = json.dumps({
            "notification_type": "DID_RENEW",
            "environment": "Sandbox",
            "unified_receipt": {
                "status": 0,
                "environment": "Sandbox",
                "latest_receipt": "A" * (main.MAX_JSON_BODY_BYTES + 8 * 1024),
                "latest_receipt_info": [],
            },
        }).encode()
        assert main.MAX_JSON_BODY_BYTES < len(body) < main.MAX_NOTIFICATION_BODY_BYTES

        r = client.post(path, content=body, headers={"content-type": "application/json"})

        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert r.json()["detail"] == (
            "Version 2 notifications required; this is a Version 1 body.")


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
        monkeypatch.setattr(opsstats, "count_scan", lambda tier: counted.append(tier))
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


# ── Which build is calling, and telling an old one to update ────────────────

# What URLSession sends when the app sets no User-Agent of its own, which it
# does not: `<CFBundleName>/<CFBundleVersion> CFNetwork/… Darwin/…`.
def _app_agent(build: int) -> str:
    return f"SnapWorth/{build} CFNetwork/3826.500.131 Darwin/25.0.0"


class TestWhichBuildIsCalling:
    """The server could not say which build a request came from, so a bad
    release could not be found, and "those installs have aged out" was a guess.
    """

    def test_the_build_is_read_from_the_default_user_agent(self):
        assert observability.parse_client_build(_app_agent(20)) == 20

    @pytest.mark.parametrize("agent", [
        "",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15",
        # The widget extension's own bundle name. It is not the app, and a
        # prefix match must not read it as one.
        "SnapWorthWidgets/20 CFNetwork/3826.500.131 Darwin/25.0.0",
        "xSnapWorth/20 CFNetwork/3826.500.131 Darwin/25.0.0",
        "SnapWorth/ CFNetwork/3826.500.131 Darwin/25.0.0",
        "SnapWorth/20a CFNetwork/3826.500.131 Darwin/25.0.0",
        "SnapWorth/1234567 CFNetwork/3826.500.131 Darwin/25.0.0",
        "python-httpx/0.28.1",
    ])
    def test_anything_else_is_unknown_rather_than_old(self, agent):
        assert observability.parse_client_build(agent) is None

    def test_the_access_line_says_which_build(self, caplog):
        with caplog.at_level(logging.INFO, logger="snapworth.access"):
            client.get("/health/live", headers={"User-Agent": _app_agent(17)})
            client.get("/health/live", headers={"User-Agent": "curl/8.7.1"})
            client.get("/health/live", headers={"User-Agent": _app_agent(17),
                                                "X-SnapWorth-Build": "21"})
        builds = [getattr(r, "build") for r in caplog.records
                  if r.name == "snapworth.access"]
        assert builds[-3:] == [17, None, 21]

    @pytest.mark.parametrize("value, build", [
        ("21", 21), (" 21 ", 21), ("", None), ("21a", None), ("-1", None),
        ("1.5.0", None), ("1234567", None), ("２１", None),
    ])
    def test_the_explicit_header_is_digits_or_nothing(self, value, build):
        assert observability.parse_build_header(value) == build

    def test_the_header_wins_and_says_it_was_explicit(self):
        from starlette.datastructures import Headers
        both = Headers({"user-agent": _app_agent(17), "x-snapworth-build": "21"})
        assert observability.client_build(both) == (21, True)
        agent_only = Headers({"user-agent": _app_agent(17)})
        assert observability.client_build(agent_only) == (17, False)
        # A header that does not read is not a build of 0: the agent decides.
        garbled = Headers({"user-agent": _app_agent(17), "x-snapworth-build": "x"})
        assert observability.client_build(garbled) == (17, False)
        assert observability.client_build(Headers({})) == (None, False)


class TestOutdatedBuildsAreToldToUpdate:
    """A bad release could not be told to update: nothing read the build, and
    there was no switch to act on it. `/minbuild` is that switch."""

    @pytest.fixture(autouse=True)
    def _switch(self, monkeypatch):
        # `main` reads the minimum through `levers`, whose store the lifespan
        # wires; `TestClient` without a `with` never runs it.
        self.store = ResilientCache(None, InMemoryCache())
        monkeypatch.setattr(levers, "_cache", self.store)
        main._rate_store.clear()
        main._ip_rate_store.clear()

    def _require(self, build: int) -> None:
        asyncio.run(self.store.set(levers.MIN_BUILD_KEY, str(build)))

    def _trends(self, agent: str):
        return client.get("/trends", headers={"User-Agent": agent,
                                              "x-device-id": "minbuild-test"})

    def test_nothing_is_refused_until_a_minimum_is_set(self):
        assert self._trends(_app_agent(1)).status_code == 200

    def test_a_scan_from_an_older_build_is_told_to_update_and_costs_nothing(
            self, monkeypatch):
        counted: list[str] = []
        completed: list[dict] = []
        monkeypatch.setattr(opsstats, "count_scan", lambda tier: counted.append(tier))
        monkeypatch.setattr(notify, "scan_completed", lambda **kw: completed.append(kw))
        self._require(18)
        headers, body = _scan_body()
        with patch("main._model") as model:
            model.generate_content_async = AsyncMock()
            r = client.post("/scan", content=body,
                            headers=headers | {"User-Agent": _app_agent(17)})
            assert not model.generate_content_async.called, "the model was billed"
        # 422 is the status whose `detail` every build from 8 shows as written
        # (`.unusablePhoto`); a 426 would be shown as "Something went wrong",
        # and a 502 as "Our AI is temporarily unavailable" on builds 8-10.
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == levers.UPDATE_REQUIRED_DETAIL
        assert "App Store" in r.json()["detail"]
        assert counted == [] and completed == []

    def test_a_refusal_does_not_page(self):
        """Turning `/minbuild` on must not look like a Gemini outage: a 5xx
        here is a `DEPENDENCY` error, which pages and feeds the 5xx surge."""
        self._require(18)
        r = self._trends(_app_agent(12))
        assert r.json()["detail"] == levers.UPDATE_REQUIRED_DETAIL
        assert r.status_code < 500
        assert not observability.classify_status(r.status_code).pages

    @pytest.mark.parametrize("method, path", [("GET", "/trends"), ("POST", "/listing")])
    def test_listing_and_trends_are_gated_too(self, method, path):
        self._require(18)
        r = client.request(method, path, json={} if method == "POST" else None,
                           headers={"User-Agent": _app_agent(12)})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == levers.UPDATE_REQUIRED_DETAIL

    def test_the_minimum_itself_and_newer_are_served(self):
        self._require(18)
        assert self._trends(_app_agent(18)).status_code == 200
        assert self._trends(_app_agent(19)).status_code == 200

    def test_a_request_that_does_not_say_its_build_is_served(self):
        """Unknown is not old. The widget, a script, a future client that
        changes its User-Agent — none of them is refused on a guess."""
        self._require(18)
        for agent in ("", "curl/8.7.1", "SnapWorthWidgets/5 CFNetwork/1 Darwin/1"):
            assert self._trends(agent).status_code == 200, agent

    def test_sign_in_is_never_gated(self):
        """An old build must still be able to sign in and record a purchase."""
        self._require(18)
        r = client.post("/auth/challenge", headers={"User-Agent": _app_agent(12)})
        assert r.status_code == 200, r.text

    def test_an_unreadable_switch_serves_everyone(self, monkeypatch):
        """Fails open: a store that blinks must not lock every user out."""
        async def broken(*_a, **_k):
            raise ConnectionError("redis is down")

        monkeypatch.setattr(self.store, "get", broken)
        assert self._trends(_app_agent(1)).status_code == 200

    def test_the_refusal_is_counted(self):
        def count() -> float:
            return metrics.outdated_build_refused.value(endpoint="/trends")

        self._require(18)
        before = count()
        self._trends(_app_agent(12))
        assert count() == before + 1

    def test_the_refusal_says_update_required_in_a_code(self):
        """Every build that reads codes routes on this rather than on
        whichever status reached it."""
        self._require(18)
        r = self._trends(_app_agent(12))
        assert r.status_code == 422
        assert r.json()["code"] == "update_required"


class TestABuildThatSaysItsBuildGetsA426:
    """The client half of "require an update". A build that sends
    `X-SnapWorth-Build` was written to show a 426 — its own update message,
    with a button to the App Store on the Scan tab — so it gets the honest
    status, where a build known only from its User-Agent keeps the 422 it can
    show."""

    @pytest.fixture(autouse=True)
    def _switch(self, monkeypatch):
        self.store = ResilientCache(None, InMemoryCache())
        monkeypatch.setattr(levers, "_cache", self.store)
        main._rate_store.clear()
        main._ip_rate_store.clear()
        asyncio.run(self.store.set(levers.MIN_BUILD_KEY, "30"))

    def _headers(self, build: str, agent_build: int = 29) -> dict[str, str]:
        return {"User-Agent": _app_agent(agent_build), "X-SnapWorth-Build": build,
                "x-device-id": "minbuild-426"}

    @pytest.mark.parametrize("method, path", [
        ("GET", "/trends"), ("POST", "/listing"), ("POST", "/scan")])
    def test_below_the_minimum_is_a_426_with_the_code(self, method, path):
        # Patched so a gate that let this through could not reach the model.
        with patch("main._generate_with_retry", AsyncMock()) as model:
            r = client.request(method, path, json={} if path == "/listing" else None,
                               headers=self._headers("29"))
        assert not model.called
        assert r.status_code == 426, r.text
        assert r.json() == {"detail": levers.UPDATE_REQUIRED_DETAIL,
                            "code": "update_required"}

    def test_the_minimum_and_newer_are_served(self):
        for build in ("30", "31"):
            r = client.get("/trends", headers=self._headers(build))
            assert r.status_code == 200, (build, r.text)

    def test_the_header_is_believed_over_the_user_agent(self):
        """The User-Agent is URLSession's; the header is the app's own."""
        r = client.get("/trends", headers=self._headers("31", agent_build=12))
        assert r.status_code == 200, r.text

    def test_an_unreadable_header_falls_back_to_the_user_agent(self):
        r = client.get("/trends", headers=self._headers("soon", agent_build=12))
        assert r.status_code == 422, "the agent says 12: refused as before"
        assert r.json()["code"] == "update_required"

    def test_sign_in_is_never_gated(self):
        r = client.post("/auth/challenge", headers=self._headers("1"))
        assert r.status_code == 200, r.text

    def test_a_426_does_not_page(self):
        assert not observability.classify_status(426).pages
