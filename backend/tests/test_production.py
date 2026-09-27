"""Production-readiness tests: metrics, redaction, tracing, probes, shutdown.

The properties here are the ones that only fail in production, and only under
load or during a deploy — which is exactly why they need tests rather than a
manual check before launch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devicecheck  # noqa: E402
import metrics  # noqa: E402
import observability as obs  # noqa: E402
import main  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.registry.reset()
    yield


# Low-entropy on purpose so the secret scanner has nothing to flag: it is a
# hyphenated English phrase, not a credential shape.
METRICS_TOKEN = "test-metrics-token-not-a-real-secret"


@pytest.fixture
def metrics_auth(monkeypatch):
    """Configures the /metrics bearer and returns the matching header.

    /metrics fails closed, so every test that needs to read the exposition has
    to opt in. Scoped per-test rather than autouse so the access-control tests
    below can observe the unconfigured state.
    """
    monkeypatch.setenv("METRICS_TOKEN", METRICS_TOKEN)
    return {"Authorization": f"Bearer {METRICS_TOKEN}"}


# ═══ Metrics ══════════════════════════════════════════════════════════════════

class TestMetricPrimitives:
    def test_counter_accumulates(self):
        c = metrics.Counter("t_counter", "help")
        c.inc()
        c.inc(4)
        assert c.value() == 5

    def test_counter_with_labels_is_isolated(self):
        c = metrics.Counter("t_labelled", "help", ("kind",))
        c.inc(kind="a")
        c.inc(2, kind="b")
        assert c.value(kind="a") == 1
        assert c.value(kind="b") == 2

    def test_wrong_labels_are_dropped_not_recorded(self):
        c = metrics.Counter("t_wrong", "help", ("kind",))
        c.inc(other="x")
        assert c.value(kind="x") == 0

    def test_gauge_goes_up_and_down(self):
        g = metrics.Gauge("t_gauge", "help")
        g.inc(); g.inc(); g.dec()
        assert g.value() == 1

    def test_histogram_buckets_are_cumulative(self):
        h = metrics.Histogram("t_hist", "help", buckets=(1.0, 5.0, 10.0))
        for value in (0.5, 2.0, 7.0):
            h.observe(value)
        snapshot = h.snapshot()
        assert snapshot is not None
        assert snapshot.count == 3
        assert snapshot.buckets[1.0] == 1
        assert snapshot.buckets[5.0] == 2
        assert snapshot.buckets[10.0] == 3

    def test_histogram_ignores_nan_and_inf(self):
        h = metrics.Histogram("t_nan", "help")
        h.observe(float("nan"))
        h.observe(float("inf"))
        assert h.snapshot() is None

    def test_cardinality_is_capped(self):
        """The classic way a metrics layer takes down the monitoring system."""
        c = metrics.Counter("t_bomb", "help", ("id",))
        for i in range(metrics._MAX_SERIES_PER_METRIC + 50):
            c.inc(id=str(i))
        assert len(c._values) <= metrics._MAX_SERIES_PER_METRIC

    def test_status_class_buckets_codes(self):
        assert metrics.status_class(200) == "2xx"
        assert metrics.status_class(404) == "4xx"
        assert metrics.status_class(502) == "5xx"

    def test_unknown_paths_collapse_to_other(self):
        """An unbounded endpoint label is a cardinality bomb: a scanner probing
        random URLs would create one series per probe."""
        assert metrics.endpoint_label("/scan") == "/scan"
        assert metrics.endpoint_label("/wp-admin.php") == "other"
        assert metrics.endpoint_label("/../../etc/passwd") == "other"

    def test_exposition_format_is_parseable(self):
        c = metrics.Counter("t_expo", "a help string", ("kind",))
        c.inc(kind="x")
        lines = c.render()
        assert lines[0].startswith("# HELP t_expo")
        assert lines[1] == "# TYPE t_expo counter"
        assert 't_expo{kind="x"} 1' in lines

    def test_label_values_are_escaped(self):
        c = metrics.Counter("t_escape", "help", ("kind",))
        c.inc(kind='has"quote')
        assert any('\\"' in line for line in c.render())

    def test_registry_renders_all_metrics(self):
        output = metrics.render()
        assert "snapworth_http_requests_total" in output
        assert output.endswith("\n")


# ═══ Endpoints ════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    def test_liveness_checks_nothing_external(self):
        """A liveness probe that fails on a dependency outage causes the
        orchestrator to restart healthy containers — a crash-loop."""
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_legacy_health_still_works(self):
        assert client.get("/health").status_code in (200, 503)

    def test_readiness_reports_state(self):
        response = client.get("/health/ready")
        assert response.status_code in (200, 503)
        assert "ready" in response.json()

    def test_metrics_endpoint_serves_prometheus_format(self, metrics_auth):
        response = client.get("/metrics", headers=metrics_auth)
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "# TYPE" in response.text

    def test_metrics_endpoint_exposes_no_credentials(self, metrics_auth):
        """Redaction must be a no-op on the metrics body.

        Checked by running the body through the same redactor the log pipeline
        uses: if it changes anything, the exposition contains something
        credential-shaped. Substring matching on words like "token" would false-
        positive on legitimate names such as `model_tokens_total`, which counts
        tokens rather than containing one.
        """
        client.get("/health/live")
        body = client.get("/metrics", headers=metrics_auth).text
        assert obs.redact(body) == body, "metrics exposition contains a credential"

    def test_metric_labels_carry_no_user_identifiers(self, metrics_auth):
        """Every declared label must come from a closed set, never user input."""
        client.get("/health/live")
        client.post("/scan")
        forbidden = ("x-device-id", "subject=", "authorization")
        body = client.get("/metrics", headers=metrics_auth).text.lower()
        for marker in forbidden:
            assert marker not in body, f"metrics leaked {marker!r}"


class TestMetricsAccessControl:
    """`/metrics` publishes request volumes, error rates and model token usage.

    None of that is user data, but on a public host it is a free scale and cost
    oracle — and a live signal to anyone probing whether their traffic is
    landing. These tests pin the guard's contract.
    """

    def test_unconfigured_token_serves_nothing(self, monkeypatch):
        """Fails closed. The alternative — open when unset — would mean the
        guard silently does nothing on any host that forgot the variable."""
        monkeypatch.delenv("METRICS_TOKEN", raising=False)
        assert client.get("/metrics").status_code == 404

    def test_anonymous_request_is_refused(self, metrics_auth):
        response = client.get("/metrics")
        assert response.status_code == 404
        assert "# TYPE" not in response.text

    def test_wrong_token_is_refused(self, metrics_auth):
        response = client.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 404

    def test_refusal_does_not_disclose_the_endpoint_exists(self, metrics_auth):
        """404 rather than 401: an unauthorised caller learns nothing, and there
        is no WWW-Authenticate header advertising a token to guess at."""
        response = client.get("/metrics")
        assert response.status_code == 404
        assert "www-authenticate" not in {k.lower() for k in response.headers}

    def test_token_prefix_does_not_leak_by_comparison(self, metrics_auth):
        """A correct prefix must be refused exactly like a wrong first byte.

        Pins the use of compare_digest over `==`. This asserts the observable
        contract, not the timing itself — a wall-clock assertion would be flaky
        in CI and prove little.
        """
        for candidate in (METRICS_TOKEN[:-1], METRICS_TOKEN[:4], "x", ""):
            response = client.get(
                "/metrics", headers={"Authorization": f"Bearer {candidate}"})
            assert response.status_code == 404, f"accepted {candidate!r}"

    def test_non_ascii_token_is_refused_not_a_server_error(self, metrics_auth):
        """hmac.compare_digest raises TypeError on non-ASCII `str` input, so
        comparing as `str` would turn this header into a 500.

        Sent as raw bytes because that is what the wire carries: httpx refuses
        to ASCII-encode a `str` header, but Starlette decodes incoming header
        bytes as latin-1, so byte 0xE4 arrives in the handler as "ä". Passing a
        `str` here would test the client's encoder rather than our comparison.
        """
        response = client.get(
            "/metrics", headers={"Authorization": "Bearer ä".encode("latin-1")})
        assert response.status_code == 404

    def test_health_probes_stay_anonymous(self, metrics_auth):
        """Platform health checks cannot present a token, and these carry no
        competitive signal — guarding them would be an outage, not a fix."""
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code in (200, 503)


class TestRequestInstrumentation:
    def test_requests_are_counted(self):
        client.get("/health/live")
        assert metrics.http_requests.value(
            endpoint="/health/live", method="GET", status_class="2xx") >= 1

    def test_duration_is_observed(self):
        client.get("/health/live")
        snapshot = metrics.http_duration.snapshot(
            endpoint="/health/live", method="GET")
        assert snapshot is not None and snapshot.count >= 1

    def test_in_flight_returns_to_zero(self):
        """A leaked in-flight counter would make the shutdown drain hang until
        its deadline on every deploy."""
        client.get("/health/live")
        assert metrics.http_in_flight.value() == 0

    def test_unknown_path_does_not_create_a_series(self):
        client.get("/definitely-not-a-route")
        assert metrics.http_requests.value(
            endpoint="other", method="GET", status_class="4xx") >= 1

    def test_every_route_has_its_own_endpoint_label(self):
        """Four routes were missing from KNOWN_ENDPOINTS — Apple's server
        notifications among them — and were counted as "other", alongside
        every scanner probe."""
        from fastapi.routing import APIRoute

        routes = {r.path for r in app.routes if isinstance(r, APIRoute)}
        assert routes - metrics.KNOWN_ENDPOINTS == set()


class TestDeclaredMetricsAreIncremented:
    """A declared metric nothing increments reads as a flat zero on any
    dashboard built on it — "no problem" rather than "no data"."""

    def test_entitlement_outcomes_are_counted(self, monkeypatch):
        import auth
        import notify
        import referral
        from entitlements import Entitlement, EntitlementError

        ent = Entitlement("pro", "com.snapworth.yearly", None, "otid-1", "Production")

        async def record(subject, jws, device_id=None, authenticated=False):
            if jws == "bad":
                raise EntitlementError("signature did not verify")
            return ent

        async def quiet(*_args, **_kwargs):
            return False

        monkeypatch.setattr(auth.deps.entitlements, "record", record)
        monkeypatch.setattr(notify, "entitlement_recorded", quiet)
        monkeypatch.setattr(referral, "on_entitlement", quiet)
        headers = {"x-device-id": "entitlement-metric"}
        assert client.post("/auth/entitlement", json={"signed_transaction": "ok"},
                           headers=headers).status_code == 200
        assert client.post("/auth/entitlement", json={"signed_transaction": "bad"},
                           headers=headers).status_code == 400
        assert metrics.entitlement_operations.value(outcome="recorded") == 1
        assert metrics.entitlement_operations.value(outcome="rejected") == 1

    def test_the_never_incremented_cache_counter_is_gone(self):
        assert not hasattr(metrics, "cache_operations")
        assert "snapworth_cache_operations_total" not in metrics.render()


# ═══ Log redaction ════════════════════════════════════════════════════════════

class TestUvicornLoggingIsRedactedToo:
    """uvicorn keeps its own handlers, and they carried none of our filters.

    `Config.__init__` applies uvicorn's `LOGGING_CONFIG` before the app is
    imported, and that config gives `uvicorn` and `uvicorn.access` a handler
    each with `propagate: False`. `uvicorn.error` has no handler of its own, so
    its records reach `uvicorn`'s and stop there. So nothing uvicorn emitted —
    every unhandled-exception traceback, every access line's query string —
    ever reached a handler holding `RedactionFilter`.
    """

    def _replay_production_order(self):
        """uvicorn's dictConfig first, then ours. That is the real order."""
        import logging.config
        import uvicorn.config
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        obs.configure_production_logging()

    def test_every_uvicorn_logger_reaches_a_redacting_handler(self):
        self._replay_production_order()
        root_filters = {type(f).__name__
                        for h in logging.getLogger().handlers for f in h.filters}
        assert "RedactionFilter" in root_filters, root_filters

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logger = logging.getLogger(name)
            # Either it holds a redacting handler itself, or it propagates to
            # one. Asserting the reachable set rather than the mechanism, so a
            # different fix still satisfies this.
            reachable = set()
            current = logger
            while current:
                for handler in current.handlers:
                    reachable |= {type(f).__name__ for f in handler.filters}
                current = current.parent if current.propagate else None
            assert "RedactionFilter" in reachable, (
                f"{name} can log without redaction (reaches {reachable})")

    def test_one_shape_of_log_line_in_production(self):
        # Two formatters would mean two shapes of line in one stream, which is
        # what made the alternative fix (adding filters to uvicorn's own
        # handlers) the worse one.
        self._replay_production_order()
        for name in ("uvicorn", "uvicorn.access"):
            assert not logging.getLogger(name).handlers, (
                f"{name} still formats its own lines")


class TestRedaction:
    @pytest.mark.parametrize("secret,marker", [
        ("Authorization: Bearer abc123def456ghi789jkl", "Bearer <redacted>"),
        ("token=eyJhbGciOiJFUzI1NiJ9.abcdefghijklmnopqrst", "<jwt-redacted>"),
        ("key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456", "<api-key-redacted>"),
        ("redis://user:hunter2@cache:6379", "<redacted>@"),
        ("contact bob@example.com", "<email-redacted>"),
        ("subject " + "a" * 64, "<subject-redacted>"),
    ])
    def test_credentials_are_stripped(self, secret, marker):
        assert marker in obs.redact(secret)

    def test_private_key_body_is_stripped(self):
        pem = ("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN\n"
               "-----END PRIVATE KEY-----")
        assert "MIIEvQIBADAN" not in obs.redact(pem)

    def test_ordinary_text_is_untouched(self):
        text = "scan ok item=Patagonia Better Sweater value_low=45"
        assert obs.redact(text) == text

    def test_redaction_never_raises(self):
        assert obs.redact("") == ""
        # None deliberately — the test is named for it. redact() is called
        # from a logging filter, where a None message is ordinary.
        assert obs.redact(None) is None  # type: ignore[arg-type]

    def test_filter_redacts_message_and_extra(self, caplog):
        logger = logging.getLogger("test.redaction")
        logger.addFilter(obs.RedactionFilter())
        with caplog.at_level(logging.INFO, logger="test.redaction"):
            logger.info("failed with Bearer abcdefghijklmnop",
                        extra={"detail": "key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"})
        record = caplog.records[-1]
        assert "abcdefghijklmnop" not in record.getMessage()
        assert "AIzaSy" not in record.detail

    def test_filter_redacts_a_traceback(self, caplog):
        """The biggest carrier, and it was never covered.

        A Telegram token in an api.telegram.org URL, a password in a Redis
        connection error, a key echoed back by an HTTP client — all of it
        reached stdout verbatim, while the identical string passed as a plain
        `str` was correctly redacted. The unit tests passed because they fed
        strings.
        """
        logger = logging.getLogger("test.redaction.tb")
        logger.addFilter(obs.RedactionFilter())
        with caplog.at_level(logging.ERROR, logger="test.redaction.tb"):
            try:
                raise RuntimeError(
                    "POST https://api.telegram.org/bot123456789:AAtest-token-abcdefghijklmnopqrstuvwx/send failed")
            except RuntimeError:
                logger.exception("send failed")
        record = caplog.records[-1]
        assert record.exc_text, "traceback was never formatted for redaction"
        assert "AAtest-token-abcdefghijklmnopqrstuvwx" not in record.exc_text

    def test_filter_redacts_an_exception_passed_as_the_message(self, caplog):
        logger = logging.getLogger("test.redaction.msg")
        logger.addFilter(obs.RedactionFilter())
        exc = RuntimeError("key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456 rejected")
        with caplog.at_level(logging.ERROR, logger="test.redaction.msg"):
            logger.error(exc)
        assert "AIzaSy" not in caplog.records[-1].getMessage()

    def test_filter_redacts_inside_a_dict_extra(self, caplog):
        logger = logging.getLogger("test.redaction.dict")
        logger.addFilter(obs.RedactionFilter())
        with caplog.at_level(logging.INFO, logger="test.redaction.dict"):
            logger.info("upstream refused",
                        extra={"ctx": {"url": "redis://default:hunter2hunter2@10.0.0.1:6379",
                                       "attempt": 3}})
        ctx = caplog.records[-1].ctx
        assert "hunter2hunter2" not in ctx["url"]
        assert ctx["attempt"] == 3, "a numeric extra must stay a number"

    def test_numeric_extras_keep_their_type(self, caplog):
        """`observability` itself logs status, duration_ms and rate as numbers.
        Blanket-stringifying extras to redact them would change the shape of
        every structured line and break anything parsing them."""
        logger = logging.getLogger("test.redaction.nums")
        logger.addFilter(obs.RedactionFilter())
        with caplog.at_level(logging.INFO, logger="test.redaction.nums"):
            logger.info("done", extra={"status": 200, "duration_ms": 12.5, "ok": True})
        r = caplog.records[-1]
        assert r.status == 200 and isinstance(r.status, int)
        assert r.duration_ms == 12.5 and isinstance(r.duration_ms, float)
        assert r.ok is True

    def test_json_formatter_emits_the_redacted_traceback(self):
        record = logging.LogRecord("n", logging.ERROR, "p", 1, "boom", (), None)
        try:
            raise RuntimeError("Bearer abcdefghijklmnopqrstuvwxyz012345")
        except RuntimeError:
            import sys as _sys
            record.exc_info = _sys.exc_info()
        obs.RedactionFilter().filter(record)
        payload = json.loads(obs.JSONFormatter().format(record))
        assert "exception" in payload
        assert "abcdefghijklmnopqrstuvwxyz012345" not in payload["exception"]

    def test_filter_preserves_request_id(self):
        """Correlation ids are hex and must not be mistaken for secrets."""
        record = logging.LogRecord("n", logging.INFO, "p", 1, "msg", (), None)
        # LogRecord has no static request_id; RequestContextMiddleware adds
        # it at runtime, which is exactly what this asserts survives.
        record.request_id = "a1b2c3d4e5f60718"  # type: ignore[attr-defined]
        obs.RedactionFilter().filter(record)
        assert record.request_id == "a1b2c3d4e5f60718"  # type: ignore[attr-defined]


# ═══ Trace context ════════════════════════════════════════════════════════════

class TestTraceContext:
    def test_valid_traceparent_is_parsed(self):
        result = obs.parse_traceparent(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        assert result == ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")

    def test_malformed_traceparent_is_rejected(self):
        for bad in ("", "garbage", "00-short-00f067aa0ba902b7-01",
                    "99-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"):
            assert obs.parse_traceparent(bad) is None

    def test_all_zero_ids_are_invalid_per_spec(self):
        assert obs.parse_traceparent(f"00-{'0'*32}-{'0'*16}-01") is None


# ═══ Sampling ═════════════════════════════════════════════════════════════════

class TestSampling:
    def _record(self, level=logging.INFO):
        return logging.LogRecord("n", level, "p", 1, "m", (), None)

    def test_full_rate_keeps_everything(self):
        f = obs.SamplingFilter(1.0)
        assert all(f.filter(self._record()) for _ in range(20))

    def test_partial_rate_keeps_roughly_the_fraction(self):
        f = obs.SamplingFilter(0.1)
        kept = sum(1 for _ in range(100) if f.filter(self._record()))
        assert 8 <= kept <= 12

    def test_warnings_are_never_sampled_away(self):
        """A dropped error is an incident you cannot investigate."""
        f = obs.SamplingFilter(0.01)
        for level in (logging.WARNING, logging.ERROR, logging.CRITICAL):
            assert all(f.filter(self._record(level)) for _ in range(20))

    def test_zero_rate_drops_info_but_keeps_errors(self):
        f = obs.SamplingFilter(0.0)
        assert not f.filter(self._record(logging.INFO))
        assert f.filter(self._record(logging.ERROR))


# ═══ Error classification ═════════════════════════════════════════════════════

class TestErrorClassification:
    def test_client_errors_do_not_page(self):
        """4xx spikes from a misbehaving scraper must not wake anyone."""
        assert not obs.classify_status(400).pages
        assert not obs.classify_status(404).pages

    def test_dependency_and_internal_errors_page(self):
        assert obs.classify_status(502).pages
        assert obs.classify_status(500).pages

    def test_capacity_and_security_do_not_page(self):
        assert obs.classify_status(429) is obs.ErrorClass.CAPACITY
        assert obs.classify_status(402) is obs.ErrorClass.CAPACITY
        assert obs.classify_status(401) is obs.ErrorClass.SECURITY
        assert not obs.classify_status(429).pages


# ═══ Connection pooling ═══════════════════════════════════════════════════════

class TestDeviceCheckPooling:
    def test_client_is_reused(self):
        """Previously a new TLS handshake to Apple on every call."""
        async def run():
            a = await devicecheck._shared_client()
            b = await devicecheck._shared_client()
            try:
                assert a is b
            finally:
                await devicecheck.aclose()
        asyncio.run(run())

    def test_concurrent_creation_yields_one_client(self):
        async def run():
            clients = await asyncio.gather(
                *(devicecheck._shared_client() for _ in range(10)))
            try:
                assert len({id(c) for c in clients}) == 1
            finally:
                await devicecheck.aclose()
        asyncio.run(run())

    def test_close_is_idempotent(self):
        async def run():
            await devicecheck._shared_client()
            await devicecheck.aclose()
            await devicecheck.aclose()      # must not raise
        asyncio.run(run())


# PEM markers assembled at runtime. Written whole, a scanner cannot tell a
# fixture from a credential — this file cost a red build proving it.
_PEM_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
_PEM_END = "-----END " + "PRIVATE KEY-----"


class TestDeviceCheckVerify:
    """`is_configured` is three non-empty variables. `verify()` asks Apple.

    Apple checks the Authorization header before the request body, so a
    deliberately fake device token separates "the key signs" (400 — it got as
    far as the token) from "the key is wrong" (401). That distinction is the
    whole probe, and it needs no real device.
    """

    # A real P-256 key is needed here — pyjwt has to actually sign with it —
    # but it is *generated*, never written down. An embedded PEM literal is
    # indistinguishable from a leaked credential to any scanner: this file had
    # two, and gitleaks failed the build on them the first time it ran against
    # backend code. A generated key costs about a millisecond and can never be
    # mistaken for the real thing, in a scan or by a reader.
    @staticmethod
    def _key() -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        return ec.generate_private_key(ec.SECP256R1()).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    def client(self, handler, **kw):
        import httpx
        dc = devicecheck.DeviceCheckClient(
            team_id="TEAM123456", key_id="KEY1234567", private_key_pem=self._key(), **kw)
        devicecheck._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return dc

    def run(self, handler, **kw):
        async def go():
            dc = self.client(handler, **kw)
            try:
                return await dc.verify()
            finally:
                await devicecheck.aclose()
        return asyncio.run(go())

    def test_a_bad_token_with_a_good_key_is_a_pass(self):
        import httpx

        def apple(request):
            assert request.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(400, text="Missing or incorrectly formatted device token")

        ok, detail = self.run(apple)
        assert ok and detail == "credentials accepted by Apple"

    def test_a_401_names_the_variables_to_check(self):
        import httpx
        ok, detail = self.run(lambda r: httpx.Response(
            401, text="Unable to verify authorization token"))
        assert ok is False, "a refusal is a verdict, not an outage"
        assert "Apple said: Unable to verify authorization token" in detail, \
            "quote Apple rather than paraphrasing it"
        # The commonest cause is a KEY_ID left over from another key, which is
        # invisible unless the line says what it signed with.
        assert "team TEAM123456" in detail and "key KEY1234567" in detail
        assert "DeviceCheck capability" in detail

    def test_a_401_about_the_device_token_is_a_pass_not_a_rejection(self):
        """The probe assumes Apple answers a bad *device* token with 400. If it
        ever answers 401 instead, a good key would read as rejected and send
        someone hunting through the developer portal for nothing. Apple's own
        wording is what distinguishes the two, so it decides."""
        import httpx
        ok, detail = self.run(lambda r: httpx.Response(
            401, text="Unable to verify device token"))
        assert ok, "the authorization was accepted; only the fake token was not"
        assert "probe's fake device token" in detail
        assert "key rejected" not in detail

    def test_an_unexpected_status_is_reported_verbatim_not_swallowed(self):
        import httpx
        ok, detail = self.run(lambda r: httpx.Response(403, text="forbidden"))
        assert ok is False and detail == "unexpected HTTP 403: forbidden"

    def test_a_5xx_is_apple_unavailable_not_a_verdict_on_the_key(self):
        """Apple answering 503 says nothing about the key. The quota treats a
        5xx as an outage (`DeviceCheckError.is_refusal`), so the probe does."""
        import httpx
        ok, detail = self.run(lambda r: httpx.Response(503, text="try later"))
        assert ok is None
        assert detail == "HTTP 503: try later"

    def test_the_probe_token_is_never_a_real_one(self):
        """It must be valid base64 so it reaches Apple's token check rather
        than being rejected by our own shape guard."""
        assert devicecheck.DeviceCheckClient.looks_like_token(devicecheck._PROBE_TOKEN)

    def test_unconfigured_says_so_without_calling_apple(self):
        async def go():
            dc = devicecheck.DeviceCheckClient()      # nothing set
            return await dc.verify()
        ok, detail = asyncio.run(go())
        assert not ok and detail == "not configured"

    def test_a_broken_private_key_is_caught_not_raised(self):
        async def go():
            dc = devicecheck.DeviceCheckClient(
                team_id="TEAM123456", key_id="KEY1234567",
                private_key_pem=f"{_PEM_BEGIN}\nnope\n{_PEM_END}")
            return await dc.verify()
        ok, detail = asyncio.run(go())
        assert not ok
        assert "private key unreadable" in detail
        assert "Keys page" in detail, "say where a good key comes from"

    def test_a_flattened_pem_is_named_not_left_as_a_bare_valueerror(self):
        """A hosting panel that eats newlines is the commonest way this breaks,
        and cryptography raises the same ValueError for every malformed key —
        so the type alone tells whoever has to fix it nothing."""
        async def go():
            dc = devicecheck.DeviceCheckClient(
                team_id="TEAM123456", key_id="KEY1234567",
                # Shape only. `_key_problem` looks for BEGIN/END and then for
                # newlines — it never inspects the body — so the body is kept
                # short deliberately: gitleaks matches the PEM envelope on
                # sight, and a long filler between the markers reads to the
                # scanner exactly like a real key.
                private_key_pem=f"{_PEM_BEGIN} shape-only {_PEM_END}")
            return await dc.verify()
        ok, detail = asyncio.run(go())
        assert not ok
        assert "single line" in detail and "newlines were lost" in detail

    def test_a_bare_base64_body_says_to_paste_the_whole_file(self):
        async def go():
            dc = devicecheck.DeviceCheckClient(
                team_id="TEAM123456", key_id="KEY1234567",
                private_key_pem="A" * 64)   # body-shaped, no key material
            return await dc.verify()
        ok, detail = asyncio.run(go())
        assert not ok and "no BEGIN/END lines" in detail

    def test_a_key_that_signs_but_cannot_reach_apple_says_which_failed(self):
        """A network problem must never read as Apple refusing the key.

        It used to be `(False, "could not reach Apple (ConnectError)")`, and
        /checkup renders every False as "REJECTED … until this is fixed". None
        is the answer that says nothing was learned about the key."""
        import httpx

        def dead(request):
            raise httpx.ConnectError("no route to host")

        ok, detail = self.run(dead)
        assert ok is None
        assert detail == "ConnectError"

    def test_a_timeout_is_unreachable_too(self):
        import httpx

        def slow(request):
            raise httpx.ConnectTimeout("timed out")

        assert self.run(slow) == (None, "ConnectTimeout")

    def test_a_request_that_fails_for_another_reason_is_not_sent_not_refused(
            self, caplog):
        """Not the network, so a False — waiting will not cure it — but no
        answer from Apple was read, so not a refusal either. The detail says
        so in a form /checkup can tell apart, and the traceback, which the
        checkup line has no room for, is logged."""
        import httpx

        def garbled(request):
            raise httpx.DecodingError("Error -3 while decompressing data")

        with caplog.at_level("WARNING", logger="snapworth.devicecheck"):
            ok, detail = self.run(garbled)
        assert ok is False
        assert detail == f"{devicecheck.PROBE_NOT_SENT} (DecodingError)"
        record = next(r for r in caplog.records
                      if r.getMessage() == "devicecheck probe could not be sent")
        assert record.exc_info and record.exc_info[0] is httpx.DecodingError


# ═══ Container configuration ══════════════════════════════════════════════════

class TestDockerfile:
    @pytest.fixture(scope="class")
    def dockerfile(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "Dockerfile")
        with open(path) as handle:
            return handle.read()

    def test_runtime_matches_ci_python_version(self, dockerfile):
        """Testing on a runtime that never reaches production validates nothing."""
        assert "python:3.13" in dockerfile

    def test_runs_unprivileged(self, dockerfile):
        assert "USER snapworth" in dockerfile

    def test_uses_exec_so_sigterm_reaches_uvicorn(self, dockerfile):
        """Without exec, the shell is PID 1 and swallows SIGTERM — no graceful
        shutdown, so every deploy kills in-flight scans."""
        assert "exec uvicorn" in dockerfile

    def test_graceful_shutdown_window_exceeds_the_drain(self, dockerfile):
        assert "--timeout-graceful-shutdown" in dockerfile

    def test_healthcheck_targets_liveness_not_readiness(self, dockerfile):
        assert "/health/live" in dockerfile
        assert "HEALTHCHECK" in dockerfile


# ── AI provider quota exhaustion ─────────────────────────────────────────────
#
# Added after a live outage: the account's prepaid Gemini credits ran out, every
# /scan returned 502, and the app told users "temporarily unavailable, try again
# in a moment" while /health reported {"status": "ok", "ai_key_set": true}. Two
# separate defects — the hard stop was retried like a transient blip, and health
# only ever checked that a key was *configured*, never that the model answered.

class TestQuotaExhaustion:
    # Verbatim from the live failure, so a wording change upstream fails here
    # rather than silently reverting to the retry-forever behaviour.
    DEPLETED = (
        "429 Your prepayment credits are depleted. Please go to AI Studio at "
        "https://ai.studio/projects to manage your project and billing."
    )

    def test_depleted_credits_are_not_retryable(self):
        assert main._is_quota_exhausted(Exception(self.DEPLETED))
        assert not main._is_retryable(Exception(self.DEPLETED))

    def test_ordinary_rate_limit_is_still_retryable(self):
        # The regression this guards: matching on "429" or "quota" would make
        # every transient per-minute rate limit permanent, turning a blip that
        # clears in seconds into a failed scan. This is the old SDK's wording;
        # the current API's is below.
        transient = Exception(
            "429 Resource has been exhausted (e.g. check quota).")
        assert not main._is_quota_exhausted(transient)
        assert main._is_retryable(transient)

    # What google-genai raises for a Gemini 429: an `APIError` whose `details`
    # is the whole response body. The message is the same for every quota; the
    # QuotaFailure entry says which one ran out.
    _QUOTA_MESSAGE = (
        "You exceeded your current quota, please check your plan and billing "
        "details. For more information on this error, head to: "
        "https://ai.google.dev/gemini-api/docs/rate-limits.")

    @classmethod
    def _gemini_429(cls, quota_id: str, retry_delay: str = "23s"):
        from google.genai import errors

        return errors.ClientError(429, {"error": {
            "code": 429, "message": cls._QUOTA_MESSAGE, "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                 "violations": [{
                     "quotaMetric": "generativelanguage.googleapis.com/"
                                    "generate_content_paid_tier_requests",
                     "quotaId": quota_id,
                     "quotaDimensions": {"location": "global",
                                         "model": "gemini-2.5-flash"},
                     "quotaValue": "4000"}]},
                {"@type": "type.googleapis.com/google.rpc.Help",
                 "links": [{"description": "Learn more about Gemini API quotas",
                            "url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                 "retryDelay": retry_delay},
            ]}})

    def test_geminis_per_minute_429_is_a_rate_limit_not_a_billing_stop(self):
        """Its message opens "You exceeded your current quota", which was on
        the billing list filed as OpenAI's wording — so a burst got no retry,
        marked the model unhealthy on the first failure and paged the
        operator to top up billing."""
        per_minute = self._gemini_429("GenerateRequestsPerMinutePerProjectPerModel")
        assert not main._is_quota_exhausted(per_minute)
        assert main._is_retryable(per_minute)
        # Its body quotes a quota value of 4000, which the text markers read
        # as a 400; the status code is what counts when there is one.
        assert "400" in str(per_minute)

    def test_the_same_message_bare_is_not_a_billing_stop(self):
        assert not main._is_quota_exhausted(Exception(self._QUOTA_MESSAGE))
        assert main._is_retryable(Exception(self._QUOTA_MESSAGE))

    def test_a_per_day_quota_is_a_stop_for_today(self):
        per_day = self._gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                   retry_delay="41380s")
        assert main._is_quota_exhausted(per_day)
        assert not main._is_retryable(per_day)

    def test_auth_failures_remain_non_retryable(self):
        from google.genai import errors

        assert not main._is_retryable(Exception("401 API key not valid"))
        assert not main._is_retryable(errors.ClientError(400, {"error": {
            "code": 400, "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT"}}))

    def test_a_server_error_is_retryable(self):
        from google.genai import errors

        assert main._is_retryable(errors.ServerError(503, {"error": {
            "code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}))

    def test_a_per_minute_429_is_retried_and_does_not_page_about_billing(self):
        """End to end through the retry loop: a burst clears on the retry."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        ok = MagicMock()
        ok.text = "{}"
        health = main._ModelHealth()
        with patch("main._model") as model, \
                patch("main._RETRY_BASE_DELAY", 0), \
                patch.object(main, "_model_health", health), \
                patch("main.notify.model_unhealthy") as paged:
            model.generate_content_async = AsyncMock(side_effect=[
                self._gemini_429("GenerateRequestsPerMinutePerProjectPerModel",
                                 retry_delay="0s"), ok])
            text, _ = asyncio.run(main._generate_with_retry("prompt", label="scan"))
        assert text == "{}"
        assert model.generate_content_async.await_count == 2
        assert health.healthy
        paged.assert_not_called()


class TestModelCallDeadline:
    """The SDK's 25s timeout is per attempt, and there are two attempts, so a
    first attempt that timed out was followed by a second that ran to about
    50s — billed, for a phone that gives up at 35s."""

    @staticmethod
    def _run(side_effect, deadline_in: float | None):
        import time
        from unittest.mock import AsyncMock, patch

        with patch("main._model") as model, \
                patch("main._RETRY_BASE_DELAY", 0), \
                patch.object(main, "_model_health", main._ModelHealth()):
            model.generate_content_async = AsyncMock(side_effect=side_effect)
            deadline = None if deadline_in is None else time.monotonic() + deadline_in
            start = time.monotonic()
            with pytest.raises(main.aiconfig.ModelUnavailable):
                asyncio.run(main._generate_with_retry(
                    "prompt", label="scan", deadline=deadline))
        return model.generate_content_async.await_count, time.monotonic() - start

    @staticmethod
    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    def test_no_retry_when_too_little_time_is_left(self):
        calls, _ = self._run([Exception("503 overloaded")] * 2,
                             deadline_in=main._MIN_RETRY_SECONDS - 1)
        assert calls == 1

    def test_a_retry_with_time_left_still_happens(self):
        calls, _ = self._run([Exception("503 overloaded")] * 2,
                             deadline_in=main._MIN_RETRY_SECONDS + 5)
        assert calls == 2

    def test_an_attempt_is_cut_off_at_the_deadline(self):
        """The first attempt is bounded by the time left, not the SDK's 25s,
        and the retry after it is skipped."""
        calls, elapsed = self._run(self._hang, deadline_in=0.3)
        assert calls == 1
        assert elapsed < 5

    def test_a_retry_gets_only_the_time_that_is_left(self):
        from unittest.mock import patch

        attempts: list[int] = []

        async def fail_then_hang(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise Exception("503 overloaded")
            await asyncio.sleep(30)

        with patch("main._MIN_RETRY_SECONDS", 0.1):
            calls, elapsed = self._run(fail_then_hang, deadline_in=0.5)
        assert calls == 2
        assert elapsed < 5

    def test_nothing_is_called_once_the_deadline_has_passed(self):
        calls, _ = self._run([Exception("never reached")], deadline_in=-1)
        assert calls == 0

    def test_without_a_deadline_both_attempts_run(self):
        """The Telegram bot, the eval and /checkup pass none."""
        calls, _ = self._run([Exception("503 overloaded")] * 2, deadline_in=None)
        assert calls == 2

    # ── A missed deadline is not a provider failure ─────────────────────────
    # Both deadline stops used to fall through to the post-loop `exhausted`
    # exit: a health failure, a gemini dependency error and a `provider` scan
    # failure. Two slow uploads in a row turned /health degraded and paged the
    # operator while Gemini was answering normally.

    @staticmethod
    def _record(side_effect, deadline_in: float, runs: int = 2):
        """Run `runs` calls against one fresh health record; return it, the
        page mock, and every outcome and dependency error counted."""
        import time
        from unittest.mock import AsyncMock, patch

        health = main._ModelHealth()
        with patch("main._model") as model, \
                patch("main._RETRY_BASE_DELAY", 0), \
                patch.object(main, "_model_health", health), \
                patch("main.notify.model_unhealthy") as paged, \
                patch("main.metrics.model_calls.inc") as calls, \
                patch("main.metrics.dependency_errors.inc") as dependency:
            model.generate_content_async = AsyncMock(side_effect=side_effect)
            errors = []
            for _ in range(runs):
                with pytest.raises(main.aiconfig.ModelUnavailable) as raised:
                    asyncio.run(main._generate_with_retry(
                        "prompt", label="scan", deadline=time.monotonic() + deadline_in))
                errors.append(raised.value)
        outcomes = [c.kwargs.get("outcome") for c in calls.call_args_list]
        return health, paged, outcomes, dependency, errors[-1]

    def test_a_deadline_passed_before_the_call_leaves_the_provider_healthy(self):
        health, paged, outcomes, dependency, exc = self._record(
            [Exception("never reached")] * 2, deadline_in=-1)
        assert isinstance(exc, main._DeadlinePassed)
        assert health.healthy and health.consecutive_failures == 0
        paged.assert_not_called()
        dependency.assert_not_called()
        assert outcomes == ["deadline", "deadline"]

    def test_an_attempt_cut_at_the_deadline_leaves_the_provider_healthy(self):
        health, paged, outcomes, dependency, exc = self._record(
            self._hang, deadline_in=0.2)
        assert isinstance(exc, main._DeadlinePassed)
        assert health.healthy and health.consecutive_failures == 0
        paged.assert_not_called()
        dependency.assert_not_called()
        assert outcomes == ["deadline", "deadline"]

    def test_the_sdks_own_timeout_is_still_held_against_the_provider(self):
        """Only our cut is the caller's; a TimeoutError from the SDK with time
        still left is Gemini being slow, retried and then counted."""
        health, _paged, outcomes, dependency, exc = self._record(
            [TimeoutError("sdk read timeout")] * 2, deadline_in=60, runs=1)
        assert not isinstance(exc, main._DeadlinePassed)
        assert health.consecutive_failures == 1
        assert health.last_failure_kind == "exhausted"
        assert outcomes == ["exhausted"]
        dependency.assert_called_once_with(dependency="gemini", kind="exhausted")

    def test_a_scan_stopped_at_the_deadline_is_not_a_provider_failure(self):
        from unittest.mock import AsyncMock, patch

        from tests.test_ai_pipeline import V2_PAYLOAD, _scan_with

        for exc, kind in ((main._DeadlinePassed("late"), "deadline"),
                          (main.aiconfig.ModelUnavailable("503"), "provider")):
            with patch.object(main, "_generate_with_retry", AsyncMock(side_effect=exc)), \
                    patch("main.notify.count_scan_failure") as failed:
                assert _scan_with(V2_PAYLOAD).status_code == 502
            failed.assert_called_once_with(kind)

    def test_the_deadline_counts_from_arrival(self):
        from types import SimpleNamespace

        request = SimpleNamespace(state=SimpleNamespace(arrived=100.0))
        assert main._client_deadline(request) == 100.0 + main.CLIENT_DEADLINE_SECONDS  # type: ignore[arg-type]

    def test_scan_and_listing_pass_a_deadline_from_arrival(self):
        """Arrival is recorded by the metrics middleware, before the upload is
        read, so a slow upload spends the budget too."""
        import time
        from unittest.mock import AsyncMock, patch

        from tests.test_ai_pipeline import V2_PAYLOAD, _scan_with
        from tests.test_main import _post_listing

        seen: list[float] = []
        arrivals: list[object] = []
        real = main._client_deadline

        def recording(request):
            arrivals.append(getattr(request.state, "arrived", None))
            return real(request)

        async def fake(contents, *, label, max_tokens=None, record_health=True,
                       deadline=None):
            assert deadline is not None
            seen.append(deadline - time.monotonic())
            return json.dumps(V2_PAYLOAD), {}

        with patch.object(main, "_generate_with_retry", AsyncMock(side_effect=fake)), \
                patch.object(main, "_client_deadline", recording):
            assert _scan_with(V2_PAYLOAD, pro=True).status_code == 200
            assert _post_listing().status_code == 200
        assert len(seen) == 2
        assert all(isinstance(a, float) for a in arrivals), "middleware did not record arrival"
        for left in seen:
            assert main.CLIENT_DEADLINE_SECONDS - 5 < left <= main.CLIENT_DEADLINE_SECONDS


class TestModelHealth:
    def _fresh(self):
        return main._ModelHealth()

    def test_starts_healthy(self):
        assert self._fresh().healthy

    def test_quota_failure_is_unhealthy_immediately(self):
        # One sample is enough: depleted credits do not self-heal, so waiting
        # for a second failure only delays the signal.
        h = self._fresh()
        h.record_failure("quota_exhausted")
        assert not h.healthy
        assert h.snapshot()["last_failure_kind"] == "quota_exhausted"

    def test_single_generic_failure_does_not_trip(self):
        h = self._fresh()
        h.record_failure("exhausted")
        assert h.healthy

    def test_repeated_generic_failures_trip(self):
        h = self._fresh()
        for _ in range(main._MODEL_UNHEALTHY_AFTER):
            h.record_failure("exhausted")
        assert not h.healthy

    def test_success_clears_the_failure_state(self):
        h = self._fresh()
        h.record_failure("quota_exhausted")
        h.record_success()
        assert h.healthy
        assert h.snapshot()["last_failure_kind"] is None
        assert h.snapshot()["consecutive_failures"] == 0

    def test_snapshot_never_leaks_the_provider_message(self):
        # /health is unauthenticated and upstream errors quote project and
        # billing identifiers back at you.
        h = self._fresh()
        h.record_failure("quota_exhausted")
        assert "ai.studio" not in json.dumps(h.snapshot())
        assert "prepayment" not in json.dumps(h.snapshot()).lower()


class TestHealthReportsModelOutage:
    def test_health_is_degraded_when_the_model_is_down(self):
        main._model_health.record_failure("quota_exhausted")
        try:
            body = client.get("/health").json()
            assert body["model"]["healthy"] is False
            assert body["model"]["last_failure_kind"] == "quota_exhausted"
            assert body["status"] == "degraded"
        finally:
            main._model_health.record_success()

    def test_health_is_ok_once_the_model_answers(self):
        main._model_health.record_success()
        body = client.get("/health").json()
        assert body["model"]["healthy"] is True
        assert body["status"] == "ok"
