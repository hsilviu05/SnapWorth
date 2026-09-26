"""Comps shadow mode (#39): run beside the scan, change nothing the user sees."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comps.cache import NullCompsCache  # noqa: E402
from comps.engine import CompsEngine  # noqa: E402
from comps.flags import CompsFlags  # noqa: E402
from comps.models import CompsStatus, Condition  # noqa: E402
from comps.providers.base import ProviderRegistry  # noqa: E402
from comps.providers.stubs import FixtureProvider  # noqa: E402
from comps.shadow import ShadowRunner, identity_from_valuation, report  # noqa: E402
from tests.images import padded_image_bytes  # noqa: E402
from tests.test_comps_engine import identity, many  # noqa: E402


@dataclass
class Identified:
    """The fields of `valuation.Valuation` the shadow reads."""

    category: str = "Shoes"
    brand: str = "Nike"
    model: str | None = "Air Max 97"
    variant: str | None = None
    size: str | None = "10"
    material: str | None = None
    condition_grade: str | None = "likeNew"


def valuation(**kw) -> Identified:
    return replace(Identified(), **kw)


def engine(*, enabled=True, provider=None):
    registry = ProviderRegistry()
    registry.register(provider or FixtureProvider(comps=many()))
    return CompsEngine(registry=registry, cache=NullCompsCache(),
                       flags=CompsFlags(enabled=enabled, shadow_mode=True))


class TestIdentity:
    def test_maps_the_model_identification(self):
        ident = identity_from_valuation(valuation())
        assert ident.category == "shoes"
        assert (ident.brand, ident.model, ident.size) == ("Nike", "Air Max 97", "10")
        assert ident.condition is Condition.LIKE_NEW
        assert ident.year is None

    def test_unknown_condition_defaults_to_the_thrift_baseline(self):
        assert identity_from_valuation(
            valuation(condition_grade=None)).condition is Condition.GOOD

    def test_brand_alone_is_not_searchable(self):
        assert not identity_from_valuation(valuation(model=None)).is_searchable


class TestReport:
    def test_evidence_is_compared_with_the_model(self):
        result = asyncio.run(engine().lookup(identity()))
        assert result.status is CompsStatus.OK
        fields = report(result, model_low=90, model_high=120, model_expected=100)
        assert fields["status"] == "ok"
        assert fields["comp_count"] == len(result.comps)
        assert 0 < fields["match_confidence"] <= 1
        assert fields["comps_low"] <= fields["comps_expected"] <= fields["comps_high"]
        assert fields["ranges_overlap"] is True
        assert fields["price_ratio"] == pytest.approx(
            fields["comps_expected"] / 100, abs=1e-3)
        assert set(fields["provider_latency_ms"]) == {"fixture"}

    def test_no_evidence_reports_no_comps_prices(self):
        """A lookup without evidence must not produce a number to compare."""
        result = asyncio.run(engine(provider=FixtureProvider(comps=[])).lookup(identity()))
        fields = report(result, model_low=90, model_high=120, model_expected=100)
        assert fields["status"] == "insufficient_comps"
        for key in ("comps_low", "comps_high", "comps_expected", "price_ratio"):
            assert key not in fields

    def test_disagreement_is_visible(self):
        result = asyncio.run(engine().lookup(identity()))
        fields = report(result, model_low=400, model_high=600, model_expected=500)
        assert fields["ranges_overlap"] is False
        assert fields["price_ratio"] < 0.5


class TestRunner:
    def test_disabled_engine_is_never_called(self):
        """COMPS_ENABLED=false — production — means no task at all."""
        runner = ShadowRunner(engine(enabled=False))
        runner.engine.lookup = AsyncMock()

        async def go():
            return runner.schedule(valuation(), model_low=1, model_high=2,
                                   model_expected=1.5)

        assert asyncio.run(go()) is None
        runner.engine.lookup.assert_not_called()

    def test_enabled_lookup_is_logged_and_counted(self, caplog):
        import metrics
        runner = ShadowRunner(engine())
        before = metrics.comps_shadow_lookups.value(status="ok")

        async def go():
            task = runner.schedule(valuation(condition_grade="good"),
                                   model_low=90, model_high=120, model_expected=100)
            assert task is not None and runner.pending == 1
            await task
            return runner.pending

        with caplog.at_level("INFO", logger="snapworth.comps.shadow"):
            assert asyncio.run(go()) == 0
        assert metrics.comps_shadow_lookups.value(status="ok") == before + 1
        record = next(r for r in caplog.records if r.getMessage() == "comps shadow")
        assert getattr(record, "status") == "ok"
        assert getattr(record, "comp_count") > 0

    def test_an_engine_failure_is_swallowed(self):
        import metrics
        runner = ShadowRunner(engine())
        runner.engine.lookup = AsyncMock(side_effect=RuntimeError("boom"))
        before = metrics.comps_shadow_lookups.value(status="error")

        async def go():
            task = runner.schedule(valuation(), model_low=1, model_high=2,
                                   model_expected=None)
            assert task is not None
            await task

        asyncio.run(go())
        assert metrics.comps_shadow_lookups.value(status="error") == before + 1

    def test_drain_cancels_a_slow_lookup(self):
        runner = ShadowRunner(engine(provider=FixtureProvider(comps=many(),
                                                             latency_ms=5000)))

        async def go():
            runner.schedule(valuation(), model_low=1, model_high=2, model_expected=None)
            await runner.drain(timeout=0.05)
            await asyncio.sleep(0)
            return runner.pending

        assert asyncio.run(go()) == 0


class TestScanPath:
    """The wiring in `main._analyse`: scheduled for users, and inert."""

    @staticmethod
    def _model_reply():
        response = MagicMock()
        response.text = json.dumps({
            "item_name": "Nike Air Max 97", "brand": "Nike", "category": "shoes",
            "model": "Air Max 97", "condition_notes": "Good",
            "est_value_low_usd": 60.0, "est_value_high_usd": 110.0,
            "listing_title": "Nike Air Max 97", "listing_description": "Worn twice.",
        })
        return response

    def _analyse(self, *, count):
        import main
        calls: list[dict] = []

        def fake_schedule(val, **kw):
            calls.append({"brand": val.brand, "model": val.model, **kw})
            return None

        with patch("main._model") as model, \
                patch.object(main._comps_shadow, "schedule", side_effect=fake_schedule):
            model.generate_content_async = AsyncMock(return_value=self._model_reply())
            response, _ = asyncio.run(main._analyse(
                padded_image_bytes("JPEG", 1024), "image/jpeg",
                subject="dev", device_short="dev", count=count))
        return response, calls

    def test_a_user_scan_schedules_a_shadow_lookup(self):
        response, calls = self._analyse(count=True)
        assert len(calls) == 1
        assert calls[0]["brand"] == "Nike" and calls[0]["model"] == "Air Max 97"
        assert calls[0]["model_low"] == response.est_value_low_usd
        assert calls[0]["model_high"] == response.est_value_high_usd

    def test_an_operator_test_scan_does_not(self):
        _, calls = self._analyse(count=False)
        assert calls == []

    def test_the_response_still_says_model(self):
        response, _ = self._analyse(count=True)
        assert response.valuation_source == "model"

    def test_enabled_shadow_leaves_the_response_untouched(self, monkeypatch):
        """With a real engine that finds evidence, the user still sees the model."""
        import main
        monkeypatch.setattr(main, "_comps_shadow", ShadowRunner(engine()))

        async def go():
            with patch("main._model") as model:
                model.generate_content_async = AsyncMock(return_value=self._model_reply())
                response, _ = await main._analyse(
                    padded_image_bytes("JPEG", 1024), "image/jpeg",
                    subject="dev", device_short="dev")
            await main._comps_shadow.drain(timeout=2)
            return response

        response = asyncio.run(go())
        assert response.valuation_source == "model"
        assert (response.est_value_low_usd, response.est_value_high_usd) == (60.0, 110.0)
