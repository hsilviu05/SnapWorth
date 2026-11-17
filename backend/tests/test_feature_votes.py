"""The in-app feature vote (`POST /events/vote`) and the bot's `/votes`.

Pinned here: only the current round and a known choice are accepted, only an
attested device is counted, a device counts once per round however often it
sends, and the tallies reach `/votes`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from ops import notify, opsstats  # noqa: E402
from security import auth  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

SUBJECT = "attested-key-vote"
ROUND = opsstats.FEATURE_VOTE_ROUND


def bearer(subject: str = SUBJECT) -> dict:
    token, _ = auth.deps.signer.mint(subject)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def voted(monkeypatch) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notify, "feature_voted", lambda s, r, c: calls.append((s, r, c)))
    return calls


class TestRoute:
    def test_counts_an_attested_vote(self, voted):
        r = client.post("/events/vote", json={"choice": "barcode", "round": ROUND},
                        headers=bearer())
        assert r.status_code == 204
        assert voted == [(SUBJECT, ROUND, "barcode")]

    @pytest.mark.parametrize("body", [
        {"choice": "made_up", "round": ROUND},
        {"choice": "barcode", "round": "2020-01"},
        {"choice": "barcode", "round": ROUND, "name": "x"},
        {"choice": "barcode"},
    ])
    def test_refuses_anything_but_a_known_choice_in_this_round(self, voted, body):
        r = client.post("/events/vote", json=body, headers=bearer())
        assert r.status_code == 422
        assert voted == []

    def test_a_legacy_caller_is_not_counted(self, voted):
        r = client.post("/events/vote", json={"choice": "other", "round": ROUND},
                        headers={"x-device-id": "someone"})
        assert r.status_code in (204, 401)
        assert voted == []


class TestCounting:
    @pytest.fixture
    def cache(self, monkeypatch) -> ResilientCache:
        cache = ResilientCache(None, InMemoryCache())
        monkeypatch.setattr(notify, "_cache", cache)
        opsstats.bind(cache)
        yield cache
        opsstats.bind(None)

    def test_one_vote_per_device_per_round(self, cache):
        async def go():
            for subject, choice in [("a", "barcode"), ("a", "barcode"),
                                    ("a", "shipping"), ("b", "shipping"), ("c", "other")]:
                await notify._note_feature_vote(subject, ROUND, choice)
            return await opsstats.feature_vote_tallies()
        tallies = asyncio.run(go())
        assert tallies == {"sold_prices": 0, "shipping": 1, "ebay_post": 0,
                           "barcode": 1, "other": 1}

    def test_an_unknown_choice_is_never_a_counter(self, cache):
        notify.feature_voted("a", ROUND, "made_up")
        notify.feature_voted("a", "2020-01", "barcode")
        assert asyncio.run(cache.get(opsstats.feature_vote_key(ROUND, "made_up"))) is None
        assert asyncio.run(cache.get(opsstats.feature_vote_key("2020-01", "barcode"))) is None

    def test_votes_text_ranks_choices_with_shares(self, cache):
        async def go():
            await cache.set(opsstats.feature_vote_key(ROUND, "shipping"), "3")
            await cache.set(opsstats.feature_vote_key(ROUND, "barcode"), "1")
            return await notify._votes_text()
        text = asyncio.run(go())
        assert text.index("Shipping costs in profit") < text.index("Barcode scanning")
        assert "   3   75%  Shipping costs in profit" in text
        assert "4 devices voted, one vote each." in text

    def test_votes_text_before_any_vote(self, cache):
        assert "No votes yet" in asyncio.run(notify._votes_text())

    def test_votes_is_a_command(self):
        assert "votes" in dict(notify.COMMANDS)
        for choice in opsstats.FEATURE_VOTE_CHOICES:
            assert choice in notify.VOTE_LABELS


def test_the_app_and_the_server_agree_on_the_round_and_the_choices():
    swift = (Path(__file__).resolve().parents[2]
             / "ios/SnapWorth/Views/Flips/HistoryView.swift").read_text()
    assert f'static let round = "{ROUND}"' in swift
    for choice in opsstats.FEATURE_VOTE_CHOICES:
        if choice == "other":
            assert 'static let otherChoice = "other"' in swift
        elif "_" in choice:
            assert f'= "{choice}"' in swift, choice
        else:
            assert f"case {choice}\n" in swift, choice
