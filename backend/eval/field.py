"""Field outcomes: user-reported sales against their scan-time estimates (#224).

    python -m eval.cli field --outcomes outcomes.jsonl

The input is `outcomes.export_jsonl()` (the bot's `/outcomes export`): sold
flips users chose to share, each with the estimate they saw at scan time.

**Not the gold set, and never a gate input.** A typed sale price has no
receipt behind it, so it is `medium` label confidence at best
(`eval/schema.py`): every figure here carries that basis, and nothing in the
gate reads this report. What it adds is volume, by prompt version, storefront,
category and confidence band, which per-region measurement (#225) and
calibration (#226) need.

**Every sale is scored in US dollars.** The estimates are in dollars, so a
lei or euro sale is converted at its sale day from the pinned ECB table
(`eval/fx.py`, #225), as the runner converts gold sales. A sale the table
cannot convert is counted, with the reason, never scored at today's rate.
Every record is also counted by currency, and grouped by storefront: bias
per storefront is the field's view of which market the estimates price for.

The point estimate is `likely` (the model's expected price after the server's
bounds, what the app shows) and, where a record has none, the middle of the
range, reported apart so the two are never blended unknowingly.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from datetime import date

from eval import fx, metrics

BASIS = "user-reported sales, medium label confidence (not the gold set)"
GROUPS = ("prompt_version", "storefront", "category", "confidence_band")
#: A group smaller than this prints its n and no figures: a median of three
#: sales is an anecdote.
MIN_GROUP = 5


@dataclass(frozen=True)
class Row:
    point: float          # the estimate the user was shown
    point_source: str     # "likely" or "midpoint"
    low: float
    high: float
    sold: float
    keys: dict[str, str]


def load(path: str | Path) -> tuple[list[Row], Counter]:
    """Scoreable rows in USD, and every record counted by currency; a record
    the rate table cannot convert is counted under "unconverted <CUR>"."""
    rows: list[Row] = []
    currencies: Counter = Counter()
    rates = fx.load()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        currency = record.get("currency", "?")
        currencies[currency] += 1
        try:
            day = date.fromisoformat(record["sold_day"]) if record.get("sold_day") else None
            sold = rates.to_usd(float(record["sold_price"]), currency, day)
        except (fx.FxUnavailable, ValueError):
            currencies[f"unconverted {currency}"] += 1
            continue
        low, high = float(record["estimate_low"]), float(record["estimate_high"])
        likely = record.get("likely")
        point, source = ((float(likely), "likely") if likely
                         else ((low + high) / 2, "midpoint"))
        rows.append(Row(point, source, low, high, sold,
                        {g: str(record.get(g) or "unknown") for g in GROUPS}))
    return rows, currencies


def summarise(rows: list[Row]) -> dict:
    """MdAPE, bias, within 25% and range coverage over `rows`, with n."""
    pairs = [(r.point, r.sold) for r in rows]
    triples = [(r.low, r.high, r.sold) for r in rows]
    n = len(rows)
    if n < MIN_GROUP:
        return {"n": n}
    within = metrics.within_tolerance(pairs, 25.0)
    coverage = metrics.range_coverage(triples)
    return {
        "n": n,
        "mdape": metrics.mdape(pairs),
        "bias": metrics.bias(pairs),
        "within_25pct": within * 100 if within is not None else None,
        "range_coverage": coverage * 100 if coverage is not None else None,
    }


def reliability(rows: list[Row]) -> dict[str, dict]:
    """Per confidence band: how often the sale landed within 25% of the
    estimate. A band that claims more than it delivers is what #226 fixes."""
    out: dict[str, dict] = {}
    for band in sorted({r.keys["confidence_band"] for r in rows}):
        subset = [r for r in rows if r.keys["confidence_band"] == band]
        within = metrics.within_tolerance([(r.point, r.sold) for r in subset], 25.0)
        out[band] = {"n": len(subset),
                     "within_25pct": (within * 100 if within is not None
                                      and len(subset) >= MIN_GROUP else None)}
    return out


def report(rows: list[Row], currencies: Counter) -> dict:
    by: dict[str, dict] = {}
    for group in GROUPS:
        by[group] = {value: summarise([r for r in rows if r.keys[group] == value])
                     for value in sorted({r.keys[group] for r in rows})}
    overall = summarise(rows)
    # In the shape the dashboard reads, tagged measured with their basis;
    # never written where `eval.cli gate` looks.
    measured = [{"name": f"field_{name}", "value": overall[name], "unit": "%",
                 "provenance": "measured", "sample_size": overall["n"], "basis": BASIS}
                for name in ("mdape", "bias", "within_25pct", "range_coverage")
                if overall.get(name) is not None]
    return {
        "basis": BASIS,
        "scored_usd": len(rows),
        "fx_table": fx.load().version,
        "by_currency": dict(sorted(currencies.items())),
        "point_source": dict(Counter(r.point_source for r in rows)),
        "overall": overall,
        "by": by,
        "reliability_by_band": reliability(rows),
        "metrics": measured,
    }


def render(rep: dict) -> str:
    def num(v):
        return "—" if v is None else f"{v:.1f}"
    lines = [f"Field outcomes — {rep['basis']}",
             f"Scored: {rep['scored_usd']} sales in USD ({rep['fx_table']}) · by currency: "
             f"{rep['by_currency']} · point estimate: {rep['point_source']}", ""]
    o = rep["overall"]
    lines.append(f"Overall  n={o['n']}  MdAPE {num(o.get('mdape'))}%  bias {num(o.get('bias'))}%  "
                 f"within 25% {num(o.get('within_25pct'))}%  in range {num(o.get('range_coverage'))}%")
    for group, values in rep["by"].items():
        lines += ["", f"By {group.replace('_', ' ')}"]
        for value, s in values.items():
            if "mdape" in s:
                lines.append(f"  {value:<14} n={s['n']:<4} MdAPE {num(s['mdape'])}%  "
                             f"bias {num(s['bias'])}%  within 25% {num(s['within_25pct'])}%")
            else:
                lines.append(f"  {value:<14} n={s['n']:<4} (fewer than {MIN_GROUP}: no figures)")
    lines += ["", "Reliability by confidence band (share within 25%)"]
    for band, s in rep["reliability_by_band"].items():
        lines.append(f"  {band:<10} n={s['n']:<4} {num(s['within_25pct'])}%")
    return "\n".join(lines) + "\n"

