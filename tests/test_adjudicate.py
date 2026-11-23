"""The adjudicator must overturn the flat union, abstain when the SOP self-conflicts, and
let evidence tier decide.

Each test names the contrast. A layer that always agreed with the cheap union would add
nothing; one that always abstained would add nothing either, so both directions are tested.
"""

from __future__ import annotations

import json

import pytest

from sop_screen.adjudicate import (
    EvidenceTier,
    Verdict,
    adjudicate,
    looks_like_age_claim,
    naive_union_verdict,
)
from sop_screen.extract import consolidate, parse_model_json


def img(name, image_type, *, in_scope=(), out_of_scope=(), ambiguous=()):
    return name, {
        "image_type": image_type,
        "description": f"{image_type} shot",
        "visible_text": [],
        "in_scope_signals": list(in_scope),
        "out_of_scope_signals": list(out_of_scope),
        "ambiguous_signals": list(ambiguous),
        "product_attributes": {},
    }


def build(*images):
    return consolidate(
        {name: parse_model_json(json.dumps(body)) for name, body in images}
    )


class TestFlatUnionIsOverturned:
    def test_a_single_staged_signal_no_longer_carries_the_product(self):
        """The original's implicit OR: one in-scope observation anywhere wins. Here a
        metal display piece with one bright-colours cue in a staged photo is not enough."""
        merged = build(
            img("01_lifestyle.jpg", "lifestyle", in_scope=["bright primary colours"]),
            img("02_product.jpg", "product_only",
                out_of_scope=["cast metal construction", "fixed display pedestal"]),
        )
        assert naive_union_verdict(merged) is Verdict.IN_SCOPE

        decision = adjudicate(merged)
        assert decision.verdict is Verdict.OUT_OF_SCOPE
        assert decision.contradicts_naive_union
        assert decision.in_scope_score == 1 and decision.out_of_scope_score == 4

    def test_the_cheaper_answer_is_reported_alongside_not_discarded(self):
        merged = build(
            img("01.jpg", "lifestyle", in_scope=["bright colours"]),
            img("02.jpg", "product_only", out_of_scope=["metal", "display base"]),
        )
        decision = adjudicate(merged)
        assert decision.naive_union is Verdict.IN_SCOPE
        assert "flat-union would say IN_SCOPE" in str(decision)

    def test_it_does_not_simply_invert_the_union(self):
        """Agreement must still be possible, or the layer is just a contrarian."""
        merged = build(
            img("01.jpg", "packaging", in_scope=["pretend play kitchen playset"]),
            img("02.jpg", "product_only", in_scope=["soft moulded plastic, chunky parts"]),
        )
        decision = adjudicate(merged)
        assert decision.verdict is Verdict.IN_SCOPE
        assert not decision.contradicts_naive_union


class TestAbstainsWhenTheSopSelfConflicts:
    def test_age_only_evidence_against_adult_use_abstains(self, sop_findings):
        """The case section 5 decides both ways: a printed age grade is the ONLY in-scope
        evidence, and everything else says display piece."""
        merged = build(
            img("01_box.jpg", "packaging", in_scope=["printed age grading: Ages 8+"]),
            img("02_product.jpg", "product_only",
                out_of_scope=["cast resin", "fixed pedestal base", "collector's edition"]),
        )
        decision = adjudicate(merged, sop_findings=sop_findings)

        assert decision.verdict is Verdict.ABSTAIN
        assert not decision.verdict.is_decision
        assert "specification does not decide this case" in " ".join(decision.reasons)
        assert decision.spec_caveats, "abstention must cite the linter finding that caused it"
        assert all("precedence-vs-qualifier" in c for c in decision.spec_caveats)

    def test_the_same_product_with_a_play_claim_is_decided(self, sop_findings):
        """Adding ONE non-age in-scope claim removes the contradiction: section 5's first
        sentence applies unambiguously, so the adjudicator stops abstaining."""
        merged = build(
            img("01_box.jpg", "packaging",
                in_scope=["printed age grading: Ages 8+", "pretend play accessories included"]),
            img("02_product.jpg", "product_only",
                out_of_scope=["cast resin", "fixed pedestal base"]),
        )
        decision = adjudicate(merged, sop_findings=sop_findings)
        assert decision.verdict is Verdict.IN_SCOPE
        assert "precedence" in " ".join(decision.reasons)

    def test_overridden_signals_are_reported_not_dropped(self, sop_findings):
        merged = build(
            img("01.jpg", "packaging", in_scope=["role play dress-up set"]),
            img("02.jpg", "product_only", out_of_scope=["premium metal finish"]),
        )
        decision = adjudicate(merged, sop_findings=sop_findings)
        assert decision.verdict is Verdict.IN_SCOPE
        joined = " ".join(decision.reasons)
        assert "overridden, not ignored" in joined
        assert "premium metal finish" in joined

    def test_age_only_evidence_with_no_opposing_signal_is_not_a_conflict(self):
        """Section 5's halves only disagree when adult-use signals are present."""
        merged = build(
            img("01.jpg", "packaging", in_scope=["Ages 3+"]),
            img("02.jpg", "product_only", in_scope=["18-36 months"]),
        )
        assert adjudicate(merged).verdict is Verdict.IN_SCOPE


class TestEvidenceTierDecides:
    SIGNALS = (["character-themed moulding"], ["etched edition number on base"])

    def test_the_same_signals_flip_on_where_they_were_seen(self):
        """Identical texts, opposite verdicts, decided purely by image type. This is the
        weighting the original ignored -- it extracted `image_type` and never used it."""
        in_printed = build(
            img("01.jpg", "packaging", in_scope=self.SIGNALS[0]),
            img("02.jpg", "lifestyle", out_of_scope=self.SIGNALS[1]),
        )
        out_printed = build(
            img("01.jpg", "lifestyle", in_scope=self.SIGNALS[0]),
            img("02.jpg", "packaging", out_of_scope=self.SIGNALS[1]),
        )
        assert adjudicate(in_printed).verdict is Verdict.IN_SCOPE
        assert adjudicate(out_printed).verdict is Verdict.OUT_OF_SCOPE

    def test_tier_lookup_covers_every_image_type_the_prompt_can_emit(self):
        from sop_screen.adjudicate import TIER_BY_IMAGE_TYPE

        emitted = {"lifestyle", "product_only", "packaging", "label", "gift_card",
                   "marketing", "other"}
        assert emitted <= set(TIER_BY_IMAGE_TYPE)

    def test_an_unknown_image_type_is_treated_as_staged_not_trusted(self):
        merged = build(img("01.jpg", "screenshot_of_a_listing", in_scope=["bright colours"]))
        decision = adjudicate(merged)
        assert decision.in_scope_score == EvidenceTier.STAGED.value


class TestAbstainIsNotAWastebasket:
    def test_no_decisive_evidence_at_all_abstains(self):
        merged = build(img("01.jpg", "product_only", ambiguous=["could be either"]))
        decision = adjudicate(merged)
        assert decision.verdict is Verdict.ABSTAIN
        assert "cannot decide" in " ".join(decision.reasons)
        assert decision.naive_union is Verdict.ABSTAIN

    def test_the_flat_union_reports_out_of_scope_when_that_is_all_there_is(self):
        """The union's other branch: no in-scope signal anywhere."""
        merged = build(img("01.jpg", "product_only", out_of_scope=["cast metal"]))
        assert naive_union_verdict(merged) is Verdict.OUT_OF_SCOPE

    def test_a_close_margin_reports_the_ambiguity_it_also_had(self):
        merged = build(
            img("01.jpg", "product_only", in_scope=["soft rounded edges"]),
            img("02.jpg", "lifestyle", out_of_scope=["minimalist styling"],
                ambiguous=["lighting hides the finish"]),
        )
        decision = adjudicate(merged, min_margin=2)
        assert decision.verdict is Verdict.ABSTAIN
        joined = " ".join(decision.reasons)
        assert "too close to call" in joined
        assert "ambiguous observation(s) worth" in joined

    def test_a_margin_under_the_threshold_abstains(self):
        merged = build(
            img("01.jpg", "product_only", in_scope=["soft rounded edges"]),
            img("02.jpg", "lifestyle", out_of_scope=["minimalist styling"]),
        )
        decision = adjudicate(merged, min_margin=2)
        assert decision.verdict is Verdict.ABSTAIN
        assert "too close to call" in " ".join(decision.reasons)
        assert decision.in_scope_score == 2 and decision.out_of_scope_score == 1

    def test_the_same_case_decides_when_the_margin_bar_is_lowered(self):
        """The threshold is a declared policy, so it has to be visibly the thing deciding."""
        merged = build(
            img("01.jpg", "product_only", in_scope=["soft rounded edges"]),
            img("02.jpg", "lifestyle", out_of_scope=["minimalist styling"]),
        )
        assert adjudicate(merged, min_margin=1).verdict is Verdict.IN_SCOPE

    def test_ambiguous_evidence_worth_the_margin_blocks_the_decision(self):
        merged = build(
            img("01.jpg", "packaging", in_scope=["moulded cartoon mascot"]),
            img("02.jpg", "label", ambiguous=["text is partly obscured", "unclear finish"]),
        )
        decision = adjudicate(merged)
        assert decision.verdict is Verdict.ABSTAIN
        assert "not safe" in " ".join(decision.reasons)

    def test_lower_tier_ambiguity_does_not_unseat_a_printed_claim(self):
        """The converse of the rule above, so it cannot become a wastebasket: doubt about a
        STAGED photo must not withdraw section 5's precedence from a legible printed claim."""
        merged = build(
            img("01.jpg", "packaging", in_scope=["moulded cartoon mascot"]),
            img("02.jpg", "lifestyle", ambiguous=["backdrop is cluttered", "angle unclear"]),
        )
        decision = adjudicate(merged)
        assert decision.verdict is Verdict.IN_SCOPE
        assert "precedence" in " ".join(decision.reasons)

    def test_failures_do_not_silently_become_evidence(self):
        from sop_screen.extract import UnparseableResponse

        merged = consolidate({
            "bad.jpg": UnparseableResponse("<<garbage>>", ()),
            "ok.jpg": parse_model_json(json.dumps(img("ok.jpg", "packaging",
                                                      in_scope=["playset"])[1])),
        })
        assert len(merged.failures) == 1
        assert adjudicate(merged).verdict is Verdict.IN_SCOPE


class TestAgeClaimDetection:
    @pytest.mark.parametrize("text", [
        "Ages 8+", "printed age grading: Ages 3+", "3-5 years", "age_range 18+",
        "minimum age 36 months", "age grading 0-14y",
    ])
    def test_age_claims(self, text):
        assert looks_like_age_claim(text)

    @pytest.mark.parametrize("text", [
        "bright pink plush body", "child shown holding the product",
        "pretend play kitchen", "for ages 3+ pretend play set",
        "articulated joints", "cast metal with display base",
    ])
    def test_not_age_only_claims(self, text):
        """The last two matter: an age number NEXT TO a play claim is not age-only
        evidence, so section 5's qualifier does not apply and no abstention is due."""
        assert not looks_like_age_claim(text)
