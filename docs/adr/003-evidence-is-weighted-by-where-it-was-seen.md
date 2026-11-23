# ADR 003 — Evidence is weighted by where it was seen, on a declared ordinal scale

**Status:** accepted
**Date:** 2026-09-21

## Context

The original merged per-image signals into a flat deduplicated union. That is an implicit OR:
any in-scope cue anywhere makes the product in-scope. A cast-metal display piece with one
"bright primary colours" cue in a staged backdrop gets flagged.

It also extracted `image_type` for every image and then **never used it**, so a printed age
grade on packaging and an aspirational marketing shot carried identical weight. And dedup by
text alone destroyed corroboration: three images independently showing the same thing collapsed
to one string.

## Decision

Signals keep provenance — `Signal(text, image, kind)` — and dedup is by the triple, so
`images_supporting()` can report corroboration. Each signal is weighted by the tier of the
image it came from:

| Tier | Image types | Rationale |
|---|---|---|
| `PRINTED` (3) | `packaging`, `label` | a printed age grade is a manufacturer statement |
| `OBJECT` (2) | `product_only` | the object's visible construction is a fact |
| `STAGED` (1) | `lifestyle`, `marketing`, `gift_card`, `other` | composed by a merchant; may be aspirational |

**The scale is ordinal and declared, not measured.** The ranking is defensible; specific
magnitudes would not be. `0.85 / 0.62 / 0.41` would imply a measurement that does not exist.
Unknown image types default to `STAGED` rather than being trusted.

`min_margin` defaults to 2 — one `OBJECT`-tier observation — so a single `STAGED` cue never
decides a compliance question alone.

Section 5's precedence rule is honoured but **narrowed**: it grants precedence over "adult or
collector claims" and says nothing about *uncertainty*, so printed-tier ambiguity ("text is
partly obscured") withdraws the shortcut while lower-tier ambiguity does not.

## Consequences

- Identical signal texts produce opposite verdicts when the image types swap. That is the
  behaviour the original lacked, and `test_the_same_signals_flip_on_where_they_were_seen` pins it.
- `Decision.naive_union` reports what the set union *would* have said, so the cheaper answer is
  visible beside the adjudicated one rather than discarded.
- The weights are a policy anyone can disagree with, which is why they are a module-level table
  with a rationale rather than constants buried in a scoring function.
- The precedence narrowing came from a failing test, not review. The converse test was added in
  the same pass so the new rule cannot quietly become a wastebasket.
