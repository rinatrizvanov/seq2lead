"""M11 as-of machinery: cross-snapshot matching, with nothing fitted.

The design is in `docs/M11.md`. Two things this package is careful about:

* a measurement's **slot** (what it is about) is kept separate from its **value**
  (what was reported), because a correction changes the value and a key that
  contained the value could never represent that;
* the **training population is a function of the earlier snapshot alone**. No
  classification here feeds back into it, and `training_population` takes only
  snapshot A so that the restriction is structural rather than remembered.
"""

from seq2lead.asof.matching import (
    Classification,
    DiffReport,
    Observation,
    Slot,
    SourceRef,
    Value,
    diff_snapshots,
    normalise_value,
    slot_of,
    training_population,
)

__all__ = [
    "Classification",
    "DiffReport",
    "Observation",
    "Slot",
    "SourceRef",
    "Value",
    "diff_snapshots",
    "normalise_value",
    "slot_of",
    "training_population",
]
