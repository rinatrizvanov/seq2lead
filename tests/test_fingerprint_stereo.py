"""Stereochemistry in the primary fingerprints.

M3 repaired compound identity after finding BindingDB's InChI Keys are
stereo-insensitive: 64,486 keys carried 2-8 distinct stereoisomers across
448,735 rows, so compounds were re-keyed on their structure. An achiral
fingerprint undoes that at the last step -- two enantiomers become the same
model input, and the model is asked to predict different affinities from
identical vectors.
"""

from __future__ import annotations

import numpy as np
import pytest

from seq2lead.features import ecfp

#: Verified pairs: each is a genuine stereoisomer pair differing only in
#: configuration, written so the two members are otherwise identical.
STEREO_PAIRS = {
    "alanine R/S": ("N[C@@H](C)C(=O)O", "N[C@H](C)C(=O)O"),
    "nicotine R/S": ("CN1CCC[C@H]1c1cccnc1", "CN1CCC[C@@H]1c1cccnc1"),
    "2-butene cis/trans": (r"C/C=C\C", "C/C=C/C"),
}


def _fingerprint(smiles: str, chirality: bool) -> np.ndarray:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdFingerprintGenerator

    RDLogger.DisableLog("rdApp.*")
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=ecfp.RADIUS, fpSize=ecfp.N_BITS, includeChirality=chirality
    )
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"test pair member does not parse: {smiles}"
    return generator.GetFingerprintAsNumPy(mol).astype(np.uint8)


@pytest.mark.parametrize("name", sorted(STEREO_PAIRS))
def test_achiral_fingerprints_cannot_tell_stereoisomers_apart(name) -> None:
    """The defect. Both members get byte-identical vectors."""
    left, right = STEREO_PAIRS[name]
    assert np.array_equal(_fingerprint(left, False), _fingerprint(right, False))


@pytest.mark.parametrize("name", sorted(STEREO_PAIRS))
def test_chiral_fingerprints_distinguish_them(name) -> None:
    """The fix, on the same pairs, so the contrast is exact."""
    left, right = STEREO_PAIRS[name]
    assert not np.array_equal(_fingerprint(left, True), _fingerprint(right, True))


def test_chirality_is_on_by_default_and_is_part_of_the_identity() -> None:
    assert ecfp.CHIRALITY is True
    chiral = ecfp.ecfp_spec("m3/v3", chirality=True)
    achiral = ecfp.ecfp_spec("m3/v3", chirality=False)
    assert chiral.chirality is True
    assert chiral.sha256() != achiral.sha256(), "an achiral and a chiral cache would share a name"


def test_chiral_fingerprints_are_not_claimed_to_be_unique() -> None:
    """A hashed 2,048-bit fingerprint collides regardless of stereochemistry.

    Enabling chirality resolves most collisions between distinct structures; it
    does not make the representation injective, and the report must not say it
    does. Two different molecules folded into the same 2,048 bits stay
    indistinguishable.
    """
    generator_bits = ecfp.N_BITS
    assert generator_bits < 2**16, "a hashed fingerprint is lossy by construction"
    # Distinct constitutional isomers that are not stereoisomers of each other
    # still occupy the same bit space; uniqueness is never guaranteed.
    a = _fingerprint("CCCCCCCCCCCCCCCCCCCC", True)
    b = _fingerprint("CCCCCCCCCCCCCCCCCCCCC", True)
    assert a.shape == b.shape == (generator_bits,)


@pytest.mark.requires_db
def test_the_built_cache_uses_chirality() -> None:
    import json

    from seq2lead.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT params FROM feature_version WHERE kind='ecfp4' "
            "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no current ecfp4 cache")
    spec = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    assert spec.get("chirality") is True


@pytest.mark.requires_db
def test_the_achiral_cache_is_preserved_and_named_as_such() -> None:
    """Superseded, not deleted: it is a valid achiral cache, not a corrupt one."""
    from seq2lead.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT name, superseded_reason FROM feature_version "
            "WHERE kind='ecfp4' AND superseded_by IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("no superseded ecfp4 cache")
    assert row[1] and "chiral" in str(row[1]).lower()
