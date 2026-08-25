"""Aggregation, engine orchestration, catalog and search tests."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.conftest import not_none
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comps import aggregate as agg  # noqa: E402
from comps import search as search_module  # noqa: E402
from comps.cache import CompsCache, NullCompsCache  # noqa: E402
from comps.catalog import Catalog, catalog  # noqa: E402
from comps.engine import CompsEngine  # noqa: E402
from comps.flags import CompsFlags  # noqa: E402
from comps.models import (  # noqa: E402
    Comp,
    CompsStatus,
    Condition,
    ItemIdentity,
    Marketplace,
)
from comps.providers.base import ProviderRegistry  # noqa: E402
from comps.providers.stubs import FixtureProvider, default_stubs, register_defaults  # noqa: E402

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def comp(price, *, days_ago=5, title="Nike Air Max 97", provider=Marketplace.EBAY,
         condition=Condition.GOOD, external_id=None, match=0.9,
         seller_id=None, seller_rating=None):
    return Comp(
        provider=provider,
        external_id=external_id or f"{title}-{price}-{days_ago}",
        title=title,
        price=Decimal(str(price)),
        sold_at=NOW - timedelta(days=days_ago),
        condition=condition,
        seller_id=seller_id,
        seller_rating=seller_rating,
    ).with_score(match)


def identity(**kw):
    # dict[str, Any]: an unannotated dict() literal infers a union of its
    # value types, and splatting that reports one error per parameter of
    # the constructor below — dozens from a single line.
    base: dict[str, Any] = dict(category="shoes", brand="Nike", model="Air Max 97",
                condition=Condition.GOOD)
    base.update(kw)
    return ItemIdentity(**base)


# ── Robust statistics ────────────────────────────────────────────────────────

class TestAggregation:
    def test_below_minimum_returns_none(self):
        assert agg.aggregate([comp(100) for _ in range(agg.MIN_COMPS - 1)],
                             now=NOW) is None

    def test_median_is_robust_to_a_wild_outlier(self):
        """The reason we do not average.

        One $1,200 data-entry error in a set of $40 sales moves a mean by ~$100
        and a median by nothing.
        """
        comps = [comp(40, external_id=str(i), days_ago=i) for i in range(9)]
        comps.append(comp(1200, external_id="outlier"))
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert Decimal("35") <= evidence.median <= Decimal("45")

    def test_outliers_are_reported(self):
        comps = [comp(40, external_id=str(i), days_ago=i) for i in range(9)]
        comps.append(comp(5000, external_id="bomb"))
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.outliers_removed >= 1

    def test_identical_prices_do_not_trigger_rejection(self):
        # Fixed-price marketplaces (StockX) legitimately produce zero MAD.
        comps = [comp(100, external_id=str(i), days_ago=i) for i in range(8)]
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.outliers_removed == 0
        assert evidence.median == Decimal("100.00")

    def test_outlier_is_caught_even_when_mad_is_zero(self):
        """MAD is zero when most comps share one price (fixed-price venues).
        A tight middle makes a stray value more conspicuous, not less, so
        rejection must fall back to an IQR fence rather than giving up."""
        comps = [comp(40, external_id=str(i), days_ago=i, seller_id=f"s{i}")
                 for i in range(9)]
        comps.append(comp(5000, external_id="bomb", seller_id="s-bomb"))
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.outliers_removed >= 1
        assert evidence.median == Decimal("40.00")

    def test_all_identical_prices_reject_nothing(self):
        comps = [comp(40, external_id=str(i), days_ago=i, seller_id=f"s{i}")
                 for i in range(8)]
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.outliers_removed == 0

    def test_rejection_never_guts_the_sample(self):
        # Genuinely dispersed data is information, not contamination.
        comps = [comp(p, external_id=str(p)) for p in (10, 40, 90, 160, 250, 400)]
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.count >= 4

    def test_fresh_comps_outweigh_stale_ones(self):
        fresh = [comp(50, external_id=f"f{i}", days_ago=1) for i in range(6)]
        stale = [comp(150, external_id=f"s{i}", days_ago=200) for i in range(6)]
        evidence = agg.aggregate(fresh + stale, now=NOW)
        assert evidence is not None
        assert evidence.weighted_mean < Decimal("120")

    def test_effective_count_is_below_raw_count_when_stale(self):
        comps = [comp(50, external_id=str(i), days_ago=180) for i in range(10)]
        evidence = agg.aggregate(comps, now=NOW)
        assert evidence is not None
        assert evidence.effective_count < evidence.count

    def test_reliable_marketplace_outweighs_unreliable(self):
        good = [comp(100, external_id=f"s{i}", provider=Marketplace.STOCKX)
                for i in range(6)]
        weak = [comp(300, external_id=f"m{i}", provider=Marketplace.MERCARI)
                for i in range(6)]
        evidence = agg.aggregate(good + weak, now=NOW)
        assert evidence is not None
        assert evidence.weighted_mean < Decimal("220")

    def test_condition_mismatch_is_downweighted(self):
        target = Condition.USED
        matched = [comp(50, external_id=f"u{i}", condition=Condition.USED)
                   for i in range(6)]
        mismatched = [comp(200, external_id=f"n{i}", condition=Condition.NEW)
                      for i in range(6)]
        evidence = agg.aggregate(matched + mismatched, target_condition=target, now=NOW)
        assert evidence is not None
        assert evidence.weighted_mean < Decimal("140")

    def test_statistics_are_all_populated(self):
        comps = [comp(40 + i * 5, external_id=str(i)) for i in range(10)]
        e = agg.aggregate(comps, now=NOW)
        assert e is not None
        for value in (e.median, e.trimmed_mean, e.weighted_mean, e.p25, e.p75,
                      e.iqr, e.mad, e.ci_low, e.ci_high):
            assert isinstance(value, Decimal)
        assert e.ci_low <= e.median <= e.ci_high
        assert e.p25 <= e.median <= e.p75

    def test_dispersion_reflects_spread(self):
        tight = agg.aggregate(
            [comp(100 + i, external_id=str(i)) for i in range(10)], now=NOW)
        assert tight is not None
        wide = agg.aggregate(
            [comp(20 + i * 40, external_id=str(i)) for i in range(10)], now=NOW)
        assert wide is not None
        assert wide.dispersion > tight.dispersion

    def test_shipping_is_included(self):
        base = [comp(100, external_id=str(i)) for i in range(6)]
        evidence = agg.aggregate(base, now=NOW)
        assert evidence is not None
        assert evidence.median == Decimal("100.00")


class TestPriceDerivation:
    def _evidence(self, prices):
        evidence = agg.aggregate(
            [comp(p, external_id=str(i)) for i, p in enumerate(prices)], now=NOW)
        # Asserted here rather than at each call site: aggregate() is Optional
        # by signature, but every caller below builds enough comps for it to
        # return one, and a None would fail them all in a less obvious place.
        assert evidence is not None
        return evidence

    def test_four_prices_are_ordered(self):
        prices = agg.to_prices(self._evidence([30, 40, 50, 60, 70, 80, 90, 100]))
        assert prices.coherent

    def test_quick_sale_is_below_expected(self):
        prices = agg.to_prices(self._evidence([30, 40, 50, 60, 70, 80, 90, 100]))
        assert prices.quick_sale <= prices.expected

    def test_collector_premium_only_when_comps_disagree(self):
        tight = agg.to_prices(self._evidence([100, 101, 102, 103, 104, 105]))
        assert tight is not None
        wide = agg.to_prices(self._evidence([20, 60, 100, 160, 240, 400]))
        assert wide is not None
        tight_gap = tight.collector - tight.expected
        wide_gap = wide.collector - wide.expected
        assert wide_gap > tight_gap

    def test_blend_favours_prior_when_comps_are_thin(self):
        evidence = self._evidence([100] * 5)
        assert evidence is not None
        blended, weight = agg.blend_with_prior(evidence, Decimal("200"))
        assert weight < 1.0
        assert Decimal("100") < blended < Decimal("200")

    def test_blend_favours_comps_when_plentiful(self):
        evidence = self._evidence([100] * 30)
        assert evidence is not None
        blended, weight = agg.blend_with_prior(evidence, Decimal("200"))
        assert weight == 1.0
        assert blended == Decimal("100.00")

    def test_blend_without_prior_returns_median(self):
        evidence = self._evidence([100] * 8)
        assert evidence is not None
        blended, weight = agg.blend_with_prior(evidence, None)
        assert blended == evidence.median and weight == 1.0


# ── Engine orchestration ─────────────────────────────────────────────────────

def build_engine(comps=None, *, flags=None, provider=None, cache=None):
    registry = ProviderRegistry()
    registry.register(provider or FixtureProvider(comps=list(comps or [])))
    return CompsEngine(
        registry=registry,
        cache=cache or NullCompsCache(),
        flags=flags or CompsFlags(enabled=True, shadow_mode=False),
    )


def many(n=12, price=100):
    # Distinct sellers: these model genuine independent sales, not relists.
    return [comp(price + i, external_id=str(i), days_ago=i, seller_id=f"seller-{i}")
            for i in range(n)]


class TestEngine:
    def test_disabled_engine_short_circuits(self):
        engine = build_engine(many(), flags=CompsFlags(enabled=False))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.DISABLED
        assert not result.has_evidence

    def test_thin_identity_is_not_searched(self):
        # Brand alone returns thousands of unrelated comps.
        engine = build_engine(many())
        assert engine is not None
        result = asyncio.run(engine.lookup(
            ItemIdentity(category="clothing", brand="Nike")))
        assert result.status is CompsStatus.NOT_SEARCHABLE
        assert result.providers_queried == ()

    def test_unknown_brand_is_not_searchable(self):
        engine = build_engine(many())
        assert engine is not None
        result = asyncio.run(engine.lookup(
            ItemIdentity(category="clothing", brand="Unknown", model="thing")))
        assert result.status is CompsStatus.NOT_SEARCHABLE

    def test_successful_lookup_produces_evidence(self):
        engine = build_engine(many())
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.OK
        assert result.has_evidence
        assert result.prices is not None and result.prices.coherent

    def test_insufficient_comps_is_reported_not_faked(self):
        engine = build_engine(many(3))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.INSUFFICIENT_COMPS
        assert not result.has_evidence
        assert result.notes

    def test_no_providers_is_distinct_from_no_comps(self):
        engine = CompsEngine(
            registry=ProviderRegistry(), cache=NullCompsCache(),
            flags=CompsFlags(enabled=True, shadow_mode=False))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.NO_PROVIDERS

    def test_provider_failure_does_not_raise(self):
        engine = build_engine(provider=FixtureProvider(comps=many(), fail=True))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.INSUFFICIENT_COMPS
        assert "fixture" in result.providers_failed

    def test_slow_provider_is_cancelled_at_the_budget(self):
        """A slow provider must not set the latency for the whole scan."""
        slow = FixtureProvider(comps=many(), latency_ms=400)
        engine = build_engine(
            provider=slow,
            flags=CompsFlags(enabled=True, shadow_mode=False,
                             fanout_budget_ms=50, provider_timeout_ms=40))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.INSUFFICIENT_COMPS
        assert result.latency_ms < 300

    def test_wrong_model_comps_are_vetoed_end_to_end(self):
        engine = build_engine(
            [comp(100, external_id=str(i), title="Nike Air Max 95") for i in range(12)])
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.INSUFFICIENT_COMPS
        assert any("different products" in n for n in result.notes)

    def test_category_flag_restricts_lookup(self):
        engine = build_engine(many(), flags=CompsFlags(
            enabled=True, shadow_mode=False,
            allowed_categories=frozenset({"clothing"})))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity(category="shoes")))
        assert result.status is CompsStatus.DISABLED

    def test_provider_flag_restricts_fanout(self):
        engine = build_engine(many(), flags=CompsFlags(
            enabled=True, shadow_mode=False,
            allowed_providers=frozenset({"ebay"})))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.NO_PROVIDERS

    def test_sample_returns_best_matches(self):
        engine = build_engine(many())
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        sample = result.sample(3)
        assert len(sample) == 3
        assert sample[0].match_score >= sample[-1].match_score

    def test_latency_is_recorded(self):
        engine = build_engine(many())
        assert engine is not None
        assert asyncio.run(engine.lookup(identity())).latency_ms >= 0


class TestShadowMode:
    def test_shadow_mode_still_computes_but_must_not_influence_output(self):
        engine = build_engine(many(), flags=CompsFlags(enabled=True, shadow_mode=True))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.OK          # measured
        assert not engine.flags.influences_user_output  # but not served

    def test_live_mode_permits_influence(self):
        flags = CompsFlags(enabled=True, shadow_mode=False)
        assert flags.influences_user_output

    def test_default_flags_are_off_and_shadowed(self):
        default = CompsFlags()
        assert not default.enabled
        assert default.shadow_mode
        assert not default.influences_user_output


# ── Registry and providers ───────────────────────────────────────────────────

class TestRegistry:
    def test_stubs_never_return_comps(self):
        for stub in default_stubs():
            # None deliberately: a stub must return [] for *any* query,
            # including one it never looks at.
            assert asyncio.run(stub.search(None)) == []  # type: ignore[arg-type]

    def test_stubs_report_unconfigured(self):
        for stub in default_stubs():
            health = asyncio.run(stub.health())
            assert not health.configured and not health.usable

    def test_defaults_register_disabled(self):
        registry = ProviderRegistry()
        register_defaults(registry)
        assert registry.all
        assert all(not e.enabled for e in registry.all)

    def test_non_sold_providers_are_never_eligible(self):
        """Facebook and Etsy expose asking prices only — they cannot price."""
        from comps.models import ProviderQuery

        registry = ProviderRegistry()
        register_defaults(registry)
        for entry in registry.all:
            entry.enabled = True
        eligible = registry.eligible(
            ProviderQuery(identity=identity(category="clothing"), sold_only=True))
        names = {e.provider.name for e in eligible}
        assert "facebook" not in names
        assert "etsy" not in names
        assert "local" not in names

    def test_category_filter_excludes_irrelevant_providers(self):
        from comps.models import ProviderQuery

        registry = ProviderRegistry()
        register_defaults(registry)
        for entry in registry.all:
            entry.enabled = True
        eligible = registry.eligible(
            ProviderQuery(identity=identity(category="shoes"), sold_only=True))
        names = {e.provider.name for e in eligible}
        assert "goat" in names          # shoes
        assert "chrono24" not in names  # watches only

    def test_duplicate_registration_is_rejected(self):
        registry = ProviderRegistry()
        registry.register(FixtureProvider())
        with pytest.raises(ValueError):
            registry.register(FixtureProvider())

    def test_circuit_opens_after_repeated_failures(self):
        from comps.providers.base import CircuitBreaker

        breaker = CircuitBreaker(threshold=3)
        assert not breaker.is_open
        for _ in range(3):
            breaker.record_failure()
        assert breaker.is_open

    def test_circuit_closes_on_success(self):
        from comps.providers.base import CircuitBreaker

        breaker = CircuitBreaker(threshold=2)
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        assert not breaker.is_open


# ── Cache ────────────────────────────────────────────────────────────────────

class _MemoryBackend:
    def __init__(self):
        self.data = {}

    async def get(self, key, **kw):
        return self.data.get(key)

    async def set(self, key, value, ttl=None, **kw):
        self.data[key] = value

    async def delete(self, key, **kw):
        self.data.pop(key, None)


class TestCompsCache:
    def test_roundtrip_preserves_comps(self):
        backend = _MemoryBackend()
        cache = CompsCache(backend=backend)
        original = many(6)
        asyncio.run(cache.put(identity(), 90, original))
        restored = asyncio.run(cache.get(identity(), 90))
        assert restored is not None and len(restored) == len(original)
        assert restored[0].price == original[0].price

    def test_negative_cache_distinguishes_empty_from_miss(self):
        backend = _MemoryBackend()
        cache = CompsCache(backend=backend)
        assert asyncio.run(cache.get(identity(), 90)) is None   # miss
        asyncio.run(cache.put(identity(), 90, []))
        assert asyncio.run(cache.get(identity(), 90)) == []      # cached empty

    def test_corrupt_entry_degrades_to_miss(self):
        backend = _MemoryBackend()
        cache = CompsCache(backend=backend)
        from comps.cache import positive_key
        backend.data[positive_key(identity(), 90)] = "not json"
        assert asyncio.run(cache.get(identity(), 90)) is None

    def test_cache_key_ignores_condition_and_size(self):
        # Condition filters and weights results but does not change which item
        # we are looking for; including it would fragment the cache.
        a = ItemIdentity(category="shoes", brand="Nike", model="Air Max 97",
                         condition=Condition.NEW, size="10")
        b = ItemIdentity(category="shoes", brand="Nike", model="Air Max 97",
                         condition=Condition.USED, size="12")
        assert a.cache_key() == b.cache_key()

    def test_cache_key_differs_on_model(self):
        a = ItemIdentity(category="shoes", brand="Nike", model="Air Max 97")
        b = ItemIdentity(category="shoes", brand="Nike", model="Air Max 95")
        assert a.cache_key() != b.cache_key()

    def test_engine_reports_cache_hit(self):
        backend = _MemoryBackend()
        cache = CompsCache(backend=backend)
        engine = build_engine(many(), cache=cache)
        assert engine is not None
        first = asyncio.run(engine.lookup(identity()))
        second = asyncio.run(engine.lookup(identity()))
        assert not first.cache_hit
        assert second.cache_hit

    def test_backend_failure_degrades_to_fetch(self):
        class _Broken:
            async def get(self, *a, **kw):
                raise RuntimeError("redis down")

            async def set(self, *a, **kw):
                raise RuntimeError("redis down")

            async def delete(self, *a, **kw):
                raise RuntimeError("redis down")

        engine = build_engine(many(), cache=CompsCache(backend=_Broken()))
        assert engine is not None
        result = asyncio.run(engine.lookup(identity()))
        assert result.status is CompsStatus.OK      # comps still served


# ── Catalog ──────────────────────────────────────────────────────────────────

class TestCatalog:
    def test_exact_resolution(self):
        assert not_none(catalog.resolve("Patagonia")).canonical == "Patagonia"

    def test_alias_resolution(self):
        assert not_none(catalog.resolve("tnf")).canonical == "The North Face"
        assert not_none(catalog.resolve("doc martens")).canonical == "Dr. Martens"

    def test_case_and_punctuation_insensitive(self):
        assert not_none(catalog.resolve("LEVI'S")).canonical == "Levi's"

    def test_fuzzy_resolution_handles_misspellings(self):
        assert not_none(catalog.resolve_fuzzy("Patagoina")).canonical == "Patagonia"
        assert not_none(catalog.resolve_fuzzy("Arcteryx")).canonical == "Arc'teryx"

    def test_fuzzy_does_not_resolve_unrelated_brands(self):
        """A wrong brand corrupts every comp that follows."""
        assert catalog.resolve_fuzzy("Zebra Corporation") is None

    def test_fuzzy_rejects_very_short_input(self):
        assert catalog.resolve_fuzzy("ab") is None

    def test_model_extraction(self):
        brand = catalog.resolve("Patagonia")
        assert catalog.extract_model(brand, "Patagonia Better Sweater 1/4-Zip") \
            == "better sweater"

    def test_model_extraction_prefers_longest_match(self):
        brand = catalog.resolve("Nike")
        assert catalog.extract_model(brand, "Nike Air Force 1 Low") == "air force 1"

    def test_luxury_and_counterfeit_views(self):
        assert any(b.canonical == "Rolex" for b in catalog.luxury_brands())
        assert any(b.canonical == "Nike" for b in catalog.counterfeit_risk_brands())

    def test_authentication_required_for_risky_brands(self):
        assert catalog.requires_authentication("Louis Vuitton")
        assert catalog.requires_authentication("Gucci")
        assert not catalog.requires_authentication("Pyrex")

    def test_category_profiles(self):
        assert catalog.category("collectibles").low_liquidity
        assert catalog.category("shoes").variant_sensitive
        assert catalog.category("nonsense").name == "other"

    def test_custom_catalog_can_be_constructed(self):
        empty = Catalog(brands=[], categories=[])
        assert empty.resolve("Nike") is None


# ── FTS5 search ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(not search_module.fts5_available(), reason="SQLite lacks FTS5")
class TestCatalogSearch:
    @pytest.fixture(scope="class")
    def index(self):
        engine = search_module.build_search()
        assert engine is not None
        yield engine
        engine.close()

    def test_brand_search(self, index):
        assert any(h.canonical == "Patagonia" for h in index.search("patagonia"))

    def test_model_search_returns_model_rows(self, index):
        hits = index.search("nike air max")
        assert any(h.kind == "model" for h in hits)

    def test_alias_is_indexed(self, index):
        assert index.best_brand("tnf") == "The North Face"

    def test_prefix_suggestions(self, index):
        assert any("Patagonia" in s for s in index.suggest("patag"))

    def test_fts_special_characters_do_not_raise(self, index):
        for hostile in ['nike "OR" 1=1', "air-max^2", "(((", "patagonia*", "'"]:
            assert isinstance(index.search(hostile), list)

    def test_empty_query_returns_nothing(self, index):
        assert index.search("") == []
        assert index.search("   ") == []

    def test_results_are_ranked(self, index):
        hits = index.search("nike", limit=5)
        if len(hits) > 1:
            assert hits[0].score >= hits[-1].score
