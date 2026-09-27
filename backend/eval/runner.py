"""Evaluation harness entry point.

Runs a benchmark set through the valuation pipeline and reports accuracy,
calibration, consistency, hallucination rate and latency.

    python -m eval.runner --dataset eval/data/gold.jsonl
    python -m eval.runner --dataset ... --prompt-version v1   # baseline
    python -m eval.runner --dataset ... --repeats 3           # consistency
    python -m eval.runner --dataset ... --compare v1 v2       # A/B
    python -m eval.runner --photos ~/scans --repeats 3 --compare v2 v2@512
                                   # no labels: consistency, latency, tokens,
                                   # and how far the second arm moves prices

`--dataset` reads either schema: gold-v2 (`schema.load_gold`, what
`eval/data/gold.jsonl` and CI use) or the v1 benchmark format
(`dataset.load`). `--json-out` writes the metric shape `eval.cli gate` reads,
so a run's own output is also a valid baseline file.

An arm is a prompt version, optionally with `@N` to cap the model's thinking
budget at N tokens for that arm only (`aiconfig.THINKING_BUDGET`).

Design notes
------------
The harness talks to the same `valuation`/`confidence` modules the API uses, so
it measures the shipping pipeline rather than a parallel reimplementation that
can silently drift.

`evaluate` and `metric_set` are pure — they score predictions with no model
calls at all, which is what makes the harness itself unit-testable in CI
without an API key. A benchmark you cannot test is a benchmark you will not
trust.

Photos without a sale price still answer real questions. Consistency across
repeats, latency and token spend need no ground truth, and neither does "how
far does the second arm move prices from the first" — the measurement a
thinking-budget cap needs before anyone ships it. `--photos` runs a folder of
JPEGs that way. It never produces an accuracy figure, because there is nothing
to be accurate against.

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
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import confidence as confidence_module  # noqa: E402
import imagequality  # noqa: E402
import prompts  # noqa: E402
import valuation as valuation_module  # noqa: E402
from eval import dataset as dataset_module  # noqa: E402
from eval import metrics  # noqa: E402
from eval import schema  # noqa: E402
from eval.provenance import Metric, MetricSet  # noqa: E402

log = logging.getLogger("snapworth.eval")


# The pipeline is sent `image/jpeg` (see `_predict_one`), so an unlabelled
# folder is read for JPEGs only rather than mislabelling anything else.
PHOTO_SUFFIXES = (".jpg", ".jpeg")


@dataclass(frozen=True)
class EvalItem:
    """One photo to run, and the sale price it is scored against, if any.

    The runner's own view of an item, so the gold-v2 loader, the v1 loader and
    a folder of unlabelled photos all feed one pipeline.
    """
    id: str
    category: str
    image_path: str                       # relative to the dataset's folder
    expected_price: float | None = None   # USD; None when unlabelled
    expected_brand: str | None = None


def _is_gold(path: Path) -> bool:
    """Whether a JSONL file is gold-v2 rather than v1, by its first record.

    The two schemas disagree on exactly the fields the runner needs — gold-v2
    has `images` and `actual_sale_price` with a `currency`, v1 has `image_path`
    and `actual_sale_price_usd`. This runner only ever read v1, so a real gold
    set had every record rejected as "missing required fields" and the run
    ended in "no scoreable items": CI either failed on every backend PR or,
    had it got further, gated on nothing.
    """
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            return isinstance(record, dict) and "images" in record
    return False


def load_items(path: Path) -> tuple[list[EvalItem], dict[str, int]]:
    """The labelled, scoreable items in a benchmark file, and what was left
    out and why — so an exclusion is a line in the output, not a silent gap.
    """
    excluded: dict[str, int] = {}

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    items: list[EvalItem] = []
    if not _is_gold(path):
        for record in dataset_module.load(path):
            if not record.is_scoreable:
                skip("not scoreable (synthetic or negative control)")
                continue
            items.append(EvalItem(record.id, record.category, record.image_path,
                                  record.actual_sale_price_usd, record.brand))
        return items, excluded

    for gold in schema.load_gold(path):
        image = gold.primary_image
        if not gold.is_scoreable:
            skip("not scoreable (unapproved, quarantined, negative control or unpriced)")
        elif not gold.counts_toward_headline:
            # docs/EVALUATION.md: only certain/high labels count toward the
            # headline, and this run *is* the headline the gate compares.
            skip("label confidence below headline (medium/low)")
        elif gold.currency != "USD":
            # The pipeline prices in USD and the harness has no FX rate.
            # Scoring a £ sale against a $ estimate would be an error metric
            # measuring the exchange rate.
            skip(f"sold in {gold.currency}; the harness scores USD only")
        elif image is None:
            skip("no image")
        else:
            items.append(EvalItem(gold.id, gold.category, image.path,
                                  gold.actual_sale_price, gold.brand))
    return items, excluded


def load_photos(folder: Path) -> list[EvalItem]:
    """Every JPEG in a folder, unlabelled."""
    return [EvalItem(id=p.stem, category="unlabelled", image_path=p.name)
            for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES]


def parse_arm(arm: str) -> tuple[str, int | None]:
    """`v2` → ("v2", None); `v2@512` → ("v2", 512)."""
    version, _, budget = arm.partition("@")
    if not budget:
        return version, None
    try:
        return version, int(budget)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{arm!r}: the thinking budget after '@' must be an integer") from None


@dataclass
class Prediction:
    """One model run against one benchmark item."""
    item_id: str
    category: str
    expected_price: float | None           # None when the item is unlabelled
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
    # Billed output and the reasoning share of it, when the SDK reports them.
    output_tokens: int | None = None
    thoughts_tokens: int | None = None
    prompt_version: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.predicted_expected > 0


def _token_summary(predictions: list[Prediction]) -> dict:
    def median(values: list[int]) -> float | None:
        return statistics.median(values) if values else None
    return {
        "output_median": median([p.output_tokens for p in predictions
                                 if p.output_tokens is not None]),
        "thoughts_median": median([p.thoughts_tokens for p in predictions
                                   if p.thoughts_tokens is not None]),
    }


def evaluate(predictions: list[Prediction]) -> dict:
    """Compute the full metric set from predictions. Pure — no model calls."""
    usable = [p for p in predictions if p.ok]
    failed = [p for p in predictions if not p.ok]
    labelled = [(p, p.expected_price) for p in usable if p.expected_price is not None]

    point_pairs = [(p.predicted_expected, actual) for p, actual in labelled]
    range_triples = [(p.predicted_low, p.predicted_high, actual) for p, actual in labelled]
    scored = [(p.confidence_score, p.predicted_expected, actual) for p, actual in labelled]

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
    for category in sorted({p.category for p, _ in labelled}):
        subset = [(p.predicted_expected, actual)
                  for p, actual in labelled if p.category == category]
        by_category[category] = {
            "n": len(subset),
            "mdape": metrics.mdape(subset),
            "within_25pct": metrics.within_tolerance(subset),
        }

    return {
        "n_total": len(predictions),
        "n_scored": len(usable),
        "n_labelled": len(labelled),
        "n_failed": len(failed),
        "accuracy": {
            "mdape": metrics.mdape(point_pairs),
            "mape": metrics.mape(point_pairs),
            "bias": metrics.bias(point_pairs),
            "within_10pct": metrics.within_tolerance(point_pairs, 10.0),
            "within_25pct": metrics.within_tolerance(point_pairs, 25.0),
            "within_50pct": metrics.within_tolerance(point_pairs, 50.0),
        },
        "range": {
            "coverage": metrics.range_coverage(range_triples),
            "mean_width_ratio": metrics.mean_range_width(range_triples),
        },
        # `calibration` returns ECE 0.0 for an empty input, and for an error
        # metric zero reads as perfect. With nothing labelled it is None.
        "calibration": {"ece": cal.ece if scored else None, "buckets": cal.as_table()},
        "hallucination": metrics.hallucination_rate(hallucination_records),
        "latency_ms": metrics.latency_summary([p.latency_ms for p in usable]),
        "tokens": _token_summary(usable),
        "by_category": by_category,
    }


def price_shift(runs_a: dict[str, list[float]],
                runs_b: dict[str, list[float]]) -> dict:
    """How far arm B's price for each item sits from arm A's.

    Median of each item's repeats on each side, then |B − A| / A. Needs no
    ground truth, so it says nothing about which arm is *right* — only how
    much the change moves the number a user sees, which is the first thing to
    know about a cost cut.
    """
    shifts: list[float] = []
    for item_id, prices_a in runs_a.items():
        a = [p for p in prices_a if p > 0]
        b = [p for p in runs_b.get(item_id, []) if p > 0]
        if not a or not b:
            continue
        median_a, median_b = statistics.median(a), statistics.median(b)
        shifts.append(abs(median_b - median_a) / median_a * 100.0)
    if not shifts:
        return {"median_shift_pct": None, "moved_over_10pct": None, "n": 0}
    return {
        "median_shift_pct": statistics.median(shifts),
        "moved_over_10pct": sum(1 for s in shifts if s > 10.0) / len(shifts),
        "n": len(shifts),
    }


def metric_set(report: dict, label: str) -> MetricSet:
    """The report as the provenance-tagged metrics `eval.cli gate` reads.

    Names and units follow `ArmResult.metric_set` in eval/experiment.py, which
    the gate thresholds were written against: percentages as 0-100. The runner
    used to write its raw report instead, which has no top-level `metrics` key,
    so the gate saw no metrics at all and every run came back SKIPPED — a gate
    that could not fail however bad the run was.

    Only measured values are added. A metric this run could not compute is
    absent rather than zero, and the gate skips it.
    """
    result = MetricSet(label=label)

    def add(name: str, value: float | None, n: int, unit: str = "") -> None:
        if value is not None and n > 0:
            result.add(Metric.measured(name, value, n, unit=unit))

    # Every figure below is over the scans that produced a price. Without this
    # a change that broke half the responses would pass on its survivors.
    n_total = report.get("n_total", 0)
    add("scored_fraction",
        report.get("n_scored", 0) / n_total * 100 if n_total else None, n_total, "%")

    n_labelled = report.get("n_labelled", 0)
    acc = report.get("accuracy", {})
    add("mdape", acc.get("mdape"), n_labelled, "%")
    add("mape", acc.get("mape"), n_labelled, "%")
    add("bias", acc.get("bias"), n_labelled, "%")
    within = acc.get("within_25pct")
    add("within_25pct", within * 100 if within is not None else None, n_labelled, "%")
    add("calibration_ece", (report.get("calibration") or {}).get("ece"), n_labelled)
    if n_labelled:
        # Only against labels: the brand-mismatch rule needs a true brand, so
        # on unlabelled photos the rate would silently mean something smaller.
        hall = report.get("hallucination") or {}
        rate = hall.get("rate")
        add("hallucination_rate", rate * 100 if rate is not None else None,
            hall.get("n", 0), "%")

    latency = report.get("latency_ms") or {}
    for key in ("p50", "p95"):
        add(f"latency_{key}", latency.get(key), latency.get("n", 0), "ms")

    tokens = report.get("tokens") or {}
    add("thoughts_tokens_median", tokens.get("thoughts_median"), report.get("n_scored", 0))
    add("output_tokens_median", tokens.get("output_median"), report.get("n_scored", 0))

    consistency = report.get("consistency") or {}
    add("consistency_mean_cv", consistency.get("mean_cv"), consistency.get("n", 0))
    repeat = report.get("repeatability") or {}
    fraction = repeat.get("stable_fraction")
    add("repeatability", fraction * 100 if fraction is not None else None,
        repeat.get("n", 0), "%")
    return result


def evaluate_consistency(runs: dict[str, list[float]]) -> dict:
    """Consistency across repeats, keyed by item id."""
    return metrics.consistency(list(runs.values()))


# ── Live evaluation ──────────────────────────────────────────────────────────

async def _predict_one(model, item: EvalItem, prompt_text: str, version: str, root: Path,
                       generation_config=None) -> Prediction:
    """Run one item through the real pipeline."""
    import aiconfig

    prediction = Prediction(
        item_id=item.id, category=item.category,
        expected_price=item.expected_price, expected_brand=item.expected_brand,
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
        response = await model.generate_content_async(
            [prompt_text, part], generation_config=generation_config)
        raw = aiconfig.extract_text(response)
    except Exception as exc:
        prediction.error = str(exc)[:200]
        prediction.latency_ms = (time.monotonic() - started) * 1000
        return prediction
    prediction.latency_ms = (time.monotonic() - started) * 1000
    usage = aiconfig.usage_of(response)
    prediction.output_tokens = usage.get("output_tokens")
    prediction.thoughts_tokens = usage.get("thoughts_tokens")

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
    # The same call `/scan` makes, not a paraphrase of it. This was a local
    # reimplementation that had drifted in two ways: it substituted `or 1.0` /
    # `or 5.0` for a response carrying no prices, a case production does not
    # have, and it never rebuilt the interior points, so `predicted_expected`
    # below was the pre-clamp figure — a headline price `/scan` would never
    # return, which is the number the whole report leads with.
    low, high, clamped = valuation_module.apply_price_bounds(val)
    conf = confidence_module.compute(
        brand=val.brand, category=val.category,
        identification_certainty=val.identification_certainty,
        authenticity=val.authenticity, demand=val.demand, supply=val.supply,
        value_low=low, value_high=high, image_quality=quality, was_clamped=clamped,
        model_field_count=valuation_module.count_present_fields(val),
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


async def run_live(items: list[EvalItem], version: str, root: Path, concurrency: int = 4,
                   thinking_budget: int | None = None) -> list[Prediction]:
    import aiconfig

    prompt_text, resolved = prompts.get_prompt(version)
    model = aiconfig.build_model()
    config = None
    if thinking_budget is not None:
        # The production config with only the reasoning cap changed, passed per
        # call — so the other arm, and the process's own GEMINI_THINKING_BUDGET,
        # are untouched.
        from google.genai import types
        config = aiconfig.generation_config().model_copy(update={
            "thinking_config": types.ThinkingConfig(thinking_budget=thinking_budget)})
        resolved = f"{resolved}@{thinking_budget}"
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(item):
        async with semaphore:
            return await _predict_one(model, item, prompt_text, resolved, root, config)

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
    ]
    if report.get("n_labelled"):
        ece = report["calibration"]["ece"]
        lines += [
            "",
            "Accuracy",
            f"  MdAPE (headline)      {num(acc['mdape'])}%",
            f"  MAPE  (tail-exposed)  {num(acc['mape'])}%",
            f"  bias  (signed median) {num(acc['bias'])}%",
            f"  within 10%            {pct(acc['within_10pct'])}",
            f"  within 25%            {pct(acc['within_25pct'])}",
            f"  within 50%            {pct(acc['within_50pct'])}",
            "",
            "Range",
            f"  coverage              {pct(rng['coverage'])}   (target ~80%)",
            f"  mean width ratio      {num(rng['mean_width_ratio'])}×  (lower is better)",
            "",
            "Calibration",
            f"  ECE                   {'n/a' if ece is None else f'{ece:.3f}'}   (0 = perfect)",
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
        ]
    else:
        lines += ["", "Accuracy: not measured — no sale prices to measure against"]

    tokens = report.get("tokens") or {}
    lines += [
        "",
        "Latency (ms)",
        f"  p50 {num(report['latency_ms']['p50'])}   "
        f"p95 {num(report['latency_ms']['p95'])}   "
        f"p99 {num(report['latency_ms']['p99'])}",
        "",
        "Tokens (median per scan)",
        f"  output {num(tokens.get('output_median'))}   "
        f"of which thinking {num(tokens.get('thoughts_median'))}",
    ]
    if report["by_category"]:
        lines += ["", "By category"]
    for category, stats in report["by_category"].items():
        lines.append(
            f"  {category:<14} n={stats['n']:<4} "
            f"MdAPE={num(stats['mdape'])}%  within25={pct(stats['within_25pct'])}"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate SnapWorth valuations")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset",
                        help="JSONL benchmark with verified sale prices (gold-v2 or v1)")
    source.add_argument("--photos",
                        help="folder of JPEGs with no prices — consistency, latency "
                             "and tokens only, never accuracy")
    parser.add_argument("--prompt-version", default=prompts.DEFAULT_PROMPT_VERSION,
                        help="the arm to run: a prompt version, optionally @N to "
                             "cap the thinking budget at N tokens")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="run two arms (e.g. v2 v2@512) and print both reports")
    parser.add_argument("--repeats", type=int, default=1,
                        help="runs per item; >1 enables the consistency metric")
    parser.add_argument("--limit", type=int, help="evaluate only the first N items")
    parser.add_argument("--categories", help="comma-separated category filter")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--json-out",
                        help="write the metrics `eval.cli gate` reads, with the raw "
                             "report alongside, to this path")
    parser.add_argument("--coverage-only", action="store_true",
                        help="report dataset composition and exit — no model calls")
    args = parser.parse_args(argv)

    arms: list[str] = list(args.compare) if args.compare else [args.prompt_version]
    try:
        parsed = [parse_arm(arm) for arm in arms]
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    labelled = args.dataset is not None
    excluded: dict[str, int] = {}
    if labelled:
        dataset_path = Path(args.dataset)
        items, excluded = load_items(dataset_path)
        root = dataset_path.parent
    else:
        root = Path(args.photos)
        items = load_photos(root)

    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",")}
        items = [i for i in items if i.category in wanted]
    if args.limit:
        items = items[: args.limit]

    by_category: dict[str, int] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
    print(json.dumps({"items": len(items), "labelled": labelled,
                      "by_category": dict(sorted(by_category.items())),
                      "excluded": excluded}, indent=2))
    if args.coverage_only:
        return 0
    if not items:
        print("no scoreable items — nothing to evaluate" if labelled
              else f"no JPEGs in {root} — nothing to evaluate")
        return 1

    reports: dict[str, dict] = {}
    runs_by_arm: dict[str, dict[str, list[float]]] = {}

    for arm, (version, budget) in zip(arms, parsed):
        all_runs: dict[str, list[float]] = {}
        predictions: list[Prediction] = []
        for repeat in range(max(1, args.repeats)):
            batch = asyncio.run(run_live(items, version, root, args.concurrency, budget))
            if repeat == 0:
                predictions = batch
            for prediction in batch:
                all_runs.setdefault(prediction.item_id, []).append(prediction.predicted_expected)

        report = evaluate(predictions)
        if args.repeats > 1:
            report["consistency"] = evaluate_consistency(all_runs)
            report["repeatability"] = metrics.repeatability(list(all_runs.values()))
        reports[arm] = report
        runs_by_arm[arm] = all_runs

        print(f"\n### {arm}")
        print(_format(report))
        if "consistency" in report:
            c = report["consistency"]
            mean_cv = "n/a" if c["mean_cv"] is None else f"{c['mean_cv']:.3f}"
            worst_cv = "n/a" if c["worst_cv"] is None else f"{c['worst_cv']:.3f}"
            print(f"Consistency over {args.repeats} runs\n"
                  f"  mean CV  {mean_cv}   worst CV  {worst_cv}   (lower is better)\n")

    shift: dict | None = None
    if args.compare:
        a, b = args.compare
        left = reports[a]["accuracy"]["mdape"]
        right = reports[b]["accuracy"]["mdape"]
        if left and right:
            delta = (left - right) / left * 100
            print(f"\nMdAPE {a}={left:.1f}%  {b}={right:.1f}%  "
                  f"→ {b} is {delta:+.1f}% better\n")
        shift = price_shift(runs_by_arm[a], runs_by_arm[b])
        if shift["n"]:
            print(f"Price shift {a} → {b}: median {shift['median_shift_pct']:.1f}% per item, "
                  f"{shift['moved_over_10pct'] * 100:.0f}% of {shift['n']} items moved >10%"
                  " — how far, not which is right\n")

    if args.json_out:
        payloads = {arm: metric_set(report, arm).to_dict() | {"report": report}
                    for arm, report in reports.items()}
        source_path = args.dataset or args.photos
        if args.compare:
            # No top-level `metrics`: a comparison is two runs, and the gate
            # compares one run against a baseline.
            out = {"compare": arms, "price_shift": shift, "arms": payloads,
                   "source": source_path, "labelled": labelled}
        else:
            out = payloads[arms[0]] | {"source": source_path, "labelled": labelled}
        Path(args.json_out).write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
