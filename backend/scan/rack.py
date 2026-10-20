"""Rack scan (#89): what a model's read of one rack photo may say.

The endpoint is `main.rack`; the prompt is `prompts.RACK_PROMPT_V1`. This
module sits between them and decides what reaches the app, where each
candidate becomes a numbered pin on the photo that a person walks over to.

* **A candidate needs a box.** One with no box, a box outside the photo, or
  a box too small to point at is dropped: a pin that lands nowhere is the
  failure this feature cannot afford.
* **At most three, no duplicates.** Two boxes that mostly overlap are one
  item named twice; the second is dropped.
* **Rough, and shown as rough.** Prices go through the same category bands
  as a scan (`promptsafety.clamp_valuation`) and are then widened to at least
  `MIN_SPREAD`, so that a range judged from across the aisle cannot read as a
  valuation. The app offers a real scan of each.
* **An empty list is an answer**, served as a 200 like any other.
"""

from __future__ import annotations

import math

from scan import categories
from scan import promptsafety

MAX_CANDIDATES = 3

#: The smallest box edge kept, as a fraction of the photo. Smaller than this
#: is a few pixels on a phone: nothing a person could find on a rail by it.
MIN_BOX_EDGE = 0.03

#: Boxes overlapping by more than this (intersection over union) are taken to
#: be one item named twice.
MAX_OVERLAP = 0.6

#: A rack read's high end is at least this multiple of its low end.
MIN_SPREAD = 1.5

MAX_POSITION = 120
MAX_NAME = promptsafety.MAX_ITEM_NAME
MAX_WHY = 200


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _box(raw: object) -> dict | None:
    """`box_2d` ([ymin, xmin, ymax, xmax] on 0-1000) as a 0-1 rectangle.

    Out-of-order edges are put in order and edges past the frame are
    clipped; a box that is then empty or tiny is no box."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    edges = [_number(v) for v in raw]
    if any(e is None for e in edges):
        return None
    ymin, xmin, ymax, xmax = (min(max(e / 1000.0, 0.0), 1.0) for e in edges)  # type: ignore[operator]
    top, bottom = sorted((ymin, ymax))
    left, right = sorted((xmin, xmax))
    width, height = right - left, bottom - top
    if width < MIN_BOX_EDGE or height < MIN_BOX_EDGE:
        return None
    return {"x": round(left, 4), "y": round(top, 4),
            "width": round(width, 4), "height": round(height, 4)}


def _overlap(a: dict, b: dict) -> float:
    left = max(a["x"], b["x"])
    top = max(a["y"], b["y"])
    right = min(a["x"] + a["width"], b["x"] + b["width"])
    bottom = min(a["y"] + a["height"], b["y"] + b["height"])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / union if union > 0 else 0.0


def _range(low: object, high: object, category: str) -> tuple[float, float] | None:
    lo, hi = _number(low), _number(high)
    if lo is None and hi is None:
        return None
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    if (lo or 0) <= 0 and (hi or 0) <= 0:
        return None
    lo, hi, _kind = promptsafety.clamp_valuation(lo, hi, category)  # type: ignore[arg-type]
    hi = max(hi, lo * MIN_SPREAD)
    return float(round(lo)), float(math.ceil(hi))


def validate(data: dict) -> list[dict]:
    """The model's reply reduced to the candidates the app may pin."""
    raw = data.get("candidates")
    if not isinstance(raw, list):
        return []
    kept: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        box = _box(entry.get("box_2d"))
        if box is None or any(_overlap(box, k["box"]) > MAX_OVERLAP for k in kept):
            continue
        name = promptsafety.sanitize_text(entry.get("name"), MAX_NAME, field="rack_name")
        if not name:
            continue
        category = categories.normalise(entry.get("category"))
        prices = _range(entry.get("low_usd"), entry.get("high_usd"), category)
        if prices is None:
            continue
        brand = promptsafety.sanitize_text(entry.get("brand"), promptsafety.MAX_BRAND,
                                           field="rack_brand") or "Unknown"
        kept.append({
            "rank": len(kept) + 1,
            "box": box,
            "position": promptsafety.sanitize_text(entry.get("position"), MAX_POSITION,
                                                   field="rack_position"),
            "name": name,
            "brand": brand,
            "category": category,
            "low_usd": prices[0],
            "high_usd": prices[1],
            "why": promptsafety.sanitize_text(entry.get("why"), MAX_WHY, field="rack_why"),
        })
        if len(kept) == MAX_CANDIDATES:
            break
    return kept
