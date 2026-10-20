"""The item categories: one table, and the only place the list is written.

The eleven names used to be restated in five places on this side alone — both
prompts, the price bands in `promptsafety`, the familiarity weights in
`confidence`, and the emoji map in `notify` (which doubled as the only
normaliser) — plus an unused copy of the v1 prompt in `main.py`. Nothing tied
them together, and nothing enforced the list on the way in: `/scan` passed the
model's `category` through verbatim, so an off-list answer ("Vintage", "Home
Decor") reached the client as-is, took the default band in the clamp, scored
the "other" familiarity in confidence, and was tallied as "other" in the
operator's trends. Three readings of one value.

Now `valuation.normalise` resolves the model's answer through `normalise`
below, so everything downstream sees one of these names, and adding a
category is one row here.

The prompts interpolate `PROMPT_LIST`, whose order is the order of this table.
That order is part of the prompt text the eval baselines were measured
against, so reordering the rows changes the prompt — bump
`prompts.PROMPT_VERSION` if you do.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    name: str
    #: (floor, ceiling) in USD for a *single secondhand item*. Ceilings sit
    #: well above the realistic top of each category so genuine finds are
    #: never clipped — they catch order-of-magnitude errors and injected
    #: numbers, not good guesses. Applied by `promptsafety.clamp_valuation`.
    band: tuple[float, float]
    #: How dense, documented and stable the secondhand market is, 0–1, so how
    #: likely a model-knowledge estimate is to land close. A confidence signal
    #: (`confidence.compute`); fine art or antiques vary enormously by piece,
    #: a Nike sneaker does not.
    familiarity: float
    #: The operator feed's marker. Not user-facing.
    emoji: str


OTHER = "other"

CATEGORIES: tuple[Category, ...] = (
    Category("clothing",     (1.0, 5_000.0),  0.90, "🧥"),
    Category("shoes",        (1.0, 5_000.0),  0.90, "👟"),
    # Designer bags and watches run high.
    Category("accessories",  (1.0, 20_000.0), 0.70, "👜"),
    Category("electronics",  (1.0, 10_000.0), 0.80, "📱"),
    # First editions.
    Category("books",        (1.0, 5_000.0),  0.65, "📚"),
    Category("furniture",    (1.0, 15_000.0), 0.50, "🪑"),
    Category("home",         (1.0, 5_000.0),  0.65, "🏠"),
    Category("sports",       (1.0, 5_000.0),  0.70, "⚽"),
    Category("toys",         (1.0, 10_000.0), 0.60, "🧸"),
    # Band deliberately loose; familiarity low because value is dominated by
    # rarity the photo cannot show.
    Category("collectibles", (1.0, 50_000.0), 0.35, "🏺"),
    Category(OTHER,          (1.0, 10_000.0), 0.30, "📦"),
)

BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}
NAMES: tuple[str, ...] = tuple(c.name for c in CATEGORIES)

#: Exactly as the prompts state it: "clothing, shoes, …, other".
PROMPT_LIST = ", ".join(NAMES)


def normalise(value: object) -> str:
    """One of `NAMES`: the model's category, case-folded, or `"other"`.

    Deliberately no synonym table. The prompt names the eleven values, a
    model that follows it matches exactly, and a guess at what "Home Decor"
    was meant to be is a second classifier nobody measures.
    """
    key = value.strip().lower() if isinstance(value, str) else ""
    return key if key in BY_NAME else OTHER
