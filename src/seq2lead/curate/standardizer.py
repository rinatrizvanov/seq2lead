"""Versioned RDKit structure standardization.

`standardize()` is a pure function of a SMILES string so it can be farmed out to
a process pool — 1.4M unique structures is too slow single-threaded.

**The pipeline, and why each step is here.** Bumping any of it means bumping
`STANDARDIZER_VERSION`, which changes the `compound` uniqueness key and the
`compound_source` cache key, so old and new results never silently mix.

1. `MolFromSmiles` — parse and sanitize. Failure is `invalid_smiles`.
2. `Cleanup` — normalize functional groups, disconnect metals, reionize.
3. `FragmentParent` — keep the largest organic fragment. This is what strips
   salts and solvates, so a hydrochloride and its free base become one compound.
4. `Uncharger` — neutralize what can be neutralized, so a carboxylate and its
   acid do not become two compounds.
5. Canonical SMILES and InChIKey of the result.

What it deliberately does **not** do: tautomer canonicalization. RDKit's
tautomer enumeration is slow and its canonical choice is a convention rather than
a chemical fact, so tautomers remain distinct compounds here. That is a known
limitation to state, not a bug to hide.
"""

from __future__ import annotations

from dataclasses import dataclass

import rdkit
from rdkit import Chem, RDLogger
from rdkit.Chem.MolStandardize import rdMolStandardize

# RDKit is loud about recoverable parse problems; we record them ourselves.
RDLogger.DisableLog("rdApp.*")

PIPELINE = "cleanup+fragment-parent+uncharge"
STANDARDIZER_VERSION = f"rdkit-{rdkit.__version__}/{PIPELINE}/v1"


@dataclass(frozen=True)
class Standardized:
    inchikey: str
    canonical_smiles: str
    n_heavy_atoms: int


def standardize(smiles: str) -> Standardized | None:
    """Return the standardized parent, or None if the structure is unusable."""
    if not smiles or not smiles.strip():
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        mol = rdMolStandardize.Cleanup(mol)
        mol = rdMolStandardize.FragmentParent(mol)
        mol = rdMolStandardize.Uncharger().uncharge(mol)
        if mol is None or mol.GetNumAtoms() == 0:
            return None
        inchikey = Chem.MolToInchiKey(mol)
        if not inchikey:
            return None
        return Standardized(
            inchikey=inchikey,
            canonical_smiles=Chem.MolToSmiles(mol),
            n_heavy_atoms=mol.GetNumHeavyAtoms(),
        )
    except Exception:  # noqa: BLE001 - any RDKit failure means "unusable structure"
        return None


def standardize_batch(items: list[tuple[str, str]]) -> list[tuple[str, Standardized | None]]:
    """Standardize `(key, smiles)` pairs. Shaped for `ProcessPoolExecutor.map`."""
    return [(key, standardize(smiles)) for key, smiles in items]
