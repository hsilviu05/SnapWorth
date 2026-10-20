"""A second opinion on "same product?" for the matcher's least certain comps (#354).

`matching.score` vetoes contradictions and then weighs soft signals. A comp
that clears `MIN_MATCH_SCORE` by a little has passed on wording alone, and a
wrong comp is worse than a missing one. Whether such a sold listing is the
same product as the scanned item is a yes/no question over two short texts,
which is what TypeSafe's Jev answers.

Shadow only: `ShadowRunner` asks it about the accepted comps in the uncertain
band and logs both answers beside each other. Nothing here changes which comps
are kept. Letting Jev veto in the live path waits until adjudicated
disagreements show it lowers `eval.metrics.matching_quality`'s false-match
rate.

Off unless COMPS_JEV_SHADOW is on and TYPESAFE_API_KEY is set. Comps itself
is off in production (#41, #231), so this has nothing to judge until it is
on. What is sent: the brand, model and variant the scan identified, and the
sold listings' titles. Never the photo or anything about the user.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from scan import promptsafety
from scan import typesafe
from comps.matching import MIN_MATCH_SCORE
from comps.models import Comp, ItemIdentity

FLAG_ENV = "COMPS_JEV_SHADOW"
QUESTION = "same_product"

#: Accepted comps scoring below this are the ones worth a second opinion.
#: Above it the model designator matched exactly or the wording agrees almost
#: entirely, and there is little for Jev to add.
UNCERTAIN_BELOW = 0.75
#: At or above this, Jev says the listing is the same product.
SAME_THRESHOLD = 0.5
#: Per lookup, most uncertain first. Bounds the cost of one scan.
MAX_JUDGED = 5
#: The whole review is abandoned past this. It runs in the background, so
#: this bounds a stuck task, not a user's wait.
TIMEOUT_S = 5.0

_INSTRUCTIONS = (
    "Is the sold listing for the same product as the identified item? Extra "
    "detail in the listing, such as a colourway, size, year or condition, does "
    "not make it a different product. A different model, model number, "
    "generation or brand does, and so does an accessory, part or box sold "
    "on its own."
)
_CRITERIA = {
    "true": "The listing sells the same product, described differently or in more detail.",
    "false": "The listing sells a different product, a different model or version of "
             "it, or only a part, accessory or box for it.",
}


def enabled() -> bool:
    flag = os.environ.get(FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    return flag and typesafe.enabled()


def uncertain(comps: tuple[Comp, ...] | list[Comp]) -> list[Comp]:
    """The accepted comps in the uncertain band, least certain first."""
    band = [c for c in comps if MIN_MATCH_SCORE <= c.match_score < UNCERTAIN_BELOW]
    return sorted(band, key=lambda c: c.match_score)[:MAX_JUDGED]


def _fenced(value: object, limit: int) -> str:
    return promptsafety.fence(promptsafety.sanitize_text(value, limit))


def build_request(identity: ItemIdentity, title: str,
                  model: str = typesafe.MODEL) -> dict[str, Any]:
    """One `/v1/systemone` body. The identity came from a model reading a
    user's photo and the title from a seller, so both are fenced."""
    item = {key: _fenced(value, limit) for key, value, limit in (
        ("category", identity.category, promptsafety.MAX_CATEGORY),
        ("brand", identity.brand, promptsafety.MAX_BRAND),
        ("model", identity.model, promptsafety.MAX_ITEM_NAME),
        ("variant", identity.variant, promptsafety.MAX_ITEM_NAME),
    ) if value}
    return {
        "model": model,
        "state": {"identified_item": item, "sold_listing": {"title": _fenced(title, 200)}},
        "questions": {QUESTION: {"type": "noul", "instructions": _INSTRUCTIONS,
                                 "criteria": _CRITERIA}},
    }


async def judge(identity: ItemIdentity, comp: Comp) -> float | None:
    """Jev's probability that `comp` is the same product; None when unjudged."""
    body = await typesafe.ask(build_request(identity, comp.title), TIMEOUT_S,
                              "comps second opinion")
    answers = body.get("answers") if isinstance(body, dict) else None
    return typesafe.noul(answers, QUESTION)


def summarise(judged: list[tuple[Comp, float | None]]) -> dict[str, Any]:
    """Log fields for one lookup. Every comp here was accepted by the matcher,
    so agreement is Jev saying "same". Each disagreement keeps its title and
    both scores, which is what an adjudicator needs to say who was right."""
    answered = [(c, p) for c, p in judged if p is not None]
    disagree = [(c, p) for c, p in answered if p < SAME_THRESHOLD]
    fields: dict[str, Any] = {
        "jev_asked": len(judged),
        "jev_judged": len(answered),
        "jev_disagreed": len(disagree),
    }
    if answered:
        fields["jev_agreement"] = round(1 - len(disagree) / len(answered), 3)
    if disagree:
        fields["jev_disagreements"] = [
            {"title": c.title, "match_score": round(c.match_score, 3), "jev": round(p, 3)}
            for c, p in disagree]
    return fields


async def review(identity: ItemIdentity, comps: tuple[Comp, ...] | list[Comp]) -> dict[str, Any]:
    """Ask about the uncertain comps; {} when there are none. Never raises."""
    candidates = uncertain(comps)
    if not candidates:
        return {}
    verdicts = await asyncio.gather(*(judge(identity, c) for c in candidates),
                                    return_exceptions=True)
    judged = [(c, v if isinstance(v, float) else None)
              for c, v in zip(candidates, verdicts)]
    return summarise(judged)
