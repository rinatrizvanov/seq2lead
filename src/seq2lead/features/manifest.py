"""What population a cache was built over, digested so it cannot be mistaken.

`FeatureSpec` says *how* a vector is computed. It says nothing about *which
entities* were fed in, and that omission is exploitable: a `--limit 1000` smoke
build and a full 1.4M-compound build produce the same spec hash, so the partial
one can be registered under the full one's identity and read back by anything
that asks for it.

An `InputManifest` closes that. It digests the entity ids *and their content*,
so a cache is invalidated not only when the population changes but when a
structure or sequence underneath it is re-standardised. For activity-derived
features it also digests the supporting evidence, because the same split can
yield different aggregates if the training set is rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

#: Populations a cache may declare. `release:<id>` is the benchmark population:
#: entities the pinned release actually measured. `all_rows` is every row
#: currently in the table, which is what M7 v1 used and is not reproducible --
#: any stray row from a test fixture silently joins it.
POPULATION_RELEASE = "release"
POPULATION_ALL = "all_rows"

FULL = "full"
PARTIAL = "partial"


class ManifestError(ValueError):
    """A population that cannot be resolved, or a cache that does not match one."""


@dataclass(frozen=True)
class InputManifest:
    """The exact inputs a cache was built from."""

    entity: str  # compound | target
    population: str  # e.g. "release:117"
    n_entities: int
    entity_digest: str  # sha256 over (id, content hash) pairs, id-sorted
    completeness: str  # full | partial
    limit: int | None = None
    evidence_digest: str | None = None  # activity features only
    split: str | None = None

    def canonical(self) -> str:
        return json.dumps(
            {
                "entity": self.entity,
                "population": self.population,
                "n_entities": self.n_entities,
                "entity_digest": self.entity_digest,
                "completeness": self.completeness,
                "limit": self.limit,
                "evidence_digest": self.evidence_digest,
                "split": self.split,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    @property
    def is_full(self) -> bool:
        return self.completeness == FULL


def _digest_pairs(rows: list[tuple[int, str]]) -> str:
    """sha256 over id:content lines, id-ordered.

    Streamed rather than accumulated: the compound population is 1.4M rows and
    building one string of it wastes hundreds of megabytes for no benefit.
    """
    digest = hashlib.sha256()
    for entity_id, content in rows:
        digest.update(f"{int(entity_id)}:{content}\n".encode())
    return digest.hexdigest()


def _population_sql(entity: str, population: str) -> tuple[str, tuple]:
    kind, _, value = population.partition(":")
    if kind == POPULATION_RELEASE:
        if not value.isdigit():
            raise ManifestError(f"population {population!r} needs a numeric release id")
        release = int(value)
        if entity == "compound":
            return (
                "SELECT c.id, encode(digest(c.canonical_smiles, 'sha256'), 'hex') "
                "FROM compound c WHERE EXISTS (SELECT 1 FROM activity a "
                "  WHERE a.compound_id = c.id AND a.source_release_id = %s) ORDER BY c.id",
                (release,),
            )
        return (
            "SELECT t.id, t.sequence_sha256 FROM target t "
            "WHERE EXISTS (SELECT 1 FROM activity a "
            "  WHERE a.target_id = t.id AND a.source_release_id = %s) ORDER BY t.id",
            (release,),
        )
    if kind == POPULATION_ALL:
        if entity == "compound":
            return (
                "SELECT id, encode(digest(canonical_smiles, 'sha256'), 'hex') "
                "FROM compound ORDER BY id",
                (),
            )
        return ("SELECT id, sequence_sha256 FROM target ORDER BY id", ())
    raise ManifestError(
        f"unknown population {population!r}. Use 'release:<id>' for the benchmark "
        "population, or 'all_rows' to take every row currently in the table."
    )


def build_manifest(
    conn: psycopg.Connection,
    entity: str,
    population: str,
    limit: int | None = None,
    split: str | None = None,
    evidence_digest: str | None = None,
) -> tuple[InputManifest, list[int]]:
    """Resolve a population to its exact members and digest them."""
    if entity not in {"compound", "target"}:
        raise ManifestError(f"unknown entity {entity!r}")
    sql, params = _population_sql(entity, population)
    rows = conn.execute(sql, params).fetchall()
    full_size = len(rows)
    if limit:
        rows = rows[:limit]
    pairs = [(int(r[0]), str(r[1])) for r in rows]
    return (
        InputManifest(
            entity=entity,
            population=population,
            n_entities=len(pairs),
            entity_digest=_digest_pairs(pairs),
            completeness=PARTIAL if limit and limit < full_size else FULL,
            limit=limit,
            evidence_digest=evidence_digest,
            split=split,
        ),
        [entity_id for entity_id, _ in pairs],
    )


def evidence_digest_for_split(conn: psycopg.Connection, split_id: int) -> str:
    """Digest the training activities an aggregate is allowed to read.

    Two caches built from the same split but a rebuilt training set are
    different objects, and this is what makes that visible.
    """
    from seq2lead.splits.assertions import training_visible_activities

    visible = training_visible_activities(conn, split_id)
    row = conn.execute(
        f"SELECT count(*), coalesce(md5(string_agg(v.activity_id::text, ',' "  # noqa: S608
        f"ORDER BY v.activity_id)), '') FROM ({visible}) v"
    ).fetchone()
    return f"n={int(row[0])}:md5={row[1]}"


def library_versions(kind: str) -> dict[str, str]:
    """Versions that could plausibly change a number, recorded with the cache."""
    import numpy

    versions = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "numpy": numpy.__version__,
    }
    if kind == "ecfp4":
        import rdkit

        versions["rdkit"] = rdkit.__version__
    elif kind == "esm2":
        import torch
        import transformers

        versions["torch"] = torch.__version__
        versions["transformers"] = transformers.__version__
    return versions


def manifest_from_ids(
    conn: psycopg.Connection,
    entity: str,
    population: str,
    entity_ids: list[int],
    split: str | None = None,
    evidence_digest: str | None = None,
) -> InputManifest:
    """A manifest over an explicitly derived population.

    Activity features do not cover a whole release: they cover the entities that
    have training-visible evidence, which is a function of the split. The set is
    computed by the caller, and digested here with the same content hashes a
    release-scoped manifest would use, so the two are comparable.
    """
    if not entity_ids:
        return InputManifest(
            entity=entity,
            population=population,
            n_entities=0,
            entity_digest=hashlib.sha256(b"").hexdigest(),
            completeness=FULL,
            split=split,
            evidence_digest=evidence_digest,
        )
    column = "canonical_smiles" if entity == "compound" else "sequence_sha256"
    table = "compound" if entity == "compound" else "target"
    expression = f"encode(digest({column}, 'sha256'), 'hex')" if entity == "compound" else column
    rows = conn.execute(
        f"SELECT id, {expression} FROM {table} WHERE id = ANY(%s) ORDER BY id",  # noqa: S608
        (list(entity_ids),),
    ).fetchall()
    return InputManifest(
        entity=entity,
        population=population,
        n_entities=len(rows),
        entity_digest=_digest_pairs([(int(r[0]), str(r[1])) for r in rows]),
        completeness=FULL,
        split=split,
        evidence_digest=evidence_digest,
    )


def content_digest_for_ids(conn: psycopg.Connection, entity: str, ids: list[int]) -> str:
    """Recompute the entity digest for an arbitrary id list, id-ordered.

    Used at registration to verify a built cache covers exactly the population
    its manifest claims. It re-reads the content hashes from the database rather
    than trusting anything the builder passed along, so a cache whose rows drifted
    from its declared inputs is caught before it is ever stored.
    """
    if not ids:
        return hashlib.sha256(b"").hexdigest()
    table = "compound" if entity == "compound" else "target"
    expression = (
        "encode(digest(canonical_smiles, 'sha256'), 'hex')"
        if entity == "compound"
        else "sequence_sha256"
    )
    rows = conn.execute(
        f"SELECT id, {expression} FROM {table} WHERE id = ANY(%s) ORDER BY id",  # noqa: S608
        (sorted(int(i) for i in ids),),
    ).fetchall()
    return _digest_pairs([(int(r[0]), str(r[1])) for r in rows])
