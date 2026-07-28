"""Normalisation: text, tokens, condition vocabularies and currency.

Everything upstream of matching depends on this. Two listings describing the
same object rarely agree on spelling, punctuation, word order, condition
vocabulary or currency, and none of the later stages can recover from
normalisation done badly.

The important idea here is **token typing**. Naive text similarity is actively
dangerous for resale matching:

    "Nike Air Max 97"  vs  "Nike Air Max 95"   →  Jaccard 0.75

Three of four tokens match, so any bag-of-words or embedding similarity rates
these as near-identical. They are different shoes with different prices, and
treating them as comparable would produce a confidently wrong valuation.

So tokens are classified by *what they mean*, not just compared as strings:

* **designators** (`97`, `501`, `XM3`, `1460`) identify the model. A conflict
  here is a veto, never a soft penalty.
* **years** (`2022`) date the release. Compared with tolerance.
* **words** (`silver`, `bullet`) describe the variant. Compared fuzzily.

That distinction is what makes "Air Max 97" reject "Air Max 95" while strongly
matching "Air Max 97 Silver Bullet 2022".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from comps.models import Condition

# ── Text ─────────────────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# Words that carry no discriminative signal in a listing title. Removing them
# stops "Nike Air Max 97 for sale free shipping" from diluting token overlap.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "in", "on", "of", "to",
    "size", "sz", "mens", "womens", "men", "women", "unisex", "adult",
    "new", "used", "vintage", "rare", "authentic", "genuine", "original",
    "free", "shipping", "ship", "fast", "sale", "sold", "listing", "item",
    "excellent", "great", "good", "nice", "clean", "euc", "nwt", "nwot",
    "vgc", "bnwt", "preowned", "pre", "owned", "condition", "cond",
})

# Equivalences that appear constantly in resale titles. Applied after token
# splitting so "1/4 zip" and "quarter zip" collide.
SYNONYMS: dict[str, str] = {
    "quarter": "14", "qtr": "14",
    "half": "12",
    "1/4": "14", "1/2": "12", "3/4": "34",
    "sneakers": "shoes", "sneaker": "shoes", "trainers": "shoes",
    "kicks": "shoes", "footwear": "shoes",
    "jumper": "sweater", "pullover": "sweater", "sweatshirt": "sweater",
    "trousers": "pants", "jeans": "pants",
    "jacket": "coat", "parka": "coat",
    "purse": "bag", "handbag": "bag", "tote": "bag",
    "wristwatch": "watch",
    "lp": "vinyl", "record": "vinyl", "album": "vinyl",
    "grey": "gray",
    "colour": "color", "colours": "color", "colors": "color",
}

# Years outside this window are model designators, not release dates. Chosen so
# Dr. Martens 1460 and Levi's 501 read as designators while 2022 reads as a year.
YEAR_MIN = 1970
YEAR_MAX = date.today().year + 1


def normalise_text(value: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace.

    NFKD folding means `Café` and `Cafe` collide, which matters for European
    marketplace titles.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def tokenize(value: str, *, drop_stopwords: bool = True) -> list[str]:
    """Normalise, split, apply synonyms and optionally drop stopwords."""
    tokens = normalise_text(value).split()
    tokens = [SYNONYMS.get(t, t) for t in tokens]
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [t for t in tokens if t]


def is_year(token: str) -> bool:
    return token.isdigit() and len(token) == 4 and YEAR_MIN <= int(token) <= YEAR_MAX


def is_designator(token: str) -> bool:
    """True for tokens that identify a specific model.

    Two shapes qualify:

    * pure digits that are *not* a plausible release year (`97`, `501`, `1460`)
    * alphanumeric mixes (`xm3`, `a1502`, `mk2`)

    A pure word is never a designator — it belongs to variant description, where
    fuzzy comparison is appropriate.
    """
    if not token:
        return False
    if token.isdigit():
        return not is_year(token)
    has_digit = any(c.isdigit() for c in token)
    has_alpha = any(c.isalpha() for c in token)
    return has_digit and has_alpha


@dataclass(frozen=True)
class TokenSet:
    """A title or identity decomposed by token type."""

    designators: frozenset[str]
    years: frozenset[int]
    words: frozenset[str]

    @property
    def all_tokens(self) -> frozenset[str]:
        return self.designators | self.words | frozenset(str(y) for y in self.years)

    @property
    def is_empty(self) -> bool:
        return not (self.designators or self.years or self.words)


def token_set(value: str, *, drop_stopwords: bool = True) -> TokenSet:
    designators: set[str] = set()
    years: set[int] = set()
    words: set[str] = set()
    for token in tokenize(value, drop_stopwords=drop_stopwords):
        if is_year(token):
            years.add(int(token))
        elif is_designator(token):
            designators.add(token)
        else:
            words.add(token)
    return TokenSet(frozenset(designators), frozenset(years), frozenset(words))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def containment(needle: frozenset[str], haystack: frozenset[str]) -> float:
    """Fraction of `needle` present in `haystack`.

    Asymmetric on purpose. A candidate title carrying *extra* words is fine —
    "Air Max 97 Silver Bullet 2022" contains everything "Air Max 97" asked for —
    whereas a candidate *missing* our words is a genuinely worse match. Jaccard
    penalises both symmetrically and would wrongly demote the richer title.
    """
    if not needle:
        return 1.0
    return len(needle & haystack) / len(needle)


# ── Condition ────────────────────────────────────────────────────────────────

# Marketplace condition vocabularies, folded into our four grades. Keys are
# normalised, so "Brand New (With Tags)" is matched via its normalised form.
_CONDITION_MAP: dict[str, Condition] = {
    "new": Condition.NEW,
    "brand new": Condition.NEW,
    "new with tags": Condition.NEW,
    "new with box": Condition.NEW,
    "nwt": Condition.NEW,
    "bnwt": Condition.NEW,
    "deadstock": Condition.NEW,
    "ds": Condition.NEW,
    "sealed": Condition.NEW,
    "mint": Condition.NEW,

    "like new": Condition.LIKE_NEW,
    "new without tags": Condition.LIKE_NEW,
    "nwot": Condition.LIKE_NEW,
    "open box": Condition.LIKE_NEW,
    "excellent": Condition.LIKE_NEW,
    "near mint": Condition.LIKE_NEW,
    "vnds": Condition.LIKE_NEW,          # "very near deadstock"
    "gently used": Condition.LIKE_NEW,
    "barely used": Condition.LIKE_NEW,

    "good": Condition.GOOD,
    "very good": Condition.GOOD,
    "used": Condition.GOOD,
    "pre owned": Condition.GOOD,
    "preowned": Condition.GOOD,
    "second hand": Condition.GOOD,
    "euc": Condition.GOOD,
    "vgc": Condition.GOOD,

    "fair": Condition.USED,
    "acceptable": Condition.USED,
    "poor": Condition.USED,
    "worn": Condition.USED,
    "heavily used": Condition.USED,
    "for parts": Condition.USED,
    "damaged": Condition.USED,
    "as is": Condition.USED,
}


def condition(value: str | None, *, default: Condition | None = None) -> Condition | None:
    """Fold a marketplace condition string into our ladder.

    Returns `default` (usually None) rather than guessing when unrecognised —
    an unknown condition is genuinely unknown, and inventing "good" would let a
    for-parts listing weight as a wearable one.
    """
    if not value:
        return default
    key = normalise_text(value)
    if not key:
        return default
    if key in _CONDITION_MAP:
        return _CONDITION_MAP[key]
    # Substring fallback for compound descriptions like
    # "Pre-owned - some pilling at the cuffs".
    for phrase, graded in sorted(_CONDITION_MAP.items(), key=lambda kv: -len(kv[0])):
        if phrase in key:
            return graded
    return default


# ── Currency ─────────────────────────────────────────────────────────────────

class FXUnavailable(Exception):
    """No usable rate for a currency. Callers must drop the comp, not guess."""


# Static fallback rates to USD, captured 2026-07-28.
#
# Deliberately static and deliberately dated. A live FX feed needs credentials,
# which this milestone excludes, and a *silently stale* rate is far worse than a
# visibly stale one — it would skew every non-USD comp with no signal that
# anything was wrong. `FXRates.is_stale` makes the staleness observable, and
# `set_provider` is the seam for a real feed.
_FALLBACK_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("1.09"),
    "GBP": Decimal("1.27"),
    "CAD": Decimal("0.73"),
    "AUD": Decimal("0.66"),
    "JPY": Decimal("0.0064"),
    "CHF": Decimal("1.12"),
    "SEK": Decimal("0.094"),
    "NOK": Decimal("0.092"),
    "DKK": Decimal("0.146"),
    "PLN": Decimal("0.25"),
    "RON": Decimal("0.22"),
    "CZK": Decimal("0.043"),
    "NZD": Decimal("0.61"),
    "SGD": Decimal("0.74"),
    "HKD": Decimal("0.128"),
    "MXN": Decimal("0.050"),
    "BRL": Decimal("0.18"),
    "INR": Decimal("0.012"),
    "KRW": Decimal("0.00072"),
    "CNY": Decimal("0.138"),
}

_RATES_CAPTURED = date(2026, 7, 28)
_STALE_AFTER_DAYS = 90


@dataclass(frozen=True)
class FXRates:
    """A snapshot of rates to USD."""

    rates: dict[str, Decimal]
    captured: date

    @property
    def age_days(self) -> int:
        return (date.today() - self.captured).days

    @property
    def is_stale(self) -> bool:
        return self.age_days > _STALE_AFTER_DAYS

    def to_usd(self, amount: Decimal, currency: str) -> Decimal:
        code = (currency or "USD").strip().upper()
        rate = self.rates.get(code)
        if rate is None:
            raise FXUnavailable(f"no rate for {code!r}")
        return (amount * rate).quantize(Decimal("0.01"))


_provider: FXRates = FXRates(rates=dict(_FALLBACK_RATES), captured=_RATES_CAPTURED)


def set_provider(rates: FXRates) -> None:
    """Install a rate source. The seam for a live feed."""
    global _provider
    _provider = rates


def current_rates() -> FXRates:
    return _provider


def to_usd(amount: Decimal | float | str, currency: str = "USD") -> Decimal:
    """Convert to USD. Raises `FXUnavailable` for unknown currencies.

    Raising rather than defaulting to 1.0 is deliberate: treating 8,000 JPY as
    $8,000 would corrupt an entire comp set, and silently dropping the comp is
    the safe failure.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise FXUnavailable(f"unparseable amount {amount!r}") from exc
    if value < 0:
        raise FXUnavailable("negative amount")
    return _provider.to_usd(value, currency)


def supported_currencies() -> frozenset[str]:
    return frozenset(_provider.rates)
