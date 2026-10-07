"""Build an inference bundle from the database-backed artifacts.

This is the only part of the standalone feature that needs PostgreSQL, and it is
a maintainer step run once per release, not something a user does. It reuses the
database path's own binding checks, so a bundle cannot be exported from a
checkpoint whose feature caches have drifted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import psycopg

#: What the bundled library is, in the terms a reader needs. Deliberately NOT
#: "approved drugs": membership means a measured exact Ki value existed in the
#: pinned release, nothing more.
LIBRARY_DESCRIPTION = (
    "Compounds carrying at least one exact-relation (=) Ki measurement in the "
    "pinned BindingDB source release, ordered by internal surrogate key and "
    "capped. Membership says a measured Ki value existed for the compound "
    "against SOME protein -- not that it is a drug, not that it is approved, "
    "not that it is safe, and not that it has any measured relationship to your "
    "query. The ordering is arbitrary with respect to anything a model predicts."
)


def export(
    conn: psycopg.Connection,
    checkpoint: Path,
    out: Path,
    *,
    library_name: str = "curated-ki-25k-v1",
    provenance: str = "",
) -> dict[str, Any]:
    from seq2lead.features.store import load_features
    from seq2lead.inference.bundle import export_bundle
    from seq2lead.models.binding import load_binding, verify
    from seq2lead.models.dual_encoder import load_checkpoint
    from seq2lead.models.library import load as load_library
    from seq2lead.models.rank import _smiles_for, _unusable

    model, extra = load_checkpoint(checkpoint, device="cpu")
    binding = load_binding(checkpoint, extra)
    binding_notes = verify(conn, binding)

    library, members = load_library(conn, library_name)
    ids, packed = load_features(conn, binding.compound_cache, allow_superseded=True)
    index = {int(v): i for i, v in enumerate(ids)}
    absent = [c for c in members if c not in index]
    if absent:
        raise LookupError(
            f"{len(absent):,} library compounds are absent from the bound compound "
            f"cache {binding.compound_cache!r}; refusing to export a bundle whose "
            "library and model do not correspond"
        )
    rows = np.fromiter((index[c] for c in members), dtype=np.int64, count=len(members))
    fingerprints = np.unpackbits(packed[rows], axis=1)[:, :2048].astype(np.float32)

    library_block = {
        "name": library.name,
        "members": int(library.n_members),
        "member_sha256": library.member_sha256,
        "cap": library.cap,
        "selection_rule": library.selection_rule,
        "description": LIBRARY_DESCRIPTION,
        "origin": (
            "Derived from BindingDB measurements under this project's curation "
            "pipeline. Compound structures are the RDKit-standardised parents of "
            "BindingDB-supplied SMILES."
        ),
        "scope": (
            "A benchmark pool, not a screening deck. It is biased towards targets "
            "and chemistry that have been measured by Ki radioligand-style assays, "
            "and away from chemistry nobody has published a Ki for."
        ),
        "licence": (
            "Compound identifiers and structures derive from BindingDB, which "
            "publishes staff-curated rows under CC BY 3.0 and rows imported from "
            "ChEMBL under CC BY-SA 3.0 Unported. See DATA_LICENSE."
        ),
    }
    manifest = export_bundle(
        out,
        model=model,
        binding=binding,
        compound_ids=members,
        fingerprints=fingerprints,
        smiles=_smiles_for(conn, members),
        library=library_block,
        checkpoint=checkpoint,
        provenance=provenance or f"exported from {checkpoint.name} against library {library.name}",
        unusable=_unusable(conn, binding.compound_cache),
    )
    manifest.setdefault("export_notes", []).extend(binding_notes)
    return manifest
