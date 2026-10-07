# Evaluation & experimentation platform

**Status: infrastructure complete, zero measurements taken.**

There is no gold dataset in this repository, so nothing here has produced a
measured accuracy figure for SnapWorth, and none is claimed anywhere. The
intake that builds one exists (`python -m eval.intake`, below); the sales it
needs are being gathered (#213). When `gold.jsonl` lands, this line names its
frozen version (`schema.freeze`) and record count, and still quotes no
accuracy until a baseline is recorded. Run
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
| `eval/dataset.py` | v1 benchmark loader |
| `eval/runner.py` | Live runner: reads gold-v2 (and v1) sets, writes the metric file `eval.cli gate` reads, and runs unlabelled photo folders |

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

Records come in through the intake, never by hand-copying the template
(`backend/eval/intake.py`, #213). From `backend/`:

```bash
# Sold flips from the app: My Flips → ⋯ → Export photos and estimates (ZIP)
python -m eval.intake zip ~/Downloads/SnapWorth-Flips-2026-10-05.zip
# Sales that never went through the app: a CSV, one photo per row
python -m eval.intake csv sales.csv --photos ~/Pictures/sold
python -m eval.cli dataset --path eval/data/gold.jsonl
```

Each sale becomes a **draft** with the next id (`G-0001`, …), appended to
`eval/data/gold.jsonl`, and its photo goes to `eval/data/images/<id>-front.jpg`
with its `sha256` in the record. `--dry-run` checks every row and writes
nothing. A draft counts toward nothing until a reviewer sets its evidence and
label confidence and approves it (`review_state: approved`, `reviewed_by`).

**What lives where.** The repository is public. `gold.jsonl` holds labels only
— price, currency, category, brand, condition, date, region, difficulty, tags,
image paths and hashes — and is in git, because the drift check reads it from
git history. Photos, receipts and screenshots live in a private repository
mounted at `backend/eval/data/images/`, which `.gitignore` keeps out. Evidence
is written as `evidence_note: "private:<ref>"`, naming a file in that store.
The intake refuses a web address as evidence, and `eval.yml`'s
`data-integrity` job fails if an image is committed under `backend/eval/data/`
(even with `git add -f`) or a record holds an evidence link.

**What the intake enforces.**
- **Production's input.** A photo larger than the app's 1568 px upload, or not
  a JPEG, is converted with macOS `sips` to a JPEG with a 1568 px long edge at
  quality 80. A JPEG already within that is copied unchanged. The app's
  export holds its 1024 px stored copy, so those records are tagged
  `stored_1024`.
- **Evidence for certainty.** `certain` and `high` need a `private:` reference.
- **Real currencies and real regions.** A My Flips sale is assumed USD,
  because the app never asks, and tagged `currency_assumed` for the reviewer
  to confirm. A CSV sale keeps its own currency. Every row names the country
  it sold in, ISO 3166 (US, RO, DE, …; never "EU" or "UK"), and a non-USD
  sale needs its `sold_date`. Labels are never converted: the runner converts
  each sale to USD **in scoring**, at its sale date, from the pinned ECB table
  in `backend/eval/fx.py` (#225), and records the table in `run.json`.
- **All or nothing.** If any row is refused, nothing is written and every
  problem is listed.
- **No duplicates.** A photo already in the set, by hash, is skipped, so
  re-importing a later export adds only the new sales.

CSV columns: `photo`, `price`, `currency`, `category`, `region` (required);
`brand`, `model`, `condition`, `sold_date` (required for non-USD), `marketplace`,
`label_confidence`, `evidence`, `difficulty`, `tags` (`;`-separated), `notes`.

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

### Per region: which market the dollar figure prices for (#225)

`run.json`'s `by_region` gives, per country, n, MdAPE, **bias**, within 25%
and range coverage; a region with fewer than 10 sales is shown and marked too
small. `eval.cli field` groups shared sales by storefront the same way. Bias
is the number to read: a steady +X% in one region means its prices were set
for another market.

**Decision rule, declared before any data:** a market-aware prompt is
warranted for a region when **|bias| > 15% with n ≥ 30** there, on the default
prompt, in the gold set or the field data. When a region crosses it, open a
follow-up (an optional `market` field on `/scan`, a prompt version pricing
for that market in USD, result copy naming the market); none of that is
built until then. The app keeps showing USD either way.

| Region | n | Bias (95% CI) | Decision |
|---|---|---|---|
| — | — | not measured yet: needs ≥ 30 sales in the region (#213, #224) | — |

## Phase 3 — experiments

```bash
# Both arms on the dev items, 3 repeats each; one file holds both arms
python -m eval.runner --dataset eval/data/gold.jsonl --compare v2 v2.1 \
  --repeats 3 --json-out runs/v2-v2.1.json
python -m eval.cli experiment --name v2-v2.1 --compare runs/v2-v2.1.json
# or two single-arm runs
python -m eval.cli experiment --name prompt-v3 \
  --baseline runs/v2.json --candidate runs/v3.json
```

The runner writes each arm per item, keyed by gold id (#216): the price,
latency and confidence are the median of an item's repeats. An item with
no priced repeat counts as a failure and is left unpaired. Each arm also
carries its config (prompt version, thinking budget, model). Two arms that
differ in anything but the prompt or the budget are refused, because they
are a different experiment. The approved `negative_control` records run as
a separate pass, giving each arm a `decline_rate`: the share returned as
`not_resalable`. The guardrails are latency p95, hallucination, calibration
ECE and bias, and, when both arms carry them, `scored_fraction` (may not
fall more than 5%), `decline_rate` (may not fall more than 20%) and median
billed output tokens (may not rise more than 25%).

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

### Without labels

Some questions need no sale price at all: does the same photo get the same
price, how long does a scan take, how many tokens does it spend — and how far
does a change move the price a user sees. That last one is the first thing to
know about a cost cut, and the thinking budget (`GEMINI_THINKING_BUDGET`,
~61% of model spend per RUNBOOK §10) is exactly that:

```bash
python -m eval.runner --photos ~/scans --repeats 3 \
  --compare v2.1 v2.1@512 --json-out runs/thinking-512.json
```

Both arms are v2.1, not v2. v2 asks for the prices before the evidence, so with
less thinking the evidence it writes after a price is a justification of it;
v2.1 has the model write what it saw first. For the same reason, ship a lowered
`GEMINI_THINKING_BUDGET` only while v2.1 is serving (`SCAN_PROMPT_VERSION=v2.1`,
or once v2.1 is `DEFAULT_PROMPT_VERSION`). The budget applies to every scan
whatever the prompt, so a cap measured on v2.1 and set while v2 serves cuts
v2's thinking, which is the case this avoids.

`--photos` takes a folder of JPEGs. An arm is a prompt version with an
optional `@N` thinking cap, applied per call so the other arm is untouched.
The report gives consistency and repeatability per arm, latency, median answer,
thinking and billed-output tokens (Gemini counts the answer and the thinking
separately and bills both as output, so the cap moves the third), and the
per-item price shift between the arms. It never
reports accuracy, bias, calibration or hallucination: with no truth to measure
against those metrics are absent, not zero. A shift says the cap *changes*
prices, never that it makes them better or worse — that needs the gold set.

The same run is the check before a prompt version becomes the default. v2.1
has not had one on real photos; the comment on `DEFAULT_PROMPT_VERSION` in
`backend/prompts.py` quotes the condition for the switch and the two ways it
reads:

```bash
python -m eval.runner --photos ~/scans --repeats 3 \
  --compare v2 v2.1 --json-out runs/v2.1.json
```

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

Target: **P(the sale lands inside the range shown)**: the `in_range` event,
`confidence.CONFIDENCE_EVENT`.

**Pre-registered 2026-10-07, before any fit** (#226, step 1, the owner's
decision). It is how a user reads "$20–$40 · High", so it is what the badge
promises. A calibrated score of N means about N% of such estimates hold the
sale:

| Band | Score | Promise, measured on the gold set's test split |
|---|---|---|
| High | ≥ 70 | the sale lands in the range at least 70% of the time |
| Medium | 45–69 | 45–69% of the time |
| Low | < 45 | no promise; check before buying |

Within 25% of the likely price was the other candidate. It stays the headline
accuracy metric and is still recorded on every example (`--event within_25pct`
fits to it), but it is not what the band means. The runner's
`calibration_ece` gate metric, its per-bucket table and the A/B experiment's
guardrail all measure `in_range`. Each arm carries a per-item `in_range` map,
taken at the median range across repeats. An arm file written before that
map existed reports no `calibration_ece`, rather than one measured against
the other event under the same name.

Every fitted model file records its event, and the server refuses to load one
fitted to anything but `in_range`. Until a model fitted to that event is
switched on, the weighted score makes none of these promises, and no copy may
claim them.

| Method | When |
|---|---|
| **Logistic regression** | Default. Coefficients are directly comparable to the hand-chosen weights, so the fit can be *argued with* rather than merely deployed. |
| **Isotonic** | Corrects arbitrarily shaped miscalibration. Needs more data; can overfit. |
| **Temperature scaling** | One parameter, cannot overfit. The honest choice at ~200 labelled outcomes — unless the score is uniformly too high or too low: with no offset it can only pull scores toward 50. |
| **Platt scaling** | sigmoid(a·logit + b): temperature plus an offset, two parameters. Fixes a uniform overclaim, which temperature cannot; on a synthetic set 30 points overconfident it beats temperature on holdout ECE (`tests/test_eval_platform.py`). |
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

### From a run to a fit

```bash
# One example per priced item: signals, raw score, outcome under the event,
# gold id and its dev/test split. First repeat, one arm.
python -m eval.runner --dataset eval/data/gold.jsonl \
    --examples-out examples.json --event within_25pct

# Per band, no fitting: n, claimed vs actual hit rate, 95% bootstrap CI.
# Says whether High is earned even at 50 outcomes.
python -m eval.cli reliability --examples examples.json

# Fit on dev, judged on test (the file's own split), when there are ~200.
python -m eval.cli calibrate --examples examples.json --method platt \
    --dataset-version gold-v1 --out calibration.json
```

The reliability table belongs in this file, tagged MEASURED with its dataset
version, once the gold set (#213) is headline-eligible. It is not here yet
because no such run exists.

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

`.github/workflows/eval.yml`, five jobs:

| Job | Runs | Fails on |
|---|---|---|
| `platform-tests` | Always | Platform bugs |
| `data-integrity` | Always | Scoreable records in template/sample files; a photo or evidence link committed; a changed label. Composition drift is a `::warning::`, not a failure |
| `gold-check` | Always | A same-repo change to a pricing file, with a gold set, whose run lacks `GEMINI_API_KEY` or `GOLD_READ_TOKEN`. Otherwise it only decides whether `accuracy-gate` runs |
| `accuracy-gate` | When a pricing file changed (`prompts`, `valuation`, `confidence`, `aiconfig`, `promptsafety`, `imagequality`, `categories`, `eval/**`), on the Monday schedule, or by hand with `run_live_eval`; and only with a headline-eligible gold set and both secrets. Otherwise *skipped*, with no model calls | A gold photo missing or altered; a baseline recorded under another config; accuracy, bias, calibration, hallucination or latency regression; fewer scans producing a price; or no baseline to compare with |
| `schema-contract` | Always | v1 client contract break |

**What CI runs, and under what (#215).** The gate checks out the private photo
store into `eval/data/images/` (repository `vars.GOLD_REPO`, default
`hsilviu05/snapworth-gold`, read with `GOLD_READ_TOKEN`). It runs
`eval.cli images`, so every photo must be present, a JPEG within 1568 px, and
match its `sha256`. It then runs under `eval/data/production.json`, which
holds the prompt version, model and thinking budget that Railway serves.
`run.json` records that config, and the gate refuses to compare runs recorded
under different configs. Each run also reports MdAPE and bias for the
midpoint of the range beside the expected price. Those are reported, never
gated. The Monday run keeps its `run.json` for 90 days, and the job summary
prints the headline row. It is the only alarm for drift on the model's side.

**Recording the baseline.** The runner's `--json-out` file is in the shape the
gate reads, so a run is its own baseline. Record it under the production
config:

```bash
python -m eval.runner --dataset eval/data/gold.jsonl \
  --config eval/data/production.json --json-out eval/data/baseline.json
```

**Tolerances come from noise, not taste.** Before the first baseline, run
that command three times at one commit with `--json-out` to three files.
Each threshold in `gates.DEFAULT_THRESHOLDS` for `mdape`, `within_25pct`,
`bias`, `calibration_ece` and `scored_fraction` must be at least the spread
the three runs show. Cite the date and n in a comment beside it. A tolerance
below the noise fails good PRs and gets switched off. `--repeats 3
--aggregate median` scores each item's median repeat, for a calmer figure at
three times the cost.

**When production changes** (`SCAN_PROMPT_VERSION`, `GEMINI_MODEL`,
`GEMINI_THINKING_BUDGET` on Railway): update `production.json` and
re-record the baseline in the same PR. Otherwise the gate refuses every run.

Commit it with the gold set. Until one exists the gate runs, measures, and
**fails** with a message saying to record one: a run compared against nothing
is not a pass. After a deliberate improvement, re-record it in the same PR —
and review the diff, because a baseline edited to make a build pass is how
gates die.

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
| CLI wrapper (`eval/cli.py`) | ✅ Implemented and tested |
| Calibration fitting | ✅ Implemented, ⚠️ never fitted on real data |
| Gold-set intake (`eval/intake.py`) | ✅ Implemented and tested, no records yet |
| CI gates | ✅ Wired, ⏭️ skip until a gold set exists |
| Dashboard models | ✅ Implemented, no frontend |
| **Gold dataset** | ❌ **Does not exist** |
| **Any measured result** | ❌ **None** |

"Skip until a gold set exists" was also true of the logic and not of what
anyone saw, until 2026-09-26. The gate skipped by `exit 0` from inside a
step, which GitHub renders as a green pass, so every backend PR showed a
passing accuracy check that measured nothing. And had a gold set been added,
the gate still could not have failed: the runner read only the v1 schema (so
gold records were rejected as malformed), it wrote its report in a shape the
gate did not read (so every metric came back skipped), and a zero baseline
skipped rather than compared (so 0% → 4% hallucination passed). All three are
fixed, and `tests/test_eval_cli.py::TestRunnerFeedsTheGate` pipes a
runner-written regression through the gate and requires `FAILED`.

The two rows above the calibration line were true of the logic and not of the
wrapper around it: until 2026-09-09 `eval/cli.py` — which CI invokes — and
`eval/erroranalysis.py` had no tests at all, 721 lines covered by a claim that
rested on `eval/gates.py` alone. `tests/test_eval_cli.py` now covers both.
"Tested" in this table means there is a test that fails when the behaviour
changes; each row should be read as a claim someone can check.

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
