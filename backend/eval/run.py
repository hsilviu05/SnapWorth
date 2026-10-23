"""Score a running backend's `/scan` on the second-hand clothing splits.

    EVAL_BACKEND_URL=http://127.0.0.1:8000 EVAL_DATA_DIR=~/data/clothing_v3.zip \\
        python -m eval.run --split id_gold --limit 20
    python -m eval.run --split price_silver
    python -m eval.run --split price_gold          # the approved gold.jsonl sales

`eval.runner` is the in-process harness for the gold set; this one goes over
HTTP, so it measures `/scan` as served. Configuration comes from the
environment (or `backend/.env`): `EVAL_BACKEND_URL`, `EVAL_DATA_DIR` and,
optionally, `EVAL_SCAN_TOKEN`, a bearer token for the local server. The
token is never printed or written.

**Only a local backend unless told otherwise.** A production scan bills a
model call, counts toward `/trends` and posts to the operator feed, so a URL
that is not localhost is refused without `--allow-remote`. On the local
server, raise `FREE_SCANS_PER_DAY`, `RATE_MAX_REQUESTS` and
`IP_RATE_MAX_REQUESTS` for the run: without a token every request is the same
free caller.

**Which photos are sent.** The front photo is `file`. The brand photo goes as
`tag` when a token is set (`--tag auto`), because the server uses a tag photo
only for a Pro caller and silently drops it otherwise. The back photo has no
slot in `/scan` and is never sent.

**`id_gold` runs its dev half by default.** Iterate on dev; run `--subset
test` to confirm a change, not to tune one.

What is scored, and how, is in `eval/README.md`. Every item
that could not be scored is counted with its reason in the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import fx, metrics, scanclient, secondhand  # noqa: E402

SPLITS = ("id_gold", "price_silver", "price_gold")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
CACHE_DIR = secondhand.MANIFEST_DIR / "cache"
RUNS_DIR = secondhand.MANIFEST_DIR / "runs"
GOLD_PATH = Path(__file__).resolve().parent / "data" / "gold.jsonl"
#: A group smaller than this shows its n and no figures (`eval/field.py`).
MIN_GROUP = 5
WORST = 20
PRICE_SILVER_BASIS = ("Myrorna (station3) shelf-price bands, currency assumed SEK; the "
                      "scan's USD converted at the item's annotation date. A thrift price, "
                      "not a sale: a sanity check, never a gate input.")
PRICE_GOLD_BASIS = "approved gold.jsonl sales, certain/high label confidence"

#: Dataset garment type → the app's category (`scan/categories.py`). A type
#: not listed is `clothing`, which is what nearly every garment is.
TYPE_CATEGORY = {
    "shoes": "shoes", "boots": "shoes", "sneakers": "shoes", "sandals": "shoes",
    "heels": "shoes", "slippers": "shoes",
    "bag": "accessories", "handbag": "accessories", "backpack": "accessories",
    "scarf": "accessories", "hat": "accessories", "cap": "accessories",
    "beanie": "accessories", "belt": "accessories", "gloves": "accessories",
    "tie": "accessories", "accessories": "accessories", "accessory": "accessories",
}

#: Dataset garment type → words that, in the scan's `item_name` or
#: `model_name`, name that type. A type not listed is not scored for type.
TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "jeans": ("jeans", "denim pants", "denim trousers"),
    "trousers": ("trousers", "pants", "chinos", "slacks", "joggers", "leggings", "jeans"),
    "pants": ("trousers", "pants", "chinos", "slacks", "joggers", "leggings", "jeans"),
    "shorts": ("shorts",),
    "skirt": ("skirt",),
    "dress": ("dress", "gown"),
    "t-shirt": ("t-shirt", "tee", "t shirt", "tshirt"),
    "top": ("top", "tank", "camisole", "blouse", "t-shirt", "tee"),
    "tank top": ("tank", "camisole", "top", "singlet", "vest top"),
    "tunic": ("tunic", "dress", "top", "blouse"),
    "blouse": ("blouse", "shirt", "top"),
    "shirt": ("shirt", "button-down", "button-up", "flannel", "polo"),
    "sweater": ("sweater", "jumper", "pullover", "knit", "cardigan"),
    "cardigan": ("cardigan", "knit"),
    "hoodie": ("hoodie", "hooded", "sweatshirt"),
    "sweatshirt": ("sweatshirt", "hoodie", "crewneck"),
    "jacket": ("jacket", "parka", "anorak", "windbreaker", "bomber", "blazer"),
    "winter jacket": ("jacket", "parka", "anorak", "coat", "puffer"),
    "rain jacket": ("jacket", "raincoat", "rain", "anorak", "shell"),
    "denim jacket": ("jacket", "denim"),
    "outerwear": ("jacket", "coat", "parka", "anorak", "windbreaker", "gilet", "vest"),
    "coat": ("coat", "parka", "trench", "overcoat", "peacoat"),
    "blazer": ("blazer", "suit jacket", "sport coat"),
    "suit": ("suit",),
    "vest": ("vest", "gilet", "waistcoat"),
    "jumpsuit": ("jumpsuit", "overall", "playsuit", "romper", "dungarees"),
    "overall": ("overall", "jumpsuit", "dungarees", "coverall"),
    "swimwear": ("swim", "bikini", "swimsuit", "trunks"),
    "pyjamas": ("pajama", "pyjama", "sleepwear", "nightgown"),
    "pajamas": ("pajama", "pyjama", "sleepwear", "nightgown"),
    "nightgown": ("nightgown", "nightdress", "nightie", "night dress", "sleepwear", "slip"),
    "robe": ("robe", "bathrobe", "dressing gown", "kimono"),
    "training top": ("top", "t-shirt", "tee", "shirt", "jersey", "sports", "athletic"),
    "rain trousers": ("trousers", "pants", "rain"),
    "winter trousers": ("trousers", "pants", "snow", "ski"),
    "underwear": ("underwear", "briefs", "boxers", "bra", "panties"),
    "socks": ("socks",),
    "tights": ("tights", "stockings", "leggings"),
    "shoes": ("shoe", "sneaker", "trainer", "boot", "loafer", "sandal", "heel", "pump"),
    "boots": ("boot",),
    "bag": ("bag", "tote", "purse", "handbag", "backpack", "clutch"),
    "scarf": ("scarf", "shawl"),
    "hat": ("hat", "cap", "beanie"),
    "belt": ("belt",),
}


def app_category(dataset_type: str | None) -> str:
    return TYPE_CATEGORY.get(secondhand.canonical("type", dataset_type), "clothing")


def type_verdict(dataset_type: str | None, item_name: str | None,
                 model_name: str | None = None) -> str | None:
    """`match`, `miss`, or None when the dataset type is not in `TYPE_WORDS`."""
    words = TYPE_WORDS.get(secondhand.canonical("type", dataset_type))
    if not words:
        return None
    text = f" {item_name or ''} {model_name or ''} ".lower()
    for ch in ",.;:/()[]\"'":
        text = text.replace(ch, " ")
    return "match" if any(f" {w}" in text for w in words) else "miss"


# ── what is run ──────────────────────────────────────────────────────────────

@dataclass
class Case:
    """One item to send and what to score it against."""
    id: str
    group: str                         # the dataset type, for per-category figures
    front: str | None
    brand_photo: str | None = None
    split: str | None = None
    brand: str | None = None
    type: str | None = None
    condition: int | None = None
    price_usd: float | None = None
    price_raw: str | None = None
    #: price_silver: the band in its own currency, and that currency per USD
    band: tuple[float, float | None] | None = None
    local_per_usd: float | None = None


@dataclass
class Loaded:
    cases: list[Case] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    read: Callable[[str], bytes] | None = None
    #: what the price labels are in, for the report
    basis: str | None = None
    fx_table: str | None = None


def load_cases(split: str, subset: str, manifest_dir: Path, data: str | None,
               rates: fx.RateTable | None = None) -> Loaded:
    loaded = Loaded()
    if split == "price_gold":
        return _load_gold(loaded, rates or fx.load())

    rows, problems = secondhand.load_manifest(manifest_dir / f"{split}.jsonl")
    loaded.skipped.update(problems)
    source = secondhand.open_source(data)
    loaded.read = source.read
    rates = rates or fx.load()
    if split == "price_silver":
        loaded.basis, loaded.fx_table = PRICE_SILVER_BASIS, rates.version
    for row in rows:
        if split == "id_gold" and subset != "all" and row.get("split") != subset:
            continue
        case = Case(id=str(row["id"]), group=secondhand.canonical("type", row.get("type"))
                    or "unknown",
                    front=row.get("front"), brand_photo=row.get("brand_photo"),
                    split=row.get("split"), brand=row.get("brand"), type=row.get("type"),
                    condition=row.get("condition"), price_raw=row.get("price_raw"))
        if split == "price_silver":
            low, high, day = row.get("price_low"), row.get("price_high"), row.get("date")
            if (not isinstance(low, (int, float)) or low < 0 or not day
                    or (high is not None and (not isinstance(high, (int, float)) or high < low))):
                loaded.skipped["price band or date missing in manifest"] += 1
                continue
            try:
                usd_per_local = rates.to_usd(1.0, "SEK", datetime.fromisoformat(day).date())
            except (fx.FxUnavailable, ValueError) as exc:
                loaded.skipped[f"no SEK rate: {str(exc)[:60]}"] += 1
                continue
            case.band = (float(low), None if high is None else float(high))
            case.local_per_usd = 1 / usd_per_local
        loaded.cases.append(case)
    return loaded


def _load_gold(loaded: Loaded, rates: fx.RateTable) -> Loaded:
    from eval import runner, schema

    if not GOLD_PATH.is_file():
        loaded.skipped["no eval/data/gold.jsonl"] += 1
        return loaded
    items, excluded = runner.load_items(GOLD_PATH)
    loaded.skipped.update(excluded)
    tags = {g.id: next((i.path for i in g.images if i.angle == "tag"), None)
            for g in schema.load_gold(GOLD_PATH)}
    root = GOLD_PATH.parent
    loaded.read = lambda name: (root / name).read_bytes()
    loaded.basis, loaded.fx_table = PRICE_GOLD_BASIS, rates.version
    for item in items:
        loaded.cases.append(Case(id=item.id, group=item.category, front=item.image_path,
                                 brand_photo=tags.get(item.id), split=item.split,
                                 brand=item.expected_brand, price_usd=item.expected_price))
    return loaded


def build_requests(loaded: Loaded, send_tag: bool, prepare: Callable[[bytes], bytes]
                   ) -> tuple[list[scanclient.ScanRequest], Counter]:
    """A request per case whose front photo can be read; the rest counted."""
    requests: list[scanclient.ScanRequest] = []
    problems: Counter = Counter()
    assert loaded.read is not None or not loaded.cases
    for case in loaded.cases:
        if not case.front:
            problems["front photo missing (not sent)"] += 1
            continue
        try:
            photo = prepare(loaded.read(case.front))  # type: ignore[misc]
        except Exception:      # unreadable or undecodable: count it, keep going
            problems["front photo unreadable (not sent)"] += 1
            continue
        tag = None
        if send_tag:
            if not case.brand_photo:
                problems["brand photo missing (sent front only)"] += 1
            else:
                try:
                    tag = prepare(loaded.read(case.brand_photo))  # type: ignore[misc]
                except Exception:
                    problems["brand photo unreadable (sent front only)"] += 1
        requests.append(scanclient.ScanRequest(case.id, photo, tag))
    return requests, problems


# ── scoring ──────────────────────────────────────────────────────────────────

def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value == value else None


def predicted_point(body: dict) -> tuple[float | None, str]:
    """`likely_price_usd`, or the range's middle when a response has none."""
    likely = _number(body.get("likely_price_usd"))
    if likely is not None and likely > 0:
        return likely, "likely"
    low, high = _number(body.get("est_value_low_usd")), _number(body.get("est_value_high_usd"))
    if low is not None and high is not None and high > 0:
        return (low + high) / 2, "midpoint"
    return None, "none"


def score_identification(cases: list[Case], outcomes: dict[str, scanclient.ScanOutcome]
                         ) -> tuple[dict, list[dict]]:
    rows: list[dict] = []
    for case in cases:
        outcome = outcomes.get(case.id)
        if outcome is None or not outcome.ok:
            continue
        body = outcome.body or {}
        brand_v = metrics.brand_verdict(body.get("brand"), case.brand)
        type_v = type_verdict(case.type, body.get("item_name"), body.get("model_name"))
        grade = body.get("condition_grade")
        ordinal = metrics.CONDITION_ORDINAL.get(grade or "")
        rows.append({
            "id": case.id, "group": case.group, "brand_photo": bool(case.brand_photo),
            "sent_tag": outcome.sent_tag,
            "expected": {"brand": case.brand, "type": case.type,
                         "category": app_category(case.type), "condition": case.condition},
            "predicted": {"brand": body.get("brand"), "item_name": body.get("item_name"),
                          "category": body.get("category"), "condition_grade": grade},
            "brand": brand_v, "type": type_v,
            "category_ok": (body.get("category") or "").lower() == app_category(case.type),
            "condition_error": (abs(ordinal - case.condition)
                                if ordinal is not None and case.condition else None),
        })

    def summary(subset: list[dict]) -> dict:
        typed = [r for r in subset if r["type"] is not None]
        return {
            "n": len(subset),
            "brand": metrics.brand_accuracy(
                [(r["predicted"]["brand"], r["expected"]["brand"]) for r in subset]),
            "category_accuracy": (sum(r["category_ok"] for r in subset) / len(subset)
                                  if subset else None),
            "type_accuracy": (sum(r["type"] == "match" for r in typed) / len(typed)
                              if typed else None),
            "type_scored": len(typed),
            "condition": metrics.condition_error(
                [(r["predicted"]["condition_grade"], r["expected"]["condition"])
                 for r in subset]),
        }

    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_group[row["group"]].append(row)
    result = {
        "overall": summary(rows),
        "brand_photo_present": summary([r for r in rows if r["brand_photo"]]),
        "brand_photo_absent": summary([r for r in rows if not r["brand_photo"]]),
        "by_type": {g: (summary(rs) if len(rs) >= MIN_GROUP else {"n": len(rs)})
                    for g, rs in sorted(by_group.items())},
    }

    def severity(row: dict) -> tuple:
        return (row["brand"] == "wrong", row["condition_error"] or 0,
                row["type"] == "miss", not row["category_ok"], row["id"])

    worst = sorted((r for r in rows if severity(r)[:4] != (False, 0, False, False)),
                   key=severity, reverse=True)[:WORST]
    return result, worst


def score_prices(cases: list[Case], outcomes: dict[str, scanclient.ScanOutcome]
                 ) -> tuple[dict, list[dict]]:
    rows: list[dict] = []
    for case in cases:
        outcome = outcomes.get(case.id)
        if outcome is None or not outcome.ok or case.price_usd is None:
            continue
        body = outcome.body or {}
        point, basis = predicted_point(body)
        low, high = _number(body.get("est_value_low_usd")), _number(body.get("est_value_high_usd"))
        if point is None or low is None or high is None:
            continue
        rows.append({"id": case.id, "group": case.group, "true_usd": case.price_usd,
                     "price_raw": case.price_raw, "predicted_usd": round(point, 2),
                     "point": basis, "low_usd": low, "high_usd": high,
                     "ape": metrics.ape(point, case.price_usd),
                     "item_name": body.get("item_name"), "brand": body.get("brand")})

    def summary(subset: list[dict]) -> dict:
        if len(subset) < MIN_GROUP:
            return {"n": len(subset)}
        pairs = [(r["predicted_usd"], r["true_usd"]) for r in subset]
        triples = [(r["low_usd"], r["high_usd"], r["true_usd"]) for r in subset]
        widths = [r["high_usd"] - r["low_usd"] for r in subset if r["high_usd"] >= r["low_usd"]]
        relative = [(r["high_usd"] - r["low_usd"]) / r["predicted_usd"] for r in subset
                    if r["predicted_usd"] > 0 and r["high_usd"] >= r["low_usd"]]
        return {
            "n": len(subset),
            "mdape": metrics.mdape(pairs),
            "within_range": metrics.range_coverage(triples),
            "median_range_width_usd": statistics.median(widths) if widths else None,
            "median_range_width_relative": statistics.median(relative) if relative else None,
            "mean_high_low_ratio": metrics.mean_range_width(triples),
            "bias_usd": statistics.median(p - a for p, a in pairs),
            "midpoint_fallbacks": sum(1 for r in subset if r["point"] == "midpoint"),
        }

    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_group[row["group"]].append(row)
    result = {"overall": summary(rows),
              "by_category": {g: summary(rs) for g, rs in sorted(by_group.items())}}
    worst = sorted((r for r in rows if r["ape"] is not None),
                   key=lambda r: (r["ape"], r["id"]), reverse=True)[:WORST]
    return result, worst


def score_bands(cases: list[Case], outcomes: dict[str, scanclient.ScanOutcome]
                ) -> tuple[dict, list[dict]]:
    """The scan's price against a price band, compared in the band's currency.

    `in_band`: the point price lies in the band (an open top has no ceiling).
    `band_error`: the scan's band minus the true one, in `BAND_EDGES` steps;
    positive is priced high. `outside_pct`: how far past the nearer edge the
    point lies, as a percentage of that edge, 0 inside: the band's MdAPE."""
    rows: list[dict] = []
    for case in cases:
        outcome = outcomes.get(case.id)
        if (outcome is None or not outcome.ok or case.band is None
                or case.local_per_usd is None):
            continue
        body = outcome.body or {}
        point, basis = predicted_point(body)
        low, high = _number(body.get("est_value_low_usd")), _number(body.get("est_value_high_usd"))
        if point is None or low is None or high is None:
            continue
        band_low, band_high = case.band
        local = point * case.local_per_usd
        if local < band_low:
            outside = (band_low - local) / band_low * 100
        elif band_high is not None and local > band_high:
            outside = (local - band_high) / band_high * 100 if band_high else None
        else:
            outside = 0.0
        true_index = secondhand.band_index(band_low)
        predicted_index = secondhand.band_index(local)
        overlaps = (high * case.local_per_usd >= band_low
                    and (band_high is None or low * case.local_per_usd <= band_high))
        rows.append({"id": case.id, "group": case.group, "price_raw": case.price_raw,
                     "band": [band_low, band_high], "predicted_usd": round(point, 2),
                     "predicted_local": round(local, 1), "point": basis,
                     "low_usd": low, "high_usd": high, "in_band": outside == 0,
                     "true_band": secondhand.band_label(true_index),
                     "predicted_band": secondhand.band_label(predicted_index),
                     "band_error": predicted_index - true_index,
                     "outside_pct": outside, "range_overlaps": overlaps,
                     "item_name": body.get("item_name"), "brand": body.get("brand")})

    def summary(subset: list[dict]) -> dict:
        if len(subset) < MIN_GROUP:
            return {"n": len(subset)}
        n = len(subset)
        outside = [r["outside_pct"] for r in subset if r["outside_pct"] is not None]
        widths = [r["high_usd"] - r["low_usd"] for r in subset if r["high_usd"] >= r["low_usd"]]
        relative = [(r["high_usd"] - r["low_usd"]) / r["predicted_usd"] for r in subset
                    if r["predicted_usd"] > 0 and r["high_usd"] >= r["low_usd"]]
        return {
            "n": n,
            "in_band": sum(r["in_band"] for r in subset) / n,
            "band_exact": sum(r["band_error"] == 0 for r in subset) / n,
            "band_within_one": sum(abs(r["band_error"]) <= 1 for r in subset) / n,
            "median_band_error": statistics.median(r["band_error"] for r in subset),
            "median_outside_pct": statistics.median(outside) if outside else None,
            "range_overlaps_band": sum(r["range_overlaps"] for r in subset) / n,
            "median_range_width_usd": statistics.median(widths) if widths else None,
            "median_range_width_relative": statistics.median(relative) if relative else None,
            "midpoint_fallbacks": sum(1 for r in subset if r["point"] == "midpoint"),
        }

    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_group[row["group"]].append(row)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        confusion[row["true_band"]][row["predicted_band"]] += 1
    result = {"kind": "bands", "overall": summary(rows),
              "by_category": {g: summary(rs) for g, rs in sorted(by_group.items())},
              "confusion": {t: dict(c) for t, c in sorted(
                  confusion.items(), key=lambda kv: secondhand.BAND_ORDER.get(kv[0], 99))}}
    worst = sorted(rows, key=lambda r: (r["outside_pct"] or 0, abs(r["band_error"]), r["id"]),
                   reverse=True)[:WORST]
    return result, worst


# ── report ───────────────────────────────────────────────────────────────────

def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def render_report(results: dict) -> str:
    lines = [f"# /scan on {results['split']}"
             + (f" ({results['subset']})" if results.get("subset") else ""),
             "",
             f"Run {results['run_at']} against commit `{results['backend']['commit']}`. "
             f"Tag photo: {'sent' if results['config']['send_tag'] else 'not sent'}.",
             ""]
    if results.get("basis"):
        lines += [f"**Basis:** {results['basis']}", ""]
    counts = results["counts"]
    lines += ["## Counts", "", "| | n |", "| --- | --- |",
              f"| Items in split | {counts['cases']} |", f"| Sent | {counts['sent']} |",
              f"| From cache | {counts['cached']} |", f"| Scored | {counts['ok']} |"]
    for reason, n in sorted({**counts["skipped"], **counts["not_sent"],
                             **counts["errors"]}.items()):
        lines.append(f"| {reason} | {n} |")
    lines.append("")

    m = results["metrics"]
    if "overall" in m and "brand" in m["overall"]:
        lines += ["## Identification", "",
                  "| Slice | n | Brand exact | Brand fuzzy | Brand abstained | Category | "
                  "Type (n) | Condition MAE | Condition exact |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        slices = [("All", m["overall"]), ("Brand photo present", m["brand_photo_present"]),
                  ("Brand photo absent", m["brand_photo_absent"])]
        slices += [(f"Type: {g}", s) for g, s in m["by_type"].items()]
        for name, s in slices:
            if "brand" not in s:
                lines.append(f"| {name} | {s['n']} | fewer than {MIN_GROUP}: no figures "
                             "| | | | | | |")
                continue
            b, c = s["brand"] or {}, s["condition"] or {}
            lines.append(
                f"| {name} | {s['n']} | {_pct(b.get('exact_accuracy'))} | "
                f"{_pct(b.get('fuzzy_accuracy'))} | {_pct(b.get('abstention_rate'))} | "
                f"{_pct(s['category_accuracy'])} | {_pct(s['type_accuracy'])} "
                f"({s['type_scored']}) | {_num(c.get('mae'))} | {_pct(c.get('exact'))} |")
        lines.append("")
    if m.get("kind") == "bands":
        lines += _band_report(m)
    elif "by_category" in m:
        lines += ["## Price", "",
                  "| Category | n | MdAPE | In range | Median width (USD) | Width / point | "
                  "Median bias (USD) |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for name, s in [("All", m["overall"]), *m["by_category"].items()]:
            if "mdape" not in s:
                lines.append(f"| {name} | {s['n']} | fewer than {MIN_GROUP}: no figures | | | | |")
                continue
            lines.append(
                f"| {name} | {s['n']} | {_num(s['mdape'], 1)}% | {_pct(s['within_range'])} | "
                f"{_num(s['median_range_width_usd'])} | "
                f"{_num(s['median_range_width_relative'])} | {_num(s['bias_usd'])} |")
        lines.append("")

    worst = results["worst"]
    lines += [f"## Worst {len(worst)}", ""]
    if worst and "true_band" in worst[0]:
        lines += ["| Item | Type | Band | Predicted (USD) | In band currency | Scan's band | "
                  "Range (USD) | Outside by |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in worst:
            lines.append(f"| {r['id']} | {r['group']} | {r['price_raw']} | "
                         f"{r['predicted_usd']:.2f} | {r['predicted_local']:.0f} | "
                         f"{r['predicted_band']} | {r['low_usd']:.0f}–{r['high_usd']:.0f} | "
                         f"{_num(r['outside_pct'], 0)}% |")
    elif worst and "true_usd" in worst[0]:
        lines += ["| Item | Category | Expected (USD) | Predicted (USD) | Range | APE |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in worst:
            lines.append(f"| {r['id']} | {r['group']} | {r['true_usd']:.2f} | "
                         f"{r['predicted_usd']:.2f} | {r['low_usd']:.0f}–{r['high_usd']:.0f} | "
                         f"{r['ape']:.0f}% |")
    elif worst:
        lines += ["| Item | Expected brand / type / condition | Predicted brand / name / grade |",
                  "| --- | --- | --- |"]
        for r in worst:
            e, p = r["expected"], r["predicted"]
            lines.append(f"| {r['id']} | {e['brand']} / {e['type']} / {e['condition']} | "
                         f"{p['brand']} / {p['item_name']} / {p['condition_grade']} |")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def _band_report(m: dict) -> list[str]:
    lines = ["## Price against the band", "",
             "| Type | n | In band | Right band | Within one band | Median band error | "
             "Median outside band | Range overlaps band | Median width (USD) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, s in [("All", m["overall"]), *m["by_category"].items()]:
        if "in_band" not in s:
            lines.append(f"| {name} | {s['n']} | fewer than {MIN_GROUP}: no figures "
                         "| | | | | | |")
            continue
        lines.append(
            f"| {name} | {s['n']} | {_pct(s['in_band'])} | {_pct(s['band_exact'])} | "
            f"{_pct(s['band_within_one'])} | {s['median_band_error']:+g} | "
            f"{_num(s['median_outside_pct'], 1)}% | {_pct(s['range_overlaps_band'])} | "
            f"{_num(s['median_range_width_usd'])} |")
    lines.append("")
    if m.get("confusion"):
        labels = sorted({p for c in m["confusion"].values() for p in c} | set(m["confusion"]),
                        key=lambda b: secondhand.BAND_ORDER.get(b, 99))
        lines += ["Band confusion (rows: true band, columns: the scan's):", "",
                  "| | " + " | ".join(labels) + " |",
                  "| --- |" + " --- |" * len(labels)]
        for true, row in m["confusion"].items():
            lines.append(f"| {true} | " + " | ".join(str(row.get(b, 0)) for b in labels) + " |")
        lines.append("")
    return lines


# ── CLI ──────────────────────────────────────────────────────────────────────

def check_url(url: str, allow_remote: bool) -> str | None:
    """None when the URL may be used, else why not."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"EVAL_BACKEND_URL={url!r} is not an http(s) URL."
    if parsed.hostname not in LOCAL_HOSTS and not allow_remote:
        return (f"EVAL_BACKEND_URL points at {parsed.hostname}, not this machine. A remote "
                "scan bills, counts toward /trends and posts to the operator feed; pass "
                "--allow-remote if that is intended.")
    return None


def main(argv: list[str] | None = None, transport=None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m eval.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--subset", choices=("dev", "test", "all"), default="dev",
                        help="id_gold half to run (default dev)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=scanclient.DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout", type=float, default=scanclient.DEFAULT_TIMEOUT_S)
    parser.add_argument("--retries", type=int, default=scanclient.DEFAULT_RETRIES)
    parser.add_argument("--tag", choices=("auto", "always", "never"), default="auto",
                        help="send the brand photo as `tag` (auto: when a token is set)")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--cache-tag", default="",
                        help="separates cache entries, e.g. per uncommitted prompt edit")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--manifests", default=str(secondhand.MANIFEST_DIR))
    parser.add_argument("--data", help="the download (default: $EVAL_DATA_DIR)")
    parser.add_argument("--out", help="output folder (default: a timestamped one under runs/)")
    args = parser.parse_args(argv)

    from eval import identity
    identity.load_env()
    url = os.environ.get("EVAL_BACKEND_URL", "").strip()
    if not url:
        print("EVAL_BACKEND_URL is not set (e.g. http://127.0.0.1:8000).", file=out)
        return 2
    if (problem := check_url(url, args.allow_remote)) is not None:
        print(problem, file=out)
        return 2
    token = os.environ.get("EVAL_SCAN_TOKEN", "").strip() or None
    send_tag = args.tag == "always" or (args.tag == "auto" and token is not None)

    loaded = load_cases(args.split, args.subset, Path(args.manifests), args.data)
    cases = loaded.cases[: args.limit] if args.limit else loaded.cases
    loaded.cases = cases
    requests, not_sent = build_requests(loaded, send_tag, scanclient.prepare_bytes)

    client = scanclient.ScanClient(
        base_url=url, token=token, concurrency=args.concurrency, timeout_s=args.timeout,
        retries=args.retries, cache_dir=None if args.no_cache else CACHE_DIR,
        cache_tag=f"{args.cache_tag}|tag={send_tag}", transport=transport)
    done = Counter()

    def progress(outcome: scanclient.ScanOutcome) -> None:
        done["n"] += 1
        if done["n"] % 25 == 0 or done["n"] == len(requests):
            print(f"  {done['n']}/{len(requests)}", file=out)

    async def go():
        await client.read_commit()
        return await client.scan_all(requests, on_done=progress)

    print(f"{args.split}: {len(cases)} items, {len(requests)} to send to {url}", file=out)
    outcomes = asyncio.run(go()) if requests else []
    by_id = {o.item_id: o for o in outcomes}

    if args.split == "id_gold":
        scored, worst = score_identification(cases, by_id)
    elif args.split == "price_silver":
        scored, worst = score_bands(cases, by_id)
    else:
        scored, worst = score_prices(cases, by_id)
    errors = Counter(o.error for o in outcomes if o.error)
    results = {
        "split": args.split,
        "subset": args.subset if args.split == "id_gold" else None,
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backend": {"host": urlparse(url).hostname, "commit": client.server_commit},
        "config": {"limit": args.limit, "concurrency": args.concurrency,
                   "timeout_s": args.timeout, "retries": args.retries, "send_tag": send_tag,
                   "token": token is not None, "cache": not args.no_cache,
                   "cache_tag": args.cache_tag, "fx_table": loaded.fx_table},
        "basis": loaded.basis,
        "provenance": "measured",
        "counts": {"cases": len(cases), "sent": len(requests),
                   "cached": sum(1 for o in outcomes if o.cached),
                   "ok": sum(1 for o in outcomes if o.ok),
                   "skipped": dict(loaded.skipped), "not_sent": dict(not_sent),
                   "errors": dict(errors)},
        "metrics": scored,
        "worst": worst,
        "outcomes": [o.to_dict() for o in outcomes],
    }
    stamp = results["run_at"].replace(":", "").replace("-", "")
    out_dir = Path(args.out) if args.out else RUNS_DIR / f"{args.split}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    report = render_report(results)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report, file=out)
    print(f"Wrote {out_dir / 'results.json'} and report.md", file=out)
    if client.halted == "quota_unavailable":
        print("Stopped early: the backend answered quota_unavailable. Its REDIS_URL names a "
              "Redis it cannot reach; start it with REDIS_URL= (empty) for a local run, or "
              "set EVAL_SCAN_TOKEN to a Pro token.", file=out)
        return 1
    if client.halted:
        print(f"Stopped early: the backend answered {client.halted}. Check the token and "
              "the local server's FREE_SCANS_PER_DAY / rate limits.", file=out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
