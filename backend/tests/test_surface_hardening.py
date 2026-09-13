"""Four findings about the shape of the public surface, not its logic.

Each is a control the codebase already applies somewhere else and had not
applied here: bounding a value before it reaches a log line, not publishing the
existence of an endpoint designed to be invisible, making a shared-state key
unique across replicas, and limiting a route that does real work per call.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import metrics  # noqa: E402
import observability  # noqa: E402
import ratelimit  # noqa: E402

client = TestClient(main.app)


# ── Log injection through an unauthenticated field ───────────────────────────

class TestNotificationTypeIsLogSafe:
    def test_newlines_cannot_forge_a_log_record(self, caplog):
        """`/apple/notifications` is unauthenticated by necessity.

        `notification_type` is read on the *first* branch of the handler,
        before any signature check, and interpolated into an ERROR record. The
        default formatter is a bare `%(message)s`, so a newline in that value
        is a second log line as far as any log reader is concerned — with an
        anonymous caller choosing its entire content.
        """
        # Short enough to pass the new 64-character bound, so this exercises
        # the sanitiser rather than the schema. The length bound is tested
        # separately below; both layers are wanted, since a value can be
        # in-bounds and still forge a line.
        forged = "DID_RENEW\nERROR audit — entitlement.granted tier=pro"
        assert len(forged) <= observability.MAX_LOGGED_VALUE
        with caplog.at_level(logging.ERROR):
            client.post("/apple/notifications", json={"notification_type": forged})
        assert caplog.records, "the handler must still report a V1 body"
        for record in caplog.records:
            message = record.getMessage()
            assert "\n" not in message, (
                "a caller-supplied newline reached a log record verbatim — "
                "that is a second log line an attacker wrote in full")
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "DID_RENEW?ERROR" in combined, (
            "the value is still reported, with the newline neutralised rather "
            f"than the whole field dropped; got {combined!r}")

    def test_an_overlong_value_is_refused_by_the_schema(self):
        """Apple's longest V1 type is 25 characters. 4 KB is not Apple."""
        r = client.post("/apple/notifications",
                        json={"notification_type": "A" * 4096})
        assert r.status_code == 422, (
            "the field was unbounded, and FastAPI reads the whole body before "
            "validation, so nothing in this codebase capped it")

    def test_a_genuine_version_1_body_still_gets_the_helpful_error(self):
        """The field exists only to produce this message; it must survive."""
        r = client.post("/apple/notifications",
                        json={"notification_type": "DID_CHANGE_RENEWAL_STATUS"})
        assert r.status_code == 400
        assert "Version 2" in r.json()["detail"]


class TestLogSafe:
    def test_control_characters_are_replaced_not_dropped(self):
        assert observability.log_safe("a\nb\tc") == "a?b?c", (
            "replacing rather than dropping keeps the record honest that "
            "something was there"
        )

    def test_length_is_bounded_and_marked(self):
        out = observability.log_safe("x" * 500)
        assert out.startswith("x" * observability.MAX_LOGGED_VALUE)
        assert out.endswith("…truncated")

    def test_ordinary_text_is_untouched(self):
        assert observability.log_safe("DID_RENEW") == "DID_RENEW"

    def test_it_never_raises(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("no")

        assert observability.log_safe(Hostile()) == "<unprintable>"
        assert observability.log_safe(None) == "None"


# ── The schema published an endpoint designed to be invisible ────────────────

class TestOpenAPIExposure:
    def test_the_schema_is_available_outside_production(self):
        """It is how the contract tests stay honest; only prod loses it."""
        assert main._OPENAPI_URL == "/openapi.json"
        assert client.get("/openapi.json").status_code == 200

    @pytest.mark.parametrize("value,expected", [
        ("production", True), ("PRODUCTION", True), ("prod", True),
        ("development", False), ("staging", False), ("", False),
    ])
    def test_production_detection(self, value, expected, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", value)
        assert main._is_production() is expected

    def test_production_serves_no_schema_and_no_docs(self):
        """Asserted in a subprocess because the decision is made at import.

        `openapi_url` is fixed when `FastAPI()` is constructed, so setting the
        environment variable in this process proves nothing. This is the only
        honest way to test it — and worth the second it costs, because the
        control it restores (`/metrics` answering 404 so that "an unconfigured
        or unauthorised caller cannot tell the endpoint exists at all") was
        being defeated by a schema listing `/metrics`, its `authorization`
        parameter, and the docstring explaining that it fails closed.
        """
        probe = (
            "import sys; sys.path.insert(0, %r)\n"
            "from fastapi.testclient import TestClient\n"
            "import main\n"
            "c = TestClient(main.app)\n"
            "print(main.app.openapi_url, c.get('/openapi.json').status_code,"
            " c.get('/docs').status_code, c.get('/redoc').status_code)\n"
        ) % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "ENVIRONMENT": "production",
               "TOKEN_KEYS": "k1:test-key-material-not-a-real-secret"}
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, env=env, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.split()[-4:] == ["None", "404", "404", "404"], out.stdout


# ── Sorted-set members collided across replicas ──────────────────────────────

class TestRedisLimiterMemberUniqueness:
    class _FakeClient:
        def register_script(self, _):
            return None

    def test_two_instances_do_not_share_member_strings(self):
        """`os.getpid()` was doing this job and could not.

        The container runs `uvicorn --workers 1`, so uvicorn is PID 1 in every
        replica: `os.getpid()` returned 1 everywhere and `_counter` started at
        0 everywhere, so two replicas walked through identical members. ZADD of
        an existing member updates its score instead of adding an entry, ZCARD
        stays flat, and one request goes uncounted — the limit quietly rises.
        """
        a = ratelimit.RedisRateLimiter(self._FakeClient())
        b = ratelimit.RedisRateLimiter(self._FakeClient())
        assert a._nonce != b._nonce
        assert str(os.getpid()) not in a._nonce

    def test_the_nonce_is_in_the_member(self):
        limiter = ratelimit.RedisRateLimiter(self._FakeClient())
        limiter._counter += 1
        member = f"{1_700_000_000_000}-{limiter._nonce}-{limiter._counter}"
        assert limiter._nonce in member and member.endswith("-1")

    def test_the_member_is_built_from_the_nonce(self):
        """Pins the construction, since nothing reads the member back.

        A behavioural test cannot see this: the member is write-only, so the
        only observable consequence of getting it wrong is an undercount
        against a Redis that is not in this suite.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "ratelimit.py"), encoding="utf-8") as handle:
            source = handle.read()
        assert 'member = f"{now_ms}-{self._nonce}-{self._counter}"' in source
        assert 'member = f"{now_ms}-{os.getpid()}' not in source


# ── /auth/entitlement had no limit at all ────────────────────────────────────

class TestEntitlementRouteIsLimited:
    def test_the_route_consults_the_limiter(self):
        """Observed through the injected hook rather than by flooding.

        The route does a three-certificate chain walk with an ECDSA
        verification per link, per call, and was the only authenticated route
        doing real work with no bucket in front of it.
        """
        import auth
        seen = []

        async def recording(subject, ip):
            seen.append((subject, ip))

        previous = auth.deps.entitlement_limiter
        auth.deps.entitlement_limiter = recording
        try:
            client.post("/auth/entitlement",
                        json={"signed_transaction": "not-a-jws"},
                        headers={"x-device-id": "limiter-probe",
                                 "x-forwarded-for": "1.1.1.1, 203.0.113.9"})
        finally:
            auth.deps.entitlement_limiter = previous

        assert len(seen) == 1, f"the limiter was not consulted: {seen}"
        subject, ip = seen[0]
        assert "limiter-probe" in subject
        assert ip == "203.0.113.9", (
            "the limiter must key on the proxy's own hop, not the "
            f"caller-supplied leftmost one; got {ip}")

    def test_the_bucket_is_not_the_scan_bucket(self):
        """A 429 here makes the server read a paying subscriber as free.

        `/scan` then strips the Pro panel and charges the free allowance. The
        legitimate client posts on cold launch, purchase, restore and every
        `Transaction.updates` event, so the 20/h scan bucket is reachable
        without doing anything unusual — a limit that downgrades a subscriber
        is worse than the work it bounds.
        """
        assert main.ENTITLEMENT_RATE_MAX_REQUESTS > main.RATE_MAX_REQUESTS
        assert main.ENTITLEMENT_RATE_MAX_REQUESTS == 60


# ── Signals that existed and reached nobody ─────────────────────────────────

class TestTraceContextIsPopulated:
    def test_an_inbound_traceparent_reaches_the_log_line(self, caplog):
        """`parse_traceparent` had no production caller.

        It, `trace_id_var`, `span_id_var` and `TRACEPARENT_HEADER` all existed
        and were tested, and a grep for every one of them found hits only
        inside `observability` and one test file. So `TraceIDFilter` wrote
        `trace_id=-` on every line ever logged and the field was dead weight in
        the JSON output rather than the thing that lets a request be followed
        across services.
        """
        trace = "4bf92f3577b34da6a3ce929d0e0e4736"
        span = "00f067aa0ba902b7"
        seen: list[tuple[str, str]] = []

        # Reads the context vars *during* the request, which is the level the
        # property actually lives at. Asserting `record.trace_id` instead would
        # have been testing the wrong thing twice over: `TraceIDFilter` is
        # attached to the root *handler*, so it runs after every logger filter
        # (the first version of this test saw `<unset>`), and it is only
        # attached at all by `configure_production_logging`, which this suite
        # does not call. `TraceIDFilter`'s own mapping is asserted separately
        # below.
        class Capture(logging.Filter):
            def filter(self, record):
                seen.append((observability.trace_id_var.get(),
                             observability.span_id_var.get()))
                return True

        filt = Capture()
        logger = logging.getLogger("snapworth.access")
        logger.addFilter(filt)
        try:
            # `/health/live`, not `/health`: the middleware logs `/health` at
            # DEBUG on purpose (high-frequency and uninteresting), so at the
            # default level it produces no record to inspect.
            client.get("/health/live", headers={
                "traceparent": f"00-{trace}-{span}-01",
            })
        finally:
            logger.removeFilter(filt)

        assert seen, "the access logger did not fire"
        assert (trace, span) in seen, f"trace context never reached it: {seen}"

    def test_the_filter_puts_the_context_on_the_record(self):
        """The other half, in isolation: the var reaches `record.trace_id`."""
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
        token = observability.trace_id_var.set("4bf92f3577b34da6a3ce929d0e0e4736")
        try:
            observability.TraceIDFilter().filter(record)
        finally:
            observability.trace_id_var.reset(token)
        assert record.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert record.span_id == "-", "absent reads as a dash, not an empty field"

    def test_the_context_does_not_leak_into_the_next_request(self):
        """Reset on every path, or a later request inherits these ids.

        The three resets are now one `finally`, which is what makes the
        exception path correct too — the original reset the request id twice
        and the trace vars not at all.
        """
        trace = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        client.get("/health", headers={"traceparent": f"00-{trace}-bbbbbbbbbbbbbbbb-01"})
        assert observability.trace_id_var.get() == ""
        assert observability.span_id_var.get() == ""

    def test_a_malformed_traceparent_is_ignored(self):
        # `parse_traceparent` rejects a malformed or all-zero header, so
        # nothing caller-supplied reaches a log line unchecked.
        for bad in ["", "garbage", "00-0000000000000000-0000000000000000-01",
                    "00-" + "0" * 32 + "-" + "0" * 16 + "-01"]:
            r = client.get("/health", headers={"traceparent": bad})
            assert r.status_code == 200
            assert observability.trace_id_var.get() == ""


class TestRateLimiterHealthIsReported:
    """`is_degraded` had no production reader at all.

    The only references in the tree were its definition and one test. It logs
    once at ERROR on the transition, so an operator looking at the log in that
    second saw it — and afterwards a replica running per-process limits was
    indistinguishable from a healthy one. Degraded limits are *per replica*, so
    the effective ceiling multiplies by the replica count: the exact failure
    the module was written to avoid.

    The limiters are wired by the startup hook, which this suite does not run,
    so they are stubbed here rather than skipped — a skipped test asserts
    nothing and reads as coverage.
    """

    class _Limiter:
        def __init__(self, degraded: bool) -> None:
            self.is_degraded = degraded

    def _health(self, device, ip):
        previous = (main._device_limiter, main._ip_limiter)
        main._device_limiter, main._ip_limiter = device, ip
        try:
            return client.get("/health").json()
        finally:
            main._device_limiter, main._ip_limiter = previous

    def test_health_reports_that_limiting_is_distributed(self):
        body = self._health(self._Limiter(False), self._Limiter(False))
        assert body["rate_limiter"] == {"distributed": True}
        assert body.get("status") != "degraded"

    def test_either_limiter_degrading_is_reported(self):
        # Two independent facades; either one falling back means the ceiling is
        # no longer shared, so both have to be consulted.
        for device, ip in ((True, False), (False, True), (True, True)):
            body = self._health(self._Limiter(device), self._Limiter(ip))
            assert body["rate_limiter"] == {"distributed": False}, (device, ip)
            assert body["status"] == "degraded", (device, ip)

    def test_it_is_reported_not_fatal(self):
        # Per-process limits still enforce something, so draining the replica
        # would be the worse trade — unlike an unreachable cache, which fails
        # quota closed and does return 503.
        response = client.get("/health")
        assert response.status_code == 200
        body = self._health(self._Limiter(True), self._Limiter(True))
        assert body["status"] == "degraded"

    def test_no_limiters_wired_reports_nothing_rather_than_guessing(self):
        body = self._health(None, None)
        assert "rate_limiter" not in body

    def test_there_is_a_gauge_for_it(self):
        # Its own series, not folded into `cache_degraded`: the limiters build
        # their own facades and degrade independently, and the consequences are
        # opposite — an unreachable cache fails quota closed, a degraded
        # limiter fails open per replica.
        assert hasattr(metrics, "rate_limiter_degraded")
        assert metrics.rate_limiter_degraded is not metrics.cache_degraded
