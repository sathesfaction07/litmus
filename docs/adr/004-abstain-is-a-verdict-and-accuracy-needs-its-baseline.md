# ADR 004 — ABSTAIN is a verdict, and accuracy is never reported without its baseline

**Status:** accepted
**Date:** 2026-09-21

## Context

Two problems, and they are the same problem.

**A classifier that may decline cannot be scored with one number.** Refusing every case scores
100% on the empty set. So a single accuracy figure is not merely incomplete for an abstaining
system, it is gameable.

**A single accuracy figure cannot establish that a rare-class screen works, even without
abstention.** The arithmetic:

```
accuracy = base_rate x recall + (1 - base_rate) x specificity
```

At a 2% in-scope base rate, a screen catching 30% of in-scope products and 99.5% right about
the rest scores **98.11%**. "Always say out-of-scope" scores **98.00%**. A tenth of a point
separates them, and one of them catches nothing.

This project has **no held-out labelled set**. Constructing one in order to argue that some
prior screen was worse than reported would be a fabricated measurement — worse than the number
it replaced.

## Decision

- `Verdict` has three members. `ABSTAIN` is returned where the policy self-conflicts, where the
  weighted margin is under `min_margin`, and where ambiguous evidence is worth at least the
  deciding margin.
- `evaluate()` never folds `ABSTAIN` into either class. The report is a **pair**: `coverage`
  and `selective_accuracy`, plus `accuracy_counting_abstention_as_error` for anyone who insists
  on one number.
- Declining every case yields `coverage 0.0` and `selective_accuracy 0.0` — **not** a vacuous
  100%.
- `majority_baseline_accuracy` and `lift_over_baseline` print on the same screen as accuracy,
  every time.
- `implied_accuracy()` states the arithmetic in closed form, with **no attribution to any real
  system's performance**.
- `risk_coverage_curve()` exists to be able to fail: if selective accuracy does not rise as
  coverage falls, the abstention policy is declining arbitrarily, and that is a finding.
- The counting is four additions, so it is hand-implemented — and `crosscheck_with_sklearn()`
  **raises** on any disagreement beyond 1e-12. `scikit-learn` is a hard dev dependency, not an
  optional extra, so the equivalence is provable on every clone rather than skippable.

## Consequences

- Coverage has to be quoted with accuracy, which is less quotable and more honest.
- Abstention needs justifying, so the uninformative-signal test exists: coverage falls and
  accuracy does not move. A curve that always rose would endorse any policy at all.
- The crosscheck is tested *firing*, via a monkeypatched metric. A crosscheck that cannot fail
  is decoration.
- The project makes **no performance claim about itself**. It demonstrates what an accuracy
  figure can and cannot establish, which does not expire the way a benchmark number does.
