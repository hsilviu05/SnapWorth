"""Normalises raw model output into a coherent, safe valuation.

Separated from `main.py` because this is where the actual product logic lives:
the endpoint handles auth, quota and transport, while everything about *what a
valuation is* belongs here and is unit-testable without a request.

Three jobs
----------
1. **Coerce.** v2 returns ~20 fields of varying reliability. Every one is
   sanitised and range-checked before it reaches a client. Model output is
   untrusted input — text photographed on an item reaches the prompt (see
   `promptsafety`), so its output can carry injected content.

2. **Repair price coherence.** The prompt requires
   `worst ≤ quick ≤ expected ≤ best`, but a language model is not a constraint
   solver and will occasionally violate it. Rather than rejecting the response —
   which costs the user their scan for a fixable problem — the ordering is
   restored deterministically.

3. **Preserve the v1 contract.** Existing installs decode a fixed set of
   non-optional fields. Every v2 addition is additive and defaulted, and the
   legacy fields are always populated, so an old client sees exactly what it saw
   before.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import promptsafety
from confidence import ConfidenceResult
from imagequality import ImageQuality

log = logging.getLogger("snapworth.valuation")

MAX_LIST_ITEMS = 5
MAX_LIST_ENTRY = 180

_CONDITION_GRADES = {"new", "likeNew", "good", "used"}
_DEMAND = {"high", "medium", "low"}
_SUPPLY = {"scarce", "moderate", "abundant"}
_AUTHENTICITY = {"no_concerns", "minor_concerns", "cannot_verify", "likely_replica"}
_CERTAINTY = {"certain", "probable", "uncertain"}

# Optional fields v2 is expected to produce. Used to score response completeness
# as a confidence signal — a half-empty response means the model struggled.
EXPECTED_OPTIONAL_FIELDS = (
    "model", "variant", "size", "material", "era", "condition_grade",
    "authenticity_assessment", "demand", "supply", "identification_certainty",
    "visual_evidence", "assumptions", "uncertainty_factors", "improve_estimate",
    "value_drivers",
)


@dataclass
class PricePoints:
    """The four prices, guaranteed ordered and non-negative."""
    worst: float = 0.0
    quick: float = 0.0
    expected: float = 0.0
    best: float = 0.0

    @property
    def coherent(self) -> bool:
        return self.worst <= self.quick <= self.expected <= self.best


@dataclass
class Valuation:
    """A fully normalised valuation, ready to serialise."""
    item_name: str = "Unknown Item"
    brand: str = "Unknown"
    category: str = "other"
    condition_notes: str = "Condition unknown"
    condition_grade: str | None = None
    model: str | None = None
    variant: str | None = None
    size: str | None = None
    material: str | None = None
    era: str | None = None
    authenticity: str | None = None
    authenticity_reasoning: str | None = None
    demand: str | None = None
    supply: str | None = None
    identification_certainty: str | None = None
    prices: PricePoints = field(default_factory=PricePoints)
    visual_evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    uncertainty_factors: list[str] = field(default_factory=list)
    improve_estimate: list[str] = field(default_factory=list)
    value_drivers: list[str] = field(default_factory=list)
    listing_title: str = ""
    listing_description: str = ""
    was_clamped: bool = False
    confidence: ConfidenceResult | None = None


def safe_float(value: object) -> float:
    """Non-negative finite float, or 0.0. Shared with `main._safe_float`."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return max(0.0, result)


def _enum(value: object, allowed: set[str]) -> str | None:
    """Return `value` if it is one of `allowed`, else None.

    Case-insensitive on input but returns the canonical spelling, so a model
    answering "Good" still matches the `likeNew`-style camelCase grades.
    """
    if not isinstance(value, str):
        return None
    needle = value.strip().lower()
    for candidate in allowed:
        if candidate.lower() == needle:
            return candidate
    return None


def _text(value: object, limit: int, field_name: str) -> str | None:
    """Sanitise an optional free-text field; None when empty or absent."""
    if value is None:
        return None
    cleaned = promptsafety.sanitize_text(value, limit, field_name)
    if not cleaned:
        return None
    # Models emit these as literal strings surprisingly often under JSON mode.
    if cleaned.strip().lower() in {"null", "none", "n/a", "unknown", "-"}:
        return None
    return cleaned


def _string_list(value: object, field_name: str) -> list[str]:
    """Sanitise a list-of-strings field, bounded in both length and entry size."""
    if not isinstance(value, list):
        # Some responses collapse a single-entry list to a bare string.
        single = _text(value, MAX_LIST_ENTRY, field_name)
        return [single] if single else []
    out: list[str] = []
    for item in value[:MAX_LIST_ITEMS]:
        cleaned = _text(item, MAX_LIST_ENTRY, field_name)
        if cleaned:
            out.append(cleaned)
    return out


def reconcile_prices(
    *, worst: float, quick: float, expected: float, best: float,
    legacy_low: float = 0.0, legacy_high: float = 0.0,
) -> PricePoints:
    """Produce a coherent, ordered set of four prices from whatever came back.

    The model is instructed to satisfy `worst ≤ quick ≤ expected ≤ best` but is
    not a constraint solver, and an out-of-order set is a formatting failure
    rather than a reason to lose the user's scan. Repair is deterministic:

    * Fall back to the legacy low/high pair when the v2 fields are absent, so a
      v1 prompt or a partial v2 response still yields four usable points.
    * Sort whatever non-zero values exist and reassign them in order — this
      preserves the model's actual magnitudes while enforcing the invariant.
    * Derive any missing point from its neighbours rather than inventing one.
    """
    worst, quick, expected, best = (safe_float(v) for v in (worst, quick, expected, best))
    legacy_low, legacy_high = safe_float(legacy_low), safe_float(legacy_high)

    # v1 prompt, or a v2 response that only filled the compatibility fields.
    if not any((worst, quick, expected, best)):
        if legacy_low or legacy_high:
            low, high = min(legacy_low, legacy_high), max(legacy_low, legacy_high)
            if low == 0:
                low = high
            return PricePoints(
                worst=low, quick=low + (high - low) * 0.25,
                expected=low + (high - low) * 0.5, best=high,
            )
        return PricePoints()

    values = sorted(v for v in (worst, quick, expected, best) if v > 0)
    if not values:
        return PricePoints()

    lowest, highest = values[0], values[-1]

    # Reassign in rank order. Any point the model omitted is interpolated from
    # the observed span rather than fabricated.
    resolved_worst = worst if worst > 0 else lowest
    resolved_best = best if best > 0 else highest
    resolved_expected = expected if expected > 0 else (lowest + highest) / 2
    resolved_quick = quick if quick > 0 else resolved_worst + (resolved_expected - resolved_worst) * 0.5

    ordered = sorted([resolved_worst, resolved_quick, resolved_expected, resolved_best])
    return PricePoints(
        worst=round(ordered[0], 2),
        quick=round(ordered[1], 2),
        expected=round(ordered[2], 2),
        best=round(ordered[3], 2),
    )


def normalise(data: dict, *, image_quality: ImageQuality | None = None) -> Valuation:
    """Coerce raw model JSON into a `Valuation`. Never raises on bad input."""
    if not isinstance(data, dict):
        data = {}

    prices = reconcile_prices(
        worst=data.get("worst_case_price_usd", 0),
        quick=data.get("quick_sale_price_usd", 0),
        expected=data.get("expected_price_usd", 0),
        best=data.get("best_case_price_usd", 0),
        legacy_low=data.get("est_value_low_usd", 0),
        legacy_high=data.get("est_value_high_usd", 0),
    )

    return Valuation(
        item_name=promptsafety.sanitize_text(
            data.get("item_name", "Unknown Item"),
            promptsafety.MAX_ITEM_NAME, "item_name") or "Unknown Item",
        brand=promptsafety.sanitize_text(
            data.get("brand", "Unknown"), promptsafety.MAX_BRAND, "brand") or "Unknown",
        category=promptsafety.sanitize_text(
            data.get("category", "other"), promptsafety.MAX_CATEGORY, "category") or "other",
        condition_notes=promptsafety.sanitize_text(
            data.get("condition_notes", "Condition unknown"),
            promptsafety.MAX_NOTES, "condition_notes") or "Condition unknown",
        condition_grade=_enum(data.get("condition_grade"), _CONDITION_GRADES),
        model=_text(data.get("model"), promptsafety.MAX_ITEM_NAME, "model"),
        variant=_text(data.get("variant"), promptsafety.MAX_BRAND, "variant"),
        size=_text(data.get("size"), 40, "size"),
        material=_text(data.get("material"), promptsafety.MAX_BRAND, "material"),
        era=_text(data.get("era"), 40, "era"),
        authenticity=_enum(data.get("authenticity_assessment"), _AUTHENTICITY),
        authenticity_reasoning=_text(
            data.get("authenticity_reasoning"), promptsafety.MAX_NOTES, "authenticity_reasoning"),
        demand=_enum(data.get("demand"), _DEMAND),
        supply=_enum(data.get("supply"), _SUPPLY),
        identification_certainty=_enum(data.get("identification_certainty"), _CERTAINTY),
        prices=prices,
        visual_evidence=_string_list(data.get("visual_evidence"), "visual_evidence"),
        assumptions=_string_list(data.get("assumptions"), "assumptions"),
        uncertainty_factors=_string_list(data.get("uncertainty_factors"), "uncertainty_factors"),
        improve_estimate=_string_list(data.get("improve_estimate"), "improve_estimate"),
        value_drivers=_string_list(data.get("value_drivers"), "value_drivers"),
        listing_title=promptsafety.sanitize_text(
            data.get("listing_title", ""), promptsafety.MAX_ITEM_NAME, "listing_title"),
        listing_description=promptsafety.sanitize_text(
            data.get("listing_description", ""), promptsafety.MAX_NOTES, "listing_description"),
    )


def count_present_fields(data: dict) -> int:
    """How many of the expected optional v2 fields carry a usable value.

    Feeds the `completeness` confidence signal.
    """
    if not isinstance(data, dict):
        return 0
    present = 0
    for name in EXPECTED_OPTIONAL_FIELDS:
        value = data.get(name)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str) and value.strip().lower() in {"null", "none", "n/a", "-"}:
            continue
        present += 1
    return present
