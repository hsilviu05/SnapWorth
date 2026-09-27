"""Prompt v2.1: v2 with the multiple-items rule back, the evidence ahead of the
prices, one market named, and the range asked for once.

What these pin, and why each matters more than the wording:

* v2.1 is selectable, and v1 and v2 are still byte for byte what they were, so
  `SCAN_PROMPT_VERSION` switches in both directions and a rollback serves the
  exact text the eval baselines were measured against.
* The output schema puts every piece of evidence before any price. The model
  attends to its own earlier tokens; evidence written after the price is a
  justification of it, and lowering the thinking budget would make that the
  only reasoning left.
* The v1 `est_value_low_usd`/`est_value_high_usd` pair is the server's to fill.
  v2 asked the model for it as a copy of worst/best and then ignored the copy.
* A reply that follows v2.1 serves exactly the shape installed clients decode.
"""

from __future__ import annotations

import hashlib
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aiconfig
import main
import prompts
import valuation
from eval import runner
from main import _ip_rate_store, _rate_store
from tests.images import VALID_JPEG, padded_image_bytes
from tests.test_ai_pipeline import V2_PAYLOAD, _client, _pro_headers
from tests.test_contract import CONTRACT, shape_differences


def _schema_keys(prompt: str) -> list[str]:
    """The output schema's keys, in the order the prompt asks for them.

    The schema block is valid JSON (placeholder values and all), so this reads
    it rather than pattern-matching the text around it.
    """
    start = prompt.index("{", prompt.index("## Output"))
    end = prompt.index("\n}", start) + 2
    return list(json.loads(prompt[start:end]))


def _rule(prompt: str, opening: str) -> str:
    """The one honesty-rule bullet that starts with `opening`."""
    (line,) = [ln for ln in prompt.splitlines() if ln.startswith(opening)]
    return line


V21 = prompts.SCAN_PROMPT_V2_1

#: A reply that follows v2.1 to the letter: its keys, in its order, and no
#: others — so no `est_value_*` pair for the server to fall back on.
V21_PAYLOAD = {key: V2_PAYLOAD[key] for key in _schema_keys(V21)}

PRICES = ("quick_sale_price_usd", "expected_price_usd",
          "best_case_price_usd", "worst_case_price_usd")

#: Everything the audit named as written after the prices in v2, plus the
#: identification and market reads the prices are meant to rest on.
EVIDENCE = ("visual_evidence", "assumptions", "identification_certainty",
            "uncertainty_factors", "item_name", "brand", "model", "variant",
            "size", "material", "era", "category", "condition_grade",
            "condition_notes", "authenticity_assessment", "authenticity_reasoning",
            "demand", "supply", "value_drivers")


# ── The registry ─────────────────────────────────────────────────────────────

class TestRegistry:
    def test_v21_is_selectable(self):
        assert prompts.get_prompt("v2.1") == (V21, "v2.1")
        # Read from an environment variable, so spelled however it was typed.
        assert prompts.get_prompt(" V2.1 ") == (V21, "v2.1")

    @pytest.mark.parametrize("version, digest", [
        ("v1", "751bbf604f8e7fd216a46fdde3d4a293c18f951df408d575ad4756db8b3a785b"),
        ("v2", "51fdbb531de3916eba6ecd570676f2d1b9259b1fdd247f7d522c0cab445c6061"),
    ])
    def test_the_rollback_prompts_are_untouched(self, version, digest):
        """v2.1 is a new entry, not an edit. If v2 changes, `SCAN_PROMPT_VERSION=v2`
        stops being a rollback to anything that was measured."""
        text, _ = prompts.get_prompt(version)
        assert hashlib.sha256(text.encode()).hexdigest() == digest


# ── The prompt text ──────────────────────────────────────────────────────────

class TestMultipleItems:
    """v1 had this rule and v2 dropped it (783aad3): a rack or a bin came back
    as one valuation of an unnamed item, or a lot price shown as one item's."""

    RULE = _rule(V21, "- **One item per valuation.**")

    def test_the_most_central_item_is_priced_and_named(self):
        assert "most central or prominent item" in self.RULE
        assert "`item_name`" in self.RULE
        assert "do not add their values together" in self.RULE

    def test_it_is_flagged_in_the_uncertainty_factors(self):
        assert prompts.MULTIPLE_ITEMS_FACTOR == "multiple items in frame"
        assert f'"{prompts.MULTIPLE_ITEMS_FACTOR}"' in self.RULE
        assert "`uncertainty_factors`" in self.RULE

    def test_it_caps_identification_certainty(self):
        assert '`identification_certainty` to "probable" at most' in self.RULE


class TestMarket:
    def test_the_market_is_named(self):
        assert prompts.VALUATION_MARKET == "US resale value, in USD"
        assert prompts.VALUATION_MARKET in V21
        assert prompts.VALUATION_MARKET in _rule(V21, "- **Prices are the")

    def test_the_venues_are_all_american(self):
        """v2's reseller bought at car boot sales and sold on Vinted: a UK
        market in the same breath as a US one, and the model free to drift."""
        assert "car boot" not in V21
        assert "Vinted" not in V21


class TestOutputSchema:
    KEYS = _schema_keys(V21)

    def test_every_piece_of_evidence_precedes_every_price(self):
        last_evidence = max(self.KEYS.index(k) for k in EVIDENCE)
        first_price = min(self.KEYS.index(k) for k in PRICES)
        assert last_evidence < first_price, self.KEYS

    def test_the_range_is_not_asked_for_twice(self):
        assert "est_value_low_usd" not in V21
        assert "est_value_high_usd" not in V21

    def test_nothing_else_v2_asked_for_was_dropped(self):
        v2 = set(_schema_keys(prompts.SCAN_PROMPT_V2))
        assert set(self.KEYS) == v2 - {"est_value_low_usd", "est_value_high_usd"}

    def test_every_field_the_server_reads_is_asked_for(self):
        assert set(valuation.EXPECTED_OPTIONAL_FIELDS) <= set(self.KEYS)
        assert set(valuation.V2_PRICE_FIELDS) <= set(self.KEYS)

    def test_the_v2_honesty_rules_carry_over(self):
        # `valuation.priced_as_unsellable` is written against this sentence.
        opening = "- **If this is not a resalable object**"
        assert _rule(V21, opening) == _rule(prompts.SCAN_PROMPT_V2, opening)
        assert "Never invent" in V21
        assert "do not attempt to rate the overall estimate" in V21


# ── Served ───────────────────────────────────────────────────────────────────

def _scan(monkeypatch, payload: dict, *, pro: bool = True):
    """POST /scan with `SCAN_PROMPT_VERSION=v2.1`; the response and model calls."""
    monkeypatch.setattr(main, "SCAN_PROMPT_VERSION", "v2.1")
    _rate_store.clear()
    _ip_rate_store.clear()
    reply = MagicMock()
    reply.text = json.dumps(payload)
    headers = {"x-device-id": "v21-contract"}
    if pro:
        headers |= _pro_headers("v21-contract-pro")
    with patch("main._model") as model:
        model.generate_content_async = AsyncMock(return_value=reply)
        result = _client.post(
            "/scan", headers=headers,
            files={"file": ("s.jpg", io.BytesIO(padded_image_bytes("JPEG", 1024)),
                            "image/jpeg")})
        calls = model.generate_content_async.await_args_list
    return result, calls


class TestServedAsV21:
    def test_the_switch_sends_v21_and_says_so(self, monkeypatch):
        result, calls = _scan(monkeypatch, V21_PAYLOAD)
        assert result.status_code == 200
        assert calls[0].args[0][0] == V21
        assert result.json()["prompt_version"] == "v2.1"

    def test_the_v1_range_is_derived_on_the_server(self, monkeypatch):
        body = _scan(monkeypatch, V21_PAYLOAD)[0].json()
        assert body["est_value_low_usd"] == body["worst_case_price_usd"] == 32
        assert body["est_value_high_usd"] == body["best_case_price_usd"] == 85
        assert body["expected_price_usd"] == 58

    @pytest.mark.parametrize("pro, fixture", [
        (True, "scan-response.json"), (False, "scan-response-free.json"),
    ])
    def test_the_contract_holds(self, monkeypatch, pro, fixture):
        """Every key and every JSON type installed clients decode, unchanged."""
        result, _ = _scan(monkeypatch, V21_PAYLOAD, pro=pro)
        assert result.status_code == 200
        served = result.json()
        assert shape_differences(json.loads((CONTRACT / fixture).read_text()), served) == []
        for required in ("item_name", "brand", "category", "condition_notes",
                         "est_value_low_usd", "est_value_high_usd", "confidence",
                         "listing_title", "listing_description"):
            assert served[required] is not None

    def test_a_deliberate_zero_is_still_a_decline(self, monkeypatch):
        """`priced_as_unsellable` needs all four prices present and zero — no
        longer backed up by a legacy pair, so this must hold on its own."""
        declined = {**V21_PAYLOAD, "category": "other", "item_name": "Plate of pasta",
                    "uncertainty_factors": ["This is a photograph of food"],
                    **{price: 0 for price in PRICES}}
        result, _ = _scan(monkeypatch, declined)
        assert result.status_code == 422
        assert "photograph of food" in result.json()["detail"]

    def test_a_multi_item_reply_reaches_the_user_flagged(self, monkeypatch):
        crowded = {**V21_PAYLOAD,
                   "item_name": "Patagonia Better Sweater 1/4-Zip (centre of rack)",
                   "identification_certainty": "probable",
                   "uncertainty_factors": [prompts.MULTIPLE_ITEMS_FACTOR,
                                           "reverse side not shown"]}
        body = _scan(monkeypatch, crowded)[0].json()
        assert body["uncertainty_factors"][0] == prompts.MULTIPLE_ITEMS_FACTOR
        assert body["identification_certainty"] == "probable"
        assert body["item_name"].startswith("Patagonia Better Sweater")


# ── Measurable before it is the default ──────────────────────────────────────

class TestTheEvalCanCompareIt:
    """`python -m eval.runner --photos … --compare v2 v2.1` is how v2.1 earns
    the default. This runs it end to end on the test fixtures with a stand-in
    model — the harness, not the prompts, is what it proves."""

    def test_the_unlabelled_mode_runs_v2_against_v21(self, tmp_path, monkeypatch):
        folder = tmp_path / "photos"
        folder.mkdir()
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            (folder / name).write_bytes(VALID_JPEG)

        seen: list[str] = []

        class Response:
            usage_metadata = None
            candidates: list = []

            def __init__(self, text: str):
                self.text = text

        class Model:
            async def generate_content_async(self, contents, generation_config=None):
                prompt = contents[0]
                seen.append(prompt)
                # Each arm answers in its own prompt's shape: v2 with the
                # legacy pair, v2.1 without it and a different headline.
                reply = (V2_PAYLOAD if prompt == prompts.SCAN_PROMPT_V2
                         else {**V21_PAYLOAD, "expected_price_usd": 64})
                return Response(json.dumps(reply))

        monkeypatch.setattr(aiconfig, "build_model", lambda *a, **k: Model())
        out = tmp_path / "run.json"

        code = runner.main(["--photos", str(folder), "--repeats", "2",
                            "--compare", "v2", "v2.1", "--json-out", str(out)])

        assert code == 0
        assert set(seen) == {prompts.SCAN_PROMPT_V2, V21}
        result = json.loads(out.read_text())
        for arm in ("v2", "v2.1"):
            assert result["arms"][arm]["metrics"]["scored_fraction"]["value"] == 100
        assert result["price_shift"]["n"] == 3
        assert result["price_shift"]["median_shift_pct"] == pytest.approx(
            (64 - 58) / 58 * 100)


def test_the_payload_fixture_is_what_v21_asks_for():
    """Guards the fixture above: were it to carry the legacy pair, every test
    here would pass on the fallback v2.1 exists to retire."""
    assert "est_value_low_usd" not in V21_PAYLOAD
    assert list(V21_PAYLOAD) == _schema_keys(V21)
