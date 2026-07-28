"""Tests for the evaluation platform.

Two things are being verified, and they are different:

* that the **implementations** are correct — statistics, calibration fitting,
  gate logic, drift detection;
* that the platform **cannot fabricate a result** — no fitted weights without a
  dataset version, no measured metric without a sample size, no gate passing on
  an empty run, no projected value failing a build.

The second set matters more. A metrics library with a subtle bug produces wrong
numbers; a platform that can silently invent numbers produces confident wrong
decisions.
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import calibration as calib  # noqa: E402
from eval import dashboard as dash  # noqa: E402
from eval import gates  # noqa: E402
from eval import metrics  # noqa: E402
from eval import schema  # noqa: E402
from eval import stats  # noqa: E402
from eval.experiment import (  # noqa: E402
    ArmResult,
    Guardrail,
    Verdict,
    run_experiment,
)
from eval.provenance import Metric, MetricSet, Provenance  # noqa: E402


# ═══ Provenance — the honesty primitive ═══════════════════════════════════════

class TestProvenance:
    def test_measured_requires_a_sample_size(self):
        with pytest.raises(ValueError, match="sample size"):
            Metric(name="mdape", value=18.0, provenance=Provenance.MEASURED)

    def test_measured_requires_a_value(self):
        with pytest.raises(ValueError):
            Metric(name="mdape", value=None, provenance=Provenance.MEASURED,
                   sample_size=10)

    def test_projected_must_state_its_basis(self):
        """An unexplained estimate is indistinguishable from a guess."""
        with pytest.raises(ValueError, match="basis"):
            Metric(name="mdape", value=18.0, provenance=Provenance.PROJECTED)

    def test_projected_with_basis_is_allowed(self):
        m = Metric.projected("mdape", 18.0, basis="extrapolated from pilot")
        assert m.value == 18.0 and not m.is_measured

    def test_only_measured_can_gate(self):
        assert Provenance.MEASURED.can_gate
        assert not Provenance.PROJECTED.can_gate
        assert not Provenance.UNAVAILABLE.can_gate

    def test_format_always_carries_the_marker(self):
        measured = Metric.measured("m", 1.0, 10)
        projected = Metric.projected("m", 1.0, basis="estimate")
        unavailable = Metric.unavailable("m", "no data")
        assert "✓" in measured.format()
        assert "≈" in projected.format() and "PROJECTED" in projected.format()
        assert "—" in unavailable.format()

    def test_unavailable_is_not_zero(self):
        """Zero reads as 'perfect' for an error metric."""
        m = Metric.unavailable("mdape", "no gold set")
        assert m.value is None
        assert "n/a" in m.format()

    def test_metric_set_separates_measured_from_projected(self):
        s = MetricSet(label="run")
        s.add(Metric.measured("a", 1.0, 5))
        s.add(Metric.projected("b", 2.0, basis="guess"))
        assert set(s.measured) == {"a"}
        assert set(s.projected) == {"b"}
        assert s.has_any_measurement


# ═══ Statistics ═══════════════════════════════════════════════════════════════

class TestStats:
    def test_bootstrap_ci_brackets_the_statistic(self):
        values = [10.0, 12.0, 11.0, 13.0, 9.0, 11.5, 10.5, 12.5]
        ci = stats.bootstrap_ci(values, statistics.median)
        assert ci is not None
        assert ci[0] <= statistics.median(values) <= ci[1]

    def test_bootstrap_is_deterministic(self):
        """A CI gate returning a different interval per run is a flaky build."""
        values = [float(i) for i in range(20)]
        assert stats.bootstrap_ci(values) == stats.bootstrap_ci(values)

    def test_bootstrap_needs_a_minimum_sample(self):
        assert stats.bootstrap_ci([1.0, 2.0]) is None

    def test_wilcoxon_detects_a_consistent_shift(self):
        deltas = [-5.0] * 15
        result = stats.wilcoxon_signed_rank(deltas)
        assert result is not None
        assert result[1] < 0.01

    def test_wilcoxon_finds_no_effect_in_symmetric_noise(self):
        deltas = [1.0, -1.0] * 10
        result = stats.wilcoxon_signed_rank(deltas)
        assert result is not None
        assert result[1] > 0.5

    def test_wilcoxon_refuses_tiny_samples(self):
        assert stats.wilcoxon_signed_rank([1.0, -1.0]) is None

    def test_cliffs_delta_sign_and_magnitude(self):
        assert stats.cliffs_delta([10.0] * 5, [1.0] * 5) == 1.0
        assert stats.cliffs_delta([1.0] * 5, [10.0] * 5) == -1.0

    def test_paired_comparison_identifies_the_winner(self):
        errors_a = [20.0] * 20
        errors_b = [10.0] * 20
        result = stats.compare_paired(errors_a, errors_b)
        assert result is not None
        assert result.median_delta < 0          # B improved
        assert result.wins_b == 20
        assert result.direction == "B better"

    def test_paired_comparison_rejects_mismatched_lengths(self):
        assert stats.compare_paired([1.0], [1.0, 2.0]) is None

    def test_effect_label_thresholds(self):
        assert stats.compare_paired([20.0] * 20, [10.0] * 20).effect_label == "large"

    def test_required_sample_size_grows_as_effect_shrinks(self):
        assert stats.required_sample_size(0.1) > stats.required_sample_size(0.8)


# ═══ Extended metrics ═════════════════════════════════════════════════════════

class TestExtendedMetrics:
    def test_rmse_exceeds_mae_when_errors_are_uneven(self):
        """The gap between them is the signal: a few severe misses vs drift."""
        pairs = [(100, 100)] * 9 + [(500, 100)]
        assert metrics.rmse(pairs) > metrics.mae(pairs)

    def test_rmse_equals_mae_for_uniform_error(self):
        pairs = [(110, 100)] * 5
        assert metrics.rmse(pairs) == pytest.approx(metrics.mae(pairs))

    def test_bias_detects_systematic_over_valuation(self):
        assert metrics.bias([(120, 100)] * 5) == pytest.approx(20.0)

    def test_bias_is_near_zero_for_symmetric_error(self):
        assert abs(metrics.bias([(120, 100), (80, 100)])) < 1e-9

    def test_bias_distinguishes_drift_from_noise(self):
        """Same MdAPE, opposite meanings — this is why bias is reported."""
        systematic = [(120, 100)] * 10
        noisy = [(120, 100), (80, 100)] * 5
        assert metrics.mdape(systematic) == pytest.approx(metrics.mdape(noisy))
        assert abs(metrics.bias(systematic)) > abs(metrics.bias(noisy))

    def test_interval_calibration_flags_overconfidence(self):
        triples = [(90.0, 110.0, 200.0)] * 10      # never covers
        result = metrics.prediction_interval_calibration(triples, nominal=0.80)
        assert result["over_confident"]
        assert result["gap"] < 0

    def test_interval_calibration_when_well_calibrated(self):
        triples = [(50.0, 150.0, 100.0)] * 8 + [(50.0, 150.0, 500.0)] * 2
        result = metrics.prediction_interval_calibration(triples, nominal=0.80)
        assert result["empirical"] == pytest.approx(0.8)
        assert not result["over_confident"]

    def test_repeatability(self):
        result = metrics.repeatability([[100.0, 101.0], [50.0, 90.0]], tolerance=0.02)
        assert result["stable_fraction"] == 0.5

    def test_field_accuracy_treats_abstention_separately(self):
        """Declining to guess is the desired behaviour, not an error."""
        result = metrics.field_accuracy([
            ("Nike", "Nike"), ("Unknown", "Adidas"), ("Puma", "Adidas")])
        assert result["abstained"] == 1
        assert result["wrong"] == 1
        assert result["precision_when_attempted"] == 0.5

    def test_field_accuracy_can_penalise_abstention(self):
        result = metrics.field_accuracy(
            [("Unknown", "Adidas")], allow_unknown=False)
        assert result["wrong"] == 1 and result["abstained"] == 0

    def test_top_k_accuracy(self):
        assert metrics.top_k_accuracy([(["a", "b", "c"], "c")], k=3) == 1.0
        assert metrics.top_k_accuracy([(["a", "b", "c"], "c")], k=2) == 0.0

    def test_matching_quality_reports_false_match_rate(self):
        """A wrong comp is worse than a missing one — this is the headline."""
        result = metrics.matching_quality(
            [(True, True), (True, False), (False, False), (False, True)])
        assert result["precision"] == 0.5
        assert result["recall"] == 0.5
        assert result["false_match_rate"] == 0.5

    def test_negative_control_accuracy(self):
        assert metrics.negative_control_accuracy(
            [True, True, False])["declined_correctly"] == pytest.approx(2 / 3)

    def test_every_extended_metric_returns_none_on_empty(self):
        """Zero would read as a perfect score for an unmeasured run."""
        assert metrics.rmse([]) is None
        assert metrics.mae([]) is None
        assert metrics.bias([]) is None
        assert metrics.median_error([]) is None
        assert metrics.prediction_interval_calibration([]) is None
        assert metrics.repeatability([]) is None
        assert metrics.field_accuracy([]) is None
        assert metrics.top_k_accuracy([]) is None
        assert metrics.matching_quality([]) is None
        assert metrics.negative_control_accuracy([]) is None


# ═══ Gold dataset schema ══════════════════════════════════════════════════════

def gold(**kw) -> schema.GoldItem:
    base = dict(
        id="x1",
        images=[schema.ImageRef(path="a.jpg", is_primary=True)],
        actual_sale_price=100.0, currency="USD", category="clothing",
        review_state=schema.ReviewState.APPROVED, reviewed_by="reviewer",
        label_confidence=schema.LabelConfidence.CERTAIN,
        evidence_note="receipt held",
    )
    base.update(kw)
    return schema.GoldItem(**base)


class TestGoldSchema:
    def test_approved_record_is_scoreable(self):
        assert gold().is_scoreable

    def test_draft_record_is_not_scoreable(self):
        assert not gold(review_state=schema.ReviewState.DRAFT).is_scoreable

    def test_negative_control_is_not_scoreable(self):
        """There is no correct price to be accurate about."""
        assert not gold(difficulty=schema.Difficulty.NEGATIVE_CONTROL,
                        actual_sale_price=0.0).is_scoreable

    def test_quarantined_record_is_excluded(self):
        assert not gold(split=schema.Split.QUARANTINE).is_scoreable

    def test_low_confidence_label_excluded_from_headline(self):
        item = gold(label_confidence=schema.LabelConfidence.LOW)
        assert item.is_scoreable
        assert not item.counts_toward_headline

    def test_high_confidence_label_requires_evidence(self):
        """A claim of certainty must be traceable to something."""
        problems = gold(evidence_note="", evidence_url=None).validate()
        assert any("evidence" in p for p in problems)

    def test_approved_requires_a_reviewer(self):
        assert any("reviewer" in p for p in gold(reviewed_by=None).validate())

    def test_future_sold_date_is_rejected(self):
        item = gold(sold_date=date(2099, 1, 1))
        assert any("future" in p for p in item.validate())

    def test_record_without_images_is_invalid(self):
        assert any("images" in p for p in gold(images=[]).validate())

    def test_split_assignment_is_deterministic(self):
        item = gold(id="stable-id")
        assert item.assigned_split() == item.assigned_split()

    def test_split_assignment_differs_across_ids(self):
        splits = {gold(id=f"item-{i}").assigned_split() for i in range(50)}
        assert len(splits) == 2      # both pools populated

    def test_label_fingerprint_changes_when_price_changes(self):
        assert gold().label_fingerprint() != gold(actual_sale_price=101.0).label_fingerprint()

    def test_label_fingerprint_ignores_annotation(self):
        """Adding a note must not look like label revision."""
        assert gold().label_fingerprint() == gold(
            human_notes="added a note", tags=["x"]).label_fingerprint()

    def test_content_hash_is_order_independent(self):
        a, b = gold(id="a"), gold(id="b")
        assert schema.content_hash([a, b]) == schema.content_hash([b, a])

    def test_content_hash_changes_on_label_edit(self):
        a, b = gold(id="a"), gold(id="b")
        edited = gold(id="b", actual_sale_price=999.0)
        assert schema.content_hash([a, b]) != schema.content_hash([a, edited])


class TestDriftDetection:
    def test_label_revision_is_detected(self):
        """The serious one: a price edited toward what the model predicted."""
        baseline = [gold(id="a", actual_sale_price=100.0)]
        candidate = [gold(id="a", actual_sale_price=140.0)]
        report = schema.composition_drift(baseline, candidate)
        assert report.has_label_drift
        assert report.label_changes == 1
        assert not report.is_clean

    def test_pure_addition_is_clean(self):
        baseline = [gold(id=f"a{i}", category="clothing") for i in range(10)]
        candidate = baseline + [gold(id="new", category="clothing")]
        report = schema.composition_drift(baseline, candidate)
        assert not report.has_label_drift
        assert report.added == 1

    def test_composition_creep_is_flagged(self):
        baseline = [gold(id=f"a{i}", category="clothing") for i in range(10)]
        candidate = ([gold(id=f"a{i}", category="clothing") for i in range(5)]
                     + [gold(id=f"b{i}", category="shoes") for i in range(10)])
        report = schema.composition_drift(baseline, candidate)
        assert any("shoes" in w for w in report.warnings)

    def test_removal_is_flagged(self):
        baseline = [gold(id="a"), gold(id="b")]
        report = schema.composition_drift(baseline, [gold(id="a")])
        assert report.removed == 1
        assert any("removed" in w for w in report.warnings)


class TestCoverage:
    def test_reports_gaps_against_target(self):
        report = schema.coverage([gold(id=f"c{i}") for i in range(5)])
        assert report.category_gaps["clothing"] == 295
        assert not report.meets_target

    def test_shipped_template_contains_no_scoreable_records(self):
        """The template must never be mistaken for measured data."""
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "eval", "data", "gold.template.jsonl")
        items = schema.load_gold(path)
        assert items, "template should parse"
        assert all(not i.is_scoreable for i in items)

    def test_shipped_v1_sample_contains_no_scoreable_records(self):
        from eval import dataset as dataset_module
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "eval", "data", "sample.jsonl")
        items = dataset_module.load(path)
        assert items
        assert all(not i.is_scoreable for i in items), (
            "sample data must never contribute to a reported metric")


# ═══ Experiment framework ═════════════════════════════════════════════════════

def arm(label, errors, **kw) -> ArmResult:
    ids = [f"i{n}" for n in range(len(errors))]
    return ArmResult(
        label=label,
        absolute_percentage_error=dict(zip(ids, errors)),
        predicted={i: 100.0 for i in ids},
        actual={i: 100.0 for i in ids},
        **kw,
    )


class TestExperiment:
    def test_clear_improvement_ships(self):
        result = run_experiment(
            "prompt-v2", arm("v1", [30.0] * 40), arm("v2", [15.0] * 40))
        assert result.verdict is Verdict.SHIP

    def test_clear_regression_is_rejected(self):
        result = run_experiment(
            "prompt-v3", arm("v1", [15.0] * 40), arm("v3", [30.0] * 40))
        assert result.verdict is Verdict.REJECT

    def test_no_difference_is_inconclusive_not_a_win(self):
        result = run_experiment(
            "noop", arm("a", [20.0] * 40), arm("b", [20.0] * 40))
        assert result.verdict is Verdict.INCONCLUSIVE

    def test_small_sample_is_inconclusive_and_says_why(self):
        """A null result on 10 items means underpowered, not equivalent."""
        result = run_experiment("tiny", arm("a", [30.0] * 10), arm("b", [10.0] * 10))
        assert result.verdict is Verdict.INCONCLUSIVE
        assert any("underpowered" in w for w in result.warnings)

    def test_guardrail_blocks_an_accuracy_win(self):
        """An accuracy win that doubles latency is not a win."""
        baseline = arm("a", [30.0] * 40,
                       latency_ms={f"i{n}": 1000.0 for n in range(40)})
        candidate = arm("b", [15.0] * 40,
                        latency_ms={f"i{n}": 5000.0 for n in range(40)})
        result = run_experiment("slow-but-accurate", baseline, candidate)
        assert result.verdict is Verdict.BLOCKED_BY_GUARDRAIL
        assert any("latency" in v for v in result.guardrail_violations)

    def test_hallucination_increase_blocks(self):
        ids = [f"i{n}" for n in range(40)]
        baseline = arm("a", [30.0] * 40,
                       hallucinated={i: False for i in ids})
        candidate = arm("b", [15.0] * 40,
                        hallucinated={i: n < 10 for n, i in enumerate(ids)})
        result = run_experiment("hallucinating", baseline, candidate)
        assert result.verdict is Verdict.BLOCKED_BY_GUARDRAIL

    def test_negligible_effect_does_not_ship(self):
        """Significance without magnitude is how teams chase noise."""
        random.seed(1)
        base = [20.0 + random.random() * 0.01 for _ in range(200)]
        cand = [b - 0.001 for b in base]
        result = run_experiment("tiny-effect", arm("a", base), arm("b", cand))
        assert result.verdict is not Verdict.SHIP

    def test_disjoint_items_are_inconclusive(self):
        a = ArmResult(label="a", absolute_percentage_error={"x": 10.0})
        b = ArmResult(label="b", absolute_percentage_error={"y": 5.0})
        result = run_experiment("disjoint", a, b)
        assert result.verdict is Verdict.INCONCLUSIVE
        assert result.paired_items == 0

    def test_partially_overlapping_arms_use_the_shared_subset(self):
        a = ArmResult(
            label="a",
            absolute_percentage_error={f"i{n}": 30.0 for n in range(40)})
        b = ArmResult(
            label="b",
            absolute_percentage_error={f"i{n}": 15.0 for n in range(35)})
        result = run_experiment("partial", a, b)
        assert result.paired_items == 35
        assert any("only one arm" in w for w in result.warnings)

    def test_zero_baseline_guardrail_is_not_disabled(self):
        """Regression: `not baseline.value` was True for 0.0, so the guardrail
        was silently disabled at exactly the baseline most worth protecting."""
        g = Guardrail("hallucination_rate", max_relative_increase=0.0)
        violation = g.check(Metric.measured("hallucination_rate", 0.0, 40),
                            Metric.measured("hallucination_rate", 25.0, 40))
        assert violation is not None
        assert "clean baseline" in violation

    def test_zero_baseline_holds_when_candidate_also_zero(self):
        g = Guardrail("hallucination_rate", max_relative_increase=0.0)
        assert g.check(Metric.measured("hallucination_rate", 0.0, 40),
                       Metric.measured("hallucination_rate", 0.0, 40)) is None

    def test_significant_but_trivial_change_does_not_ship(self):
        """Regression: p ~ 0 and Cliff's delta 'small' for a 0.005% shift.

        Cliff's delta measures distributional overlap, not magnitude, so a
        uniform micro-shift clears it. The practical-magnitude floor is what
        stops the framework rubber-stamping noise.
        """
        random.seed(1)
        base = [20.0 + random.random() * 0.01 for _ in range(200)]
        cand = [b - 0.001 for b in base]
        result = run_experiment("micro-shift", arm("a", base), arm("b", cand))
        assert result.comparison.significant       # statistically, yes
        assert result.verdict is Verdict.INCONCLUSIVE   # practically, no
        assert any("magnitude" in w for w in result.warnings)

    def test_meaningful_improvement_still_ships(self):
        """The magnitude floor must not block genuine progress."""
        result = run_experiment("real-gain", arm("a", [20.0] * 60),
                                arm("b", [17.0] * 60))
        assert result.verdict is Verdict.SHIP

    def test_arm_metrics_are_all_measured(self):
        s = arm("a", [10.0] * 30).metric_set()
        assert s.has_any_measurement
        assert all(m.is_measured for m in s.metrics.values())

    def test_guardrail_absolute_ceiling(self):
        g = Guardrail("bias", max_relative_increase=10.0, absolute_ceiling=5.0)
        violation = g.check(None, Metric.measured("bias", 9.0, 10))
        assert violation and "ceiling" in violation


# ═══ CI gates ═════════════════════════════════════════════════════════════════

class TestGates:
    def _set(self, **values) -> MetricSet:
        s = MetricSet(label="run")
        for name, value in values.items():
            s.add(Metric.measured(name, value, 100))
        return s

    def test_stable_metrics_pass(self):
        report = gates.check(self._set(mdape=18.0), self._set(mdape=18.0))
        assert report.status is gates.GateStatus.PASSED
        assert report.exit_code == 0

    def test_regression_fails_the_build(self):
        report = gates.check(self._set(mdape=25.0), self._set(mdape=18.0))
        assert report.status is gates.GateStatus.FAILED
        assert report.exit_code == 1

    def test_improvement_passes(self):
        report = gates.check(self._set(mdape=12.0), self._set(mdape=18.0))
        assert report.status is gates.GateStatus.PASSED

    def test_higher_is_better_direction_is_respected(self):
        report = gates.check(self._set(within_25pct=50.0),
                             self._set(within_25pct=70.0))
        assert report.status is gates.GateStatus.FAILED

    def test_empty_run_is_skipped_never_passed(self):
        """Silent success on an empty benchmark is the most dangerous outcome."""
        report = gates.check(MetricSet(label="empty"), self._set(mdape=18.0))
        assert report.status is gates.GateStatus.SKIPPED
        assert report.status is not gates.GateStatus.PASSED

    def test_projected_values_cannot_fail_a_build(self):
        current = MetricSet(label="run")
        current.add(Metric.projected("mdape", 99.0, basis="estimate"))
        report = gates.check(current, self._set(mdape=18.0))
        assert report.status is not gates.GateStatus.FAILED

    def test_first_run_without_baseline_skips_gracefully(self):
        report = gates.check(self._set(mdape=18.0), None)
        assert report.status is gates.GateStatus.SKIPPED

    def test_warn_only_gate_does_not_fail(self):
        threshold = gates.Threshold("m", gates.Direction.LOWER_IS_BETTER,
                                    max_regression=0.01, warn_only=True)
        report = gates.check(self._set(m=100.0), self._set(m=1.0),
                             thresholds=(threshold,))
        assert report.status is gates.GateStatus.WARNED
        assert report.exit_code == 0

    def test_hallucination_has_zero_tolerance(self):
        report = gates.check(self._set(hallucination_rate=1.0),
                             self._set(hallucination_rate=0.5))
        assert report.status is gates.GateStatus.FAILED

    def test_baseline_roundtrip(self, tmp_path):
        path = tmp_path / "baseline.json"
        gates.save_baseline(self._set(mdape=18.0), path, ref="abc123")
        loaded = gates.load_baseline(path)
        assert loaded is not None
        assert loaded.get("mdape").value == 18.0

    def test_missing_baseline_file_is_none_not_an_error(self, tmp_path):
        assert gates.load_baseline(tmp_path / "nope.json") is None

    def test_corrupt_baseline_degrades_to_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert gates.load_baseline(path) is None


class TestSchemaCompliance:
    def _valid(self) -> dict:
        return {
            "item_name": "x", "brand": "y", "category": "clothing",
            "condition_notes": "good", "est_value_low_usd": 10.0,
            "est_value_high_usd": 20.0, "confidence": "High",
            "sold_listings_count": 0, "listing_title": "t",
            "listing_description": "d",
        }

    def test_valid_response_is_compliant(self):
        assert gates.schema_compliance([self._valid()]).value == 100.0

    def test_missing_required_field_is_not_compliant(self):
        broken = self._valid()
        del broken["brand"]
        assert gates.schema_compliance([broken]).value == 0.0

    def test_inverted_range_is_not_compliant(self):
        broken = self._valid() | {"est_value_low_usd": 99.0}
        assert gates.schema_compliance([broken]).value == 0.0

    def test_invalid_confidence_band_is_not_compliant(self):
        broken = self._valid() | {"confidence": "Very High"}
        assert gates.schema_compliance([broken]).value == 0.0

    def test_no_responses_is_unavailable_not_zero(self):
        result = gates.schema_compliance([])
        assert result.provenance is Provenance.UNAVAILABLE


# ═══ Calibration ══════════════════════════════════════════════════════════════

def synthetic_examples(n=400, seed=7):
    """Examples from a KNOWN generating process.

    Validates the fitting implementation only. These are not product data and
    nothing measured about SnapWorth can be inferred from them.
    """
    rng = random.Random(seed)
    out = []
    for i in range(n):
        brand = rng.choice([0.0, 1.0])
        image = rng.random()
        tight = rng.random()
        logit = -2.0 + 2.5 * brand + 1.5 * image + 1.0 * tight
        probability = 1 / (1 + math.exp(-logit))
        out.append(calib.TrainingExample(
            signals={"brand": brand, "image": image, "tight": tight},
            correct=rng.random() < probability,
            item_id=f"syn-{i}",
            raw_confidence=probability * 100,
        ))
    return out


class TestCalibration:
    def test_logistic_recovers_the_signal_ordering(self):
        model = calib.fit_logistic(synthetic_examples())
        assert model is not None
        weights = model.weights
        # Generating process weighted brand > image > tight.
        assert weights["brand"] > weights["image"] > weights["tight"]

    def test_logistic_refuses_tiny_samples(self):
        """Weights fitted on 10 points look authoritative and are arbitrary."""
        assert calib.fit_logistic(synthetic_examples(10)) is None

    def test_normalised_weights_are_comparable_to_the_hand_chosen_prior(self):
        model = calib.fit_logistic(synthetic_examples())
        normalised = model.normalised_weights()
        assert abs(sum(abs(v) for v in normalised.values()) - 1.0) < 1e-6

    def test_isotonic_is_monotone(self):
        model = calib.fit_isotonic(
            [(i / 100, i > 50) for i in range(100)])
        assert model is not None
        outputs = [model.predict_proba(x / 20) for x in range(20)]
        assert all(b >= a - 1e-9 for a, b in zip(outputs, outputs[1:]))

    def test_isotonic_clamps_outside_the_fitted_range(self):
        model = calib.fit_isotonic([(i / 100, i > 50) for i in range(100)])
        assert 0.0 <= model.predict_proba(-5.0) <= 1.0
        assert 0.0 <= model.predict_proba(5.0) <= 1.0

    def test_temperature_softens_an_overconfident_system(self):
        # Claims 0.95 but is right half the time.
        points = [(0.95, i % 2 == 0) for i in range(100)]
        model = calib.fit_temperature(points)
        assert model is not None
        assert model.temperature > 1.0
        assert model.predict_proba(0.95) < 0.95

    def test_temperature_sharpens_an_underconfident_system(self):
        points = [(0.55, True) for _ in range(100)]
        model = calib.fit_temperature(points)
        assert model.predict_proba(0.55) > 0.55

    def test_gradient_boosting_raises_rather_than_degrading_silently(self):
        with pytest.raises(NotImplementedError, match="scikit-learn"):
            calib.GradientBoostingPlaceholder().fit([])

    def test_measured_model_must_name_its_dataset_version(self):
        """Otherwise the weights cannot be reproduced or audited."""
        with pytest.raises(ValueError, match="dataset version"):
            calib.CalibrationModel(
                method="logistic", provenance=Provenance.MEASURED,
                fitted_at=datetime.now(timezone.utc), n_examples=100)

    def test_projected_model_needs_no_dataset_version(self):
        model = calib.CalibrationModel(
            method="logistic", provenance=Provenance.PROJECTED,
            fitted_at=datetime.now(timezone.utc), n_examples=100)
        assert model.provenance is Provenance.PROJECTED

    def test_fit_without_dataset_version_is_projected(self):
        model = calib.fit(synthetic_examples(), provenance=Provenance.PROJECTED)
        assert model is not None
        assert model.provenance is Provenance.PROJECTED

    def test_holdout_split_is_deterministic_and_disjoint(self):
        examples = synthetic_examples(200)
        train_a, holdout_a = calib.split_examples(examples)
        train_b, holdout_b = calib.split_examples(examples)
        assert [e.item_id for e in train_a] == [e.item_id for e in train_b]
        assert not ({e.item_id for e in train_a} & {e.item_id for e in holdout_a})

    def test_calibration_evaluation_reports_ece_and_brier(self):
        examples = synthetic_examples()
        train, holdout = calib.split_examples(examples)
        model = calib.fit(train, provenance=Provenance.PROJECTED)
        result = calib.evaluate_calibration(model, holdout)
        assert result["ece"] is not None
        assert 0.0 <= result["ece"] <= 1.0
        assert result["brier"] is not None

    def test_evaluation_on_empty_holdout_is_none(self):
        model = calib.fit(synthetic_examples(), provenance=Provenance.PROJECTED)
        assert calib.evaluate_calibration(model, [])["ece"] is None


# ═══ Dashboard ════════════════════════════════════════════════════════════════

class TestDashboard:
    def test_empty_dashboard_declares_it_has_no_measurements(self):
        board = dash.build(metrics=MetricSet(label="none"))
        integrity = board.integrity
        assert not integrity["is_evidence_backed"]
        assert "do not cite" in integrity["warning"]

    def test_measured_dashboard_is_evidence_backed(self):
        s = MetricSet(label="run")
        s.add(Metric.measured("mdape", 18.0, 500))
        board = dash.build(metrics=s)
        assert board.integrity["is_evidence_backed"]

    def test_panel_provenance_takes_the_worst_value(self):
        """A mixed panel must be labelled by its weaker member."""
        panel = dash.Panel(
            key="p", title="P", kind=dash.PanelKind.SCALAR,
            metrics=[Metric.measured("a", 1.0, 10),
                     Metric.projected("b", 2.0, basis="estimate")])
        assert panel.provenance is Provenance.PROJECTED

    def test_unavailable_dominates_projected(self):
        panel = dash.Panel(
            key="p", title="P", kind=dash.PanelKind.SCALAR,
            metrics=[Metric.projected("b", 2.0, basis="e"),
                     Metric.unavailable("c", "no data")])
        assert panel.provenance is Provenance.UNAVAILABLE

    def test_trend_needs_two_runs(self):
        panel = dash.trend_panel([{"run": 1}])
        assert panel.provenance is Provenance.UNAVAILABLE

    def test_trend_renders_with_history(self):
        panel = dash.trend_panel([{"run": 1}, {"run": 2}])
        assert panel.provenance is Provenance.MEASURED

    def test_calibration_panel_without_data_is_explicit(self):
        panel = dash.calibration_panel(None)
        assert panel.provenance is Provenance.UNAVAILABLE
        assert "prior" in panel.metrics[0].basis

    def test_payload_serialises(self):
        board = dash.build(metrics=MetricSet(label="none"))
        payload = json.loads(board.to_json())
        assert "integrity" in payload
        assert all("provenance" in p for p in payload["panels"])
