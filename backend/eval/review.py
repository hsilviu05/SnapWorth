"""Gold-set review: approve the intake's drafts one at a time (#213).

    python -m eval.review                       # every draft, in id order
    python -m eval.review --only G-0007         # one record
    python -m eval.review --reviewer silviu     # default: git config user.name

The intake writes drafts, and a draft counts toward nothing. This walks each
record that still needs a person (`draft`, `pending_review`, `needs_relabel`),
opens its photo, shows its labels and anything `GoldItem.validate` objects to,
and asks what to do:

* **e**dit — step through the labels; Enter keeps a value, `-` clears it.
* **a**pprove — only when the record validates. Sets `review_state`,
  `reviewed_by` and `reviewed_at`.
* **r**eject — kept in the file as `rejected`, so its photo is never taken
  again by the intake's duplicate check, and counted nowhere.
* **s**kip, **q**uit.

Every decision is written to `gold.jsonl` at once, so quitting, or Ctrl-C,
loses nothing already decided. Lines it does not change are left byte for
byte, comments included.

Evidence goes through the intake's rule: a `private:<ref>` naming a file in
the private photo store, never a web address.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import intake, schema

OPEN_STATES = {schema.ReviewState.DRAFT.value, schema.ReviewState.PENDING_REVIEW.value,
               schema.ReviewState.NEEDS_RELABEL.value}

Ask = Callable[[str], str]
Say = Callable[[str], None]
Opener = Callable[[Path], None]


@dataclass(frozen=True)
class Field:
    key: str            # the record's key
    label: str          # what the prompt says
    choices: tuple[str, ...] = ()
    hint: str = ""


FIELDS: tuple[Field, ...] = (
    Field("actual_sale_price", "price"),
    Field("currency", "currency", hint="ISO 4217, as it sold: USD, RON, EUR"),
    Field("category", "category", tuple(schema.TARGET_CATEGORIES)),
    Field("brand", "brand"),
    Field("model", "model"),
    Field("variant", "variant", hint="size, colour, storage"),
    Field("condition", "condition", hint="new, like new, good, fair, poor"),
    Field("region", "region", hint="the country it sold in: US, RO, DE"),
    Field("sold_date", "sold date", hint="YYYY-MM-DD"),
    Field("marketplace", "marketplace"),
    Field("label_confidence", "label confidence",
          tuple(c.value for c in schema.LabelConfidence)),
    Field("evidence_note", "evidence", hint="private:<file in the photo store>"),
    Field("difficulty", "difficulty", tuple(d.value for d in schema.Difficulty)),
    Field("tags", "tags", hint="; between tags"),
    Field("human_notes", "notes"),
)


# ── The file ─────────────────────────────────────────────────────────────────

@dataclass
class Line:
    text: str               # as read, without the newline
    record: dict | None     # parsed, for a record line; None for anything else
    dirty: bool = False

    def render(self) -> str:
        if self.record is not None and self.dirty:
            return json.dumps(self.record, sort_keys=True)
        return self.text


def read_lines(path: Path) -> list[Line]:
    lines: list[Line] = []
    for text in path.read_text(encoding="utf-8").splitlines():
        stripped = text.strip()
        record = None
        if stripped and not stripped.startswith("#"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and "id" in parsed:
                record = parsed
        lines.append(Line(text, record))
    return lines


def write_lines(path: Path, lines: list[Line]) -> None:
    """Replace the file in one step, so an interrupted write leaves the old one."""
    rendered = [line.render() for line in lines]
    body = "".join(text + "\n" for text in rendered)
    handle, scratch = tempfile.mkstemp(dir=path.parent, prefix=".gold-", suffix=".jsonl")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(body)
        os.replace(scratch, path)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise
    for line, text in zip(lines, rendered):
        line.text, line.dirty = text, False


# ── One record ───────────────────────────────────────────────────────────────

def problems_of(record: dict) -> list[str]:
    item = schema.item_from_dict(record)
    if item is None:
        return ["not a valid gold record (price, category and images are required)"]
    return item.validate()


def shown(record: dict, key: str) -> str:
    value = record.get(key)
    if key == "tags":
        return "; ".join(value or []) or "-"
    if value is None or value == "":
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def describe(record: dict) -> list[str]:
    lines = [f"── {record.get('id')}  ({record.get('review_state', 'draft')})"]
    width = max(len(f.label) for f in FIELDS)
    lines += [f"  {f.label:<{width}}  {shown(record, f.key)}" for f in FIELDS]
    return lines


def parse(field: Field, raw: str) -> tuple[object, str | None]:
    """(value, problem) for what was typed. `-` clears."""
    text = raw.strip()
    if text == "-":
        if field.key in ("actual_sale_price", "currency", "category"):
            return None, f"{field.label} can't be empty"
        return ([] if field.key == "tags" else ""
                if field.key in ("evidence_note", "human_notes") else None), None
    if field.choices and text not in field.choices:
        return None, f"{field.label} is one of: {', '.join(field.choices)}"
    if field.key == "actual_sale_price":
        price = intake._money(text)
        if price is None or not math.isfinite(price):
            return None, "price is a number, e.g. 45 or 45.50"
        return price, None
    if field.key == "currency":
        if len(text) != 3 or not text.isalpha():
            return None, "currency is three letters, e.g. USD"
        return text.upper(), None
    if field.key == "region":
        return text.upper(), None   # validate() names a bad code
    if field.key == "sold_date":
        try:
            when = date.fromisoformat(text)
        except ValueError:
            return None, "sold date is YYYY-MM-DD"
        if when > date.today():
            return None, "sold date is in the future"
        return when.isoformat(), None
    if field.key == "evidence_note":
        note, problem = intake.evidence_note(text)
        return note, problem
    if field.key == "tags":
        return [t.strip() for t in text.split(";") if t.strip()], None
    return text, None


def edit(record: dict, ask: Ask, say: Say) -> None:
    say("Enter keeps a value, - clears it.")
    for field in FIELDS:
        extra = f" ({' | '.join(field.choices)})" if field.choices else (
            f" ({field.hint})" if field.hint else "")
        while True:
            raw = ask(f"  {field.label}{extra} [{shown(record, field.key)}]: ")
            if not raw.strip():
                break
            value, problem = parse(field, raw)
            if problem:
                say(f"    {problem}")
                continue
            if value is None:
                record.pop(field.key, None)
            else:
                record[field.key] = value
            break


def decide(record: dict, state: str, reviewer: str, now: datetime) -> None:
    record["review_state"] = state
    record["reviewed_by"] = reviewer
    record["reviewed_at"] = now.isoformat()


# ── The session ──────────────────────────────────────────────────────────────

def mac_open(path: Path) -> None:
    if sys.platform == "darwin" and path.is_file():
        subprocess.run(["open", str(path)], check=False)


def review(path: Path, reviewer: str, ask: Ask = input, say: Say = print,
           opener: Opener = mac_open, only: str | None = None,
           now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Walk the open records. Returns counts of what was decided."""
    lines = read_lines(path)
    todo = [line for line in lines if line.record is not None
            and (line.record.get("id") == only if only
                 else line.record.get("review_state", "draft") in OPEN_STATES)]
    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    if not todo:
        say(f"Nothing to review{f' with id {only}' if only else ''} in {path}.")
        return counts

    say(f"{len(todo)} record(s) to review.")
    for index, line in enumerate(todo):
        record = line.record
        assert record is not None
        primary = next((i for i in record.get("images", []) if i.get("is_primary")),
                       (record.get("images") or [{}])[0])
        if primary.get("path"):
            opener(path.parent / primary["path"])
        while True:
            say("")
            for text in describe(record):
                say(text)
            found = problems_of(record)
            for problem in found:
                say(f"  ! {problem}")
            if not found and record.get("label_confidence") not in ("certain", "high"):
                say("  · approvable, but only certain or high counts toward the headline")
            choice = ask(f"[{index + 1}/{len(todo)}] (a)pprove (e)dit (r)eject "
                         "(s)kip (q)uit: ").strip().lower()
            if choice == "e":
                edit(record, ask, say)
                line.dirty = True
                write_lines(path, lines)
                continue
            if choice == "a":
                if found:
                    say("  Not approved: fix the lines marked ! first (e to edit).")
                    continue
                decide(record, schema.ReviewState.APPROVED.value, reviewer, now())
                line.dirty = True
                write_lines(path, lines)
                counts["approved"] += 1
                break
            if choice == "r":
                reason = ask("  Why (kept in the notes; Enter for none): ").strip()
                if reason:
                    notes = record.get("human_notes") or ""
                    record["human_notes"] = (notes + " " if notes else "") + \
                        f"Rejected: {reason}"
                decide(record, schema.ReviewState.REJECTED.value, reviewer, now())
                line.dirty = True
                write_lines(path, lines)
                counts["rejected"] += 1
                break
            if choice == "s":
                counts["skipped"] += 1
                break
            if choice == "q":
                return counts
            say("  a, e, r, s or q.")
    return counts


def headline_count(path: Path) -> int:
    return sum(1 for item in schema.load_gold(path) if item.counts_toward_headline)


def git_user() -> str | None:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True,
                              text=True, check=False).stdout.strip()
    except OSError:
        return None
    return name or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", default=str(intake.DEFAULT_GOLD),
                        help="the gold set (default eval/data/gold.jsonl)")
    parser.add_argument("--reviewer", default=None,
                        help="the name written to reviewed_by (default: git config user.name)")
    parser.add_argument("--only", default=None, help="review one record by id, whatever its state")
    parser.add_argument("--no-open", action="store_true", help="don't open each photo")
    args = parser.parse_args(argv)

    gold = Path(args.gold)
    if not gold.is_file():
        print(f"{gold} doesn't exist yet. Add sales with python -m eval.intake first.",
              file=sys.stderr)
        return 1
    reviewer = args.reviewer or git_user()
    if not reviewer:
        print("Who is reviewing? Pass --reviewer NAME.", file=sys.stderr)
        return 1

    try:
        counts = review(gold, reviewer, only=args.only,
                        opener=(lambda _p: None) if args.no_open else mac_open)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Every decision made so far is saved.")
        counts = None
    if counts is not None:
        print(f"\nApproved {counts['approved']}, rejected {counts['rejected']}, "
              f"skipped {counts['skipped']}.")
    print(f"{headline_count(gold)} record(s) now count toward the headline "
          "(approved, certain or high). Aim for 60: about 30% go to the test split, "
          "and the prompt experiment needs 30 in dev.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
