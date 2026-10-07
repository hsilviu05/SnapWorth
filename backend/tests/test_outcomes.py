"""The opt-in sale-outcomes receiver (#224).

Pinned here: exactly the documented fields are accepted and nothing else is
(no item name, photo, notes, paid price or device id); a sale keeps the
currency it was typed in; an edit replaces the same record; un-marking
deletes it; "Delete my shared sales" deletes every record the install made
and nobody else's; and the route refuses unattested, malformed, future-dated,
oversized or unknown-currency requests.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import outcomes  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

TOKEN_A = "a" * 40
TOKEN_B = "b" * 40


def run(coro):
    return asyncio.run(coro)


def bearer(subject: str = "attested-key-1") -> dict:
    token, _ = auth.deps.signer.mint(subject)
    return {"Authorization": f"Bearer {token}"}


def outcome(**overrides) -> dict:
    body = {
        "contribution_id": str(uuid.uuid4()),
        "contributor_token": TOKEN_A,
        "build": "23",
        "storefront": "USA",
        "scan_day": "2026-10-01",
        "prompt_version": "v2",
        "valuation_source": "model",
        "category": "clothing",
        "brand_identified": True,
        "condition_grade": "good",
        "condition_chosen": "good",
        "confidence_score": 72,
        "confidence_band": "Medium",
        "estimate_low": 20.0,
        "estimate_high": 40.0,
        "likely": 30.0,
        "expected": None,
        "sold_price": 35.0,
        "currency": "USD",
        "sold_day": "2026-10-04",
        "days_listed_to_sold": 3,
        "marketplace": "poshmark",
    }
    body.update(overrides)
    return body


def stored(contribution_id: str) -> dict | None:
    raw = run(auth.deps.cache.get(f"outcome:{contribution_id.lower()}"))
    return json.loads(raw) if raw else None


def post(body: dict, subject: str = "attested-key-1"):
    return client.post("/outcomes", json=body, headers=bearer(subject))


def delete(body: dict, subject: str = "attested-key-1"):
    return client.request("DELETE", "/outcomes", json=body, headers=bearer(subject))


# ── What is accepted ─────────────────────────────────────────────────────────

def test_the_documented_fields_are_stored_and_the_token_is_not():
    body = outcome()
    r = post(body)
    assert r.status_code == 200 and r.json() == {"stored": True, "created": True}
    record = stored(body["contribution_id"])
    assert record is not None
    assert "contributor_token" not in record
    assert record["contributor"] == outcomes.contributor_tag(TOKEN_A)
    assert TOKEN_A not in json.dumps(record)
    expected_keys = (set(outcomes.Outcome.model_fields) - {"contributor_token"}) | {
        "contributor", "received"}
    assert set(record) == expected_keys


@pytest.mark.parametrize("field, value", [
    ("item_name", "Patagonia Better Sweater"),
    ("photo", "base64..."),
    ("notes", "bought at Goodwill"),
    ("paid_price", 5.0),
    ("device_id", "ABC-123"),
    ("brand", "Patagonia"),
])
def test_anything_beyond_the_documented_fields_is_refused(field, value):
    body = outcome(**{field: value})
    assert post(body).status_code == 422
    assert stored(body["contribution_id"]) is None


def test_a_sale_keeps_the_currency_it_was_typed_in():
    body = outcome(currency="RON", sold_price=120.0, storefront="ROU")
    assert post(body).status_code == 200
    record = stored(body["contribution_id"])
    assert (record["currency"], record["sold_price"]) == ("RON", 120.0)


@pytest.mark.parametrize("overrides", [
    {"currency": "XYZ"},
    {"currency": "usd"},
    {"sold_price": 0},
    {"sold_price": 250_000},
    {"estimate_low": -1},
    {"sold_day": (date.today() + timedelta(days=5)).isoformat()},
    {"sold_day": "2019-01-01"},
    {"marketplace": "craigslist"},
    {"contribution_id": "not-an-id"},
    {"contributor_token": "short"},
    {"confidence_score": 101},
])
def test_malformed_values_are_refused(overrides):
    assert post(outcome(**overrides)).status_code == 422


def test_a_range_upside_down_is_refused():
    assert post(outcome(estimate_low=50.0, estimate_high=20.0)).status_code == 422


def test_the_values_the_app_sends_are_stored_as_sent():
    body = outcome(category="shoes", condition_grade="likeNew", condition_chosen="used",
                   confidence_band="High", prompt_version="v2.1")
    assert post(body).status_code == 200
    record = stored(body["contribution_id"])
    assert (record["category"], record["condition_grade"], record["condition_chosen"],
            record["confidence_band"], record["prompt_version"]) == (
        "shoes", "likeNew", "used", "High", "v2.1")


@pytest.mark.parametrize("field, sent, kept", [
    ("category", "Patagonia Better Sweater", "other"),
    ("condition_grade", "Jane's coat", None),
    ("condition_chosen", "Jane's coat", None),
    ("confidence_band", "Jane Doe", "unknown"),
    ("prompt_version", "jane.doe", "unknown"),
])
def test_a_naming_field_never_stores_free_text(field, sent, kept):
    """A modified client could otherwise store an item's name where a
    category goes. Normalised rather than refused: an old flip may carry a
    value from an earlier release, and its sale should still be shared."""
    body = outcome(**{field: sent})
    assert post(body).status_code == 200
    record = stored(body["contribution_id"])
    assert record[field] == kept
    assert sent not in json.dumps(record)


def test_a_band_in_another_case_is_kept_as_the_band():
    body = outcome(confidence_band="medium")
    assert post(body).status_code == 200
    assert stored(body["contribution_id"])["confidence_band"] == "Medium"


# ── Who may call ─────────────────────────────────────────────────────────────

def test_unattested_callers_are_refused():
    body = outcome()
    r = client.post("/outcomes", json=body, headers={"x-device-id": "any-string"})
    assert r.status_code == 401
    r = client.request("DELETE", "/outcomes", json={"contributor_token": TOKEN_A},
                       headers={"x-device-id": "any-string"})
    assert r.status_code == 401
    assert stored(body["contribution_id"]) is None


def test_one_install_has_a_daily_cap(monkeypatch):
    monkeypatch.setattr(outcomes, "DAILY_CAP", 2)
    assert post(outcome()).status_code == 200
    assert post(outcome()).status_code == 200
    assert post(outcome()).status_code == 429


# ── Lifecycle ────────────────────────────────────────────────────────────────

def test_an_edit_replaces_the_same_record():
    body = outcome()
    post(body)
    r = post({**body, "sold_price": 42.0})
    assert r.json() == {"stored": True, "created": False}
    assert stored(body["contribution_id"])["sold_price"] == 42.0
    lines = run(outcomes.export_jsonl()).strip().splitlines()
    assert len(lines) == 1, "an edit is not a second record"


def test_another_install_cannot_overwrite_or_delete_a_record():
    body = outcome()
    post(body)
    assert post({**body, "contributor_token": TOKEN_B, "sold_price": 1.0}).status_code == 403
    r = delete({"contributor_token": TOKEN_B, "contribution_id": body["contribution_id"]})
    assert r.status_code == 403
    assert stored(body["contribution_id"])["sold_price"] == 35.0


def test_unmarking_a_sale_deletes_its_record():
    body = outcome()
    post(body)
    r = delete({"contributor_token": TOKEN_A, "contribution_id": body["contribution_id"]})
    assert r.json() == {"deleted": 1}
    assert stored(body["contribution_id"]) is None


def test_delete_my_shared_sales_removes_every_record_of_the_install_and_no_other():
    mine = [outcome() for _ in range(3)]
    theirs = outcome(contributor_token=TOKEN_B)
    for body in mine:
        post(body)
    post(theirs, subject="attested-key-2")
    r = delete({"contributor_token": TOKEN_A})
    assert r.json() == {"deleted": 3}
    for body in mine:
        assert stored(body["contribution_id"]) is None
    assert stored(theirs["contribution_id"]) is not None


def test_the_export_has_every_live_record_and_no_contributor():
    kept, gone = outcome(), outcome()
    post(kept)
    post(gone)
    delete({"contributor_token": TOKEN_A, "contribution_id": gone["contribution_id"]})
    rows = [json.loads(line) for line in run(outcomes.export_jsonl()).splitlines()]
    assert [r["contribution_id"] for r in rows] == [kept["contribution_id"].lower()]
    assert "contributor" not in rows[0]
