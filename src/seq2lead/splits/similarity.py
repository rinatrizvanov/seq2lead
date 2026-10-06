"""How close is each held-out entity to the nearest thing in training?

A zero-overlap assertion says no *group* spans partitions. It does not say the
held-out entities are actually unfamiliar: two proteins below the clustering
threshold can still share a binding site, and two compounds with different
Bemis-Murcko scaffolds can still be near-neighbours by fingerprint. These
distributions are what turn "disjoint by construction" into a claim with
evidence behind it.
"""

from __future__ import annotations

import random
import shutil
import statistics
import subprocess  # noqa: S404 - runs mmseqs, a declared dependency
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

#: Sampling caps. Exhaustive nearest-neighbour over 250k test x 330k train
#: compounds is a 10^11-comparison job; a sample with its size reported is the
#: honest alternative to not measuring at all.
COMPOUND_TEST_SAMPLE = 2_000
COMPOUND_TRAIN_SAMPLE = 20_000


@dataclass
class HighIdentityHit:
    """One held-out target's best hit into training, with the geometry behind it.

    A bare identity figure cannot distinguish "the same protein under another
    accession" from "a short conserved motif shared by two unrelated proteins".
    Alignment length and coverage on *both* sequences are what separate them.
    """

    query: int
    target: int
    identity: float
    aln_len: int
    query_len: int
    target_len: int
    query_cov: float
    target_cov: float

    @property
    def mutual_coverage(self) -> float:
        return min(self.query_cov, self.target_cov)


@dataclass
class SimilarityDistribution:
    label: str
    n: int
    sampled: bool
    p10: float | None = None
    median: float | None = None
    p90: float | None = None
    p99: float | None = None
    fraction_over: dict[float, float] | None = None

    @classmethod
    def of(
        cls, label: str, values: list[float], sampled: bool, cutoffs: tuple[float, ...]
    ) -> SimilarityDistribution:
        if not values:
            return cls(label=label, n=0, sampled=sampled)
        ordered = sorted(values)
        return cls(
            label=label,
            n=len(ordered),
            sampled=sampled,
            p10=ordered[int(0.10 * (len(ordered) - 1))],
            median=statistics.median(ordered),
            p90=ordered[int(0.90 * (len(ordered) - 1))],
            p99=ordered[int(0.99 * (len(ordered) - 1))],
            fraction_over={c: sum(1 for v in ordered if v > c) / len(ordered) for c in cutoffs},
        )


def _partition_targets(conn: psycopg.Connection, split_id: int, partition: str) -> dict[int, str]:
    rows = conn.execute(
        "SELECT DISTINCT t.id, t.sequence FROM split_pair_assignment p "
        "JOIN target t ON t.id = p.target_id WHERE p.split_id=%s AND p.partition=%s",
        (split_id, partition),
    ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def target_identity_to_train(
    conn: psycopg.Connection, split_id: int, partition: str = "test"
) -> SimilarityDistribution:
    """Best sequence identity from each held-out target to any training target."""
    if shutil.which("mmseqs") is None:
        return SimilarityDistribution(label=f"{partition} target identity", n=0, sampled=False)
    held = _partition_targets(conn, split_id, partition)
    train = _partition_targets(conn, split_id, "train")
    if not held or not train:
        return SimilarityDistribution(label=f"{partition} target identity", n=0, sampled=False)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, seqs in (("query", held), ("db", train)):
            with (root / f"{name}.fasta").open("w", encoding="ascii") as fh:
                for tid, seq in seqs.items():
                    fh.write(f">{tid}\n{seq}\n")
        subprocess.run(  # noqa: S603
            [
                "mmseqs",
                "easy-search",
                str(root / "query.fasta"),
                str(root / "db.fasta"),
                str(root / "hits.tsv"),
                str(root / "tmp"),
                "--format-output",
                "query,target,fident,alnlen,qlen,tlen,qcov,tcov",
                "-s",
                "5.7",
                "--max-seqs",
                "50",
                "-v",
                "1",
            ],
            check=True,
            capture_output=True,
            timeout=3600,
        )
        best: dict[int, float] = {}
        with (root / "hits.tsv").open(encoding="ascii") as fh:
            for line in fh:
                query, _target, fident = line.rstrip("\n").split("\t")[:3]
                value = float(fident)
                key = int(query)
                if value > best.get(key, -1.0):
                    best[key] = value
    # A held-out target with no hit at all is maximally unfamiliar, which is the
    # good case and must not be dropped from the distribution.
    values = [best.get(tid, 0.0) for tid in held]
    return SimilarityDistribution.of(
        f"{partition} target -> nearest train target (sequence identity)",
        values,
        sampled=False,
        cutoffs=(0.3, 0.5, 0.9),
    )


def compound_tanimoto_to_train(
    conn: psycopg.Connection,
    split_id: int,
    partition: str = "test",
    seed: int = 20260929,
) -> SimilarityDistribution:
    """Max ECFP4 Tanimoto from a sample of held-out compounds to a sample of training ones.

    Sampled on both sides, and says so: the exhaustive comparison is ~10^11 pairs.
    Because the training side is sampled, every value is a **lower bound** on the
    true nearest-neighbour similarity.
    """
    from rdkit import Chem, DataStructs, RDLogger
    from rdkit.Chem import rdFingerprintGenerator

    RDLogger.DisableLog("rdApp.*")
    rng = random.Random(seed)

    def smiles_for(part: str, cap: int) -> list[str]:
        # ORDER BY is load-bearing, not cosmetic: without it Postgres may return
        # these rows in any order, so a seeded shuffle of them is not reproducible
        # and the reported percentiles drift between runs of the same command.
        rows = conn.execute(
            "SELECT DISTINCT c.canonical_smiles FROM split_pair_assignment p "
            "JOIN compound c ON c.id = p.compound_id WHERE p.split_id=%s AND p.partition=%s "
            "ORDER BY 1",
            (split_id, part),
        ).fetchall()
        values = [str(r[0]) for r in rows]
        rng.shuffle(values)
        return values[:cap]

    held = smiles_for(partition, COMPOUND_TEST_SAMPLE)
    train = smiles_for("train", COMPOUND_TRAIN_SAMPLE)
    if not held or not train:
        return SimilarityDistribution(label=f"{partition} compound Tanimoto", n=0, sampled=True)

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

    def fingerprints(smiles: list[str]) -> list:
        out = []
        for text in smiles:
            mol = Chem.MolFromSmiles(text)
            if mol is not None:
                out.append(generator.GetFingerprint(mol))
        return out

    train_fps = fingerprints(train)
    values = [max(DataStructs.BulkTanimotoSimilarity(fp, train_fps)) for fp in fingerprints(held)]
    return SimilarityDistribution.of(
        f"{partition} compound -> nearest train compound (ECFP4 Tanimoto)",
        values,
        sampled=True,
        cutoffs=(0.4, 0.7, 0.9),
    )


def partition_target_count(conn: psycopg.Connection, split_id: int, partition: str = "test") -> int:
    """How many distinct targets a partition scores."""
    return len(_partition_targets(conn, split_id, partition))


def high_identity_audit(
    conn: psycopg.Connection,
    split_id: int,
    partition: str = "test",
    cutoff: float = 0.9,
) -> list[HighIdentityHit]:
    """Every held-out target whose best training hit exceeds `cutoff` identity.

    Reported with alignment length and coverage on both proteins, because
    identity alone does not say whether the two sequences are the same protein
    or merely share a domain. Clustering used `--min-seq-id 0.4 -c 0.8`, so a
    pair that aligns at 95% identity over 15% of either sequence is *correctly*
    placed in different clusters and is not evidence that the split leaks.
    """
    if shutil.which("mmseqs") is None:
        return []
    held = _partition_targets(conn, split_id, partition)
    train = _partition_targets(conn, split_id, "train")
    if not held or not train:
        return []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, seqs in (("query", held), ("db", train)):
            with (root / f"{name}.fasta").open("w", encoding="ascii") as fh:
                for tid, seq in seqs.items():
                    fh.write(f">{tid}\n{seq}\n")
        subprocess.run(  # noqa: S603
            [
                "mmseqs",
                "easy-search",
                str(root / "query.fasta"),
                str(root / "db.fasta"),
                str(root / "hits.tsv"),
                str(root / "tmp"),
                "--format-output",
                "query,target,fident,alnlen,qlen,tlen,qcov,tcov",
                "-s",
                "5.7",
                "--max-seqs",
                "50",
                "-v",
                "1",
            ],
            check=True,
            capture_output=True,
            timeout=3600,
        )
        best: dict[int, HighIdentityHit] = {}
        with (root / "hits.tsv").open(encoding="ascii") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 8:
                    continue
                hit = HighIdentityHit(
                    query=int(parts[0]),
                    target=int(parts[1]),
                    identity=float(parts[2]),
                    aln_len=int(parts[3]),
                    query_len=int(parts[4]),
                    target_len=int(parts[5]),
                    query_cov=float(parts[6]),
                    target_cov=float(parts[7]),
                )
                if hit.identity < cutoff:
                    continue
                current = best.get(hit.query)
                # Rank by how much of both proteins the alignment explains: the
                # worst case for the split is high identity over most of both.
                if current is None or hit.mutual_coverage > current.mutual_coverage:
                    best[hit.query] = hit
    return sorted(best.values(), key=lambda h: -h.mutual_coverage)


#: A held-out target counts as a near homolog of training when a high-identity
#: alignment explains most of *both* proteins. Identity alone is not enough: the
#: audit found 43 of 44 high-identity hits were fragment containment, where one
#: sequence is a domain construct of the other and the pair is correctly split.
NEAR_HOMOLOG_IDENTITY = 0.90
NEAR_HOMOLOG_COVERAGE = 0.50
NEAR_HOMOLOG_STRATUM = "near_homolog"


def record_near_homolog_stratum(
    conn: psycopg.Connection,
    split_id: int,
    partition: str = "test",
    identity: float = NEAR_HOMOLOG_IDENTITY,
    coverage: float = NEAR_HOMOLOG_COVERAGE,
) -> int:
    """Persist the near-homolog targets as an evaluation stratum.

    `cold_protein` guarantees cluster disjointness, which is not the same as
    novelty. These targets satisfy the guarantee and still have a near-identical
    training counterpart, so a score on them measures near-homolog transfer
    rather than generalisation to an unfamiliar protein. Recorded as a stratum
    so that distinction can be reported instead of averaged away.
    """
    import json

    hits = [
        h
        for h in high_identity_audit(conn, split_id, partition=partition, cutoff=identity)
        if h.mutual_coverage >= coverage
    ]
    conn.execute(
        "DELETE FROM split_target_stratum WHERE split_id=%s AND stratum=%s",
        (split_id, NEAR_HOMOLOG_STRATUM),
    )
    if not hits:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO split_target_stratum (split_id, target_id, stratum, detail) "
            "VALUES (%s,%s,%s,%s)",
            [
                (
                    split_id,
                    h.query,
                    NEAR_HOMOLOG_STRATUM,
                    json.dumps(
                        {
                            "nearest_train_target": h.target,
                            "identity": round(h.identity, 4),
                            "aln_len": h.aln_len,
                            "query_len": h.query_len,
                            "target_len": h.target_len,
                            "query_cov": round(h.query_cov, 4),
                            "target_cov": round(h.target_cov, 4),
                            "mutual_coverage": round(h.mutual_coverage, 4),
                        }
                    ),
                )
                for h in hits
            ],
        )
    return len(hits)
