# Load test

How much `/scan` traffic one backend process carries, measured, not guessed.

```sh
cd backend
python3 -m loadtest.run                              # 0.5 → 20 scans/s, 45 s each
python3 -m loadtest.run --rates 5,40 --stage-seconds 30 --out /tmp/load.md
LOADTEST_MODEL_MEDIAN_S=22 python3 -m loadtest.run   # Gemini having a slow day
```

Needs `redis-server` on the PATH (`brew install redis`), or `--redis none`.

## What it runs

- **The real app.** `loadtest/server.py` imports `main.app` unchanged, with the
  Dockerfile's uvicorn flags (one worker, proxy headers). Only `main._model` is
  replaced: it waits as long as Gemini does (lognormal, median 11 s, p95 about
  19 s, the 7-day production figures in the Dockerfile) and returns a fixed
  valuation. No model call is made and nothing is billed.
- **Its own Redis**, started on a free port with production's `maxmemory` and
  `noeviction`, no persistence, and stopped afterwards.
- **A clean environment.** The server never reads `backend/.env`, which is a
  copy of the production config. Its `REDIS_URL` would aim the load at
  production Redis, and its Telegram token would page the operator.
- **Open-loop arrivals.** Each stage sends at a fixed rate whatever the server
  does, so a slowdown shows up as latency and errors instead of quietly
  lowering the load.
- **Every request is a new device from a new address**, the way a burst of
  new users arrives. The per-device and per-IP limits and the free allowance
  apply for real. A test from one device would only measure the rate limiter.

## What it reports

One row per stage: requests sent, outcomes by status and error code, peak
requests in flight, scan latency (p50/p95/max), `/health` latency while the
stage runs (how long anything else waits behind the event loop), and the
server's peak resident memory.

## What it does not cover

- Gemini's own quota (requests per minute and per day on the project's tier).
  That is the first real ceiling. Read it in AI Studio or the Cloud Console.
- Railway's edge, TLS, and the network between the phone and the server.
- App Attest token checks. The server runs with `REQUIRE_APP_ATTEST` off, so
  the scans arrive as unauthenticated free devices. A token check is a local
  signature verification plus one cache read.
- The client and server share one machine, so at the top stages the load
  generator competes with the server for CPU. The numbers are a lower bound.
