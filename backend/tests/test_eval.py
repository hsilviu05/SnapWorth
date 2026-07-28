"""Tests for the evaluation harness.

A benchmark you cannot test is a benchmark you will not trust — if the metrics
are silently wrong, every prompt decision made from them is wrong too. All of
these run without a model, an API key, or a network.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import dataset as dataset_module  # noqa: E402
from eval import metrics  # noqa: E402
from eval.runner import Prediction, evaluate, evaluate_consistency  # noqa: E402


# ── Point accuracy ───────────────────────────────────────────────────────────

class TestAccuracyMetrics:
    def test_ape_basic(self):
        assert metrics.ape(110, 100) == pytest.approx(10.0)
        assert metrics.ape(90, 100) == pytest.approx(10.0)

    def test_ape_undefined_on_zero_actual(self):
        assert metrics.ape(50, 0) is None

    def test_perfect_predictions_score_zero_error(self):
        pairs = [(50, 50), (100, 100)]
        assert metrics.mdape(pairs) == 0.0
        assert metrics.mape(pairs) == 0.0

    def test_mdape_is_robust_to_a_single_wild_outlier(self):
        """The reason MdAPE is the headline and MAPE is only reported.

        Thrift data is mostly $5-$60 with a long tail, so one $200-predicted
        $5-sold item would otherwise dominate the whole benchmark.
        """
        pairs = [(50, 50)] * 9 + [(200, 5)]
        assert metrics.mdape(pairs) == 0.0          # unmoved
        assert metrics.mape(pairs) > 300            # swamped

    def test_within_tolerance_counts_correctly(self):
        pairs = [(100, 100), (120, 100), (200, 100)]
        assert metrics.within_tolerance(pairs, 25.0) == pytest.approx(2 / 3)

    def test_empty_input_returns_none_not_zero(self):
        # Zero would read as "perfect accuracy" on an empty run.
        assert metrics.mdape([]) is None
        assert metrics.mape([]) is None
        assert metrics.within_tolerance([]) is None


# ── Range quality ────────────────────────────────────────────────────────────

class TestRangeMetrics:
    def test_coverage_counts_inclusive_bounds(self):
        triples = [(10, 50, 30), (10, 50, 10), (10, 50, 50), (10, 50, 80)]
        assert metrics.range_coverage(triples) == 0.75

    def test_inverted_ranges_are_excluded(self):
        assert metrics.range_coverage([(50, 10, 30)]) is None

    def test_width_ratio_penalises_useless_wide_ranges(self):
        tight = metrics.mean_range_width([(40, 60, 50)])
        wide = metrics.mean_range_width([(10, 500, 50)])
        assert wide > tight
        # Coverage alone is gameable by widening; this is why both are reported.
        assert metrics.range_coverage([(10, 500, 50)]) == 1.0


# ── Calibration ──────────────────────────────────────────────────────────────

class TestCalibration:
    def test_perfectly_calibrated_system_has_low_ece(self):
        # 90-confidence predictions that are all accurate.
        scored = [(95, 100, 100)] * 10
        assert metrics.calibration(scored).ece < 0.15

    def test_overconfident_system_is_detected(self):
        """The exact failure v1 had: high confidence, poor accuracy."""
        scored = [(95, 500, 100)] * 10          # claims ~95%, right 0% of the time
        assert metrics.calibration(scored).ece > 0.8

    def test_underconfident_system_is_also_detected(self):
        scored = [(5, 100, 100)] * 10           # claims ~5%, right 100% of the time
        assert metrics.calibration(scored).ece > 0.8

    def test_buckets_report_claimed_vs_actual(self):
        scored = [(95, 100, 100)] * 5 + [(15, 900, 100)] * 5
        table = metrics.calibration(scored).as_table()
        assert len(table) == 2
        high = next(b for b in table if b["range"] == "80-100")
        assert high["actual"] == 1.0

    def test_empty_scored_set_is_zero_ece(self):
        assert metrics.calibration([]).ece == 0.0


# ── Consistency ──────────────────────────────────────────────────────────────

class TestConsistency:
    def test_identical_repeats_have_zero_variance(self):
        result = metrics.consistency([[50.0, 50.0, 50.0]])
        assert result["mean_cv"] == 0.0

    def test_varying_repeats_are_flagged(self):
        """What a default temperature of 1.0 produced: the same photo,
        materially different prices."""
        result = metrics.consistency([[20.0, 60.0, 100.0]])
        assert result["mean_cv"] > 0.15

    def test_single_run_items_are_skipped(self):
        assert metrics.consistency([[50.0]])["n"] == 0

    def test_zero_prices_are_ignored(self):
        assert metrics.consistency([[0.0, 0.0]])["n"] == 0


# ── Hallucination proxies ────────────────────────────────────────────────────

class TestHallucinationRate:
    def test_model_name_without_evidence_is_flagged(self):
        result = metrics.hallucination_rate([
            {"model_name": "Better Sweater", "visual_evidence": []},
        ])
        assert result["rate"] == 1.0
        assert "unsupported_model" in result["reasons"]

    def test_model_name_with_evidence_is_clean(self):
        result = metrics.hallucination_rate([
            {"model_name": "Better Sweater", "visual_evidence": ["chest wordmark"]},
        ])
        assert result["rate"] == 0.0

    def test_brand_mismatch_is_flagged(self):
        result = metrics.hallucination_rate([
            {"brand": "Nike", "expected_brand": "Adidas",
             "visual_evidence": ["logo on side"]},
        ])
        assert "brand_mismatch" in result["reasons"]

    def test_honest_unknown_brand_is_not_a_hallucination(self):
        # Returning "Unknown" is the desired behaviour, not a failure.
        result = metrics.hallucination_rate([
            {"brand": "Unknown", "expected_brand": "Adidas",
             "visual_evidence": ["no visible branding"]},
        ])
        assert result["rate"] == 0.0

    def test_certain_without_evidence_is_flagged(self):
        result = metrics.hallucination_rate([
            {"identification_certainty": "certain", "visual_evidence": []},
        ])
        assert "evidence_missing" in result["reasons"]

    def test_empty_records_return_none_not_zero(self):
        assert metrics.hallucination_rate([])["rate"] is None


# ── Latency ──────────────────────────────────────────────────────────────────

class TestLatency:
    def test_percentiles_ordered(self):
        summary = metrics.latency_summary([float(i) for i in range(1, 101)])
        assert summary["p50"] <= summary["p95"] <= summary["p99"]

    def test_empty_is_none(self):
        assert metrics.latency_summary([])["p50"] is None


# ── Dataset ──────────────────────────────────────────────────────────────────

class TestDataset:
    def _write(self, tmp_path, lines):
        path = tmp_path / "bench.jsonl"
        path.write_text("\n".join(lines))
        return path

    def test_loads_valid_records(self, tmp_path):
        path = self._write(tmp_path, [json.dumps({
            "id": "a1", "image_path": "x.jpg",
            "actual_sale_price_usd": 50, "category": "clothing"})])
        items = dataset_module.load(path)
        assert len(items) == 1 and items[0].id == "a1"

    def test_skips_malformed_lines_without_failing_the_run(self, tmp_path):
        path = self._write(tmp_path, [
            "{not json",
            json.dumps({"id": "a1", "image_path": "x.jpg",
                        "actual_sale_price_usd": 50, "category": "clothing"}),
        ])
        assert len(dataset_module.load(path)) == 1

    def test_rejects_records_missing_required_fields(self, tmp_path):
        path = self._write(tmp_path, [json.dumps({"id": "a1", "category": "clothing"})])
        assert dataset_module.load(path) == []

    def test_rejects_negative_and_non_numeric_prices(self, tmp_path):
        path = self._write(tmp_path, [
            json.dumps({"id": "a", "image_path": "x", "actual_sale_price_usd": -5,
                        "category": "clothing"}),
            json.dumps({"id": "b", "image_path": "x", "actual_sale_price_usd": "free",
                        "category": "clothing"}),
        ])
        assert dataset_module.load(path) == []

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        path = self._write(tmp_path, [
            "# a comment", "",
            json.dumps({"id": "a1", "image_path": "x.jpg",
                        "actual_sale_price_usd": 50, "category": "clothing"}),
        ])
        assert len(dataset_module.load(path)) == 1

    def test_synthetic_records_are_not_scoreable(self):
        item = dataset_module.BenchmarkItem(
            id="s", image_path="x", actual_sale_price_usd=10,
            category="clothing", source="synthetic")
        assert not item.is_scoreable

    def test_negative_controls_are_not_scoreable(self):
        item = dataset_module.BenchmarkItem(
            id="n", image_path="x", actual_sale_price_usd=0,
            category="other", not_resalable=True)
        assert not item.is_scoreable

    def test_coverage_report_identifies_gaps(self):
        items = [dataset_module.BenchmarkItem(
            id=f"c{i}", image_path="x", actual_sale_price_usd=20,
            category="clothing") for i in range(5)]
        report = dataset_module.coverage_report(items)
        assert report["scoreable"] == 5
        assert not report["meets_minimum"]
        assert report["gaps"]["clothing"] == 145

    def test_shipped_sample_dataset_parses(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "eval", "data", "sample.jsonl")
        items = dataset_module.load(path)
        assert items, "sample file should parse"
        assert any(i.not_resalable for i in items), "sample must include a negative control"

    def test_shipped_sample_contains_no_scoreable_records(self):
        """The sample file is a format reference containing invented prices.

        Previously these were labelled `personal_sale` and `ebay_sold`, so they
        were scoreable and would have contributed fabricated numbers to a
        reported metric. Every record is now synthetic and excluded.
        """
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "eval", "data", "sample.jsonl")
        items = dataset_module.load(path)
        assert all(not i.is_scoreable for i in items)
        assert all(i.source == "synthetic" for i in items)


# ── End-to-end report assembly ───────────────────────────────────────────────

class TestEvaluate:
    def _prediction(self, **kw):
        base = dict(item_id="a", category="clothing", expected_price=50.0,
                    predicted_expected=52.0, predicted_low=40.0,
                    predicted_high=70.0, confidence_score=80,
                    brand="Patagonia", visual_evidence=["wordmark"],
                    latency_ms=1200.0)
        base.update(kw)
        return Prediction(**base)

    def test_report_has_every_metric_section(self):
        report = evaluate([self._prediction()])
        for section in ("accuracy", "range", "calibration",
                        "hallucination", "latency_ms", "by_category"):
            assert section in report

    def test_failed_predictions_are_counted_not_scored(self):
        report = evaluate([self._prediction(), self._prediction(error="timeout")])
        assert report["n_total"] == 2
        assert report["n_scored"] == 1
        assert report["n_failed"] == 1

    def test_zero_prediction_is_treated_as_failure(self):
        assert evaluate([self._prediction(predicted_expected=0.0)])["n_scored"] == 0

    def test_per_category_breakdown(self):
        report = evaluate([
            self._prediction(category="clothing"),
            self._prediction(category="shoes", expected_price=90, predicted_expected=95),
        ])
        assert set(report["by_category"]) == {"clothing", "shoes"}

    def test_all_failed_run_does_not_raise(self):
        report = evaluate([self._prediction(error="x")])
        assert report["accuracy"]["mdape"] is None

    def test_consistency_wiring(self):
        result = evaluate_consistency({"a": [50.0, 51.0], "b": [10.0, 90.0]})
        assert result["n"] == 2
        assert result["worst_cv"] > result["mean_cv"] or result["n"] == 1
