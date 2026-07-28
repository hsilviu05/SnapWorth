# Evaluation & experimentation platform

**Status: infrastructure complete, zero measurements taken.**

There is no gold dataset in this repository, so nothing here has produced a
measured accuracy figure for SnapWorth, and none is claimed anywhere. Run
`python -m eval.cli status` for the current answer to "what can we measure?" —
today it is "nothing", and the platform says so rather than printing zeros.

That distinction is the point of the whole system.

---

## The one rule

> A number is either **measured** or it is **labelled**.

`eval/provenance.py` enforces this in the type system rather than by convention:

| State | Meaning | May fail a build? |
|---|---|---|
| `MEASURED` ✓ | Computed from real labelled outcomes | Yes |
| `PROJECTED` ≈ | An estimate — **must state its basis** | No |
| `UNAVAILABLE` — | Not computable yet | No |

`Metric` cannot be constructed as MEASURED without a sample size, cannot be
PROJECTED without a written basis, and every renderer prints the marker. The
third state exists because the honest answer to most questions about this system
today is "not measured yet", and a framework that cannot express that will
invent something instead.

Zero is never used for "unmeasured": for an error metric, `0.0` reads as
*perfect*.

---

## Modules

| File | Purpose |
|---|---|
| `eval/provenance.py` | Measured / projected / unavailable tagging |
| `eval/schema.py` | Gold record, versioning, review workflow, drift detection |
| `eval/metrics.py` | Accuracy, error magnitude, calibration, retrieval quality |
| `eval/stats.py` | Bootstrap CI, Wilcoxon, Cliff's delta, power |
| `eval/experiment.py` | A/B framework with guardrails and a ship/reject verdict |
| `eval/erroranalysis.py` | Failure taxonomy and prioritised reports |
| `eval/calibration.py` | Learned confidence weights |
| `eval/gates.py` | CI thresholds, baselines, schema compliance |
| `eval/dashboard.py` | Dashboard data models |
| `eval/cli.py` | Unified CLI |
| `eval/dataset.py`, `eval/runner.py` | v1 loader and live runner (unchanged) |

Pure Python throughout — no numpy, scipy or sklearn. A quality gate that needs a
60 MB scientific stack to start is a gate people disable.

---

## Phase 1 — the gold dataset

### What a record is

A photograph paired with a **verified completed sale price**. Not an asking
price, not an appraisal, not an opinion. SnapWorth's claim is about what an item
sells for, so the ground truth has to be what an item sold for.

Target: **1,000 headline-eligible records.** That is the point at which
per-category MdAPE has a usable sample for the long tail, not just for clothing.

### Composition

Weighted to mirror real scan traffic, not spread evenly — a uniform benchmark
over-weights categories users rarely scan and will report improvement nobody
experiences.

| Category | Target | | Difficulty | Share |
|---|---|---|---|---|
| clothing | 300 | | easy | 20% |
| shoes | 150 | | typical | 45% |
| electronics | 120 | | hard | 20% |
| accessories | 100 | | adversarial | 10% |
| home | 80 | | negative control | 5% |
| collectibles | 80 | | | |
| books | 60 | | **Region** | |
| sports | 50 | | US | 60% |
| toys | 40 | | GB | 20% |
| furniture | 20 | | EU | 20% |

Hard and adversarial cases are 30% deliberately. A benchmark of clean studio
shots measures a product nobody uses.

### Review workflow

```
draft → pending_review → approved ──► scoreable
                       ↘ rejected
                       ↘ needs_relabel
```

`approved` requires a named reviewer. A `certain` or `high` label confidence
requires evidence (a URL or a note) — a claim of certainty must be traceable to
something.

### Governance: three ways a benchmark rots

1. **Label revision.** A price quietly edited toward what the model predicted
   turns the benchmark into a mirror. Guarded by `label_fingerprint()`, which
   hashes only ground-truth fields — adding a note does not trip it, changing a
   price does. `eval.cli drift` **exits non-zero** on any label change.

2. **Composition creep.** Easy items accumulate because they are easy to source.
   Guarded by `composition_drift`, which flags category and difficulty shifts
   beyond 10 points.

3. **Test-set leakage.** Prompts tuned until the benchmark passes measure
   memorisation. Guarded by a hash-based `dev`/`test` split — assignment is
   deterministic from the item id, so a record never migrates pools as the set
   grows. Run `test` to confirm a release, not to steer one.

### Building it

```bash
cp backend/eval/data/gold.template.jsonl backend/eval/data/gold.jsonl
# replace the TEMPLATE records with verified sales
python -m eval.cli dataset --path eval/data/gold.jsonl
```

Fastest honest sources, in order: your own completed sales (evidence in hand),
captured eBay completed listings, then a partner reseller's records. ~50 records
is enough to expose obvious failure modes; ~200 to fit a calibration model; 1,000
for stable per-category numbers.

---

## Phase 2 — metrics

**MdAPE is the headline, not MAPE.** Thrift data is mostly $5–$60 with a long
tail, so one $200-predicted/$5-sold item contributes +3900% to MAPE and swamps a
hundred good predictions. MAPE is still reported, to expose the tail rather than
hide it.

**RMSE is reported next to MAE** because the gap between them is the signal:
RMSE ≫ MAE means a few severe misses rather than uniform drift, and that changes
what you fix.

**Bias may be the most product-relevant metric here.** A system 20% high on
every item has the same MdAPE as one randomly ±20%, but the first is a
calibration problem with a one-line fix and the second is a capability problem.
Positive bias is the dangerous direction: a user buys on our number and cannot
resell.

**False-match rate is the headline for comps, not F1.** A missed comp shrinks
the sample; a wrong comp poisons the median while wearing the authority of
evidence.

**Abstention is not an error.** `field_accuracy` counts an honest "Unknown"
separately from a wrong answer. Scoring abstention as failure would train the
system toward confident guessing.

---

## Phase 3 — experiments

```bash
python -m eval.cli experiment --name prompt-v3 \
  --baseline runs/v2.json --candidate runs/v3.json
```

Declare **one primary metric** before running. Everything else is a guardrail.
With twenty secondary metrics at α=0.05 you expect one false positive per run,
and picking the winner afterwards is a machine for manufacturing improvements
that do not exist.

Comparisons are **paired** — both arms on the same items. Item-to-item variance
in resale pricing dwarfs the difference between two prompts.

A candidate must clear **three independent bars**, because each catches a
different way of being fooled:

- **significance** (Wilcoxon signed-rank) — unlikely to be chance;
- **effect size** (Cliff's delta) — the distributions genuinely separate;
- **practical magnitude** (≥1% relative) — large enough to be worth shipping.

The third is not redundant. See "Bugs the tests caught" below.

Guardrails are absolute: an accuracy win that doubles latency or increases
hallucination is `BLOCKED_BY_GUARDRAIL`, not a win.

`INCONCLUSIVE` does **not** fail CI — that would push people toward
under-powered runs. Only `REJECT` and `BLOCKED_BY_GUARDRAIL` do.

---

## Phase 4 — error analysis

19 failure modes, each mapped to **who can fix it** — the most common way an
error report fails is producing findings nobody owns.

Every classifier is a rule over observable fields, not a model. A learned
failure classifier would need its own labelled data and its own evaluation, and
would fail in ways harder to audit than the failures it describes.

Where rules cannot decide, the case is `UNCLASSIFIED`, and the unclassified
share is itself reported — a taxonomy explaining 40% of failures should not be
presented as if it explains them all.

Failures are ranked by **value at risk** (absolute currency error) as well as by
percentage. Being 200% wrong on a $4 item matters less than 40% wrong on a $600
one, and percentage-ranked reports bury the second behind the first.

---

## Phase 5 — calibration

`backend/confidence.py` currently uses hand-chosen weights; its own docstring
says they are "a considered prior, not a fitted model". This is how they stop
being assumed.

Target: **P(estimate within 25% of the true sale price)** — the same event as
the `within_25pct` headline metric.

| Method | When |
|---|---|
| **Logistic regression** | Default. Coefficients are directly comparable to the hand-chosen weights, so the fit can be *argued with* rather than merely deployed. |
| **Isotonic** | Corrects arbitrarily shaped miscalibration. Needs more data; can overfit. |
| **Temperature scaling** | One parameter, cannot overfit. The honest choice at ~200 labelled outcomes. |
| **Gradient boosting** | **Not implemented** — see below. |

Gradient boosting would likely win, because signal interactions are real (image
quality matters far more when the brand is unknown). It is not implemented
because a hand-rolled GBM would be worse than sklearn's while being harder to
trust, and adding sklearn to CI is a poor trade at present dataset sizes.
`GradientBoostingPlaceholder` **raises** rather than silently degrading, so a
caller cannot believe they got a boosted model when they did not.

A `MEASURED` calibration model **must** name the dataset version it was fitted
on — otherwise the weights cannot be reproduced or audited. Fitting on synthetic
data must pass `PROJECTED`.

---

## Phase 6 — dashboard

`eval/dashboard.py` produces JSON; no frontend, because a chart library is the
least durable part of this platform.

Every panel carries provenance, and a mixed panel is labelled by its **worst**
member — a dashboard is where numbers get screenshotted, and a screenshot strips
context.

`integrity.is_evidence_backed` is the field to read first. When false:

> No panel on this dashboard contains measured data. Every value shown is
> projected or unavailable — do not cite any of it as a result.

Sections that cannot be computed render as explicit "unavailable" panels rather
than being omitted. An absent panel reads as "we do not track that"; an
unavailable one reads as "we track it and have not measured it yet".

---

## Phase 7 — CI gates

`.github/workflows/eval.yml`, four jobs:

| Job | Runs | Fails on |
|---|---|---|
| `platform-tests` | Always | Platform bugs |
| `data-integrity` | Always | Scoreable records in template/sample files; gold label drift |
| `accuracy-gate` | When `gold.jsonl` exists | Accuracy, bias, calibration, latency regression |
| `schema-contract` | Always | v1 client contract break |

Three rules keep the gate trustworthy:

1. **Only measured values can fail a build** — failing on a projection means
   failing on an assumption.
2. **Missing data is `SKIPPED`, never `PASSED`** — silent success on an empty
   benchmark looks identical to a real pass and is the most dangerous possible
   output.
3. **Thresholds are relative to a recorded baseline** — absolute thresholds
   either never trigger or get edited to make a build pass, which is how gates
   die.

---

## Bugs the tests caught

Both were in code I had just written, and both would have produced confidently
wrong decisions.

**Guardrails silently disabled at a zero baseline.** The check began
`if baseline is None or not baseline.value: return None`. Python treats `0.0` as
falsy, so a baseline of *zero hallucinations* — the best possible value, and the
one most worth protecting — turned the guardrail off entirely. A candidate
introducing hallucinations on 25% of items sailed through as `SHIP`.

**Statistical significance without practical magnitude.** A uniform shift of
0.005% across 200 paired items produced p ≈ 0 and a Cliff's delta of 0.19
("small", above the negligible threshold), so the framework shipped it. Cliff's
delta measures distributional overlap, not magnitude; a micro-shift moves it.
Fixed with the relative-magnitude floor described above.

There was also a data problem in earlier work: `eval/data/sample.jsonl` shipped
records labelled `source: "personal_sale"` and `"ebay_sold"` with invented
prices, dates and notes ("sold in 6 days, 2 watchers"). Those were *scoreable*,
so they would have contributed fabricated numbers to any reported metric. The
file is now templated, every record is `synthetic`, and a CI job fails the build
if any shipped sample record ever becomes scoreable again.

---

## Maturity

| Capability | State |
|---|---|
| Metric implementations | ✅ Implemented and tested |
| Provenance enforcement | ✅ Implemented and tested |
| Dataset schema & governance | ✅ Implemented and tested |
| Experiment framework | ✅ Implemented and tested |
| Error taxonomy | ✅ Implemented and tested |
| Calibration fitting | ✅ Implemented, ⚠️ never fitted on real data |
| CI gates | ✅ Wired, ⏭️ skip until a gold set exists |
| Dashboard models | ✅ Implemented, no frontend |
| **Gold dataset** | ❌ **Does not exist** |
| **Any measured result** | ❌ **None** |

**Evaluation maturity: 3 / 5** — instrumented, not yet measuring. Level 4
requires a gold set and a recorded baseline; level 5 requires continuous
evaluation on every release with trend history.

### Effort to continuous evaluation

Estimates, not measurements — labelling throughput is the dominant unknown and
varies enormously with how sales records are sourced.

| Step | Estimate |
|---|---|
| 50 records → first real signal | ~1 day |
| 200 records → fit calibration, run experiments | ~1 week |
| 1,000 records → stable per-category numbers | ~3–4 weeks |
| Record baseline, enable gates | ~1 hour once data exists |
| Nightly scheduled evaluation | ~1 day |

The platform is not the bottleneck. Labelling is.

---

## Future work

- **Inter-rater agreement.** With more than one labeller, measure Cohen's κ on a
  shared subset. Labels nobody agrees on are not ground truth.
- **Stratified reporting with CIs per stratum.** Per-category MdAPE without an
  interval invites over-reading a 12-item cell.
- **Sequential testing.** Fixed-horizon tests are wasteful when a candidate is
  clearly worse; a sequential design stops early.
- **Regression triage bot.** Post the error-analysis diff on failing PRs.
- **Counterfactual replay.** Store raw model output so a scoring change can be
  re-evaluated without re-calling the model — the single biggest cost saver once
  the set reaches 1,000 items.
