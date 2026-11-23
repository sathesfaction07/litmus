#!/usr/bin/env bash
# Installs, tests, and re-derives every claim in the README from the code rather than
# quoting it. No AWS account, no credentials, no network.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if ! command -v $PY >/dev/null 2>&1; then
  echo "python3 not found."
  case "$(uname -s)" in
    Darwin) echo "  macOS:  brew install python@3.12" ;;
    Linux)  echo "  Debian/Ubuntu:  sudo apt-get install -y python3 python3-venv" ;;
    *)      echo "  Windows:  install from https://www.python.org/downloads/" ;;
  esac
  exit 1
fi

$PY - <<'PY' || { echo "Python >= 3.10 required (pyproject requires-python = '>=3.10')."; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if [ ! -d .venv ]; then
  $PY -m venv .venv
fi
# bin/ on POSIX, Scripts/ on Windows.
VENV_BIN=".venv/bin"
[ -d "$VENV_BIN" ] || VENV_BIN=".venv/Scripts"

"$VENV_BIN/python" -m pip install -q --upgrade pip
"$VENV_BIN/python" -m pip install -q -e ".[dev]"

"$VENV_BIN/python" -m ruff check src tests
"$VENV_BIN/python" -m pytest -q --cov=sop_screen --cov-report=term-missing \
  --cov-fail-under=98

echo
echo "═══ 1. The policy cannot answer every question put to it ═══"
"$VENV_BIN/python" - <<'PY'
from collections import Counter
from pathlib import Path
from sop_screen import clauses, lint_text

text = Path("sop/toys-image-classification.md").read_text(encoding="utf-8")
findings = list(lint_text(text))
by_kind = Counter(f.kind.name for f in findings)
by_rule = Counter(f.rule for f in findings)

print(f"  {len(text.splitlines())} lines, {len(clauses(text))} clauses parsed")
print(f"  {len(findings)} findings across {len(by_rule)} rules: {dict(by_kind)}")
for rule, n in sorted(by_rule.items()):
    lines = sorted({ln for f in findings if f.rule == rule for ln in f.lines})
    print(f"    {rule:<34} x{n}  L{','.join(str(x) for x in lines)}")

assert len(findings) == 15, len(findings)
assert len(by_rule) == 7, len(by_rule)
assert by_kind == {"UNDECIDABLE": 10, "UNEVALUABLE": 4, "UNLEARNABLE": 1}, by_kind
PY

echo
echo "═══ 2. The linter decides what the model is asked for ═══"
"$VENV_BIN/python" - <<'PY'
from pathlib import Path
from sop_screen import OBSERVABLE_FIELDS, build_prompt, lint_text
from sop_screen.lint import Kind

text = Path("sop/toys-image-classification.md").read_text(encoding="utf-8")
findings = tuple(lint_text(text))
prompt = build_prompt(text, findings)
dropped = {n for f in findings if f.kind is Kind.UNEVALUABLE for n in f.lines}

print(f"  {len(dropped)} policy line(s) withheld because a photograph cannot carry them")
print(f"  fields requested: {', '.join(OBSERVABLE_FIELDS)}")
print("  'product_attributes' requested: "
      f"{'yes' if 'product_attributes' in prompt else 'no -- it could only be invented'}")

assert "product_attributes" not in prompt
assert "were REMOVED from the text above" in prompt
lines = text.splitlines()
for n in dropped:
    body = lines[n - 1].strip()
    if len(body) > 25:
        assert body not in prompt, n
PY

echo
echo "═══ 3. A set union over signals is an implicit OR, and it over-flags ═══"
"$VENV_BIN/python" - <<'PY'
import json
from pathlib import Path
from sop_screen import adjudicate, collect, consolidate, lint_text
from sop_screen.model import StubClient

def obs(kind, **kw):
    base = {"image_type": kind, "description": "", "visible_text": [],
            "in_scope_signals": [], "out_of_scope_signals": [], "ambiguous_signals": []}
    base.update(kw)
    return base

findings = tuple(lint_text(Path("sop/toys-image-classification.md").read_text()))
images = {
    "01_hero.jpg": obs("lifestyle", in_scope_signals=["bright primary colours in backdrop"]),
    "02_product.jpg": obs("product_only",
                          out_of_scope_signals=["cast pewter construction",
                                                "fixed display pedestal"]),
}
stub = StubClient({n: json.dumps(b) for n, b in images.items()})
merged = consolidate(collect({n: n.encode() for n in images}, stub, "prompt"))
decision = adjudicate(merged, sop_findings=findings)

print(f"  {decision}")
assert decision.naive_union.value == "IN_SCOPE"
assert decision.verdict.value == "OUT_OF_SCOPE"
assert decision.contradicts_naive_union
PY

echo
echo "═══ 4. Where the policy contradicts itself, the tool declines and cites the line ═══"
"$VENV_BIN/python" - <<'PY'
import json
from pathlib import Path
from sop_screen import adjudicate, collect, consolidate, lint_text
from sop_screen.model import StubClient

def obs(kind, **kw):
    base = {"image_type": kind, "description": "", "visible_text": [],
            "in_scope_signals": [], "out_of_scope_signals": [], "ambiguous_signals": []}
    base.update(kw)
    return base

findings = tuple(lint_text(Path("sop/toys-image-classification.md").read_text()))
images = {
    "01_box.jpg": obs("packaging", in_scope_signals=["printed age grading: Ages 8+"]),
    "02_product.jpg": obs("product_only",
                          out_of_scope_signals=["cast resin", "fixed pedestal base",
                                                "collector's edition"]),
}
stub = StubClient({n: json.dumps(b) for n, b in images.items()})
merged = consolidate(collect({n: n.encode() for n in images}, stub, "prompt"))
decision = adjudicate(merged, sop_findings=findings)

print(f"  {decision}")
print(f"  caveats: {decision.spec_caveats}")
assert decision.verdict.value == "ABSTAIN"
assert decision.spec_caveats and all("precedence-vs-qualifier" in c for c in decision.spec_caveats)
PY

echo
echo "═══ 5. Identical evidence, opposite verdicts, decided by WHERE it was seen ═══"
"$VENV_BIN/python" - <<'PY'
import json
from sop_screen import adjudicate, consolidate
from sop_screen.extract import parse_model_json

def build(*pairs):
    payloads = {}
    for name, kind, key, text in pairs:
        payloads[name] = parse_model_json(json.dumps({
            "image_type": kind, "description": "", "visible_text": [],
            "in_scope_signals": [text] if key == "in" else [],
            "out_of_scope_signals": [text] if key == "out" else [],
            "ambiguous_signals": [],
        }))
    return consolidate(payloads)

A = build(("01.jpg", "packaging", "in", "character-themed moulding"),
          ("02.jpg", "lifestyle", "out", "etched edition number on base"))
B = build(("01.jpg", "lifestyle", "in", "character-themed moulding"),
          ("02.jpg", "packaging", "out", "etched edition number on base"))
a, b = adjudicate(A).verdict.value, adjudicate(B).verdict.value
print(f"  claim printed / counter-claim staged -> {a}")
print(f"  claim staged  / counter-claim printed -> {b}")
assert a == "IN_SCOPE" and b == "OUT_OF_SCOPE"
PY

echo
echo "═══ 6. What an accuracy figure does and does not establish ═══"
"$VENV_BIN/python" - <<'PY'
from sop_screen import crosscheck_with_sklearn, evaluate, implied_accuracy
from sop_screen.adjudicate import Verdict

IN, OUT = Verdict.IN_SCOPE, Verdict.OUT_OF_SCOPE
for recall in (0.0, 0.30, 0.60, 1.0):
    print(f"  recall {recall:.2f} at a 2% base rate -> accuracy "
          f"{implied_accuracy(0.02, recall, 0.995):.3%}")

preds = [IN] * 60 + [OUT] * 140 + [OUT] * 9751 + [IN] * 49
truths = [IN.value] * 200 + [OUT.value] * 9800
report = evaluate(preds, truths)
print()
for line in str(report).splitlines():
    print(f"  {line}")

pairs = crosscheck_with_sklearn(preds, truths)
print(f"\n  scikit-learn agrees on all {len(pairs)} metrics to 1e-12")

assert report.selective_accuracy > 0.98
assert report.per_class[IN.value].recall == 0.30
assert report.lift_over_baseline < 0.002
assert report.balanced_accuracy < 0.65
PY

echo
echo "═══ 7. Abstention is only worth something if declining buys accuracy ═══"
"$VENV_BIN/python" - <<'PY'
from sop_screen import risk_coverage_curve
from sop_screen.adjudicate import Verdict

IN, OUT = Verdict.IN_SCOPE, Verdict.OUT_OF_SCOPE
informative = ([(IN, 0.9, IN.value)] * 40 + [(OUT, 0.9, OUT.value)] * 40
               + [(IN, 0.1, OUT.value)] * 20)
useless = ([(IN, 0.9, IN.value)] * 20 + [(IN, 0.1, IN.value)] * 20
           + [(IN, 0.9, OUT.value)] * 20 + [(IN, 0.1, OUT.value)] * 20)

for label, graded in (("confidence ranks errors", informative),
                      ("confidence is noise   ", useless)):
    row = " | ".join(
        f"cov {c:.0%} acc {a:.0%}" for _, c, a in risk_coverage_curve(graded, [0.0, 0.5])
    )
    print(f"  {label}: {row}")

(_, _, lo), (_, _, hi) = risk_coverage_curve(informative, [0.0, 0.5])
assert hi > lo, "a real signal must buy accuracy"
(_, _, lo2), (_, _, hi2) = risk_coverage_curve(useless, [0.0, 0.5])
assert hi2 == lo2, "and noise must not"
PY

echo
echo "═══ 8. Every number in the README is still the one the code produces ═══"
"$VENV_BIN/python" scripts/check_claims.py

echo
echo "Done. Every number above was computed just now, not quoted."
