"""`eval.cli field`: shared sale outcomes, reported and never gated (#224)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cli, field, gates  # noqa: E402


def record(sold: float, *, low=20.0, high=40.0, likely=30.0, currency="USD",
           band="Medium", prompt="v2", storefront="USA", category="clothing") -> dict:
    return {"estimate_low": low, "estimate_high": high, "likely": likely,
            "sold_price": sold, "currency": currency, "confidence_band": band,
            "prompt_version": prompt, "storefront": storefront, "category": category}


def write(tmp: Path, records: list[dict]) -> Path:
    path = tmp / "outcomes.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def test_figures_are_computed_by_group_and_labelled_user_reported(tmp_path):
    records = ([record(30) for _ in range(5)]                       # spot on
               + [record(60, prompt="v2.1") for _ in range(5)])      # 50% under
    rows, currencies = field.load(write(tmp_path, records))
    rep = field.report(rows, currencies)
    assert rep["basis"].startswith("user-reported sales, medium label confidence")
    assert rep["by"]["prompt_version"]["v2"]["mdape"] == 0.0
    assert rep["by"]["prompt_version"]["v2.1"]["mdape"] == 50.0
    assert rep["by"]["prompt_version"]["v2.1"]["bias"] < 0, "under-estimated"
    assert all(m["provenance"] == "measured" and m["basis"] == field.BASIS
               for m in rep["metrics"])


def test_only_usd_sales_are_scored_and_the_rest_are_counted(tmp_path):
    records = [record(30) for _ in range(5)] + [record(120, currency="RON") for _ in range(3)]
    rows, currencies = field.load(write(tmp_path, records))
    rep = field.report(rows, currencies)
    assert rep["scored_usd"] == 5
    assert rep["by_currency"] == {"RON": 3, "USD": 5}


def test_a_small_group_prints_its_n_and_no_figures(tmp_path):
    rows, currencies = field.load(write(tmp_path, [record(30) for _ in range(3)]))
    rep = field.report(rows, currencies)
    assert rep["overall"] == {"n": 3}
    assert "fewer than" in field.render(rep)


def test_a_record_without_likely_is_scored_on_the_midpoint_and_says_so(tmp_path):
    rows, currencies = field.load(write(tmp_path, [record(30, likely=None) for _ in range(5)]))
    assert field.report(rows, currencies)["point_source"] == {"midpoint": 5}


def test_the_reliability_table_is_by_band(tmp_path):
    records = ([record(30, band="High") for _ in range(5)]
               + [record(100, band="Low") for _ in range(5)])
    rows, currencies = field.load(write(tmp_path, records))
    table = field.report(rows, currencies)["reliability_by_band"]
    assert table["High"]["within_25pct"] == 100.0
    assert table["Low"]["within_25pct"] == 0.0


def test_the_cli_prints_and_writes_and_nothing_here_is_a_gate_metric(tmp_path):
    path = write(tmp_path, [record(30) for _ in range(5)])
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["field", "--outcomes", str(path), "--json-out", str(tmp_path / "f.json")])
    assert code == 0
    assert "Field outcomes" in out.getvalue() and "By prompt version" in out.getvalue()
    names = {m["name"] for m in json.loads((tmp_path / "f.json").read_text())["metrics"]}
    gated = {t.metric for t in gates.DEFAULT_THRESHOLDS}
    assert names and not names & gated
