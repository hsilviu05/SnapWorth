"""Tests for the eval CLI wrapper and the error taxonomy.

B-17. `docs/EVALUATION.md` listed "Error taxonomy" and the surrounding rows as
"Implemented and tested". The gate logic underneath (`eval/gates.py`) genuinely
is. `eval/cli.py` — which CI invokes — and `eval/erroranalysis.py` had zero
tests between them, so the claim covered 721 lines nothing exercised.

These are deliberately about behaviour a reader would rely on: that the
classifier assigns the mode a rule says it should, that a case can carry more
than one, that "unclassified" is a real state rather than a silent default,
that value-at-risk ranking puts the expensive miss first, and that each CLI
subcommand parses its arguments and returns the exit code CI branches on.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cli                                             # noqa: E402
from eval.erroranalysis import (FailureCase, FailureMode,         # noqa: E402
                                analyse, classify, recurring_patterns)


def _case(**overrides) -> FailureCase:
    """A case that is a failure but classifies to nothing on its own."""
    base = dict(item_id="i1", category="clothing", predicted=200.0, actual=100.0)
    base.update(overrides)
    return FailureCase(**base)


# ── Thresholds ───────────────────────────────────────────────────────────────

class TestFailureThresholds:
    def test_error_percentages_are_relative_to_truth(self):
        c = _case(predicted=150.0, actual=100.0)
        assert c.error_pct == pytest.approx(50.0)
        assert c.signed_error_pct == pytest.approx(50.0)
        assert _case(predicted=50.0, actual=100.0).signed_error_pct == pytest.approx(-50.0)

    def test_a_zero_truth_price_does_not_divide_by_zero(self):
        c = _case(predicted=10.0, actual=0.0)
        assert c.error_pct == 0.0
        assert c.signed_error_pct == 0.0
        assert not c.is_failure

    def test_failure_and_severe_are_distinct_bands(self):
        assert not _case(predicted=120.0, actual=100.0).is_failure   # 20%
        assert _case(predicted=130.0, actual=100.0).is_failure       # 30%
        assert not _case(predicted=130.0, actual=100.0).is_severe
        assert _case(predicted=250.0, actual=100.0).is_severe        # 150%

    def test_value_at_risk_ranks_the_expensive_miss_first(self):
        """The reason this metric exists: 200% wrong on a $4 item matters
        less than 40% wrong on a $600 one, and percentage ranking buries the
        second behind the first."""
        cheap = _case(item_id="cheap", predicted=12.0, actual=4.0)      # 200%
        pricey = _case(item_id="pricey", predicted=840.0, actual=600.0)  # 40%
        assert cheap.error_pct > pricey.error_pct
        assert pricey.value_at_risk > cheap.value_at_risk


# ── Classification rules ─────────────────────────────────────────────────────

class TestClassify:
    def test_unclassified_is_a_real_answer_not_an_empty_list(self):
        assert classify(_case()) == [FailureMode.UNCLASSIFIED]

    def test_wrong_brand(self):
        modes = classify(_case(truth_brand="Patagonia", predicted_brand="Arc'teryx"))
        assert FailureMode.WRONG_BRAND in modes

    def test_matching_brand_is_not_a_brand_failure(self):
        modes = classify(_case(truth_brand="Patagonia", predicted_brand="  patagonia "))
        assert FailureMode.WRONG_BRAND not in modes

    def test_abstaining_counts_only_when_the_brand_was_knowable(self):
        knowable = classify(_case(truth_brand="Nike", predicted_brand="unknown",
                                  difficulty="typical"))
        assert FailureMode.ABSTAINED_ON_IDENTIFIABLE in knowable

        hard = classify(_case(truth_brand="Nike", predicted_brand="unknown",
                              difficulty="hard"))
        assert FailureMode.ABSTAINED_ON_IDENTIFIABLE not in hard

    def test_a_case_can_carry_several_modes(self):
        """A blurry photo causing a brand misread is genuinely both, and
        forcing one label would hide the other."""
        modes = classify(_case(truth_brand="Nike", predicted_brand="Adidas",
                               image_quality=0.1))
        assert FailureMode.WRONG_BRAND in modes
        assert FailureMode.POOR_IMAGE in modes
        assert FailureMode.UNCLASSIFIED not in modes

    def test_poor_image_is_bounded_by_the_quality_threshold(self):
        assert FailureMode.POOR_IMAGE in classify(_case(image_quality=0.39))
        assert FailureMode.POOR_IMAGE not in classify(_case(image_quality=0.41))
        assert FailureMode.POOR_IMAGE not in classify(_case(image_quality=None))

    def test_tag_driven_modes(self):
        assert FailureMode.NO_VISIBLE_TAG in classify(_case(tags=["no_tag"]))
        assert FailureMode.BACKGROUND_CLUTTER in classify(_case(tags=["cluttered"]))
        assert FailureMode.BACKGROUND_CLUTTER in classify(
            _case(tags=["background_clutter"]))

    def test_regional_pricing_needs_a_non_us_region(self):
        assert FailureMode.REGIONAL_PRICING in classify(_case(region="DE"))
        assert FailureMode.REGIONAL_PRICING not in classify(_case(region="US"))

    def test_insufficient_comps_only_applies_to_comps_valuations(self):
        assert FailureMode.INSUFFICIENT_COMPS in classify(
            _case(valuation_source="comps", comps_count=2))
        assert FailureMode.INSUFFICIENT_COMPS not in classify(
            _case(valuation_source="model", comps_count=2))

    def test_hallucination_is_a_specific_model_with_nothing_to_support_it(self):
        assert FailureMode.PROMPT_HALLUCINATION in classify(
            _case(predicted_model="Air Max 90", visual_evidence=[]))
        assert FailureMode.PROMPT_HALLUCINATION not in classify(
            _case(predicted_model="Air Max 90", visual_evidence=["swoosh", "tongue tag"]))

    def test_overconfident_requires_both_confidence_and_a_miss(self):
        assert FailureMode.OVERCONFIDENT in classify(
            _case(confidence=90, predicted=300.0, actual=100.0))
        # High confidence and a correct answer is not a failure mode.
        assert FailureMode.OVERCONFIDENT not in classify(
            _case(confidence=90, predicted=105.0, actual=100.0))

    def test_schema_violation(self):
        assert FailureMode.SCHEMA_VIOLATION in classify(_case(schema_valid=False))

    def test_every_mode_names_an_owner(self):
        """The most common way an error report fails is by producing findings
        nobody owns."""
        for mode in FailureMode:
            assert isinstance(mode.actionable_by, str) and mode.actionable_by


# ── Aggregation ──────────────────────────────────────────────────────────────

class TestAnalyse:
    def test_empty_input_is_an_empty_report_not_a_crash(self):
        report = analyse([])
        assert report.total_evaluated == 0
        assert report.by_mode == []

    def test_counts_separate_failures_from_the_evaluated_set(self):
        cases = [
            _case(item_id="ok", predicted=101.0, actual=100.0),        # 1%
            _case(item_id="bad", predicted=200.0, actual=100.0),       # 100%
            _case(item_id="worse", predicted=400.0, actual=100.0),     # 300%
        ]
        report = analyse(cases)
        assert report.total_evaluated == 3
        assert report.total_failures == 2
        assert report.severe_failures == 1
        assert report.failure_rate == pytest.approx(2 / 3)

    def test_worst_by_value_is_ordered_by_money_not_percentage(self):
        cheap = _case(item_id="cheap", predicted=12.0, actual=4.0)
        pricey = _case(item_id="pricey", predicted=840.0, actual=600.0)
        report = analyse([cheap, pricey])
        assert report.worst_by_value[0].item_id == "pricey"
        assert report.worst_by_percentage[0].item_id == "cheap"

    def test_report_renders_without_raising(self):
        assert "error analysis" in analyse([_case()]).render()

    def test_recurring_patterns_respects_min_support(self):
        cases = [_case(item_id=f"i{n}", tags=["no_tag"]) for n in range(4)]
        assert recurring_patterns(cases, min_support=3)
        assert recurring_patterns(cases, min_support=99) == []


# ── CLI ──────────────────────────────────────────────────────────────────────

def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCLI:
    def test_no_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            _run([])
        assert exc.value.code != 0

    def test_unknown_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit):
            _run(["nonsense"])

    def test_status_reports_honestly_with_no_gold_set(self):
        """The point of this subcommand: say what cannot be measured rather
        than print a number nothing supports."""
        code, out, err = _run(["status", "--gold", "does/not/exist.jsonl",
                               "--baseline", "does/not/exist.json"])
        assert code == 0
        status = json.loads(out)
        assert status["gold_dataset"]["exists"] is False
        assert status["can_measure_accuracy"] is False
        assert status["can_gate_regressions"] is False
        assert "No gold dataset" in err

    def test_dataset_reports_an_empty_gold_file(self, tmp_path):
        gold = tmp_path / "gold.jsonl"
        gold.write_text("")
        code, out, err = _run(["dataset", "--path", str(gold)])
        assert code == 0
        assert json.loads(out)["total"] == 0
        assert "more headline-eligible records needed" in err

    def test_drift_against_itself_is_clean(self, tmp_path):
        gold = tmp_path / "gold.jsonl"
        gold.write_text("")
        code, out, _ = _run(["drift", "--baseline", str(gold), "--candidate", str(gold)])
        assert code == 0
        assert json.loads(out)["clean"] is True

    def test_gate_skips_rather_than_failing_when_nothing_was_measured(self, tmp_path):
        current = tmp_path / "current.json"
        current.write_text(json.dumps({}))
        code, _, _ = _run(["gate", "--current", str(current)])
        assert code == 0

    def test_gate_can_be_told_to_fail_when_nothing_was_measured(self, tmp_path):
        """`--require-measurement` is what stops "no gold set" reading as a
        pass forever."""
        current = tmp_path / "current.json"
        current.write_text(json.dumps({}))
        code, _, _ = _run(["gate", "--current", str(current), "--require-measurement"])
        assert code != 0

    def test_gate_writes_its_json_when_asked(self, tmp_path):
        current = tmp_path / "current.json"
        current.write_text(json.dumps({}))
        out_path = tmp_path / "gate.json"
        _run(["gate", "--current", str(current), "--json-out", str(out_path)])
        assert out_path.exists()
        json.loads(out_path.read_text())        # parses

    def test_missing_required_argument_is_a_usage_error(self):
        with pytest.raises(SystemExit):
            _run(["dataset"])

    def test_calibrate_rejects_an_unknown_method(self):
        with pytest.raises(SystemExit):
            _run(["calibrate", "--examples", "x.json", "--method", "astrology"])
