"""Unified evaluation CLI.

    python -m eval.cli dataset   --path eval/data/gold.jsonl
    python -m eval.cli drift     --baseline old.jsonl --candidate new.jsonl
    python -m eval.cli gate      --current run.json --baseline baseline.json
    python -m eval.cli experiment --baseline a.json --candidate b.json
    python -m eval.cli dashboard --run run.json --out dashboard.json
    python -m eval.cli calibrate --examples outcomes.json --method logistic
    python -m eval.cli status

Every subcommand works without a model or an API key. `status` is the one to
run first: it reports what can and cannot currently be measured, which on a
fresh checkout is "nothing" — and says so plainly rather than printing zeros.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import calibration as calibration_module  # noqa: E402
from eval import dashboard as dashboard_module  # noqa: E402
from eval import gates as gates_module  # noqa: E402
from eval import schema  # noqa: E402
from eval.experiment import ArmResult, run_experiment  # noqa: E402
from eval.provenance import Metric, MetricSet, Provenance  # noqa: E402


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _metric_set_from(payload: dict, label: str) -> MetricSet:
    result = MetricSet(label=label)
    for name, raw in payload.get("metrics", {}).items():
        try:
            result.add(Metric(
                name=name, value=raw.get("value"),
                provenance=Provenance(raw.get("provenance", "measured")),
                unit=raw.get("unit", ""), sample_size=raw.get("sample_size", 0),
                basis=raw.get("basis", ""),
            ))
        except ValueError:
            continue
    return result


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_dataset(args) -> int:
    items = schema.load_gold(args.path)
    report = schema.coverage(items)
    print(json.dumps({
        "total": report.total,
        "scoreable": report.scoreable,
        "headline_eligible": report.headline_eligible,
        "approved": report.approved,
        "pending_review": report.pending,
        "meets_target": report.meets_target,
        "target_size": schema.TARGET_SIZE,
        "by_category": report.by_category,
        "category_gaps": report.category_gaps,
        "by_difficulty": report.by_difficulty,
        "difficulty_gaps": report.difficulty_gaps,
        "by_region": report.by_region,
        "by_label_confidence": report.by_label_confidence,
        "splits": report.split_counts,
        "problems": report.problems,
    }, indent=2))

    if not report.meets_target:
        shortfall = schema.TARGET_SIZE - report.headline_eligible
        print(f"\n⚠️  {shortfall} more headline-eligible records needed "
              f"to reach the {schema.TARGET_SIZE}-item target.", file=sys.stderr)
    if report.problems:
        print(f"⚠️  {len(report.problems)} validation problem(s); see above.",
              file=sys.stderr)
    return 0


def cmd_drift(args) -> int:
    baseline = schema.load_gold(args.baseline)
    candidate = schema.load_gold(args.candidate)
    report = schema.composition_drift(baseline, candidate)

    print(json.dumps({
        "label_changes": report.label_changes,
        "added": report.added,
        "removed": report.removed,
        "category_shift": report.category_shift,
        "difficulty_shift": report.difficulty_shift,
        "price_median_change_pct": report.price_median_change_pct,
        "warnings": report.warnings,
        "clean": report.is_clean,
    }, indent=2))

    if report.has_label_drift:
        print("\n❌ Ground truth changed on existing records. Label revision must "
              "be reviewed explicitly — this is how a benchmark quietly becomes "
              "a mirror of the model.", file=sys.stderr)
        return 1
    # Composition moves as the set grows, which is the point of growing it.
    # A warning to read, not a failure (#215): failing here pushed the set
    # toward never changing shape. In Actions each becomes an annotation.
    for warning in report.warnings:
        print(f"::warning::gold set composition: {warning}")
    return 0


def _config_mismatch(current: dict, baseline: dict) -> str | None:
    """Why two runs can't be compared, or None (#215). A run recorded under a
    config, against a baseline under another or under none, measures the
    config change, not the PR."""
    ours, theirs = current.get("config"), baseline.get("config")
    if not ours:
        return None
    if not theirs:
        return ("the baseline records no config, so it can't be known to match "
                f"this run's {ours}. Re-record eval/data/baseline.json under "
                "eval/data/production.json")
    keys = ("prompt_version", "model", "thinking_budget")
    differs = [k for k in keys if ours.get(k) != theirs.get(k)]
    if differs:
        detail = ", ".join(f"{k} {theirs.get(k)!r} → {ours.get(k)!r}" for k in differs)
        return (f"this run and the baseline ran under different configs ({detail}). "
                "Re-record eval/data/baseline.json under the new config in the same PR")
    return None


def cmd_gate(args) -> int:
    raw_current = _load_json(args.current)
    if args.baseline and Path(args.baseline).exists():
        mismatch = _config_mismatch(raw_current, _load_json(args.baseline))
        if mismatch:
            print(f"❌ {mismatch}.", file=sys.stderr)
            return 1
    current = _metric_set_from(raw_current, "current")
    baseline = (gates_module.load_baseline(args.baseline) if args.baseline else None)
    report = gates_module.check(current, baseline,
                                baseline_ref=args.baseline or "",
                                current_ref=args.current)
    print(report.render())
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report.to_dict(), indent=2))

    if report.status is gates_module.GateStatus.SKIPPED and args.require_measurement:
        if current.has_any_measurement and baseline is None:
            # The first run on a new gold set lands here: real numbers, nothing
            # to compare them with. Say how to arm the gate, not "nothing
            # was measured", which would send someone hunting the wrong bug.
            print("❌ this run measured, but there is no baseline to compare it "
                  "with. A runner --json-out file is a valid baseline: commit "
                  "one as eval/data/baseline.json (docs/EVALUATION.md).",
                  file=sys.stderr)
        else:
            print("❌ --require-measurement was set but nothing was measured.",
                  file=sys.stderr)
        return 1
    return report.exit_code


def _arm_from(payload: dict, label: str) -> ArmResult:
    """An arm from either shape: a runner `--json-out` for one arm (its `arm`
    key, #216) or a bare ArmResult dict, as written before the runner did."""
    return ArmResult.from_dict(payload.get("arm", payload), label)


def cmd_experiment(args) -> int:
    if args.compare:
        # A runner `--compare A B` file: A is the baseline, B the candidate,
        # in the order they were run.
        payload = _load_json(args.compare)
        order = payload.get("compare") or list(payload.get("arms", {}))
        if len(order) != 2:
            print(f"{args.compare}: not a two-arm runner --compare file", file=sys.stderr)
            return 2
        baseline = _arm_from(payload["arms"][order[0]], order[0])
        candidate = _arm_from(payload["arms"][order[1]], order[1])
    elif args.baseline and args.candidate:
        baseline = _arm_from(_load_json(args.baseline), "baseline")
        candidate = _arm_from(_load_json(args.candidate), "candidate")
    else:
        print("experiment needs --compare FILE, or --baseline and --candidate",
              file=sys.stderr)
        return 2

    if baseline.config and candidate.config:
        differs = sorted(k for k in set(baseline.config) | set(candidate.config)
                         if k != "prompt_version" and k != "thinking_budget"
                         and baseline.config.get(k) != candidate.config.get(k))
        if differs:
            # Only the arm's own lever may differ; a different model is a
            # different experiment.
            print(f"the arms ran under different settings ({', '.join(differs)}); "
                  "re-run both under one", file=sys.stderr)
            return 2

    result = run_experiment(
        args.name,
        baseline,
        candidate,
        primary_metric=args.primary_metric,
        require_significance=not args.allow_insignificant,
    )
    print(result.render())
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result.to_dict(), indent=2))

    # Non-zero only when the candidate is actively worse or breaks a guardrail.
    # Inconclusive is not a failure — it means "measure more", and failing a
    # build on it would push people toward under-powered runs.
    from eval.experiment import Verdict
    return 1 if result.verdict in {Verdict.REJECT, Verdict.BLOCKED_BY_GUARDRAIL} else 0


def cmd_dashboard(args) -> int:
    payload = _load_json(args.run) if args.run else {}
    metrics = _metric_set_from(payload, "run")
    board = dashboard_module.build(
        metrics=metrics,
        dataset_version=payload.get("dataset_version", ""),
        by_category=payload.get("by_category"),
        by_brand=payload.get("by_brand"),
        calibration=payload.get("calibration"),
        history=payload.get("history"),
        experiment=payload.get("experiment"),
    )
    if args.out:
        Path(args.out).write_text(board.to_json())
        print(f"wrote {args.out}")
    print(board.render_summary())
    return 0


def cmd_calibrate(args) -> int:
    raw = _load_json(args.examples)
    examples = [
        calibration_module.TrainingExample(
            signals=e["signals"], correct=bool(e["correct"]),
            item_id=e.get("item_id", ""), raw_confidence=e.get("raw_confidence"))
        for e in raw.get("examples", [])
    ]
    if not examples:
        print("no examples provided", file=sys.stderr)
        return 1

    train, holdout = calibration_module.split_examples(examples)
    model = calibration_module.fit(
        train, method=args.method, dataset_version=args.dataset_version,
        provenance=Provenance.MEASURED if args.dataset_version else Provenance.PROJECTED,
    )
    if model is None:
        print(f"insufficient data to fit ({len(train)} training examples; "
              f"20 minimum)", file=sys.stderr)
        return 1

    evaluation = calibration_module.evaluate_calibration(model, holdout)
    print(json.dumps({
        "method": model.method,
        "provenance": model.provenance.value,
        "n_train": len(train),
        "n_holdout": len(holdout),
        "holdout_evaluation": evaluation,
        **({"normalised_weights": model.logistic.normalised_weights()}
           if model.logistic else {}),
    }, indent=2))

    if args.out:
        model.save(args.out)
        print(f"\nwrote {args.out}")
    if not args.dataset_version:
        print("\n⚠️  No --dataset-version given, so this model is tagged "
              "PROJECTED and must not be deployed as a measured calibration.",
              file=sys.stderr)
    return 0


def cmd_images(args) -> int:
    """Every gold record's photos are in the store, as the intake wrote them
    (#213, #215): present, a JPEG within the app's upload size, and the
    sha256 the record carries. A missing or altered photo fails, because the
    gate would otherwise score a different image than the label describes."""
    from eval import intake
    gold = Path(args.path)
    root = gold.parent
    problems: list[str] = []
    checked = 0
    for item in schema.load_gold(gold):
        for image in item.images:
            path = root / image.path
            checked += 1
            if not path.is_file():
                problems.append(f"{item.id}: {image.path} is not in the store")
                continue
            data = path.read_bytes()
            size = intake.jpeg_size(data)
            if size is None:
                problems.append(f"{item.id}: {image.path} is not a JPEG")
            elif max(size) > intake.MAX_EDGE:
                problems.append(f"{item.id}: {image.path} is {size[0]}×{size[1]}, "
                                f"over the {intake.MAX_EDGE} px the app sends")
            if image.sha256 and intake.hashlib.sha256(data).hexdigest() != image.sha256:
                problems.append(f"{item.id}: {image.path} does not match its sha256")
            elif not image.sha256:
                problems.append(f"{item.id}: {image.path} has no sha256 in the record")
    for problem in problems:
        print(f"::error::{problem}" if args.annotate else problem)
    print(f"{checked} image(s) checked, {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_status(args) -> int:
    """What can and cannot currently be measured."""
    gold_path = Path(args.gold) if args.gold else None
    items = schema.load_gold(gold_path) if gold_path and gold_path.exists() else []
    report = schema.coverage(items) if items else None

    baseline = (gates_module.load_baseline(args.baseline)
                if args.baseline and Path(args.baseline).exists() else None)

    status = {
        "gold_dataset": {
            "path": str(gold_path) if gold_path else None,
            "exists": bool(items),
            "headline_eligible": report.headline_eligible if report else 0,
            "target": schema.TARGET_SIZE,
            "ready": bool(report and report.meets_target),
        },
        "baseline_recorded": baseline is not None,
        "can_measure_accuracy": bool(report and report.headline_eligible > 0),
        "can_gate_regressions": baseline is not None and bool(items),
        "can_fit_calibration": bool(report and report.headline_eligible >= 20),
        "can_run_experiments": bool(report and report.headline_eligible >= 30),
    }
    print(json.dumps(status, indent=2))

    if not status["can_measure_accuracy"]:
        print(
            "\n⚠️  No gold dataset. Nothing in this repository can currently "
            "produce a measured accuracy figure, and none is claimed.\n"
            "    See docs/EVALUATION.md → 'Building the gold set'.",
            file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval", description="SnapWorth evaluation platform")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dataset", help="report gold set composition")
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("drift", help="compare a candidate set against a baseline")
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("gate", help="check CI quality gates")
    p.add_argument("--current", required=True)
    p.add_argument("--baseline")
    p.add_argument("--json-out")
    p.add_argument("--require-measurement", action="store_true",
                   help="fail when nothing was measured, instead of skipping")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("experiment", help="compare two arms")
    p.add_argument("--name", default="unnamed")
    p.add_argument("--compare", help="a runner --compare file; its first arm is the baseline")
    p.add_argument("--baseline", help="one arm: a runner --json-out or an ArmResult dict")
    p.add_argument("--candidate")
    p.add_argument("--primary-metric", default="mdape")
    p.add_argument("--allow-insignificant", action="store_true")
    p.add_argument("--json-out")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("dashboard", help="build dashboard JSON")
    p.add_argument("--run")
    p.add_argument("--out")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("calibrate", help="fit confidence calibration")
    p.add_argument("--examples", required=True)
    p.add_argument("--method", default="logistic",
                   choices=["logistic", "isotonic", "temperature"])
    p.add_argument("--dataset-version", default="")
    p.add_argument("--out")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("images", help="check the gold photos against their records")
    p.add_argument("--path", default="eval/data/gold.jsonl")
    p.add_argument("--annotate", action="store_true", help="print GitHub Actions errors")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("status", help="what can currently be measured")
    p.add_argument("--gold", default="eval/data/gold.jsonl")
    p.add_argument("--baseline", default="eval/data/baseline.json")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
