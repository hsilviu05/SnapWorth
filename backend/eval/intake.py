"""Gold-set intake: sales in, draft gold-v2 records out (#213).

    python -m eval.intake zip SnapWorth-Flips-2026-10-05.zip
    python -m eval.intake csv sales.csv --photos ~/Pictures/sold

Two sources, one result. The My Flips ZIP (the app's "Export photos and
estimates", #214) holds a JPEG and a JSON record per sold flip; a CSV names a
photo in a folder per row, for sales that never went through the app. Either
way each sale becomes a **draft** record appended to `eval/data/gold.jsonl`
with the next stable id (`G-0001`, …), and its photo is written to
`eval/data/images/<id>-front.jpg`.

Nothing here approves anything. A draft counts toward no metric until a person
reviews it, sets its evidence and label confidence, and approves it with
`reviewed_by` (`schema.GoldItem.validate`). The tool only makes that review
quick and the record well-formed.

What it enforces, because the public repo is where the labels go:

* **Photos never go into git.** They land in `eval/data/images/`, which is
  gitignored and is where the private photo repository is mounted; the
  `data-integrity` job in eval.yml fails if one is ever committed.
* **Evidence is a private reference, never a URL.** A row's evidence is written
  as `evidence_note: "private:<ref>"`. A value that looks like a URL is
  refused: an order page or a seller profile in a public file says who sold
  what, and for how much.
* **`certain` and `high` need evidence.** A row claiming either without it is
  refused, as `GoldItem.validate` would flag it later anyway.
* **The input matches production.** The app uploads a JPEG with a 1568 px long
  edge at quality 0.8, so a photo larger than that, or not a JPEG, is converted
  with macOS `sips` to exactly that. A JPEG already within it is copied
  byte for byte: re-encoding the app's 1024 px copy would only lose detail it
  never had. ZIP records are tagged `stored_1024`, because that copy is
  smaller than what the app sent the model.
* **Currencies are never converted.** The ZIP says `currency_assumed: "USD"`
  because the app never asks; such a record is tagged `currency_assumed` for
  the reviewer to confirm or correct. An EU sale stays in its own currency.
* **All or nothing.** If any row is refused, nothing is written, and every
  problem is printed at once.
* **A photo is taken once.** A row whose photo hashes the same as an image
  already in the set is skipped, so re-running an export adds only new sales.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import schema

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_GOLD = DATA / "gold.jsonl"

#: What the app uploads (`ScanAPIClient`): the long edge, and JPEG quality.
MAX_EDGE = 1568
JPEG_QUALITY = 80

ID_PATTERN = re.compile(r"^G-(\d{4,})$")
# A web address: a scheme, `www.`, or a host with a common top-level domain.
URLISH = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|eu|ro|de|es|fr|it|"
                    r"co|io|app|uk|us|cn)\b)", re.IGNORECASE)
JPEG_MAGIC = b"\xff\xd8\xff"

# A converter takes a source photo and a destination path, and writes a JPEG
# no larger than MAX_EDGE there. Injected so tests don't need macOS.
Converter = Callable[[Path, Path], None]


class IntakeError(Exception):
    """One or more rows were refused; nothing was written."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Draft:
    """A row on its way to becoming a record: the record's fields, and the
    photo that will be written beside it."""

    source: str                 # "flip 001-ab12cd34.json", "sales.csv row 3"
    photo: Path
    fields: dict
    tags: list[str]


# ── Photos ───────────────────────────────────────────────────────────────────

def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's start-of-frame marker, or None if `data`
    is not a JPEG this can read. Enough to decide whether to resample, without
    an image library."""
    if not data.startswith(JPEG_MAGIC):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF15, except DHT (C4), JPG (C8) and DAC (CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        i += 2 + length
    return None


def sips_converter(source: Path, destination: Path) -> None:
    """Re-encode `source` as the app would send it, with macOS `sips`."""
    if shutil.which("sips") is None:
        raise IntakeError([(f"{source.name}: needs converting, and `sips` (macOS) "
                            "is not available here. Run the intake on a Mac.")])
    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(JPEG_QUALITY),
         "-Z", str(MAX_EDGE), str(source), "--out", str(destination)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise IntakeError([f"{source.name}: sips failed: {result.stderr.strip()}"])


def place_photo(source: Path, destination: Path, convert: Converter) -> str:
    """Write the photo the model should see to `destination`; its sha256."""
    data = source.read_bytes()
    size = jpeg_size(data)
    if size is not None and max(size) <= MAX_EDGE:
        destination.write_bytes(data)
    else:
        convert(source, destination)
        converted = destination.read_bytes()
        size = jpeg_size(converted)
        if size is None or max(size) > MAX_EDGE:
            raise IntakeError([(f"{source.name}: conversion did not produce a JPEG "
                                f"within {MAX_EDGE} px")])
        data = converted
    return hashlib.sha256(data).hexdigest()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Sources ──────────────────────────────────────────────────────────────────

def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _money(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _date(value) -> str | None:
    text = _text(value)
    if text is None:
        return None
    date.fromisoformat(text)        # raises ValueError on a bad date
    return text


def drafts_from_zip(archive: Path, workdir: Path) -> list[Draft]:
    """One draft per JSON record in a My Flips export."""
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            # Refuse any entry that would land outside the work folder.
            target = (workdir / name).resolve()
            if not str(target).startswith(str(workdir.resolve())):
                raise IntakeError([f"{archive.name}: entry {name!r} escapes the archive"])
        z.extractall(workdir)

    drafts: list[Draft] = []
    problems: list[str] = []
    for path in sorted(workdir.rglob("*.json")):
        source = f"{archive.name}:{path.name}"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{source}: not JSON ({exc})")
            continue
        if record.get("schema") != 1:
            problems.append(f"{source}: export schema {record.get('schema')!r}, "
                            "this intake reads schema 1")
            continue
        if not record.get("photo"):
            problems.append(f"{source}: the flip has no photo, so it can't be scored")
            continue
        photo = path.parent / record["photo"]
        price = _money(record.get("sold_price"))
        if not photo.is_file():
            problems.append(f"{source}: photo {record['photo']} is not in the archive")
            continue
        if price is None or price == 0:
            problems.append(f"{source}: no sold price")
            continue

        tags = ["my_flips", "stored_1024"]
        currency = (_text(record.get("currency")) or "").upper()
        if not currency:
            currency = (_text(record.get("currency_assumed")) or "USD").upper()
            tags.append("currency_assumed")
        try:
            sold = _date(record.get("sold_date"))
        except ValueError:
            problems.append(f"{source}: sold_date {record.get('sold_date')!r} is not a date")
            continue

        notes = (f"From My Flips (flip {str(record.get('id', ''))[:8].lower()}). "
                 f"Scan-time estimate {record.get('estimate_low')}–"
                 f"{record.get('estimate_high')}, likely {record.get('estimate_likely')} "
                 f"({record.get('likely_source')}), confidence "
                 f"{record.get('confidence_band')}, prompt "
                 f"{record.get('prompt_version') or 'unknown'}.")
        drafts.append(Draft(
            source=source, photo=photo, tags=tags,
            fields={
                "actual_sale_price": price, "currency": currency,
                "category": _text(record.get("category")) or "other",
                "brand": _text(record.get("brand")),
                "condition": _text(record.get("condition_grade")),
                "sold_date": sold,
                # The app knows neither; a USD sale is most likely US.
                "region": "US" if currency == "USD" else None,
                "label_confidence": "medium",
                "human_notes": notes,
            }))
    if problems:
        raise IntakeError(problems)
    return drafts


CSV_REQUIRED = ("photo", "price", "currency", "category")
CSV_OPTIONAL = ("brand", "model", "condition", "sold_date", "marketplace", "region",
                "label_confidence", "evidence", "difficulty", "tags", "notes")


def drafts_from_csv(table: Path, photos: Path) -> list[Draft]:
    """One draft per row of a sales CSV whose `photo` names a file in `photos`."""
    drafts: list[Draft] = []
    problems: list[str] = []
    with table.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = [c for c in CSV_REQUIRED if c not in columns]
        unknown = sorted(columns - set(CSV_REQUIRED) - set(CSV_OPTIONAL))
        header = ([f"{table.name}: missing columns {missing}"] if missing else []) + \
                 ([f"{table.name}: unknown columns {unknown}"] if unknown else [])
        if header:
            raise IntakeError(header)
        for number, row in enumerate(reader, start=2):
            source = f"{table.name} row {number}"
            photo = photos / (row.get("photo") or "").strip()
            if not row.get("photo") or not photo.is_file():
                problems.append(f"{source}: photo {row.get('photo')!r} is not in {photos}")
                continue
            price = _money(row.get("price"))
            difficulty = _text(row.get("difficulty")) or "typical"
            if price is None or (price == 0 and difficulty != "negative_control"):
                problems.append(f"{source}: price {row.get('price')!r} is not a positive number")
                continue
            try:
                sold = _date(row.get("sold_date"))
            except ValueError:
                problems.append(f"{source}: sold_date {row.get('sold_date')!r} is not YYYY-MM-DD")
                continue
            currency = (_text(row.get("currency")) or "").upper()
            region = (_text(row.get("region")) or "").upper() or None
            if region is None:
                # Every row says where it sold (#225): the per-region breakdown
                # is the point, and a guess would file a sale under the wrong
                # market. A dollar sale is not necessarily a US one.
                problems.append(f"{source}: a {currency or 'non-USD'} sale needs its region "
                                "(US, RO, DE, ES, …)")
                continue
            tags = [t.strip() for t in (row.get("tags") or "").split(";") if t.strip()]
            drafts.append(Draft(
                source=source, photo=photo, tags=tags,
                fields={
                    "actual_sale_price": price,
                    "currency": currency,
                    "category": _text(row.get("category")),
                    "brand": _text(row.get("brand")), "model": _text(row.get("model")),
                    "condition": _text(row.get("condition")), "sold_date": sold,
                    "marketplace": _text(row.get("marketplace")),
                    "region": region,
                    "label_confidence": _text(row.get("label_confidence")) or "medium",
                    "evidence": _text(row.get("evidence")),
                    "difficulty": difficulty,
                    "human_notes": _text(row.get("notes")) or "",
                }))
    if problems:
        raise IntakeError(problems)
    return drafts


# ── Records ──────────────────────────────────────────────────────────────────

def evidence_note(raw: str | None) -> tuple[str, str | None]:
    """(`evidence_note`, problem). Evidence is a private reference: `private:<ref>`
    or a bare ref, which gets the prefix. Anything that looks like a web
    address is refused."""
    if raw is None:
        return "", None
    ref = raw.removeprefix("private:").strip()
    if not ref:
        return "", None
    if URLISH.search(ref):
        return "", (f"evidence {raw!r} looks like a web address, which would put an "
                    "order page or seller account in the public repo. Keep the "
                    "receipt or screenshot in the private store and name it, "
                    "e.g. private:receipts/2026-09-14-levis.png")
    return f"private:{ref}", None


def next_number(items: list[schema.GoldItem]) -> int:
    numbers = [int(m.group(1)) for i in items if (m := ID_PATTERN.match(i.id))]
    return max(numbers, default=0) + 1


def build_records(drafts: list[Draft], existing: list[schema.GoldItem],
                  images: Path, convert: Converter, now: datetime
                  ) -> tuple[list[schema.GoldItem], list[str]]:
    """The new records, and the sources skipped as already in the set.

    Photos are written to `images` as each record is built. On a refusal the
    caller removes them; see `run`."""
    known = {img.sha256 for item in existing for img in item.images if img.sha256}
    number = next_number(existing)
    records: list[schema.GoldItem] = []
    skipped: list[str] = []
    problems: list[str] = []

    for draft in drafts:
        source_hash = sha256_of(draft.photo)
        fields = dict(draft.fields)
        note, problem = evidence_note(fields.pop("evidence", None))
        if problem:
            problems.append(f"{draft.source}: {problem}")
            continue
        confidence = fields.pop("label_confidence")
        if confidence not in {c.value for c in schema.LabelConfidence}:
            problems.append(f"{draft.source}: label_confidence {confidence!r} is not one of "
                            f"{[c.value for c in schema.LabelConfidence]}")
            continue
        if confidence in ("certain", "high") and not note:
            problems.append(f"{draft.source}: label_confidence {confidence!r} needs "
                            "evidence (a private:<ref>)")
            continue

        record_id = f"G-{number:04d}"
        destination = images / f"{record_id}-front.jpg"
        digest = place_photo(draft.photo, destination, convert)
        if digest in known or source_hash in known:
            destination.unlink(missing_ok=True)
            skipped.append(draft.source)
            continue

        raw = dict(
            id=record_id,
            images=[{"path": f"images/{destination.name}", "angle": "front",
                     "is_primary": True, "sha256": digest}],
            label_confidence=confidence, evidence_note=note,
            review_state="draft", tags=draft.tags,
            schema_version=schema.SCHEMA_VERSION,
            **{k: v for k, v in fields.items() if v is not None},
        )
        item = schema.item_from_dict(raw)
        if item is None:
            destination.unlink(missing_ok=True)
            problems.append(f"{draft.source}: not a valid gold record")
            continue
        item.created_at = now
        found = item.validate()
        if found:
            destination.unlink(missing_ok=True)
            problems.extend(f"{draft.source}: {p}" for p in found)
            continue
        known.add(digest)
        known.add(source_hash)
        records.append(item)
        number += 1

    if problems:
        for item in records:
            for image in item.images:
                (images / Path(image.path).name).unlink(missing_ok=True)
        raise IntakeError(problems)
    return records, skipped


def run(drafts: list[Draft], gold: Path, images: Path, convert: Converter,
        dry_run: bool = False, now: datetime | None = None
        ) -> tuple[list[schema.GoldItem], list[str]]:
    existing = schema.load_gold(gold) if gold.exists() else []
    images.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    if dry_run:
        with tempfile.TemporaryDirectory() as scratch:
            return build_records(drafts, existing, Path(scratch), convert, now)
    records, skipped = build_records(drafts, existing, images, convert, now)
    if records:
        with gold.open("a", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(item.to_dict(), sort_keys=True) + "\n")
    return records, skipped


def main(argv: list[str] | None = None, convert: Converter = sips_converter) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.intake", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", default=str(DEFAULT_GOLD),
                        help="the gold set to append to (default eval/data/gold.jsonl)")
    parser.add_argument("--images", default=None,
                        help="where photos go (default: images/ beside the gold set)")
    parser.add_argument("--dry-run", action="store_true",
                        help="check every row and print what would be added; write nothing")
    sub = parser.add_subparsers(dest="source", required=True)
    from_zip = sub.add_parser("zip", help="a My Flips export (Export photos and estimates)")
    from_zip.add_argument("archive")
    from_csv = sub.add_parser("csv", help="a sales CSV and a folder of photos")
    from_csv.add_argument("table")
    from_csv.add_argument("--photos", required=True)
    args = parser.parse_args(argv)

    gold = Path(args.gold)
    images = Path(args.images) if args.images else gold.parent / "images"
    try:
        with tempfile.TemporaryDirectory() as work:
            if args.source == "zip":
                drafts = drafts_from_zip(Path(args.archive), Path(work))
            else:
                drafts = drafts_from_csv(Path(args.table), Path(args.photos))
            records, skipped = run(drafts, gold, images, convert, dry_run=args.dry_run)
    except IntakeError as error:
        print(f"Nothing written. {len(error.problems)} problem(s):", file=sys.stderr)
        for problem in error.problems:
            if problem:
                print(f"  - {problem}", file=sys.stderr)
        return 1

    verb = "Would add" if args.dry_run else "Added"
    print(f"{verb} {len(records)} draft record(s) to {gold}"
          + (f"; {len(skipped)} already in the set" if skipped else ""))
    for item in records:
        flags = ", ".join(t for t in item.tags if t in ("currency_assumed", "stored_1024"))
        print(f"  {item.id}  {item.actual_sale_price:g} {item.currency}  {item.category}"
              f"  {item.brand or '-'}  [{item.label_confidence.value}]"
              + (f"  ({flags})" if flags else ""))
    if records and not args.dry_run:
        print("Next: review each draft, set label_confidence and evidence_note "
              "(private:<ref>), then approve it with review_state and reviewed_by.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
