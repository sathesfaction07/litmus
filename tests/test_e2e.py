"""End to end, offline: policy text in, scored verdicts out.

This is the test the Build Checklist asks for ("e2e if pipeline") and the one that would have
caught the module-boundary mistakes the unit tests could not — `prepare_image`'s real
signature, and whether the linter's sop_findings actually reach the prompt and the adjudicator.

Three products, nine images, no credentials, no network. Every stage is the shipped one:

    lint_text  ->  build_prompt  ->  collect(StubClient)  ->  consolidate
                                                                  |
                                          evaluate  <-  adjudicate
"""

from __future__ import annotations

import json

import pytest

from sop_screen import (
    Verdict,
    adjudicate,
    build_prompt,
    collect,
    consolidate,
    crosscheck_with_sklearn,
    evaluate,
)
from sop_screen.lint import Kind


def obs(image_type, *, seen=(), in_scope=(), out_of_scope=(), ambiguous=()):
    return {
        "image_type": image_type,
        "description": f"{image_type} shot",
        "visible_text": list(seen),
        "in_scope_signals": list(in_scope),
        "out_of_scope_signals": list(out_of_scope),
        "ambiguous_signals": list(ambiguous),
    }


# Three products. The truth labels are what a human reviewer would decide, and they are
# deliberately NOT what the flat union would produce for the first two.
PRODUCTS: dict[str, dict] = {
    "toddler-stacking-rings": {
        "truth": Verdict.IN_SCOPE.value,
        "images": {
            "01_box.jpg": obs("packaging", seen=["Ages 6-18 months"],
                              in_scope=["pretend play stacking set", "Ages 6-18 months"]),
            "02_product.jpg": obs("product_only",
                                  in_scope=["soft moulded plastic, chunky rounded parts"]),
            "03_scene.jpg": obs("lifestyle", in_scope=["child shown holding the product"]),
        },
    },
    "pewter-dragon-figurine": {
        # A staged colour cue would flag this under a set union. It is a display piece.
        "truth": Verdict.OUT_OF_SCOPE.value,
        "images": {
            "01_hero.jpg": obs("lifestyle", in_scope=["bright primary colours in backdrop"]),
            "02_product.jpg": obs("product_only",
                                  out_of_scope=["cast pewter construction",
                                                "fixed display pedestal"]),
            "03_base.jpg": obs("label", seen=["Limited edition 44/500"],
                               out_of_scope=["etched edition number on base"]),
        },
    },
    "resin-collector-figure": {
        # The case section 5 answers both ways. Truth is a human call; the tool must abstain.
        "truth": Verdict.IN_SCOPE.value,
        "images": {
            "01_box.jpg": obs("packaging", seen=["Ages 8+"],
                              in_scope=["printed age grading: Ages 8+"]),
            "02_product.jpg": obs("product_only",
                                  out_of_scope=["cast resin", "fixed pedestal base"]),
            "03_side.jpg": obs("marketing", out_of_scope=["collector's edition wording"]),
        },
    },
}


@pytest.fixture(scope="module")
def prompt(sop_text, sop_findings):
    return build_prompt(sop_text, sop_findings)


@pytest.fixture(scope="module")
def run(prompt, sop_findings):
    """The whole pipeline, once, offline."""
    from sop_screen.model import StubClient

    results = {}
    for product, spec in PRODUCTS.items():
        stub = StubClient({name: json.dumps(body) for name, body in spec["images"].items()})
        images = {name: name.encode() for name in spec["images"]}
        merged = consolidate(collect(images, stub, prompt))
        results[product] = (merged, adjudicate(merged, sop_findings=sop_findings))
    return results


class TestThePipelineRunsOffline:
    def test_every_product_produced_a_decision_and_no_failures(self, run):
        assert set(run) == set(PRODUCTS)
        for product, (merged, decision) in run.items():
            assert not merged.failures, f"{product} had transport failures"
            assert len(merged.per_image) == 3
            assert decision.verdict in set(Verdict)

    def test_no_credentials_were_needed(self):
        """The suite must run for someone with no AWS account. boto3 is never imported."""
        import sys

        assert "boto3" not in sys.modules


class TestTheLinterReachesThePrompt:
    def test_unevaluable_policy_lines_are_not_in_the_prompt(self, sop_text, sop_findings, prompt):
        unevaluable = {n for f in sop_findings if f.kind is Kind.UNEVALUABLE for n in f.lines}
        assert unevaluable
        lines = sop_text.splitlines()
        for number in unevaluable:
            text = lines[number - 1].strip()
            if len(text) > 25:
                assert text not in prompt
        assert "were REMOVED from the text above" in prompt

    def test_the_prompt_never_asks_for_catalog_attributes(self, prompt):
        assert "product_attributes" not in prompt


class TestTheVerdictsAreNotTheFlatUnion:
    def test_the_display_piece_is_not_flagged_by_one_staged_cue(self, run):
        merged, decision = run["pewter-dragon-figurine"]
        assert decision.naive_union is Verdict.IN_SCOPE, "the set union would flag it"
        assert decision.verdict is Verdict.OUT_OF_SCOPE
        assert decision.contradicts_naive_union

    def test_the_genuine_toy_is_still_in_scope(self, run):
        """Agreement must remain possible or the layer is only contrarian."""
        _, decision = run["toddler-stacking-rings"]
        assert decision.verdict is Verdict.IN_SCOPE
        assert not decision.contradicts_naive_union

    def test_the_contested_case_abstains_and_cites_the_policy_defect(self, run):
        _, decision = run["resin-collector-figure"]
        assert decision.verdict is Verdict.ABSTAIN
        assert decision.spec_caveats
        assert all("precedence-vs-qualifier" in c for c in decision.spec_caveats)
        assert "specification does not decide" in " ".join(decision.reasons)


class TestScoringTheBatch:
    def test_coverage_and_selective_accuracy_are_reported_as_a_pair(self, run):
        predictions = [decision.verdict for _, decision in run.values()]
        truths = [PRODUCTS[name]["truth"] for name in run]
        report = evaluate(predictions, truths)

        assert report.n == 3
        assert report.n_abstained == 1, "the contested product is declined, not guessed"
        assert report.coverage == pytest.approx(2 / 3)
        assert report.selective_accuracy == pytest.approx(1.0), "both decided cases correct"
        assert report.accuracy_counting_abstention_as_error == pytest.approx(2 / 3)
        assert report.abstained_by_truth[Verdict.IN_SCOPE.value] == 1

    def test_the_abstention_is_not_scored_as_a_wrong_answer_silently(self, run):
        predictions = [decision.verdict for _, decision in run.values()]
        truths = [PRODUCTS[name]["truth"] for name in run]
        report = evaluate(predictions, truths)
        assert report.per_class[Verdict.IN_SCOPE.value].support == 1, (
            "two products are truly in scope; the declined one must not count as support"
        )

    def test_the_decided_subset_agrees_with_sklearn(self, run):
        predictions = [decision.verdict for _, decision in run.values()]
        truths = [PRODUCTS[name]["truth"] for name in run]
        for mine, theirs in crosscheck_with_sklearn(predictions, truths).values():
            assert mine == pytest.approx(theirs, abs=1e-12)


class TestOneBadImageDoesNotSinkTheProduct:
    def test_a_malformed_response_is_recorded_and_the_rest_still_decide(self, prompt, sop_findings):
        from sop_screen.model import StubClient

        spec = PRODUCTS["pewter-dragon-figurine"]["images"]
        responses = {name: json.dumps(body) for name, body in spec.items()}
        responses["02_product.jpg"] = '{"image_type": "product_only", "description" }'

        stub = StubClient(responses)
        merged = consolidate(collect({n: n.encode() for n in responses}, stub, prompt))

        assert len(merged.failures) == 1
        assert len(merged.per_image) == 2
        decision = adjudicate(merged, sop_findings=sop_findings)
        assert decision.verdict in set(Verdict)

    def test_one_lost_image_does_not_move_the_verdict_while_evidence_remains(
        self, prompt, sop_findings
    ):
        """Dropping `02_product.jpg` leaves `03_base.jpg` -- a LABEL, so PRINTED tier --
        still carrying out-of-scope evidence. The verdict rightly stands, and the scores
        show it standing on less. Written the other way round first, which was wrong: I had
        assumed one image held all the evidence, and the e2e test disproved it."""
        from sop_screen.model import StubClient

        spec = dict(PRODUCTS["pewter-dragon-figurine"]["images"])
        del spec["02_product.jpg"]
        stub = StubClient({n: json.dumps(b) for n, b in spec.items()})
        merged = consolidate(collect({n: n.encode() for n in spec}, stub, prompt))

        decision = adjudicate(merged, sop_findings=sop_findings)
        assert decision.verdict is Verdict.OUT_OF_SCOPE
        assert decision.out_of_scope_score == 3, "down from 7 with all three images"

    def test_losing_ALL_decisive_evidence_abstains_rather_than_deciding_anyway(
        self, prompt, sop_findings
    ):
        """With only a staged colour cue left, one STAGED signal must not carry a
        compliance verdict -- the margin bar exists for exactly this."""
        from sop_screen.model import StubClient

        spec = {"01_hero.jpg": PRODUCTS["pewter-dragon-figurine"]["images"]["01_hero.jpg"]}
        stub = StubClient({n: json.dumps(b) for n, b in spec.items()})
        merged = consolidate(collect({n: n.encode() for n in spec}, stub, prompt))

        decision = adjudicate(merged, sop_findings=sop_findings)
        assert decision.verdict is Verdict.ABSTAIN
        assert "too close to call" in " ".join(decision.reasons)
