"""Recompute a published gate from the saved per-ligand files, and refuse to
publish a report that disagrees with them.

The defect this exists for: `render()` read `m10_gate.json` and printed whatever
it said. Editing that file to `decision: pass, auroc: 0.99` produced a report
announcing a pass, with the 578 saved scores sitting untouched on disk beside it.
A published number has to be derivable from the evidence, not merely adjacent to
it.

Nothing here can change a decision. It recomputes under the recorded contract and
compares; a mismatch raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from seq2lead.dock.gate import evaluate_gate

if TYPE_CHECKING:
    from seq2lead.dock.cohort import Cohort
    from seq2lead.dock.config import DockingConfig

#: Metrics are floats recomputed through the same code path, so they should agree
#: to the bit. A tolerance this tight still catches a hand-edited file.
TOLERANCE = 1e-9


class EvidenceMismatch(RuntimeError):
    """The published result does not follow from the saved evidence."""


@dataclass
class Recomputation:
    decision: str
    auroc: float | None
    ci_low: float | None
    ci_high: float | None
    n_active: int
    n_inactive: int
    secondary: dict[str, Any] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def recompute_from_saved(
    scores_path: Path,
    failures_path: Path,
    cohort: Cohort,
    config: DockingConfig,
) -> Recomputation:
    """Rebuild the gate from the per-ligand files, checking the chain as it goes."""
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    checks: list[str] = []

    by_id = {m.compound_id: m for m in cohort.members}

    # ---- the cohort is the one the contract pins
    actual = cohort.content_sha256()
    if actual != config.expected_cohort_content_sha256:
        problems.append(
            f"cohort contents {actual[:16]}… do not match the contract pin "
            f"{config.expected_cohort_content_sha256[:16]}…"
        )
    else:
        checks.append("cohort contents match the contract pin")
    problems.extend(cohort.validate())

    # ---- every scored row belongs to the cohort, with the cohort's own label
    unknown = [r["compound_id"] for r in scores if r["compound_id"] not in by_id]
    if unknown:
        problems.append(f"scored compounds absent from the cohort: {unknown[:10]}")
    mislabelled = [
        r["compound_id"]
        for r in scores
        if r["compound_id"] in by_id and r["label"] != by_id[r["compound_id"]].label
    ]
    if mislabelled:
        problems.append(
            f"scored labels disagree with the cohort for: {mislabelled[:10]}. The gate would "
            "be measuring a relabelled cohort."
        )
    if not unknown and not mislabelled:
        checks.append(f"all {len(scores)} scored rows match the cohort's ids and labels")

    # ---- score direction: the ranking score is the negated Vina energy
    flipped = [
        r["compound_id"]
        for r in scores
        if abs(r["ranking_score"] + r["affinity_kcal_per_mol"]) > TOLERANCE
    ]
    if flipped:
        problems.append(
            f"ranking_score is not the negated Vina energy for: {flipped[:10]}. A flipped "
            "sign inverts the gate."
        )
    else:
        checks.append("ranking_score == -affinity for every scored row")

    # ---- every failed row belongs to the cohort too, with the cohort's own label.
    #      Previously only the scored rows were checked this way, so a failure
    #      naming an unknown compound or carrying the wrong class passed.
    failed_unknown = [f["compound_id"] for f in failures if f["compound_id"] not in by_id]
    if failed_unknown:
        problems.append(f"failed compounds absent from the cohort: {failed_unknown[:10]}")
    failed_mislabelled = [
        f["compound_id"]
        for f in failures
        if f["compound_id"] in by_id and f.get("label") != by_id[f["compound_id"]].label
    ]
    if failed_mislabelled:
        problems.append(
            f"failure labels disagree with the cohort for: {failed_mislabelled[:10]}. The "
            "per-class attrition table would be wrong."
        )
    if not failed_unknown and not failed_mislabelled:
        checks.append(f"all {len(failures)} failure rows match the cohort's ids and labels")

    # ---- scored and failed together account for the cohort, exactly once each.
    #      Each list is deduplicated independently: a duplicated FAILURE used to
    #      slip through because only the scored list was checked, and the summary
    #      then asserted an arithmetic identity that was false
    #      ("578 scored + 23 failed = 600").
    scored_ids = [r["compound_id"] for r in scores]
    failed_ids = [f["compound_id"] for f in failures]
    for name, ids in (("scored", scored_ids), ("failed", failed_ids)):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        if duplicated:
            problems.append(f"compounds listed more than once as {name}: {duplicated[:10]}")

    overlap = sorted(set(scored_ids) & set(failed_ids))
    if overlap:
        problems.append(f"compounds both scored and recorded as failed: {overlap[:10]}")

    accounted = set(scored_ids) | set(failed_ids)
    missing = sorted(set(by_id) - accounted)
    extra = sorted(accounted - set(by_id))
    if missing:
        problems.append(
            f"{len(missing)} cohort member(s) neither scored nor recorded as failed: {missing[:10]}"
        )
    if extra:
        problems.append(f"accounted compounds absent from the cohort: {extra[:10]}")

    # The identity the summary line states, tested rather than assumed.
    if len(scored_ids) + len(failed_ids) != len(by_id):
        problems.append(
            f"{len(scored_ids)} scored + {len(failed_ids)} failed = "
            f"{len(scored_ids) + len(failed_ids)}, which is not the cohort's "
            f"{len(by_id)} members"
        )
    elif not (overlap or missing or extra):
        checks.append(
            f"{len(scored_ids)} scored + {len(failed_ids)} failed = "
            f"{len(by_id)} cohort members, each exactly once"
        )

    if problems:
        return Recomputation(
            decision="unverified",
            auroc=None,
            ci_low=None,
            ci_high=None,
            n_active=0,
            n_inactive=0,
            checks=checks,
            problems=problems,
        )

    gate = evaluate_gate(
        np.array([r["ranking_score"] for r in scores], dtype=np.float64),
        np.array([r["label"] == "active" for r in scores], dtype=bool),
        effect_threshold=config.effect_threshold,
        resamples=config.bootstrap_resamples,
        level=config.bootstrap_level,
        seed=config.bootstrap_seed,
        minimum_per_class=config.minimum_usable_per_class,
    )
    checks.append("gate recomputed from the saved scores under the recorded contract")
    return Recomputation(
        decision=gate.decision,
        auroc=gate.auroc,
        ci_low=gate.ci_low,
        ci_high=gate.ci_high,
        n_active=gate.n_active,
        n_inactive=gate.n_inactive,
        secondary=gate.secondary,
        checks=checks,
        problems=problems,
    )


#: Every secondary metric the report displays. Recomputed and compared, because
#: `check_published` used to compare only the primary fields while the
#: recomputation already held all of these -- so editing bedroc_alpha20 to 0.99
#: published 0.990.
DISPLAYED_SECONDARY = (
    "mannwhitney_u",
    "mannwhitney_p_two_sided",
    "median_ranking_score_active",
    "median_ranking_score_inactive",
    "ef_1pct",
    "ef_5pct",
    "bedroc_alpha20",
)


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= TOLERANCE


def _finite_problems(label: str, values: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    """A required value that is missing, None or non-finite is not publishable."""
    import math

    problems: list[str] = []
    for key in required:
        if key not in values:
            problems.append(f"{label} does not record the required value {key!r}")
            continue
        value = values[key]
        if value is None:
            problems.append(f"{label}.{key} is null")
        elif isinstance(value, (int, float)) and not math.isfinite(float(value)):
            problems.append(f"{label}.{key} is not finite ({value!r})")
    return problems


def check_published(
    published: dict[str, Any],
    recomputed: Recomputation,
) -> list[str]:
    """Does the published result follow from the recomputation?"""
    problems = list(recomputed.problems)
    if recomputed.problems:
        return problems
    if published.get("decision") != recomputed.decision:
        problems.append(
            f"published decision {published.get('decision')!r} but the saved scores give "
            f"{recomputed.decision!r}"
        )
    for name, got in (
        ("auroc", recomputed.auroc),
        ("ci_low", recomputed.ci_low),
        ("ci_high", recomputed.ci_high),
    ):
        if not _close(published.get(name), got):
            problems.append(
                f"published {name} {published.get(name)!r} but the saved scores give {got!r}"
            )
    for name, got in (("n_active", recomputed.n_active), ("n_inactive", recomputed.n_inactive)):
        if published.get(name) != got:
            problems.append(
                f"published {name} {published.get(name)!r} but the saved scores give {got}"
            )

    # Everything the report displays, not just what the decision turns on.
    published_secondary = published.get("secondary") or {}
    problems.extend(
        _finite_problems("published gate.secondary", published_secondary, DISPLAYED_SECONDARY)
    )
    for name in DISPLAYED_SECONDARY:
        got = recomputed.secondary.get(name)
        if name not in published_secondary:
            continue
        if not _close(published_secondary.get(name), got):
            problems.append(
                f"published secondary {name} {published_secondary.get(name)!r} but the saved "
                f"scores give {got!r}"
            )
    return problems


def verify_publication(
    gate_path: Path,
    run_root: Path,
    cohort: Cohort,
    config: DockingConfig,
    *,
    sensitivity_paths: dict[str, Path] | None = None,
    qa_path: Path = Path("reports/results/m10_qa_redock.json"),
    manifest_path: Path = Path("configs/manifests/m10_docking.json"),
    verification_contract: Path | None = None,
) -> Recomputation:
    """Recompute and compare everything the report will display. Raises on mismatch.

    Raises `EvidenceMismatch` rather than returning a flag, because a caller on
    the publishing path could ignore a flag -- and the whole point is that the
    report cannot be written unless its contents follow from the evidence.
    """
    from seq2lead.dock.artifacts import (
        VerificationContractError,
        load_verification_contract,
        verify_manifest_scoped,
    )

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    record = payload.get("record") or {}
    recomputed = recompute_from_saved(
        run_root / "scores.json", run_root / "failures.json", cohort, config
    )
    problems = check_published(payload.get("gate") or {}, recomputed)

    # ---- attrition: the table, the element breakdown and the bounds
    if recomputed.ok:
        problems.extend(
            check_attrition(
                payload,
                recompute_attrition(run_root / "scores.json", run_root / "failures.json", cohort),
            )
        )
        recomputed.checks.append(
            "attrition table, element breakdown and bounds recomputed and matched"
        )

    # ---- the contract this result was produced under
    recorded_config = record.get("config_sha256")
    if recorded_config and recorded_config != config.config_sha256:
        declared: set[str] = set()
        if manifest_path.exists():
            declared = {
                c.get("sha256")
                for c in json.loads(manifest_path.read_text(encoding="utf-8")).get(
                    "superseded_contracts", []
                )
            }
        if recorded_config not in declared:
            problems.append(
                f"the published result records contract {recorded_config[:16]}…, which is "
                "neither the current contract nor a declared superseded one"
            )
        else:
            recomputed.checks.append(
                f"produced under declared superseded contract {recorded_config[:16]}…"
            )
    recorded_cohort = record.get("cohort_sha256")
    if recorded_cohort and recorded_cohort != cohort.membership_sha256():
        problems.append(
            f"the published result records cohort {recorded_cohort[:16]}… but the cohort on "
            f"disk is {cohort.membership_sha256()[:16]}…"
        )

    # ---- the manifest must satisfy a contract declared outside itself
    contract = None
    try:
        contract = load_verification_contract(
            verification_contract
            if verification_contract is not None
            else Path("configs/manifests/m10_verification.json")
        )
    except VerificationContractError as exc:
        problems.append(str(exc))
    if contract is not None:
        scope = verify_manifest_scoped(manifest_path, contract=contract)
        problems.extend(f"manifest: {p}" for p in scope.problems)
        if not scope.problems:
            recomputed.checks.append(
                f"manifest satisfies verification contract {contract.version} "
                f"({len(scope.verified)} artifact(s))"
            )

    # ---- every displayed sensitivity row, against its own saved run
    for label, path in sorted((sensitivity_paths or {}).items()):
        row_problems = verify_sensitivity(Path(path), cohort, config, run_root.parent)
        problems.extend(f"sensitivity {label}: {p}" for p in row_problems)
        if not row_problems:
            recomputed.checks.append(f"sensitivity row {label} recomputed from its own saved run")

    # ---- the QA artifact the report will actually render.
    #      Previously this verified a hardcoded path while `render()` accepted a
    #      `qa_path` override, so pointing the report at a forged QA artifact
    #      published an RMSD of 0.01 A against an untouched real file.
    qa_problems, qa_checks, qa_unavailable = verify_qa(qa_path, manifest_path)
    problems.extend(qa_problems)
    recomputed.checks.extend(qa_checks)
    recomputed.caveats.extend(qa_unavailable)

    if problems:
        listed = "\n  ".join(problems)
        raise EvidenceMismatch(
            "refusing to publish a report that does not follow from the saved evidence:\n"
            f"  {listed}\n"
            "Nothing was written. The saved per-ligand scores are the authority; if they are "
            "right the published file is wrong, and if the published file is right the scores "
            "were not the ones it came from."
        )
    recomputed.problems = []
    return recomputed


# ---------------------------------------------------------------- attrition


def recompute_attrition(scores_path: Path, failures_path: Path, cohort: Cohort) -> dict[str, Any]:
    """Rebuild the per-class table, the element breakdown and the bounds.

    All three are displayed, so all three are recomputed. The element breakdown in
    particular was the subject of a correction (19 unique compounds, not a sum of
    21 per-element tallies), and recomputing it is what stops that drifting again.
    """
    from collections import Counter

    from seq2lead.dock.gate import attrition_bounds
    from seq2lead.dock.runner import attrition_by_class

    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    by_id = {m.compound_id: m for m in cohort.members}

    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    chemistry: Counter[str] = Counter()
    for failure in failures:
        member = by_id.get(failure["compound_id"])
        mol = Chem.MolFromSmiles(member.smiles) if member else None
        if mol is None:
            chemistry["unparseable SMILES"] += 1
            continue
        exotic = sorted(
            {a.GetSymbol() for a in mol.GetAtoms()}
            - {"C", "N", "O", "S", "H", "F", "Cl", "Br", "I", "P"}
        )
        chemistry[",".join(exotic) if exotic else f"no exotic element ({failure['stage']})"] += 1

    return {
        "attrition": attrition_by_class(scores_path, failures_path),
        "attrition_chemistry": dict(chemistry),
        "attrition_bounds": attrition_bounds(
            np.array([r["ranking_score"] for r in scores], dtype=np.float64),
            np.array([r["label"] == "active" for r in scores], dtype=bool),
            cohort.n_active,
            cohort.n_inactive,
        ),
    }


def check_attrition(published: dict[str, Any], recomputed: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if published.get("attrition") != recomputed["attrition"]:
        problems.append(
            f"published attrition table {published.get('attrition')!r} does not match the "
            f"saved files, which give {recomputed['attrition']!r}"
        )
    if published.get("attrition_chemistry") != recomputed["attrition_chemistry"]:
        problems.append(
            f"published attrition chemistry {published.get('attrition_chemistry')!r} does not "
            f"match the saved files, which give {recomputed['attrition_chemistry']!r}"
        )
    bounds_published = published.get("attrition_bounds") or {}
    problems.extend(
        _finite_problems(
            "published attrition_bounds",
            bounds_published,
            ("observed_auroc", "best_case_auroc", "worst_case_auroc"),
        )
    )
    for key, got in recomputed["attrition_bounds"].items():
        if key in bounds_published and not _close(bounds_published[key], got):
            problems.append(
                f"published attrition bound {key} {bounds_published[key]!r} but the saved "
                f"scores give {got!r}"
            )
    return problems


# ------------------------------------------------------- sensitivity and QA


def verify_sensitivity(
    published_path: Path,
    cohort: Cohort,
    config: DockingConfig,
    run_root: Path = Path("data/m10/runs"),
) -> list[str]:
    """Check a displayed sensitivity result against *its own* saved run.

    Each sensitivity row has its own scores, failures and run record. Verifying
    the primary and trusting the rest would leave the table forgeable.
    """
    problems: list[str] = []
    if not published_path.exists():
        return [f"displayed sensitivity result {published_path} is absent"]
    payload = json.loads(published_path.read_text(encoding="utf-8"))
    gate = payload.get("gate") or {}
    record = payload.get("record") or {}
    label = record.get("run")
    if not label:
        return [f"{published_path} does not record which run produced it"]

    run = run_root / label
    if not (run / "scores.json").exists():
        return [f"{published_path} names run {label!r} but {run}/scores.json is absent"]

    recomputed = recompute_from_saved(run / "scores.json", run / "failures.json", cohort, config)
    if not recomputed.ok:
        return [f"{label}: {problem}" for problem in recomputed.problems]
    problems.extend(f"{label}: {p}" for p in check_published(gate, recomputed))

    # the box this row claims is the box its record says was used
    declared = payload.get("scale")
    if declared is not None:
        expected = [round(v * float(declared), 6) for v in config.box_size]
        actual = [round(float(v), 6) for v in record.get("box_size", [])]
        if expected != actual:
            problems.append(
                f"{label}: claims scale {declared} of {config.box_size} = {expected} but its "
                f"run record says {actual}"
            )
    if record.get("box_size") == list(config.box_size):
        problems.append(
            f"{label}: its run record uses the primary box {config.box_size}, so it is not a "
            "sensitivity run at all"
        )
    return problems


def recalculate_pose_rmsd(
    pose_path: Path, structure_path: Path, resname: str
) -> tuple[float | None, list[str]]:
    """Recompute the top-pose RMSD from saved files. Returns (rmsd, missing_inputs).

    Re-docking is **not** required for this. Three inputs suffice: the saved pose,
    the crystal coordinates, and an atom mapping between them. The mapping comes
    from the SMILES meeko embeds in the pose file as a `REMARK SMILES` line, and
    `rdMolAlign.CalcRMS` resolves topological symmetry itself.

    An earlier version of this module claimed recalculation needed re-docking and
    settled for a digest check. That was wrong, and it understated what the
    published artifact could be held to.
    """
    missing: list[str] = []
    if not pose_path.exists():
        missing.append(f"{pose_path} (the saved docked pose)")
    if not structure_path.exists():
        missing.append(f"{structure_path} (the crystal reference coordinates)")
    if missing:
        return (None, missing)

    from meeko import PDBQTMolecule, RDKitMolCreate
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem, rdMolAlign

    RDLogger.DisableLog("rdApp.*")

    smiles = None
    for line in pose_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("REMARK SMILES") and "IDX" not in line:
            smiles = line.split("REMARK SMILES", 1)[1].strip()
            break
    if not smiles:
        return (None, [f"{pose_path} carries no REMARK SMILES line to map atoms by"])

    het = [
        line
        for line in structure_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("HETATM") and line[17:20].strip() == resname
    ]
    if not het:
        return (None, [f"{structure_path} contains no {resname} to compare against"])

    raw = Chem.MolFromPDBBlock("\n".join(het) + "\nEND\n", removeHs=False, sanitize=False)
    crystal = Chem.RemoveHs(AllChem.AssignBondOrdersFromTemplate(Chem.MolFromSmiles(smiles), raw))
    docked = RDKitMolCreate.from_pdbqt_mol(
        PDBQTMolecule.from_file(str(pose_path), skip_typing=True)
    )[0]
    probe = Chem.Mol(docked)
    probe.RemoveAllConformers()
    probe.AddConformer(docked.GetConformer(0), assignId=True)
    return (float(rdMolAlign.CalcRMS(Chem.RemoveHs(probe), crystal)), [])


def verify_qa(
    qa_path: Path,
    manifest_path: Path,
    *,
    pose_path: Path = Path("data/m10/runs/qa-redock/SUA_redock.pdbqt"),
    structure_path: Path = Path("data/m10/structures/3k34.pdb"),
    rmsd_tolerance: float = 1e-3,
) -> tuple[list[str], list[str], list[str]]:
    """Check the QA artifact by digest **and**, where possible, by recalculation.

    Returns (problems, checks, unavailable). The recalculation is attempted
    whenever its inputs are present; when they are not -- as in the review
    archive, which ships neither the pose directory nor the raw structure -- the
    specific missing inputs are named rather than the limitation being asserted
    in the abstract.
    """
    from seq2lead.dock.artifacts import sha256_file

    problems: list[str] = []
    checks: list[str] = []
    unavailable: list[str] = []

    if not qa_path.exists():
        return ([f"QA artifact {qa_path} is absent"], checks, unavailable)
    if not manifest_path.exists():
        return ([f"cannot verify the QA digest: no manifest at {manifest_path}"], checks, [])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = (manifest.get("published") or {}).get("qa") or []
    match = [r for r in rows if r.get("path") == str(qa_path)]
    if not match:
        problems.append(
            f"the manifest binds no QA digest for {qa_path}. A QA artifact the report renders "
            "but the manifest does not bind cannot be verified at all."
        )
    else:
        expected = match[0].get("sha256")
        actual = sha256_file(qa_path)
        if expected != actual:
            problems.append(
                f"QA artifact {qa_path} is {actual[:16]}… but the manifest bound {expected[:16]}…"
            )
        else:
            checks.append("QA artifact matches the digest the manifest bound")

    payload = json.loads(qa_path.read_text(encoding="utf-8"))
    problems.extend(
        _finite_problems("QA artifact", payload, ("top_pose_rmsd", "threshold", "passed"))
    )
    if payload.get("passed") is not (
        payload.get("top_pose_rmsd", 1e9) <= payload.get("threshold", 0)
    ):
        problems.append("the QA artifact's `passed` flag disagrees with its own RMSD and threshold")

    recalculated, missing = recalculate_pose_rmsd(
        pose_path, structure_path, str(payload.get("ligand", "")) or "SUA"
    )
    if recalculated is None:
        unavailable.extend(missing)
        unavailable.append(
            "QA pose RMSD could not be independently recalculated here for want of the "
            "inputs above; it rests on the artifact's digest alone. Re-docking is NOT "
            "required -- the saved pose and the crystal coordinates would suffice."
        )
    elif abs(recalculated - float(payload["top_pose_rmsd"])) > rmsd_tolerance:
        problems.append(
            f"QA artifact reports top_pose_rmsd {payload['top_pose_rmsd']} but recomputing "
            f"it from {pose_path} against {structure_path} gives {recalculated:.3f}"
        )
    else:
        checks.append(
            f"QA pose RMSD independently recalculated from the saved pose "
            f"({recalculated:.3f} Å), without re-docking"
        )
    return (problems, checks, unavailable)
