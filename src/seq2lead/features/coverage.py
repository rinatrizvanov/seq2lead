"""Does every entity a split scores actually have a feature vector?

Cache identity now guarantees a cache matches the population it *claims*. It
says nothing about whether that population covers the entities a split will
score. Those are different questions, and the gap between them is where a model
run fails at hour three: a cache scoped to `release:117` and a split built on a
different endpoint can each be internally consistent and still not line up.

A missing vector and an unusable one are also distinct and reported separately.
A compound with no row at all would raise a KeyError at lookup. A compound with
an all-zero row because its structure would not parse looks like a molecule with
no features, which is worse -- nothing raises, and the model trains on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

#: Entity -> the cache kind expected to cover it.
ENTITY_CACHE = {"compound": "ecfp4", "target": "esm2"}


class CoverageError(RuntimeError):
    """A split scores entities that the active feature caches do not cover."""


@dataclass
class CoverageCheck:
    split: str
    entity: str
    cache: str | None
    scored: int
    covered: int
    missing: int
    flagged: int
    missing_examples: list[int] = field(default_factory=list)
    flagged_examples: list[int] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.cache is not None and self.missing == 0

    @property
    def usable(self) -> int:
        return self.covered - self.flagged


def _current_cache(conn: psycopg.Connection, kind: str) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT id, name FROM feature_version WHERE kind=%s AND superseded_by IS NULL "
        "AND completeness='full' ORDER BY id DESC LIMIT 1",
        (kind,),
    ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def check_split_coverage(conn: psycopg.Connection, split_id: int) -> list[CoverageCheck]:
    """Coverage of one split's scored entities by the current caches."""
    split_name = str(
        conn.execute("SELECT name FROM split_version WHERE id=%s", (split_id,)).fetchone()[0]
    )
    checks: list[CoverageCheck] = []
    for entity, kind in ENTITY_CACHE.items():
        column = f"{entity}_id"
        current = _current_cache(conn, kind)
        scored = conn.execute(
            f"SELECT count(DISTINCT {column}) FROM split_pair_assignment "  # noqa: S608
            "WHERE split_id=%s AND partition IN ('train','validation','test')",
            (split_id,),
        ).fetchone()[0]
        if current is None:
            checks.append(CoverageCheck(split_name, entity, None, int(scored), 0, int(scored), 0))
            continue
        feature_id, cache_name = current
        ids = _cached_ids(conn, feature_id)
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM split_pair_assignment "  # noqa: S608
            "WHERE split_id=%s AND partition IN ('train','validation','test')",
            (split_id,),
        ).fetchall()
        scored_ids = {int(r[0]) for r in rows}
        missing = sorted(scored_ids - ids)
        flagged = {
            int(r[0])
            for r in conn.execute(
                "SELECT entity_id FROM feature_entity_flag WHERE feature_id=%s "
                "AND flag IN ('unparseable_smiles')",
                (feature_id,),
            ).fetchall()
        }
        flagged_here = sorted(scored_ids & flagged)
        checks.append(
            CoverageCheck(
                split=split_name,
                entity=entity,
                cache=cache_name,
                scored=len(scored_ids),
                covered=len(scored_ids) - len(missing),
                missing=len(missing),
                flagged=len(flagged_here),
                missing_examples=missing[:5],
                flagged_examples=flagged_here[:5],
            )
        )
    return checks


def _cached_ids(conn: psycopg.Connection, feature_id: int) -> set[int]:
    """The entity ids a built cache actually holds, read from its own file."""
    import numpy as np

    from seq2lead.features.store import FeatureCacheError, sha256_file

    row = conn.execute(
        "SELECT name, storage_path, storage_sha256 FROM feature_version WHERE id=%s",
        (feature_id,),
    ).fetchone()
    from pathlib import Path

    path = Path(str(row[1]))
    if not path.exists() or sha256_file(path) != str(row[2]):
        raise FeatureCacheError(
            f"cannot check coverage against {row[0]!r}: its bytes are missing or no "
            "longer match the recorded digest"
        )
    with np.load(path) as data:
        return {int(i) for i in data["ids"]}


def check_all_splits(conn: psycopg.Connection) -> list[CoverageCheck]:
    """Coverage for every split that is not superseded."""
    checks: list[CoverageCheck] = []
    for (split_id,) in conn.execute(
        "SELECT id FROM split_version WHERE superseded_by IS NULL ORDER BY id"
    ).fetchall():
        checks.extend(check_split_coverage(conn, int(split_id)))
    return checks


def assert_feature_coverage(conn: psycopg.Connection) -> list[CoverageCheck]:
    """Raise if any active split scores an entity no current cache covers."""
    checks = check_all_splits(conn)
    broken = [c for c in checks if not c.passed]
    if broken:
        lines = []
        for check in broken:
            if check.cache is None:
                lines.append(
                    f"  {check.split} / {check.entity}: no complete "
                    f"{ENTITY_CACHE[check.entity]} cache is registered"
                )
            else:
                lines.append(
                    f"  {check.split} / {check.entity}: {check.missing:,} of "
                    f"{check.scored:,} scored entities absent from {check.cache} "
                    f"(e.g. {check.missing_examples})"
                )
        raise CoverageError("feature caches do not cover every scored entity:\n" + "\n".join(lines))
    return checks
