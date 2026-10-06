"""M7 feature caches: content-addressed, versioned, and split-aware."""

from seq2lead.features.identity import FeatureSpec, cache_key, cache_name, spec_sha256
from seq2lead.features.manifest import InputManifest, build_manifest, library_versions
from seq2lead.features.store import FeatureStore, find_reusable, load_features, register
from seq2lead.features.validate import CacheInvalid, validate

BUILDER_VERSION = "m7/v2"

__all__ = [
    "BUILDER_VERSION",
    "CacheInvalid",
    "FeatureSpec",
    "FeatureStore",
    "InputManifest",
    "build_manifest",
    "cache_key",
    "cache_name",
    "find_reusable",
    "library_versions",
    "load_features",
    "register",
    "spec_sha256",
    "validate",
]
