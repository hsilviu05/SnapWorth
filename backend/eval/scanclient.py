"""A `/scan` client for the eval: concurrency, retries, timeouts, and a cache.

`eval.runner` calls the pipeline in-process. This goes over HTTP to a running
backend instead, so a run measures what `/scan` itself serves: image
validation, the tag photo's Pro gate, price bounds, Pro stripping. It is used
by `python -m eval.run`.

**The cache is what keeps a rerun from re-billing.** Every answer worth
keeping is written as one JSON file, keyed on the bytes sent, how they were
sent, and the backend's commit from `/health`. A rerun against the same
server reads those files and makes no model call. What is cached is a 200
and a refusal that the same bytes would earn again (a 400 image error, a 422,
`ai_no_price`); a timeout, a 5xx or a 429 is never cached, so it is retried
on the next run. A local server with uncommitted prompt edits reports the
same commit, so pass `--no-cache` (or a `cache_tag`) while iterating on one.

**What stops a run early.** A 401, 402 or 426 is a setup problem (no token,
the free allowance spent, a refused build), and every later request would get
the same answer while still costing a round trip. The first one halts the
client: the rest are recorded as `halted:<code>` without being sent.

The bearer token, when there is one, is sent and never logged or returned.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from eval import intake

#: The iOS client gives up at 35 s; a little over that, so a slow answer the
#: app would still have shown is not counted as a timeout.
DEFAULT_TIMEOUT_S = 40.0
DEFAULT_RETRIES = 3
DEFAULT_CONCURRENCY = 4
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0

#: Refusals the same bytes would earn again: safe to cache, never retried.
DETERMINISTIC_CODES = frozenset({
    "not_resalable", "photo_unusable", "ai_no_price",
    "image_empty", "image_type_unsupported", "image_unreadable",
    "image_too_large", "image_too_small",
})
#: Statuses that mean the run itself is misconfigured.
HALTING_STATUSES = frozenset({401, 402, 426})
#: Error codes that no later request will get past either: a local server
#: whose `REDIS_URL` names a Redis it cannot reach refuses every free scan.
HALTING_CODES = frozenset({"quota_unavailable"})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def prepare_photo(path: Path) -> bytes:
    return prepare_bytes(path.read_bytes())


def prepare_bytes(data: bytes) -> bytes:
    """The photo as the app would upload it: a JPEG with at most a 1568 px
    long edge at quality 80 (`intake.MAX_EDGE`, `intake.JPEG_QUALITY`).

    A JPEG already within that is sent byte for byte, as the intake does;
    anything larger, or not a JPEG, is re-encoded with Pillow.
    """
    size = intake.jpeg_size(data)
    if size is not None and max(size) <= intake.MAX_EDGE:
        return data
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((intake.MAX_EDGE, intake.MAX_EDGE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=intake.JPEG_QUALITY)
    return out.getvalue()


@dataclass(frozen=True)
class ScanRequest:
    item_id: str
    photo: bytes
    tag: bytes | None = None


@dataclass
class ScanOutcome:
    """What one item's request came back with.

    `body` is the response JSON for a 200, and the error body (`code`,
    `detail`) otherwise. `error` is None for a 200 and a short reason for
    everything else: the API's `code`, or `timeout`, `transport`,
    `unparseable`, `halted:<code>`.
    """
    item_id: str
    status: int | None = None
    body: dict | None = None
    error: str | None = None
    attempts: int = 0
    latency_ms: float | None = None
    cached: bool = False
    sent_tag: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and self.body is not None

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "status": self.status, "body": self.body,
                "error": self.error, "attempts": self.attempts,
                "latency_ms": self.latency_ms, "cached": self.cached,
                "sent_tag": self.sent_tag}


def cache_key(request: ScanRequest, server_commit: str, cache_tag: str = "") -> str:
    parts = [cache_tag, server_commit,
             hashlib.sha256(request.photo).hexdigest(),
             hashlib.sha256(request.tag).hexdigest() if request.tag else "no-tag"]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _code_of(body: dict | None) -> str | None:
    code = (body or {}).get("code")
    return code if isinstance(code, str) and code else None


def _cacheable(outcome: ScanOutcome) -> bool:
    if outcome.ok:
        return True
    return outcome.status in (400, 422, 502) and outcome.error in DETERMINISTIC_CODES


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


@dataclass
class ScanClient:
    base_url: str
    token: str | None = None
    concurrency: int = DEFAULT_CONCURRENCY
    timeout_s: float = DEFAULT_TIMEOUT_S
    retries: int = DEFAULT_RETRIES
    cache_dir: Path | None = None
    cache_tag: str = ""
    device_id: str = "eval-secondhand"
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    server_commit: str = "unknown"
    halted: str | None = field(default=None, init=False)
    #: False when `/health` could not be reached at all: no server is listening
    reachable: bool = field(default=True, init=False)

    def _headers(self) -> dict[str, str]:
        headers = {"x-device-id": self.device_id}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url.rstrip("/"),
                                 timeout=self.timeout_s, transport=self.transport)

    async def read_commit(self) -> str:
        """The backend's commit from `/health`, which keys the cache. It is read
        from a 503 too, which a server with an unreachable cache still answers
        with its commit. A server that reports none keys it as `unknown`."""
        async with self._client() as client:
            try:
                response = await client.get("/health")
                body = response.json()
                commit = body.get("commit") if isinstance(body, dict) else None
            except httpx.TransportError:
                commit, self.reachable = None, False
            except (httpx.HTTPError, ValueError, AttributeError):
                commit = None
        self.server_commit = str(commit or "unknown")
        return self.server_commit

    # ── cache ────────────────────────────────────────────────────────────────

    def _cache_path(self, request: ScanRequest) -> Path | None:
        if self.cache_dir is None:
            return None
        key = cache_key(request, self.server_commit, self.cache_tag)
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_read(self, request: ScanRequest) -> ScanOutcome | None:
        path = self._cache_path(request)
        if path is None or not path.is_file():
            return None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            return ScanOutcome(item_id=request.item_id, status=saved["status"],
                               body=saved["body"], error=saved["error"],
                               attempts=0, latency_ms=saved.get("latency_ms"),
                               cached=True, sent_tag=bool(saved.get("sent_tag")))
        except (OSError, ValueError, KeyError, TypeError):
            return None             # a damaged entry is a miss, not a crash

    def _cache_write(self, request: ScanRequest, outcome: ScanOutcome) -> None:
        path = self._cache_path(request)
        if path is None or not _cacheable(outcome):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"status": outcome.status, "body": outcome.body, "error": outcome.error,
                  "latency_ms": outcome.latency_ms, "sent_tag": outcome.sent_tag,
                  "server_commit": self.server_commit}
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    # ── requests ─────────────────────────────────────────────────────────────

    async def _send(self, client: httpx.AsyncClient, request: ScanRequest) -> ScanOutcome:
        outcome = ScanOutcome(item_id=request.item_id, sent_tag=request.tag is not None)
        files = {"file": ("photo.jpg", request.photo, "image/jpeg")}
        if request.tag is not None:
            files["tag"] = ("tag.jpg", request.tag, "image/jpeg")
        for attempt in range(1, self.retries + 2):
            if self.halted:
                outcome.error = f"halted:{self.halted}"
                return outcome
            outcome.attempts = attempt
            started = time.monotonic()
            wait: float | None = None
            try:
                response = await client.post("/scan", files=files, headers=self._headers())
            except httpx.TimeoutException:
                outcome.status, outcome.body, outcome.error = None, None, "timeout"
            except httpx.TransportError:
                outcome.status, outcome.body, outcome.error = None, None, "transport"
            else:
                outcome.latency_ms = (time.monotonic() - started) * 1000
                outcome.status = response.status_code
                try:
                    body = response.json()
                    outcome.body = body if isinstance(body, dict) else None
                except ValueError:
                    outcome.body = None
                if response.status_code == 200:
                    outcome.error = None if outcome.body is not None else "unparseable"
                    return outcome
                code = _code_of(outcome.body) or f"http_{response.status_code}"
                outcome.error = code
                if response.status_code in HALTING_STATUSES or code in HALTING_CODES:
                    self.halted = code
                    return outcome
                if code in DETERMINISTIC_CODES or response.status_code not in RETRYABLE_STATUSES:
                    return outcome
                if response.status_code == 429:
                    wait = _retry_after(response)
            if attempt > self.retries:
                return outcome
            if wait is None:
                wait = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
                wait *= 0.5 + random.random() / 2      # jitter: concurrent retries spread out
            await self.sleep(wait)
        return outcome

    async def scan_all(self, requests: list[ScanRequest],
                       on_done: Callable[[ScanOutcome], None] | None = None
                       ) -> list[ScanOutcome]:
        """Every request, at most `concurrency` in flight, in input order."""
        semaphore = asyncio.Semaphore(max(1, self.concurrency))

        async with self._client() as client:
            async def one(request: ScanRequest) -> ScanOutcome:
                cached = self._cache_read(request)
                if cached is not None:
                    outcome = cached
                else:
                    async with semaphore:
                        outcome = await self._send(client, request)
                    self._cache_write(request, outcome)
                if on_done is not None:
                    on_done(outcome)
                return outcome

            return list(await asyncio.gather(*(one(r) for r in requests)))
