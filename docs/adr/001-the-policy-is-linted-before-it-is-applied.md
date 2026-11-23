# ADR 001 — The policy is linted before it is applied, and the findings are inputs

**Status:** accepted
**Date:** 2026-09-21

## Context

The brief for a screen like this is "classify product images against the SOP", and the obvious
build is: inject the SOP into a vision prompt, collect observations, decide. That treats the
policy as a trusted specification and the model as the only source of error.

Reading the example policy first produced 15 findings across 7 rules on 134 lines. One of them
settles the design:

> Section 5: *"a single genuine in-scope children's or play claim takes precedence over adult
> or collector claims"* ... *"presence of an in-scope age alone is not sufficient for
> collectibles when all other signals point to adult or display use."*

For a metal figurine on a display base marked "Ages 8+", those two sentences return opposite
verdicts. No model quality changes that. Separately, of the four source-field rows in the age
table, **three are catalog attributes** — title and bullets, manufacturer minimum age, age
range description — and only one is visible in a photograph. "High price relative to
equivalent toys" is not in an image either.

So a tool that injects the whole policy and asks for "any attributes the policy uses for
classification" is asking a vision model for fields it cannot see. A model will often supply
them anyway, and by the time a fabricated attribute reaches a consumer it is indistinguishable
from an observed one.

## Decision

`lint.py` runs first and depends on nothing else. Findings are typed by *why* they are
unusable — `UNDECIDABLE`, `UNEVALUABLE`, `UNLEARNABLE` — and are **consumed by two modules
rather than printed for a human**:

- `model.py:build_prompt()` removes every clause cited by an `UNEVALUABLE` finding and states
  in the prompt that lines were withheld and why.
- `adjudicate.py` returns `ABSTAIN` on a case where `precedence-vs-qualifier` applies, carrying
  the rule name and line numbers in `Decision.spec_caveats`.

## Consequences

- An abstention is traceable to the sentence that caused it: `precedence-vs-qualifier at L51,
  L131`, not "low confidence".
- Fixing the policy changes tool behaviour with no code change. That is the intended incentive.
- The linter must be able to go quiet, or it proves nothing. `TestCleanSopPasses` and
  `test_planted_rule_becomes_evaluable_when_the_evidence_exists` enforce that; six
  `test_planted_*` tests enforce the other direction.
- `lint.py` is the largest module (492 lines) in a project whose brief never mentioned linting.
