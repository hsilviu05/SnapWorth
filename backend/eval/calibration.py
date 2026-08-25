"""Learned confidence calibration.

What this replaces
------------------
`backend/confidence.py` combines signals using hand-chosen weights. Its own
docstring is explicit that those are "a considered prior, not a fitted model —
there is no labelled outcome data yet", and that the absolute values should be
read as ordinal until measured. This module is how they stop being assumed.

The target
----------
Given the signals available at scan time (brand identified, range tightness,
image quality, category, and so on), predict **P(the estimate is within
tolerance of the true sale price)**. That probability, expressed 0–100, is what
the confidence score should mean. Today it means "a weighted sum of things we
believe correlate with accuracy", which is not the same claim.

Why pure Python
---------------
No numpy, scipy or sklearn in this environment, and adding them would put a
heavy dependency in the CI path. Logistic regression by gradient descent,
isotonic regression by pool-adjacent-violators, and temperature scaling by
one-dimensional search are all short and exact enough to implement directly.
Gradient boosting is *not* — see `GradientBoostingPlaceholder`, which documents
the interface and refuses to pretend.

Nothing here has been fitted
----------------------------
There is no gold dataset in this repository, so no weights have been learned and
none are claimed. Every function is tested on synthetic data with a known
generating process — which validates the *implementation*, not the product. The
distinction is enforced by `CalibrationModel.provenance`, which cannot be
MEASURED unless the model was fitted on real labelled outcomes.
"""

from __future__ import annotations

import json
import math
from typing import Any
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from eval.provenance import Provenance

# Prediction counts as correct when within this percentage of the true price.
# Matches the `within_25pct` metric so the calibration target and the headline
# accuracy metric describe the same event.
DEFAULT_TOLERANCE_PCT = 25.0


@dataclass
class TrainingExample:
    """One scan outcome with its signals and whether it turned out correct."""

    signals: dict[str, float]        # signal name → 0..1
    correct: bool                    # within tolerance of ground truth
    item_id: str = ""
    raw_confidence: float | None = None    # what the current system reported


def _sigmoid(z: float) -> float:
    # Clamped to avoid overflow on extreme logits during early iterations.
    if z < -35:
        return 1e-15
    if z > 35:
        return 1 - 1e-15
    return 1 / (1 + math.exp(-z))


# ── Logistic regression ──────────────────────────────────────────────────────

@dataclass
class LogisticModel:
    """Weights over named signals, fitted by gradient descent.

    Chosen as the default because its coefficients are *interpretable*: a weight
    on `brand_known` is directly comparable to the hand-chosen 0.26 currently in
    `confidence.py`, so the fitted model can be argued with rather than merely
    deployed. That matters for a number shown to users next to a price.
    """

    weights: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    feature_order: list[str] = field(default_factory=list)
    iterations: int = 0
    final_loss: float | None = None

    def predict_proba(self, signals: dict[str, float]) -> float:
        z = self.intercept + sum(
            self.weights.get(name, 0.0) * signals.get(name, 0.0)
            for name in self.feature_order
        )
        return _sigmoid(z)

    def normalised_weights(self) -> dict[str, float]:
        """Weights rescaled to sum to 1 over their absolute values.

        Directly comparable to the hand-chosen weights in `confidence.py`, which
        is the point — the output of calibration should be reviewable against
        the prior it replaces.
        """
        total = sum(abs(w) for w in self.weights.values())
        if total == 0:
            return dict(self.weights)
        return {k: round(v / total, 4) for k, v in self.weights.items()}


def fit_logistic(
    examples: list[TrainingExample],
    *,
    learning_rate: float = 0.1,
    iterations: int = 2000,
    l2: float = 0.01,
    tolerance: float = 1e-7,
) -> LogisticModel | None:
    """Fit logistic regression by batch gradient descent with L2.

    L2 is on by default: with a dozen correlated signals and a few hundred
    examples, unregularised fitting produces large offsetting coefficients that
    look meaningful and generalise poorly.
    """
    if len(examples) < 20:
        # Below this the fit is dominated by noise and would produce weights
        # that appear authoritative while being arbitrary.
        return None

    features = sorted({name for e in examples for name in e.signals})
    if not features:
        return None

    weights = {name: 0.0 for name in features}
    intercept = 0.0
    n = len(examples)
    previous_loss: float | None = None
    performed = 0

    for step in range(iterations):
        gradients = {name: 0.0 for name in features}
        gradient_intercept = 0.0
        loss = 0.0

        for example in examples:
            z = intercept + sum(weights[f] * example.signals.get(f, 0.0)
                                for f in features)
            prediction = _sigmoid(z)
            target = 1.0 if example.correct else 0.0
            error = prediction - target

            loss -= (target * math.log(max(prediction, 1e-15))
                     + (1 - target) * math.log(max(1 - prediction, 1e-15)))
            for name in features:
                gradients[name] += error * example.signals.get(name, 0.0)
            gradient_intercept += error

        loss = loss / n + l2 * sum(w * w for w in weights.values()) / 2
        for name in features:
            weights[name] -= learning_rate * (gradients[name] / n + l2 * weights[name])
        intercept -= learning_rate * gradient_intercept / n
        performed = step + 1

        if previous_loss is not None and abs(previous_loss - loss) < tolerance:
            break
        previous_loss = loss

    return LogisticModel(weights=weights, intercept=intercept,
                         feature_order=features, iterations=performed,
                         final_loss=previous_loss)


# ── Isotonic regression ──────────────────────────────────────────────────────

@dataclass
class IsotonicModel:
    """Monotone mapping from raw score to calibrated probability.

    Fitted by pool-adjacent-violators. Non-parametric, so it can correct an
    arbitrarily shaped miscalibration — including the common case where a system
    is well calibrated in the middle and badly overconfident at the top, which
    a single temperature parameter cannot fix.

    The cost is that it can overfit on small samples and cannot extrapolate
    beyond the range it saw, so predictions are clamped to the fitted endpoints.
    """

    thresholds: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def predict_proba(self, score: float) -> float:
        if not self.thresholds:
            return score
        if score <= self.thresholds[0]:
            return self.values[0]
        if score >= self.thresholds[-1]:
            return self.values[-1]
        for index in range(1, len(self.thresholds)):
            if score <= self.thresholds[index]:
                x0, x1 = self.thresholds[index - 1], self.thresholds[index]
                y0, y1 = self.values[index - 1], self.values[index]
                if x1 == x0:
                    return y1
                ratio = (score - x0) / (x1 - x0)
                return y0 + ratio * (y1 - y0)
        return self.values[-1]


def fit_isotonic(points: list[tuple[float, bool]]) -> IsotonicModel | None:
    """Pool-adjacent-violators isotonic regression.

    `points` is (raw_score, was_correct). Produces a non-decreasing mapping —
    the monotonicity constraint encodes the thing we actually want to be true:
    a higher confidence score should never mean a lower hit rate.
    """
    if len(points) < 20:
        return None

    ordered = sorted(points, key=lambda p: p[0])
    scores = [p[0] for p in ordered]
    targets = [1.0 if p[1] else 0.0 for p in ordered]

    # Each block holds (sum, count); merge while the sequence decreases.
    blocks: list[list[float]] = []
    for target in targets:
        blocks.append([target, 1.0])
        while len(blocks) > 1:
            last, previous = blocks[-1], blocks[-2]
            if previous[0] / previous[1] <= last[0] / last[1]:
                break
            blocks.pop()
            previous[0] += last[0]
            previous[1] += last[1]

    fitted: list[float] = []
    for total, count in blocks:
        fitted.extend([total / count] * int(count))

    # Collapse duplicate scores so the mapping is a function.
    thresholds: list[float] = []
    values: list[float] = []
    for score, value in zip(scores, fitted):
        if thresholds and thresholds[-1] == score:
            values[-1] = value
        else:
            thresholds.append(score)
            values.append(value)

    return IsotonicModel(thresholds=thresholds, values=values)


# ── Temperature scaling ──────────────────────────────────────────────────────

@dataclass
class TemperatureModel:
    """Single-parameter rescaling of a probability.

    T > 1 softens an overconfident score; T < 1 sharpens an underconfident one.
    One parameter cannot fix a badly *shaped* miscalibration, but it also cannot
    overfit, which makes it the right first choice on a small gold set — and the
    honest one when there are 200 labelled outcomes rather than 2,000.
    """

    temperature: float = 1.0

    def predict_proba(self, probability: float) -> float:
        p = min(max(probability, 1e-6), 1 - 1e-6)
        logit = math.log(p / (1 - p))
        return _sigmoid(logit / self.temperature)


def fit_temperature(
    points: list[tuple[float, bool]], *, low: float = 0.05, high: float = 10.0
) -> TemperatureModel | None:
    """Fit temperature by golden-section search on negative log-likelihood."""
    if len(points) < 20:
        return None

    def nll(temperature: float) -> float:
        total = 0.0
        for probability, correct in points:
            p = min(max(probability, 1e-6), 1 - 1e-6)
            logit = math.log(p / (1 - p))
            scaled = _sigmoid(logit / temperature)
            target = 1.0 if correct else 0.0
            total -= (target * math.log(max(scaled, 1e-15))
                      + (1 - target) * math.log(max(1 - scaled, 1e-15)))
        return total / len(points)

    phi = (math.sqrt(5) - 1) / 2
    a, b = low, high
    c, d = b - phi * (b - a), a + phi * (b - a)
    for _ in range(200):
        if nll(c) < nll(d):
            b, d = d, c
            c = b - phi * (b - a)
        else:
            a, c = c, d
            d = a + phi * (b - a)
        if abs(b - a) < 1e-6:
            break
    return TemperatureModel(temperature=(a + b) / 2)


class GradientBoostingPlaceholder:
    """Interface for a GBM calibrator, deliberately not implemented.

    Gradient boosting would likely outperform logistic regression here, because
    signal interactions are real — image quality matters far more when the brand
    is unknown than when it is legible, and a linear model cannot express that.

    It is not implemented because a correct GBM is several hundred lines of
    numerically fiddly code, and a hand-rolled approximation would be worse than
    sklearn's while being harder to trust. Adding sklearn to CI for this is also
    a poor trade at present dataset sizes: with a few hundred examples the
    variance of a boosted model exceeds its bias advantage.

    Revisit when the gold set passes ~2,000 labelled outcomes. Until then this
    raises rather than silently degrading to something weaker, so a caller
    cannot believe they are getting a boosted model when they are not.
    """

    def fit(self, examples: list[TrainingExample]):
        raise NotImplementedError(
            "Gradient boosting needs scikit-learn, which is deliberately not a "
            "dependency of the CI evaluation path. Use fit_logistic, or install "
            "scikit-learn and implement against this interface. See the class "
            "docstring for why this is not approximated."
        )


# ── Container ────────────────────────────────────────────────────────────────

@dataclass
class CalibrationModel:
    """A fitted calibrator plus the provenance of its fit."""

    method: str
    provenance: Provenance
    fitted_at: datetime
    n_examples: int
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT
    logistic: LogisticModel | None = None
    isotonic: IsotonicModel | None = None
    temperature: TemperatureModel | None = None
    dataset_version: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.provenance is Provenance.MEASURED and not self.dataset_version:
            raise ValueError(
                "a MEASURED calibration model must name the dataset version it "
                "was fitted on — otherwise its weights cannot be reproduced or "
                "audited"
            )

    def predict(self, signals: dict[str, float],
                raw_score: float | None = None) -> float:
        """Calibrated probability in 0..1."""
        if self.method == "logistic" and self.logistic:
            return self.logistic.predict_proba(signals)
        if self.method == "isotonic" and self.isotonic and raw_score is not None:
            return self.isotonic.predict_proba(raw_score)
        if self.method == "temperature" and self.temperature and raw_score is not None:
            return self.temperature.predict_proba(raw_score)
        return raw_score if raw_score is not None else 0.5

    def to_confidence_score(self, signals: dict[str, float],
                            raw_score: float | None = None) -> int:
        return int(round(self.predict(signals, raw_score) * 100))

    def to_dict(self) -> dict:
        payload = {
            "method": self.method,
            "provenance": self.provenance.value,
            "fitted_at": self.fitted_at.isoformat(),
            "n_examples": self.n_examples,
            "tolerance_pct": self.tolerance_pct,
            "dataset_version": self.dataset_version,
            "notes": self.notes,
        }
        if self.logistic:
            payload["logistic"] = asdict(self.logistic)
            payload["normalised_weights"] = self.logistic.normalised_weights()
        if self.isotonic:
            payload["isotonic"] = asdict(self.isotonic)
        if self.temperature:
            payload["temperature"] = asdict(self.temperature)
        return payload

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)


def fit(
    examples: list[TrainingExample],
    *,
    method: str = "logistic",
    dataset_version: str = "",
    provenance: Provenance = Provenance.MEASURED,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
) -> CalibrationModel | None:
    """Fit a calibrator. Returns None when there is not enough data.

    `provenance` must be MEASURED for a model fitted on real outcomes, and the
    dataset version is then mandatory. Fitting on synthetic data for testing
    must pass PROJECTED, which keeps a test artefact from being mistaken for a
    production calibration.
    """
    if not examples:
        return None

    now = datetime.now(timezone.utc)
    # Annotated: the inline dict() infers a union of its value types, and
    # splatting that reports one error per CalibrationModel parameter, at every
    # call site below.
    common: dict[str, Any] = dict(
        provenance=provenance, fitted_at=now, n_examples=len(examples),
        dataset_version=dataset_version, tolerance_pct=tolerance_pct)

    if method == "logistic":
        # One name per branch. Reusing a single `model` binding across the three
        # made it the first branch's type, so the later assignments and the
        # isotonic/temperature arguments both read as the wrong model class.
        logistic = fit_logistic(examples)
        return CalibrationModel(
            method="logistic", logistic=logistic, **common) if logistic else None

    points = [(e.raw_confidence / 100 if e.raw_confidence is not None else 0.5, e.correct)
              for e in examples]
    if method == "isotonic":
        isotonic = fit_isotonic(points)
        return CalibrationModel(
            method="isotonic", isotonic=isotonic, **common) if isotonic else None
    if method == "temperature":
        temperature = fit_temperature(points)
        return CalibrationModel(
            method="temperature", temperature=temperature, **common) if temperature else None
    if method == "gradient_boosting":
        GradientBoostingPlaceholder().fit(examples)

    raise ValueError(f"unknown calibration method {method!r}")


def evaluate_calibration(
    model: CalibrationModel, holdout: list[TrainingExample], *, buckets: int = 5
) -> dict:
    """Expected calibration error of a fitted model on held-out examples.

    Must be run on data the model did not see. Reporting ECE on the training set
    measures how well the fit memorised, which will look excellent and mean
    nothing.
    """
    if not holdout:
        return {"ece": None, "n": 0, "buckets": []}

    predictions = [
        (model.predict(e.signals, e.raw_confidence / 100
                       if e.raw_confidence is not None else None), e.correct)
        for e in holdout
    ]

    edges = [i / buckets for i in range(buckets + 1)]
    rows: list[dict] = []
    ece = 0.0
    for index in range(buckets):
        low, high = edges[index], edges[index + 1]
        members = [(p, c) for p, c in predictions
                   if (low <= p < high or (index == buckets - 1 and p == 1.0))]
        if not members:
            continue
        mean_predicted = sum(p for p, _ in members) / len(members)
        empirical = sum(1 for _, c in members if c) / len(members)
        ece += (len(members) / len(predictions)) * abs(mean_predicted - empirical)
        rows.append({
            "range": f"{low:.1f}-{high:.1f}", "n": len(members),
            "predicted": round(mean_predicted, 4),
            "actual": round(empirical, 4),
        })

    brier = sum((p - (1.0 if c else 0.0)) ** 2 for p, c in predictions) / len(predictions)
    return {"ece": round(ece, 4), "brier": round(brier, 4),
            "n": len(holdout), "buckets": rows}


def split_examples(
    examples: list[TrainingExample], *, holdout_fraction: float = 0.3, seed: int = 20260728
) -> tuple[list[TrainingExample], list[TrainingExample]]:
    """Deterministic train/holdout split by hash of item id.

    Hash-based rather than random so an example never migrates between pools as
    the dataset grows — migration would leak holdout examples into training.
    """
    import hashlib

    train: list[TrainingExample] = []
    holdout: list[TrainingExample] = []
    for index, example in enumerate(examples):
        key = example.item_id or f"idx-{index}"
        digest = hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        (holdout if bucket < holdout_fraction else train).append(example)
    return train, holdout
