"""Request correlation and structured logging.

Every log line carries the request id, so a user-reported failure can be traced
through the whole request without guessing from timestamps. The id is accepted
from an inbound ``X-Request-ID`` header when present (so an edge proxy's id is
preserved) and echoed on the response.

Logs are emitted as JSON when ``LOG_FORMAT=json``, which is what a log
aggregator wants, and as human-readable lines otherwise for local development.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from enum import Enum

from starlette.middleware.base import BaseHTTPMiddleware

# Bound per request; read by the log filter below.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"


def current_request_id() -> str:
    return request_id_var.get()


class RequestIDFilter(logging.Filter):
    """Injects the current request id into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line."""

    # Attributes LogRecord always defines; anything else is caller-supplied
    # structured context and gets merged into the output.
    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "request_id", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIDFilter())
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s] — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and logs its completion."""

    def __init__(self, app, logger_name: str = "snapworth.access") -> None:
        super().__init__(app)
        self._log = logging.getLogger(logger_name)

    async def dispatch(self, request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        # Only trust an inbound id that looks like one — it lands in logs, so an
        # unbounded or newline-bearing value would be a log-injection vector.
        rid = incoming if (incoming and len(incoming) <= 64 and incoming.isprintable()
                           and "\n" not in incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            self._log.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path,
                       "duration_ms": round((time.monotonic() - start) * 1000, 1)},
            )
            request_id_var.reset(token)
            raise

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        response.headers[REQUEST_ID_HEADER] = rid
        # Health checks are high-frequency and uninteresting; keep them at DEBUG.
        level = logging.DEBUG if request.url.path == "/health" else logging.INFO
        self._log.log(
            level, "request",
            extra={"method": request.method, "path": request.url.path,
                   "status": response.status_code, "duration_ms": duration_ms},
        )
        request_id_var.reset(token)
        return response


# ═══════════════════════════════════════════════════════════════════════════
# PII redaction, trace context, sampling, error classification
# ═══════════════════════════════════════════════════════════════════════════
# Added for production launch. The request-id correlation above is unchanged;
# these layer on top of it.

import os
import re

# Patterns that must never reach a log aggregator.
#
# This is defence in depth, not the primary control. The primary control is that
# call sites don't log secrets — `auditlog.pseudonymise` already hashes subjects,
# and no code path logs a token deliberately. But logs are the one place where a
# well-meaning `extra={"error": str(exc)}` can carry a bearer token out of the
# system, because exception strings from HTTP clients routinely include headers.
#
# Ordered most-specific first: a JWT must match before the generic long-token
# pattern, or the replacement would be less informative.
_REDACTIONS: tuple[tuple[re.Pattern, str], ...] = (
    # Bearer tokens and our own two-part HMAC tokens.
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"), "<jwt-redacted>"),
    # Gemini / Google API keys.
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"), "<api-key-redacted>"),
    # Apple .p8 private key bodies.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "<private-key-redacted>"),
    # Redis / Postgres URLs carrying a password.
    (re.compile(r"(?i)\b(redis|rediss|postgres|postgresql)://[^:\s]+:[^@\s]+@"),
     r"\1://<redacted>@"),
    # Email addresses — support correspondence occasionally reaches a log line.
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email-redacted>"),
    # Raw App Attest key ids: 64 hex chars. Subjects should be pseudonymised by
    # `auditlog.pseudonymise`; this catches the paths that forgot.
    (re.compile(r"\b[a-f0-9]{64}\b"), "<subject-redacted>"),
)


def redact(text: str) -> str:
    """Strip credential-shaped substrings. Never raises."""
    if not text:
        return text
    try:
        for pattern, replacement in _REDACTIONS:
            text = pattern.sub(replacement, text)
    except Exception:          # pragma: no cover — redaction must never break logging
        return "<redaction-failed>"
    return text


class RedactionFilter(logging.Filter):
    """Applies `redact` to the message and to every string field in `extra`.

    Runs as a filter rather than inside the formatter so it applies to both the
    JSON and human-readable outputs, and so a future handler cannot bypass it.
    """

    _SKIP = {"request_id", "trace_id", "span_id"}

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: redact(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact(a) if isinstance(a, str) else a for a in record.args)
            for key, value in list(record.__dict__.items()):
                if key in self._SKIP or key.startswith("_"):
                    continue
                if isinstance(value, str):
                    record.__dict__[key] = redact(value)
        except Exception:      # pragma: no cover
            pass
        return True


# ── Trace context (W3C traceparent) ──────────────────────────────────────────
#
# OpenTelemetry readiness without the SDK. The `traceparent` header is a stable
# W3C standard, so propagating it now means an edge proxy or a future OTel
# collector can stitch our logs into a distributed trace with no code change.
#
# Deliberately NOT adding the opentelemetry packages: they pull a large
# dependency tree onto the request path, and until there is a collector to send
# spans to they would add cost for no signal. Parsing and propagating the header
# is the 20 lines that preserve the option.

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
span_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("span_id", default="")

TRACEPARENT_HEADER = "traceparent"
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def parse_traceparent(value: str) -> tuple[str, str] | None:
    """Return `(trace_id, parent_span_id)` from a W3C traceparent header."""
    if not value:
        return None
    match = _TRACEPARENT.match(value.strip().lower())
    if not match:
        return None
    trace_id, span_id = match.group(1), match.group(2)
    # All-zero ids are explicitly invalid per the spec.
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id


def current_trace_id() -> str:
    return trace_id_var.get()


class TraceIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get() or "-"
        record.span_id = span_id_var.get() or "-"
        return True


# ── Sampling ─────────────────────────────────────────────────────────────────

class SamplingFilter(logging.Filter):
    """Drops a fraction of records on a named logger.

    Exists for one specific problem: at a million users, access logs for
    high-frequency, low-information endpoints dominate log spend while telling
    you nothing an aggregate metric doesn't.

    Only ever samples records **below WARNING**. An error that is dropped is an
    incident you cannot investigate, and no cost saving justifies that.
    """

    def __init__(self, rate: float = 1.0, logger_name: str = "") -> None:
        super().__init__(logger_name)
        self.rate = max(0.0, min(1.0, rate))
        self._counter = 0
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING or self.rate >= 1.0:
            return True
        if self.rate <= 0.0:
            return False
        # Deterministic 1-in-N rather than random, so sampled volume is exactly
        # predictable and a low-rate sample cannot produce a burst by chance.
        step = max(1, round(1 / self.rate))
        with self._lock:
            self._counter += 1
            return self._counter % step == 0


# ── Error classification ─────────────────────────────────────────────────────

class ErrorClass(str, Enum):
    """Who is responsible, and does it page anyone.

    The distinction that matters operationally: `CLIENT` errors are the system
    working correctly and must never fire an alert, while `DEPENDENCY` and
    `INTERNAL` are ours. Conflating them is why 4xx spikes wake people up for a
    misbehaving scraper.
    """

    CLIENT = "client"              # 4xx — user or client error, not actionable
    DEPENDENCY = "dependency"      # upstream failed — actionable, often transient
    INTERNAL = "internal"          # our bug — always actionable
    CAPACITY = "capacity"          # rate limited / quota — expected under load
    SECURITY = "security"          # auth failures, rejected uploads

    @property
    def pages(self) -> bool:
        return self in {ErrorClass.DEPENDENCY, ErrorClass.INTERNAL}


def classify_status(status: int) -> ErrorClass:
    if status == 429:
        return ErrorClass.CAPACITY
    if status == 402:
        return ErrorClass.CAPACITY
    if status in {401, 403}:
        return ErrorClass.SECURITY
    if status in {502, 503, 504}:
        return ErrorClass.DEPENDENCY
    if status >= 500:
        return ErrorClass.INTERNAL
    if status >= 400:
        return ErrorClass.CLIENT
    return ErrorClass.CLIENT


def configure_production_logging(
    level: str = "INFO",
    json_output: bool = True,
    access_sample_rate: float = 1.0,
) -> None:
    """Configure logging with redaction, trace context and optional sampling.

    Additive to `configure_logging`: same handler shape, extra filters. Kept as
    a separate entry point so existing callers and tests are unaffected.
    """
    configure_logging(level=level, json_output=json_output)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RedactionFilter())
        handler.addFilter(TraceIDFilter())

    if access_sample_rate < 1.0:
        logging.getLogger("snapworth.access").addFilter(
            SamplingFilter(access_sample_rate))
        log_ = logging.getLogger("snapworth.observability")
        log_.info("access log sampling enabled", extra={"rate": access_sample_rate})
