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

from tests.conftest import not_none
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import dataset as dataset_module  # noqa: E402
from eval import metrics  # noqa: E402
from eval import schema  # noqa: E402
from eval.runner import Prediction, evaluate, evaluate_consistency  # noqa: E402


# ── The harness must measure the shipping pipeline ───────────────────────────
# The design note at the top of eval/runner.py claims it "talks to the same
# valuation/confidence modules the API uses, so it measures the shipping
# pipeline rather than a parallel reimplementation that can silently drift."
# It had drifted anyway, in the step between normalise() and confidence: a
# local clamp that never rebuilt the price ladder and substituted defaults for
# an empty response. These tests hold that claim to its word, because a
# benchmark measuring a pipeline no user hits is worse than no benchmark — it
# is trusted.

class TestHarnessMatchesProduction:
    def _source(self, name: str) -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, name), encoding="utf-8") as handle:
            return handle.read()

    def test_both_callers_go_through_the_shared_clamp(self):
        for name in ("main.py", "eval/runner.py"):
            source = self._source(name)
            assert "apply_price_bounds" in source, (
                f"{name} must clamp through valuation.apply_price_bounds")
            assert "clamp_valuation" not in source, (
                f"{name} calls promptsafety.clamp_valuation directly — that is "
                "the second copy of the rule, and how the two drifted apart")

    def test_both_callers_score_completeness_on_the_normalised_object(self):
        for name in ("main.py", "eval/runner.py"):
            source = self._source(name)
            assert "count_present_fields(val)" in source, (
                f"{name} must score the normalised Valuation, not the raw "
                "payload — fields normalise discards are not evidence the "
                "model answered")

    @staticmethod
    def _predict(reply: dict, tmp_path) -> Prediction:
        """One item through `_predict_one`, the model answering `reply`."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from eval.runner import EvalItem, _predict_one
        from tests.images import image_bytes

        (tmp_path / "item.jpg").write_bytes(image_bytes("JPEG"))
        # The runner's own item type, not a stand-in: its fields changed when
        # the runner learned the gold-v2 format, and a namespace with the old
        # names only failed deep inside `_predict_one`.
        item = EvalItem(id="i1", category="clothing", image_path="item.jpg",
                        expected_price=50.0, expected_brand="Patagonia")
        response = MagicMock()
        response.text = json.dumps(reply)
        model = MagicMock()
        model.generate_content_async = AsyncMock(return_value=response)
        return asyncio.run(_predict_one(model, item, "prompt", "v2", tmp_path))

    def test_a_reply_scan_refuses_is_not_scored(self, tmp_path):
        """`/scan` 502s a reply carrying one price (`prices.servable`) and 422s
        a deliberate zero; the harness scored both as predictions."""
        import valuation as valuation_module
        from tests.test_ai_pipeline import V2_PAYLOAD

        unpriced = {k: v for k, v in V2_PAYLOAD.items()
                    if k not in valuation_module.V2_PRICE_FIELDS + valuation_module.V1_PRICE_FIELDS}

        one_price = self._predict({**unpriced, "expected_price_usd": 58}, tmp_path)
        assert one_price.error == "no_price"
        assert not one_price.ok and one_price.confidence_score == 0

        zero = dict.fromkeys(valuation_module.V2_PRICE_FIELDS, 0)
        declined = self._predict({**unpriced, **zero}, tmp_path)
        assert declined.error == "not_resalable" and not declined.ok

        served = self._predict(V2_PAYLOAD, tmp_path)
        assert served.error is None and served.ok


# ── Point accuracy ───────────────────────────────────────────────────────────

class TestAccuracyMetrics:
    def test_ape_basic(self):
        assert metrics.ape(110, 100) == pytest.approx(10.0)
        assert metrics.ape(90, 100) == pytest.approx(10.0)

    def test_ape_undefined_on_zero_actual(self):
        assert metrics.ape(50, 0) is None

    def test_perfect_predictions_score_zero_error(self):
        pairs = [(50.0, 50.0), (100.0, 100.0)]
        assert metrics.mdape(pairs) == 0.0
        assert metrics.mape(pairs) == 0.0

    def test_mdape_is_robust_to_a_single_wild_outlier(self):
        """The reason MdAPE is the headline and MAPE is only reported.

        Thrift data is mostly $5-$60 with a long tail, so one $200-predicted
        $5-sold item would otherwise dominate the whole benchmark.
        """
        pairs = [(50.0, 50.0)] * 9 + [(200.0, 5.0)]
        assert metrics.mdape(pairs) == 0.0          # unmoved
        assert not_none(metrics.mape(pairs)) > 300            # swamped

    def test_within_tolerance_counts_correctly(self):
        pairs = [(100.0, 100.0), (120.0, 100.0), (200.0, 100.0)]
        assert metrics.within_tolerance(pairs, 25.0) == pytest.approx(2 / 3)

    def test_empty_input_returns_none_not_zero(self):
        # Zero would read as "perfect accuracy" on an empty run.
        assert metrics.mdape([]) is None
        assert metrics.mape([]) is None
        assert metrics.within_tolerance([]) is None


# ── Range quality ────────────────────────────────────────────────────────────

class TestRangeMetrics:
    def test_coverage_counts_inclusive_bounds(self):
        triples = [(10.0, 50.0, 30.0), (10.0, 50.0, 10.0), (10.0, 50.0, 50.0), (10.0, 50.0, 80.0)]
        assert metrics.range_coverage(triples) == 0.75

    def test_inverted_ranges_are_excluded(self):
        assert metrics.range_coverage([(50, 10, 30)]) is None

    def test_width_ratio_penalises_useless_wide_ranges(self):
        tight = not_none(metrics.mean_range_width([(40, 60, 50)]))
        wide = not_none(metrics.mean_range_width([(10.0, 500.0, 50.0)]))
        assert wide > tight
        # Coverage alone is gameable by widening; this is why both are reported.
        assert metrics.range_coverage([(10, 500, 50)]) == 1.0


# ── Calibration ──────────────────────────────────────────────────────────────

class TestCalibration:
    def test_perfectly_calibrated_system_has_low_ece(self):
        # 90-confidence predictions that are all accurate.
        scored = [(95, 100.0, 100.0)] * 10
        assert not_none(metrics.calibration(scored)).ece < 0.15

    def test_overconfident_system_is_detected(self):
        """The exact failure v1 had: high confidence, poor accuracy."""
        scored = [(95, 500.0, 100.0)] * 10          # claims ~95%, right 0% of the time
        assert not_none(metrics.calibration(scored)).ece > 0.8

    def test_underconfident_system_is_also_detected(self):
        scored = [(5, 100.0, 100.0)] * 10           # claims ~5%, right 100% of the time
        assert not_none(metrics.calibration(scored)).ece > 0.8

    def test_buckets_report_claimed_vs_actual(self):
        scored = [(95, 100.0, 100.0)] * 5 + [(15, 900.0, 100.0)] * 5
        table = not_none(metrics.calibration(scored)).as_table()
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
        # dict[str, Any]: an unannotated dict() literal infers a union of its value
        # types, and splatting that reports one error per constructor parameter.
        base: dict[str, Any] = dict(item_id="a", category="clothing", expected_price=50.0,
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


# ── What the runner loads ────────────────────────────────────────────────────
# CI runs the runner on eval/data/gold.jsonl, which is written in the gold-v2
# schema. The runner only knew v1, rejected every gold record as "missing
# required fields", and ended in "no scoreable items".

from pathlib import Path  # noqa: E402

from eval import runner  # noqa: E402


def _gold(**overrides) -> dict:
    record = {"id": "g1", "images": [{"path": "images/g1.jpg", "is_primary": True}],
              "actual_sale_price": 42.0, "currency": "USD", "category": "clothing",
              "brand": "Patagonia", "label_confidence": "certain",
              "evidence_note": "own sale, receipt held", "review_state": "approved",
              "reviewed_by": "operator", "difficulty": "typical", "region": "US"}
    record.update(overrides)
    return record


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


class TestRunnerLoading:
    def test_gold_records_load_through_the_gold_schema(self, tmp_path):
        path = _write_jsonl(tmp_path / "gold.jsonl", [_gold()])
        items, excluded = runner.load_items(path)
        assert excluded == {}
        split = not_none(schema.item_from_dict(_gold())).assigned_split().value
        assert items == [runner.EvalItem("g1", "clothing", "images/g1.jpg", 42.0, "Patagonia",
                                         split, region="US")]

    def test_only_headline_usd_records_are_scored_and_every_exclusion_is_named(self, tmp_path):
        path = _write_jsonl(tmp_path / "gold.jsonl", [
            _gold(id="ok"),
            _gold(id="draft", review_state="draft"),
            _gold(id="medium", label_confidence="medium"),
            _gold(id="gbp", currency="GBP"),
            _gold(id="neg", difficulty="negative_control"),
        ])
        items, excluded = runner.load_items(path)
        assert [i.id for i in items] == ["ok"]
        assert sum(excluded.values()) == 4
        assert any("GBP" in reason for reason in excluded)
        assert any("medium/low" in reason for reason in excluded)

    def test_the_shipped_gold_template_is_read_as_gold_and_scores_nothing(self):
        path = Path(__file__).resolve().parents[1] / "eval/data/gold.template.jsonl"
        items, excluded = runner.load_items(path)
        assert items == []
        # Excluded as drafts, i.e. understood — not dropped as malformed.
        assert sum(excluded.values()) == 3
        assert all(reason.startswith("not scoreable") for reason in excluded)

    def test_v1_files_still_load(self, tmp_path):
        path = _write_jsonl(tmp_path / "v1.jsonl", [
            {"id": "a", "image_path": "a.jpg", "actual_sale_price_usd": 10.0,
             "category": "shoes", "source": "personal_sale"},
            {"id": "b", "image_path": "b.jpg", "actual_sale_price_usd": 10.0,
             "category": "shoes", "source": "synthetic"},
        ])
        items, excluded = runner.load_items(path)
        assert [i.id for i in items] == ["a"]
        assert items[0].expected_price == 10.0
        assert sum(excluded.values()) == 1

    def test_photos_are_jpegs_only_and_unlabelled(self, tmp_path):
        for name in ("b.jpg", "a.JPEG", "c.png", "notes.txt"):
            (tmp_path / name).write_bytes(b"x")
        items = runner.load_photos(tmp_path)
        assert [i.image_path for i in items] == ["a.JPEG", "b.jpg"]
        assert all(i.expected_price is None for i in items)

    def test_arm_syntax(self):
        assert runner.parse_arm("v2") == ("v2", None)
        assert runner.parse_arm("v2@512") == ("v2", 512)
        assert runner.parse_arm("v1@0") == ("v1", 0)


# ── Measuring without labels ─────────────────────────────────────────────────
# Consistency, latency and tokens need no sale price, and neither does "how far
# does this change move prices". `--repeats` refused to run without scoreable
# items, so none of it could be measured on real photos — including the
# thinking-budget experiment aiconfig.THINKING_BUDGET is waiting on.

def _fake_run_live(price_by_budget: dict):
    calls: list[tuple[str, int | None]] = []

    async def fake(items, version, root, concurrency=4, thinking_budget=None):
        calls.append((version, thinking_budget))
        price = price_by_budget[thinking_budget]
        return [runner.Prediction(
            item_id=i.id, category=i.category, expected_price=i.expected_price,
            predicted_expected=price, predicted_low=price * 0.8,
            predicted_high=price * 1.2, confidence_score=70, latency_ms=900.0,
            # Separate counts, as Gemini reports them: the answer is smaller
            # than the reasoning, and neither contains the other.
            output_tokens=850, thoughts_tokens=1400 if thinking_budget is None else 500)
            for i in items]
    return fake, calls


class TestUnlabelledRuns:
    def _photos(self, tmp_path: Path) -> Path:
        folder = tmp_path / "photos"
        folder.mkdir()
        for name in ("one.jpg", "two.jpg", "three.jpg"):
            (folder / name).write_bytes(b"not-a-real-jpeg")
        return folder

    def test_compare_arms_on_photos_reports_shift_not_accuracy(self, tmp_path, monkeypatch):
        fake, calls = _fake_run_live({None: 100.0, 512: 120.0})
        monkeypatch.setattr(runner, "run_live", fake)
        out = tmp_path / "run.json"

        code = runner.main(["--photos", str(self._photos(tmp_path)), "--repeats", "2",
                            "--compare", "v2", "v2@512", "--json-out", str(out)])

        assert code == 0
        assert calls == [("v2", None), ("v2", None), ("v2", 512), ("v2", 512)]
        result = json.loads(out.read_text())
        assert result["labelled"] is False
        assert result["price_shift"]["n"] == 3
        assert result["price_shift"]["median_shift_pct"] == pytest.approx(20.0)
        assert result["price_shift"]["moved_over_10pct"] == pytest.approx(1.0)
        for arm in ("v2", "v2@512"):
            measured = result["arms"][arm]["metrics"]
            assert {"repeatability", "consistency_mean_cv", "latency_p95",
                    "thoughts_tokens_median"} <= set(measured)
            # No sale prices, so no accuracy — absent, not zero.
            assert not {"mdape", "within_25pct", "bias", "calibration_ece",
                        "hallucination_rate"} & set(measured)
        assert result["arms"]["v2@512"]["metrics"]["thoughts_tokens_median"]["value"] == 500
        billed = {arm: result["arms"][arm]["metrics"]["billed_output_tokens_median"]["value"]
                  for arm in ("v2", "v2@512")}
        assert billed == {"v2": 2250, "v2@512": 1350}

    def test_a_single_labelled_run_writes_the_gate_shape(self, tmp_path, monkeypatch):
        fake, _ = _fake_run_live({None: 50.0})
        monkeypatch.setattr(runner, "run_live", fake)
        dataset = _write_jsonl(tmp_path / "gold.jsonl", [_gold(id="a"), _gold(id="b")])
        out = tmp_path / "run.json"

        assert runner.main(["--dataset", str(dataset), "--json-out", str(out)]) == 0

        result = json.loads(out.read_text())
        assert result["labelled"] is True
        assert result["metrics"]["mdape"]["provenance"] == "measured"
        assert result["metrics"]["mdape"]["sample_size"] == 2
        assert result["metrics"]["within_25pct"]["value"] == pytest.approx(100.0)

    def test_an_empty_gold_set_still_fails_loudly(self, tmp_path):
        dataset = _write_jsonl(tmp_path / "gold.jsonl", [_gold(review_state="draft")])
        assert runner.main(["--dataset", str(dataset)]) == 1

    def test_a_bad_arm_is_a_usage_error(self, tmp_path):
        with pytest.raises(SystemExit):
            runner.main(["--photos", str(tmp_path), "--compare", "v2", "v2@lots"])

    def test_unlabelled_predictions_report_no_calibration_rather_than_zero(self):
        report = evaluate([Prediction(item_id="a", category="unlabelled",
                                      expected_price=None, predicted_expected=40.0)])
        assert report["n_labelled"] == 0
        assert report["accuracy"]["mdape"] is None
        assert report["calibration"]["ece"] is None
        assert runner.metric_set(report, "x").get("calibration_ece") is None

    def test_unlabelled_predictions_report_no_hallucination_rate_either(self):
        """The raw report is written to --json-out too, and the docs say an
        unlabelled run carries no hallucination figure — not one resting on
        two of its three heuristics."""
        report = evaluate([Prediction(item_id="a", category="unlabelled",
                                      expected_price=None, predicted_expected=40.0,
                                      model_name="Synchilla", visual_evidence=[])])
        assert report["hallucination"] == {"rate": None, "n": 0}
        assert runner.metric_set(report, "x").get("hallucination_rate") is None

    def test_tokens_print_as_separate_counts_not_a_share(self):
        report = evaluate([Prediction(item_id="a", category="unlabelled",
                                      expected_price=None, predicted_expected=40.0,
                                      output_tokens=850, thoughts_tokens=1400)])
        text = runner._format(report)
        assert "of which" not in text
        assert "answer 850.0" in text and "thinking 1400.0" in text
        assert "billed output 2250.0" in text


class TestLiveArm:
    """`run_live` against a stand-in model: the one path above that the fakes
    replace. The budget must reach the call as config, and only for its arm."""

    def _model(self, seen: list):
        payload = json.dumps({
            "item_name": "Fleece", "brand": "Patagonia", "category": "clothing",
            "identification_certainty": "certain",
            "worst_case_price_usd": 32, "quick_sale_price_usd": 45,
            "expected_price_usd": 58, "best_case_price_usd": 85,
            "visual_evidence": ["wordmark"]})

        class Usage:
            candidates_token_count = 2100
            thoughts_token_count = 480

        class Response:
            text = payload
            usage_metadata = Usage()
            candidates: list = []

        class Model:
            async def generate_content_async(self, contents, generation_config=None):
                seen.append(generation_config)
                return Response()
        return Model()

    def _run(self, tmp_path, monkeypatch, budget):
        import aiconfig
        from tests.images import VALID_JPEG

        (tmp_path / "a.jpg").write_bytes(VALID_JPEG)
        seen: list = []
        monkeypatch.setattr(aiconfig, "build_model", lambda *a, **k: self._model(seen))
        items = [runner.EvalItem("a", "clothing", "a.jpg", 60.0, "Patagonia")]
        import asyncio
        predictions = asyncio.run(runner.run_live(items, "v2", tmp_path, 1, budget))
        return predictions, seen

    def test_a_budget_arm_sends_that_cap_and_records_tokens(self, tmp_path, monkeypatch):
        predictions, seen = self._run(tmp_path, monkeypatch, 512)
        (p,) = predictions
        assert p.error is None and p.predicted_expected == 58
        assert p.output_tokens == 2100 and p.thoughts_tokens == 480
        assert p.prompt_version == "v2@512"
        assert seen[0].thinking_config.thinking_budget == 512

    def test_an_arm_without_a_budget_uses_the_production_config(self, tmp_path, monkeypatch):
        _, seen = self._run(tmp_path, monkeypatch, None)
        assert seen == [None]


# ── Calibration examples (#226) ──────────────────────────────────────────────

def _fake_run_with_signals():
    """Priced items with signals and splits, as `_predict_one` fills them.
    Every third item lands within 25% and inside its range; the rest miss."""
    async def fake(items, version, root, concurrency=4, thinking_budget=None):
        out = []
        for n, i in enumerate(items):
            hit = n % 3 == 0
            price = i.expected_price * (1.1 if hit else 2.0)
            out.append(runner.Prediction(
                item_id=i.id, category=i.category, expected_price=i.expected_price,
                predicted_expected=price, predicted_low=price * 0.8,
                predicted_high=price * 1.2, confidence_score=60 + n % 30,
                split=i.split, signals={"brand": 1.0, "range": 0.5}))
        return out
    return fake


class TestCalibrationExamples:
    def _dataset(self, tmp_path: Path, n: int = 90) -> Path:
        return _write_jsonl(tmp_path / "gold.jsonl",
                            [_gold(id=f"g{i}", images=[{"path": f"images/g{i}.jpg",
                                                        "is_primary": True}])
                             for i in range(n)])

    def test_examples_out_feeds_eval_cli_calibrate(self, tmp_path, monkeypatch, capsys):
        from eval import cli
        monkeypatch.setattr(runner, "run_live", _fake_run_with_signals())
        examples = tmp_path / "examples.json"

        assert runner.main(["--dataset", str(self._dataset(tmp_path)),
                            "--examples-out", str(examples)]) == 0

        written = json.loads(examples.read_text())
        # By default, what the band promises: the sale lands in the range.
        assert written["event"] == "in_range"
        assert all(e["correct"] == e["in_range"] for e in written["examples"])
        assert len(written["examples"]) == 90
        first = written["examples"][0]
        assert set(first) == {"item_id", "split", "signals", "raw_confidence",
                              "within_25pct", "in_range", "correct"}
        assert first["signals"] == {"brand": 1.0, "range": 0.5}
        assert {e["split"] for e in written["examples"]} == {"dev", "test"}

        capsys.readouterr()
        assert cli.main(["calibrate", "--examples", str(examples), "--method", "platt"]) == 0
        report = json.loads(capsys.readouterr().out.split("\n\n⚠️")[0])
        # Fitted on the gold set's dev split and judged on its test split.
        dev = sum(1 for e in written["examples"] if e["split"] == "dev")
        assert report["n_train"] == dev
        assert report["n_holdout"] == 90 - dev
        assert report["method"] == "platt"
        assert report["event"] == "in_range"

    def test_the_event_picks_which_outcome_counts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "run_live", _fake_run_with_signals())
        examples = tmp_path / "examples.json"
        assert runner.main(["--dataset", str(self._dataset(tmp_path, 30)),
                            "--examples-out", str(examples), "--event", "within_25pct"]) == 0
        written = json.loads(examples.read_text())
        assert written["event"] == "within_25pct"
        assert all(e["correct"] == e["within_25pct"] for e in written["examples"])

    def test_reliability_reads_the_same_file(self, tmp_path, monkeypatch, capsys):
        from eval import cli
        monkeypatch.setattr(runner, "run_live", _fake_run_with_signals())
        examples = tmp_path / "examples.json"
        runner.main(["--dataset", str(self._dataset(tmp_path)), "--examples-out", str(examples)])
        capsys.readouterr()

        assert cli.main(["reliability", "--examples", str(examples)]) == 0
        table = json.loads(capsys.readouterr().out)
        assert table["n"] == 90
        assert [row["band"] for row in table["bands"]] == ["Low", "Medium", "High"]
        assert sum(row["n"] for row in table["bands"]) == 90

    def test_examples_out_is_one_arm(self, tmp_path):
        with pytest.raises(SystemExit):
            runner.main(["--dataset", "x.jsonl", "--compare", "v2", "v2.1",
                         "--examples-out", str(tmp_path / "e.json")])
