"""Shared fixtures. The example policy is read and linted once per session.

These lived in four test modules under two different names (`findings` and `sop_findings`),
which is the repetition the style rule forbids -- and two names for one fixture is the smell
that makes it worth fixing rather than a cosmetic tidy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sop_screen.lint import Finding, lint_text

SOP = Path(__file__).resolve().parent.parent / "sop" / "toys-image-classification.md"


@pytest.fixture(scope="session")
def sop_text() -> str:
    return SOP.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def sop_findings(sop_text: str) -> tuple[Finding, ...]:
    return tuple(lint_text(sop_text))
