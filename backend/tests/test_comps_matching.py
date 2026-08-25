"""Matching, normalisation and dedupe tests.

The headline cases are the Air Max 97/95 pair from the design brief: a wrong
comp is worse than no comp, because it carries the authority of evidence.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comps import dedupe as dedupe_module  # noqa: E402
from comps import matching, normalize  # noqa: E402
from comps.models import Comp, Condition, ItemIdentity, Marketplace  # noqa: E402

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def comp(title, *, price="100", days_ago=5, provider=Marketplace.EBAY,
         condition=Condition.GOOD, external_id=None, shipping=None,
         seller_id=None, seller_rating=None):
    return Comp(
        provider=provider,
        external_id=external_id or title[:24],
        title=title,
        price=Decimal(price),
        sold_at=NOW - timedelta(days=days_ago),
        condition=condition,
        shipping=Decimal(shipping) if shipping else None,
        seller_id=seller_id,
        seller_rating=seller_rating,
    )


def identity(**kw):
    # dict[str, Any]: an unannotated dict() literal infers a union of its
    # value types, and splatting that reports one error per parameter of
    # the constructor below — dozens from a single line.
    base: dict[str, Any] = dict(category="shoes", brand="Nike", model="Air Max 97",
                condition=Condition.GOOD)
    base.update(kw)
    return ItemIdentity(**base)


# ── Token typing ─────────────────────────────────────────────────────────────

class TestTokenTyping:
    def test_model_numbers_are_designators(self):
        assert normalize.is_designator("97")
        assert normalize.is_designator("501")
        assert normalize.is_designator("xm3")
        assert normalize.is_designator("1460")

    def test_plausible_release_years_are_not_designators(self):
        assert normalize.is_year("2022")
        assert not normalize.is_designator("2022")

    def test_plain_words_are_never_designators(self):
        assert not normalize.is_designator("silver")
        assert not normalize.is_designator("bullet")

    def test_token_set_separates_types(self):
        ts = normalize.token_set("Nike Air Max 97 Silver Bullet 2022")
        assert ts.designators == frozenset({"97"})
        assert ts.years == frozenset({2022})
        assert {"air", "max", "silver", "bullet"} <= ts.words

    def test_containment_is_asymmetric_by_design(self):
        # Extra candidate tokens are cheap; missing query tokens are expensive.
        query = frozenset({"air", "max", "97"})
        rich = frozenset({"air", "max", "97", "silver", "bullet"})
        assert normalize.containment(query, rich) == 1.0
        assert normalize.containment(rich, query) < 1.0

    def test_jaccard_would_rate_97_and_95_as_similar(self):
        """Documents *why* soft similarity alone is unsafe here."""
        a = normalize.token_set("Nike Air Max 97").all_tokens
        b = normalize.token_set("Nike Air Max 95").all_tokens
        assert normalize.jaccard(a, b) > 0.5      # dangerously high


# ── The brief's headline cases ───────────────────────────────────────────────

class TestDesignatorVeto:
    def test_air_max_95_is_rejected_for_air_max_97(self):
        result = matching.score(identity(), comp("Nike Air Max 95 OG"), now=NOW)
        assert result.vetoed
        assert result.score == 0.0
        assert "designator conflict" in (result.veto_reason or "")

    def test_air_max_97_silver_bullet_2022_matches_strongly(self):
        result = matching.score(
            identity(), comp("Nike Air Max 97 Silver Bullet 2022 Mens"), now=NOW)
        assert not result.vetoed
        assert result.score >= 0.8, f"expected a strong match, got {result.score}"

    def test_exact_model_matches(self):
        result = matching.score(identity(), comp("Nike Air Max 97"), now=NOW)
        assert result.accepted

    def test_vague_listing_without_designator_survives(self):
        # "Nike Air Max Silver Bullet" is a sloppy but legitimate listing for
        # the same shoe. Absence is not contradiction.
        result = matching.score(
            identity(), comp("Nike Air Max Silver Bullet"), now=NOW)
        assert not result.vetoed

    def test_different_designator_vetoes_even_with_identical_wording(self):
        result = matching.score(
            identity(), comp("Nike Air Max 95 Silver Bullet 2022"), now=NOW)
        assert result.vetoed

    def test_alphanumeric_designators_match(self):
        result = matching.score(
            identity(brand="New Balance", model="990v5"),
            comp("New Balance 990v5 Grey"), now=NOW)
        assert not result.vetoed
        assert result.accepted


class TestBrandVeto:
    def test_missing_brand_is_vetoed(self):
        result = matching.score(identity(), comp("Adidas Ultraboost 97"), now=NOW)
        assert result.vetoed
        assert "brand" in (result.veto_reason or "").lower()

    def test_multiword_brand_matches_on_distinctive_token(self):
        result = matching.score(
            identity(brand="The North Face", model="Nuptse 700"),
            comp("North Face Nuptse 700 Puffer"), now=NOW)
        assert not result.vetoed

    def test_unknown_brand_does_not_veto(self):
        result = matching.score(
            identity(brand="Unknown", model="Air Max 97"),
            comp("Air Max 97 sneakers"), now=NOW)
        assert not result.vetoed


# ── Soft signals ─────────────────────────────────────────────────────────────

class TestSoftScoring:
    def test_recent_sale_scores_above_old_sale(self):
        recent = matching.score(identity(), comp("Nike Air Max 97", days_ago=3), now=NOW)
        old = matching.score(identity(), comp("Nike Air Max 97", days_ago=150), now=NOW)
        assert recent.score > old.score

    def test_matching_condition_scores_above_mismatched(self):
        same = matching.score(
            identity(), comp("Nike Air Max 97", condition=Condition.GOOD), now=NOW)
        different = matching.score(
            identity(), comp("Nike Air Max 97", condition=Condition.NEW), now=NOW)
        assert same.score > different.score

    def test_size_confirmation_helps(self):
        with_size = matching.score(
            identity(size="10"), comp("Nike Air Max 97 Size 10"), now=NOW)
        without = matching.score(
            identity(size="10"), comp("Nike Air Max 97"), now=NOW)
        assert with_size.score > without.score

    def test_year_mismatch_penalises_heavily(self):
        close = matching.score(
            identity(year=2022), comp("Nike Air Max 97 2022"), now=NOW)
        far = matching.score(
            identity(year=2022), comp("Nike Air Max 97 1998"), now=NOW)
        assert close.score > far.score * 1.4

    def test_scores_are_bounded(self):
        result = matching.score(identity(), comp("Nike Air Max 97"), now=NOW)
        assert 0.0 <= result.score <= 1.0

    def test_reasons_are_populated_for_imperfect_matches(self):
        result = matching.score(
            identity(size="10"), comp("Nike Air Max 97", days_ago=200), now=NOW)
        assert result.reasons


class TestRank:
    def test_rank_filters_and_orders(self):
        comps = [
            comp("Nike Air Max 95", external_id="a"),              # vetoed
            comp("Nike Air Max 97", external_id="b", days_ago=2),
            comp("Nike Air Max 97 Silver Bullet", external_id="c", days_ago=60),
        ]
        ranked = matching.rank(identity(), comps, now=NOW)
        assert [c.external_id for c in ranked] == ["b", "c"] or \
               [c.external_id for c in ranked] == ["c", "b"]
        assert all(c.match_score > 0 for c in ranked)

    def test_rank_annotates_scores(self):
        ranked = matching.rank(identity(), [comp("Nike Air Max 97")], now=NOW)
        assert ranked[0].match_score > 0
        assert isinstance(ranked[0].match_reasons, tuple)

    def test_rank_is_sorted_best_first(self):
        comps = [comp(f"Nike Air Max 97 v{i}", external_id=str(i), days_ago=i * 20)
                 for i in range(1, 5)]
        ranked = matching.rank(identity(), comps, now=NOW)
        scores = [c.match_score for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_empty_input_is_safe(self):
        assert matching.rank(identity(), [], now=NOW) == []


# ── Condition normalisation ──────────────────────────────────────────────────

class TestConditionNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("Brand New", Condition.NEW),
        ("New with tags", Condition.NEW),
        ("NWT", Condition.NEW),
        ("Deadstock", Condition.NEW),
        ("DS", Condition.NEW),
        ("Like New", Condition.LIKE_NEW),
        ("NWOT", Condition.LIKE_NEW),
        ("VNDS", Condition.LIKE_NEW),
        ("Pre-owned", Condition.GOOD),
        ("Very Good", Condition.GOOD),
        ("EUC", Condition.GOOD),
        ("Fair", Condition.USED),
        ("For parts", Condition.USED),
        ("As-is", Condition.USED),
    ])
    def test_marketplace_vocabularies_fold_correctly(self, raw, expected):
        assert normalize.condition(raw) is expected

    def test_compound_description_matches_by_substring(self):
        assert normalize.condition(
            "Pre-owned - some pilling at the cuffs") is Condition.GOOD

    def test_unknown_condition_returns_default_not_a_guess(self):
        # Inventing "good" would let a for-parts listing weight as wearable.
        assert normalize.condition("indeterminate") is None
        assert normalize.condition(None) is None

    def test_condition_distance(self):
        assert Condition.NEW.distance(Condition.USED) == 3
        assert Condition.GOOD.distance(Condition.GOOD) == 0


# ── Currency ─────────────────────────────────────────────────────────────────

class TestCurrency:
    def test_usd_is_identity(self):
        assert normalize.to_usd(Decimal("50"), "USD") == Decimal("50.00")

    def test_gbp_converts(self):
        assert normalize.to_usd(Decimal("100"), "GBP") > Decimal("100")

    def test_jpy_converts_to_a_sane_magnitude(self):
        # Treating 8000 JPY as $8000 would corrupt an entire comp set.
        assert normalize.to_usd(Decimal("8000"), "JPY") < Decimal("100")

    def test_unknown_currency_raises_rather_than_defaulting(self):
        with pytest.raises(normalize.FXUnavailable):
            normalize.to_usd(Decimal("10"), "XYZ")

    def test_negative_amount_rejected(self):
        with pytest.raises(normalize.FXUnavailable):
            normalize.to_usd(Decimal("-5"), "USD")

    def test_case_insensitive_currency_code(self):
        assert normalize.to_usd(Decimal("10"), "gbp") == normalize.to_usd(
            Decimal("10"), "GBP")

    def test_rates_expose_their_own_staleness(self):
        rates = normalize.current_rates()
        assert hasattr(rates, "is_stale")
        assert isinstance(rates.age_days, int)


# ── Deduplication ────────────────────────────────────────────────────────────

class TestDedupe:
    def test_exact_duplicates_collapse(self):
        c = comp("Nike Air Max 97", external_id="X")
        report = dedupe_module.deduplicate([c, c])
        assert len(report.kept) == 1
        assert report.exact_removed == 1

    def test_same_seller_relist_collapses(self):
        # Relists cluster at the price that FAILED to sell, biasing the median.
        # A price move between attempts is normal, so the seller decides it.
        a = comp("Nike Air Max 97 Silver Bullet", external_id="1",
                 price="100", days_ago=2, seller_id="seller-a")
        b = comp("Nike Air Max 97 Silver Bullet", external_id="2",
                 price="115", days_ago=4, seller_id="seller-a")
        report = dedupe_module.deduplicate([a, b])
        assert len(report.kept) == 1
        assert report.relist_removed == 1

    def test_different_sellers_are_never_relists(self):
        """The fungible-goods case: ten sellers moving a common item at a
        similar price are ten data points, not one listing."""
        comps = [
            comp("Nike Air Max 97", external_id=str(i), price="100",
                 days_ago=i % 5, seller_id=f"seller-{i}")
            for i in range(10)
        ]
        report = dedupe_module.deduplicate(comps)
        assert len(report.kept) == 10
        assert report.relist_removed == 0

    def test_unknown_seller_requires_near_exact_agreement(self):
        # Weak evidence ⇒ strict threshold. 1% apart is not enough.
        a = comp("Nike Air Max 97", external_id="1", price="100", days_ago=2)
        b = comp("Nike Air Max 97", external_id="2", price="101", days_ago=3)
        assert len(dedupe_module.deduplicate([a, b]).kept) == 2

    def test_dedupe_never_guts_a_fungible_cluster(self):
        """Safety valve: if most of a set looks duplicated, it is far more
        likely a cluster of identical common items than genuine relists."""
        comps = [
            comp("Nike Air Max 97", external_id=str(i), price="100", days_ago=1)
            for i in range(20)
        ]
        report = dedupe_module.deduplicate(comps)
        assert len(report.kept) >= int(20 * (1 - dedupe_module.MAX_RELIST_REMOVAL_RATIO))

    def test_distant_sales_are_not_relists(self):
        a = comp("Nike Air Max 97", external_id="1", price="100", days_ago=2)
        b = comp("Nike Air Max 97", external_id="2", price="100", days_ago=40)
        assert len(dedupe_module.deduplicate([a, b]).kept) == 2

    def test_different_prices_are_not_relists(self):
        a = comp("Nike Air Max 97", external_id="1", price="100", days_ago=2)
        b = comp("Nike Air Max 97", external_id="2", price="180", days_ago=3)
        assert len(dedupe_module.deduplicate([a, b]).kept) == 2

    def test_cross_provider_duplicate_keeps_higher_precedence(self):
        ebay = comp("Nike Air Max 97 Silver", external_id="e",
                    provider=Marketplace.EBAY, price="100")
        stockx = comp("Nike Air Max 97 Silver", external_id="s",
                      provider=Marketplace.STOCKX, price="102")
        report = dedupe_module.deduplicate([ebay, stockx])
        assert len(report.kept) == 1
        assert report.kept[0].provider is Marketplace.STOCKX

    def test_genuinely_different_items_survive(self):
        comps = [
            comp("Nike Air Max 97 Silver", external_id="1", price="100"),
            comp("Nike Air Max 97 Triple Black", external_id="2", price="140"),
            comp("Nike Air Max 97 Gold", external_id="3", price="220"),
        ]
        assert len(dedupe_module.deduplicate(comps).kept) == 3

    def test_empty_input_is_safe(self):
        report = dedupe_module.deduplicate([])
        assert report.kept == () and report.removed == 0

    def test_shipping_is_included_in_price_comparison(self):
        # A free-shipping listing and a "$88 + $12 postage" listing are the same
        # transaction from the buyer's side.
        a = comp("Nike Air Max 97", external_id="1", price="100")
        b = comp("Nike Air Max 97", external_id="2", price="88", shipping="12")
        assert len(dedupe_module.deduplicate([a, b]).kept) == 1
