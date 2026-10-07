"""`tokens.signer_from_env`: the signing keys the service starts with.

Untested until AUDIT-2026-10-07 (M2, `tokens.py` at 70%), including its one
safety property: production refuses to start without `TOKEN_KEYS`, because an
ephemeral key signs every user out on each deploy and makes replicas reject
each other's tokens.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tokens  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    for name in ("TOKEN_KEYS", "TOKEN_CURRENT_KID", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)

    def configure(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
    return configure


@pytest.mark.parametrize("environment", ["production", "prod", "Production"])
def test_production_refuses_to_start_without_keys(env, environment):
    env(ENVIRONMENT=environment)
    with pytest.raises(RuntimeError, match="TOKEN_KEYS must be set in production"):
        tokens.signer_from_env()


def test_development_without_keys_signs_with_an_ephemeral_key(env):
    signer = tokens.signer_from_env()
    assert signer.current_kid == "dev"
    token, _ = signer.mint("subject")
    assert signer.verify(token)["sub"] == "subject"
    assert tokens.signer_from_env().active_kids == ["dev"]
    with pytest.raises(tokens.TokenError):
        tokens.signer_from_env().verify(token)        # a restart forgets it


def test_the_named_key_signs_and_every_key_verifies(env):
    env(TOKEN_KEYS="k1:first-secret, k2:second-secret", TOKEN_CURRENT_KID="k2",
        ENVIRONMENT="production")
    signer = tokens.signer_from_env()
    assert (signer.current_kid, signer.active_kids) == ("k2", ["k1", "k2"])
    _, claims = signer.mint("subject")
    assert claims["kid"] == "k2"

    older = tokens.TokenSigner({"k1": b"first-secret"}, "k1")
    token, _ = older.mint("subject")
    assert signer.verify(token)["kid"] == "k1", "a rotated-out key still verifies"


def test_an_unknown_current_kid_falls_back_to_the_first_key(env):
    env(TOKEN_KEYS="b:two,a:one", TOKEN_CURRENT_KID="missing")
    assert tokens.signer_from_env().current_kid == "a"


def test_malformed_pairs_are_skipped(env):
    env(TOKEN_KEYS="nocolon, :nokid, empty: , ok:secret", ENVIRONMENT="production")
    signer = tokens.signer_from_env()
    assert signer.active_kids == ["ok"]
