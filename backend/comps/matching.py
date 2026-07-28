"""Comparable matching: does this listing describe the same object?

This is the highest-stakes module in the engine. A wrong comp is worse than no
comp, because it carries the authority of evidence — a user who sees "based on
38 sold listings" and acts on it has no way to know that six of those were a
different model.

The central design decision
---------------------------
**Similarity is not enough; some mismatches must be vetoes.**

    "Nike Air Max 97"  vs  "Nike Air Max 95"

Token overlap is 3/4. Cosine similarity on sentence embeddings is ~0.97. Every
soft-similarity method rates these as near-identical, and every one of them is
wrong: different shoe, materially different market price.

Meanwhile:

    "Nike Air Max 97"  vs  "Nike Air Max 97 Silver Bullet 2022"

is an excellent comp, despite carrying three extra tokens the query never asked
for.

So scoring runs in two stages:

1. **Veto stage** — hard disqualifiers. A contradicted model designator, a brand
   conflict, or a category conflict returns 0.0 immediately. No weighted signal
   can rescue a comp that is definitively a different product.

2. **Score stage** — weighted soft signals for everything that is genuinely a
   matter of degree: variant wording, size, condition, recency, year proximity.

Stage 1 is what a pure embedding approach cannot express, and it is why RAG is
the wrong tool for this problem (see docs/COMPS-ARCHITECTURE.md §RAG).

Asymmetry
---------
Extra tokens in the *candidate* are cheap; missing tokens from the *query* are
expensive. `containment` rather than `jaccard` encodes that, which is what lets
the Silver Bullet listing score highly while a bare "Air Max" scores lower.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from comps import normalize
from comps.models import Comp, Condition, ItemIdentity

log = logging.getLogger("snapworth.comps.matching")

# Below this, a comp is discarded entirely. Deliberately high: too few good
# comps is a better outcome than many mediocre ones, because the aggregate of
# mediocre comps looks exactly as authoritative as the aggregate of good ones.
MIN_MATCH_SCORE = 0.55

# Weights for the soft-signal stage. Sum to 1.0.
WEIGHTS = {
    "model": 0.34,       # the primary discriminator once vetoes have passed
    "variant": 0.20,     # colourway / edition wording
    "condition": 0.16,   # a deadstock comp misprices a worn item
    "size": 0.12,        # material in apparel and footwear
    "recency": 0.10,     # a sale last week beats one from ten weeks ago
    "year": 0.08,        # release-year proximity
}

# Freshness half-life. A comp loses half its recency score every 45 days.
RECENCY_HALF_LIFE_DAYS = 45.0

# Release years this far apart are different products in practice.
YEAR_TOLERANCE = 2


@dataclass(frozen=True)
class MatchResult:
    score: float
    reasons: tuple[str, ...]
    vetoed: bool = False
    veto_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return not self.vetoed and self.score >= MIN_MATCH_SCORE


def _designator_veto(
    query: normalize.TokenSet, candidate: normalize.TokenSet
) -> str | None:
    """Veto when the candidate contradicts a model designator.

    The rule is *contradiction*, not absence. If the query specifies `97` and the
    candidate carries `95`, that is a different shoe — veto. If the candidate
    carries no designator at all, it is merely vague, which the soft score can
    handle.

    Vetoing on absence would discard "Nike Air Max Silver Bullet" (a legitimate,
    if sloppy, listing for the same shoe), so absence is deliberately survivable.
    """
    if not query.designators:
        return None
    shared = query.designators & candidate.designators
    if shared:
        return None
    if not candidate.designators:
        return None                     # vague, not contradictory
    return (
        f"model designator conflict: expected one of "
        f"{sorted(query.designators)}, listing has {sorted(candidate.designators)}"
    )


def _brand_veto(identity: ItemIdentity, candidate_tokens: normalize.TokenSet,
                candidate_title: str) -> str | None:
    """Veto when a known brand is absent from the candidate.

    Only applies when we actually know the brand. An unknown-brand identity is
    already too thin to be searchable (`ItemIdentity.is_searchable`), so this
    never fires on the degraded path.
    """
    brand = (identity.brand or "").strip()
    if not brand or brand.lower() == "unknown":
        return None
    brand_tokens = normalize.token_set(brand).all_tokens
    if not brand_tokens:
        return None
    # Multi-word brands ("The North Face") need only their distinctive tokens.
    present = brand_tokens & candidate_tokens.all_tokens
    if present:
        return None
    # Fall back to a substring check for brands that normalise oddly.
    if normalize.normalise_text(brand) in normalize.normalise_text(candidate_title):
        return None
    return f"brand {brand!r} absent from listing"


def _year_component(identity: ItemIdentity, candidate: normalize.TokenSet) -> tuple[float, str]:
    if not identity.year:
        return 1.0, ""                  # nothing asked for; no penalty
    if not candidate.years:
        return 0.6, "listing gives no year"
    closest = min(candidate.years, key=lambda y: abs(y - identity.year))
    delta = abs(closest - identity.year)
    if delta == 0:
        return 1.0, ""
    if delta <= YEAR_TOLERANCE:
        return 1.0 - (delta / (YEAR_TOLERANCE + 1)), f"year differs by {delta}"
    return 0.0, f"year differs by {delta}"


def _condition_component(identity: ItemIdentity, comp: Comp) -> tuple[float, str]:
    if comp.condition is None:
        return 0.7, "listing condition unknown"
    distance = identity.condition.distance(comp.condition)
    if distance == 0:
        return 1.0, ""
    if distance == 1:
        return 0.7, f"condition differs ({comp.condition.value} vs {identity.condition.value})"
    return max(0.0, 1.0 - distance * 0.4), (
        f"condition differs sharply ({comp.condition.value} vs {identity.condition.value})"
    )


def _size_component(identity: ItemIdentity, candidate: normalize.TokenSet) -> tuple[float, str]:
    if not identity.size:
        return 1.0, ""
    size_tokens = normalize.token_set(identity.size, drop_stopwords=False).all_tokens
    if not size_tokens:
        return 1.0, ""
    if size_tokens & candidate.all_tokens:
        return 1.0, ""
    return 0.5, "size not confirmed in listing"


def _recency_component(comp: Comp, now: datetime) -> tuple[float, str]:
    age = comp.age_days(now)
    score = 0.5 ** (age / RECENCY_HALF_LIFE_DAYS)
    if age > 120:
        return score, f"sale is {int(age)} days old"
    return score, ""


def score(identity: ItemIdentity, comp: Comp, *, now: datetime | None = None) -> MatchResult:
    """Score one comp against the identity. 0.0 means unusable."""
    now = now or datetime.now(timezone.utc)
    candidate = normalize.token_set(comp.title)

    # ── Stage 1: vetoes ─────────────────────────────────────────────────────
    identity_text = " ".join(
        p for p in (identity.model, identity.variant, identity.edition) if p)
    query_tokens = normalize.token_set(identity_text)

    for veto in (
        _brand_veto(identity, candidate, comp.title),
        _designator_veto(query_tokens, candidate),
    ):
        if veto:
            return MatchResult(0.0, (veto,), vetoed=True, veto_reason=veto)

    # ── Stage 2: weighted soft signals ──────────────────────────────────────
    reasons: list[str] = []

    # Model: containment, so extra candidate tokens are not penalised.
    model_tokens = normalize.token_set(identity.model or "").all_tokens
    if model_tokens:
        model_score = normalize.containment(model_tokens, candidate.all_tokens)
        if model_score < 1.0:
            missing = sorted(model_tokens - candidate.all_tokens)
            reasons.append(f"listing missing {missing}")
    else:
        model_score = 0.7           # no model to match on; neither good nor bad

    # An exactly-shared designator is strong positive evidence and should lift
    # the model component even when surrounding wording differs.
    if query_tokens.designators and (query_tokens.designators & candidate.designators):
        model_score = max(model_score, 0.9)
        reasons.append("model designator matches exactly")

    variant_tokens = normalize.token_set(
        " ".join(p for p in (identity.variant, identity.edition, identity.colour) if p)
    ).all_tokens
    variant_score = (
        normalize.containment(variant_tokens, candidate.all_tokens)
        if variant_tokens else 1.0
    )

    condition_score, condition_note = _condition_component(identity, comp)
    size_score, size_note = _size_component(identity, candidate)
    recency_score, recency_note = _recency_component(comp, now)
    year_score, year_note = _year_component(identity, candidate)

    for note in (condition_note, size_note, recency_note, year_note):
        if note:
            reasons.append(note)

    total = (
        WEIGHTS["model"] * model_score
        + WEIGHTS["variant"] * variant_score
        + WEIGHTS["condition"] * condition_score
        + WEIGHTS["size"] * size_score
        + WEIGHTS["recency"] * recency_score
        + WEIGHTS["year"] * year_score
    )

    # A year that is definitively wrong is close to a veto in practice, but is
    # kept soft because listings frequently omit or misstate it.
    if year_score == 0.0 and identity.year:
        total *= 0.6

    return MatchResult(round(min(1.0, max(0.0, total)), 4), tuple(reasons[:5]))


def rank(
    identity: ItemIdentity,
    comps: list[Comp],
    *,
    now: datetime | None = None,
    min_score: float = MIN_MATCH_SCORE,
) -> list[Comp]:
    """Score, filter and sort comps best-first.

    Returns only comps at or above `min_score`, each annotated with its score
    and the reasons behind it so a valuation can be explained rather than
    asserted.
    """
    now = now or datetime.now(timezone.utc)
    scored: list[Comp] = []
    vetoed = 0
    for comp in comps:
        result = score(identity, comp, now=now)
        if result.vetoed:
            vetoed += 1
            continue
        if result.score < min_score:
            continue
        scored.append(comp.with_score(result.score, result.reasons))

    scored.sort(key=lambda c: (-c.match_score, -c.sold_at.timestamp()))
    if vetoed:
        log.debug("comps vetoed as different products", extra={"count": vetoed})
    return scored
