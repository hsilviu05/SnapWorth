"""Tests for the gold-set review (#213).

Review is the step that turns a draft into a record that counts, so these pin
what it must never do: approve without a reviewer, approve `certain`/`high`
without evidence, take a web address as evidence, keep a currency the app only
assumed, approve a record whose photo is missing, revisit an approved record,
or disturb the lines it did not decide. Input is scripted; nothing is opened.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import review, schema

COMMENT = "# gold set — labels only; photos live in the private store"


def draft(record_id: str, **overrides) -> dict:
    raw = {
        "id": record_id,
        "images": [{"path": f"images/{record_id}-front.jpg", "angle": "front",
                    "is_primary": True, "sha256": "ab" * 32}],
        "actual_sale_price": 35.0, "currency": "USD", "category": "clothing",
        "brand": "Levi's", "model": "501", "condition": "good",
        "sold_date": "2026-09-14", "marketplace": "ebay", "region": "US",
        "label_confidence": "medium", "evidence_note": "", "review_state": "draft",
        "difficulty": "typical", "tags": ["my_flips", "stored_1024", "currency_assumed"],
        "human_notes": "", "schema_version": schema.SCHEMA_VERSION,
        "created_at": "2026-10-23T12:00:00+00:00",
    }
    raw.update(overrides)
    return raw


@pytest.fixture
def store(tmp_path: Path):
    """A gold set with a comment, two drafts and an approved record, and the
    private store beside it holding the drafts' photos."""
    images = tmp_path / "images"
    images.mkdir()
    approved = draft("G-0003", review_state="approved", reviewed_by="Ana",
                     label_confidence="certain", evidence_note="private:evidence/x.png",
                     tags=["my_flips"])
    lines = [COMMENT] + [json.dumps(r, sort_keys=True)
                         for r in (draft("G-0001"), draft("G-0002"), approved)]
    gold = tmp_path / "gold.jsonl"
    gold.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for record_id in ("G-0001", "G-0002", "G-0003"):
        (images / f"{record_id}-front.jpg").write_bytes(b"\xff\xd8\xff" + record_id.encode())
    receipt = tmp_path / "My Receipts" / "order 1.png"
    receipt.parent.mkdir()
    receipt.write_bytes(b"png")
    return gold, images, receipt


def run(gold: Path, answers: list[str], *args: str, opened: list | None = None):
    prompts: list[str] = []
    feed = iter(answers)

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    out = io.StringIO()
    code = review.main(["--gold", str(gold), "--reviewer", "Silviu", *args],
                       ask=ask, opener=(opened.append if opened is not None else lambda p: None),
                       out=out)
    return code, prompts, out.getvalue()


def records(gold: Path) -> dict[str, dict]:
    return {raw["id"]: raw for _, raw in review.GoldFile(gold).records()}


class TestApprove:
    def test_a_certain_sale_with_a_dragged_receipt_is_approved(self, store):
        gold, images, receipt = store
        dragged = str(receipt).replace(" ", "\\ ") + " "      # as Finder drops it
        code, _, out = run(gold, ["a", "", "certain", dragged, "", "s"])
        assert code == 0
        record = records(gold)["G-0001"]
        assert record["review_state"] == "approved"
        assert record["reviewed_by"] == "Silviu" and record["reviewed_at"]
        assert record["label_confidence"] == "certain"
        assert record["evidence_note"] == "private:evidence/G-0001-evidence1.png"
        assert (images / "evidence" / "G-0001-evidence1.png").read_bytes() == b"png"
        assert "currency_assumed" not in record["tags"]
        item = schema.item_from_dict(record)
        assert item is not None and item.counts_toward_headline
        assert "1 approved, 0 rejected, 1 skipped" in out

    def test_a_corrected_currency_replaces_the_assumed_one(self, store):
        gold, _, _ = store
        run(gold, ["a", "ron", "medium", "", "q"])
        record = records(gold)["G-0001"]
        assert record["currency"] == "RON"
        assert "currency_assumed" not in record["tags"]
        assert record["review_state"] == "approved"

    def test_certain_without_evidence_is_not_approved(self, store):
        gold, _, _ = store
        _, _, out = run(gold, ["a", "", "certain", "", "q"])
        assert records(gold)["G-0001"]["review_state"] == "draft"
        assert "needs evidence; not approved" in out

    def test_a_web_address_is_refused_as_evidence(self, store):
        gold, _, _ = store
        _, _, out = run(gold, ["a", "", "high", "https://www.ebay.com/ord/123",
                               "private:https://ebay.com/x", "", "q"])
        assert records(gold)["G-0001"]["review_state"] == "draft"
        assert "web address" in out
        assert "ebay.com" not in gold.read_text()

    def test_a_typed_private_ref_is_kept(self, store):
        gold, _, _ = store
        run(gold, ["a", "", "high", "private:receipts/levis.png", "easy", "q"])
        record = records(gold)["G-0001"]
        assert record["evidence_note"] == "private:receipts/levis.png"
        assert record["difficulty"] == "easy"

    def test_a_record_whose_photo_is_missing_is_not_approved(self, store):
        gold, images, _ = store
        (images / "G-0001-front.jpg").unlink()
        _, _, out = run(gold, ["a", "", "medium", "", "q"])
        assert records(gold)["G-0001"]["review_state"] == "draft"
        assert "photo is not in the private store" in out

    def test_an_invalid_region_is_not_approved(self, store):
        gold, _, _ = store
        gold.write_text(gold.read_text().replace('"region": "US"', '"region": "unknown"'))
        _, _, out = run(gold, ["a", "", "EU", "medium", "", "q"])
        assert records(gold)["G-0001"]["review_state"] == "draft"
        assert "not an ISO 3166 country code" in out


class TestOtherDecisions:
    def test_edit_then_approve_keeps_the_correction(self, store):
        gold, _, _ = store
        edits = ["", "", "", "", "very_good", "42.5", "", "", "", "", "", ""]
        run(gold, ["e", *edits, "a", "", "medium", "", "q"])
        record = records(gold)["G-0001"]
        assert record["actual_sale_price"] == 42.5
        assert record["condition"] == "very_good"
        assert record["review_state"] == "approved"

    def test_reject_records_the_reason(self, store):
        gold, _, _ = store
        run(gold, ["r", "photo shows two items", "q"])
        record = records(gold)["G-0001"]
        assert record["review_state"] == "rejected"
        assert record["reviewed_by"] == "Silviu"
        assert record["human_notes"].endswith("rejected: photo shows two items")
        item = schema.item_from_dict(record)
        assert item is not None and not item.is_scoreable

    def test_skip_and_quit_change_nothing(self, store):
        gold, _, _ = store
        before = gold.read_text()
        run(gold, ["s", "q"])
        assert gold.read_text() == before


class TestFile:
    def test_untouched_lines_are_kept_byte_for_byte(self, store):
        gold, _, receipt = store
        before = gold.read_text().splitlines()
        run(gold, ["a", "", "certain", str(receipt), "", "q"])
        after = gold.read_text().splitlines()
        assert after[0] == COMMENT
        assert after[2:] == before[2:]
        assert after[1] != before[1]

    def test_an_approved_record_is_never_offered_again(self, store):
        gold, _, _ = store
        _, prompts, out = run(gold, ["s", "s"])
        assert sum("[a]pprove" in p for p in prompts) == 2       # G-0001, G-0002 only
        _, _, out = run(gold, [], "G-0003")
        assert "Already decided, not offered again: G-0003" in out
        assert "Nothing to review." in out

    def test_each_photo_is_opened_before_its_review(self, store):
        gold, images, _ = store
        opened: list[Path] = []
        run(gold, ["s", "q"], opened=opened)
        assert opened == [images / "G-0001-front.jpg", images / "G-0002-front.jpg"]

    def test_stopping_midway_keeps_what_was_decided(self, store):
        gold, _, _ = store
        run(gold, ["r", "duplicate"])              # input runs out on G-0002
        state = {k: v["review_state"] for k, v in records(gold).items()}
        assert state == {"G-0001": "rejected", "G-0002": "draft", "G-0003": "approved"}

    def test_no_gold_set_says_where_to_start(self, tmp_path):
        code, _, out = run(tmp_path / "gold.jsonl", [])
        assert code == 1
        assert "eval.intake" in out
