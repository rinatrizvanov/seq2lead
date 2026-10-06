"""End-to-end: prepare, dock, record, gate.

The order is deliberate and enforced. The engine is verified and every
destination preflighted before a single ligand is prepared, because the
expensive part is irreversible in practice -- nobody re-runs forty minutes of
docking to recover from a collision that could have been caught in a millisecond.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.dock.artifacts import (
    DockRunPaths,
    RunRecord,
    preflight,
    run_paths,
    sha256_file,
    utc_now,
    write_record,
)
from seq2lead.dock.cohort import Cohort, read_cohort
from seq2lead.dock.engine import VINA_BIN, dock_many, verify_engine
from seq2lead.dock.gate import GateResult, evaluate_gate
from seq2lead.dock.ligands import LigandFailure, prepare_ligand
from seq2lead.dock.receptor import (
    PreparationError,
    clean_structure,
    prepare_receptor,
    receptor_has_atom_type,
)

if TYPE_CHECKING:
    from seq2lead.dock.config import DockingConfig

STRUCTURE_DIR = Path("data/m10/structures")


def verify_cohort(cohort: Cohort, config: DockingConfig) -> None:
    """Everything that must hold before a single atom is prepared.

    Called on the cohort object, so a cohort handed in directly faces the same
    checks as one loaded from the frozen file. Ordered cheapest-first and run
    before receptor or ligand preparation, because the point is to cost nothing
    when it refuses.
    """
    problems = cohort.validate()
    if problems:
        listed = "\n  ".join(problems)
        raise PreparationError(f"the cohort is not internally consistent:\n  {listed}")

    if cohort.selection_sha256 != config.selection_sha256():
        raise PreparationError(
            f"the cohort was selected under {cohort.selection_sha256[:16]}… but this "
            f"contract selects under {config.selection_sha256()[:16]}…. The endpoint, "
            "eligibility rule or sampling has changed, so this cohort is not the one this "
            "contract describes."
        )

    want_active, want_inactive = config.expected_cohort_counts
    if (cohort.n_active, cohort.n_inactive) != (want_active, want_inactive):
        raise PreparationError(
            f"the contract pins {want_active} active / {want_inactive} inactive but the "
            f"cohort declares {cohort.n_active} / {cohort.n_inactive}"
        )

    expected = config.expected_cohort_content_sha256
    actual = cohort.content_sha256()
    if actual != expected:
        raise PreparationError(
            f"the cohort's contents do not match the contract's pin: expected "
            f"{expected[:16]}…, got {actual[:16]}…. Some member's structure, label, "
            "evidence or bound differs from the frozen cohort this contract was pinned "
            "to, so docking it would not be the declared experiment."
        )


def build_receptor(
    config: DockingConfig, paths: DockRunPaths, structure_dir: Path = STRUCTURE_DIR
) -> dict[str, Any]:
    """Clean and prepare the receptor, and prove the declared cofactors survived."""
    pdb_id = str(config.raw["structure"]["pdb_id"]).lower()
    source = structure_dir / f"{pdb_id}.pdb"
    expected = config.raw["structure"]["sha256"][f"{pdb_id}.pdb"]
    actual = sha256_file(source)
    if actual != expected:
        raise PreparationError(
            f"{source} has digest {actual[:16]}… but the contract names {expected[:16]}…"
        )

    keep = frozenset(config.raw["structure"]["cofactors_kept"])
    report = clean_structure(
        source,
        paths.receptor_clean_pdb,
        keep_heteroatoms=keep,
        altloc=str(config.raw["structure"]["altloc_keep"]),
    )
    pdbqt = prepare_receptor(
        paths.receptor_clean_pdb,
        paths.root / "receptor",
        config.box_center,
        config.box_size,
    )
    # A metalloenzyme whose metal was silently dropped is a different experiment.
    for het in keep:
        type_name = het.capitalize() if len(het) == 2 else het
        if not receptor_has_atom_type(pdbqt, type_name):
            raise PreparationError(
                f"the contract keeps cofactor {het!r} but no {type_name!r} atom reached "
                f"{pdbqt}. Docking would use a pocket missing its metal."
            )
    return {"clean_report": asdict(report), "receptor_pdbqt": str(pdbqt)}


def run_cohort(
    config: DockingConfig,
    run_label: str,
    cohort: Cohort | None = None,
    *,
    binary: Path = VINA_BIN,
    workers: int = 8,
    overwrite: bool = False,
    limit: int | None = None,
    box_size: tuple[float, float, float] | None = None,
    progress: bool = False,
    run_root: Path | None = None,
) -> tuple[DockRunPaths, GateResult, dict[str, Any]]:
    """Dock the frozen cohort and apply the frozen gate rule."""
    paths = run_paths(run_label) if run_root is None else run_paths(run_label, run_root)
    preflight(paths, overwrite=overwrite)
    engine_reported = verify_engine(config, binary)

    cohort = cohort if cohort is not None else read_cohort()
    verify_cohort(cohort, config)
    members = cohort.members[:limit] if limit else cohort.members

    recep = build_receptor(config, paths)
    receptor_pdbqt = Path(recep["receptor_pdbqt"])

    started = time.monotonic()
    prep_failures: list[LigandFailure] = []
    jobs: list[tuple[int, Path, Path]] = []
    seed = int(config.raw["protocol"]["seed"])
    for m in members:
        out = prepare_ligand(
            m.compound_id, m.smiles, paths.ligand_dir / f"{m.compound_id}.pdbqt", seed
        )
        if isinstance(out, LigandFailure):
            prep_failures.append(out)
        else:
            jobs.append((m.compound_id, out.path, paths.pose_dir / f"{m.compound_id}.pdbqt"))
    if progress:
        print(f"  prepared {len(jobs):,} ligands, {len(prep_failures)} failed", flush=True)

    results, dock_failures = dock_many(
        jobs,
        receptor_pdbqt,
        config,
        binary,
        workers=workers,
        box_size=box_size,
        progress=progress,
    )
    wall = time.monotonic() - started

    by_id = {m.compound_id: m for m in members}
    scores = [
        {
            "compound_id": r.compound_id,
            "label": by_id[r.compound_id].label,
            "evidence": by_id[r.compound_id].evidence,
            "affinity_kcal_per_mol": r.affinity,
            "ranking_score": r.ranking_score,
            "seconds": round(r.seconds, 3),
        }
        for r in results
    ]
    failures = [
        {
            "compound_id": f.compound_id,
            "stage": f.stage,
            "reason": f.reason,
            "label": by_id[f.compound_id].label,
        }
        for f in (*prep_failures, *dock_failures)
    ]
    paths.scores.parent.mkdir(parents=True, exist_ok=True)
    paths.scores.write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.failures.write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    gate = evaluate_gate(
        np.array([s["ranking_score"] for s in scores], dtype=np.float64),
        np.array([s["label"] == "active" for s in scores], dtype=bool),
        effect_threshold=config.effect_threshold,
        resamples=config.bootstrap_resamples,
        level=config.bootstrap_level,
        seed=config.bootstrap_seed,
        minimum_per_class=config.minimum_usable_per_class,
    )

    record = RunRecord(
        run=run_label,
        experiment=config.version,
        config_sha256=config.config_sha256,
        engine_version=engine_reported,
        engine_sha256=sha256_file(binary),
        receptor_sha256=sha256_file(receptor_pdbqt),
        cohort_sha256=cohort.membership_sha256(),
        cohort_path=str(Path("reports/results/m10_cohort.json")),
        scores_sha256=sha256_file(paths.scores),
        failures_sha256=sha256_file(paths.failures),
        box_center=list(config.box_center),
        box_size=list(box_size or config.box_size),
        exhaustiveness=config.exhaustiveness,
        num_modes=config.num_modes,
        seed=config.seed,
        n_requested=len(members),
        n_scored=len(scores),
        n_failed=len(failures),
        wall_seconds=round(wall, 1),
        created_at=utc_now(),
        provenance=(
            f"Docked with the engine and box named in {config.path} "
            f"({config.config_sha256[:16]}…). The cohort was frozen before any docking "
            "score existed; this run read it and did not modify it."
        ),
    )
    write_record(record, paths.record, overwrite=overwrite)
    extra = {"receptor": recep, "record": asdict(record)}
    return paths, gate, extra


def attrition_by_class(scores_path: Path, failures_path: Path) -> dict[str, dict[str, int]]:
    """Who made it through, per class -- the table that shows a biased dropout."""
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, int]] = {}
    for label in ("active", "inactive"):
        got = sum(1 for s in scores if s["label"] == label)
        lost = [f for f in failures if f["label"] == label]
        stages: dict[str, int] = {}
        for f in lost:
            stages[f["stage"]] = stages.get(f["stage"], 0) + 1
        out[label] = {
            "scored": got,
            "failed": len(lost),
            **{f"failed_{k}": v for k, v in stages.items()},
        }
    return out
