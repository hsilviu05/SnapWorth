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
import time
import uuid

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
