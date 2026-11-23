"""Lint a classification SOP, then adjudicate evidence against it.

The linter is the point. A classification SOP is a specification, and a specification can
be undecidable, unevaluable or unlearnable in ways no amount of model quality repairs --
so measuring the spec comes before measuring the model.
"""

from .adjudicate import (
    Decision,
    EvidenceTier,
    Verdict,
    adjudicate,
    naive_union_verdict,
)
from .extract import (
    Consolidated,
    ImagePreparationError,
    ParsedPayload,
    Signal,
    UnparseableResponse,
    consolidate,
    parse_model_json,
    prepare_image,
)
from .lint import (
    AgeBand,
    Clause,
    Finding,
    Kind,
    Severity,
    clauses,
    lint_text,
    store_caps,
)
from .metrics import (
    ClassMetrics,
    SelectiveReport,
    accuracy_illusion,
    crosscheck_with_sklearn,
    evaluate,
    implied_accuracy,
    risk_coverage_curve,
)
from .model import (
    MAX_IMAGE_BYTES,
    OBSERVABLE_FIELDS,
    BedrockClient,
    MissingStubResponse,
    StubClient,
    VisionClient,
    build_prompt,
    collect,
)

__version__ = "0.1.0"

__all__ = [
    "MAX_IMAGE_BYTES",
    "OBSERVABLE_FIELDS",
    "AgeBand",
    "BedrockClient",
    "ClassMetrics",
    "SelectiveReport",
    "accuracy_illusion",
    "crosscheck_with_sklearn",
    "evaluate",
    "implied_accuracy",
    "risk_coverage_curve",
    "Decision",
    "EvidenceTier",
    "Verdict",
    "adjudicate",
    "Clause",
    "Consolidated",
    "Finding",
    "MissingStubResponse",
    "StubClient",
    "VisionClient",
    "ImagePreparationError",
    "Kind",
    "ParsedPayload",
    "Severity",
    "Signal",
    "UnparseableResponse",
    "build_prompt",
    "clauses",
    "collect",
    "consolidate",
    "naive_union_verdict",
    "lint_text",
    "parse_model_json",
    "prepare_image",
    "store_caps",
]
