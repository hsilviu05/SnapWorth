"""The "Clothing Dataset for Second-Hand Fashion" as SnapWorth eval splits.

    python -m eval.secondhand profile                  # what the copy holds
    python -m eval.secondhand build --price-min 10 --price-max 2000 \\
                                    --price-drop 49 99  # write the manifests

`EVAL_DATA_DIR` is the Zenodo v3 download (`clothing_v3.zip`, DOI
10.5281/zenodo.12518734, CC-BY 4.0): either the zip itself or the folder it
extracts to. Both are read in place. No image is ever copied: a manifest
names each photo by its path inside the download, and `python -m eval.run`
reads it from there.

What the download is, from its Zenodo page: about 31,600 garments sorted at
three stations, each with a front photo, a back photo, a brand-label close-up
and a `labels_<item id>.json`: one annotation object (the Zenodo page says
a list; both are read). The item id is
the time the item was annotated (`2024_04_05_08_47_35`). `station1` and
`station2` are Wargön Innovation, whose prices are suggestions only;
`station3` is Myrorna, a charity-shop chain that sets the prices it sells at.
About 4,000-5,000 brand photos were removed for privacy, and `test100` holds
about 100 items annotated more than once.

Photos are `front_<item id>.jpg`, `back_<item id>.jpg` and
`brand_<item id>.jpg` beside the labels (checked on the first 100 MB of the
v3 zip). A photo is matched to its item by the id in its name and its kind by
the word in it, so a variation in that pattern still matches. `profile` prints what it found, including every annotation key,
so a difference from these assumptions shows before anything is built on it.

Splits (manifests in `eval/data/secondhand/`, committed; labels only):

* `id_gold`: items with a brand, type, category, condition and a front photo,
  ~500 sampled with a fixed seed, stratified by category x type. Each item is
  `dev` or `test` by a hash of its id, as `schema.GoldItem.assigned_split`
  does: iterate on dev, confirm on test, never tune on test.
* `price_silver`: station3 items with a parseable price inside the cutoffs
  chosen after `profile`. The price is kept raw and its currency marked
  unconfirmed. It is a thrift shelf price, not a sale: never gold, never a
  gate input.

`price_gold` is not built here: it is the existing `eval/data/gold.jsonl`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

DATASET = "Clothing Dataset for Second-Hand Fashion, v3"
DOI = "10.5281/zenodo.12518734"
LICENSE = "CC-BY-4.0"
MANIFEST_DIR = Path(__file__).resolve().parent / "data" / "secondhand"
SEED = 20261023
ID_GOLD_TARGET = 500
#: A stratum with fewer eligible items than this is pooled into "other".
POOL_BELOW = 5
TEST_FRACTION = 0.5
PRICE_STATION = "station3"
PRICE_CURRENCY = "unconfirmed (likely SEK)"
BUILDER_VERSION = 1

ITEM_ID = re.compile(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
KINDS = ("front", "back", "brand")
#: Values a sorter typed for "no brand". Such an item has no brand to score.
NO_BRAND = {"", "unknown", "no brand", "nobrand", "no label", "missing", "n/a", "na",
            "none", "-", "unbranded", "okänt", "okant", "inget"}


# ── reading the download ─────────────────────────────────────────────────────

class Source:
    """The download, as a list of member paths and a way to read one."""

    root: str

    def names(self) -> list[str]:
        raise NotImplementedError

    def read(self, name: str) -> bytes:
        raise NotImplementedError


class DirSource(Source):
    def __init__(self, root: Path):
        self.root = str(root)
        self._root = root

    def names(self) -> list[str]:
        return sorted(p.relative_to(self._root).as_posix()
                      for p in self._root.rglob("*") if p.is_file())

    def read(self, name: str) -> bytes:
        return (self._root / name).read_bytes()


class ZipSource(Source):
    def __init__(self, path: Path):
        self.root = str(path)
        self._zip = zipfile.ZipFile(path)

    def names(self) -> list[str]:
        return sorted(n for n in self._zip.namelist() if not n.endswith("/"))

    def read(self, name: str) -> bytes:
        return self._zip.read(name)


def open_source(path: str | os.PathLike | None = None) -> Source:
    """`path`, or `EVAL_DATA_DIR`: the zip or the folder it extracts to."""
    raw = path or os.environ.get("EVAL_DATA_DIR", "")
    if not raw:
        raise SystemExit("EVAL_DATA_DIR is not set. Point it at clothing_v3.zip "
                         "or the folder it extracts to.")
    resolved = Path(raw).expanduser()
    if resolved.is_dir():
        return DirSource(resolved)
    if resolved.is_file() and zipfile.is_zipfile(resolved):
        return ZipSource(resolved)
    raise SystemExit(f"EVAL_DATA_DIR={raw} is neither a folder nor a zip file.")


# ── parsing ─────────────────────────────────────────────────────────────────

@dataclass
class Item:
    id: str
    station: str
    label_path: str
    front: str | None = None
    back: str | None = None
    brand_photo: str | None = None
    brand: str | None = None
    type: str | None = None
    category: str | None = None
    condition: int | None = None
    price_raw: str | None = None
    usage: str | None = None
    annotations: int = 1

    @property
    def day(self) -> date | None:
        return day_of(self.id)

    @property
    def has_brand(self) -> bool:
        return (self.brand or "").strip().lower() not in NO_BRAND

    @property
    def split(self) -> str:
        return split_of(self.id)


@dataclass
class Parsed:
    items: list[Item] = field(default_factory=list)
    #: reason → count: malformed JSON, empty annotation lists, …
    problems: Counter = field(default_factory=Counter)
    #: every annotation key seen, with how often
    keys: Counter = field(default_factory=Counter)
    #: label files that had more than one annotation, by id
    multi: dict[str, list[dict]] = field(default_factory=dict)


def day_of(item_id: str) -> date | None:
    match = ITEM_ID.search(item_id)
    if not match:
        return None
    year, month, day_ = (int(x) for x in match.group(1).split("_")[:3])
    try:
        return date(year, month, day_)
    except ValueError:
        return None


def split_of(item_id: str, test_fraction: float = TEST_FRACTION) -> str:
    """`dev` or `test`, from a hash of the id (`schema.GoldItem.assigned_split`)."""
    bucket = int(hashlib.sha256(item_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if bucket < test_fraction else "dev"


def station_of(name: str) -> str:
    for part in PurePosixPath(name).parts:
        low = part.lower()
        if low.startswith("station") or low == "test100":
            return low
    return "unknown"


def _text(value) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def parse_condition(value) -> int | None:
    """A 1-5 grade from `3`, `"3"`, `"3.0"` or `"3 - Good"`; None otherwise."""
    text = _text(value)
    if text is None:
        return None
    match = re.match(r"\s*([1-5])(?:\.0+)?\b", text)
    return int(match.group(1)) if match else None


_BAND = re.compile(r"^\s*(?:([<>])\s*(\d+)|(\d+)\s*-\s*(\d+))\s*$")


def parse_price_band(value) -> tuple[float, float | None] | None:
    """A price band as station1 records it, `"<50"`, `"50-100"` or `">500"`,
    as (low, high); high is None for an open top. None when it is not a band."""
    text = _text(value)
    match = _BAND.match(text.replace(" ", "")) if text else None
    if not match:
        return None
    sign, bound, low, high = match.groups()
    if sign == "<":
        return 0.0, float(bound)
    if sign == ">":
        return float(bound), None
    return float(low), float(high)


def parse_price(value) -> float | None:
    """A number from a raw price: `49`, `"49"`, `"49,50"`, `"49 kr"`. None for
    empty, non-numeric, negative or non-finite, and for a band such as
    `"50-100"`, which is not one price (`parse_price_band`)."""
    text = _text(value)
    if text is None or parse_price_band(text) is not None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text.replace(" ", ""))
    if not match:
        return None
    number = float(match.group(0).replace(",", "."))
    return number if number >= 0 and math.isfinite(number) else None


def _pick(record: dict, *names: str) -> str | None:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        if (value := _text(lowered.get(name))) is not None:
            return value
    return None


def image_index(names: Iterable[str]) -> dict[str, dict[str, str]]:
    """item id → kind → member path, from the photos' file names."""
    index: dict[str, dict[str, str]] = defaultdict(dict)
    for name in names:
        if not name.lower().endswith(IMAGE_SUFFIXES):
            continue
        base = PurePosixPath(name).name.lower()
        match = ITEM_ID.search(base)
        kind = next((k for k in KINDS if k in base), None)
        if match and kind:
            index[match.group(1)].setdefault(kind, name)
    return index


def parse_label(name: str, raw: bytes, photos: dict[str, dict[str, str]],
                parsed: Parsed) -> None:
    """One `labels_<id>.json` into `parsed`: an item, or a counted problem."""
    match = ITEM_ID.search(PurePosixPath(name).name)
    if not match:
        parsed.problems["label file without an item id"] += 1
        return
    item_id = match.group(1)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed.problems["malformed JSON"] += 1
        return
    records = data if isinstance(data, list) else [data]
    records = [r for r in records if isinstance(r, dict)]
    if not records:
        parsed.problems["no annotation in the file"] += 1
        return
    if len(records) > 1:
        parsed.multi[item_id] = records
    record = records[0]
    parsed.keys.update(str(k).lower() for k in record)
    found = photos.get(item_id, {})
    parsed.items.append(Item(
        id=item_id, station=station_of(name), label_path=name,
        front=found.get("front"), back=found.get("back"), brand_photo=found.get("brand"),
        brand=_pick(record, "brand", "brandtext"),
        type=_pick(record, "type"),
        category=_pick(record, "category"),
        condition=parse_condition(record.get("condition")),
        price_raw=_pick(record, "price"),
        usage=_pick(record, "usage"),
        annotations=len(records),
    ))


def is_label_file(name: str) -> bool:
    base = PurePosixPath(name).name.lower()
    return base.endswith(".json") and "json_errors" not in name.lower() and \
        ITEM_ID.search(base) is not None


def parse(source: Source) -> Parsed:
    names = source.names()
    photos = image_index(names)
    parsed = Parsed()
    seen: set[str] = set()
    for name in names:
        if "json_errors" in name.lower() and name.lower().endswith(".json"):
            parsed.problems["file in json_errors/ (skipped)"] += 1
            continue
        if not is_label_file(name):
            continue
        try:
            raw = source.read(name)
        except (OSError, KeyError, zipfile.BadZipFile):
            parsed.problems["label file unreadable"] += 1
            continue
        before = len(parsed.items)
        parse_label(name, raw, photos, parsed)
        if len(parsed.items) > before:
            item = parsed.items[-1]
            if item.id in seen:
                parsed.items.pop()
                parsed.problems["duplicate item id (kept the first)"] += 1
            else:
                seen.add(item.id)
    for item in parsed.items:
        if item.front is None:
            parsed.problems["front photo missing"] += 1
        if item.brand_photo is None:
            parsed.problems["brand photo missing"] += 1
    return parsed


# ── profile ──────────────────────────────────────────────────────────────────

def price_profile(items: list[Item]) -> dict:
    """What station3's prices look like, so cutoffs are chosen from the data."""
    raw = [i.price_raw for i in items]
    numbers = [p for r in raw if (p := parse_price(r)) is not None]
    positive = sorted(p for p in numbers if p > 0)
    counts = Counter(numbers)
    bands = Counter(r for r in raw if r is not None and parse_price_band(r) is not None)
    buckets = [0, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, float("inf")]
    histogram = {}
    for low, high in zip(buckets, buckets[1:]):
        label = f"{low:g}-{high:g}" if high != float("inf") else f"{low:g}+"
        histogram[label] = sum(1 for p in positive if low <= p < high)
    return {
        "items": len(items),
        "missing": sum(1 for r in raw if r is None),
        "bands": sum(bands.values()),
        "unparseable": sum(1 for r in raw if r is not None and parse_price(r) is None
                           and parse_price_band(r) is None),
        "zero": counts.get(0.0, 0),
        "positive": len(positive),
        "min": positive[0] if positive else None,
        "median": statistics.median(positive) if positive else None,
        "p90": positive[int(len(positive) * 0.9)] if positive else None,
        "max": positive[-1] if positive else None,
        "most_common": [[value, n] for value, n in counts.most_common(25)],
        "most_common_bands": [[value, n] for value, n in bands.most_common(10)],
        "histogram": histogram,
    }


def profile(parsed: Parsed) -> dict:
    items = parsed.items
    by_station = Counter(i.station for i in items)

    def top(values: Iterable[str | int | None], n: int = 30) -> list[list]:
        return [[v, c] for v, c in Counter(v for v in values if v is not None).most_common(n)]

    return {
        "items": len(items),
        "by_station": dict(sorted(by_station.items())),
        "problems": dict(sorted(parsed.problems.items())),
        "annotation_keys": dict(parsed.keys.most_common()),
        "multi_annotated": len(parsed.multi),
        "with_brand": sum(1 for i in items if i.has_brand),
        "with_condition": sum(1 for i in items if i.condition is not None),
        "eligible_id_gold": len(eligible_for_id(items)),
        "top": {
            "brand": top(i.brand for i in items),
            "type": top(i.type for i in items),
            "category": top(i.category for i in items),
            "condition": top(i.condition for i in items),
            "usage": top(i.usage for i in items),
        },
        "price_station3": price_profile([i for i in items if i.station == PRICE_STATION]),
    }


# ── splits ───────────────────────────────────────────────────────────────────

def eligible_for_id(items: Iterable[Item]) -> list[Item]:
    return [i for i in items
            if i.has_brand and i.type and i.category and i.condition is not None and i.front]


def stratified_sample(items: list[Item], target: int, seed: int = SEED,
                      pool_below: int = POOL_BELOW) -> list[Item]:
    """`target` items, proportional to category x type, at least one from every
    stratum kept, strata smaller than `pool_below` pooled into one. The same
    input and seed always give the same sample."""
    if target <= 0 or not items:
        return []
    ordered = sorted(items, key=lambda i: i.id)
    strata: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for item in ordered:
        strata[((item.category or "").lower(), (item.type or "").lower())].append(item)
    pooled: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for key, members in strata.items():
        pooled[key if len(members) >= pool_below else ("other", "other")].extend(members)
    total = sum(len(m) for m in pooled.values())
    if target >= total:
        return ordered
    keys = sorted(pooled)
    exact = {k: target * len(pooled[k]) / total for k in keys}
    quota = {k: max(1, int(exact[k])) for k in keys}
    remainder = target - sum(quota.values())
    for k in sorted(keys, key=lambda k: (-(exact[k] - int(exact[k])), k)):
        if remainder <= 0:
            break
        if quota[k] < len(pooled[k]):
            quota[k] += 1
            remainder -= 1
    while sum(quota.values()) > target:          # more strata than target
        biggest = max(keys, key=lambda k: (quota[k], k))
        quota[biggest] -= 1
    rng = random.Random(seed)
    chosen: list[Item] = []
    for k in keys:
        members = sorted(pooled[k], key=lambda i: i.id)
        chosen.extend(rng.sample(members, min(quota[k], len(members))))
    return sorted(chosen, key=lambda i: i.id)


@dataclass(frozen=True)
class PriceCutoffs:
    minimum: float
    maximum: float
    drop: tuple[float, ...] = ()


def price_rows(items: Iterable[Item], cutoffs: PriceCutoffs) -> tuple[list[Item], Counter]:
    """Station3 items whose price survives the cutoffs, and why the rest did not."""
    kept: list[Item] = []
    dropped: Counter = Counter()
    drop = set(cutoffs.drop)
    for item in sorted(items, key=lambda i: i.id):
        if item.station != PRICE_STATION:
            continue
        price = parse_price(item.price_raw)
        if price is None and parse_price_band(item.price_raw) is not None:
            dropped["a price band, not a price"] += 1
        elif price is None:
            dropped["no parseable price"] += 1
        elif price == 0:
            dropped["zero"] += 1
        elif price in drop:
            dropped[f"fixed value {price:g}"] += 1
        elif price < cutoffs.minimum:
            dropped[f"below {cutoffs.minimum:g}"] += 1
        elif price > cutoffs.maximum:
            dropped[f"above {cutoffs.maximum:g}"] += 1
        elif not item.front:
            dropped["front photo missing"] += 1
        elif item.day is None:
            dropped["no date in item id"] += 1
        else:
            kept.append(item)
    return kept, dropped


def manifest_row(item: Item, with_price: bool = False) -> dict:
    row = {"id": item.id, "station": item.station, "split": item.split,
           "front": item.front, "back": item.back, "brand_photo": item.brand_photo,
           "brand": item.brand, "type": item.type, "category": item.category,
           "condition": item.condition}
    if with_price:
        row.update({"price_raw": item.price_raw, "price": parse_price(item.price_raw),
                    "currency": PRICE_CURRENCY,
                    "date": item.day.isoformat() if item.day else None})
    return row


def write_manifest(name: str, rows: list[dict], meta: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                            for r in rows), encoding="utf-8")
    (out_dir / f"{name}.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(path: Path) -> tuple[list[dict], Counter]:
    """Rows of a manifest; a malformed line is counted and skipped."""
    rows: list[dict] = []
    problems: Counter = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems["malformed manifest line"] += 1
            continue
        if isinstance(row, dict) and row.get("id"):
            rows.append(row)
        else:
            problems["manifest line without an id"] += 1
    return rows, problems


def _meta(source: Source, split: str, rows: int, **extra) -> dict:
    return {"dataset": DATASET, "doi": DOI, "license": LICENSE, "split": split,
            "source": Path(source.root).name, "builder_version": BUILDER_VERSION,
            "seed": SEED, "rows": rows, **extra}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_profile(report: dict, out) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str), file=out)


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m eval.secondhand", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", help="the download (default: $EVAL_DATA_DIR)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_profile = sub.add_parser("profile", help="what the download holds")
    p_profile.add_argument("--json-out", help="also write the profile here")
    p_build = sub.add_parser("build", help="write the id_gold and price_silver manifests")
    p_build.add_argument("--target", type=int, default=ID_GOLD_TARGET)
    p_build.add_argument("--price-min", type=float, required=True)
    p_build.add_argument("--price-max", type=float, required=True)
    p_build.add_argument("--price-drop", type=float, nargs="*", default=[],
                         help="fixed prices to leave out, e.g. 49 99")
    p_build.add_argument("--out", default=str(MANIFEST_DIR))
    args = parser.parse_args(argv)

    source = open_source(args.data)
    parsed = parse(source)
    if args.command == "profile":
        report = profile(parsed)
        _print_profile(report, out)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(report, indent=2, default=str) + "\n",
                                           encoding="utf-8")
        return 0

    out_dir = Path(args.out)
    sample = stratified_sample(eligible_for_id(parsed.items), args.target)
    write_manifest("id_gold", [manifest_row(i) for i in sample],
                   _meta(source, "id_gold", len(sample), target=args.target,
                         pool_below=POOL_BELOW, test_fraction=TEST_FRACTION,
                         by_split=dict(Counter(i.split for i in sample))), out_dir)
    cutoffs = PriceCutoffs(args.price_min, args.price_max, tuple(args.price_drop))
    kept, dropped = price_rows(parsed.items, cutoffs)
    write_manifest("price_silver", [manifest_row(i, with_price=True) for i in kept],
                   _meta(source, "price_silver", len(kept), cutoffs=asdict(cutoffs),
                         currency=PRICE_CURRENCY, dropped=dict(sorted(dropped.items()))),
                   out_dir)
    print(f"id_gold: {len(sample)} items "
          f"({sum(1 for i in sample if i.split == 'dev')} dev, "
          f"{sum(1 for i in sample if i.split == 'test')} test)", file=out)
    print(f"price_silver: {len(kept)} items; dropped "
          + (", ".join(f"{k} {v}" for k, v in sorted(dropped.items())) or "none"), file=out)
    if parsed.problems:
        print("problems: " + ", ".join(f"{k} {v}" for k, v in sorted(parsed.problems.items())),
              file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
