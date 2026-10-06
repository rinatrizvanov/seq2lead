"""Removing curated entities that nothing references.

Curation inserts into `compound` and `target` with no foreign key back to the
release that caused it. That is deliberate -- a structure is the same structure
whichever release first carried it -- but it means a test fixture that ingests a
synthetic release leaves its compounds and targets behind forever, and no
provenance query can find them afterwards.

Deleting by structure is not an option either: the test SMILES are real
BindingDB compounds. Benzene, aspirin and nicotine all appear in the corpus with
real measurements, so a cleanup keyed on structure would destroy production
rows.

A high-water mark on the id sequence is the one safe discriminator, and the
activity check is the safety net: anything still carrying a measurement is left
alone no matter where it sits in the sequence.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ORPHAN_COMPOUND = (
    "SELECT id FROM compound WHERE id > %s AND NOT EXISTS "
    "(SELECT 1 FROM activity a WHERE a.compound_id = compound.id)"
)
_ORPHAN_TARGET = (
    "SELECT id FROM target WHERE id > %s AND NOT EXISTS "
    "(SELECT 1 FROM activity a WHERE a.target_id = target.id)"
)


@dataclass(frozen=True)
class EntityWatermark:
    """The id ceiling below which entities are pre-existing."""

    compound: int
    target: int


def watermark() -> EntityWatermark:
    from seq2lead.db import connect

    with connect() as conn:
        compound = conn.execute("SELECT coalesce(max(id), 0) FROM compound").fetchone()[0]
        target = conn.execute("SELECT coalesce(max(id), 0) FROM target").fetchone()[0]
    return EntityWatermark(compound=int(compound), target=int(target))


def drop_entities_above(mark: EntityWatermark) -> tuple[int, int]:
    """Delete activity-free compounds and targets created after `mark`."""
    from seq2lead.db import transaction

    with transaction() as conn:
        conn.execute(
            f"DELETE FROM compound_source WHERE compound_id IN ({_ORPHAN_COMPOUND})",
            (mark.compound,),
        )
        for table in ("target_alias", "target_cluster"):
            conn.execute(
                f"DELETE FROM {table} WHERE target_id IN ({_ORPHAN_TARGET})", (mark.target,)
            )
        compounds = conn.execute(
            f"DELETE FROM compound WHERE id IN ({_ORPHAN_COMPOUND})", (mark.compound,)
        ).rowcount
        targets = conn.execute(
            f"DELETE FROM target WHERE id IN ({_ORPHAN_TARGET})", (mark.target,)
        ).rowcount
    return int(compounds), int(targets)


@contextmanager
def no_entity_leak() -> Iterator[EntityWatermark]:
    """Take a watermark, run the block, then delete whatever it created.

    Wrap any fixture that curates a synthetic release. Without it the synthetic
    compounds and targets survive into the production tables and are picked up
    by anything that enumerates entities -- which is how two test molecules and
    two test proteins reached the M7 feature caches.
    """
    mark = watermark()
    try:
        yield mark
    finally:
        drop_entities_above(mark)
