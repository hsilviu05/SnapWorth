"""Converting sales to US dollars for scoring (#225).

Pinned against the committed ECB table, so a refresh that moved a published
rate — or a conversion that drifted to today's rate — fails here.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import fx, runner  # noqa: E402

RATES = fx.load()


def test_a_lei_sale_converts_at_its_sale_date():
    # 2025-12-31: 1.175 USD and 5.0968 RON per euro.
    assert RATES.to_usd(120, "RON", date(2025, 12, 31)) == pytest.approx(120 * 1.175 / 5.0968)
    assert round(RATES.to_usd(120, "RON", date(2025, 12, 31)), 2) == 27.66


def test_a_weekend_sale_uses_the_last_business_day():
    # 2026-01-03 is a Saturday; the last fixing before it is 2026-01-02.
    assert RATES.to_usd(100, "EUR", date(2026, 1, 3)) == pytest.approx(117.21)


def test_euro_and_dollar_need_no_table():
    assert RATES.to_usd(50, "USD", None) == 50
    assert RATES.to_usd(10, "EUR", date(2025, 12, 31)) == pytest.approx(11.75)


def test_no_date_is_never_converted_at_todays_rate():
    with pytest.raises(fx.FxUnavailable, match="never converted at today's rate"):
        RATES.to_usd(120, "RON", None)


def test_a_date_outside_the_table_is_refused():
    with pytest.raises(fx.FxUnavailable):
        RATES.to_usd(120, "RON", date(2023, 6, 1))


def test_the_lev_converts_at_its_fixed_rate_after_the_euro():
    # The ECB stopped publishing BGN when Bulgaria adopted the euro.
    assert RATES.to_usd(195.583, "BGN", date(2026, 3, 2)) == pytest.approx(100 * RATES.per_euro("USD", date(2026, 3, 2))[0])


def test_the_table_version_names_the_file_and_its_hash():
    assert RATES.version.startswith("ecb-2026-10-06.csv@") and len(RATES.version.split("@")[1]) == 12


# ── In the runner ────────────────────────────────────────────────────────────

def _gold(**overrides) -> dict:
    record = {"id": "g1", "images": [{"path": "images/g1.jpg", "is_primary": True}],
              "actual_sale_price": 42.0, "currency": "USD", "category": "clothing",
              "label_confidence": "certain", "evidence_note": "own sale",
              "review_state": "approved", "reviewed_by": "operator",
              "difficulty": "typical", "region": "US"}
    record.update(overrides)
    return record


def test_the_runner_scores_a_lei_sale_in_dollars_and_names_every_exclusion(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        _gold(id="us"),
        _gold(id="ro", actual_sale_price=120.0, currency="RON", region="RO",
              sold_date="2025-12-31"),
        _gold(id="nodate", actual_sale_price=120.0, currency="RON", region="RO"),
    ]) + "\n")
    items, excluded = runner.load_items(path)
    by_id = {i.id: i for i in items}
    assert set(by_id) == {"us", "ro"}, "no record is excluded for its currency"
    assert by_id["ro"].expected_price == 27.66 and by_id["ro"].region == "RO"
    assert list(excluded) == ["sold in RON: no sale date: a sale is never converted at today's rate"]


def _prediction(region: str, predicted: float, actual: float) -> runner.Prediction:
    return runner.Prediction(item_id=f"{region}-{actual}-{predicted}", category="clothing",
                             expected_price=actual, predicted_expected=predicted,
                             predicted_low=predicted * 0.8, predicted_high=predicted * 1.2,
                             confidence_score=60, region=region)


def test_run_json_breaks_accuracy_down_by_region():
    predictions = ([_prediction("US", 30, 30) for _ in range(12)]
                   + [_prediction("RO", 36, 30) for _ in range(3)])
    report = runner.evaluate(predictions)
    us, ro = report["by_region"]["US"], report["by_region"]["RO"]
    assert (us["n"], us["mdape"], us["bias"], us["too_small"]) == (12, 0.0, 0.0, False)
    assert ro["n"] == 3 and ro["bias"] == pytest.approx(20.0) and ro["too_small"] is True
    assert {"within_25pct", "range_coverage"} <= set(us)
    assert report["fx_table"] == RATES.version
    assert "too small to read" in runner._format(report)
