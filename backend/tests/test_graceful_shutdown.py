"""A deploy's SIGTERM, against the real server running the real app.

Railway sends the old deployment SIGTERM once the new one is live, and SIGKILL
`RAILWAY_DEPLOYMENT_DRAINING_SECONDS` later (RUNBOOK §6). What a request in
flight gets in between is uvicorn's decision, not the app's: it stops
accepting, waits up to `--timeout-graceful-shutdown` for requests in flight,
cancels what is left, and only then runs `main._lifespan`'s shutdown, whose
DRAIN_TIMEOUT_SECONDS wait comes after all of that. The two windows add up,
which is why Railway's value has to cover both.

The server here is started with the Dockerfile's own command line, so the
numbers under test are the ones that ship. It runs in a subprocess because a
signal is the thing being tested; the app is `main.app` with one slow route
added, and never a developer's `.env`.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX signals and `sh -c exec`")

SLOW_APP = '''
import asyncio

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False   # never a developer's .env

from main import app  # noqa: E402


@app.get("/__test/slow")
async def slow(seconds: float) -> dict:
    print("slow request started", flush=True)
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        print("slow request cancelled", flush=True)
        raise
    print("slow request finished", flush=True)
    return {"slept": seconds}
'''


# ── The numbers, read from where they are set ────────────────────────────────

def _dockerfile_command() -> str:
    text = (BACKEND / "Dockerfile").read_text()
    match = re.search(r'CMD \["sh", "-c", "(exec uvicorn main:app [^"]+)"\]', text)
    assert match, "the Dockerfile's CMD is no longer `sh -c \"exec uvicorn main:app …\"`"
    return match.group(1)


def _graceful_seconds() -> int:
    match = re.search(r"--timeout-graceful-shutdown (\d+)", _dockerfile_command())
    assert match, "the Dockerfile no longer sets --timeout-graceful-shutdown"
    return int(match.group(1))


def _default(name: str) -> float:
    """A `float(os.environ.get(NAME, "n"))` default, from main.py's source:
    the imported value would reflect an override in this environment."""
    match = re.search(rf'os\.environ\.get\("{name}", "([\d.]+)"\)',
                      (BACKEND / "main.py").read_text())
    assert match, f"main.py no longer reads {name} with a literal default"
    return float(match.group(1))


def _runbook_section_6() -> str:
    text = (ROOT / "docs" / "RUNBOOK.md").read_text()
    return text[text.index("## 6. Deployment"):text.index("## 7. ")]


def _railway_draining_seconds() -> int:
    match = re.search(r"RAILWAY_DEPLOYMENT_DRAINING_SECONDS=(\d+)", _runbook_section_6())
    assert match, "RUNBOOK §6 no longer states RAILWAY_DEPLOYMENT_DRAINING_SECONDS=<n>"
    return int(match.group(1))


def _ci_stop_timeout() -> int:
    text = (ROOT / ".github" / "workflows" / "backend.yml").read_text()
    match = re.search(r"docker stop --timeout (\d+) snapworth-ci", text)
    assert match, "the container job no longer stops with `docker stop --timeout <n>`"
    return int(match.group(1))


class TestShutdownBudget:
    """One budget, stated in four places. A change to one without the others is
    how the Dockerfile came to promise a 30s Railway grace that is really 0."""

    def test_the_graceful_window_outlasts_every_request_still_awaited(self):
        # The app stops waiting on the model CLIENT_DEADLINE_SECONDS after a
        # request arrives, and the phone gives up at 35s. A window shorter than
        # that cancels a scan someone is still waiting for.
        assert _graceful_seconds() >= max(_default("CLIENT_DEADLINE_SECONDS"), 35)

    def test_sigkill_comes_after_the_graceful_window_and_the_drain(self):
        graceful, drain = _graceful_seconds(), _default("DRAIN_TIMEOUT_SECONDS")
        assert 0 < drain < graceful
        # They run one after the other, so Railway's value covers their sum,
        # with room for closing Redis and the HTTP clients.
        assert _railway_draining_seconds() >= graceful + drain + 1

    def test_ci_stops_the_container_on_railways_budget(self):
        assert _ci_stop_timeout() == _railway_draining_seconds()

    def test_runbook_states_the_numbers_that_ship(self):
        section = _runbook_section_6()
        assert f"--timeout-graceful-shutdown {_graceful_seconds()}" in section
        assert f"DRAIN_TIMEOUT_SECONDS={_default('DRAIN_TIMEOUT_SECONDS'):g}" in section


# ── The server ───────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(port: int, path: str, timeout: float) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _wait_for(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class _Server:
    def __init__(self, proc: subprocess.Popen, port: int, log: Path) -> None:
        self.proc, self.port, self._log = proc, port, log

    @property
    def output(self) -> str:
        return self._log.read_text(errors="replace")

    def position(self, needle: str) -> int:
        index = self.output.find(needle)
        assert index >= 0, f"{needle!r} not in the server's output:\n{self.output[-4000:]}"
        return index


@contextlib.contextmanager
def _serve(tmp_path: Path, graceful: int | None = None):
    """uvicorn as the Dockerfile starts it, serving `main.app` plus a slow route."""
    (tmp_path / "slowapp.py").write_text(SLOW_APP)
    command = _dockerfile_command().replace(
        "exec uvicorn main:app",
        f"exec {shlex.quote(sys.executable)} -m uvicorn slowapp:app", 1)
    command = command.replace("--host 0.0.0.0", "--host 127.0.0.1", 1)
    if graceful is not None:
        command = re.sub(r"--timeout-graceful-shutdown \d+",
                         f"--timeout-graceful-shutdown {graceful}", command)
    port = _free_port()
    # A clean environment: no REDIS_URL, no Telegram token, no ENVIRONMENT.
    # Inheriting a developer's shell could point this server at production.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(BACKEND)]),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PORT": str(port),
        "GEMINI_API_KEY": "ci-placeholder-not-real",
    }
    log = tmp_path / "server.log"
    with open(log, "wb") as sink:
        proc = subprocess.Popen(["sh", "-c", command], cwd=BACKEND, env=env,
                                stdout=sink, stderr=subprocess.STDOUT)
    server = _Server(proc, port, log)
    try:
        def live() -> bool:
            assert proc.poll() is None, f"server exited at startup:\n{server.output[-4000:]}"
            try:
                return _get(port, "/health/live", timeout=1)[0] == 200
            except OSError:
                return False
        _wait_for(live, 60, "the server to serve /health/live")
        yield server
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


class _InFlight(threading.Thread):
    """A request to the slow route, running while the test signals the server."""

    def __init__(self, port: int, seconds: float) -> None:
        super().__init__(daemon=True)
        self.port, self.seconds = port, seconds
        self.status: int | None = None
        self.body: bytes = b""
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.status, self.body = _get(
                self.port, f"/__test/slow?seconds={self.seconds}", timeout=self.seconds + 60)
        except BaseException as exc:          # the failure *is* the result
            self.error = exc


# How the process ends after a *graceful* SIGTERM: uvicorn finishes its
# shutdown, lifespan included, then raises the signal it caught again
# (`Server.capture_signals`), so the exit is by SIGTERM either way. Whether the
# shutdown was graceful is read from its output, not its exit status.
EXIT_AFTER_SIGTERM = -signal.SIGTERM


def _sigterm_mid_request(server: _Server, seconds: float) -> tuple[_InFlight, float]:
    request = _InFlight(server.port, seconds)
    request.start()
    _wait_for(lambda: "slow request started" in server.output, 30,
              "the request to reach the app")
    server.proc.send_signal(signal.SIGTERM)
    return request, time.monotonic()


class TestSigtermWithARequestInFlight:
    def test_a_request_that_finishes_inside_the_window_completes(self, tmp_path):
        """The acceptance test for #211: a scan in flight when the deploy lands
        is answered, as long as it ends inside the graceful window."""
        with _serve(tmp_path) as server:
            request, signalled = _sigterm_mid_request(server, seconds=3)
            request.join(timeout=_graceful_seconds() + 15)
            answered = time.monotonic()
            assert not request.is_alive()
            assert request.error is None, f"{request.error!r}\n{server.output[-4000:]}"
            assert request.status == 200
            assert json.loads(request.body) == {"slept": 3}
            # It really was in flight across the signal, not answered before it.
            assert answered - signalled > 1

            drain = _default("DRAIN_TIMEOUT_SECONDS")
            assert server.proc.wait(timeout=drain + 15) == EXIT_AFTER_SIGTERM
            # uvicorn waited for the request; the lifespan ran after it ended.
            assert server.position("Waiting for connections to close") \
                < server.position("slow request finished") \
                < server.position("shutdown: readiness withdrawn") \
                < server.position("shutdown complete")
            assert "still in flight after" not in server.output
            # The line RUNBOOK §6 tells the owner to read after a deploy.
            assert (f"startup complete — accepting traffic (replica -, "
                    f"shutdown drain {drain:g}s)") in server.output

    def test_one_that_outlasts_the_window_is_cancelled_and_cleaned_up_before_close(
            self, tmp_path):
        """The window is uvicorn's, and the drain comes after it. A request past
        the window is cancelled by uvicorn, runs its cleanup (for a scan, the
        free-scan refund) inside the lifespan's drain, and only then do the
        connections close. Graceful is cut to 1s here to keep the test short."""
        with _serve(tmp_path, graceful=1) as server:
            request, signalled = _sigterm_mid_request(server, seconds=60)
            drain = _default("DRAIN_TIMEOUT_SECONDS")
            assert server.proc.wait(timeout=1 + drain + 15) == EXIT_AFTER_SIGTERM
            request.join(timeout=10)
            assert request.error is not None or request.status != 200
            assert server.position("timeout graceful shutdown exceeded") \
                < server.position("slow request cancelled") \
                < server.position("shutdown complete")
            assert server.position("timeout graceful shutdown exceeded") \
                < server.position("shutdown: readiness withdrawn")
            assert "still in flight after" not in server.output
            assert time.monotonic() - signalled < 1 + drain + 15
