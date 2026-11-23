# Architecture

## The shape of the problem

The obvious design for an image-only compliance screen is a pipe: inject the policy into a
vision prompt, merge what comes back, threshold, report accuracy. The module layout is the
argument that two of those four steps are not safe to perform in that order, and that the
fourth one cannot be a single number.

The policy is not a trusted input. It is the first thing examined, and what it *cannot decide*
propagates forward: into what the model is asked for, and into the verdict's right to exist.

```
  sop/*.md
     │
     ▼
  lint.py ─────────► findings: (rule, lines, kind, severity)
     │                 UNDECIDABLE  two rules answer one case differently
     │                 UNEVALUABLE  the evidence is not in a photograph
     │                 UNLEARNABLE  the rule keys on a named entity, not a property
     │
     ├──────────────────────────┐
     │                          │
     ▼                          ▼
  model.py                  adjudicate.py
  build_prompt()            abstains where the policy self-conflicts,
  drops UNEVALUABLE         citing the finding by rule name and line
  clauses; never asks
  for catalog fields
     │
     ▼
  collect()  ── per image ──►  extract.py
  failures are RETURNED,       parse FIRST; repair only on failure
  never raised past the        repairs are string-aware and DECLINE
  other images                 a malformed array rather than guess
     │                              │
     │                              ▼
     │                         consolidate()
     │                         Signal(text, image, kind) keeps provenance;
     │                         three images agreeing is three pieces of
     │                         evidence, not one deduplicated string
     │                              │
     ▼                              ▼
  UnparseableResponse ──► Consolidated.failures
  (an image that failed is not an image that showed nothing)
                                    │
                                    ▼
                              adjudicate.py
                              tier weights   PRINTED 3 / OBJECT 2 / STAGED 1
                              precedence     section 5, narrowed to claims
                              margin bar     min_margin, declared not tuned
                              ambiguity      printed-tier doubt withdraws the shortcut
                                    │
                                    ▼
                              Decision(verdict, reasons, scores,
                                       naive_union, spec_caveats)
                                    │
                                    ▼
                               metrics.py
                               (coverage, selective accuracy) as a PAIR
                               + majority baseline, always
                               + per-class recall
                               + risk_coverage_curve, which can fail
                               └─ crosscheck_with_sklearn() raises on drift
```

## Why the linter comes first

`lint.py` has no dependency on any other module. Everything else depends on it, directly or by
receiving its output. That direction is the design claim: you cannot improve a classifier's
accuracy on a question its specification does not answer, so establishing *which questions are
answerable* is prior to answering any of them.

Two consumers, and they are the reason the linter is not just a report:

- **`model.py:build_prompt()`** drops every clause cited by an `UNEVALUABLE` finding. Three of
  the four age-rule source fields in the example policy are catalog attributes, not image
  content. Asking a vision model to satisfy them can only produce invention, and a fabricated
  attribute is indistinguishable from an observed one downstream.
- **`adjudicate.py`** takes the findings and, on a case where `precedence-vs-qualifier`
  applies, returns `ABSTAIN` carrying the rule name and line numbers. The abstention is
  traceable to the sentence that caused it.

## Where the four original defects live now

| Defect | Module | What replaced it |
|---|---|---|
| Repair regex ran before `json.loads`, rewriting valid payloads | `extract.py` | parse first; repairs are string-aware, cumulative, re-validated, and recorded in `ParsedPayload.repairs` |
| Set union over signals — an implicit OR | `extract.py` + `adjudicate.py` | `Signal(text, image, kind)` provenance; tier-weighted scoring with a margin bar |
| `image_type` extracted and never used | `adjudicate.py` | ordinal `TIER_BY_IMAGE_TYPE`; identical texts flip the verdict when types swap |
| Whole policy injected, catalog fields requested | `model.py` | prompt built from lint findings; `OBSERVABLE_FIELDS` excludes `product_attributes` |

## Module boundaries

| Module | Depends on | Holds |
|---|---|---|
| `lint.py` | stdlib | clause parsing, 7 rules, 3 finding kinds, store caps, age arithmetic |
| `extract.py` | stdlib (+ Pillow only when an image must shrink) | payload parsing, repair ladder, provenance, `prepare_image` |
| `adjudicate.py` | `lint`, `extract` | tiers, precedence, margin, ambiguity guard, `Verdict` |
| `metrics.py` | `adjudicate` | selective evaluation, closed-form illusion, risk-coverage, sklearn crosscheck |
| `model.py` | `lint`, `extract` | `VisionClient` Protocol, `StubClient`, optional `BedrockClient`, `build_prompt`, `collect` |

Runtime is standard library only. `Pillow` is touched exclusively inside `prepare_image`, and
only when an image exceeds the byte ceiling. `boto3` is imported lazily inside
`BedrockClient._ensure_client` and never at module import, which is why `tests/test_e2e.py`
can assert `"boto3" not in sys.modules` after a full pipeline run.

## Failure is data

Three places refuse to convert a failure into a quiet default, because each of those defaults
is indistinguishable from a real observation:

- A model reply that cannot be parsed becomes an `UnparseableResponse` in
  `Consolidated.failures`. It is *not* an image with no signals.
- `StubClient` raises on an image it has no response for, rather than returning `""`.
- `prepare_image` raises rather than returning an oversized payload, turning a remote failure
  with a poor message into a local one with a good message.

## What the metrics layer is for

`metrics.py` exists because the honest answer to "how accurate is it?" on a rare-class screen
is two numbers and a baseline, not one number. `implied_accuracy()` shows why in closed form:
at a 2% base rate, `accuracy = 0.02·recall + 0.98·specificity`, so the second term dominates
and has nothing to do with catching anything. The module therefore prints the
always-commonest-class baseline on the same screen as accuracy, every time, and
`risk_coverage_curve()` is tested *failing* on an uninformative confidence signal — a curve
that always rose would endorse any abstention policy at all.
