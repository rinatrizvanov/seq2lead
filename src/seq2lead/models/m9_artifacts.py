"""Preflight ownership checks and a frozen manifest for the M9 runs.

The first M9 pass wrote checkpoints, training records and predictions to fixed
paths with unconditional writes, and only asked the result registry for
permission at the very end. A rerun therefore overwrote every artifact before
anything refused it -- the refusal arrived after the damage.

So: paths are run-scoped, every destination is checked **before** a single fit
starts, and a collision refuses with nothing written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seq2lead.eval.config import ExperimentConfig

MANIFEST_PATH = Path("configs/manifests/m9_runs.json")
MANIFEST_VERSION = "m9-runs-v1"


class ArtifactCollision(RuntimeError):
    """A destination path is already occupied. Nothing has been written."""


@dataclass(frozen=True)
class RunPaths:
    """Where one (split, seed) run's artifacts live, scoped by run label."""

    run: str
    checkpoint: Path
    record: Path
    prediction: Path

    def all(self) -> tuple[Path, ...]:
        return (self.checkpoint, self.record, self.prediction)


def run_paths(
    run_label: str,
    model: str,
    split: str,
    seed: int,
    run_root: Path = Path("data/m9"),
    prediction_root: Path = Path("data/predictions_m9"),
) -> RunPaths:
    """Run-scoped destinations. A new label never collides with an old run."""
    tag = f"{model}__{split}__seed{seed}"
    return RunPaths(
        run=run_label,
        checkpoint=run_root / run_label / f"{tag}.pt",
        record=run_root / run_label / f"{tag}.json",
        prediction=prediction_root / run_label / f"{tag}.npz",
    )


def preflight(paths: list[RunPaths], *, overwrite: bool = False) -> None:
    """Refuse before fitting if any destination is occupied.

    Checked across the whole planned set, not per run: discovering the collision
    on run 17 of 20 would already have overwritten sixteen.
    """
    if overwrite:
        return
    occupied = [p for run in paths for p in run.all() if p.exists()]
    if occupied:
        raise ArtifactCollision(
            f"{len(occupied)} artifact path(s) already exist, e.g. "
            f"{[str(p) for p in occupied[:3]]}. Refusing before any fitting starts. "
            "Use a new run label, or pass overwrite explicitly if you mean to "
            "discard the existing run."
        )


#: Legacy selections predate the binding fields. Recovery must be asked for
#: explicitly and is recorded, never applied silently.
LEGACY_SELECTION_NOTE = (
    "selection predates the binding fields and was accepted under explicit legacy recovery"
)


def check_selection_binding(
    selection: dict[str, Any],
    config: ExperimentConfig,
    expected_dims: tuple[int, ...],
    *,
    allow_legacy: bool = False,
) -> list[str]:
    """A frozen selection is only valid for the exact config that produced it.

    Matching on the experiment *name* alone is not enough: the same name can point
    at a different pinned cache, a different split set or a different search space,
    and the selection would silently be carried across.

    Missing or empty binding fields are **rejected**, not skipped. Treating an
    absent field as "no constraint" turns every one of these checks off for
    exactly the records that cannot prove where they came from.
    """
    notes: list[str] = []
    if selection.get("experiment") != config.version:
        raise ValueError(
            f"selection was frozen for experiment {selection.get('experiment')!r}, "
            f"config is {config.version!r}"
        )

    required = ("config_sha256", "declared_splits", "declared_projection_dims")
    missing = [field for field in required if not selection.get(field)]
    if missing:
        if not allow_legacy:
            raise ValueError(
                f"selection is missing {missing}. Refusing: without those fields none "
                "of the binding checks can run, and a selection frozen under a "
                "different config would be accepted silently. Re-freeze it, or pass "
                "allow_legacy explicitly to accept a pre-binding record."
            )
        notes.append(f"{LEGACY_SELECTION_NOTE}: missing {missing}")

    recorded = selection.get("config_sha256")
    if recorded and recorded != config.config_sha256:
        raise ValueError(
            "selection was frozen under a different config file:\n"
            f"  selection ran under {recorded[:16]}…\n"
            f"  loaded config is    {config.config_sha256[:16]}…\n"
            "The pinned inputs or the search space may differ, so the choice does "
            "not transfer."
        )

    current = {s.name for s in config.splits}
    declared = set(selection.get("declared_splits") or [])
    if declared and declared != current:
        raise ValueError(
            f"selection covered splits {sorted(declared)}, config declares {sorted(current)}"
        )

    dims = tuple(selection.get("declared_projection_dims") or ())
    if dims and tuple(dims) != tuple(expected_dims):
        raise ValueError(
            f"selection searched projection dims {list(dims)}, config declares "
            f"{list(expected_dims)}"
        )

    # The declared fields describe what was *searched*. `chosen` is what will
    # actually be fitted, and it is what the test pass reads -- so it is checked
    # against both.
    chosen = selection.get("chosen") or {}
    if not chosen:
        raise ValueError("selection records no chosen configuration")
    reference = declared or current
    missing_splits = sorted(reference - set(chosen))
    extra_splits = sorted(set(chosen) - reference)
    if missing_splits or extra_splits:
        raise ValueError(
            f"selection chose configurations for {sorted(chosen)}, which does not "
            f"cover the declared splits {sorted(reference)}: "
            f"{len(missing_splits)} missing {missing_splits}, "
            f"{len(extra_splits)} unexpected {extra_splits}"
        )
    searched = set(dims or expected_dims)
    unsearched = sorted({d for d in chosen.values() if d not in searched})
    if unsearched:
        raise ValueError(
            f"selection chose projection dims {unsearched} that were never searched "
            f"(searched: {sorted(searched)})"
        )
    return notes


def effective_settings(config: ExperimentConfig, projection_dim: int, seed: int):
    """Build training settings from the declared config, refusing unknown keys.

    Relying on the dataclass defaults happening to match the YAML is how a config
    change silently fails to take effect.
    """
    from seq2lead.models.dual_encoder import DualEncoderConfig

    search = config.search or {}
    mapping = {
        "learning_rate": "learning_rate",
        "batch_size": "batch_size",
        "max_epochs": "max_epochs",
        "early_stopping_patience": "patience",
    }
    known = set(mapping) | {"projection_dim", "optimizer", "note"}
    unknown = sorted(set(search) - known)
    if unknown:
        raise ValueError(
            f"experiment {config.version!r} declares search settings this builder does "
            f"not implement: {unknown}. Refusing rather than ignoring them."
        )
    optimizer = str(search.get("optimizer", "adam")).lower()
    if optimizer != "adam":
        raise ValueError(f"optimizer {optimizer!r} is not implemented; only 'adam' is")
    kwargs = {target: search[source] for source, target in mapping.items() if source in search}
    return DualEncoderConfig(projection_dim=projection_dim, seed=seed, **kwargs)


# ------------------------------------------------------------------- manifest


def freeze_manifest(
    run_root: Path = Path("data/m9/final"),
    prediction_root: Path = Path("data/predictions_m9"),
    path: Path = MANIFEST_PATH,
    provenance: str = "",
) -> dict[str, Any]:
    """Digest the existing run artifacts so they can be verified without refitting."""
    from seq2lead.features.store import sha256_file

    runs = []
    #  sidecars also match a bare *.json glob; they are metadata
    # about a run, not a run record.
    for record_path in sorted(run_root.glob("*.json")):
        if record_path.name.endswith(".binding.json"):
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        stem = record_path.stem
        checkpoint = run_root / f"{stem}.pt"
        prediction = prediction_root / f"{stem}.npz"
        sidecar = checkpoint.with_suffix(checkpoint.suffix + ".binding.json")
        runs.append(
            {
                "run": stem,
                "split": record["split"],
                "seed": record["seed"],
                "projection_dim": record["projection_dim"],
                "n_parameters": record["n_parameters"],
                "best_epoch": record["best_epoch"],
                "best_validation_rmse": record["best_validation_rmse"],
                "config_digest": record["config_digest"],
                "checkpoint": str(checkpoint),
                "checkpoint_sha256": sha256_file(checkpoint) if checkpoint.exists() else None,
                "record": str(record_path),
                "record_sha256": sha256_file(record_path),
                "prediction": str(prediction),
                "prediction_sha256": sha256_file(prediction) if prediction.exists() else None,
                "binding_sidecar": str(sidecar) if sidecar.exists() else None,
                "binding_sha256": sha256_file(sidecar) if sidecar.exists() else None,
            }
        )
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "frozen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_runs": len(runs),
        "provenance": provenance,
        "runs": runs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


#: Artifacts every run must carry. A missing path or digest for one of these is
#: a problem, not something to skip: silently passing a run whose record cannot
#: be located is how an unverifiable artifact looks identical to a verified one.
REQUIRED_ARTIFACTS = (
    ("checkpoint", "checkpoint", "checkpoint_sha256"),
    ("prediction", "prediction", "prediction_sha256"),
    ("training record", "record", "record_sha256"),
)
#: Carried when present, but a run predating it is not a failure.
OPTIONAL_ARTIFACTS = (("binding", "binding_sidecar", "binding_sha256"),)


def verify_manifest(path: Path = MANIFEST_PATH) -> list[str]:
    """Re-hash every recorded artifact. Returns the problems; empty means clean."""
    from seq2lead.features.store import sha256_file

    manifest = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for run in manifest["runs"]:
        for label, file_key, digest_key, required in (
            *((*a, True) for a in REQUIRED_ARTIFACTS),
            *((*a, False) for a in OPTIONAL_ARTIFACTS),
        ):
            expected = run.get(digest_key)
            target = run.get(file_key)
            if expected is None or target is None:
                if required:
                    missing = [
                        name
                        for name, value in ((file_key, target), (digest_key, expected))
                        if value is None
                    ]
                    problems.append(
                        f"{run['run']}: {label} cannot be verified -- the manifest "
                        f"records no {missing}"
                    )
                continue
            candidate = Path(target)
            if not candidate.exists():
                problems.append(f"{run['run']}: {label} missing at {candidate}")
            elif sha256_file(candidate) != expected:
                problems.append(f"{run['run']}: {label} bytes changed")
    return problems


def manifest_digest(path: Path = MANIFEST_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
