"""The linter must find the shipped SOP's defects, pass a clean SOP, and catch planted ones.

All three matter. Finding defects in the shipped SOP could be luck; passing a clean SOP
rules out a linter that just complains; catching a NEWLY planted contradiction in that
clean SOP is the only evidence each check is live rather than incidentally satisfied.

Two of this file's tests exist because the linter was WRONG in an earlier version and
reported findings that were artifacts of its own parsing. Those are pinned so they cannot
come back.
"""

from __future__ import annotations

from collections import Counter

import pytest
from tests.conftest import SOP

from sop_screen.lint import (
    IN,
    OUT,
    AgeBand,
    Kind,
    Severity,
    clauses,
    lint_text,
    store_caps,
)

# A deliberately sound SOP: one store, non-overlapping bands, property-based rules,
# image-only evidence, and no block that both asserts and qualifies a precedence.
CLEAN = """\
# Widget Screen SOP

## 1. Rule scope

| Region | Store | Age range |
|---|---|---|
| NA | US | child 12 years or younger |

## 2. In-scope signals (IN_SCOPE indicators)

- Soft plush construction with stitched seams.
- Bright primary colours across the whole body.

## 3. Out-of-scope signals (OUT_OF_SCOPE indicators)

- Rigid metal construction with a fixed pedestal.
- Etched edition numbering on the base.

## 4. Decision

Weigh construction against finish and record which signal decided it.
"""


class TestShippedSopDefects:
    """The four classes found by hand before the linter existed. These are the fixtures."""

    def test_totals_are_pinned(self, sop_findings):
        assert len(sop_findings) == 15
        assert dict(Counter(f.kind.value for f in sop_findings)) == {
            "UNDECIDABLE": 10,
            "UNEVALUABLE": 4,
            "UNLEARNABLE": 1,
        }

    def test_finds_overlapping_age_bands_and_bands_beyond_declared_scope(self, sop_findings):
        rules = Counter(f.rule for f in sop_findings)
        assert rules["age-band-overlap"] == 3
        assert rules["band-exceeds-declared-scope"] == 3
        msgs = " ".join(f.message for f in sop_findings)
        assert "[36mo, inf) -> OUT_OF_SCOPE overlaps [24mo, 84mo) -> IN_SCOPE" in msgs
        assert "[0mo, 168mo) is marked IN_SCOPE for store IN" in msgs

    def test_finds_rules_that_no_image_can_evaluate(self, sop_findings):
        ev = [f for f in sop_findings if f.rule == "evidence-not-available"]
        assert len(ev) == 4
        named = " ".join(f.message for f in ev)
        for catalog_field in ("title", "manufacturer_minimum_age", "age_range_description",
                             "price"):
            assert catalog_field in named
        assert all(f.kind is Kind.UNEVALUABLE for f in ev)

    def test_finds_the_category_split_by_franchise(self, sop_findings):
        hits = [f for f in sop_findings if f.rule == "category-split-by-entity"]
        assert len(hits) == 1
        assert "trading_card" in hits[0].message
        assert "Pokemon cards -> IN_SCOPE" in hits[0].message

    def test_finds_the_entity_listed_while_its_category_is_denied(self, sop_findings):
        hits = [f for f in sop_findings if f.rule == "entity-listed-but-category-denied"]
        assert len(hits) == 1
        assert "Dragon Ball" in hits[0].message and "anime" in hits[0].message

    def test_finds_both_precedence_rules_qualified_away(self, sop_findings):
        hits = [f for f in sop_findings if f.rule == "precedence-vs-qualifier"]
        assert len(hits) == 2

    def test_every_finding_points_at_a_real_line(self, sop_text, sop_findings):
        n_lines = len(sop_text.splitlines())
        for f in sop_findings:
            assert f.lines, f"{f.rule} carries no location"
            assert all(1 <= ln <= n_lines for ln in f.lines), f"{f.rule} cites {f.lines}"


class TestCleanSopPasses:
    def test_a_sound_sop_produces_no_findings(self):
        assert lint_text(CLEAN) == []


class TestFindingRendering:
    def test_a_finding_renders_with_severity_kind_rule_and_lines(self):
        """This string is what a reader actually sees, so assert it rather than assume it."""
        finding = next(
            f for f in lint_text(SOP.read_text(encoding="utf-8"))
            if f.rule == "precedence-vs-qualifier"
        )
        rendered = str(finding)
        assert "BLOCK" in rendered
        assert "UNDECIDABLE" in rendered
        assert "precedence-vs-qualifier" in rendered
        assert all(f"L{n}" in rendered for n in finding.lines)


class TestPlantedContradictionsAreCaught:
    """One plant per check. If a check is dead, its plant survives and the test fails."""

    def test_planted_overlapping_bands(self):
        planted = CLEAN + "\nProducts for 0-8y are IN_SCOPE; 5+ years is OUT_OF_SCOPE.\n"
        hits = [f for f in lint_text(planted) if f.rule == "age-band-overlap"]
        assert hits, "overlapping bands were not caught"
        assert hits[0].severity is Severity.BLOCK

    def test_planted_band_beyond_the_declared_store_cap(self):
        planted = CLEAN + "\n> **US store only:** 0-20y is IN_SCOPE.\n"
        hits = [f for f in lint_text(planted) if f.rule == "band-exceeds-declared-scope"]
        assert hits, "a band exceeding the store's own cap was not caught"
        assert "store US" in hits[0].message

    def test_planted_unevaluable_rule(self):
        planted = CLEAN + "\n| Source field | Interpretation |\n|---|---|\n" \
                          "| age_range_description | bare number -> months |\n"
        hits = [f for f in lint_text(planted) if f.rule == "evidence-not-available"]
        assert hits, "a catalog-attribute rule was not flagged against image-only evidence"

    def test_planted_rule_becomes_evaluable_when_the_evidence_exists(self):
        """The same check must go quiet when the pipeline actually has the field -- this is
        an availability check, not a blanket ban on attribute rules."""
        planted = CLEAN + "\n| Source field | Interpretation |\n|---|---|\n" \
                          "| age_range_description | bare number -> months |\n"
        avail = frozenset({"image", "catalog_attribute"})
        assert [f for f in lint_text(planted, available_evidence=avail)
                if f.rule == "evidence-not-available"] == []

    def test_planted_category_split(self):
        planted = CLEAN + "\nPokemon cards are IN_SCOPE.\n\nMagic: The Gathering is OUT_OF_SCOPE.\n"
        hits = [f for f in lint_text(planted) if f.rule == "category-split-by-entity"]
        assert hits, "a category split across opposite verdicts was not caught"

    def test_planted_precedence_qualified_away(self):
        planted = CLEAN + "\nA play claim takes precedence, though age alone is not sufficient.\n"
        hits = [f for f in lint_text(planted) if f.rule == "precedence-vs-qualifier"]
        assert hits, "a precedence rule qualified away in the same block was not caught"


class TestParserRegressions:
    """Both of these were live defects in the linter, not hypotheticals."""

    def test_a_verdict_wrapped_onto_the_next_source_line_is_still_attached(self, sop_text):
        """The IN-store rule wraps mid-statement. A line-based parser read
        `0-3y / 0-36mo / ... = IN_SCOPE` as OUT_OF_SCOPE because the token sat on the
        following line, and then reported overlaps that were its own invention."""
        band_clause = [c for c in clauses(sop_text) if "0-3y" in c.text]
        assert len(band_clause) == 1
        assert band_clause[0].verdict == IN, "wrapped verdict lost again"
        assert band_clause[0].scope == "IN"

    def test_bands_scoped_to_different_stores_are_not_compared(self):
        """No single evaluation applies two stores' rules, so reporting them as a
        contradiction is an artifact. Same bands, one store: caught. Two stores: silent."""
        one_store = "# S\n\n0-8y is IN_SCOPE; 5+ years is OUT_OF_SCOPE.\n"
        assert [f for f in lint_text(one_store) if f.rule == "age-band-overlap"]

        two_stores = ("# S\n\n> **US store only:** 0-8y is IN_SCOPE.\n"
                      "\n> **CA store only:** 5+ years is OUT_OF_SCOPE.\n")
        assert [f for f in lint_text(two_stores) if f.rule == "age-band-overlap"] == []

    def test_a_longer_field_name_is_not_double_counted_as_its_suffix(self, sop_findings):
        """`manufacturer_minimum_age` contains `minimum_age`; counting both inflated an
        early run's totals and would make the report look worse than the SOP is."""
        row = [f for f in sop_findings
               if f.rule == "evidence-not-available" and "manufacturer_minimum_age" in f.message]
        assert len(row) == 1
        assert row[0].message.count("minimum_age") == 1


class TestAgeArithmetic:
    def test_store_caps_are_read_from_the_sops_own_table(self, sop_text):
        caps = store_caps(sop_text)
        assert caps["US"] == 144 and caps["CA"] == 156 and caps["IN"] == 36
        assert caps["AE"] == caps["SA"] == caps["EG"] == caps["ZA"] == 156

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ((0, 36), (24, 84), True),
            ((0, 36), (36, None), False),   # half-open: touching is not overlapping
            ((36, None), (0, 60), True),
            ((0, 12), (24, 36), False),
            ((36, None), (48, None), True),
        ],
    )
    def test_overlap_is_half_open(self, a, b, expected):
        x = AgeBand(a[0], a[1], IN, 1, "")
        y = AgeBand(b[0], b[1], OUT, 2, "")
        assert x.overlaps(y) is expected
        assert y.overlaps(x) is expected

    def test_a_clause_with_no_verdict_yields_no_bands(self):
        """Otherwise the scope table's own rows would be read as rules."""
        assert lint_text("# S\n\nAges 3-7y are considered separately.\n") == []
