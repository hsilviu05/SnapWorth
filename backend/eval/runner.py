"""Evaluation harness entry point.

Runs a benchmark set through the valuation pipeline and reports accuracy,
calibration, consistency, hallucination rate and latency.

    python -m eval.runner --dataset eval/data/benchmark.jsonl
    python -m eval.runner --dataset ... --prompt-version v1   # baseline
    python -m eval.runner --dataset ... --repeats 3           # consistency
    python -m eval.runner --dataset ... --compare v1 v2       # A/B

Design notes
------------
The harness talks to the same `valuation`/`confidence` modules the API uses, so
it measures the shipping pipeline rather than a parallel reimplementation that
can silently drift.

`--dry-run` scores a dataset against recorded predictions with no model calls at
all, which is what makes the harness itself unit-testable in CI without an API
key. A benchmark you cannot test is a benchmark you will not trust.

Cost control matters: 500 items × 2 prompts × 3 repeats is 3,000 vision calls.
`--limit` and `--categories` exist so iteration happens on a cheap subset and
the full run is deliberate.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import confidence as confidence_module  # noqa: E402
import imagequality  # noqa: E402
import promptsafety  # noqa: E402
import prompts  # noqa: E402
import valuation as valuation_module  # noqa: E402
from eval import dataset as dataset_module  # noqa: E402
from eval import metrics  # noqa: E402

log = logging.getLogger("snapworth.eval")


@dataclass
class Prediction:
    """One model run against one benchmark item."""
    item_id: str
    category: str
    expected_price: float
    expected_brand: str | None = None
    predicted_expected: float = 0.0
    predicted_low: float = 0.0
    predicted_high: float = 0.0
    confidence_score: int = 0
    brand: str | None = None
    model_name: str | None = None
    identification_certainty: str | None = None
    visual_evidence: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    prompt_version: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.predicted_expected > 0


def evaluate(predictions: list[Prediction]) -> dict:
    """Compute the full metric set from predictions. Pure — no model calls."""
    usable = [p for p in predictions if p.ok]
    failed = [p for p in predictions if not p.ok]

    point_pairs = [(p.predicted_expected, p.expected_price) for p in usable]
    range_triples = [(p.predicted_low, p.predicted_high, p.expected_price) for p in usable]
    scored = [(p.confidence_score, p.predicted_expected, p.expected_price) for p in usable]

    hallucination_records = [
        {
            "model_name": p.model_name,
            "brand": p.brand,
            "expected_brand": p.expected_brand,
            "identification_certainty": p.identification_certainty,
            "visual_evidence": p.visual_evidence,
        }
        for p in usable
    ]

    cal = metrics.calibration(scored)

    by_category: dict[str, dict] = {}
    for category in sorted({p.category for p in usable}):
        subset = [(p.predicted_expected, p.expected_price)
                  for p in usable if p.category == category]
        by_category[category] = {
            "n": len(subset),
            "mdape": metrics.mdape(subset),
            "within_25pct": metrics.within_tolerance(subset),
        }

    return {
        "n_total": len(predictions),
        "n_scored": len(usable),
        "n_failed": len(failed),
        "accuracy": {
            "mdape": metrics.mdape(point_pairs),
            "mape": metrics.mape(point_pairs),
            "within_10pct": metrics.within_tolerance(point_pairs, 10.0),
            "within_25pct": metrics.within_tolerance(point_pairs, 25.0),
            "within_50pct": metrics.within_tolerance(point_pairs, 50.0),
        },
        "range": {
            "coverage": metrics.range_coverage(range_triples),
            "mean_width_ratio": metrics.mean_range_width(range_triples),
        },
        "calibration": {"ece": cal.ece, "buckets": cal.as_table()},
        "hallucination": metrics.hallucination_rate(hallucination_records),
        "latency_ms": metrics.latency_summary([p.latency_ms for p in usable]),
        "by_category": by_category,
    }


def evaluate_consistency(runs: dict[str, list[float]]) -> dict:
    """Consistency across repeats, keyed by item id."""
    return metrics.consistency(list(runs.values()))


# ── Live evaluation ──────────────────────────────────────────────────────────

async def _predict_one(model, item, prompt_text: str, version: str, root: Path) -> Prediction:
    """Run one item through the real pipeline."""
    import aiconfig

    prediction = Prediction(
        item_id=item.id, category=item.category,
        expected_price=item.actual_sale_price_usd, expected_brand=item.brand,
        prompt_version=version,
    )

    image_path = (root / item.image_path).resolve()
    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        prediction.error = f"image unreadable: {exc}"
        return prediction

    quality = imagequality.analyse(image_bytes)
    part = {"mime_type": "image/jpeg",
            "data": base64.standard_b64encode(image_bytes).decode()}

    started = time.monotonic()
    try:
        response = await model.generate_content_async([prompt_text, part])
        raw = aiconfig.extract_text(response)
    except Exception as exc:
        prediction.error = str(exc)[:200]
        prediction.latency_ms = (time.monotonic() - started) * 1000
        return prediction
    prediction.latency_ms = (time.monotonic() - started) * 1000

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        from main import _extract_json
        try:
            data = _extract_json(raw)
        except Exception as exc:
            prediction.error = f"unparseable: {exc}"
            return prediction

    val = valuation_module.normalise(data, image_quality=quality)
    low, high, clamped = promptsafety.clamp_valuation(
        val.prices.worst or 1.0, val.prices.best or 5.0, val.category)
    conf = confidence_module.compute(
        brand=val.brand, category=val.category,
        identification_certainty=val.identification_certainty,
        authenticity=val.authenticity, demand=val.demand, supply=val.supply,
        value_low=low, value_high=high, image_quality=quality, was_clamped=clamped,
        model_field_count=valuation_module.count_present_fields(data),
        expected_field_count=len(valuation_module.EXPECTED_OPTIONAL_FIELDS),
    )

    prediction.predicted_expected = val.prices.expected
    prediction.predicted_low = low
    prediction.predicted_high = high
    prediction.confidence_score = conf.score
    prediction.brand = val.brand
    prediction.model_name = val.model
    prediction.identification_certainty = val.identification_certainty
    prediction.visual_evidence = val.visual_evidence
    return prediction


async def run_live(items, version: str, root: Path, concurrency: int = 4) -> list[Prediction]:
    import aiconfig

    prompt_text, resolved = prompts.get_prompt(version)
    model = aiconfig.build_model()
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item):
        async with semaphore:
            return await _predict_one(model, item, prompt_text, resolved, root)

    return list(await asyncio.gather(*(guarded(i) for i in items)))


def _format(report: dict) -> str:
    acc, rng = report["accuracy"], report["range"]

    def pct(value):
        return "n/a" if value is None else f"{value * 100:.1f}%"

    def num(value):
        return "n/a" if value is None else f"{value:.1f}"

    lines = [
        "",
        "═══ SnapWorth valuation evaluation ═══",
        f"scored {report['n_scored']}/{report['n_total']}  (failed: {report['n_failed']})",
        "",
        "Accuracy",
        f"  MdAPE (headline)      {num(acc['mdape'])}%",
        f"  MAPE  (tail-exposed)  {num(acc['mape'])}%",
        f"  within 10%            {pct(acc['within_10pct'])}",
        f"  within 25%            {pct(acc['within_25pct'])}",
        f"  within 50%            {pct(acc['within_50pct'])}",
        "",
        "Range",
        f"  coverage              {pct(rng['coverage'])}   (target ~80%)",
        f"  mean width ratio      {num(rng['mean_width_ratio'])}×  (lower is better)",
        "",
        "Calibration",
        f"  ECE                   {report['calibration']['ece']:.3f}   (0 = perfect)",
    ]
    for bucket in report["calibration"]["buckets"]:
        lines.append(
            f"    conf {bucket['range']:>7}  n={bucket['n']:<4} "
            f"claimed={bucket['predicted']:.2f}  actual={bucket['actual']:.2f}"
        )

    hall = report["hallucination"]
    lines += [
        "",
        "Hallucination",
        f"  flagged rate          {pct(hall.get('rate'))}",
        f"  reasons               {hall.get('reasons') or '—'}",
        "",
        "Latency (ms)",
        f"  p50 {num(report['latency_ms']['p50'])}   "
        f"p95 {num(report['latency_ms']['p95'])}   "
        f"p99 {num(report['latency_ms']['p99'])}",
        "",
        "By category",
    ]
    for category, stats in report["by_category"].items():
        lines.append(
            f"  {category:<14} n={stats['n']:<4} "
            f"MdAPE={num(stats['mdape'])}%  within25={pct(stats['within_25pct'])}"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate SnapWorth valuations")
    parser.add_argument("--dataset", required=True, help="path to a JSONL benchmark")
    parser.add_argument("--prompt-version", default=prompts.DEFAULT_PROMPT_VERSION)
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="run two prompt versions and print both reports")
    parser.add_argument("--repeats", type=int, default=1,
                        help="runs per item; >1 enables the consistency metric")
    parser.add_argument("--limit", type=int, help="evaluate only the first N items")
    parser.add_argument("--categories", help="comma-separated category filter")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--json-out", help="write the raw report to this path")
    parser.add_argument("--coverage-only", action="store_true",
                        help="report dataset composition and exit — no model calls")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dataset_path = Path(args.dataset)
    items = dataset_module.load(dataset_path)
    root = dataset_path.parent

    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",")}
        items = [i for i in items if i.category in wanted]
    items = [i for i in items if i.is_scoreable]
    if args.limit:
        items = items[: args.limit]

    coverage = dataset_module.coverage_report(items)
    print(json.dumps(coverage, indent=2))
    if args.coverage_only:
        return 0
    if not items:
        print("no scoreable items — nothing to evaluate")
        return 1

    versions = args.compare if args.compare else [args.prompt_version]
    reports: dict[str, dict] = {}

    for version in versions:
        all_runs: dict[str, list[float]] = {}
        predictions: list[Prediction] = []
        for repeat in range(max(1, args.repeats)):
            batch = asyncio.run(run_live(items, version, root, args.concurrency))
            if repeat == 0:
                predictions = batch
            for prediction in batch:
                all_runs.setdefault(prediction.item_id, []).append(prediction.predicted_expected)

        report = evaluate(predictions)
        if args.repeats > 1:
            report["consistency"] = evaluate_consistency(all_runs)
        reports[version] = report

        print(f"\n### prompt {version}")
        print(_format(report))
        if "consistency" in report:
            c = report["consistency"]
            mean_cv = "n/a" if c["mean_cv"] is None else f"{c['mean_cv']:.3f}"
            worst_cv = "n/a" if c["worst_cv"] is None else f"{c['worst_cv']:.3f}"
            print(f"Consistency over {args.repeats} runs\n"
                  f"  mean CV  {mean_cv}   worst CV  {worst_cv}   (lower is better)\n")

    if args.compare:
        a, b = args.compare
        left = reports[a]["accuracy"]["mdape"]
        right = reports[b]["accuracy"]["mdape"]
        if left and right:
            delta = (left - right) / left * 100
            print(f"\nMdAPE {a}={left:.1f}%  {b}={right:.1f}%  "
                  f"→ {b} is {delta:+.1f}% better\n")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(reports, indent=2, default=str))
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
