"""Standalone inference: rank a bundled library against one sequence.

Nothing in this package touches PostgreSQL or the historical training caches. It
consumes an exported bundle, which carries the model pieces, the precomputed
compound projections and the metadata needed to score a query and explain what
the scores are.

The scoring rule is unchanged from the database-backed path: an affine map on the
cosine between the projected query and a precomputed compound projection, in pKi
units. It is not a probability, not a binding likelihood and not a calibrated
confidence.
"""

from seq2lead.inference.bundle import (
    BUNDLE_VERSION,
    BundleError,
    InferenceBundle,
    export_bundle,
    load_bundle,
)

__all__ = [
    "BUNDLE_VERSION",
    "BundleError",
    "InferenceBundle",
    "export_bundle",
    "load_bundle",
]
