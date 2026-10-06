"""ECFP4 fingerprints for the compounds a declared release actually measured.

Input-only: a fingerprint is a function of the structure alone and reads no
measured activity, so it may be computed for held-out compounds without leaking
anything.

**Stereochemistry is on by default.** M3 went to some trouble to repair compound
identity after discovering BindingDB's InChI Keys are stereo-insensitive --
64,486 keys carried 2-8 distinct stereoisomers across 448,735 rows -- and keyed
compounds on their structure instead. An achiral fingerprint throws that repair
away at the last step: measured here, R/S enantiomers and cis/trans isomers
receive *byte-identical* vectors under `includeChirality=False`. The model would
then be asked to predict different affinities from identical inputs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

from seq2lead.features.identity import FeatureSpec
from seq2lead.features.manifest import build_manifest, library_versions
from seq2lead.features.store import FeatureStore

if TYPE_CHECKING:
    import psycopg

    from seq2lead.features.manifest import InputManifest

RADIUS = 2  # ECFP4 == Morgan radius 2
N_BITS = 2048
CHIRALITY = True
BATCH = 20_000


def ecfp_spec(
    standardizer_version: str,
    radius: int = RADIUS,
    n_bits: int = N_BITS,
    chirality: bool = CHIRALITY,
) -> FeatureSpec:
    return FeatureSpec(
        kind="ecfp4",
        entity="compound",
        pooling="none",
        radius=radius,
        n_bits=n_bits,
        chirality=chirality,
        standardizer_version=standardizer_version,
        dtype="uint8",
        extra={"features": False, "storage": "bitpacked"},
    )


def build_compound_features(
    conn: psycopg.Connection,
    standardizer_version: str,
    population: str,
    radius: int = RADIUS,
    n_bits: int = N_BITS,
    chirality: bool = CHIRALITY,
    limit: int | None = None,
) -> tuple[FeatureStore, InputManifest, list[int]]:
    """Fingerprint the declared population. Unparseable structures are flagged, not dropped."""
    from rdkit import Chem, RDLogger
    from rdkit.Chem import rdFingerprintGenerator

    RDLogger.DisableLog("rdApp.*")
    started = time.perf_counter()
    spec = ecfp_spec(standardizer_version, radius, n_bits, chirality)
    manifest, entity_ids = build_manifest(conn, "compound", population, limit=limit)
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=radius, fpSize=n_bits, includeChirality=chirality
    )

    n = len(entity_ids)
    # Bit-packed: 2,048 bits is 256 bytes, not 2,048. At 1.4M compounds that is
    # the difference between ~370 MB and ~3 GB on disk and in RAM.
    packed_bytes = n_bits // 8
    ids = np.zeros(n, dtype=np.int64)
    vectors = np.zeros((n, packed_bytes), dtype=np.uint8)
    flags: dict[int, list[tuple[str, dict]]] = {}

    position = {int(cid): i for i, cid in enumerate(entity_ids)}
    with conn.cursor(name="ecfp_cursor") as cur:
        cur.itersize = BATCH
        cur.execute(
            "SELECT id, canonical_smiles FROM compound WHERE id = ANY(%s) ORDER BY id",
            (entity_ids,),
        )
        for compound_id, smiles in cur:
            i = position[int(compound_id)]
            ids[i] = int(compound_id)
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                # Left as an all-zero row so the matrix stays aligned with `ids`.
                # The flag is what tells a consumer not to read it as a molecule
                # with no features -- see the missing-feature policy in the report.
                flags[int(compound_id)] = [("unparseable_smiles", {"smiles": smiles[:200]})]
            else:
                bits = generator.GetFingerprintAsNumPy(mol).astype(np.uint8)
                vectors[i] = np.packbits(bits)

    return (
        FeatureStore(
            spec=spec,
            ids=ids,
            vectors=vectors,
            flags=flags,
            seconds=time.perf_counter() - started,
            manifest=manifest,
            library_versions=library_versions("ecfp4"),
        ),
        manifest,
        entity_ids,
    )


def unpack(vectors: np.ndarray, n_bits: int = N_BITS) -> np.ndarray:
    """Bit-packed rows back to a 0/1 matrix. The stored form is packed."""
    return np.unpackbits(vectors, axis=1)[:, :n_bits]
