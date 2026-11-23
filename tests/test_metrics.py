"""The metric layer has to demonstrate the illusion, and its own guards have to be able to
fail.

Two tests here are the point of the project: `test_ninety_eight_percent_with_poor_recall`
shows a 98% accuracy figure coexisting with catching under a third of what matters, and
`test_curve_does_not_rise_on_an_uninformative_signal` shows the risk-coverage curve
declining to endorse an abstention policy that is only declining arbitrarily. A curve that
always rose would prove nothing.
"""

from __future__ import annotations

import pytest

from sop_screen.adjudicate import Verdict
from sop_screen.metrics import (
    SelectiveReport,
    accuracy_illusion,
    crosscheck_with_sklearn,
    evaluate,
    implied_accuracy,
    risk_coverage_curve,
)

IN, OUT, ABSTAIN = Verdict.IN_SCOPE, Verdict.OUT_OF_SCOPE, Verdict.ABSTAIN
IN_L, OUT_L = IN.value, OUT.value


def population(*, n_in, n_out, in_caught, out_correct):
    """Build predictions/truths with exactly the requested per-class outcomes."""
    preds = [IN] * in_caught + [OUT] * (n_in - in_caught)
    truths = [IN_L] * n_in
    preds += [OUT] * out_correct + [IN] * (n_out - out_correct)
    truths += [OUT_L] * n_out
    return preds, truths


class TestTheAccuracyIllusion:
    def test_closed_form_matches_the_headline(self):
        """2% base rate, 30% recall, 99.5% specificity -> 98.1%, against a 98.0% baseline."""
        accuracy = implied_accuracy(base_rate=0.02, recall=0.30, specificity=0.995)
        assert accuracy == pytest.approx(0.9811, abs=1e-4)

        constant = implied_accuracy(base_rate=0.02, recall=0.0, specificity=1.0)
        assert constant == pytest.approx(0.98), "always-out-of-scope scores the base rate"
        assert accuracy - constant < 0.002, "the screen leads a constant function by 0.1pp"

    def test_ninety_eight_percent_with_poor_recall(self):
        """THE demonstration: both statements true of one screen at the same time."""
        preds, truths = population(n_in=200, n_out=9800, in_caught=60, out_correct=9751)
        report = evaluate(preds, truths)

        assert report.selective_accuracy > 0.98
        assert report.per_class[IN_L].recall < 0.35
        assert report.balanced_accuracy < 0.65
        assert report.lift_over_baseline < 0.01, "0.1 points from a constant function"

    def test_the_constant_classifier_also_scores_ninety_eight(self):
        """Always saying out-of-scope. The number to which every accuracy claim on an
        imbalanced screen must be compared before it means anything."""
        preds, truths = population(n_in=200, n_out=9800, in_caught=0, out_correct=9800)
        report = evaluate(preds, truths)

        assert report.selective_accuracy == pytest.approx(0.98)
        assert report.per_class[IN_L].recall == 0.0
        assert report.balanced_accuracy == pytest.approx(0.50)
        assert report.lift_over_baseline == pytest.approx(0.0)

    def test_empirical_fixture_confirms_the_closed_form(self):
        predicted, report = accuracy_illusion(n=200_000, seed=7)
        assert report.selective_accuracy == pytest.approx(predicted, abs=0.002)
        assert report.per_class[IN_L].recall == pytest.approx(0.30, abs=0.03)
        assert report.selective_accuracy > 0.98

    def test_lift_can_be_negative(self):
        """A screen can be WORSE than the constant function while still looking decent."""
        preds, truths = population(n_in=200, n_out=9800, in_caught=100, out_correct=9000)
        assert evaluate(preds, truths).lift_over_baseline < 0

    def test_balanced_accuracy_ignores_the_base_rate_that_moves_accuracy(self):
        """Same per-class recalls, different mix: plain accuracy moves a lot, balanced
        accuracy does not move at all. That is the whole argument for reporting it."""
        rare = evaluate(*population(n_in=100, n_out=9900, in_caught=50, out_correct=9900))
        even = evaluate(*population(n_in=5000, n_out=5000, in_caught=2500, out_correct=5000))

        assert rare.balanced_accuracy == pytest.approx(even.balanced_accuracy)
        assert rare.selective_accuracy - even.selective_accuracy > 0.2


class TestAbstentionIsNotFoldedIn:
    def test_abstentions_are_counted_apart_and_attributed_to_true_class(self):
        preds = [IN, ABSTAIN, OUT, ABSTAIN, OUT]
        truths = [IN_L, IN_L, OUT_L, OUT_L, OUT_L]
        report = evaluate(preds, truths)

        assert report.n == 5 and report.n_decided == 3 and report.n_abstained == 2
        assert report.coverage == pytest.approx(0.6)
        assert report.abstained_by_truth == {IN_L: 1, OUT_L: 1}
        assert report.per_class[IN_L].support == 1, "the declined in-scope case is not support"

    def test_declining_everything_scores_zero_not_one(self):
        """The failure mode the pair exists to prevent: a vacuous 100% on the empty set."""
        report = evaluate([ABSTAIN, ABSTAIN, ABSTAIN], [IN_L, OUT_L, OUT_L])
        assert report.coverage == 0.0
        assert report.selective_accuracy == 0.0
        assert report.accuracy_counting_abstention_as_error == 0.0

    def test_the_pessimistic_and_selective_numbers_differ_and_both_are_reported(self):
        preds = [IN, ABSTAIN, OUT, OUT]
        truths = [IN_L, IN_L, OUT_L, OUT_L]
        report = evaluate(preds, truths)
        assert report.selective_accuracy == pytest.approx(1.0)
        assert report.accuracy_counting_abstention_as_error == pytest.approx(0.75)
        rendered = str(report)
        assert "coverage=75.0%" in rendered and "abstentions by true class" in rendered

    def test_ground_truth_may_not_abstain(self):
        with pytest.raises(ValueError, match="may not contain"):
            evaluate([IN], ["ABSTAIN"])

    def test_length_mismatch_is_refused(self):
        with pytest.raises(ValueError, match="2 predictions vs 1 truths"):
            evaluate([IN, OUT], [IN_L])


class TestRiskCoverageCurve:
    def test_curve_rises_when_confidence_ranks_errors(self):
        graded = [(IN, 0.9, IN_L)] * 40 + [(OUT, 0.9, OUT_L)] * 40 + [(IN, 0.1, OUT_L)] * 20
        curve = risk_coverage_curve(graded, [0.0, 0.5])

        (_, cov_lo, acc_lo), (_, cov_hi, acc_hi) = curve
        assert cov_lo == pytest.approx(1.0) and acc_lo == pytest.approx(0.8)
        assert cov_hi == pytest.approx(0.8) and acc_hi == pytest.approx(1.0)

    def test_curve_does_not_rise_on_an_uninformative_signal(self):
        """The guard proving the curve can fail: confidence uncorrelated with correctness
        buys no accuracy, and the curve says so rather than flattering the policy."""
        graded = (
            [(IN, 0.9, IN_L)] * 20 + [(IN, 0.1, IN_L)] * 20
            + [(IN, 0.9, OUT_L)] * 20 + [(IN, 0.1, OUT_L)] * 20
        )
        (_, cov_lo, acc_lo), (_, cov_hi, acc_hi) = risk_coverage_curve(graded, [0.0, 0.5])

        assert cov_hi < cov_lo, "coverage did fall"
        assert acc_hi == pytest.approx(acc_lo), "but accuracy did not improve"

    def test_an_anti_correlated_signal_makes_the_curve_fall(self):
        graded = [(IN, 0.1, IN_L)] * 40 + [(OUT, 0.1, OUT_L)] * 40 + [(IN, 0.9, OUT_L)] * 20
        (_, _, acc_lo), (_, _, acc_hi) = risk_coverage_curve(graded, [0.0, 0.5])
        assert acc_hi < acc_lo

    def test_an_already_abstaining_verdict_stays_abstained_at_any_threshold(self):
        graded = [(ABSTAIN, 1.0, IN_L), (OUT, 1.0, OUT_L)]
        _, coverage, _ = risk_coverage_curve(graded, [0.0])[0]
        assert coverage == pytest.approx(0.5)


class TestSklearnCrosscheck:
    def test_hand_rolled_metrics_agree_exactly(self):
        preds, truths = population(n_in=200, n_out=9800, in_caught=60, out_correct=9751)
        pairs = crosscheck_with_sklearn(preds, truths)

        assert set(pairs) == {"accuracy", "balanced_accuracy", "recall_in_scope",
                             "precision_in_scope", "macro_f1"}
        for name, (mine, theirs) in pairs.items():
            assert mine == pytest.approx(theirs, abs=1e-12), name

    def test_agreement_holds_with_abstentions_present(self):
        """Only the decided subset is comparable -- sklearn has no abstention concept."""
        preds = [IN, ABSTAIN, OUT, IN, OUT, ABSTAIN, OUT]
        truths = [IN_L, IN_L, OUT_L, OUT_L, OUT_L, OUT_L, OUT_L]
        for mine, theirs in crosscheck_with_sklearn(preds, truths).values():
            assert mine == pytest.approx(theirs, abs=1e-12)

    def test_the_crosscheck_actually_fires_on_disagreement(self, monkeypatch):
        """Planted breakage: a crosscheck that cannot fail is decoration."""
        monkeypatch.setattr(SelectiveReport, "macro_f1", property(lambda self: 0.123456))
        preds, truths = population(n_in=50, n_out=50, in_caught=40, out_correct=45)
        with pytest.raises(AssertionError, match="disagree with scikit-learn"):
            crosscheck_with_sklearn(preds, truths)

    def test_mismatched_lengths_are_refused_not_silently_truncated(self):
        """A bare zip would compare a prefix and report agreement. The linter caught this;
        the test keeps it caught."""
        with pytest.raises(ValueError, match="argument 2 is shorter|shorter than argument"):
            crosscheck_with_sklearn([IN, OUT, IN], [IN_L, OUT_L])

    def test_nothing_decided_is_an_error_not_a_pass(self):
        with pytest.raises(ValueError, match="no decided cases"):
            crosscheck_with_sklearn([ABSTAIN, ABSTAIN], [IN_L, OUT_L])


class TestInputValidation:
    @pytest.mark.parametrize("base_rate", [-0.01, 1.01])
    def test_impossible_base_rate_is_refused(self, base_rate):
        with pytest.raises(ValueError, match="outside"):
            implied_accuracy(base_rate, 0.5, 0.5)

    def test_empty_input_does_not_divide_by_zero(self):
        report = evaluate([], [])
        assert report.coverage == 0.0 and report.majority_baseline_accuracy == 0.0
        assert report.balanced_accuracy == 0.0 and report.macro_f1 == 0.0
