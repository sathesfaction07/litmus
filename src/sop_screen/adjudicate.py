"""Adjudication: turn signals into a verdict, or decline to.

The original tool merged signals into a flat deduped union, which is an implicit OR: any
in-scope observation anywhere made the product in-scope. That maximises false positives and
ignores the SOP's own section 5, which states a precedence rule. This module implements the
precedence, weights evidence by where it came from, and has a real ABSTAIN.

ABSTAIN is the point. The linter shows that section 5 contradicts itself: "a single genuine
in-scope claim takes precedence" followed by "an in-scope age alone is not sufficient for
collectibles when all other signals point to adult use". For a metal figurine on a display
base marked "Ages 8+", those two sentences give opposite verdicts. A classifier that returns
IN_SCOPE or OUT_OF_SCOPE there is not being decisive, it is concealing that the
specification does not decide. So the adjudicator abstains and cites the finding.

EVIDENCE TIERS ARE A DECLARED CONVENTION, NOT A MEASUREMENT. They are ordinal on purpose:
the ranking is defensible, specific magnitudes would not be.

  PRINTED (3)  packaging, label -- a printed age grade or claim is a manufacturer statement
  OBJECT  (2)  product_only    -- the object's own visible construction is a fact
  STAGED  (1)  lifestyle, marketing, gift_card, other -- composed by a merchant, and may be
               aspirational rather than descriptive

A staged photo of a child holding a product is evidence about the listing, not about the
product; the SOP treats it as a strong cue, and it is kept as one, but not at the weight of
a printed age grade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .extract import Consolidated, Signal
from .lint import Finding

__all__ = [
    "Decision",
    "EvidenceTier",
    "TIER_BY_IMAGE_TYPE",
    "Verdict",
    "adjudicate",
    "looks_like_age_claim",
    "naive_union_verdict",
]


class Verdict(Enum):
    IN_SCOPE = "IN_SCOPE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ABSTAIN = "ABSTAIN"

    @property
    def is_decision(self) -> bool:
        return self is not Verdict.ABSTAIN


class EvidenceTier(Enum):
    PRINTED = 3
    OBJECT = 2
    STAGED = 1


TIER_BY_IMAGE_TYPE: dict[str, EvidenceTier] = {
    "packaging": EvidenceTier.PRINTED,
    "label": EvidenceTier.PRINTED,
    "product_only": EvidenceTier.OBJECT,
    "lifestyle": EvidenceTier.STAGED,
    "marketing": EvidenceTier.STAGED,
    "gift_card": EvidenceTier.STAGED,
    "other": EvidenceTier.STAGED,
}
_DEFAULT_TIER = EvidenceTier.STAGED

_AGE_CLAIM = re.compile(
    r"\bages?\s*\d+|\b\d+\s*\+\s*(?:y|yrs?|years?|mo|months?)?\b|"
    r"\b\d+\s*-\s*\d+\s*(?:y|yrs?|years?|mo|months?)\b|"
    r"\b(?:age[_ ]?range|minimum[_ ]age|age grading|age grade)\b",
    re.I,
)
_PLAY_CLAIM = re.compile(
    r"\bplay\b|\bplayset\b|\bplay set\b|pretend|role[- ]play|poseable|articulated|"
    r"interchangeable|snap[- ]together|buildable|plush|squish|\bkid|\bchild|toddler|"
    r"\bbaby\b|\bboy\b|\bgirl\b|\btoy\b",
    re.I,
)


def looks_like_age_claim(text: str) -> bool:
    """An age grading, as opposed to a play-value or character observation.

    Section 5's two halves disagree specifically about age-only evidence, so the
    distinction has to be explicit rather than left to a scorer.
    """
    return bool(_AGE_CLAIM.search(text)) and not _PLAY_CLAIM.search(text)


@dataclass(frozen=True)
class WeightedSignal:
    signal: Signal
    tier: EvidenceTier

    @property
    def weight(self) -> int:
        return self.tier.value


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reasons: tuple[str, ...]
    in_scope_score: int
    out_of_scope_score: int
    ambiguous_score: int
    naive_union: Verdict
    spec_caveats: tuple[str, ...] = field(default_factory=tuple)
    decisive: tuple[WeightedSignal, ...] = field(default_factory=tuple)

    @property
    def contradicts_naive_union(self) -> bool:
        """Whether adjudication reached a different answer than the flat OR would have."""
        return self.verdict is not self.naive_union

    def __str__(self) -> str:
        head = (f"{self.verdict.value} (in={self.in_scope_score} out={self.out_of_scope_score} "
                f"ambiguous={self.ambiguous_score}; flat-union would say {self.naive_union.value})")
        return "\n".join([head, *(f"  - {r}" for r in self.reasons)])


def naive_union_verdict(consolidated: Consolidated) -> Verdict:
    """What the original consolidation implied: any in-scope signal wins.

    Kept in the API so a report can show the cheaper answer beside the adjudicated one.
    """
    if consolidated.of_kind("in_scope_signals"):
        return Verdict.IN_SCOPE
    if consolidated.of_kind("out_of_scope_signals"):
        return Verdict.OUT_OF_SCOPE
    return Verdict.ABSTAIN


def _tiers(consolidated: Consolidated) -> dict[str, EvidenceTier]:
    return {
        rec.get("image", ""): TIER_BY_IMAGE_TYPE.get(
            str(rec.get("image_type", "")).lower(), _DEFAULT_TIER
        )
        for rec in consolidated.per_image
    }


def _weigh(signals: tuple[Signal, ...], tiers: dict[str, EvidenceTier]) -> list[WeightedSignal]:
    return [WeightedSignal(s, tiers.get(s.image, _DEFAULT_TIER)) for s in signals]


def adjudicate(
    consolidated: Consolidated,
    *,
    sop_findings: tuple[Finding, ...] = (),
    min_margin: int = 2,
) -> Decision:
    """Weigh evidence by tier, apply section 5's precedence, and abstain when it self-conflicts.

    `min_margin` is the weighted lead one side needs. It defaults to 2 -- one OBJECT-tier
    observation -- so a single STAGED signal never decides a compliance question on its own.
    """
    tiers = _tiers(consolidated)
    in_w = _weigh(consolidated.of_kind("in_scope_signals"), tiers)
    out_w = _weigh(consolidated.of_kind("out_of_scope_signals"), tiers)
    amb_w = _weigh(consolidated.of_kind("ambiguous_signals"), tiers)

    in_score = sum(w.weight for w in in_w)
    out_score = sum(w.weight for w in out_w)
    amb_score = sum(w.weight for w in amb_w)
    naive = naive_union_verdict(consolidated)
    reasons: list[str] = []
    caveats: list[str] = []

    if not in_w and not out_w:
        return Decision(
            Verdict.ABSTAIN,
            ("no in-scope or out-of-scope evidence in any image; "
             f"{len(amb_w)} ambiguous observation(s) cannot decide",),
            in_score, out_score, amb_score, naive,
        )

    # Section 5, first sentence: a genuine play/character claim takes precedence.
    # Second sentence: an AGE claim alone does not, when everything else says adult use.
    # Where both apply, the SOP contradicts itself -- so decline rather than pick.
    non_age_in = [w for w in in_w if not looks_like_age_claim(w.signal.text)]
    age_only_in = bool(in_w) and not non_age_in

    if age_only_in and out_w:
        conflict = [f for f in sop_findings if f.rule == "precedence-vs-qualifier"]
        caveats.extend(
            f"{f.rule} at {','.join(f'L{n}' for n in f.lines)}" for f in conflict
        )
        reasons.append(
            "the only in-scope evidence is an age claim, and out-of-scope evidence is "
            "present: section 5 says a single in-scope claim takes precedence AND that an "
            "age claim alone is not sufficient against adult-use signals. The "
            "specification does not decide this case"
        )
        reasons.append(
            f"age evidence: {'; '.join(w.signal.text for w in in_w)} | "
            f"adult-use evidence: {'; '.join(w.signal.text for w in out_w)}"
        )
        return Decision(
            Verdict.ABSTAIN, tuple(reasons), in_score, out_score, amb_score, naive,
            tuple(caveats), tuple(in_w + out_w),
        )

    # Section 5 grants precedence over "adult or collector claims" -- it says nothing about
    # UNCERTAINTY. Ambiguity at PRINTED tier ("text is partly obscured") is doubt about the
    # printed material itself, so it withdraws the shortcut rather than being outranked by
    # it. Lower-tier ambiguity does not: doubt about a staged photo cannot unseat a legible
    # printed claim. Found by a test that expected the shortcut to yield and it did not.
    printed_doubt = [w for w in amb_w if w.tier is EvidenceTier.PRINTED]
    printed_in = [w for w in non_age_in if w.tier is EvidenceTier.PRINTED]
    if printed_in and printed_doubt:
        reasons.append(
            f"a printed in-scope claim is present but so is printed-tier uncertainty "
            f"({'; '.join(w.signal.text for w in printed_doubt)}), so section 5's "
            "precedence shortcut does not apply and the evidence is weighed instead"
        )
    if printed_in and not printed_doubt:
        reasons.append(
            "section 5 precedence: a printed in-scope claim outranks adult-use signals — "
            + "; ".join(f"{w.signal.text!r} ({w.signal.image})" for w in printed_in)
        )
        if out_w:
            reasons.append(
                f"{len(out_w)} out-of-scope signal(s) present and overridden, not ignored: "
                + "; ".join(w.signal.text for w in out_w)
            )
        return Decision(
            Verdict.IN_SCOPE, tuple(reasons), in_score, out_score, amb_score, naive,
            tuple(caveats), tuple(printed_in),
        )

    margin = in_score - out_score
    if abs(margin) < min_margin:
        reasons.append(
            f"weighted evidence is too close to call: in={in_score} vs out={out_score}, "
            f"margin {margin:+d} under the required {min_margin}"
        )
        if amb_score:
            reasons.append(f"and {len(amb_w)} ambiguous observation(s) worth {amb_score}")
        return Decision(
            Verdict.ABSTAIN, tuple(reasons), in_score, out_score, amb_score, naive,
            tuple(caveats), tuple(in_w + out_w),
        )

    if amb_score >= abs(margin):
        reasons.append(
            f"ambiguous evidence ({amb_score}) is worth at least the deciding margin "
            f"({abs(margin)}), so the lead is not safe: "
            + "; ".join(w.signal.text for w in amb_w)
        )
        return Decision(
            Verdict.ABSTAIN, tuple(reasons), in_score, out_score, amb_score, naive,
            tuple(caveats), tuple(amb_w),
        )

    won = in_w if margin > 0 else out_w
    verdict = Verdict.IN_SCOPE if margin > 0 else Verdict.OUT_OF_SCOPE
    reasons.append(
        f"weighted evidence favours {verdict.value} by {abs(margin)} "
        f"(in={in_score}, out={out_score}), tiers "
        + ", ".join(f"{w.signal.image}:{w.tier.name}" for w in won)
    )
    return Decision(
        verdict, tuple(reasons), in_score, out_score, amb_score, naive,
        tuple(caveats), tuple(won),
    )
