"""Benchmark dataset format and loading.

Format
------
JSONL, one item per line. JSONL rather than a single JSON array because the set
grows by appending, diffs stay readable in review, and a malformed line can be
skipped without losing the file.

Each record pairs a photo with a **verified sale price** — not an asking price,
not an appraisal, not someone's opinion. The distinction is the whole point of
the dataset: SnapWorth's claim is about what an item sells for, so the ground
truth has to be what an item sold for.

    {
      "id": "clo-0001",
      "image_path": "images/clo-0001.jpg",
      "actual_sale_price_usd": 58.00,
      "sold_date": "2026-05-14",
      "marketplace": "ebay",
      "category": "clothing",
      "brand": "Patagonia",
      "model": "Better Sweater 1/4-Zip",
      "size": "M",
      "condition": "good",
      "notes": "sold after 6 days, 2 watchers",
      "source": "personal_sale"
    }

Required: `id`, `image_path`, `actual_sale_price_usd`, `category`.
Everything else is optional but improves what the harness can measure — `brand`
in particular enables the brand-mismatch hallucination check.

Recommended composition (≥500 items)
------------------------------------
Category mix should mirror real scan traffic, not be uniform: an evenly-spread
benchmark over-weights categories users rarely scan and will happily report
"improvement" that no user experiences.

| Category      | Items | Why this weight                                  |
|---------------|-------|--------------------------------------------------|
| clothing      | 150   | Largest share of real thrift scans                |
| shoes         |  75   | High value density, strong brand signal           |
| electronics   |  60   | High value, model-number dependent                |
| accessories   |  50   | Bags/watches — widest price variance              |
| home          |  40   | Pyrex, cast iron, small appliances                |
| collectibles  |  40   | Worst-case for a model-only estimate; include it  |
| books/media   |  30   | Mostly low value, tests the floor                 |
| sports        |  25   | Seasonal demand swings                            |
| toys          |  20   | Nostalgia-driven pricing                          |
| furniture     |  10   | Rarely photographed whole; known weak spot        |

Within each category, deliberately include:

* **~20% adversarial**: blurry, poorly lit, partially occluded, multiple items
  in frame, tag not visible. These are what real in-store photos look like, and
  a benchmark of clean studio shots measures a product nobody uses.
* **~10% negative controls**: not resalable at all (a wall, a pet, food). The
  model must not confidently price these.
* **~15% near-duplicates**: the same item photographed twice from different
  angles, to measure identification stability.
* **A realistic price distribution**, i.e. mostly $5-$60 with a genuine long
  tail. Over-sampling expensive items flatters MAPE badly.

Provenance
----------
`source` records where the ground truth came from, because their reliability
differs and results should be interpretable by tier:

* `personal_sale` — the author's own completed sale. Highest trust.
* `ebay_sold` — a captured completed listing, with the listing ID in `notes`.
* `partner_reseller` — supplied by a partner from their sales records.
* `synthetic` — for pipeline tests only. **Never** counted in reported accuracy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("snapworth.eval.dataset")

REQUIRED_FIELDS = ("id", "image_path", "actual_sale_price_usd", "category")

VALID_SOURCES = {"personal_sale", "ebay_sold", "partner_reseller", "synthetic"}

# Target composition, used by `coverage_report` to show what the set is missing.
TARGET_COMPOSITION = {
    "clothing": 150, "shoes": 75, "electronics": 60, "accessories": 50,
    "home": 40, "collectibles": 40, "books": 30, "sports": 25,
    "toys": 20, "furniture": 10,
}
RECOMMENDED_MINIMUM = 500


@dataclass
class BenchmarkItem:
    id: str
    image_path: str
    actual_sale_price_usd: float
    category: str
    brand: str | None = None
    model: str | None = None
    size: str | None = None
    condition: str | None = None
    marketplace: str | None = None
    sold_date: str | None = None
    notes: str | None = None
    source: str = "personal_sale"
    # Marks a record as a negative control: the model must not confidently price it.
    not_resalable: bool = False
    tags: list[str] = field(default_factory=list)

    @property
    def is_scoreable(self) -> bool:
        """Synthetic records exercise the pipeline but must never be reported
        as accuracy — doing so would be measuring our own fixtures."""
        return self.source != "synthetic" and not self.not_resalable


def parse_line(raw: str, line_no: int) -> BenchmarkItem | None:
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("dataset line %d is not valid JSON: %s", line_no, exc)
        return None

    missing = [f for f in REQUIRED_FIELDS if record.get(f) in (None, "")]
    if missing:
        log.warning("dataset line %d missing required fields: %s", line_no, missing)
        return None

    try:
        price = float(record["actual_sale_price_usd"])
    except (TypeError, ValueError):
        log.warning("dataset line %d has a non-numeric price", line_no)
        return None
    if price < 0:
        log.warning("dataset line %d has a negative price", line_no)
        return None

    source = record.get("source", "personal_sale")
    if source not in VALID_SOURCES:
        log.warning("dataset line %d has unknown source %r", line_no, source)
        source = "personal_sale"

    known = {f.name for f in BenchmarkItem.__dataclass_fields__.values()}
    return BenchmarkItem(
        **{k: v for k, v in record.items() if k in known and k != "actual_sale_price_usd"},
        actual_sale_price_usd=price,
    )


def load(path: str | Path) -> list[BenchmarkItem]:
    """Load a JSONL benchmark, skipping malformed lines rather than failing."""
    items: list[BenchmarkItem] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            item = parse_line(raw, line_no)
            if item is not None:
                items.append(item)

    ids = [i.id for i in items]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        # Duplicate ids silently double-weight those items in every metric.
        log.warning("dataset contains duplicate ids: %s", sorted(duplicates))
    return items


def coverage_report(items: list[BenchmarkItem]) -> dict:
    """Compare the loaded set against the recommended composition.

    Reports what is missing so the dataset can be grown deliberately rather than
    by whatever happened to be easy to collect — which is how benchmarks end up
    unrepresentative.
    """
    by_category: dict[str, int] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1

    gaps = {
        cat: target - by_category.get(cat, 0)
        for cat, target in TARGET_COMPOSITION.items()
        if by_category.get(cat, 0) < target
    }
    scoreable = [i for i in items if i.is_scoreable]
    prices = sorted(i.actual_sale_price_usd for i in scoreable)

    return {
        "total": len(items),
        "scoreable": len(scoreable),
        "meets_minimum": len(scoreable) >= RECOMMENDED_MINIMUM,
        "by_category": dict(sorted(by_category.items())),
        "gaps": dict(sorted(gaps.items(), key=lambda kv: -kv[1])),
        "negative_controls": sum(1 for i in items if i.not_resalable),
        "price_median": prices[len(prices) // 2] if prices else None,
        "price_min": prices[0] if prices else None,
        "price_max": prices[-1] if prices else None,
    }
