"""M4: versioned Ki endpoint tables built from the curated activity layer."""

from seq2lead.endpoint.build import (
    BUILDER_VERSION,
    DEFAULT_DISCORDANCE_PKI,
    DEFAULT_THRESHOLD_PKI,
    SENSITIVITY_THRESHOLDS,
    BuildCounters,
    build_endpoint,
)
from seq2lead.endpoint.interval import (
    ACTIVE,
    AMBIGUOUS,
    CENSORED_RELATIONS,
    EXACT_RELATIONS,
    INACTIVE,
    Interval,
    classify,
    constraint,
    intersect_all,
    is_usable_magnitude,
    pki_from_nm,
)

__all__ = [
    "ACTIVE",
    "AMBIGUOUS",
    "BUILDER_VERSION",
    "BuildCounters",
    "CENSORED_RELATIONS",
    "DEFAULT_DISCORDANCE_PKI",
    "DEFAULT_THRESHOLD_PKI",
    "EXACT_RELATIONS",
    "INACTIVE",
    "Interval",
    "SENSITIVITY_THRESHOLDS",
    "build_endpoint",
    "classify",
    "constraint",
    "intersect_all",
    "is_usable_magnitude",
    "pki_from_nm",
]
