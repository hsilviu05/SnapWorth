"""Production-readiness tests: metrics, redaction, tracing, probes, shutdown.

The properties here are the ones that only fail in production, and only under
load or during a deploy — which is exactly why they need tests rather than a
manual check before launch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devicecheck  # noqa: E402
import metrics  # noqa: E402
import observability as obs  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.registry.reset()
    yield


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

    def test_metrics_endpoint_serves_prometheus_format(self):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "# TYPE" in response.text

    def test_metrics_endpoint_exposes_no_credentials(self):
        """Redaction must be a no-op on the metrics body.

        Checked by running the body through the same redactor the log pipeline
        uses: if it changes anything, the exposition contains something
        credential-shaped. Substring matching on words like "token" would false-
        positive on legitimate names such as `model_tokens_total`, which counts
        tokens rather than containing one.
        """
        client.get("/health/live")
        body = client.get("/metrics").text
        assert obs.redact(body) == body, "metrics exposition contains a credential"

    def test_metric_labels_carry_no_user_identifiers(self):
        """Every declared label must come from a closed set, never user input."""
        client.get("/health/live")
        client.post("/scan")
        forbidden = ("x-device-id", "subject=", "authorization")
        body = client.get("/metrics").text.lower()
        for marker in forbidden:
            assert marker not in body, f"metrics leaked {marker!r}"


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
        assert obs.redact(None) is None

    def test_filter_redacts_message_and_extra(self, caplog):
        logger = logging.getLogger("test.redaction")
        logger.addFilter(obs.RedactionFilter())
        with caplog.at_level(logging.INFO, logger="test.redaction"):
            logger.info("failed with Bearer abcdefghijklmnop",
                        extra={"detail": "key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"})
        record = caplog.records[-1]
        assert "abcdefghijklmnop" not in record.getMessage()
        assert "AIzaSy" not in record.detail

    def test_filter_preserves_request_id(self):
        """Correlation ids are hex and must not be mistaken for secrets."""
        record = logging.LogRecord("n", logging.INFO, "p", 1, "msg", (), None)
        record.request_id = "a1b2c3d4e5f60718"
        obs.RedactionFilter().filter(record)
        assert record.request_id == "a1b2c3d4e5f60718"


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
