"""Tests for the second-hand clothing eval: the `/scan` client, the brand and
condition metrics, and (below) the dataset parser, splits and scoring.

No network and no backend: the client talks to an `httpx.MockTransport`, and
the dataset is a few JSON files written into `tmp_path`.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import metrics, scanclient
from tests.test_eval_intake import fake_jpeg


# ── the /scan client ─────────────────────────────────────────────────────────

SCAN_BODY = {"item_name": "Denim jacket", "brand": "Levi's", "category": "clothing",
             "condition_notes": "", "est_value_low_usd": 12.0, "est_value_high_usd": 30.0,
             "confidence": "Medium", "listing_title": "", "listing_description": "",
             "likely_price_usd": 18.0, "condition_grade": "good"}


class FakeBackend:
    """Answers `/health` and `/scan` from a script of responses, and records
    what each `/scan` request carried."""

    def __init__(self, script=None, commit="abc123"):
        self.script = list(script or [])
        self.commit = commit
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "commit": self.commit})
        assert request.url.path == "/scan"
        self.requests.append(request)
        step = self.script.pop(0) if self.script else (200, SCAN_BODY)
        if isinstance(step, Exception):
            raise step
        status, body, *headers = step
        return httpx.Response(status, json=body, headers=headers[0] if headers else None)


def client_for(backend: FakeBackend, tmp_path: Path | None = None, **kwargs):
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = scanclient.ScanClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(backend),
        cache_dir=tmp_path / "cache" if tmp_path else None, sleep=no_sleep, **kwargs)
    return client, sleeps


def run(client, requests):
    async def go():
        await client.read_commit()
        return await client.scan_all(requests)
    return asyncio.run(go())


def request(item_id="i1", photo=b"front", tag=None):
    return scanclient.ScanRequest(item_id=item_id, photo=photo, tag=tag)


class TestScanClient:
    def test_a_good_scan(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.body == SCAN_BODY and outcome.attempts == 1
        assert client.server_commit == "abc123"

    def test_the_tag_photo_goes_in_its_own_part(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        [outcome] = run(client, [request(tag=b"label")])
        body = backend.requests[0].content
        assert b'name="file"' in body and b'name="tag"' in body
        assert outcome.sent_tag

    def test_no_tag_part_without_a_tag(self):
        backend = FakeBackend()
        client, _ = client_for(backend)
        run(client, [request()])
        assert b'name="tag"' not in backend.requests[0].content

    def test_the_token_is_a_bearer_header_and_never_in_the_outcome(self):
        backend = FakeBackend()
        client, _ = client_for(backend, token="s3cret")
        [outcome] = run(client, [request()])
        assert backend.requests[0].headers["authorization"] == "Bearer s3cret"
        assert "s3cret" not in json.dumps(outcome.to_dict())

    def test_retries_a_5xx_then_succeeds(self):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"}), (200, SCAN_BODY)])
        client, sleeps = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.attempts == 2 and len(sleeps) == 1

    def test_retries_a_timeout(self):
        backend = FakeBackend([httpx.ReadTimeout("slow"), (200, SCAN_BODY)])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.ok and outcome.attempts == 2

    def test_honours_retry_after_on_429(self):
        backend = FakeBackend([(429, {"code": "rate_limited", "detail": "x"},
                                {"Retry-After": "7"}), (200, SCAN_BODY)])
        client, sleeps = client_for(backend)
        run(client, [request()])
        assert sleeps == [7.0]

    def test_gives_up_after_the_retries(self):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"})] * 10)
        client, _ = client_for(backend, retries=2)
        [outcome] = run(client, [request()])
        assert not outcome.ok and outcome.error == "ai_unavailable" and outcome.attempts == 3
        assert len(backend.requests) == 3

    def test_a_deterministic_refusal_is_not_retried(self):
        backend = FakeBackend([(422, {"code": "not_resalable", "detail": "x"})])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.error == "not_resalable" and outcome.attempts == 1

    def test_no_price_502_is_an_outcome_not_a_retry(self):
        backend = FakeBackend([(502, {"code": "ai_no_price", "detail": "x"})])
        client, _ = client_for(backend)
        [outcome] = run(client, [request()])
        assert outcome.error == "ai_no_price" and len(backend.requests) == 1

    def test_a_spent_allowance_halts_the_rest(self):
        backend = FakeBackend([(402, {"code": "quota_exhausted", "detail": "x"})])
        client, _ = client_for(backend, concurrency=1)
        outcomes = run(client, [request(f"i{n}", photo=bytes([n])) for n in range(4)])
        assert outcomes[0].error == "quota_exhausted"
        assert [o.error for o in outcomes[1:]] == ["halted:quota_exhausted"] * 3
        assert len(backend.requests) == 1

    def test_an_unparseable_200_is_counted(self):
        def answer(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(200, json={"commit": "c"})
            return httpx.Response(200, content=b"<html>")
        client = scanclient.ScanClient(base_url="http://127.0.0.1:8000",
                                       transport=httpx.MockTransport(answer))
        [outcome] = run(client, [request()])
        assert outcome.error == "unparseable" and not outcome.ok

    def test_results_keep_input_order(self):
        backend = FakeBackend()
        client, _ = client_for(backend, concurrency=3)
        outcomes = run(client, [request(f"i{n}", photo=bytes([n])) for n in range(6)])
        assert [o.item_id for o in outcomes] == [f"i{n}" for n in range(6)]


class TestCache:
    def test_a_rerun_makes_no_request(self, tmp_path):
        backend = FakeBackend()
        client, _ = client_for(backend, tmp_path)
        run(client, [request()])
        client2, _ = client_for(backend, tmp_path)
        [outcome] = run(client2, [request()])
        assert outcome.cached and outcome.ok and len(backend.requests) == 1

    def test_a_new_server_commit_misses(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        backend.commit = "def456"
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert not outcome.cached and len(backend.requests) == 2

    def test_a_different_tag_misses(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path)[0], [request(tag=b"label")])
        assert not outcome.cached

    def test_the_cache_tag_separates_runs(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path, cache_tag="a")[0], [request()])
        [outcome] = run(client_for(backend, tmp_path, cache_tag="b")[0], [request()])
        assert not outcome.cached

    def test_transient_failures_are_not_cached(self, tmp_path):
        backend = FakeBackend([(503, {"code": "ai_unavailable", "detail": "x"})] * 4)
        run(client_for(backend, tmp_path, retries=0)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path, retries=0)[0], [request()])
        assert not outcome.cached and len(backend.requests) == 2

    def test_a_deterministic_refusal_is_cached(self, tmp_path):
        backend = FakeBackend([(422, {"code": "not_resalable", "detail": "x"})])
        run(client_for(backend, tmp_path)[0], [request()])
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert outcome.cached and outcome.error == "not_resalable"

    def test_a_damaged_entry_is_a_miss(self, tmp_path):
        backend = FakeBackend()
        run(client_for(backend, tmp_path)[0], [request()])
        for path in (tmp_path / "cache").rglob("*.json"):
            path.write_text("{not json")
        [outcome] = run(client_for(backend, tmp_path)[0], [request()])
        assert outcome.ok and not outcome.cached


class TestPreparePhoto:
    def test_a_small_jpeg_is_sent_as_is(self, tmp_path):
        data = fake_jpeg(1280, 720, b"x")
        path = tmp_path / "a.jpg"
        path.write_bytes(data)
        assert scanclient.prepare_photo(path) == data

    def test_a_large_photo_is_resized_to_the_upload_size(self, tmp_path):
        from PIL import Image
        path = tmp_path / "big.jpg"
        Image.new("RGB", (1920, 1080), "white").save(path, format="JPEG")
        out = scanclient.prepare_photo(path)
        with Image.open(io.BytesIO(out)) as image:
            assert image.format == "JPEG" and max(image.size) == 1568


# ── brand and condition metrics ──────────────────────────────────────────────

class TestBrand:
    @pytest.mark.parametrize("predicted, truth, verdict", [
        ("H&M", "H & M", "exact"),
        ("HM", "H&M", "exact"),
        ("Fjällräven", "Fjallraven AB", "exact"),
        ("KAPPAHL", "KappAhl", "exact"),
        ("Dr. Martens", "Dr Martens", "exact"),
        ("Levi's", "Levi Strauss & Co.", "fuzzy"),
        ("Lindex", "Lindex Kids", "fuzzy"),
        ("Esprit", "Espirit", "fuzzy"),
        ("Gant", "Ganni", "wrong"),
        ("Nike", "Adidas", "wrong"),
        ("Unknown", "Nike", "abstained"),
        (None, "Nike", "abstained"),
    ])
    def test_verdicts(self, predicted, truth, verdict):
        assert metrics.brand_verdict(predicted, truth) == verdict

    def test_accuracy_counts_fuzzy_on_top_of_exact(self):
        result = metrics.brand_accuracy([("H&M", "H & M"), ("Levis", "Levi Strauss"),
                                         ("Gant", "Ganni"), ("Unknown", "Zara")])
        assert result is not None
        assert result["exact_accuracy"] == 0.25 and result["fuzzy_accuracy"] == 0.5
        assert result["abstained"] == 1 and result["wrong"] == 1
        assert result["precision_when_attempted"] == pytest.approx(2 / 3)

    def test_unknown_truths_are_not_scored(self):
        assert metrics.brand_accuracy([("Nike", "unknown"), ("Nike", "")]) is None


class TestCondition:
    def test_mae_and_confusion(self):
        result = metrics.condition_error([("new", 5), ("good", 4), ("used", 1), (None, 3)])
        assert result is not None
        assert result["mae"] == pytest.approx(2 / 3)
        assert result["exact"] == pytest.approx(1 / 3)
        assert result["abstained"] == 1 and result["scored"] == 3 and result["n"] == 4
        assert result["confusion"] == {"1→used": 1, "4→good": 1, "5→new": 1}

    def test_an_unknown_grade_is_an_abstention(self):
        result = metrics.condition_error([("mint", 5)])
        assert result is not None and result["mae"] is None and result["abstained"] == 1

    def test_out_of_scale_truths_are_skipped(self):
        assert metrics.condition_error([("good", 0), ("good", 7), ("good", None)]) is None
