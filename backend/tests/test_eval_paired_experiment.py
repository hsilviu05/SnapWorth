"""The runner's arms feed `eval.cli experiment` directly (#216).

The v2/v2.1 decision is a paired comparison per gold item. The runner wrote
aggregate reports only, and `eval.cli experiment` read per-item maps nobody
wrote, so the two halves of the decision had never met. These pipe
runner-shaped output through the CLI with no model calls: a candidate with
uniformly lower errors ships, uniformly higher is rejected, and a candidate
that stops declining negative controls is blocked whatever its accuracy.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cli, runner  # noqa: E402
from eval.experiment import ArmResult, Guardrail  # noqa: E402
from eval.provenance import Metric  # noqa: E402
from eval.runner import Prediction  # noqa: E402

N = 40


def prediction(item: str, price: float, *, actual: float | None = 100.0, ok: bool = True,
               latency: float = 1000.0, error: str | None = None, confidence: int = 60,
               tokens: int = 400) -> Prediction:
    return Prediction(item_id=item, category="clothing", expected_price=actual,
                      predicted_expected=price if ok else 0.0,
                      predicted_low=price * 0.8, predicted_high=price * 1.2,
                      confidence_score=confidence, latency_ms=latency,
                      output_tokens=tokens, thoughts_tokens=0,
                      visual_evidence=["tag"], error=error)


def arm(label: str, error_pct: float, *, decline: int = 5, controls: int = 5,
        config: dict | None = None) -> ArmResult:
    """N items sold at 100, each priced `error_pct` above it, with a spread so
    the errors are not all identical."""
    batch = [prediction(f"G-{i:04d}", 100 * (1 + (error_pct + i * 0.1) / 100))
             for i in range(N)]
    control_runs = [prediction(f"C-{i}", 0, actual=None, ok=False,
                               error="not_resalable" if i < decline else "no_price")
                    for i in range(controls)]
    return runner.arm_result(label, [batch], config or {"prompt_version": label,
                                                         "model": "gemini-2.5-flash"},
                             control_runs)


def write_compare(tmp: Path, a: ArmResult, b: ArmResult) -> Path:
    reports = {a.label: {"n_total": N}, b.label: {"n_total": N}}
    out = runner.json_out([a.label, b.label], reports, {a.label: a, b.label: b},
                          shift=None, source="gold.jsonl", labelled=True)
    path = tmp / "compare.json"
    path.write_text(json.dumps(out, default=str))
    return path


def run_cli(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


def verdict_of(tmp: Path, a: ArmResult, b: ArmResult) -> tuple[int, str]:
    result = tmp / "result.json"
    code, out, err = run_cli(["experiment", "--name", "v2-v2.1",
                              "--compare", str(write_compare(tmp, a, b)),
                              "--json-out", str(result)])
    assert result.exists(), out + err
    return code, json.loads(result.read_text())["verdict"]


def test_lower_errors_in_the_candidate_ship(tmp_path):
    code, verdict = verdict_of(tmp_path, arm("v2", 40), arm("v2.1", 10))
    assert verdict == "ship"
    assert code == 0


def test_higher_errors_in_the_candidate_are_rejected(tmp_path):
    # Under-valued, and with no confidence to calibrate, so only MdAPE moves.
    # Otherwise a guardrail stops it first: bias for over-valuation (next
    # test), calibration for the same confidence over worse prices.
    a = replace(arm("v2", -10), confidence={})
    b = replace(arm("v2.1", -40), confidence={})
    code, verdict = verdict_of(tmp_path, a, b)
    assert verdict == "reject"
    assert code == 1


def test_systematic_over_valuation_is_blocked_before_it_is_rejected(tmp_path):
    _, verdict = verdict_of(tmp_path, arm("v2", 10), arm("v2.1", 40))
    assert verdict == "blocked_by_guardrail"


def test_a_candidate_that_stops_declining_controls_is_blocked(tmp_path):
    _, verdict = verdict_of(tmp_path, arm("v2", 40), arm("v2.1", 10, decline=1))
    assert verdict == "blocked_by_guardrail"


def test_two_single_arm_files_work_too(tmp_path):
    a, b = arm("v2", 40), arm("v2.1", 10)
    paths = []
    for one in (a, b):
        out = runner.json_out([one.label], {one.label: {"n_total": N}}, {one.label: one},
                              shift=None, source="gold.jsonl", labelled=True)
        path = tmp_path / f"{one.label}.json"
        path.write_text(json.dumps(out, default=str))
        paths.append(path)
    code, out, err = run_cli(["experiment", "--baseline", str(paths[0]),
                              "--candidate", str(paths[1])])
    assert code == 0, out + err


def test_arms_run_under_different_models_are_refused(tmp_path):
    a = arm("v2", 40, config={"prompt_version": "v2", "model": "gemini-2.5-flash"})
    b = arm("v2.1", 10, config={"prompt_version": "v2.1", "model": "gemini-3-flash"})
    code, _, err = run_cli(["experiment", "--compare", str(write_compare(tmp_path, a, b))])
    assert code == 2 and "model" in err


def test_arm_result_takes_the_median_of_repeats_and_keys_by_item():
    repeats = [[prediction("G-1", 90), prediction("G-2", 0, ok=False, error="no_price")],
               [prediction("G-1", 110, latency=3000), prediction("G-2", 0, ok=False)],
               [prediction("G-1", 100)]]
    result = runner.arm_result("v2", repeats)
    assert result.predicted == {"G-1": 100}
    assert result.absolute_percentage_error == {"G-1": 0.0}
    assert result.latency_ms == {"G-1": 1000.0}
    assert result.failures == 1, "an item with no priced repeat is a failure"
    assert result.extra["scored_fraction"]["value"] == 50.0


def test_negative_controls_give_a_decline_rate():
    controls = [prediction("C-1", 0, actual=None, ok=False, error="not_resalable"),
                prediction("C-2", 0, actual=None, ok=False, error="not_resalable"),
                prediction("C-3", 0, actual=None, ok=False, error="no_price"),
                prediction("C-4", 12, actual=None)]
    result = runner.arm_result("v2", [[prediction("G-1", 100)]], controls=controls)
    assert result.extra["decline_rate"] == {"value": 50.0, "n": 4, "unit": "%"}


def test_an_arm_survives_the_round_trip():
    original = arm("v2.1", 10)
    again = ArmResult.from_dict(json.loads(json.dumps(original.to_dict())))
    assert again.metric_set().get("mdape").value == original.metric_set().get("mdape").value
    assert again.extra == original.extra


def test_a_drop_is_the_regression_for_higher_is_better_metrics():
    guard = Guardrail("scored_fraction", 0.05, higher_is_better=True)
    assert guard.check(Metric.measured("scored_fraction", 95, 40),
                       Metric.measured("scored_fraction", 80, 40))
    assert guard.check(Metric.measured("scored_fraction", 95, 40),
                       Metric.measured("scored_fraction", 99, 40)) is None
