"""M10: a docking validation gate.

One question, asked of a fixed structure, pocket and protocol:

    does it rank measured actives above measured inactives for this target?

A passing gate licenses *this protocol on this cohort*. It is not evidence that
docking improves the M9 ranking -- that is a different experiment with a
different control, and it is not attempted here.
"""

from seq2lead.dock.artifacts import ArtifactCollision, DockRunPaths, preflight, run_paths
from seq2lead.dock.config import DockingConfig, load_docking_config

__all__ = [
    "ArtifactCollision",
    "DockRunPaths",
    "DockingConfig",
    "load_docking_config",
    "preflight",
    "run_paths",
]
