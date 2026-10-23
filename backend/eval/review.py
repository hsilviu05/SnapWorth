"""Gold-set review: approve the intake's drafts one at a time (#213).

    python -m eval.review --reviewer Silviu            # every open draft
    python -m eval.review --reviewer Silviu G-0003     # just these

`eval.intake` writes drafts; a draft counts toward nothing until a person sets
its label confidence and evidence and approves it with `reviewed_by`. Doing
that by editing `gold.jsonl` by hand is slow and easy to get wrong, so this
walks each open record: it opens the photo, shows the label, and asks only
what a reviewer has to decide.

* **Approve** asks for the label confidence (`certain` for your own sale with
  a receipt or screenshot, `high` for a captured completed listing) and, when
  that needs evidence the record lacks, the receipt itself: drag the file in
  and it is copied into the private store as `evidence/<id>-evidence1.png`,
  named in the record as `private:evidence/…`. A web address is refused, as
  in the intake. A sale tagged `currency_assumed` asks for the real currency
  first, and the tag goes once someone has confirmed it.
* **Edit** corrects the label fields on a draft. An approved record is never
  offered again: changing its label afterwards is the drift `schema` guards
  against.
* **Reject** records why, and keeps the record out of every metric.

Nothing is approved unless `GoldItem.validate` finds no problem and the
primary photo is in the store. Each decision is written straight away, so
stopping (q, Ctrl-C) loses nothing; comment lines and record order are kept.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import intake, schema

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_GOLD = DATA / "gold.jsonl"

#: Review states a record can still be reviewed from.
OPEN_STATES = {"draft", "pending_review", "needs_relabel"}
CONFIDENCES = [c.value for c in schema.LabelConfidence]
DIFFICULTIES = [d.value for d in schema.Difficulty]
#: Label fields a reviewer may correct on a draft, in the order asked.
EDITABLE = ("category", "brand", "model", "variant", "condition",
            "actual_sale_price", "currency", "region", "sold_date", "marketplace")
EVIDENCE_DIR = "evidence"

Ask = Callable[[str], str]
Opener = Callable[[Path], None]


class Quit(Exception):
    """The reviewer asked to stop."""


class GoldFile:
    """`gold.jsonl` as its lines, so one record can be rewritten in place."""

    def __init__(self, path: Path):
        self.path = path
        self.lines = path.read_text(encoding="utf-8").splitlines()

    def records(self) -> list[tuple[int, dict]]:
        found = []
        for index, line in enumerate(self.lines):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                found.append((index, json.loads(text)))
            except json.JSONDecodeError:
                continue
        return found

    def replace(self, index: int, raw: dict) -> None:
        self.lines[index] = json.dumps(raw, sort_keys=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def default_opener(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        print(f"  photo: {path}")


def dropped_path(text: str) -> Path:
    """A path typed, pasted or dragged in from Finder: quoted, or with its
    spaces escaped, and often with a trailing space."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return Path(text.replace("\\ ", " ")).expanduser()


def primary_photo(raw: dict, images: Path) -> Path | None:
    entries = raw.get("images") or []
    chosen = next((i for i in entries if i.get("is_primary")), entries[0] if entries else None)
    return images / Path(chosen["path"]).name if chosen else None


def show(raw: dict, out) -> None:
    def value(key):
        found = raw.get(key)
        return "-" if found in (None, "") else found

    print(f"\n── {raw.get('id')} ──────────────────────────────", file=out)
    print(f"  sold for     {value('actual_sale_price')} {value('currency')}"
          f"  in {value('region')} on {value('sold_date')} via {value('marketplace')}", file=out)
    print(f"  item         {value('category')} · {value('brand')} · {value('model')}"
          f"{' · ' + raw['variant'] if raw.get('variant') else ''} · condition {value('condition')}",
          file=out)
    print(f"  label        {value('label_confidence')} · evidence {value('evidence_note')}"
          f" · difficulty {value('difficulty')}", file=out)
    if raw.get("tags"):
        print(f"  tags         {', '.join(raw['tags'])}", file=out)
    if raw.get("human_notes"):
        print(f"  notes        {raw['human_notes']}", file=out)


def choose(ask: Ask, prompt: str, options: list[str], current: str) -> str:
    while True:
        answer = ask(f"{prompt} ({' / '.join(options)}) [{current}]: ").strip().lower()
        if not answer:
            return current
        if answer in options:
            return answer
        print(f"  '{answer}' is not one of {', '.join(options)}")


def edit_fields(raw: dict, ask: Ask, out) -> None:
    """Enter keeps a value, '-' clears an optional one."""
    print("  Enter keeps the value shown; '-' clears it.", file=out)
    for key in EDITABLE:
        while True:
            current = raw.get(key)
            answer = ask(f"  {key} [{'' if current is None else current}]: ").strip()
            if not answer:
                break
            if answer == "-":
                if key in ("category", "actual_sale_price", "currency"):
                    print(f"  {key} is required.", file=out)
                    continue
                raw.pop(key, None)
                break
            try:
                if key == "actual_sale_price":
                    raw[key] = float(answer)
                elif key == "sold_date":
                    raw[key] = date.fromisoformat(answer).isoformat()
                elif key in ("currency", "region"):
                    raw[key] = answer.upper()
                else:
                    raw[key] = answer
            except ValueError:
                print("  Not valid here (price is a number, sold_date YYYY-MM-DD).", file=out)
                continue
            break
    tags = ask(f"  tags, ';'-separated [{'; '.join(raw.get('tags') or [])}]: ").strip()
    if tags:
        raw["tags"] = [] if tags == "-" else [t.strip() for t in tags.split(";") if t.strip()]
    notes = ask(f"  notes [{raw.get('human_notes') or ''}]: ").strip()
    if notes:
        raw["human_notes"] = "" if notes == "-" else notes


def store_evidence(source: Path, record_id: str, images: Path) -> str:
    """Copy a receipt or screenshot into the private store; return its ref."""
    folder = images / EVIDENCE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        name = f"{record_id}-evidence{number}{source.suffix.lower()}"
        if not (folder / name).exists():
            break
        number += 1
    shutil.copy2(source, folder / name)
    return f"private:{EVIDENCE_DIR}/{name}"


def ask_evidence(raw: dict, ask: Ask, images: Path, out) -> str | None:
    """A `private:` ref for the record, or None if the reviewer gave none."""
    while True:
        answer = ask("  Evidence: drag the receipt or screenshot here, or type "
                     "private:<ref> (Enter to go back): ").strip()
        if not answer:
            return None
        if answer.startswith("private:"):
            note, problem = intake.evidence_note(answer)
            if problem:
                print(f"  {problem}", file=out)
                continue
            return note
        if intake.URLISH.search(answer) and not dropped_path(answer).exists():
            print("  That looks like a web address. Keep a screenshot of the page in "
                  "the private store instead, and drag the file in.", file=out)
            continue
        path = dropped_path(answer)
        if not path.is_file():
            print(f"  No file at {path}.", file=out)
            continue
        return store_evidence(path, str(raw.get("id")), images)


def approve(raw: dict, reviewer: str, ask: Ask, images: Path, out,
            now: datetime) -> dict | None:
    """The approved record, or None (nothing changed) if it isn't ready."""
    record = json.loads(json.dumps(raw))
    tags = list(record.get("tags") or [])

    if "currency_assumed" in tags:
        answer = ask(f"  The app assumed {record.get('currency')}. Enter if that's what "
                     "you were paid in, or the real currency (EUR, RON, GBP, …): ").strip()
        if answer:
            record["currency"] = answer.upper()
        tags.remove("currency_assumed")
        record["tags"] = tags
    if record.get("region") in (None, "", "unknown"):
        record["region"] = ask("  Country it sold in (US, RO, DE, …): ").strip().upper()
    if record.get("currency", "USD") != "USD" and not record.get("sold_date"):
        answer = ask("  Sold on (YYYY-MM-DD), needed to convert a non-USD price: ").strip()
        try:
            record["sold_date"] = date.fromisoformat(answer).isoformat()
        except ValueError:
            print("  Not a date; not approved.", file=out)
            return None

    print("  certain = your own sale, receipt or screenshot held · high = a captured "
          "completed listing · medium/low = not counted in the headline", file=out)
    confidence = choose(ask, "  Label confidence", CONFIDENCES,
                        record.get("label_confidence") or "medium")
    record["label_confidence"] = confidence
    if confidence in ("certain", "high") and not record.get("evidence_note"):
        note = ask_evidence(record, ask, images, out)
        if not note:
            print(f"  '{confidence}' needs evidence; not approved.", file=out)
            return None
        record["evidence_note"] = note
    record["difficulty"] = choose(ask, "  Difficulty", DIFFICULTIES,
                                  record.get("difficulty") or "typical")

    record["review_state"] = "approved"
    record["reviewed_by"] = reviewer
    record["reviewed_at"] = now.isoformat()

    item = schema.item_from_dict(record)
    problems = item.validate() if item else ["not a valid gold record"]
    photo = primary_photo(record, images)
    if photo is None or not photo.is_file():
        problems.append(f"its photo is not in the private store ({photo})")
    if problems:
        print("  Not approved:", file=out)
        for problem in problems:
            print(f"    - {problem}", file=out)
        return None
    return record


def review_one(raw: dict, reviewer: str, ask: Ask, images: Path, out,
               now: datetime) -> dict | None:
    """The record as decided, or None if skipped. Raises Quit."""
    while True:
        show(raw, out)
        action = ask("  [a]pprove  [e]dit  [r]eject  [s]kip  [q]uit: ").strip().lower()[:1]
        if action == "q":
            raise Quit
        if action == "s":
            return None
        if action == "e":
            raw = json.loads(json.dumps(raw))
            edit_fields(raw, ask, out)
            continue
        if action == "r":
            reason = ask("  Why? ").strip() or "no reason given"
            record = json.loads(json.dumps(raw))
            record["review_state"] = "rejected"
            record["reviewed_by"] = reviewer
            record["reviewed_at"] = now.isoformat()
            notes = record.get("human_notes") or ""
            record["human_notes"] = (notes + " | " if notes else "") + f"rejected: {reason}"
            return record
        if action == "a":
            record = approve(raw, reviewer, ask, images, out, now)
            if record is not None:
                return record


def main(argv: list[str] | None = None, ask: Ask = input,
         opener: Opener = default_opener, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m eval.review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="only these records (default: every open one)")
    parser.add_argument("--reviewer", help="your name, written as reviewed_by")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--images", default=None,
                        help="the private photo store (default: images/ beside the gold set)")
    parser.add_argument("--no-open", action="store_true", help="don't open each photo")
    args = parser.parse_args(argv)

    gold_path = Path(args.gold)
    images = Path(args.images) if args.images else gold_path.parent / "images"
    if not gold_path.exists():
        print(f"No gold set at {gold_path} yet. Add sales with `python -m eval.intake` first.",
              file=out)
        return 1

    gold = GoldFile(gold_path)
    wanted = set(args.ids)
    records = [(i, raw) for i, raw in gold.records()
               if not wanted or raw.get("id") in wanted]
    queue = [(i, raw) for i, raw in records
             if raw.get("review_state", "draft") in OPEN_STATES]
    if wanted:
        decided = sorted(raw["id"] for _, raw in records
                         if raw.get("review_state", "draft") not in OPEN_STATES)
        missing = sorted(wanted - {raw.get("id") for _, raw in records})
        if decided:
            print(f"Already decided, not offered again: {', '.join(decided)}", file=out)
        if missing:
            print(f"Not in the gold set: {', '.join(missing)}", file=out)
    if not queue:
        print("Nothing to review.", file=out)
        return 0

    try:
        reviewer = (args.reviewer or ask("Your name, for reviewed_by: ")).strip()
    except (EOFError, KeyboardInterrupt):
        return 1
    if not reviewer:
        print("A reviewer name is required.", file=out)
        return 1

    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    print(f"{len(queue)} record(s) to review. q stops at any point; every decision is "
          "saved as you go.", file=out)
    try:
        for index, raw in queue:
            photo = primary_photo(raw, images)
            if photo and photo.is_file() and not args.no_open:
                opener(photo)
            decided = review_one(raw, reviewer, ask, images, out, datetime.now(timezone.utc))
            if decided is None:
                counts["skipped"] += 1
                continue
            gold.replace(index, decided)
            counts[decided["review_state"]] += 1
            print(f"  ✓ {decided['id']} {decided['review_state']}", file=out)
    except (Quit, EOFError, KeyboardInterrupt):
        print("\nStopped.", file=out)

    print(f"\n{counts['approved']} approved, {counts['rejected']} rejected, "
          f"{counts['skipped']} skipped.", file=out)
    if counts["approved"] or counts["rejected"]:
        print("Next:\n"
              "  python -m eval.cli dataset --path eval/data/gold.jsonl   # how many count now\n"
              f"  git -C {images} add -A && git -C {images} commit -m 'Gold photos and evidence'"
              f" && git -C {images} push   # the private store\n"
              "  then commit eval/data/gold.jsonl in this repo (labels only).", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
