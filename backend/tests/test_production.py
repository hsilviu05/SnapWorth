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
        assert not ok
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
        ok, detail = self.run(lambda r: httpx.Response(503, text="try later"))
        assert not ok and "503" in detail

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
        """A network problem must never read as Apple refusing the key."""
        import httpx

        def dead(request):
            raise httpx.ConnectError("no route to host")

        ok, detail = self.run(dead)
        assert not ok
        assert "could not reach Apple" in detail
        assert "rejected" not in detail


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
        # clears in seconds into a failed scan.
        transient = Exception(
            "429 Resource has been exhausted (e.g. check quota).")
        assert not main._is_quota_exhausted(transient)
        assert main._is_retryable(transient)

    def test_openai_style_wording_also_matches(self):
        assert main._is_quota_exhausted(
            Exception("You exceeded your current quota, please check your plan"))

    def test_auth_failures_remain_non_retryable(self):
        assert not main._is_retryable(Exception("401 API key not valid"))


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
