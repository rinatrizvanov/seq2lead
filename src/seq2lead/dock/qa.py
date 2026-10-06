"""Pose-recovery QA: can the protocol reproduce a pose we already know?

Reported separately from the gate, and it answers a different question. Pose
recovery says the search and the scoring function can find the crystallographic
minimum when it exists. It says nothing about whether the protocol can *rank*
one ligand above another, which is what the gate asks. A protocol can recover a
pose perfectly and still rank at chance.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.dock.engine import VINA_BIN, DockFailure, dock_one
from seq2lead.dock.ligands import LigandFailure, prepare_ligand

if TYPE_CHECKING:
    from seq2lead.dock.config import DockingConfig


@dataclass
class RedockResult:
    ligand: str
    n_poses: int
    top_pose_rmsd: float
    best_pose_rmsd: float
    best_pose_rank: int
    affinity_kcal_per_mol: float
    metal_contact_crystal: float | None
    metal_contact_docked: float | None
    threshold: float
    passed: bool
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def _crystal_ligand(pdb: Path, resname: str, smiles: str):
    """Rebuild the deposited ligand with correct bond orders from a SMILES template."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    lines = [
        line
        for line in pdb.read_text(encoding="utf-8").splitlines()
        if line.startswith("HETATM") and line[17:20].strip() == resname
    ]
    if not lines:
        raise ValueError(f"{pdb} has no {resname}")
    raw = Chem.MolFromPDBBlock("\n".join(lines) + "\nEND\n", removeHs=False, sanitize=False)
    return AllChem.AssignBondOrdersFromTemplate(Chem.MolFromSmiles(smiles), raw)


def _closest_heteroatom_to(mol, point: tuple[float, float, float], elements=("N", "O")) -> float:
    conf = mol.GetConformer()
    best = math.inf
    for atom in mol.GetAtoms():
        if atom.GetSymbol() in elements:
            p = conf.GetAtomPosition(atom.GetIdx())
            best = min(best, math.dist((p.x, p.y, p.z), point))
    return best


def redock_reference(
    config: DockingConfig,
    receptor_pdbqt: Path,
    work_dir: Path,
    structure: Path,
    resname: str,
    smiles: str,
    metal_point: tuple[float, float, float] | None = None,
    binary: Path = VINA_BIN,
) -> RedockResult:
    """Prepare the reference ligand from scratch, dock it, compare to the crystal pose.

    The docking input is built from SMILES and re-embedded, never from the
    deposited coordinates -- otherwise the search would start at the answer and
    the RMSD would measure nothing.
    """
    from meeko import PDBQTMolecule, RDKitMolCreate
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign

    work_dir.mkdir(parents=True, exist_ok=True)
    crystal = Chem.RemoveHs(_crystal_ligand(structure, resname, smiles))

    prepared = prepare_ligand(0, smiles, work_dir / f"{resname}_input.pdbqt", config.seed)
    if isinstance(prepared, LigandFailure):
        raise ValueError(f"reference ligand preparation failed: {prepared.reason}")

    out = dock_one(
        0, prepared.path, receptor_pdbqt, work_dir / f"{resname}_redock.pdbqt", config, binary
    )
    if isinstance(out, DockFailure):
        raise ValueError(f"reference ligand docking failed: {out.reason}")

    pm = PDBQTMolecule.from_file(str(out.pose_path), skip_typing=True)
    docked = RDKitMolCreate.from_pdbqt_mol(pm)[0]
    rmsds: list[float] = []
    for ci in range(docked.GetNumConformers()):
        probe = Chem.Mol(docked)
        probe.RemoveAllConformers()
        probe.AddConformer(docked.GetConformer(ci), assignId=True)
        rmsds.append(rdMolAlign.CalcRMS(Chem.RemoveHs(probe), crystal))

    d_cry = d_dock = None
    if metal_point is not None:
        d_cry = _closest_heteroatom_to(crystal, metal_point)
        top = Chem.Mol(docked)
        top.RemoveAllConformers()
        top.AddConformer(docked.GetConformer(0), assignId=True)
        d_dock = _closest_heteroatom_to(top, metal_point)

    threshold = float(config.raw["qa"]["pose_rmsd_threshold_angstrom"])
    note = (
        "Pose recovery only. It does not license the ranking the gate measures."
        if rmsds[0] <= threshold
        else "The protocol did not reproduce the known pose."
    )
    if d_cry is not None and d_dock is not None and d_dock - d_cry > 0.5:
        note += (
            f" The docked pose sits {d_dock - d_cry:.2f} A further from the metal than the "
            "crystal pose, the signature of an empirical scoring function that does not model "
            "metal coordination explicitly."
        )
    return RedockResult(
        ligand=resname,
        n_poses=len(rmsds),
        top_pose_rmsd=round(rmsds[0], 3),
        best_pose_rmsd=round(min(rmsds), 3),
        best_pose_rank=rmsds.index(min(rmsds)) + 1,
        affinity_kcal_per_mol=out.affinity,
        metal_contact_crystal=None if d_cry is None else round(d_cry, 3),
        metal_contact_docked=None if d_dock is None else round(d_dock, 3),
        threshold=threshold,
        passed=rmsds[0] <= threshold,
        note=note,
    )
