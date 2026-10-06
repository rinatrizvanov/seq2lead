"""Frozen, versioned candidate libraries.

A ranking is only reproducible if the pool it ranked is. The member list is
digested and the selection rule is stored beside it, so nobody has to infer from
the members what the rule was -- and so a library cannot be quietly reshaped to
flatter a model.

The rule for the first runnable library is deliberately blind to affinity:
compounds with at least one eligible exact Ki measurement in the pinned release,
ordered by surrogate key, capped. Ordering by `compound.id` is arbitrary with
respect to anything a model predicts, which is the point.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

DEFAULT_NAME = "curated-ki-25k-v1"
DEFAULT_CAP = 25_000
SELECTION_RULE = (
    "compounds with at least one eligible exact-relation (=) Ki measurement in the "
    "pinned source release, ordered by compound.id ascending, capped at {cap}. The "
    "ordering is a stable surrogate key and is arbitrary with respect to predicted "
    "affinity; compounds are not selected by score, by similarity to any query, or "
    "by any property of a model."
)


@dataclass
class Library:
    id: int
    name: str
    selection_rule: str
    n_members: int
    member_sha256: str
    cap: int | None


def member_digest(compound_ids: list[int]) -> str:
    digest = hashlib.sha256()
    for compound_id in compound_ids:
        digest.update(f"{int(compound_id)}\n".encode())
    return digest.hexdigest()


def select_members(conn: psycopg.Connection, release_id: int, cap: int = DEFAULT_CAP) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT a.compound_id
        FROM activity a
        WHERE a.source_release_id = %s
          AND a.measurement_type = 'KI'
          AND a.relation = '='
          AND a.value_numeric > 0
        ORDER BY a.compound_id
        LIMIT %s
        """,
        (release_id, cap),
    ).fetchall()
    return [int(r[0]) for r in rows]


def build(
    conn: psycopg.Connection,
    release_id: int,
    name: str = DEFAULT_NAME,
    cap: int = DEFAULT_CAP,
    notes: str = "",
) -> Library:
    """Freeze a library. Rebuilding with different members is refused."""
    members = select_members(conn, release_id, cap)
    digest = member_digest(members)
    rule = SELECTION_RULE.format(cap=cap)

    existing = conn.execute(
        "SELECT id, member_sha256, n_members FROM candidate_library WHERE name=%s", (name,)
    ).fetchone()
    if existing is not None:
        if str(existing[1]) != digest:
            raise ValueError(
                f"library {name!r} already exists with a different member set "
                f"({existing[2]:,} members, digest {existing[1][:12]}…). A published "
                "library is frozen; build a new version rather than redefining one."
            )
        return Library(int(existing[0]), name, rule, int(existing[2]), digest, cap)

    library_id = int(
        conn.execute(
            "INSERT INTO candidate_library (name, selection_rule, source_release_id, "
            "n_members, member_sha256, cap, notes) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (name, rule, release_id, len(members), digest, cap, notes),
        ).fetchone()[0]
    )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO candidate_library_member (library_id, compound_id) VALUES (%s,%s)",
            [(library_id, c) for c in members],
        )
    return Library(library_id, name, rule, len(members), digest, cap)


def load(conn: psycopg.Connection, name: str) -> tuple[Library, list[int]]:
    row = conn.execute(
        "SELECT id, selection_rule, n_members, member_sha256, cap FROM candidate_library "
        "WHERE name=%s",
        (name,),
    ).fetchone()
    if row is None:
        known = [str(r[0]) for r in conn.execute("SELECT name FROM candidate_library").fetchall()]
        raise LookupError(f"no candidate library named {name!r}. Known: {known or 'none'}")
    library_id = int(row[0])
    members = [
        int(r[0])
        for r in conn.execute(
            "SELECT compound_id FROM candidate_library_member WHERE library_id=%s "
            "ORDER BY compound_id",
            (library_id,),
        ).fetchall()
    ]
    digest = member_digest(members)
    if digest != str(row[3]):
        raise ValueError(
            f"library {name!r} members do not match its recorded digest; it has been "
            "altered since it was frozen"
        )
    return Library(library_id, name, str(row[1]), int(row[2]), digest, row[4]), members
