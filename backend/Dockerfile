# Runtime must match the version CI tests against.
#
# This previously pinned 3.12 while both CI workflows tested on 3.13, so every
# test run validated a runtime that never reached production. Version drift
# between test and production is a class of bug that only surfaces live.
FROM python:3.13-slim

# Fail fast and log immediately:
#   PYTHONUNBUFFERED   — without it stdout is block-buffered when not a TTY, so
#                        container logs arrive in bursts and the last lines
#                        before a crash are lost, which is when they matter most.
#   PYTHONFAULTHANDLER — dumps a traceback on SIGSEGV/SIGABRT rather than
#                        exiting silently.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a source change does not invalidate the wheel layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run unprivileged. A container process running as root shares the host kernel,
# so a container escape starts from root. Created after COPY so the source is
# owned by root and not writable by the runtime user — the application never
# needs to modify its own code.
RUN useradd --system --create-home --uid 10001 snapworth \
    && chown -R snapworth:snapworth /app
USER snapworth

EXPOSE 8000

# Liveness only — deliberately NOT /health/ready. A container whose dependency
# is down is still alive, and restarting it turns a recoverable Redis blip into
# a fleet-wide crash-loop. Readiness is the load balancer's concern.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health/live', timeout=2).status==200 else 1)"

# `exec` so uvicorn becomes PID 1 and receives SIGTERM directly.
#
# Without it the shell is PID 1 and swallows the signal, so there is no graceful
# shutdown: in-flight scans are killed and Redis connections leaked on every
# deploy. The lifespan handler in main.py drains in-flight work on SIGTERM, and
# that only runs if the signal actually reaches the process.
#
# One worker per container is deliberate. The workload is I/O-bound on the model
# call, so a single event loop saturates it; scale by adding containers, which
# keeps per-container memory predictable for the autoscaler.
#
# --timeout-graceful-shutdown must exceed DRAIN_TIMEOUT_SECONDS (default 15s)
# and stay below the platform's SIGKILL grace period (Railway: 30s).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-graceful-shutdown 20 --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
