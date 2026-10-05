"""Tests for the gold-set intake (#213).

The intake is the only door into the gold set, so these pin the rules it
exists for: drafts only, stable ids, the photo hashed as written, evidence
never a web address, `certain`/`high` never without evidence, no currency
quietly assumed, nothing written when any row is refused, and a re-run adding
nothing it already has. `sips` is replaced by a stub; the JPEGs are headers
only, which is all `jpeg_size` reads.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tests.conftest import not_none

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import intake, schema


def fake_jpeg(width: int, height: int, salt: bytes = b"") -> bytes:
    """SOI, an APP0 segment, a baseline SOF0 with the size, EOI."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
            + height.to_bytes(2, "big") + width.to_bytes(2, "big")
            + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + sof0 + salt + b"\xff\xd9"


def stub_converter(calls: list[Path]):
    def convert(source: Path, destination: Path) -> None:
        calls.append(source)
        destination.write_bytes(fake_jpeg(1568, 1176, source.name.encode()))
    return convert


def flip(n: int, **overrides) -> dict:
    record = {
        "schema": 1, "id": f"{n:08d}-AAAA-BBBB-CCCC-DDDDDDDDDDDD",
        "item_name": f"Item {n}", "scanned_at": "2026-09-01T10:00:00Z",
        "photo": f"{n:03d}-x.jpg", "photo_source": "stored_1024",
        "estimate_low": 20, "estimate_high": 40, "estimate_likely": 30,
        "estimate_expected": None, "likely_source": "model",
        "confidence_score": 72, "confidence_band": "Medium",
        "category": "clothing", "brand": "Levi's", "condition_grade": "good",
        "prompt_version": "v2", "paid_price": 5, "sold_price": 35,
        "sold_date": "2026-09-14", "listed_date": None, "currency_assumed": "USD",
    }
    record.update(overrides)
    return record


def make_zip(tmp: Path, flips: list[dict], photos: dict[str, bytes] | None = None) -> Path:
    archive = tmp / "SnapWorth-Flips-2026-10-05.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for record in flips:
            stem = record["photo"].removesuffix(".jpg") if record.get("photo") else record["id"][:8]
            z.writestr(f"SnapWorth-Flips-2026-10-05/{stem}.json", json.dumps(record))
            if record.get("photo"):
                data = (photos or {}).get(record["photo"],
                                          fake_jpeg(1024, 768, record["photo"].encode()))
                z.writestr(f"SnapWorth-Flips-2026-10-05/{record['photo']}", data)
    return archive


def run_cli(args: list[str], convert) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = intake.main(args, convert=convert)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "data" / "gold.jsonl", tmp_path / "data" / "images"


# ── The My Flips ZIP ─────────────────────────────────────────────────────────

def test_a_zip_becomes_draft_records_with_hashed_photos(tmp_path, paths):
    gold, images = paths
    gold.parent.mkdir()
    archive = make_zip(tmp_path, [flip(1), flip(2, sold_price=12.5, brand="")])
    calls: list[Path] = []
    code, out, err = run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter(calls))
    assert code == 0, err
    assert calls == [], "a 1024 px JPEG is copied, never re-encoded"

    items = schema.load_gold(gold)
    assert [i.id for i in items] == ["G-0001", "G-0002"]
    first = items[0]
    assert first.review_state is schema.ReviewState.DRAFT
    assert first.label_confidence is schema.LabelConfidence.MEDIUM
    assert not first.is_scoreable, "a draft never counts"
    assert first.validate() == []
    assert first.actual_sale_price == 35 and first.currency == "USD" and first.region == "US"
    assert first.brand == "Levi's" and first.condition == "good"
    assert not_none(first.sold_date).isoformat() == "2026-09-14"
    assert {"my_flips", "stored_1024", "currency_assumed"} <= set(first.tags)
    assert "prompt v2" in first.human_notes and "30" in first.human_notes
    assert items[1].brand is None

    image = not_none(first.primary_image)
    assert image.path == "images/G-0001-front.jpg"
    assert intake.sha256_of(images / "G-0001-front.jpg") == image.sha256
    assert "Added 2 draft record(s)" in out


def test_rerunning_the_same_export_adds_nothing(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    archive = make_zip(tmp_path, [flip(1), flip(2)])
    run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter([]))
    code, out, _ = run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter([]))
    assert code == 0
    assert len(schema.load_gold(gold)) == 2
    assert "Added 0" in out and "2 already in the set" in out


def test_ids_continue_from_the_highest_in_the_set(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    existing = not_none(schema.item_from_dict({
        "id": "G-0041", "images": [{"path": "images/G-0041-front.jpg", "is_primary": True,
                                    "sha256": "0" * 64}],
        "actual_sale_price": 10, "currency": "USD", "category": "shoes"}))
    schema.save_gold([existing], gold)
    run_cli(["--gold", str(gold), "zip", str(make_zip(tmp_path, [flip(1)]))], stub_converter([]))
    assert [i.id for i in schema.load_gold(gold)] == ["G-0041", "G-0042"]


def test_a_large_or_non_jpeg_photo_is_converted(tmp_path, paths):
    gold, images = paths
    gold.parent.mkdir()
    archive = make_zip(tmp_path, [flip(1), flip(2)],
                       photos={"001-x.jpg": fake_jpeg(4032, 3024), "002-x.jpg": b"HEIC..."})
    calls: list[Path] = []
    code, _, err = run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter(calls))
    assert code == 0, err
    assert [c.name for c in calls] == ["001-x.jpg", "002-x.jpg"]
    assert max(not_none(intake.jpeg_size((images / "G-0001-front.jpg").read_bytes()))) == 1568


def test_a_flip_without_a_photo_or_price_refuses_the_whole_export(tmp_path, paths):
    gold, images = paths
    gold.parent.mkdir()
    archive = make_zip(tmp_path, [flip(1), flip(2, photo=None), flip(3, sold_price=None)])
    code, _, err = run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter([]))
    assert code == 1
    assert "no photo" in err and "no sold price" in err
    assert not gold.exists(), "all or nothing: the good row is not written either"
    assert not list(images.glob("*.jpg")) if images.exists() else True


def test_an_unknown_export_schema_is_refused(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    code, _, err = run_cli(["--gold", str(gold), "zip",
                            str(make_zip(tmp_path, [flip(1, schema=2)]))], stub_converter([]))
    assert code == 1 and "schema 2" in err


def test_dry_run_writes_nothing(tmp_path, paths):
    gold, images = paths
    gold.parent.mkdir()
    code, out, _ = run_cli(["--gold", str(gold), "--dry-run", "zip",
                            str(make_zip(tmp_path, [flip(1)]))], stub_converter([]))
    assert code == 0 and "Would add 1" in out
    assert not gold.exists()
    assert not any(images.glob("*.jpg"))


def test_a_zip_entry_cannot_escape_the_work_folder(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../../escaped.json", "{}")
    code, _, err = run_cli(["--gold", str(gold), "zip", str(archive)], stub_converter([]))
    assert code == 1 and "escapes" in err


# ── The sales CSV ────────────────────────────────────────────────────────────

def write_csv(tmp: Path, rows: list[dict]) -> tuple[Path, Path]:
    photos = tmp / "photos"
    photos.mkdir(exist_ok=True)
    table = tmp / "sales.csv"
    columns = ["photo", "price", "currency", "category", "region",
               "label_confidence", "evidence", "difficulty", "tags"]
    with table.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for i, row in enumerate(rows):
            name = row.setdefault("photo", f"p{i}.jpg")
            (photos / name).write_bytes(fake_jpeg(1200, 900, name.encode()))
            writer.writerow({c: row.get(c, "") for c in columns})
    return table, photos


def test_certain_without_evidence_is_refused(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    table, photos = write_csv(tmp_path, [
        {"price": "40", "currency": "USD", "category": "shoes", "label_confidence": "certain"}])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 1 and "needs evidence" in err
    assert not gold.exists()


def test_evidence_is_kept_private_and_never_a_web_address(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    table, photos = write_csv(tmp_path, [
        {"price": "40", "currency": "USD", "category": "shoes", "label_confidence": "certain",
         "evidence": "receipts/2026-09-14.png"},
        {"price": "30", "currency": "USD", "category": "shoes", "label_confidence": "high",
         "evidence": "https://www.ebay.com/itm/123"},
    ])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 1 and "web address" in err

    table, photos = write_csv(tmp_path, [
        {"price": "40", "currency": "USD", "category": "shoes", "label_confidence": "certain",
         "evidence": "receipts/2026-09-14.png", "tags": "negative_check;low_light"}])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 0, err
    item = schema.load_gold(gold)[0]
    assert item.evidence_note == "private:receipts/2026-09-14.png"
    assert item.evidence_url is None
    assert item.label_confidence is schema.LabelConfidence.CERTAIN
    assert item.tags == ["negative_check", "low_light"]


def test_a_non_usd_sale_keeps_its_currency_and_needs_its_region(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    table, photos = write_csv(tmp_path, [{"price": "120", "currency": "ron", "category": "bags"}])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 1 and "needs its region" in err

    table, photos = write_csv(tmp_path, [
        {"price": "120", "currency": "ron", "category": "bags", "region": "ro"}])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 0, err
    item = schema.load_gold(gold)[0]
    assert (item.actual_sale_price, item.currency, item.region) == (120, "RON", "RO")


def test_a_negative_control_may_be_unpriced(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    table, photos = write_csv(tmp_path, [
        {"price": "0", "currency": "USD", "category": "other",
         "difficulty": "negative_control"}])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 0, err
    assert schema.load_gold(gold)[0].difficulty is schema.Difficulty.NEGATIVE_CONTROL


def test_a_csv_with_unknown_columns_is_refused(tmp_path, paths):
    gold, _ = paths
    gold.parent.mkdir()
    table = tmp_path / "bad.csv"
    table.write_text("photo,price,currency,category,evidence_url\n")
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(tmp_path)],
                           stub_converter([]))
    assert code == 1 and "evidence_url" in err


def test_jpeg_size_reads_the_frame_header():
    assert intake.jpeg_size(fake_jpeg(1024, 768)) == (1024, 768)
    assert intake.jpeg_size(b"not a jpeg") is None


def test_a_refused_row_removes_the_photos_already_written(tmp_path, paths):
    """The good first row's photo is placed before the second row is refused;
    all or nothing covers the images folder too."""
    gold, images = paths
    gold.parent.mkdir()
    table, photos = write_csv(tmp_path, [
        {"price": "40", "currency": "USD", "category": "shoes"},
        {"price": "30", "currency": "USD", "category": "shoes", "label_confidence": "sure"},
    ])
    code, _, err = run_cli(["--gold", str(gold), "csv", str(table), "--photos", str(photos)],
                           stub_converter([]))
    assert code == 1 and "label_confidence 'sure'" in err
    assert not gold.exists()
    assert list(images.glob("*")) == []
