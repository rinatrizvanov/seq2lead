"""What makes one feature cache a different object from another.

A cache keyed only by "ECFP4" or "ESM-2" is a trap: change the radius, the model
revision or the pooling rule and the old vectors are silently reused under the
same name, so a benchmark number can move for a reason nobody recorded. The spec
below is hashed into the cache's identity, so any change that could alter a
vector produces a different cache and a visibly different name.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seq2lead.features.manifest import InputManifest


@dataclass(frozen=True)
class FeatureSpec:
    """Everything that determines the value of a feature vector.

    Anything that can change a number belongs here. Anything that cannot --
    batch size, device, worker count, progress reporting -- deliberately does
    not, so re-running on different hardware resolves to the same identity.
    """

    kind: str  # ecfp4 | esm2 | activity
    entity: str  # compound | target | pair
    #: Pinned upstream artifact. For ESM-2 this is the Hugging Face commit sha,
    #: not the tag: tags move, commits do not.
    model: str | None = None
    model_revision: str | None = None
    #: How a per-residue or per-atom representation collapses to one vector.
    pooling: str | None = None
    #: The sequence-length policy, spelled out. `full` embeds every residue;
    #: `truncate:N` keeps the first N; `chunk:N` embeds N-residue windows and
    #: pools across them. Recorded because two policies give different vectors
    #: for exactly the sequences where it matters most.
    length_policy: str | None = None
    #: The longest input the policy will admit before it refuses, or None.
    max_length: int | None = None
    #: Fingerprint geometry. `chirality` is in the identity because an achiral
    #: fingerprint gives two enantiomers the same vector, which is a different
    #: representation of the same molecule, not a variant spelling of one.
    radius: int | None = None
    n_bits: int | None = None
    chirality: bool | None = None
    #: The curation generation the inputs came from.
    standardizer_version: str | None = None
    dtype: str = "float32"
    #: Set only for features derived from measured activities. A split makes a
    #: feature a different object, because the training set it may read differs.
    split: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def canonical(self) -> str:
        """Stable JSON. Sorted keys, no whitespace drift, nulls preserved."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def short(self) -> str:
        return self.sha256()[:12]

    def cache_name(self) -> str:
        """Representation-only name. Prefer `cache_name(spec, manifest)`.

        Kept because a spec alone is still a meaningful object -- "the ECFP4
        recipe" -- but it is **not** a cache identity: it says nothing about
        which entities went in.
        """
        parts = [self.kind, self.entity]
        if self.split:
            parts.append(self.split)
        return "-".join(parts) + "-" + self.short()


def spec_sha256(spec: FeatureSpec) -> str:
    return spec.sha256()


def cache_key(spec: FeatureSpec, manifest: InputManifest) -> str:
    """The identity of a built cache: how it was computed **and** over what.

    Without the manifest half, a `--limit 1000` build and a full build hash
    identically, so the partial one can be registered as the complete result and
    handed to a caller that asked for everything.
    """
    payload = spec.canonical() + "|" + manifest.canonical()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_name(spec: FeatureSpec, manifest: InputManifest) -> str:
    parts = [spec.kind, spec.entity]
    if spec.split:
        parts.append(spec.split)
    if not manifest.is_full:
        parts.append("partial")
    return "-".join(parts) + "-" + cache_key(spec, manifest)[:12]
