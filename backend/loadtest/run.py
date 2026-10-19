"""Load test: how much /scan traffic one backend process carries.

    cd backend && python3 -m loadtest.run                 # default stages
    python3 -m loadtest.run --rates 1,5,10 --stage-seconds 30

Starts a throwaway Redis and the real app (`loadtest/server.py`, the model
call faked at production latency) with the Dockerfile's uvicorn flags, then
sends /scan at a fixed arrival rate per stage. Each request is a new device
from a new address, so the per-device and per-IP limits apply exactly as they
do to real first scans rather than throttling the test. Prints one row per
stage; `--out` also writes the table to a file.

Nothing here reaches production or Gemini. The server gets a clean
environment, never `backend/.env`, and REDIS_URL points at the Redis this
script started (or none with `--redis none`).

What it does not measure: Gemini's own rate limits, Railway's edge, or
App Attest token verification (the test runs with REQUIRE_APP_ATTEST off,
as unauthenticated free devices).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent

#: The app's upload: longest edge 1568 px, JPEG quality 0.8
#: (ScanAPIClient.maxUploadEdge, encodeForUpload).
_UPLOAD_EDGE = 1568
#: How long the phone waits for /scan (Dockerfile, step 1).
_CLIENT_TIMEOUT_S = 35.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _photo_like_jpeg() -> bytes:
    """A JPEG about the size and shape of a real upload.

    Low-frequency colour with some grain, so it compresses like a photo of a
    garment rather than like a flat test image (a few KB) or pure noise (MBs).
    """
    from PIL import Image, ImageFilter

    rng = random.Random(7)
    w, h = _UPLOAD_EDGE * 3 // 4, _UPLOAD_EDGE
    small = Image.new("RGB", (48, 64))
    small.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                   for _ in range(48 * 64)])
    img = small.resize((w, h), Image.Resampling.BICUBIC)
    grain = Image.effect_noise((w, h), 40).convert("RGB")
    img = Image.blend(img, grain, 0.18).filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _rss_mb(pid: int) -> float | None:
    """Resident memory of `pid` in MB, via `ps` so it works on macOS too."""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) / 1024 if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


@dataclass
class Stage:
    rate: float
    latencies: list[float] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    health_ms: list[float] = field(default_factory=list)
    rss_mb: list[float] = field(default_factory=list)
    peak_in_flight: int = 0

    def count(self, key: str) -> None:
        self.statuses[key] = self.statuses.get(key, 0) + 1

    @property
    def sent(self) -> int:
        return sum(self.statuses.values())

    def row(self) -> str:
        ok = self.statuses.get("200", 0)
        others = ", ".join(f"{k}×{v}" for k, v in sorted(self.statuses.items()) if k != "200")
        return (f"| {self.rate:g} | {self.sent} | {ok} | {others or '-'} "
                f"| {self.peak_in_flight} "
                f"| {_pct(self.latencies, 50):.1f} | {_pct(self.latencies, 95):.1f} "
                f"| {max(self.latencies, default=float('nan')):.1f} "
                f"| {_pct(self.health_ms, 95):.0f} | {max(self.health_ms, default=float('nan')):.0f} "
                f"| {max(self.rss_mb, default=float('nan')):.0f} |")


HEADER = ("| Scans/s offered | Sent | 200 | Other outcomes | Peak in flight "
          "| p50 s | p95 s | max s | /health p95 ms | /health max ms | Peak RSS MB |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|")


class Runner:
    def __init__(self, base: str, server_pid: int, image: bytes) -> None:
        self.base = base
        self.pid = server_pid
        self.image = image
        self.in_flight = 0

    async def _one_scan(self, client: httpx.AsyncClient, stage: Stage) -> None:
        # A fresh device from a fresh address: 10.x.y.z, then the edge hop
        # Railway appends (ratelimit.client_ip keys on the first entry).
        ip = f"10.{random.randrange(256)}.{random.randrange(256)}.{random.randrange(1, 255)}"
        headers = {"x-device-id": f"loadtest-{uuid.uuid4()}",
                   "x-forwarded-for": f"{ip}, 95.173.0.1"}
        self.in_flight += 1
        stage.peak_in_flight = max(stage.peak_in_flight, self.in_flight)
        t0 = time.monotonic()
        try:
            r = await client.post(f"{self.base}/scan", headers=headers,
                                  files={"file": ("scan.jpg", self.image, "image/jpeg")})
            key = str(r.status_code)
            if r.status_code != 200:
                try:
                    code = r.json().get("code")
                except ValueError:
                    code = None
                key = f"{r.status_code} {code}" if code else key
            stage.count(key)
            if r.status_code == 200:
                stage.latencies.append(time.monotonic() - t0)
        except httpx.TimeoutException:
            stage.count("phone gave up (35 s)")
        except httpx.HTTPError as exc:
            stage.count(type(exc).__name__)
        finally:
            self.in_flight -= 1

    async def _sample(self, client: httpx.AsyncClient, stage: Stage, stop: asyncio.Event) -> None:
        """Every half second: /health latency (how blocked the event loop is) and RSS."""
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                await client.get(f"{self.base}/health", timeout=10)
                stage.health_ms.append((time.monotonic() - t0) * 1000)
            except httpx.HTTPError:
                stage.health_ms.append(10_000)
            rss = await asyncio.to_thread(_rss_mb, self.pid)
            if rss is not None:
                stage.rss_mb.append(rss)
            try:
                await asyncio.wait_for(stop.wait(), 0.5)
            except TimeoutError:
                pass

    async def stage(self, rate: float, seconds: float) -> Stage:
        stage = Stage(rate)
        limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
        timeout = httpx.Timeout(_CLIENT_TIMEOUT_S, connect=10)
        async with httpx.AsyncClient(limits=limits, timeout=timeout) as client, \
                httpx.AsyncClient(timeout=10) as probe:
            stop = asyncio.Event()
            sampler = asyncio.create_task(self._sample(probe, stage, stop))
            tasks: list[asyncio.Task[None]] = []
            end = time.monotonic() + seconds
            # Open loop: Poisson arrivals at `rate`, whatever the server does.
            # A closed loop would slow down with the server and hide the knee.
            while time.monotonic() < end:
                tasks.append(asyncio.create_task(self._one_scan(client, stage)))
                await asyncio.sleep(random.expovariate(rate))
            await asyncio.gather(*tasks)
            stop.set()
            await sampler
        return stage


def _start_redis(tmp: Path) -> tuple[subprocess.Popen[bytes], str]:
    exe = shutil.which("redis-server")
    if exe is None:
        sys.exit("redis-server is not installed; pass --redis none or --redis <url>")
    port = _free_port()
    # Production's settings (RUNBOOK §9, §11), minus persistence: nothing
    # here outlives the run.
    proc = subprocess.Popen(
        [exe, "--port", str(port), "--bind", "127.0.0.1", "--save", "",
         "--appendonly", "no", "--maxmemory", "768mb",
         "--maxmemory-policy", "noeviction", "--dir", str(tmp)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, f"redis://127.0.0.1:{port}/0"


def _start_server(port: int, redis_url: str, log_path: Path) -> subprocess.Popen[bytes]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(BACKEND),
        "GEMINI_API_KEY": "loadtest-not-real",
        "REDIS_URL": redis_url,
        # Each request is one scan from a new device, so the real free
        # allowance applies; set explicitly so a code default can't drift it.
        "FREE_SCANS_PER_DAY": "1",
    }
    for key in ("LOADTEST_MODEL_MEDIAN_S", "LOADTEST_MODEL_SIGMA", "LOADTEST_MODEL_CAP_S"):
        if key in os.environ:
            env[key] = os.environ[key]
    # The Dockerfile's command, on loopback.
    cmd = [sys.executable, "-m", "uvicorn", "loadtest.server:app",
           "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
           "--timeout-graceful-shutdown", "40", "--no-server-header",
           "--proxy-headers", "--forwarded-allow-ips=*"]
    log = open(log_path, "wb")
    return subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)


async def _wait_ready(base: str, server: subprocess.Popen[bytes]) -> None:
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(120):
            if server.poll() is not None:
                raise RuntimeError("the server exited during startup")
            try:
                if (await client.get(f"{base}/health/ready")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("the server was not ready after 60 s")


async def _main(args: argparse.Namespace) -> int:
    rates = [float(r) for r in args.rates.split(",") if r.strip()]
    image = _photo_like_jpeg()
    tmp = Path(tempfile.mkdtemp(prefix="snapworth-loadtest-"))
    redis_proc: subprocess.Popen[bytes] | None = None
    if args.redis == "spawn":
        redis_proc, redis_url = _start_redis(tmp)
    elif args.redis == "none":
        redis_url = ""
    else:
        redis_url = args.redis
    port = _free_port()
    log_path = tmp / "server.log"
    server = _start_server(port, redis_url, log_path)
    base = f"http://127.0.0.1:{port}"
    rows: list[str] = []
    try:
        await _wait_ready(base, server)
        print(f"upload {len(image) // 1024} KB, redis {'spawned' if redis_proc else redis_url or 'none'}, "
              f"server log {log_path}", flush=True)
        print(HEADER, flush=True)
        runner = Runner(base, server.pid, image)
        for rate in rates:
            stage = await runner.stage(rate, args.stage_seconds)
            rows.append(stage.row())
            print(rows[-1], flush=True)
            failed = stage.sent - stage.statuses.get("200", 0)
            if args.stop_at and stage.sent and failed / stage.sent > args.stop_at:
                print(f"stopping: {failed}/{stage.sent} failed", flush=True)
                break
    finally:
        server.terminate()
        try:
            server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            server.kill()
        if redis_proc is not None:
            redis_proc.terminate()
            redis_proc.wait(timeout=10)
    if args.out:
        Path(args.out).write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--rates", default="0.5,1,2,5,10,20",
                        help="scans per second, one stage each (default %(default)s)")
    parser.add_argument("--stage-seconds", type=float, default=45,
                        help="how long each stage sends for (default %(default)s)")
    parser.add_argument("--redis", default="spawn",
                        help="'spawn' a local redis-server (default), 'none' for "
                             "in-process memory, or a redis:// URL that is NOT production")
    parser.add_argument("--stop-at", type=float, default=0.5,
                        help="stop after a stage where more than this share failed")
    parser.add_argument("--out", help="also write the table to this file")
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
