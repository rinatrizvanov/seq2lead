"""Sequence and chemistry clustering that backs the cold splits.

Both exist so that `cold_protein` and `chemistry_disjoint` hold out *groups*
rather than individuals. Holding out a single protein while its 99%-identical
homolog stays in training is not a cold-protein test, and holding out one
analog while its scaffold-mates remain is not a chemistry-disjoint one.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 - runs mmseqs, a declared dependency
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

MMSEQS_METHOD = "mmseqs2-cluster"
SCAFFOLD_METHOD = "bemis-murcko"

#: 40% identity. Reported at 30/40/60 in the audit so the choice is visible;
#: 60% is what ProtoBind-Diff used with CD-HIT and DeepDTA's reported
#: Smith-Waterman redundancy level, so 40% is the stricter end of precedent.
DEFAULT_IDENTITY = 0.40


def mmseqs_available() -> bool:
    return shutil.which("mmseqs") is not None


def cluster_sequences(
    sequences: dict[int, str], identity: float = DEFAULT_IDENTITY, coverage: float = 0.8
) -> dict[int, str]:
    """Cluster target sequences by identity. Returns target_id -> cluster id.

    Uses `mmseqs easy-cluster`, whose representative-sequence output gives a flat
    membership table directly.
    """
    if not mmseqs_available():
        raise RuntimeError(
            "mmseqs is not installed. `brew install mmseqs2` (or conda) and retry; "
            "cold_protein cannot be built without sequence clustering."
        )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fasta = root / "targets.fasta"
        with fasta.open("w", encoding="ascii") as fh:
            for target_id, sequence in sequences.items():
                fh.write(f">{target_id}\n{sequence}\n")

        subprocess.run(  # noqa: S603
            [
                "mmseqs",
                "easy-cluster",
                str(fasta),
                str(root / "out"),
                str(root / "tmp"),
                "--min-seq-id",
                str(identity),
                "-c",
                str(coverage),
                "--cov-mode",
                "0",
                "-v",
                "1",
            ],
            check=True,
            capture_output=True,
            timeout=3600,
        )
        membership: dict[int, str] = {}
        with (root / "out_cluster.tsv").open(encoding="ascii") as fh:
            for line in fh:
                representative, member = line.rstrip("\n").split("\t")
                membership[int(member)] = representative
    return membership


def _scaffold(smiles: str) -> str:
    """Bemis-Murcko scaffold, or a stable sentinel when there is none."""
    from rdkit import Chem, RDLogger
    from rdkit.Chem.Scaffolds import MurckoScaffold

    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return "__unparseable__"
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
        text = Chem.MolToSmiles(scaffold) if scaffold is not None else ""
    except Exception:  # noqa: BLE001 - any failure is "no usable scaffold"
        return "__unparseable__"
    # An acyclic molecule has an empty Murcko scaffold. Grouping every acyclic
    # compound into one cluster would create an enormous artificial group, so each
    # gets its own singleton keyed by its own structure.
    return text if text else f"__acyclic__{smiles}"


def _scaffold_batch(items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(compound_id, _scaffold(smiles)) for compound_id, smiles in items]


def cluster_compounds(compounds: dict[int, str], workers: int = 8) -> dict[int, str]:
    """Group compounds by Bemis-Murcko scaffold. Returns compound_id -> scaffold.

    Butina clustering on ECFP4 would be the alternative, but it is O(n^2) in the
    number of compounds and this endpoint carries several hundred thousand.
    Scaffold grouping is the standard substitute and is linear.
    """
    items = list(compounds.items())
    if not items:
        return {}
    if workers <= 1 or len(items) < 512:
        return dict(_scaffold_batch(items))
    chunk = max(1, len(items) // (workers * 4))
    shards = [items[i : i + chunk] for i in range(0, len(items), chunk)]
    out: dict[int, str] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for shard in pool.map(_scaffold_batch, shards):
            out.update(dict(shard))
    return out


def store_target_clusters(
    conn: psycopg.Connection, membership: dict[int, str], identity: float
) -> int:
    conn.execute(
        "DELETE FROM target_cluster WHERE method=%s AND threshold=%s::numeric",
        (MMSEQS_METHOD, identity),
    )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO target_cluster (method, threshold, target_id, cluster_id) "
            "VALUES (%s,%s,%s,%s)",
            [(MMSEQS_METHOD, identity, t, c) for t, c in membership.items()],
        )
    return len(membership)


def store_compound_clusters(conn: psycopg.Connection, membership: dict[int, str]) -> int:
    conn.execute("DELETE FROM compound_cluster WHERE method=%s", (SCAFFOLD_METHOD,))
    copy_sql = "COPY compound_cluster (method, compound_id, cluster_id) FROM STDIN"
    with conn.cursor() as cur, cur.copy(copy_sql) as cp:
        for compound_id, cluster_id in membership.items():
            cp.write_row((SCAFFOLD_METHOD, compound_id, cluster_id))
    return len(membership)
