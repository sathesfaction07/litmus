"""SOP linter: find the defects that cap achievable accuracy before you pay for inference.

A classification SOP is a specification, and specifications can be wrong in ways no
amount of model quality repairs. Three kinds are worth separating, because the fix differs:

  UNDECIDABLE  two rules give OPPOSITE verdicts on a case satisfying both. No classifier
               can be right; the SOP must choose.
  UNEVALUABLE  the rule keys on evidence the pipeline does not have. Injecting it into a
               prompt does not make the evidence appear -- it invites fabrication.
  UNLEARNABLE  the verdict is keyed to a NAMED ENTITY rather than a property, so entities
               nobody listed have no rule at all.

HONEST ABOUT WHAT THIS IS: a heuristic text linter, not a theorem prover. It has false
negatives and cannot find a contradiction phrased in a way it does not recognise. Every
finding carries line numbers and quoted text so a human adjudicates; it never edits the SOP.

PARSING IS CLAUSE-BASED, and that was not the first design. A line-based first version
reported real-looking overlaps that were artifacts of its own parser: the IN-store rule
wraps across two source lines, so `0-3y ... = IN_SCOPE` had its verdict on the next line
and was read as OUT_OF_SCOPE. Findings that are artifacts are worse than no findings, so
blocks are now unwrapped, split into clauses, and each clause takes an explicit verdict
token or inherits one from its section heading.

Age arithmetic is the one place this does real inference rather than pattern-matching:
bands are normalised to months and intersected, so "3+ years = OUT" against "2-7y = IN" is
found by overlap, not by a phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "AgeBand",
    "Clause",
    "Finding",
    "Kind",
    "Severity",
    "clauses",
    "lint_text",
    "store_caps",
]

IN, OUT = "IN_SCOPE", "OUT_OF_SCOPE"


class Kind(Enum):
    UNDECIDABLE = "UNDECIDABLE"
    UNEVALUABLE = "UNEVALUABLE"
    UNLEARNABLE = "UNLEARNABLE"


class Severity(Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"


@dataclass(frozen=True)
class Finding:
    kind: Kind
    severity: Severity
    rule: str
    message: str
    lines: tuple[int, ...]
    quotes: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        loc = ",".join(f"L{n}" for n in self.lines)
        return (f"[{self.severity.value:5s}] {self.kind.value:12s} "
                f"{self.rule} ({loc}): {self.message}")


# ------------------------------------------------------------------- clause segmentation


@dataclass(frozen=True)
class Clause:
    line: int
    text: str
    verdict: str | None
    section: str
    scope: str | None = None  # store code, when the clause declares one


_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_VERDICT_TOKEN = re.compile(r"\b(IN_SCOPE|OUT_OF_SCOPE)\b")
_SECTION_OUT = re.compile(r"out[- ]of[- ]scope|adult\s*/\s*collector", re.I)
_SECTION_IN = re.compile(
    r"in[- ]scope|children's (?:visual )?indicators|children's marketing", re.I
)
_SCOPE_ONLY = re.compile(r"\b([A-Z]{2})\s+store only\b")
# An inline "(US)" scopes a single clause. Without this, the US age rule was compared
# against the IN-store rule and reported as a contradiction no evaluation could meet.
_SCOPE_INLINE = re.compile(r"\(([A-Z]{2})\)")
_CAP_ROW = re.compile(
    r"^\|\s*[A-Z]{2,3}\s*\|\s*([A-Z/]{2,})\s*\|\s*.*?(\d+)\s*(years?|months?)\s*or\s+younger",
    re.I,
)


def store_caps(text: str) -> dict[str, int]:
    """Per-store upper age bound in MONTHS, parsed from the scope table.

    A single global cap is wrong and manufactures contradictions: the US cap is 144mo and
    the IN cap 36mo, so checking a US band against IN's cap invents a conflict that no
    single evaluation could encounter.
    """
    caps: dict[str, int] = {}
    for raw in text.splitlines():
        m = _CAP_ROW.match(raw.strip())
        if not m:
            continue
        n = int(m.group(2))
        months = n if m.group(3).lower().startswith("month") else n * 12
        for store in m.group(1).split("/"):
            caps[store.upper()] = months
    return caps


def _explicit_verdict(text: str) -> str | None:
    found = set(_VERDICT_TOKEN.findall(text))
    if len(found) == 1:
        return found.pop()
    return None  # zero, or both (a mapping row states both and asserts neither)


def _section_verdict(heading: str) -> str | None:
    explicit = _explicit_verdict(heading)
    if explicit:
        return explicit
    if _SECTION_OUT.search(heading):
        return OUT
    if _SECTION_IN.search(heading):
        return IN
    return None


def clauses(text: str) -> list[Clause]:
    """Blank-line blocks, unwrapped, split on `;` and sentence ends.

    Each clause keeps the source line its text STARTED on, so a finding can point at
    something a reader can actually find.
    """
    out: list[Clause] = []
    section, section_v = "", None
    buf: list[tuple[int, str]] = []

    def flush() -> None:
        if not buf:
            return
        joined, line_of = "", []
        for n, raw in buf:
            stripped = raw.strip().lstrip(">").strip()
            stripped = re.sub(r"^[-*]\s+|^\d+\.\s+", "", stripped)
            if joined:
                joined += " "
                line_of.append(n)
            for _ in stripped:
                line_of.append(n)
            joined += stripped
        ms = _SCOPE_ONLY.search(joined)
        block_scope = ms.group(1) if ms else None
        for m in re.finditer(r"[^;]+?(?:\.(?=\s|$)|;|$)", joined):
            frag = m.group(0).strip(" ;.")
            if not frag:
                continue
            start = min(m.start(), len(line_of) - 1) if line_of else 0
            mi = _SCOPE_INLINE.search(frag)
            out.append(
                Clause(line_of[start] if line_of else 0, frag,
                       _explicit_verdict(frag) or section_v, section,
                       block_scope or (mi.group(1) if mi else None))
            )
        buf.clear()

    for n, raw in enumerate(text.splitlines(), 1):
        h = _HEADING.match(raw)
        if h:
            flush()
            section = h.group(1).strip()
            section_v = _section_verdict(section)
            continue
        if raw.strip():
            buf.append((n, raw))
        else:
            flush()
    flush()
    return out


# ------------------------------------------------------------------------------ age bands


@dataclass(frozen=True)
class AgeBand:
    """Half-open interval in MONTHS. `hi=None` means unbounded above."""

    lo: int
    hi: int | None
    verdict: str
    line: int
    quote: str

    def overlaps(self, other: AgeBand) -> bool:
        hi_a = float("inf") if self.hi is None else self.hi
        hi_b = float("inf") if other.hi is None else other.hi
        return self.lo < hi_b and other.lo < hi_a

    def span(self) -> str:
        return f"[{self.lo}mo, {'inf' if self.hi is None else str(self.hi) + 'mo'})"


_YEAR_UNITS = {"y", "yr", "yrs", "year", "years"}
_RANGE = re.compile(r"\b(\d+)\s*-\s*(\d+)\s*(y|yrs?|years?|mo|months?)\b", re.I)
_OPEN = re.compile(r"\b(\d+)\s*\+\s*(y|yrs?|years?|mo|months?)\b", re.I)
_OR_YOUNGER = re.compile(
    r"\b(\d+)\s*(y|yrs?|years?|months?|mo)?\s*(?:or|and)\s+(?:younger|below)\b", re.I
)


def _months(n: int, unit: str | None) -> int:
    if unit and unit.lower().startswith("mo"):
        return n
    if unit and unit.lower() in _YEAR_UNITS:
        return n * 12
    return n * 12  # bare numbers in a band expression are years in this SOP family


def _bands(cl: Clause) -> list[AgeBand]:
    if cl.verdict is None:
        return []
    got: list[AgeBand] = []
    for m in _RANGE.finditer(cl.text):
        got.append(AgeBand(_months(int(m.group(1)), m.group(3)),
                           _months(int(m.group(2)), m.group(3)), cl.verdict, cl.line, cl.text))
    for m in _OPEN.finditer(cl.text):
        got.append(
            AgeBand(_months(int(m.group(1)), m.group(2)), None, cl.verdict, cl.line, cl.text)
        )
    for m in _OR_YOUNGER.finditer(cl.text):
        got.append(
            AgeBand(0, _months(int(m.group(1)), m.group(2)), cl.verdict, cl.line, cl.text)
        )
    return got


def _check_age_bands(cls: list[Clause], caps: dict[str, int]) -> list[Finding]:
    """Bands are compared only where a single product could be judged by both rules.

    Two bands scoped to DIFFERENT stores are not a contradiction -- no evaluation applies
    both -- and reporting them as one was an artifact of an earlier version.
    """
    out: list[Finding] = []
    bands = [(b, c.scope) for c in cls for b in _bands(c)]
    seen: set[tuple] = set()

    for i, (a, sa) in enumerate(bands):
        for b, sb in bands[i + 1 :]:
            if sa is not None and sb is not None and sa != sb:
                continue
            if a.verdict == b.verdict or not a.overlaps(b):
                continue
            key = (a.lo, a.hi, a.verdict, b.lo, b.hi, b.verdict, sa or sb)
            if key in seen:
                continue
            seen.add(key)
            where = f" (store {sa or sb})" if (sa or sb) else ""
            out.append(Finding(
                Kind.UNDECIDABLE, Severity.BLOCK, "age-band-overlap",
                f"{a.span()} -> {a.verdict} overlaps {b.span()} -> {b.verdict}{where}; a "
                "product whose age grading falls in both satisfies two rules with opposite "
                "verdicts",
                tuple(sorted({a.line, b.line})), (a.quote, b.quote),
            ))

    for x, scope in {(x, s) for x, s in bands}:
        cap = caps.get(scope) if scope else None
        if cap is None or x.verdict != IN:
            continue
        if x.hi is None or x.hi > cap:
            out.append(Finding(
                Kind.UNDECIDABLE, Severity.BLOCK, "band-exceeds-declared-scope",
                f"{x.span()} is marked {IN} for store {scope} but that store's declared "
                f"scope ends at {cap}mo, so the band asserts scope the same rule denies",
                (x.line,), (x.quote,),
            ))
    return out


# ------------------------------------------------------------------ evidence availability

FIELD_EVIDENCE: dict[str, str] = {
    "title": "catalog_text",
    "bullets": "catalog_text",
    "product description": "catalog_text",
    "item description": "catalog_text",
    "manufacturer_minimum_age": "catalog_attribute",
    "minimum_age": "catalog_attribute",
    "age_range_description": "catalog_attribute",
    "price": "catalog_attribute",
    "package age visible on image": "image",
    "packaging": "image",
}


def _fields_on(low: str) -> list[tuple[str, str]]:
    """Longest-match first so `manufacturer_minimum_age` is not also counted as
    `minimum_age` -- the substring double-count inflated an early run's totals."""
    hits, consumed = [], []
    for name in sorted(FIELD_EVIDENCE, key=len, reverse=True):
        idx = low.find(name)
        if idx == -1 or any(idx >= s and idx < e for s, e in consumed):
            continue
        consumed.append((idx, idx + len(name)))
        hits.append((name, FIELD_EVIDENCE[name]))
    return hits


def _check_evidence(text: str, available: frozenset[str]) -> list[Finding]:
    out: list[Finding] = []
    for n, raw in enumerate(text.splitlines(), 1):
        low = raw.lower()
        if not (raw.strip().startswith("|") or "price" in low):
            continue
        missing = [(f, k) for f, k in _fields_on(low) if k not in available]
        if not missing:
            continue
        names = ", ".join(f"{f} ({k})" for f, k in sorted(missing))
        out.append(Finding(
            Kind.UNEVALUABLE, Severity.BLOCK, "evidence-not-available",
            f"rule keys on {names} but the pipeline provides only {sorted(available)}; "
            "injecting this rule cannot make the field observable and invites the model to "
            "invent a value",
            (n,), (raw.strip(),),
        ))
    return out


# -------------------------------------------------------------------- entity-keyed rules


def _check_entity_rules(cls: list[Clause], entity_category: dict[str, str]) -> list[Finding]:
    """Verdicts keyed to named entities, and categories split by which entity was listed.

    `entity_category` is an INPUT, not knowledge this linter has. Declaring that Dragon
    Ball is anime is the caller's job; deriving the conflict is the linter's.
    """
    out: list[Finding] = []
    seen: dict[str, list[tuple[int, str, str, str]]] = {}
    for cl in cls:
        if cl.verdict is None:
            continue
        for entity, category in entity_category.items():
            if re.search(rf"(?<!\w){re.escape(entity)}(?!\w)", cl.text, re.I):
                seen.setdefault(category, []).append((cl.line, entity, cl.verdict, cl.text))

    for category, hits in sorted(seen.items()):
        if len({v for _, _, v, _ in hits}) > 1:
            detail = "; ".join(f"{e} -> {v} (L{n})" for n, e, v, _ in hits)
            out.append(Finding(
                Kind.UNDECIDABLE, Severity.BLOCK, "category-split-by-entity",
                f"members of category '{category}' receive opposite verdicts: {detail}. The "
                "line is drawn by which entity was listed, not by any property, so an "
                "unlisted member of the same category has no rule at all",
                tuple(sorted({n for n, _, _, _ in hits})),
                tuple(t for _, _, _, t in hits),
            ))
        else:
            names = sorted({e for _, e, _, _ in hits})
            out.append(Finding(
                Kind.UNLEARNABLE, Severity.WARN, "verdict-keyed-to-entity",
                f"category '{category}' is decided by an explicit list ({', '.join(names)}); "
                "members absent from the list have no rule and will be decided by guesswork",
                tuple(sorted({n for n, _, _, _ in hits})),
            ))
    return out


_DENIAL = re.compile(
    r"\b([A-Z][\w-]*)\s+alone is\b.*?(?:general[- ]use|not\b)|"
    r"\b([A-Z][\w-]*)\b[^.]*\bNOT by itself\b",
    re.I,
)


def _check_category_denial(
    cls: list[Clause], entity_category: dict[str, str]
) -> list[Finding]:
    """A category explicitly denied as an indicator, while a member of it is listed as one.

    This is the Dragon Ball case: it sits in the in-scope character list, and the very next
    clause says anime alone is not a children's indicator. Neither clause is wrong on its
    own; together they decide the same product both ways.
    """
    out: list[Finding] = []
    denied: dict[str, tuple[int, str]] = {}
    for cl in cls:
        m = _DENIAL.search(cl.text)
        if m:
            cat = (m.group(1) or m.group(2) or "").lower()
            if cat:
                denied[cat] = (cl.line, cl.text)

    for cl in cls:
        if cl.verdict != IN:
            continue
        for entity, category in entity_category.items():
            if category.lower() not in denied:
                continue
            if re.search(rf"(?<!\w){re.escape(entity)}(?!\w)", cl.text, re.I):
                dline, dtext = denied[category.lower()]
                out.append(Finding(
                    Kind.UNDECIDABLE, Severity.BLOCK, "entity-listed-but-category-denied",
                    f"'{entity}' is listed as an {IN} indicator while its category "
                    f"'{category}' is explicitly denied as an indicator; a product whose "
                    "only signal is that entity satisfies both rules",
                    tuple(sorted({cl.line, dline})), (cl.text[:160], dtext[:160]),
                ))
    return out


# ----------------------------------------------------------------- precedence conflicts

_PRECEDENCE = re.compile(r"takes precedence|regardless of claims|does not override|overrides", re.I)
_QUALIFIER = re.compile(
    r"alone is not sufficient|not a standalone criterion|not by itself|weigh ", re.I
)


def _check_precedence(text: str) -> list[Finding]:
    out: list[Finding] = []
    buf: list[tuple[int, str]] = []

    def flush() -> None:
        if not buf:
            return
        block = " ".join(r.strip() for _, r in buf)
        p, q = _PRECEDENCE.search(block), _QUALIFIER.search(block)
        if p and q:
            out.append(Finding(
                Kind.UNDECIDABLE, Severity.BLOCK, "precedence-vs-qualifier",
                "the same block asserts a precedence rule and then qualifies it away "
                f"(precedence: {p.group(0)!r}; qualifier: {q.group(0)!r}); a case whose only "
                "in-scope evidence is the qualified kind satisfies both and gets opposite "
                "verdicts",
                (buf[0][0],), (block[:220],),
            ))
        buf.clear()

    for n, raw in enumerate(text.splitlines(), 1):
        if raw.strip() and not _HEADING.match(raw):
            buf.append((n, raw))
        else:
            flush()
    flush()
    return out


# -------------------------------------------------------------------------------- entry

DEFAULT_ENTITY_CATEGORY: dict[str, str] = {
    "Pokemon cards": "trading_card",
    "Magic: The Gathering": "trading_card",
    "Sports collectible cards": "trading_card",
    "Dragon Ball": "anime",
}


def lint_text(
    text: str,
    *,
    available_evidence: frozenset[str] = frozenset({"image"}),
    entity_category: dict[str, str] | None = None,
    caps: dict[str, int] | None = None,
) -> list[Finding]:
    """Every finding, BLOCK before WARN, then by first line.

    `caps` defaults to the per-store bounds parsed out of the SOP's own scope table, so
    the SOP is checked against itself rather than against a number supplied from outside.
    """
    ec = DEFAULT_ENTITY_CATEGORY if entity_category is None else entity_category
    cls = clauses(text)
    findings = [
        *_check_age_bands(cls, store_caps(text) if caps is None else caps),
        *_check_evidence(text, available_evidence),
        *_check_entity_rules(cls, ec),
        *_check_category_denial(cls, ec),
        *_check_precedence(text),
    ]
    return sorted(findings, key=lambda f: (f.severity is Severity.WARN, f.lines[0], f.rule))
