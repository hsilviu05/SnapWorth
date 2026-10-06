"""The accuracy gate's wiring (#215), with no model calls.

The gate only means something if what it compares is comparable and what it
scores is what production scores. These pin the pieces that make that true:
a baseline recorded under another config is refused, the run is pinned to
production's config, repeats can be scored as their median, the midpoint is
reported beside the expected price, composition drift warns while a label
edit fails, and a gold photo that is missing or altered stops the run.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cli, intake, runner, schema  # noqa: E402
from eval.runner import Prediction  # noqa: E402
from tests.test_eval_intake import fake_jpeg  # noqa: E402

PRODUCTION = Path(__file__).resolve().parents[1] / "eval" / "data" / "production.json"


def run_cli(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


def gold_record(item_id: str, price: float, category: str = "shoes", **extra) -> dict:
    return {"id": item_id, "images": [{"path": f"images/{item_id}-front.jpg",
                                       "is_primary": True, "sha256": "0" * 64}],
            "actual_sale_price": price, "currency": "USD", "category": category,
            "label_confidence": "certain", "evidence_note": "private:r",
            "review_state": "approved", "reviewed_by": "o", **extra}


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# ── Config: pinned, recorded, compared ───────────────────────────────────────

def test_production_json_names_what_production_runs():
    pinned = runner.load_config(PRODUCTION)
    assert set(pinned) == {"prompt_version", "model", "thinking_budget"}
    import prompts
    # The code defaults, which is what Railway runs while the three variables
    # are unset (checked 2026-10-06). A default change must move this too.
    assert pinned["prompt_version"] == prompts.DEFAULT_PROMPT_VERSION
    assert pinned["thinking_budget"] is None
    assert pinned["model"] == "gemini-2.5-flash"


def test_the_pinned_model_is_aiconfigs_default():
    """With GEMINI_MODEL unset, as on Railway, aiconfig runs its default, and
    that is what the file must pin. Read from the source: reloading aiconfig
    would swap classes other tests hold."""
    import re
    source = (Path(__file__).resolve().parents[1] / "aiconfig.py").read_text()
    default = re.search(r'MODEL_NAME = os\.environ\.get\("GEMINI_MODEL", "([^"]+)"\)', source)
    assert default, "aiconfig.MODEL_NAME no longer reads GEMINI_MODEL with a default"
    assert default.group(1) == runner.load_config(PRODUCTION)["model"]


def gate_files(tmp_path: Path, current_config: dict | None, baseline_config: dict | None):
    metrics = {"mdape": {"value": 30.0, "provenance": "measured", "unit": "%",
                         "sample_size": 60}}
    current = {"metrics": metrics, **({"config": current_config} if current_config else {})}
    baseline = {"metrics": metrics, **({"config": baseline_config} if baseline_config else {})}
    (tmp_path / "run.json").write_text(json.dumps(current))
    (tmp_path / "baseline.json").write_text(json.dumps(baseline))
    return str(tmp_path / "run.json"), str(tmp_path / "baseline.json")


V2 = {"prompt_version": "v2", "model": "gemini-2.5-flash", "thinking_budget": None}


def test_the_gate_refuses_a_baseline_from_another_config(tmp_path):
    current, baseline = gate_files(tmp_path, V2 | {"prompt_version": "v2.1"}, V2)
    code, _, err = run_cli(["gate", "--current", current, "--baseline", baseline])
    assert code == 1
    assert "prompt_version 'v2' → 'v2.1'" in err and "Re-record" in err


def test_the_gate_refuses_a_baseline_with_no_recorded_config(tmp_path):
    current, baseline = gate_files(tmp_path, V2, None)
    code, _, err = run_cli(["gate", "--current", current, "--baseline", baseline])
    assert code == 1 and "records no config" in err


def test_the_gate_compares_runs_under_one_config(tmp_path):
    current, baseline = gate_files(tmp_path, V2, V2)
    code, out, err = run_cli(["gate", "--current", current, "--baseline", baseline])
    assert "different configs" not in err
    assert code == 0, out + err


def test_a_single_arm_run_records_its_config_at_the_top():
    arm = runner.arm_result("v2", [[]], {"prompt_version": "v2", "model": "m",
                                         "thinking_budget": None})
    out = runner.json_out(["v2"], {"v2": {"n_total": 0}}, {"v2": arm},
                          shift=None, source="gold.jsonl", labelled=True)
    assert out["config"] == {"prompt_version": "v2", "model": "m", "thinking_budget": None}


def test_config_cannot_be_combined_with_compare():
    with pytest.raises(SystemExit):
        runner.main(["--dataset", "x.jsonl", "--config", str(PRODUCTION),
                     "--compare", "v2", "v2.1"])


def test_config_refuses_a_different_model(monkeypatch, capsys):
    import aiconfig
    monkeypatch.setattr(aiconfig, "MODEL_NAME", "gemini-3-flash")
    with pytest.raises(SystemExit):
        runner.main(["--dataset", "x.jsonl", "--config", str(PRODUCTION)])
    assert "pins model 'gemini-2.5-flash'" in capsys.readouterr().err


# ── Scoring ──────────────────────────────────────────────────────────────────

def p(item: str, price: float, low: float, high: float, *, ok: bool = True) -> Prediction:
    return Prediction(item_id=item, category="shoes", expected_price=100.0,
                      predicted_expected=price if ok else 0.0,
                      predicted_low=low, predicted_high=high, confidence_score=60,
                      latency_ms=1000.0, error=None if ok else "no_price")


def test_median_aggregation_scores_each_items_middle_repeat():
    repeats = [[p("A", 80, 60, 100), p("B", 0, 0, 0, ok=False)],
               [p("A", 200, 150, 250), p("B", 0, 0, 0, ok=False)],
               [p("A", 110, 90, 130), p("B", 0, 0, 0, ok=False)]]
    merged = {x.item_id: x for x in runner.median_predictions(repeats)}
    assert merged["A"].predicted_expected == 110
    assert (merged["A"].predicted_low, merged["A"].predicted_high) == (90, 130)
    assert not merged["B"].ok, "an item with no priced repeat stays a failure"


def test_the_midpoint_is_reported_beside_the_expected_price():
    # Expected 120 against 100 sold; the range 60–100 has its middle at 80.
    report = runner.evaluate([p("A", 120, 60, 100), p("B", 120, 60, 100)])
    assert report["accuracy"]["mdape"] == pytest.approx(20.0)
    assert report["accuracy_midpoint"]["mdape"] == pytest.approx(20.0)
    assert report["accuracy_midpoint"]["bias"] == pytest.approx(-20.0)
    names = {m for m in runner.metric_set(report, "run").to_dict()["metrics"]}
    assert {"mdape_midpoint", "bias_midpoint"} <= names


def test_the_midpoint_metrics_are_never_gated():
    from eval import gates
    gated = {t.metric for t in gates.DEFAULT_THRESHOLDS}
    assert not gated & {"mdape_midpoint", "bias_midpoint"}


# ── Drift ────────────────────────────────────────────────────────────────────

def test_composition_drift_warns_and_passes(tmp_path):
    base = write_jsonl(tmp_path / "base.jsonl", [gold_record(f"G-{i:04d}", 50) for i in range(10)])
    grown = write_jsonl(tmp_path / "grown.jsonl",
                        [gold_record(f"G-{i:04d}", 50) for i in range(10)]
                        + [gold_record(f"G-{i:04d}", 50, "bags") for i in range(10, 20)])
    code, out, _ = run_cli(["drift", "--baseline", str(base), "--candidate", str(grown)])
    assert code == 0
    assert "::warning::gold set composition:" in out


def test_an_edited_label_still_fails(tmp_path):
    base = write_jsonl(tmp_path / "base.jsonl", [gold_record("G-0001", 50)])
    edited = write_jsonl(tmp_path / "edited.jsonl", [gold_record("G-0001", 65)])
    code, _, err = run_cli(["drift", "--baseline", str(base), "--candidate", str(edited)])
    assert code == 1 and "Ground truth changed" in err


# ── Photos ───────────────────────────────────────────────────────────────────

def photo_store(tmp_path: Path, data: bytes, record_sha: str | None = None) -> Path:
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "G-0001-front.jpg").write_bytes(data)
    record = gold_record("G-0001", 50)
    record["images"][0]["sha256"] = record_sha if record_sha is not None else \
        intake.hashlib.sha256(data).hexdigest()
    return write_jsonl(tmp_path / "gold.jsonl", [record])


def test_images_pass_when_present_and_matching(tmp_path):
    gold = photo_store(tmp_path, fake_jpeg(1024, 768))
    code, out, _ = run_cli(["images", "--path", str(gold)])
    assert code == 0 and "1 image(s) checked, 0 problem(s)" in out


@pytest.mark.parametrize("data, sha, says", [
    (fake_jpeg(1024, 768), "f" * 64, "does not match its sha256"),
    (fake_jpeg(4032, 3024), None, "over the 1568 px"),
    (b"HEIC....", None, "is not a JPEG"),
])
def test_images_fail_when_altered_oversized_or_not_jpeg(tmp_path, data, sha, says):
    gold = photo_store(tmp_path, data, sha)
    code, out, _ = run_cli(["images", "--path", str(gold), "--annotate"])
    assert code == 1 and says in out and "::error::" in out


def test_images_fail_when_missing(tmp_path):
    gold = write_jsonl(tmp_path / "gold.jsonl", [gold_record("G-0001", 50)])
    code, out, _ = run_cli(["images", "--path", str(gold)])
    assert code == 1 and "is not in the store" in out


def test_the_template_set_is_still_unscoreable():
    """The shipped template must never become something the gate measures."""
    template = Path(__file__).resolve().parents[1] / "eval" / "data" / "gold.template.jsonl"
    assert not any(i.is_scoreable for i in schema.load_gold(template))
