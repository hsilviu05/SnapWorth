"""Gold benchmark dataset: schema, versioning, review workflow, governance.

Extends `dataset.py` rather than replacing it. The v1 `BenchmarkItem` loader
stays working; `GoldItem` is the richer record the production benchmark needs.

What a gold record actually is
------------------------------
A photograph paired with a **verified completed sale price**. Not an asking
price, not an appraisal, not an opinion. That distinction is the entire point:
SnapWorth's claim is about what an item sells for, so the ground truth must be
what an item sold for. Anything weaker turns the benchmark into a measurement of
someone's intuition.

Label drift is the main threat
------------------------------
A benchmark degrades in three ways, and all three are silent:

1. **Label edits after the fact.** Someone "corrects" a price to match what the
   model predicted. Guarded by content hashing — `DatasetVersion.content_hash`
   changes if any label changes, and the manifest records the hash of every
   frozen release.

2. **Composition creep.** Easy items get added because they are easy to source,
   so accuracy improves without the product improving. Guarded by
   `composition_drift`, which compares a candidate set against a frozen
   baseline and reports category, price-band and difficulty shifts.

3. **Test-set leakage into prompt design.** Prompts get tuned until the
   benchmark passes, which measures memorisation rather than capability.
   Guarded by the holdout split: `Split.DEV` is for iteration, `Split.TEST` is
   sealed and should be run rarely and never inspected item-by-item.

Governance is deliberately part of the schema, not a wiki page, because a
convention nobody can enforce is a convention that decays.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

log = logging.getLogger("snapworth.eval.schema")

SCHEMA_VERSION = "gold-v2"

# The brief asks for 1,000. That is the number at which per-category MdAPE has a
# usable sample for the long tail, not just the head — see docs/EVALUATION.md.
TARGET_SIZE = 1000


class Split(str, Enum):
    """Which pool a record belongs to.

    DEV is for iteration and may be inspected freely. TEST is sealed: run it to
    confirm a release, not to steer one. Splitting by a hash of the item id
    keeps assignment stable as the set grows — a record never moves pools.
    """

    DEV = "dev"
    TEST = "test"
    QUARANTINE = "quarantine"        # under review, excluded from both


class SellerType(str, Enum):
    PRIVATE = "private"
    BUSINESS = "business"
    CONSIGNMENT = "consignment"
    UNKNOWN = "unknown"


class AuthenticityStatus(str, Enum):
    """Verified status of the physical item, not the model's guess."""

    VERIFIED_AUTHENTIC = "verified_authentic"
    PRESUMED_AUTHENTIC = "presumed_authentic"
    VERIFIED_REPLICA = "verified_replica"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"       # unbranded items


class LabelConfidence(str, Enum):
    """How much the *label* can be trusted — distinct from model confidence.

    A benchmark with uncertain labels produces uncertain conclusions, and mixing
    the two hides that. Only CERTAIN and HIGH labels count toward headline
    accuracy; the rest are retained for error analysis, where a fuzzy label is
    still informative.
    """

    CERTAIN = "certain"          # own sale, receipt or screenshot held
    HIGH = "high"                # captured completed listing
    MEDIUM = "medium"            # reported by a trusted third party
    LOW = "low"                  # inferred; excluded from headline metrics

    @property
    def counts_toward_headline(self) -> bool:
        return self in {LabelConfidence.CERTAIN, LabelConfidence.HIGH}


class ReviewState(str, Enum):
    """Where a record sits in the intake workflow."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_RELABEL = "needs_relabel"

    @property
    def usable(self) -> bool:
        return self is ReviewState.APPROVED


class Difficulty(str, Enum):
    """Deliberate difficulty tiering.

    A benchmark of clean studio shots measures a product nobody uses. Real
    in-store photos are dim, cluttered and partial, so the set must contain them
    in a known proportion — and report accuracy separately, since a headline
    that mixes tiers hides regressions on the hard cases.
    """

    EASY = "easy"                # clear photo, visible tag, common item
    TYPICAL = "typical"          # representative in-store capture
    HARD = "hard"                # poor light, no tag, occluded, cluttered
    ADVERSARIAL = "adversarial"  # deliberately difficult
    NEGATIVE_CONTROL = "negative_control"   # not resalable at all


@dataclass(frozen=True)
class ImageRef:
    """One photograph of the item.

    Multiple angles matter because a single photo genuinely under-determines
    condition — the reverse of a garment is not visible, and the model should be
    evaluated on the same information a user would realistically provide.
    """

    path: str
    angle: str = "front"         # front | back | tag | detail | flaw | box
    is_primary: bool = False
    sha256: str | None = None    # tamper-evidence for the image itself


@dataclass
class GoldItem:
    """One verified benchmark record."""

    id: str
    images: list[ImageRef]
    actual_sale_price: float
    currency: str
    category: str

    # Ground-truth identification. Optional because a genuine record may
    # legitimately have an unidentifiable brand — those are the hard cases and
    # excluding them would flatter the benchmark.
    brand: str | None = None
    model: str | None = None
    variant: str | None = None
    condition: str | None = None

    # Sale context.
    sold_date: date | None = None
    marketplace: str | None = None
    region: str = "US"
    seller_type: SellerType = SellerType.UNKNOWN
    shipping_included: bool | None = None

    # Provenance and trust.
    authenticity: AuthenticityStatus = AuthenticityStatus.UNVERIFIED
    label_confidence: LabelConfidence = LabelConfidence.MEDIUM
    evidence_url: str | None = None
    evidence_note: str = ""

    # Governance.
    review_state: ReviewState = ReviewState.DRAFT
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    difficulty: Difficulty = Difficulty.TYPICAL
    split: Split | None = None       # assigned deterministically if unset
    tags: list[str] = field(default_factory=list)
    human_notes: str = ""

    schema_version: str = SCHEMA_VERSION
    created_at: datetime | None = None

    # ── Derived ──────────────────────────────────────────────────────────────

    @property
    def is_scoreable(self) -> bool:
        """Whether this record may contribute to reported accuracy.

        Four independent gates. Negative controls are excluded because there is
        no correct price to be accurate about — they are scored separately, on
        whether the system correctly declines to price them.
        """
        return (
            self.review_state.usable
            and self.difficulty is not Difficulty.NEGATIVE_CONTROL
            and self.split is not Split.QUARANTINE
            and self.actual_sale_price > 0
        )

    @property
    def counts_toward_headline(self) -> bool:
        return self.is_scoreable and self.label_confidence.counts_toward_headline

    @property
    def primary_image(self) -> ImageRef | None:
        for image in self.images:
            if image.is_primary:
                return image
        return self.images[0] if self.images else None

    def assigned_split(self, test_fraction: float = 0.3) -> Split:
        """Deterministic split from a hash of the id.

        Hash-based rather than random so a record never migrates between dev and
        test as the set grows — migration would leak test items into the pool
        used for iteration, which is the failure this split exists to prevent.
        """
        if self.split is not None:
            return self.split
        digest = hashlib.sha256(self.id.encode()).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        return Split.TEST if bucket < test_fraction else Split.DEV

    def label_fingerprint(self) -> str:
        """Hash over the *label* fields only.

        Changes if a ground-truth value is edited, and does not change when a
        note or tag is added. That separation is what makes silent label
        revision detectable without flagging routine annotation.
        """
        payload = json.dumps({
            "id": self.id,
            "price": round(self.actual_sale_price, 2),
            "currency": self.currency,
            "category": self.category,
            "brand": self.brand,
            "model": self.model,
            "variant": self.variant,
            "condition": self.condition,
            "sold_date": self.sold_date.isoformat() if self.sold_date else None,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def validate(self) -> list[str]:
        """Return a list of problems. Empty means the record is well-formed."""
        problems: list[str] = []
        if not self.id.strip():
            problems.append("missing id")
        if not self.images:
            problems.append("no images")
        if self.actual_sale_price < 0:
            problems.append("negative price")
        if self.difficulty is not Difficulty.NEGATIVE_CONTROL and self.actual_sale_price == 0:
            problems.append("zero price on a non-negative-control record")
        if not self.currency or len(self.currency) != 3:
            problems.append(f"invalid currency {self.currency!r}")
        if self.review_state is ReviewState.APPROVED and not self.reviewed_by:
            problems.append("approved without a reviewer")
        if (self.label_confidence in {LabelConfidence.CERTAIN, LabelConfidence.HIGH}
                and not (self.evidence_url or self.evidence_note)):
            problems.append(
                "high-confidence label without evidence — a claim of certainty "
                "must be traceable to something")
        if self.sold_date and self.sold_date > date.today():
            problems.append("sold_date is in the future")
        return problems

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["images"] = [asdict(i) for i in self.images]
        for key in ("seller_type", "authenticity", "label_confidence",
                    "review_state", "difficulty", "split"):
            value = getattr(self, key)
            payload[key] = value.value if isinstance(value, Enum) else value
        if self.sold_date:
            payload["sold_date"] = self.sold_date.isoformat()
        for key in ("reviewed_at", "created_at"):
            value = getattr(self, key)
            payload[key] = value.isoformat() if value else None
        return payload


def _enum(cls, raw, default):
    if raw is None:
        return default
    try:
        return cls(raw)
    except ValueError:
        return default


def item_from_dict(raw: dict) -> GoldItem | None:
    """Parse one record, returning None (with a log line) if malformed."""
    try:
        images = [
            ImageRef(
                path=i["path"], angle=i.get("angle", "front"),
                is_primary=bool(i.get("is_primary", False)),
                sha256=i.get("sha256"),
            )
            for i in raw.get("images", [])
        ]
        sold_date = (
            date.fromisoformat(raw["sold_date"]) if raw.get("sold_date") else None)
        return GoldItem(
            id=str(raw["id"]),
            images=images,
            actual_sale_price=float(raw["actual_sale_price"]),
            currency=str(raw.get("currency", "USD")).upper(),
            category=str(raw["category"]),
            brand=raw.get("brand"),
            model=raw.get("model"),
            variant=raw.get("variant"),
            condition=raw.get("condition"),
            sold_date=sold_date,
            marketplace=raw.get("marketplace"),
            region=raw.get("region", "US"),
            seller_type=_enum(SellerType, raw.get("seller_type"), SellerType.UNKNOWN),
            shipping_included=raw.get("shipping_included"),
            authenticity=_enum(AuthenticityStatus, raw.get("authenticity"),
                               AuthenticityStatus.UNVERIFIED),
            label_confidence=_enum(LabelConfidence, raw.get("label_confidence"),
                                   LabelConfidence.MEDIUM),
            evidence_url=raw.get("evidence_url"),
            evidence_note=raw.get("evidence_note", ""),
            review_state=_enum(ReviewState, raw.get("review_state"), ReviewState.DRAFT),
            reviewed_by=raw.get("reviewed_by"),
            difficulty=_enum(Difficulty, raw.get("difficulty"), Difficulty.TYPICAL),
            split=_enum(Split, raw.get("split"), None),
            tags=list(raw.get("tags", [])),
            human_notes=raw.get("human_notes", ""),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("malformed gold record: %s", exc)
        return None


# ── Versioning ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DatasetVersion:
    """An immutable, content-addressed snapshot of the gold set."""

    version: str                     # e.g. "2026.07.28-1"
    created_at: datetime
    item_count: int
    content_hash: str                # over every label fingerprint
    schema_version: str = SCHEMA_VERSION
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "item_count": self.item_count,
            "content_hash": self.content_hash,
            "schema_version": self.schema_version,
            "note": self.note,
        }


def content_hash(items: list[GoldItem]) -> str:
    """Stable hash over every label in the set.

    Order-independent (fingerprints are sorted), so reordering the file does not
    invalidate a version — but changing any ground-truth value does.
    """
    fingerprints = sorted(item.label_fingerprint() for item in items)
    return hashlib.sha256("|".join(fingerprints).encode()).hexdigest()[:32]


def freeze(items: list[GoldItem], version: str, note: str = "") -> DatasetVersion:
    """Create a version record for the current state of the set."""
    return DatasetVersion(
        version=version,
        created_at=datetime.now(timezone.utc),
        item_count=len(items),
        content_hash=content_hash(items),
        note=note,
    )


# ── Governance ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DriftReport:
    """Composition comparison between a candidate set and a frozen baseline."""

    label_changes: int
    added: int
    removed: int
    category_shift: dict[str, float]
    difficulty_shift: dict[str, float]
    price_median_change_pct: float | None
    warnings: list[str]

    @property
    def has_label_drift(self) -> bool:
        return self.label_changes > 0

    @property
    def is_clean(self) -> bool:
        return not self.warnings and not self.has_label_drift


def _distribution(items: list[GoldItem], key) -> dict[str, float]:
    if not items:
        return {}
    counts: dict[str, int] = {}
    for item in items:
        value = key(item)
        counts[value] = counts.get(value, 0) + 1
    return {k: v / len(items) for k, v in counts.items()}


def composition_drift(
    baseline: list[GoldItem],
    candidate: list[GoldItem],
    *,
    shift_threshold: float = 0.10,
) -> DriftReport:
    """Compare a candidate set against a frozen baseline.

    Catches the two silent degradations that matter:

    * **Label revision** — a record whose id persists but whose ground truth
      changed. This is the serious one; a price quietly edited toward what the
      model predicted turns the benchmark into a mirror.
    * **Composition creep** — easy items accumulating because they are easy to
      source, so accuracy improves without the product improving.
    """
    baseline_by_id = {item.id: item for item in baseline}
    candidate_by_id = {item.id: item for item in candidate}

    label_changes = sum(
        1 for item_id, item in candidate_by_id.items()
        if item_id in baseline_by_id
        and item.label_fingerprint() != baseline_by_id[item_id].label_fingerprint()
    )
    added = len(set(candidate_by_id) - set(baseline_by_id))
    removed = len(set(baseline_by_id) - set(candidate_by_id))

    base_cat = _distribution(baseline, lambda i: i.category)
    cand_cat = _distribution(candidate, lambda i: i.category)
    category_shift = {
        key: round(cand_cat.get(key, 0.0) - base_cat.get(key, 0.0), 4)
        for key in set(base_cat) | set(cand_cat)
    }

    base_diff = _distribution(baseline, lambda i: i.difficulty.value)
    cand_diff = _distribution(candidate, lambda i: i.difficulty.value)
    difficulty_shift = {
        key: round(cand_diff.get(key, 0.0) - base_diff.get(key, 0.0), 4)
        for key in set(base_diff) | set(cand_diff)
    }

    def median_price(items):
        prices = sorted(i.actual_sale_price for i in items if i.is_scoreable)
        return prices[len(prices) // 2] if prices else None

    base_median, cand_median = median_price(baseline), median_price(candidate)
    price_change = (
        round((cand_median - base_median) / base_median * 100, 2)
        if base_median and cand_median and base_median > 0 else None
    )

    warnings: list[str] = []
    if label_changes:
        warnings.append(
            f"{label_changes} record(s) changed ground truth — label revision "
            "must be reviewed, not merged silently")
    if removed:
        warnings.append(f"{removed} record(s) removed from the baseline")
    for key, delta in category_shift.items():
        if abs(delta) > shift_threshold:
            warnings.append(f"category {key!r} share shifted {delta:+.1%}")
    for key, delta in difficulty_shift.items():
        if abs(delta) > shift_threshold:
            warnings.append(f"difficulty {key!r} share shifted {delta:+.1%}")
    if price_change is not None and abs(price_change) > 25:
        warnings.append(f"median price shifted {price_change:+.1f}%")

    return DriftReport(
        label_changes=label_changes, added=added, removed=removed,
        category_shift=category_shift, difficulty_shift=difficulty_shift,
        price_median_change_pct=price_change, warnings=warnings,
    )


# ── Composition targets ──────────────────────────────────────────────────────
#
# Weighted to mirror real scan traffic, not spread evenly. A uniform benchmark
# over-weights categories users rarely scan and will happily report
# "improvement" nobody experiences.

TARGET_CATEGORIES: dict[str, int] = {
    "clothing": 300, "shoes": 150, "electronics": 120, "accessories": 100,
    "home": 80, "collectibles": 80, "books": 60, "sports": 50,
    "toys": 40, "furniture": 20,
}

TARGET_DIFFICULTY: dict[str, float] = {
    "easy": 0.20, "typical": 0.45, "hard": 0.20,
    "adversarial": 0.10, "negative_control": 0.05,
}

TARGET_REGIONS: dict[str, float] = {"US": 0.6, "GB": 0.2, "EU": 0.2}


@dataclass(frozen=True)
class CoverageReport:
    total: int
    scoreable: int
    headline_eligible: int
    approved: int
    pending: int
    meets_target: bool
    by_category: dict[str, int]
    category_gaps: dict[str, int]
    by_difficulty: dict[str, int]
    difficulty_gaps: dict[str, float]
    by_region: dict[str, int]
    by_label_confidence: dict[str, int]
    split_counts: dict[str, int]
    problems: list[str]


def coverage(items: list[GoldItem]) -> CoverageReport:
    """Report composition against target, so the set is grown deliberately."""
    def tally(key) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in items:
            value = key(item)
            out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items()))

    scoreable = [i for i in items if i.is_scoreable]
    headline = [i for i in items if i.counts_toward_headline]
    by_category = tally(lambda i: i.category)
    by_difficulty = tally(lambda i: i.difficulty.value)

    gaps = {
        cat: target - by_category.get(cat, 0)
        for cat, target in TARGET_CATEGORIES.items()
        if by_category.get(cat, 0) < target
    }

    difficulty_gaps: dict[str, float] = {}
    if items:
        for name, target_share in TARGET_DIFFICULTY.items():
            actual = by_difficulty.get(name, 0) / len(items)
            if abs(actual - target_share) > 0.05:
                difficulty_gaps[name] = round(target_share - actual, 4)

    problems: list[str] = []
    for item in items:
        problems.extend(f"{item.id}: {p}" for p in item.validate())

    return CoverageReport(
        total=len(items),
        scoreable=len(scoreable),
        headline_eligible=len(headline),
        approved=sum(1 for i in items if i.review_state is ReviewState.APPROVED),
        pending=sum(1 for i in items if i.review_state is ReviewState.PENDING_REVIEW),
        meets_target=len(headline) >= TARGET_SIZE,
        by_category=by_category,
        category_gaps=dict(sorted(gaps.items(), key=lambda kv: -kv[1])),
        by_difficulty=by_difficulty,
        difficulty_gaps=difficulty_gaps,
        by_region=tally(lambda i: i.region),
        by_label_confidence=tally(lambda i: i.label_confidence.value),
        split_counts=tally(lambda i: i.assigned_split().value),
        problems=problems[:50],
    )


def load_gold(path: str | Path) -> list[GoldItem]:
    """Load a JSONL gold set, skipping malformed lines rather than failing."""
    items: list[GoldItem] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("gold set line is not valid JSON: %s", exc)
                continue
            item = item_from_dict(raw)
            if item is not None:
                items.append(item)

    ids = [i.id for i in items]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        # Duplicate ids double-weight those records in every metric.
        log.warning("gold set contains duplicate ids: %s", sorted(duplicates))
    return items


def save_gold(items: list[GoldItem], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.to_dict(), sort_keys=True) + "\n")
