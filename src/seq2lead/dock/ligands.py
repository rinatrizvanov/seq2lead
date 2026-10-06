"""Ligand preparation: curated SMILES -> one 3D conformer -> PDBQT.

Every failure is returned with the compound id and a reason, never swallowed.
A compound that cannot be prepared is dropped from the metrics and counted in
the attrition table; it is never scored as "worst", which would invent
separation out of a preparation bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreparedLigand:
    compound_id: int
    path: Path
    n_atoms: int
    n_rotatable: int


@dataclass(frozen=True)
class LigandFailure:
    compound_id: int
    stage: str  # parse | embed | optimise | pdbqt
    reason: str


def prepare_ligand(
    compound_id: int, smiles: str, out_path: Path, seed: int
) -> PreparedLigand | LigandFailure:
    """One ligand, one conformer, deterministic for a given seed."""
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem, rdMolDescriptors

    RDLogger.DisableLog("rdApp.*")

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return LigandFailure(compound_id, "parse", "RDKit could not parse the curated SMILES")
    n_rot = rdMolDescriptors.CalcNumRotatableBonds(mol)
    mol = Chem.AddHs(mol)

    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(mol, params) != 0:
        # One documented retry with random coordinates: ETKDG fails on some
        # macrocycles and cages from the distance-geometry stage alone.
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(mol, params) != 0:
            return LigandFailure(compound_id, "embed", "no 3D conformer could be generated")

    try:
        AllChem.MMFFOptimizeMolecule(mol)
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        return LigandFailure(compound_id, "optimise", f"MMFF optimisation failed: {exc}")

    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy

        prep = MoleculePreparation()
        setups = prep.prepare(mol)
        if not setups:
            return LigandFailure(compound_id, "pdbqt", "meeko returned no setup")
        text, ok, err = PDBQTWriterLegacy.write_string(setups[0])
        if not ok:
            return LigandFailure(compound_id, "pdbqt", f"meeko refused to write: {err}")
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        return LigandFailure(compound_id, "pdbqt", f"meeko raised: {exc}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return PreparedLigand(
        compound_id=compound_id,
        path=out_path,
        n_atoms=mol.GetNumAtoms(),
        n_rotatable=n_rot,
    )
