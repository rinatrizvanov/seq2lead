"""The exportable inference bundle: what a query needs, and nothing else.

A bundle is a directory, not an archive, so a reader can see what is in it and
hash the parts independently. It holds:

* ``model/protein_tower.npz``    the protein-side projection weights and the
                                 affine head's scale and offset
* ``model/compound_tower.npz``   the compound-side projection weights. Not needed
                                 to rank the bundled library, whose projections
                                 are precomputed; carried so that a future custom
                                 library can be projected with the same weights
                                 rather than an approximation of them
* ``model/protein_transform.npz`` the standardisation fitted on training inputs
                                 and frozen with the checkpoint
* ``library/projections.npy``    the precomputed compound projections
* ``library/compounds.csv``      stable identifier and SMILES per compound, in
                                 projection row order
* ``manifest.json``              versions, digests, provenance and the recorded
                                 representation spec

**Why projections and not fingerprints.** The towers never see each other's
input, so a compound's projection depends only on its fingerprint. Precomputing
it removes both the fingerprint cache and RDKit from the inference path, and is
the property the dual encoder's independent towers exist to provide.

**What the manifest is for.** A bundle whose parts have drifted apart is worse
than a missing one, because it still produces numbers. Every file is digested at
export and re-digested at load, before the language model is downloaded or a
vector is read.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from seq2lead.models.binding import FeatureBinding

BUNDLE_VERSION = "seq2lead-inference-bundle-v1"
MANIFEST_NAME = "manifest.json"

#: Files a bundle must contain, each digested in the manifest.
BUNDLE_FILES = (
    "model/protein_tower.npz",
    "model/compound_tower.npz",
    "model/protein_transform.npz",
    "library/projections.npy",
    "library/compounds.csv",
)


#: Where a bundle is looked for when none is named, in order.
DEFAULT_LOCATIONS = ("SEQ2LEAD_BUNDLE", "./seq2lead-bundle", "~/.seq2lead/bundle")


def resolve_bundle(explicit: str | Path | None = None) -> Path:
    """Find a bundle: the one named, else the env var, else the usual places.

    Raises with the list of places looked at rather than a bare "not found", so
    the fix is obvious from the message.
    """
    import os

    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("SEQ2LEAD_BUNDLE")
    if env:
        return Path(env).expanduser()
    for candidate in ("./seq2lead-bundle", "~/.seq2lead/bundle"):
        path = Path(candidate).expanduser()
        if (path / MANIFEST_NAME).exists():
            return path
    raise BundleError(
        "no inference bundle found. Looked at $SEQ2LEAD_BUNDLE, ./seq2lead-bundle "
        "and ~/.seq2lead/bundle. Pass --bundle, set SEQ2LEAD_BUNDLE, or put the "
        "bundle in one of those places. A bundle is produced by "
        "`seq2lead bundle export` and is distributed separately from the source, "
        "because it is tens of megabytes of precomputed projections."
    )


class BundleError(RuntimeError):
    """The bundle is missing, incomplete, altered or incompatible."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class InferenceBundle:
    """A loaded bundle. Arrays are in memory; the ESM-2 encoder is not loaded yet."""

    root: Path
    manifest: dict[str, Any]
    protein_weights: dict[str, np.ndarray]
    compound_weights: dict[str, np.ndarray]
    transform_mean: np.ndarray
    transform_scale: np.ndarray
    scale: float
    offset: float
    projections: np.ndarray
    compound_ids: list[str]
    smiles: list[str]

    @property
    def projection_dim(self) -> int:
        return int(self.projections.shape[1])

    @property
    def n_compounds(self) -> int:
        return int(self.projections.shape[0])

    @property
    def protein_spec(self) -> dict[str, Any]:
        return self.manifest["representation"]["protein"]

    @property
    def library(self) -> dict[str, Any]:
        return self.manifest["library"]


# --------------------------------------------------------------------- export


def export_bundle(
    out: Path,
    *,
    model,
    binding: FeatureBinding,
    compound_ids: list[int],
    fingerprints: np.ndarray,
    smiles: dict[int, str],
    library: dict[str, Any],
    checkpoint: Path,
    provenance: str,
    unusable: set[int] | None = None,
) -> dict[str, Any]:
    """Write a bundle from a loaded checkpoint and the library it is bound to.

    `fingerprints` is the unpacked float32 ECFP4 matrix in `compound_ids` order,
    exactly as the database-backed path builds it; projecting here rather than
    storing fingerprints is what lets inference skip RDKit and the cache.
    """
    import torch

    if model.transform is None:
        raise BundleError("refusing to export a bundle from a checkpoint with no transform")
    if len(compound_ids) != fingerprints.shape[0]:
        raise BundleError(
            f"{len(compound_ids):,} compound ids against {fingerprints.shape[0]:,} "
            "fingerprint rows; they must correspond row for row"
        )
    missing = [c for c in compound_ids if c not in smiles]
    if missing:
        raise BundleError(
            f"{len(missing):,} library compounds have no SMILES; a bundle without "
            "structures would rank identifiers nobody can act on"
        )

    (out / "model").mkdir(parents=True, exist_ok=True)
    (out / "library").mkdir(parents=True, exist_ok=True)

    with torch.no_grad():
        projections = model.project_compounds(torch.from_numpy(fingerprints)).cpu().numpy()
    projections = np.ascontiguousarray(projections, dtype=np.float32)

    state = {k: v.cpu().numpy() for k, v in model.module.state_dict().items()}
    protein_weights = {k.split("protein.", 1)[1]: v for k, v in state.items() if "protein." in k}
    compound_weights = {k.split("compound.", 1)[1]: v for k, v in state.items() if "compound." in k}
    if not protein_weights or not compound_weights:
        raise BundleError(f"unexpected state-dict layout: {sorted(state)}")

    np.savez(
        out / "model/protein_tower.npz",
        **protein_weights,
        scale=np.asarray(float(state["scale"]), dtype=np.float32),
        offset=np.asarray(float(state["offset"]), dtype=np.float32),
    )
    np.savez(out / "model/compound_tower.npz", **compound_weights)
    np.savez(
        out / "model/protein_transform.npz",
        mean=model.transform.mean.astype(np.float32),
        scale=model.transform.scale.astype(np.float32),
        n_fitted=np.asarray(int(model.transform.n_fitted)),
    )
    np.save(out / "library/projections.npy", projections)

    flagged = unusable or set()
    with (out / "library/compounds.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["row", "compound_id", "smiles", "flags"])
        for row, compound_id in enumerate(compound_ids):
            writer.writerow(
                [
                    row,
                    compound_id,
                    smiles[compound_id],
                    "unusable_fingerprint" if compound_id in flagged else "",
                ]
            )

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "source_checkpoint": {
            "path": str(checkpoint),
            "sha256": sha256_file(checkpoint),
            "experiment": binding.experiment,
            "config_sha256": binding.config_sha256,
        },
        "model": {
            "projection_dim": int(model.config.projection_dim),
            "compound_dim": int(model.config.compound_dim),
            "protein_dim": int(model.config.protein_dim),
            "scoring_rule": "predicted_pKi = scale * cosine(compound_z, protein_z) + offset",
            "scale": float(state["scale"]),
            "offset": float(state["offset"]),
            "transform_fitted_on": model.transform.fitted_on,
            "transform_n_fitted": int(model.transform.n_fitted),
        },
        "representation": {
            "protein": {
                "model": binding.protein.model,
                "model_revision": binding.protein.model_revision,
                "pooling": binding.protein.pooling,
                "dtype": binding.protein.dtype,
                "length_policy": binding.protein.length_policy,
                "max_length": int(binding.protein.max_length),
                "training_window": int(binding.protein.training_window),
            },
            "compound": {
                "cache": binding.compound_cache,
                "manifest_sha256": binding.compound_manifest_sha256,
                "storage_sha256": binding.compound_storage_sha256,
                "fingerprint": "chiral ECFP4, radius 2, 2048 bits",
                "note": (
                    "Fingerprints are NOT shipped. The compound projections in "
                    "library/projections.npy were computed from them with the "
                    "compound tower below, which is the only use inference makes "
                    "of them."
                ),
            },
        },
        "library": library,
        "files": {},
        "counts": {
            "compounds": len(compound_ids),
            "projection_rows": int(projections.shape[0]),
            "projection_dim": int(projections.shape[1]),
            "flagged_unusable_fingerprint": len([c for c in compound_ids if c in flagged]),
        },
        "not_included_and_why": {
            "measured evidence": (
                "Prior measured activity is deliberately absent. It is not validation "
                "of a ranking, and the partition-filtered evidence API that would be "
                "needed to show it safely is unimplemented."
            ),
            "fingerprint cache": "superseded by the precomputed projections",
            "PostgreSQL corpus": "not consulted by the standalone path",
        },
    }
    for name in BUNDLE_FILES:
        path = out / name
        manifest["files"][name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    manifest["total_bytes"] = sum(v["bytes"] for v in manifest["files"].values())

    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


# ----------------------------------------------------------------------- load


def verify_bundle(root: Path) -> dict[str, Any]:
    """Check the manifest and every digest. Runs before anything expensive."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        raise BundleError(
            f"{root} has no {MANIFEST_NAME}. Point --bundle at an exported bundle "
            "directory, not at a checkpoint or a library file."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("bundle_version")
    if version != BUNDLE_VERSION:
        raise BundleError(
            f"bundle reports version {version!r}; this build reads {BUNDLE_VERSION!r}. "
            "Refusing rather than guessing at a layout it may not have."
        )
    recorded = manifest.get("files") or {}
    missing = [n for n in BUNDLE_FILES if not (root / n).exists()]
    if missing:
        raise BundleError(f"bundle is incomplete; missing: {missing}")
    undeclared = [n for n in BUNDLE_FILES if n not in recorded]
    if undeclared:
        raise BundleError(f"manifest declares no digest for: {undeclared}")
    drifted = []
    for name, entry in recorded.items():
        path = root / name
        if not path.exists():
            drifted.append(f"{name}: declared but absent")
            continue
        actual = sha256_file(path)
        if actual != entry.get("sha256"):
            declared_digest = str(entry.get("sha256"))[:16]
            drifted.append(f"{name}: {actual[:16]}… against declared {declared_digest}…")
    if drifted:
        raise BundleError(
            "bundle contents do not match the manifest:\n  - " + "\n  - ".join(drifted)
        )
    return manifest


def load_bundle(root: Path) -> InferenceBundle:
    """Verify, then load the arrays. The ESM-2 encoder is not touched here."""
    root = Path(root)
    manifest = verify_bundle(root)

    protein = np.load(root / "model/protein_tower.npz")
    compound = np.load(root / "model/compound_tower.npz")
    transform = np.load(root / "model/protein_transform.npz")
    projections = np.load(root / "library/projections.npy")

    ids: list[str] = []
    smiles: list[str] = []
    with (root / "library/compounds.csv").open(encoding="utf-8", newline="") as fh:
        for record in csv.DictReader(fh):
            ids.append(record["compound_id"])
            smiles.append(record["smiles"])

    if len(ids) != projections.shape[0]:
        raise BundleError(
            f"{len(ids):,} compound rows against {projections.shape[0]:,} projection "
            "rows. The metadata and the projections describe different libraries."
        )
    declared = manifest.get("model", {}).get("projection_dim")
    if declared is not None and int(declared) != int(projections.shape[1]):
        raise BundleError(
            f"manifest says projection_dim {declared}, projections are "
            f"{projections.shape[1]}-dimensional"
        )

    return InferenceBundle(
        root=root,
        manifest=manifest,
        protein_weights={k: protein[k] for k in protein.files if k not in {"scale", "offset"}},
        compound_weights={k: compound[k] for k in compound.files},
        transform_mean=np.asarray(transform["mean"], dtype=np.float32),
        transform_scale=np.asarray(transform["scale"], dtype=np.float32),
        scale=float(protein["scale"]),
        offset=float(protein["offset"]),
        projections=np.ascontiguousarray(projections, dtype=np.float32),
        compound_ids=ids,
        smiles=smiles,
    )
