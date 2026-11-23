# Litmus

A library for screening product images against a written classification policy — which
begins by checking whether the policy can be applied at all. It lints the standard operating
procedure for rules that contradict each other or cannot be evaluated from an image, extracts
observations from model responses without corrupting them, adjudicates those observations
under the policy's own precedence rules, and declines to answer when the policy does not
decide. Then it scores itself with metrics that survive a 2% base rate.

## The Problem

An image-only compliance screen is usually built as: inject the whole policy into a prompt,
ask a vision model for observations, merge the observations, threshold, done. Four things go
wrong, and three of them are invisible in the output.

**The policy contradicts itself.** The shipped example SOP (`sop/toys-image-classification.md`,
134 lines) has 15 findings across 7 rules — 14 blocking, 1 warning. The sharpest is section
5: *"a single genuine in-scope children's or play claim takes precedence over adult or
collector claims"* followed by *"presence of an in-scope age alone is not sufficient for
collectibles when all other signals point to adult or display use."* For a metal figurine on
a display base marked "Ages 8+", those two sentences return opposite verdicts. No amount of
model quality fixes that, and a screen that returns a verdict there is concealing it.

**Most of the policy's most precise rules are not in the image.** Of the four source-field
rows in the age table, three are catalog attributes — title and bullets, manufacturer
minimum age, age range description — and only one ("package age visible on image") can be
seen. "High price relative to equivalent toys" is not in an image either. An image-only tool
that injects the whole policy and then asks for *"any attributes the policy uses for
classification"* is inviting the model to report fields it cannot observe.

**Merging observations with a set union is an implicit OR.** Any in-scope cue anywhere makes
the product in-scope. A cast-metal display piece with one bright-colours cue in a staged
photo gets flagged. Meanwhile `image_type` is extracted and then never used, so a printed age
grade and an aspirational marketing shot carry identical weight.

**A repair pass can corrupt valid input.** Running a "fix the JSON" regex *before* trying to
parse means well-formed responses get rewritten too, and a string-blind repair rewrites text
*inside* string values.

And then the whole thing is reported as an accuracy percentage, on a task where the minority
class is a few percent of volume.

## How It Works

Five stages. Each one exists because a cheaper version of it is wrong in a way you cannot see
from its output.

**1. Lint the policy** (`lint.py`) — before trusting any verdict, establish which questions
the policy can answer. Clauses are parsed with their line numbers (77 from the example SOP),
then checked for rule pairs that decide the same case differently, age bands that overlap or
exceed the declared scope, categories split by named entity rather than by any property, and
rules whose evidence is not in an image. Findings are typed by *why* they are unusable:
`UNDECIDABLE` (10), `UNEVALUABLE` (4), `UNLEARNABLE` (1). *Why first:* a finding here is
worth more than a better model, and it is an input to stages 2 and 4 rather than a report
nobody reads.

**2. Ask only for what is in the image** (`model.py`) — `build_prompt()` takes the linter's
`UNEVALUABLE` findings and removes exactly those clauses from the injected policy, then
requests the six fields a photograph can carry. `product_attributes` is not one of them: three
of the four age-rule source fields are catalog data, so that request could only be met by
invention, and a fabricated attribute is indistinguishable from an observed one downstream.
The withholding is stated in the prompt rather than done silently. `VisionClient` is a
Protocol; `StubClient` is deterministic and offline, `BedrockClient` is optional and imports
boto3 lazily.

**3. Extract observations** (`extract.py`) — parse first, repair only on failure, and record
which repairs fired so a recovered payload can be weighted as weaker evidence. Repairs are
string-aware and will **decline** a malformed array rather than guess at it, because guessing
is what silently corrupts evidence. Signals keep their source image and kind, so three
images agreeing is three pieces of evidence rather than one deduplicated string.

**4. Adjudicate** (`adjudicate.py`) — weigh each signal by where it was seen, apply the
policy's precedence rule, and abstain where the policy self-conflicts, citing the lint
finding by line number. Evidence tiers are ordinal and declared, not measured:

| Tier | Image types | Why |
|---|---|---|
| `PRINTED` (3) | `packaging`, `label` | a printed age grade is a manufacturer statement |
| `OBJECT` (2) | `product_only` | the object's visible construction is a fact |
| `STAGED` (1) | `lifestyle`, `marketing`, `gift_card`, `other` | composed by a merchant; may be aspirational rather than descriptive |

Unknown image types default to `STAGED` rather than being trusted. A staged photo of a child
holding a product is evidence about the listing, not about the product — kept as a strong
cue, but not at the weight of printed text.

**5. Score honestly** (`metrics.py`) — report coverage and selective accuracy as a pair, with
the majority-class baseline on the same screen, always. Hand-implemented (the counting is
four additions) and crosschecked against scikit-learn to 1e-12.

## Architecture

```
  sop/*.md ──────► lint.py ──────► findings (rule, lines, kind, severity)
                                        │
                                        │  cited by name+line when the
                                        │  policy does not decide
                                        ▼
  model JSON ────► extract.py ────► Consolidated ────► adjudicate.py ────► Decision
   per image        │  parse first    signals keep      │  tier weights     verdict
                   │  repair on      (text, image,      │  precedence rule  reasons
                   │  failure only   kind) provenance   │  margin bar       naive_union
                   │                                    │  ambiguity guard  spec_caveats
                   └─ UnparseableResponse ──► failures  │
                      (never silently evidence)         │
                                                        ▼
  ground truth ──────────────────────────────────► metrics.py ────► SelectiveReport
                                                    coverage + selective accuracy
                                                    + majority baseline + per-class recall
                                                    └─ crosscheck_with_sklearn (raises)
```

## Project Structure

```
litmus/
├── setup.sh                           # venv + suite + re-derives all 7 claims
├── pyproject.toml
├── ARCHITECTURE.md
├── docs/adr/                          # 4 decision records
├── scripts/
│   └── check_claims.py                # every README number, re-derived; CI + pre-commit
├── sop/
│   └── toys-image-classification.md   # example policy; its defects are the lint fixtures
├── src/sop_screen/
│   ├── __init__.py                    # 36 public names
│   ├── lint.py                        # clause parsing + 7 rules + 3 finding kinds
│   ├── extract.py                     # parse-first payload handling, provenance, images
│   ├── adjudicate.py                  # tiers, precedence, ABSTAIN
│   ├── metrics.py                     # selective evaluation + sklearn crosscheck
│   └── model.py                       # prompt from findings, Protocol, stub + Bedrock
└── tests/
    ├── test_lint.py                   # 25
    ├── test_extract.py                # 34
    ├── test_adjudicate.py             # 30
    ├── test_metrics.py                # 23
    ├── test_model.py                  # 18
    └── test_e2e.py                    # 13  full pipeline, offline
```

## Running

No AWS account, no network, no credentials. The whole suite runs offline.

```bash
bash setup.sh
```

That creates the venv, installs, lints, runs the suite, and then **re-derives all seven
headline claims from the code** — the lint counts, the withheld policy lines, the flat-union
contrast, the abstention with its cited line numbers, the tier flip, the accuracy arithmetic,
and the risk-coverage curve. Nothing in it is quoted from this file.

Or by hand:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest tests/ -q          # 143 passed, 99% coverage
./.venv/bin/python -m ruff check src tests scripts
./.venv/bin/python scripts/check_claims.py      # every number below, re-derived
```

Lint the example policy and see every finding with its line numbers:

```python
from pathlib import Path
from sop_screen import lint_text

for f in lint_text(Path("sop/toys-image-classification.md").read_text()):
    print(f.severity.name, f.rule, [f"L{n}" for n in f.lines], f.kind.name)
```

## Usage

```python
import json
from pathlib import Path
from sop_screen import (
    adjudicate, consolidate, crosscheck_with_sklearn, evaluate,
    implied_accuracy, lint_text, parse_model_json,
)

findings = tuple(lint_text(Path("sop/toys-image-classification.md").read_text()))

# one parsed payload per image; parse_model_json repairs only on failure
merged = consolidate({
    "01_box.jpg": parse_model_json(response_for_box),
    "02_product.jpg": parse_model_json(response_for_product),
})

decision = adjudicate(merged, sop_findings=findings, min_margin=2)
print(decision)                     # verdict, reasons, scores, and the flat-union answer
decision.spec_caveats               # lint findings that caused an abstention
decision.contradicts_naive_union    # whether weighing changed the answer
```

Scoring, with the baseline that makes the number mean something:

```python
report = evaluate(predictions, truths)   # predictions may include Verdict.ABSTAIN
print(report)
crosscheck_with_sklearn(predictions, truths)   # raises if the hand-rolled metrics drift
```

That report on a screen catching 30% of a 2% minority class:

```
n=10000  decided=10000  abstained=0  coverage=100.0%
selective accuracy (on decided)      98.11%
accuracy, abstention counted wrong   98.11%
always-commonest-class baseline      98.00%
lift over that baseline              +0.11%
balanced accuracy (mean recall)      64.75%
macro F1                             68.94%

class             support  base rate   recall  precision
IN_SCOPE              200      2.0%    30.0%      55.0%
OUT_OF_SCOPE         9800     98.0%    99.5%      98.6%
```

`implied_accuracy(0.02, 0.30, 0.995)` returns that 98.11% in closed form, without data:
`accuracy = base_rate * recall + (1 - base_rate) * specificity`. This is arithmetic about the
metric, not a measurement of anybody's screen — there is no labelled set here, and inventing
one to argue with would be worse than the figure it replaced.

## Testing

```
tests/test_lint.py        25 passed
tests/test_extract.py     34 passed
tests/test_adjudicate.py  30 passed
tests/test_metrics.py     23 passed
tests/test_model.py       18 passed
tests/test_e2e.py         13 passed   full pipeline, offline
                         143 passed

99% statement coverage, enforced by `--cov-fail-under=98` in `setup.sh` and CI. The five
uncovered statements are named rather than chased: two defensive `continue` guards in
`lint.py` (an empty clause fragment, a duplicate finding key) and the lazy `boto3` import in
`BedrockClient._ensure_client`. Contriving input to reach the guards would assert nothing
about behaviour, and the import path depends on whether boto3 is installed.
```

Five of these exist specifically to make sure a guard can fail, because a guard that passes
may be passing on nothing:

- **`test_original_silently_corrupted_valid_input`** (5 parametrised cases) reproduces the
  *original* repair regex verbatim and asserts it changes the meaning of valid payloads while
  still returning parseable JSON — the trap is that nothing raises. The replacement is
  asserted byte-identical on the same inputs. A test that only checked the new code would not
  notice the defect returning.
- **`TestPlantedContradictionsAreCaught`** (6 tests) adds a fresh defect to a clean policy —
  overlapping bands, a band beyond the declared store cap, an unevaluable rule, a category
  split by entity, a precedence rule qualified away — and requires the linter to find each.
  Its converse, `test_planted_rule_becomes_evaluable_when_the_evidence_exists`, requires the
  linter to go *quiet* when the evidence is available; alongside `TestCleanSopPasses`, that is
  what stops a linter which merely always complains from looking like a working one.
- **`test_curve_does_not_rise_on_an_uninformative_signal`** feeds `risk_coverage_curve` a
  confidence signal uncorrelated with correctness: coverage falls and accuracy does not move.
  The curve declines to endorse an abstention policy that is only declining arbitrarily.
- **`test_the_crosscheck_actually_fires_on_disagreement`** monkeypatches one metric so the
  scikit-learn comparison must raise. A crosscheck that cannot fail is decoration.
- **`test_losing_ALL_decisive_evidence_abstains_rather_than_deciding_anyway`** strips the
  images carrying the decisive evidence and requires the tool to stop deciding. Its sibling
  checks the opposite: while printed evidence remains, the verdict rightly stands. I wrote the
  first one backwards — I had assumed a single image held all the evidence, and the end-to-end
  test disproved it.

`scripts/check_claims.py` covers what tests cannot: it re-derives every figure printed in this
README and fails if any has drifted, and it verifies that every test name and symbol named in
back-ticks here actually exists in the source. It runs in CI and as a pre-commit hook. It was
added because two test names in an earlier draft of this file did not exist.

## Configuration

Two policy knobs, both declared rather than tuned, both visible in the output:

| Knob | Default | Meaning |
|---|---|---|
| `min_margin` | `2` | weighted lead a side needs to win. 2 = one `OBJECT`-tier observation, so a single `STAGED` cue never decides a compliance question alone. |
| `TIER_BY_IMAGE_TYPE` | see table above | ordinal evidence tiers. Editing this changes verdicts, which is the point — `test_the_same_signals_flip_on_where_they_were_seen` pins it. |

`[dev]` installs pytest, ruff, **scikit-learn and pillow**. Both are hard dev dependencies on
purpose: the metric equivalence must be provable on every clone rather than skippable, and
the RGBA regression test must actually *run*. A skipped regression test is the defect coming
back unnoticed.

## Tradeoffs and Decisions I Made Along the Way

**I put the linter first, and it turned out to be the whole project.** I expected to spend
my time on extraction quality. Instead the first pass over the policy found that section 5
answers the same case both ways, and after that "improve the classifier" stopped being the
interesting problem. You cannot raise accuracy on a question the specification does not
answer. So the linter's findings became inputs to the adjudicator rather than a report
nobody reads — when it abstains, it cites the rule and the line numbers.

**ABSTAIN was the hard call, and the risk-coverage curve is what stops it being a cop-out.**
An abstaining classifier can score 100% by refusing everything, so a single accuracy number
is not merely incomplete, it is gameable. Reporting `(coverage, selective accuracy)` as a
pair fixes that, and the curve tests whether declining actually buys accuracy. I wrote the
uninformative-signal test before I trusted my own abstention policy, and I would not have
believed the curve without it.

**Evidence tiers are ordinal and I am not going to pretend otherwise.** I wanted weights with
a justification, and 3/2/1 with a written rationale is honest in a way that 0.85/0.62/0.41
would not be. The ranking is defensible — printed manufacturer text outranks a composed
marketing photo — and the magnitudes are a convention. `min_margin` is the same: a declared
policy, not a measurement, and the test that flips the verdict by lowering it makes that
visible rather than buried.

**I narrowed the precedence rule after a test failed and I had to go re-read the policy.**
A printed in-scope claim was short-circuiting to a verdict before ambiguous evidence was
considered, so "text is partly obscured" on a label could not block it. Section 5 grants
precedence over *adult or collector claims* and says nothing about uncertainty — so
printed-tier doubt now withdraws the shortcut while staged-tier doubt does not, and I added
the converse test in the same pass so the new rule cannot quietly become a wastebasket.

**Ruff found a defect that 22 passing tests did not.** `crosscheck_with_sklearn` zipped
predictions against ground truth without checking lengths, so a mismatch would have compared
a prefix and reported agreement — in the function whose entire job is catching that class of
error. `strict=True`, plus a test. I have stopped treating linter output as style noise.

**The prompt is built from the linter's output, which I did not plan and now think is the best
idea in the project.** I had `build_prompt` slated as plumbing. But the linter already knows
which clauses cannot be evaluated from a photograph, and injecting those clauses anyway is what
invites a model to invent catalog attributes. So the findings drive the prompt, the withheld
lines are declared in it, and `product_attributes` is gone. That closes the loop: the policy's
own defects now constrain what the tool is willing to ask, not just what it is willing to
answer.

**The end-to-end test earned its place immediately.** It caught two things the 105 unit tests
could not: `prepare_image`'s real signature — I had written `model.py` against a remembered
two-argument version and it takes three, returning a media type I was hardcoding — and a false
premise in my own fixture about which image carried the decisive evidence. Module boundaries
are where remembered APIs go wrong.

**What is not here.** Nothing in this
repo has been evaluated against a real labelled set, so it makes no performance claim at all
— the metrics module demonstrates what an accuracy figure can and cannot establish, which is
a different and more durable thing. The example policy is a sanitized stand-in: its defects
are real defect *shapes*, authored to be reproducible by anyone who clones this.

## Stack

Python 3.10+, standard library only at runtime. `pytest` + `ruff` + `scikit-learn` + `pillow`
for development, all four hard dev dependencies so no check is skippable. `[bedrock]` adds
`boto3` + `pillow` for real inference; `BedrockClient` imports boto3 lazily and accepts an
injected client, so its request construction is tested without the dependency, without
credentials and without a network.
