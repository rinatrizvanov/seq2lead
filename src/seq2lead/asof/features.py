"""Content-keyed feature binding across an accepted cache and its extension.

The accepted caches are keyed by the 202609 schema's surrogate ids. Those ids
mean nothing in the 202601 schema, so the as-of path cannot index them directly:
it has to go through what the vector is actually a function of -- the InChIKey for
a fingerprint, the sequence hash for an embedding.

Two rules this module exists to enforce:

* **Reuse only through verified content identity.** A surrogate id is never
  equated across schemas. The caller supplies a reuse map built by comparing the
  thing the vector depends on, and a key absent from that map is not reused even
  if a row with the same InChIKey exists somewhere.
* **An extension is a different artifact.** A cache is its spec *and* its
  population, so a larger population cannot inherit the accepted cache's
  identity. Extensions arrive as their own files with their own digests, and a
  key present in both is a provenance conflict rather than a convenience.

Missing and unusable are reported apart and neither is imputed: nothing here
writes a zero vector or a mean substitute for an entity it could not resolve.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


class BindingError(RuntimeError):
    """The feature binding is not what the contract declares."""


@dataclass
class Coverage:
    """Per-role coverage. `missing` needs an extension; `unusable` needs a fix."""

    role: str
    kind: str
    requested: int = 0
    usable: int = 0
    missing: set[str] = field(default_factory=set)
    unusable: dict[str, str] = field(default_factory=dict)
    from_accepted: int = 0
    from_extension: int = 0

    @property
    def complete(self) -> bool:
        return not self.missing and not self.unusable

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "kind": self.kind,
            "requested": self.requested,
            "usable": self.usable,
            "missing": len(self.missing),
            "unusable": len(self.unusable),
            "unusable_reasons": dict(sorted(Counter(self.unusable.values()).items())),
            "from_accepted_cache": self.from_accepted,
            "from_extension": self.from_extension,
            "complete": self.complete,
            "nothing_imputed": True,
        }


@dataclass
class FeatureBinding:
    """One feature kind, resolved by content key across accepted and extension."""

    kind: str
    stored_dim: int
    accepted_path: Path
    accepted_sha256: str
    extension_path: Path | None = None
    extension_sha256: str | None = None
    _accepted: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    _extension: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def keys(self) -> set[str]:
        return set(self._accepted) | set(self._extension)

    def vector(self, key: str) -> np.ndarray | None:
        if key in self._extension:
            return self._extension[key]
        return self._accepted.get(key)

    def source(self, key: str) -> str | None:
        if key in self._extension:
            return "extension"
        return "accepted" if key in self._accepted else None

    def _validate(self, key: str, vec: np.ndarray) -> str | None:
        """Return a reason the vector is unusable, or None."""
        if vec.shape[-1] != self.stored_dim:
            return "wrong_stored_dimension"
        if np.issubdtype(vec.dtype, np.floating):
            if not np.all(np.isfinite(vec)):
                return "non_finite_values"
        elif not np.issubdtype(vec.dtype, np.integer):
            return "unexpected_dtype"
        return None

    def coverage(self, role: str, wanted: set[str]) -> Coverage:
        out = Coverage(role=role, kind=self.kind, requested=len(wanted))
        for key in wanted:
            vec = self.vector(key)
            if vec is None:
                out.missing.add(key)
                continue
            reason = self._validate(key, vec)
            if reason is not None:
                out.unusable[key] = reason
                continue
            out.usable += 1
            if self.source(key) == "extension":
                out.from_extension += 1
            else:
                out.from_accepted += 1
        return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def load_binding(
    kind: str,
    *,
    accepted_path: str | Path,
    accepted_sha256: str,
    reuse_map: dict[str, int],
    stored_dim: int,
    extension_path: str | Path | None = None,
    extension_sha256: str | None = None,
) -> FeatureBinding:
    """Load an accepted cache through a verified reuse map, plus any extension.

    `reuse_map` is content key -> surrogate id in the accepted cache's own schema,
    built by the caller from the thing the vector depends on. Nothing here infers
    it, because inferring it is the mistake this module exists to prevent.
    """
    accepted_path = Path(accepted_path)
    actual = _sha256(accepted_path)
    if actual != accepted_sha256:
        msg = (
            f"{kind}: accepted cache digest {actual[:16]}… does not match the bound "
            f"{accepted_sha256[:16]}…"
        )
        raise BindingError(msg)
    data = np.load(accepted_path, allow_pickle=False)
    row_of = {int(v): n for n, v in enumerate(np.asarray(data["ids"]))}
    vectors = np.asarray(data["vectors"])
    accepted: dict[str, np.ndarray] = {}
    for key, surrogate in reuse_map.items():
        row = row_of.get(int(surrogate))
        if row is not None:
            accepted[key] = vectors[row]

    extension: dict[str, np.ndarray] = {}
    if extension_path is not None:
        extension_path = Path(extension_path)
        if extension_sha256 is None:
            msg = f"{kind}: an extension was supplied without a digest to bind it"
            raise BindingError(msg)
        actual_ext = _sha256(extension_path)
        if actual_ext != extension_sha256:
            msg = (
                f"{kind}: extension digest {actual_ext[:16]}… does not match the bound "
                f"{extension_sha256[:16]}…"
            )
            raise BindingError(msg)
        ext = np.load(extension_path, allow_pickle=True)
        ext_keys = [str(k) for k in np.asarray(ext["keys"])]
        ext_vectors = np.asarray(ext["vectors"])
        overlap = set(ext_keys) & set(accepted)
        if overlap:
            msg = (
                f"{kind}: {len(overlap)} keys are in both the accepted cache and the "
                f"extension, so their provenance is ambiguous: {sorted(overlap)[:3]}"
            )
            raise BindingError(msg)
        extension = {k: ext_vectors[n] for n, k in enumerate(ext_keys)}

    return FeatureBinding(
        kind=kind,
        stored_dim=stored_dim,
        accepted_path=accepted_path,
        accepted_sha256=accepted_sha256,
        extension_path=extension_path,
        extension_sha256=extension_sha256,
        _accepted=accepted,
        _extension=extension,
    )
