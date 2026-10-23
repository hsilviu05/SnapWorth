"""Tests for the gold-set review (#213).

The review is where a draft becomes a record that counts, so these pin what
it must never do: approve a record that doesn't validate, take a web address
as evidence, lose a decision already made when the session stops, or touch a
line it didn't change. Input is scripted; the photo opener is a recorder.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import review, schema

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def draft(record_id: str, **fields) -> dict:
    record = {
        "id": record_id,
        "images": [{"path": f"images/{record_id}-front.jpg", "angle": "front",
                    "is_primary": True, "sha256": "ab" * 32}],
        "actual_sale_price": 45.0, "currency": "USD", "category": "clothing",
        "region": "US", "label_confidence": "certain",
        "evidence_note": "private:receipts/a.png", "review_state": "draft",
        "tags": [], "schema_version": schema.SCHEMA_VERSION,
    }
    record.update(fields)
    return {k: v for k, v in record.items() if v is not None}


def gold_file(tmp_path: Path, *records: dict, header: str | None = None) -> Path:
    path = tmp_path / "gold.jsonl"
    lines = ([header] if header else []) + [json.dumps(r, sort_keys=True) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def scripted(*answers: str):
    queue = list(answers)

    def ask(_prompt: str) -> str:
        if not queue:
            raise AssertionError("the review asked for more input than the test gave")
        return queue.pop(0)
    return ask


def records(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            raw = json.loads(line)
            out[raw["id"]] = raw
    return out


def run(path: Path, *answers: str, only: str | None = None, opened: list | None = None,
        said: list | None = None) -> dict:
    return review.review(
        path, "silviu", ask=scripted(*answers),
        say=(said.append if said is not None else (lambda _t: None)),
        opener=(opened.append if opened is not None else (lambda _p: None)),
        only=only, now=lambda: NOW)


def test_approve_sets_state_reviewer_and_time(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"))
    counts = run(path, "a")
    saved = records(path)["G-0001"]
    assert counts["approved"] == 1
    assert saved["review_state"] == "approved"
    assert saved["reviewed_by"] == "silviu"
    assert saved["reviewed_at"] == NOW.isoformat()
    item = schema.item_from_dict(saved)
    assert item is not None and item.counts_toward_headline


def test_a_record_that_does_not_validate_is_never_approved(tmp_path):
    path = gold_file(tmp_path, draft("G-0001", region=None))
    said: list[str] = []
    counts = run(path, "a", "q", said=said)
    assert counts["approved"] == 0
    assert records(path)["G-0001"]["review_state"] == "draft"
    assert any("no region" in line for line in said)


def test_editing_fixes_a_record_then_it_approves(tmp_path):
    path = gold_file(tmp_path, draft("G-0001", region=None))
    # e, then one answer per field in order: price, currency, category, brand,
    # model, variant, condition, region, sold date, marketplace, confidence,
    # evidence, difficulty, tags, notes.
    edits = ["", "RON", "", "Levi's", "501", "", "good", "ro", "2026-09-14",
             "vinted", "", "", "", "currency_checked; hand_entered", ""]
    run(path, "e", *edits, "a")
    saved = records(path)["G-0001"]
    assert saved["review_state"] == "approved"
    assert saved["currency"] == "RON"
    assert saved["region"] == "RO"
    assert saved["brand"] == "Levi's"
    assert saved["sold_date"] == "2026-09-14"
    assert saved["tags"] == ["currency_checked", "hand_entered"]


def test_a_bad_answer_is_asked_again_not_saved(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"))
    said: list[str] = []
    edits = ["", "", "furniturez", "home", "", "", "", "", "", "", "", "",
             "", "", "", ""]
    run(path, "e", *edits, "s", said=said)
    assert records(path)["G-0001"]["category"] == "home"
    assert any("category is one of" in line for line in said)


def test_evidence_is_never_a_web_address(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"))
    said: list[str] = []
    edits = ["", "", "", "", "", "", "", "", "", "", "",
             "https://www.vinted.ro/items/123", "receipts/levis.png",
             "", "", ""]
    run(path, "e", *edits, "s", said=said)
    assert records(path)["G-0001"]["evidence_note"] == "private:receipts/levis.png"
    assert any("web address" in line for line in said)


def test_dash_clears_an_optional_field_but_not_a_required_one(tmp_path):
    path = gold_file(tmp_path, draft("G-0001", brand="Nike"))
    said: list[str] = []
    # The price is asked again after "-", so it takes one extra answer.
    edits = ["-", "", "", "", "-", "", "", "", "", "", "", "", "", "", "", ""]
    run(path, "e", *edits, "s", said=said)
    saved = records(path)["G-0001"]
    assert saved["actual_sale_price"] == 45.0
    assert "brand" not in saved
    assert any("price can't be empty" in line for line in said)


def test_reject_keeps_the_record_and_the_reason(tmp_path):
    path = gold_file(tmp_path, draft("G-0001", human_notes="from the ZIP"))
    run(path, "r", "two items in one photo")
    saved = records(path)["G-0001"]
    assert saved["review_state"] == "rejected"
    assert saved["human_notes"] == "from the ZIP Rejected: two items in one photo"
    assert saved["reviewed_by"] == "silviu"


def test_every_decision_in_a_session_is_kept(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"), draft("G-0002"), draft("G-0003"))
    run(path, "a", "r", "", "a")
    saved = records(path)
    assert [saved[i]["review_state"] for i in ("G-0001", "G-0002", "G-0003")] == \
        ["approved", "rejected", "approved"]


def test_quit_keeps_what_was_decided(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"), draft("G-0002"))
    run(path, "a", "q")
    saved = records(path)
    assert saved["G-0001"]["review_state"] == "approved"
    assert saved["G-0002"]["review_state"] == "draft"


def test_an_interrupt_keeps_what_was_decided(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"), draft("G-0002"))
    answers = iter(["a"])

    def ask(_prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None

    try:
        review.review(path, "silviu", ask=ask, say=lambda _t: None,
                      opener=lambda _p: None, now=lambda: NOW)
    except KeyboardInterrupt:
        pass
    assert records(path)["G-0001"]["review_state"] == "approved"
    assert not list(tmp_path.glob(".gold-*"))


def test_only_open_states_are_walked_and_untouched_lines_stay_byte_for_byte(tmp_path):
    done = draft("G-0001", review_state="approved", reviewed_by="silviu")
    header = "# gold set v1"
    path = gold_file(tmp_path, done, draft("G-0002"), header=header)
    before = path.read_text(encoding="utf-8").splitlines()
    opened: list[Path] = []
    run(path, "a", opened=opened)
    after = path.read_text(encoding="utf-8").splitlines()
    assert after[0] == header
    assert after[1] == before[1]
    assert opened == [tmp_path / "images/G-0002-front.jpg"]
    assert records(path)["G-0002"]["review_state"] == "approved"


def test_only_reviews_one_record_whatever_its_state(tmp_path):
    path = gold_file(tmp_path, draft("G-0001"),
                     draft("G-0002", review_state="approved", reviewed_by="x"))
    run(path, "e", *[""] * 10, "medium", *[""] * 4, "a", only="G-0002")
    saved = records(path)
    assert saved["G-0002"]["label_confidence"] == "medium"
    assert saved["G-0002"]["reviewed_by"] == "silviu"
    assert saved["G-0001"]["review_state"] == "draft"


def test_main_needs_a_gold_file_and_a_reviewer(tmp_path, capsys, monkeypatch):
    assert review.main(["--gold", str(tmp_path / "missing.jsonl")]) == 1
    path = gold_file(tmp_path, draft("G-0001"))
    monkeypatch.setattr(review, "git_user", lambda: None)
    assert review.main(["--gold", str(path), "--no-open"]) == 1
    assert "--reviewer" in capsys.readouterr().err
