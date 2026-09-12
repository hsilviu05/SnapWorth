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
