"""M5: analysis of the curated endpoint. Reads only; builds no splits, trains nothing."""

from seq2lead.analysis.assay_variance import (
    ANALYSIS_VERSION,
    DECISION_POOL,
    DECISION_RESTRICT,
    DECISION_UNSUITABLE,
    Distribution,
    SpreadComparison,
    SupersededEndpoint,
    VarianceReport,
    decide,
    resolve_endpoint,
    run,
)

__all__ = [
    "ANALYSIS_VERSION",
    "DECISION_POOL",
    "DECISION_RESTRICT",
    "DECISION_UNSUITABLE",
    "Distribution",
    "SpreadComparison",
    "SupersededEndpoint",
    "VarianceReport",
    "decide",
    "resolve_endpoint",
    "run",
]
