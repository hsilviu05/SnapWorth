"""A published AUDIT_SALT is said out loud at startup, and nothing more.

`auditlog` falls back to `snapworth-audit-v1` when AUDIT_SALT is unset, a
literal in this public repository, and `.env.example` suggests
`change-me-in-production`. With either, audit pseudonyms and the device tags
/trends keeps beside scanned items can be recomputed from a key id. TOKEN_KEYS
refuses to boot without a value; nothing looked at the salt. It now logs one
ERROR in production, and does not refuse to boot: production may be running
on the default today, and a refusal would take the API down at the next
deploy. Checkup's line for the same finding is tested in `test_notify`.

The value must appear in no output. The ERROR is a fixed string, with nothing
interpolated, so nothing derived from the salt can reach it either.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auditlog  # noqa: E402
import main  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERROR_TEXT = "AUDIT_SALT is unset or a placeholder"
PUBLISHED = [auditlog._DEFAULT_SALT, "change-me-in-production"]


class TestTheStartupWarning:
    def _run(self, monkeypatch, caplog, *, environment: str, salt: str) -> list[logging.LogRecord]:
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.setattr(auditlog, "_SALT", salt.encode())
        with caplog.at_level(logging.DEBUG):
            main._warn_if_audit_salt_is_public()
        return caplog.records

    @pytest.mark.parametrize("salt", PUBLISHED)
    def test_a_published_salt_in_production_is_an_error(self, monkeypatch, caplog, salt):
        [record] = self._run(monkeypatch, caplog, environment="production", salt=salt)
        assert record.levelno == logging.ERROR
        assert record.getMessage().startswith(ERROR_TEXT)
        assert not record.args, "nothing may be interpolated into it"
        assert salt not in caplog.text

    def test_a_random_salt_in_production_says_nothing(self, monkeypatch, caplog):
        salt = secrets.token_urlsafe(32)
        assert self._run(monkeypatch, caplog, environment="production", salt=salt) == []

    def test_outside_production_the_default_is_expected(self, monkeypatch, caplog):
        """Tests, CI and a developer's machine run on the default by design."""
        assert self._run(monkeypatch, caplog, environment="development",
                         salt=auditlog._DEFAULT_SALT) == []


# A real startup: the salt is read from the environment at import, and the
# warning comes from the lifespan. `load_dotenv` is neutralised as conftest
# does, so a developer's `.env` cannot stand in for the value under test, and
# the Telegram and Redis variables are dropped so the probe sends nothing and
# connects to nothing.
_PROBE = """
import sys
sys.path.insert(0, %r)
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from fastapi.testclient import TestClient
import main
with TestClient(main.app) as c:
    print("ready", c.get("/health/ready").status_code)
"""


def _start_in_production(salt: str | None) -> str:
    env = {k: v for k, v in os.environ.items()
           if k not in {"AUDIT_SALT", "LOG_FORMAT"}
           and not k.startswith(("REDIS_", "TELEGRAM_"))}
    env.update(ENVIRONMENT="production",
               TOKEN_KEYS="k1:test-key-material-not-a-real-secret")
    if salt is not None:
        env["AUDIT_SALT"] = salt
    out = subprocess.run([sys.executable, "-c", _PROBE % BACKEND], capture_output=True,
                         text=True, env=env, timeout=120)
    output = out.stdout + out.stderr
    assert out.returncode == 0, output[-2000:]
    # Not refused: the API boots and serves, whatever the salt.
    assert "ready 200" in out.stdout, output[-2000:]
    return output


class TestARealStartup:
    @pytest.mark.parametrize("salt", [None, "change-me-in-production"],
                             ids=["unset", "the .env.example placeholder"])
    def test_production_on_a_published_salt_boots_and_logs_it(self, salt):
        output = _start_in_production(salt)
        assert output.count(ERROR_TEXT) == 1, output[-2000:]
        assert (salt or auditlog._DEFAULT_SALT) not in output

    def test_production_on_a_random_salt_boots_quietly(self):
        salt = secrets.token_urlsafe(32)
        output = _start_in_production(salt)
        assert ERROR_TEXT not in output
        assert salt not in output
