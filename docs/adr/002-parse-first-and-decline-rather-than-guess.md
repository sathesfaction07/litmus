# ADR 002 — Parse first, repair only on failure, and decline rather than guess

**Status:** accepted
**Date:** 2026-09-21

## Context

Model replies are usually valid JSON and occasionally are not, so a repair pass is reasonable.
The original ran its repair regex **unconditionally, before `json.loads`**. Two failures follow,
and neither is visible in the output:

1. A valid payload is rewritten. `{"description": "The box says \"Ages 3+\" 5 pieces included"}`
   comes back changed — and **still parses**, so nothing raises. The trap is not a crash; it is
   a silently altered transcription.
2. The repair was string-blind. A quoted run inside a string *value* looks identical to a
   missing colon between a key and its value.

`["a" "b"]` has no correct repair. A guess produces `["a", "b"]` or `["a b"]` and there is no
evidence in the text for either.

## Decision

- `json.loads` runs **first**. Repairs happen only after a parse failure.
- Four repairs apply cumulatively with re-validation after each; `ParsedPayload.repairs` records
  which fired and `was_clean` lets a consumer weight a recovered payload as weaker evidence.
- `_insert_missing_colons` fires **only in a key slot** — directly after `{`, or after a `,`
  whose innermost container is an object — so text inside a string value is untouched.
- A malformed array **raises `UnparseableResponse`** carrying the original text, rather than
  being guessed at.
- A failure is returned as data. `collect()` puts it in the result and `consolidate()` keeps it
  in `Consolidated.failures` — an image that failed is not an image that showed nothing.

## Consequences

- The regression test reproduces the original regex **verbatim** and asserts it corrupts valid
  input across 5 parametrised cases while the replacement leaves it byte-identical. A test that
  only asserted the new code was correct would not notice the defect returning.
- Some responses now fail loudly that previously "succeeded" with altered content. That is the
  point; the alternative is corrupted evidence that looks fine.
- `repairs` and `was_clean` are exposed, so a downstream report can discount a recovered
  payload instead of treating all payloads as equal.
