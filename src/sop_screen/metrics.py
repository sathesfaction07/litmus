"""Selective evaluation: what an accuracy figure does and does not tell you.

A single accuracy figure cannot establish that a rare-class screen works. This module does
not make a counter-claim about any particular screen's performance -- it has no held-out
labelled set, and inventing one to argue with would be worse than the number it replaced.
What it shows is narrower and stronger, because it is arithmetic:

    accuracy = base_rate * recall + (1 - base_rate) * specificity

At a 2% in-scope base rate, a screen catching 30% of in-scope products and 99.5% right about
the rest scores 98.11% accuracy. "Always say out-of-scope" scores 98.00% and catches
nothing. So a high-nineties accuracy figure on this task is consistent with missing seven in
ten of the products the screen exists to find, while sitting a tenth of a point from a
constant function. The number is uninformative whoever produced it, which is why this module
reports the majority-class baseline on the same screen as accuracy, always.

`implied_accuracy()` derives that in closed form; `accuracy_illusion()` confirms it on a
fixture clearly labelled synthetic.

The second half is abstention. A classifier that may decline cannot be scored with one
number: refusing every case would score 100% on the empty set. The honest report is a PAIR,
(coverage, selective accuracy), plus the risk-coverage curve that shows whether declining
actually buys accuracy. If selective accuracy does not rise as coverage falls, the
abstention policy is choosing which cases to skip no better than at random, and ABSTAIN has
not earned its place. `risk_coverage_curve()` exists to be able to fail.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .adjudicate import Verdict

__all__ = [
    "ClassMetrics",
    "SelectiveReport",
    "accuracy_illusion",
    "crosscheck_with_sklearn",
    "evaluate",
    "implied_accuracy",
    "risk_coverage_curve",
]

DECIDABLE = (Verdict.IN_SCOPE, Verdict.OUT_OF_SCOPE)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class ClassMetrics:
    label: str
    support: int
    predicted: int
    correct: int

    @property
    def recall(self) -> float:
        """Of the items truly in this class, the fraction the screen placed here.

        For the in-scope class this is the only number a compliance owner cares about.
        """
        return _rate(self.correct, self.support)

    @property
    def precision(self) -> float:
        return _rate(self.correct, self.predicted)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return _rate(2 * p * r, p + r) if (p + r) else 0.0


@dataclass(frozen=True)
class SelectiveReport:
    n: int
    n_decided: int
    counts: dict[tuple[str, str], int]
    per_class: dict[str, ClassMetrics]
    abstained_by_truth: dict[str, int] = field(default_factory=dict)

    @property
    def n_abstained(self) -> int:
        return self.n - self.n_decided

    @property
    def coverage(self) -> float:
        """Fraction of cases the screen was willing to decide."""
        return _rate(self.n_decided, self.n)

    @property
    def n_correct(self) -> int:
        return sum(v for (truth, pred), v in self.counts.items() if truth == pred)

    @property
    def selective_accuracy(self) -> float:
        """Accuracy on the DECIDED subset. Meaningless without coverage beside it."""
        return _rate(self.n_correct, self.n_decided)

    @property
    def accuracy_counting_abstention_as_error(self) -> float:
        """The pessimistic single number, for when someone insists on a single number."""
        return _rate(self.n_correct, self.n)

    @property
    def base_rates(self) -> dict[str, float]:
        return {label: _rate(m.support, self.n) for label, m in self.per_class.items()}

    @property
    def majority_baseline_accuracy(self) -> float:
        """Accuracy of the constant function that always names the commonest class.

        The most important line in the report. A model that does not clearly beat this has
        demonstrated nothing, however high its accuracy looks.
        """
        if not self.n:
            return 0.0
        return max((m.support for m in self.per_class.values()), default=0) / self.n

    @property
    def lift_over_baseline(self) -> float:
        return self.selective_accuracy - self.majority_baseline_accuracy

    @property
    def balanced_accuracy(self) -> float:
        """Mean per-class recall: immune to the base rate that flatters plain accuracy."""
        recalls = [m.recall for m in self.per_class.values() if m.support]
        return sum(recalls) / len(recalls) if recalls else 0.0

    @property
    def macro_f1(self) -> float:
        scores = [m.f1 for m in self.per_class.values() if m.support]
        return sum(scores) / len(scores) if scores else 0.0

    def __str__(self) -> str:
        lines = [
            f"n={self.n}  decided={self.n_decided}  abstained={self.n_abstained}  "
            f"coverage={self.coverage:.1%}",
            f"selective accuracy (on decided)      {self.selective_accuracy:.2%}",
            f"accuracy, abstention counted wrong   "
            f"{self.accuracy_counting_abstention_as_error:.2%}",
            f"always-commonest-class baseline      {self.majority_baseline_accuracy:.2%}",
            f"lift over that baseline              {self.lift_over_baseline:+.2%}",
            f"balanced accuracy (mean recall)      {self.balanced_accuracy:.2%}",
            f"macro F1                             {self.macro_f1:.2%}",
            "",
            f"{'class':<16}{'support':>9}{'base rate':>11}{'recall':>9}{'precision':>11}",
        ]
        for label, m in sorted(self.per_class.items()):
            lines.append(
                f"{label:<16}{m.support:>9}{self.base_rates[label]:>10.1%}"
                f"{m.recall:>9.1%}{m.precision:>11.1%}"
            )
        if self.n_abstained:
            declined = ", ".join(
                f"{k}={v}" for k, v in sorted(self.abstained_by_truth.items()) if v
            )
            lines += ["", f"abstentions by true class: {declined}"]
        return "\n".join(lines)


def evaluate(
    predictions: list[Verdict] | tuple[Verdict, ...],
    truths: list[str] | tuple[str, ...],
) -> SelectiveReport:
    """Score verdicts against binary ground truth, keeping abstentions separate.

    Ground truth is IN_SCOPE / OUT_OF_SCOPE only: a human label is a decision. ABSTAIN is a
    legitimate prediction and is never silently folded into either class, because folding it
    in is how an abstaining classifier gets scored as if it had answered.
    """
    if len(predictions) != len(truths):
        raise ValueError(f"{len(predictions)} predictions vs {len(truths)} truths")
    labels = {v.value for v in DECIDABLE}
    bad = sorted(set(truths) - labels)
    if bad:
        raise ValueError(f"ground truth may not contain {bad}; expected one of {sorted(labels)}")

    counts: dict[tuple[str, str], int] = {}
    abstained: dict[str, int] = {label: 0 for label in labels}
    n_decided = 0
    for pred, truth in zip(predictions, truths, strict=True):
        if pred is Verdict.ABSTAIN:
            abstained[truth] += 1
            continue
        n_decided += 1
        counts[(truth, pred.value)] = counts.get((truth, pred.value), 0) + 1

    per_class = {}
    for label in sorted(labels):
        support = sum(v for (truth, _), v in counts.items() if truth == label)
        predicted = sum(v for (_, pred), v in counts.items() if pred == label)
        per_class[label] = ClassMetrics(
            label=label, support=support, predicted=predicted,
            correct=counts.get((label, label), 0),
        )
    return SelectiveReport(
        n=len(predictions), n_decided=n_decided, counts=counts,
        per_class=per_class, abstained_by_truth=abstained,
    )


def implied_accuracy(base_rate: float, recall: float, specificity: float) -> float:
    """Closed form for the illusion: accuracy is a base-rate-weighted blend.

    No data required. This is why an accuracy figure on an imbalanced screen carries so
    little information -- the second term dominates and has nothing to do with recall.
    """
    if not 0.0 <= base_rate <= 1.0:
        raise ValueError(f"base_rate {base_rate} outside [0, 1]")
    return base_rate * recall + (1.0 - base_rate) * specificity


def accuracy_illusion(
    *,
    base_rate: float = 0.02,
    recall: float = 0.30,
    specificity: float = 0.995,
    n: int = 100_000,
    seed: int = 0,
) -> tuple[float, SelectiveReport]:
    """Derive the headline analytically, then confirm it on a SYNTHETIC fixture.

    Returns (closed-form accuracy, report from generated data). The fixture is synthetic and
    proves nothing about any real screen; the arithmetic is what does the work, and the
    fixture only demonstrates that the arithmetic describes real counts.
    """
    predicted_accuracy = implied_accuracy(base_rate, recall, specificity)
    rng = random.Random(seed)
    predictions: list[Verdict] = []
    truths: list[str] = []
    for _ in range(n):
        in_scope = rng.random() < base_rate
        truths.append(Verdict.IN_SCOPE.value if in_scope else Verdict.OUT_OF_SCOPE.value)
        if in_scope:
            hit = rng.random() < recall
            predictions.append(Verdict.IN_SCOPE if hit else Verdict.OUT_OF_SCOPE)
        else:
            right = rng.random() < specificity
            predictions.append(Verdict.OUT_OF_SCOPE if right else Verdict.IN_SCOPE)
    return predicted_accuracy, evaluate(predictions, truths)


def risk_coverage_curve(
    graded: list[tuple[Verdict, float, str]],
    thresholds: list[float] | tuple[float, ...],
) -> list[tuple[float, float, float]]:
    """(threshold, coverage, selective accuracy) as the abstention bar rises.

    `graded` is (verdict, confidence, truth) per case; a case is declined when its
    confidence falls below the threshold. Selective accuracy should RISE as coverage falls.
    Where it does not, the confidence signal is not ranking errors and the abstention policy
    is declining arbitrarily -- which is a finding, not a bug to hide.
    """
    curve = []
    for threshold in thresholds:
        preds = [
            v if (c >= threshold and v is not Verdict.ABSTAIN) else Verdict.ABSTAIN
            for v, c, _ in graded
        ]
        report = evaluate(preds, [t for _, _, t in graded])
        curve.append((threshold, report.coverage, report.selective_accuracy))
    return curve


def crosscheck_with_sklearn(
    predictions: list[Verdict] | tuple[Verdict, ...],
    truths: list[str] | tuple[str, ...],
    *,
    tolerance: float = 1e-12,
) -> dict[str, tuple[float, float]]:
    """Recompute the decided-subset metrics with scikit-learn and compare.

    Only the decided subset is comparable: sklearn has no abstention concept, which is
    exactly why the selective half of this module is hand-rolled. Raises on disagreement --
    a crosscheck that reports a mismatch without failing will be read as a pass.
    """
    from sklearn.metrics import (  # noqa: PLC0415
        accuracy_score,
        balanced_accuracy_score,
        f1_score,
        precision_score,
        recall_score,
    )

    # strict=True, not a bare zip: a length mismatch here would silently truncate the
    # population and the crosscheck would compare a prefix and pass. Flagged by the linter.
    decided = [
        (p.value, t)
        for p, t in zip(predictions, truths, strict=True)
        if p is not Verdict.ABSTAIN
    ]
    if not decided:
        raise ValueError("no decided cases to crosscheck")
    y_pred = [p for p, _ in decided]
    y_true = [t for _, t in decided]
    mine = evaluate(list(predictions), list(truths))
    positive = Verdict.IN_SCOPE.value

    pairs = {
        "accuracy": (mine.selective_accuracy, accuracy_score(y_true, y_pred)),
        "balanced_accuracy": (mine.balanced_accuracy, balanced_accuracy_score(y_true, y_pred)),
        "recall_in_scope": (
            mine.per_class[positive].recall,
            recall_score(y_true, y_pred, pos_label=positive, zero_division=0),
        ),
        "precision_in_scope": (
            mine.per_class[positive].precision,
            precision_score(y_true, y_pred, pos_label=positive, zero_division=0),
        ),
        "macro_f1": (mine.macro_f1, f1_score(y_true, y_pred, average="macro", zero_division=0)),
    }
    disagreements = {
        name: (a, b) for name, (a, b) in pairs.items() if not math.isclose(a, b, abs_tol=tolerance)
    }
    if disagreements:
        raise AssertionError(f"hand-rolled metrics disagree with scikit-learn: {disagreements}")
    return pairs
