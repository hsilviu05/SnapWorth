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

    #: True when the model's non-zero points arrived out of order and this
    #: object is the repaired version.
    #:
    #: `promptsafety.clamp_valuation` has an `"order"` kind whose docstring says
    #: an out-of-order response "is a real error and the caller should lower
    #: confidence" — and it is unreachable from any real call site, because
    #: `reconcile_prices` sorts the points before the clamp ever sees them. The
    #: branch works when called directly (there is a test), so it looked
    #: covered; the evidence was simply destroyed upstream. Carried here
    #: instead of re-detected, because by the time anything downstream looks,
    #: the disorder is gone.
    order_repaired: bool = False

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

    resolved = [resolved_worst, resolved_quick, resolved_expected, resolved_best]
    ordered = sorted(resolved)
    return PricePoints(
        worst=round(ordered[0], 2),
        quick=round(ordered[1], 2),
        expected=round(ordered[2], 2),
        best=round(ordered[3], 2),
        # The one place that can still see the disorder. The prompt requires
        # `worst ≤ quick ≤ expected ≤ best`; a response that violates it is a
        # formatting failure, which is weak evidence that the response was a
        # struggle — the same premise the completeness signal rests on. Repair
        # it, keep the user's scan, and let confidence know.
        order_repaired=ordered != resolved,
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


#: The price keys the v2 and v1 prompts ask for, respectively.
V2_PRICE_FIELDS = ("worst_case_price_usd", "quick_sale_price_usd",
                   "expected_price_usd", "best_case_price_usd")
V1_PRICE_FIELDS = ("est_value_low_usd", "est_value_high_usd")


def priced_as_unsellable(data: object) -> bool:
    """True when the model deliberately answered "this has no resale value".

    The prompt ends its honesty rules with an explicit instruction: *"If this
    is not a resalable object (a person, a pet, a room, a screenshot, food),
    set `category` to "other", set all four prices to 0, and explain in
    `uncertainty_factors`."* A model that follows it is behaving perfectly.

    `/scan` could not tell that apart from a truncated response, because
    `normalise` defaults a missing price to 0 too, and both arrive as
    `prices.worst == 0`. So the documented correct answer was served as
    `502 The AI couldn't price this item. Please try again.` — a retry that
    cannot succeed, on the class of photo people try first when they are
    playing with a new app, with the model's own explanation thrown away.

    The distinction is in the raw payload and only there: a deliberate zero has
    the price *keys present* and set to zero; a truncated or malformed response
    does not have them at all. This requires the full set the prompt asks for —
    all four v2 fields, or both v1 fields — precisely so that a response cut
    off mid-object cannot be read as a considered judgement.
    """
    if not isinstance(data, dict):
        return False
    for group in (V2_PRICE_FIELDS, V1_PRICE_FIELDS):
        # The presence test is redundant today — a missing key resolves to
        # None, and `_is_explicit_zero(None)` is False — and a mutation run
        # confirmed removing it changes no behaviour. It stays because
        # *presence* is the property this function rests on, and leaving it
        # implicit in another function's None handling is how that gets lost.
        if all(name in data for name in group) and all(
                _is_explicit_zero(data[name]) for name in group):
            return True
    return False


def _is_explicit_zero(value: object) -> bool:
    """A value the model actually wrote as zero — not one coerced into zero.

    `safe_float` is deliberately not used here. It floors negatives, so a
    response whose prices are `-100/-50` came back as 0.0 and would have read
    as a considered "this has no resale value", when it is a malformed or
    attacker-influenced response that must stay a 502. The test that catches
    that predates this function by months
    (`test_negative_values_are_refused_not_invented`) and caught it again.

    Strings are accepted because models emit `"0.00"` under JSON mode; `bool`
    is excluded because `False == 0` is true in Python and a boolean in a price
    field is junk, not a judgement.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return float(value.strip()) == 0.0
        except ValueError:
            return False
    return False


def apply_price_bounds(valuation: Valuation) -> tuple[float, float, bool]:
    """Clamp a valuation into its category's plausible band, in place.

    Returns `(low, high, was_clamped)`: the bounds to serve, and whether the
    clamp represented a real model error rather than a cheap item touching its
    category floor.

    This is the single source of the rule because it has two callers that must
    not disagree — `/scan`, and the evaluation harness that exists to measure
    what `/scan` does. The harness had its own copy and had drifted twice:

    * It never rebuilt the interior points, so it scored `prices.expected`
      straight from the model — a headline number that can sit outside the
      `[low, high]` the same run would have served, and that `/scan` would
      never return. The whole claim of the eval is that it measures the
      shipping pipeline; on the one number the product leads with, it did not.

    * It passed `worst or 1.0` and `best or 5.0`, substituting defaults for a
      response that carried no prices. Production passes the real zeroes. So
      the harness scored a case that cannot occur and stayed silent about one
      that can.

    A benchmark that measures a pipeline no user hits is worse than no
    benchmark, because it is trusted.
    """
    low, high, clamp_kind = promptsafety.clamp_valuation(
        valuation.prices.worst, valuation.prices.best, valuation.category)
    # Captured before the rebuild below, which loses it.
    order_repaired = valuation.prices.order_repaired

    if (low, high) != (round(valuation.prices.worst, 2),
                       round(valuation.prices.best, 2)):
        # Rebuild whenever the span moved at all, so the v1 low/high pair and
        # the v2 ladder cannot disagree — the response builds one from `low`
        # and the other from `prices`.
        #
        # The interior points are pinned into the new span, not discarded.
        # Passing quick=0, expected=0 made `reconcile_prices` interpolate them,
        # so `expected_price_usd` came back as the exact midpoint of the
        # clamped range — the one thing prompts.py forbids ("must be your best
        # point estimate, not the midpoint of a range you invented"). A $0.25
        # floor adjustment was silently rewriting the headline number a Pro
        # subscriber is paying to see. Zero still means absent, because
        # `reconcile_prices` reads it that way and interpolating a point the
        # model never sent is better than pinning it to the floor.
        def _pin(value: float) -> float:
            return min(max(value, low), high) if value > 0 else 0.0

        valuation.prices = reconcile_prices(
            worst=low, quick=_pin(valuation.prices.quick),
            expected=_pin(valuation.prices.expected), best=high)

    # Only a real model error lowers confidence. A cheap item touching its
    # category floor, or a point estimate being opened into a range, is not one.
    #
    # `order_repaired` is read from the pre-rebuild points: `reconcile_prices`
    # above is called with values this function already sorted, so it would
    # report False and wipe the signal it exists to carry.
    was_clamped = clamp_kind in {"ceiling", "order"} or order_repaired
    valuation.was_clamped = was_clamped
    return low, high, was_clamped


#: `EXPECTED_OPTIONAL_FIELDS` are the names the *model* uses; these are the
#: attributes they land on. One rename (`authenticity_assessment` →
#: `authenticity`) is the only place the two vocabularies differ, which is
#: exactly why this is a table rather than a `getattr(val, name)`.
_FIELD_ATTRIBUTES = {
    "authenticity_assessment": "authenticity",
}


def count_present_fields(valuation: "Valuation") -> int:
    """How many of the expected optional v2 fields *survived* normalisation.

    Feeds the `completeness` confidence signal, whose premise is that a
    half-empty response means the model struggled and the estimate deserves
    less confidence.

    This counted the raw payload, which broke that premise: `normalise` throws
    away anything it cannot use — a `condition_grade` outside the four grades,
    a `demand` the model phrased as "extremely high", a list whose every entry
    sanitises to nothing — and none of that was visible here. A response in
    which *every one* of the fifteen fields is present but every one is
    discarded scored 11/15, so a model that answered entirely off-vocabulary
    was rewarded with 73% completeness while the user saw a `Valuation` with
    nothing in it at all.

    Counting the normalised object is also the only version that cannot drift:
    the thing scored is the thing served.
    """
    if not isinstance(valuation, Valuation):
        return 0
    present = 0
    for name in EXPECTED_OPTIONAL_FIELDS:
        value = getattr(valuation, _FIELD_ATTRIBUTES.get(name, name), None)
        # `normalise` already resolves absence to None or [], and every
        # placeholder spelling ("null", "n/a", …) is stripped by `_text` and
        # `_enum` before it gets here — so emptiness is the whole test.
        if value is None or value == "" or value == []:
            continue
        present += 1
    return present
