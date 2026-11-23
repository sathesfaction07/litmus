#!/usr/bin/env python3
"""Re-derive every number the README quotes, and verify every identifier it names.

One script, three callers: `setup.sh`, `.pre-commit-config.yaml`, and CI. Written as a file
rather than inlined into YAML and shell because the first attempt inlined it and produced a
workflow whose YAML did not parse -- a gate that cannot run is worse than no gate, since it
reports nothing and looks green.

Exit 0 = every claim holds. Exit 1 = a claim in the README is no longer produced by the code.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sop_screen import (  # noqa: E402
    OBSERVABLE_FIELDS,
    build_prompt,
    clauses,
    crosscheck_with_sklearn,
    evaluate,
    implied_accuracy,
    lint_text,
    risk_coverage_curve,
)
from sop_screen.adjudicate import Verdict  # noqa: E402

IN, OUT = Verdict.IN_SCOPE, Verdict.OUT_OF_SCOPE
failures: list[str] = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    print(f"  {'ok ' if ok else 'FAIL'}  {label:<52} {actual!r}")
    if not ok:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def _collected_test_count() -> int:
    """Ask pytest how many tests there are, rather than trusting a number in prose.

    This exists because a sibling project shipped a README claiming 25 tests for a suite of
    106. A count in prose drifts silently; a collected count cannot.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(ROOT / "tests")],
        capture_output=True, text=True, cwd=ROOT,
    )
    match = re.search(r"^(\d+) tests? collected", proc.stdout, re.M)
    if not match:
        failures.append("could not collect the test count from pytest")
        return -1
    return int(match.group(1))


def main() -> int:
    sop = (ROOT / "sop" / "toys-image-classification.md").read_text(encoding="utf-8")
    findings = list(lint_text(sop))
    kinds = Counter(f.kind.name for f in findings)

    print("the policy still lints to what the README quotes")
    check("SOP lines", len(sop.splitlines()), 134)
    check("clauses parsed", len(clauses(sop)), 77)
    check("findings", len(findings), 15)
    check("distinct rules", len({f.rule for f in findings}), 7)
    check("BLOCK severity", sum(1 for f in findings if f.severity.name == "BLOCK"), 14)
    check("finding kinds", dict(sorted(kinds.items())),
          {"UNDECIDABLE": 10, "UNEVALUABLE": 4, "UNLEARNABLE": 1})

    print("\nthe prompt has not re-acquired the fabrication invitation")
    prompt = build_prompt(sop, tuple(findings))
    check("product_attributes in OBSERVABLE_FIELDS", "product_attributes" in OBSERVABLE_FIELDS,
          False)
    check("product_attributes in prompt", "product_attributes" in prompt, False)
    check("withholding is declared", "were REMOVED from the text above" in prompt, True)

    print("\nan accuracy figure still arrives with its baseline")
    preds = [IN] * 60 + [OUT] * 140 + [OUT] * 9751 + [IN] * 49
    truths = [IN.value] * 200 + [OUT.value] * 9800
    report = evaluate(preds, truths)
    check("selective accuracy", f"{report.selective_accuracy:.2%}", "98.11%")
    check("majority-class baseline", f"{report.majority_baseline_accuracy:.2%}", "98.00%")
    check("lift over baseline", f"{report.lift_over_baseline:+.2%}", "+0.11%")
    check("balanced accuracy", f"{report.balanced_accuracy:.2%}", "64.75%")
    check("macro F1", f"{report.macro_f1:.2%}", "68.94%")
    check("in-scope recall", report.per_class[IN.value].recall, 0.30)
    check("closed form agrees", f"{implied_accuracy(0.02, 0.30, 0.995):.2%}", "98.11%")

    try:
        pairs = crosscheck_with_sklearn(preds, truths)
        check("scikit-learn metrics compared", len(pairs), 5)
    except AssertionError as exc:
        failures.append(f"sklearn crosscheck: {exc}")
        print(f"  FAIL  sklearn crosscheck {exc}")

    print("\nabstention still has to earn its place")
    noise = ([(IN, 0.9, IN.value)] * 20 + [(IN, 0.1, IN.value)] * 20
             + [(IN, 0.9, OUT.value)] * 20 + [(IN, 0.1, OUT.value)] * 20)
    (_, _, lo), (_, _, hi) = risk_coverage_curve(noise, [0.0, 0.5])
    check("noise buys no accuracy", hi == lo, True)
    signal = ([(IN, 0.9, IN.value)] * 40 + [(OUT, 0.9, OUT.value)] * 40
              + [(IN, 0.1, OUT.value)] * 20)
    (_, _, slo), (_, _, shi) = risk_coverage_curve(signal, [0.0, 0.5])
    check("a real signal does", shi > slo, True)

    print("\nthe README TEXT still carries the numbers the code produces")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    quoted = {
        "SOP line count": f"{len(sop.splitlines())} lines",
        "findings headline": (f"{len(findings)} findings across "
                             f"{len({f.rule for f in findings})} rules"),
        "finding kinds": (f"`UNDECIDABLE` ({kinds['UNDECIDABLE']}), "
                          f"`UNEVALUABLE` ({kinds['UNEVALUABLE']}), "
                          f"`UNLEARNABLE` ({kinds['UNLEARNABLE']})"),
        "selective accuracy row": ("selective accuracy (on decided)      "
                                   f"{report.selective_accuracy:.2%}"),
        "baseline row": ("always-commonest-class baseline      "
                         f"{report.majority_baseline_accuracy:.2%}"),
        "lift row": f"lift over that baseline              {report.lift_over_baseline:+.2%}",
        "balanced accuracy row": ("balanced accuracy (mean recall)      "
                                  f"{report.balanced_accuracy:.2%}"),
        "macro F1 row": f"macro F1                             {report.macro_f1:.2%}",
    }
    for label, text in quoted.items():
        check(f"README states {label}", text in readme_text, True)

    collected = _collected_test_count()
    check("README states the real test total", f"{collected} passed" in readme_text, True)

    print("\nthe README names only identifiers that exist")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
    )
    cited = sorted(set(re.findall(
        r"`(test_[a-z_]+|Test[A-Za-z]+|[a-z_]+\(\)|[A-Z_]{4,})`", readme
    )))
    missing = [name for name in cited if name.rstrip("()") not in source]
    check(f"identifiers cited ({len(cited)}) all present", missing, [])

    if failures:
        print(f"\n{len(failures)} claim(s) no longer hold:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"\nall claims hold ({len(cited)} identifiers, {len(findings)} findings, "
          "98.11% against a 98.00% constant function)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
