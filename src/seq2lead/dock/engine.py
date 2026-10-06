"""Running AutoDock Vina, with its identity pinned and its failures recorded.

Two things make a docking score meaningless if they drift: the engine binary and
the box. Both are checked against the frozen contract before the first ligand,
and the binary's digest is carried into the run record.
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess  # noqa: S404 - runs vina, pinned by digest
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from seq2lead.dock.artifacts import sha256_file

if TYPE_CHECKING:
    from seq2lead.dock.config import DockingConfig

VINA_BIN = Path("tools/vina/vina_1.2.7_mac_aarch64")

#: Vina prints a mode table; the first data row is the best pose. Matching the
#: table rather than parsing free text keeps a changed banner from silently
#: becoming a score of 0.0.
_MODE_ROW = re.compile(r"^\s*1\s+(-?\d+\.\d+)\s")


class EngineError(RuntimeError):
    """The docking engine is not the one the contract names."""


@dataclass(frozen=True)
class DockResult:
    compound_id: int
    affinity: float  # kcal/mol, Vina's convention: more negative is better
    pose_path: Path
    seconds: float

    @property
    def ranking_score(self) -> float:
        """Higher means predicted to bind more tightly.

        Vina reports a free energy, so the ranking score is its negation. This
        property is the only place the sign is decided, and the gate reads it
        rather than the raw affinity.
        """
        return -self.affinity


@dataclass(frozen=True)
class DockFailure:
    compound_id: int
    stage: str
    reason: str


def verify_engine(config: DockingConfig, binary: Path = VINA_BIN) -> str:
    """Refuse an engine the contract does not name. Returns the verified version."""
    if not binary.exists():
        raise EngineError(
            f"no docking engine at {binary}. The contract names AutoDock Vina "
            f"{config.engine_version} ({config.engine_sha256[:16]}…)."
        )
    digest = sha256_file(binary)
    if digest != config.engine_sha256:
        raise EngineError(
            f"{binary} has digest {digest[:16]}… but the contract names "
            f"{config.engine_sha256[:16]}…. Refusing to dock with an engine the "
            "contract does not identify."
        )
    done = subprocess.run(  # noqa: S603
        [str(binary), "--version"], capture_output=True, text=True, timeout=120
    )
    reported = done.stdout.strip()
    if config.engine_version not in reported:
        raise EngineError(
            f"{binary} reports {reported!r}, which does not contain the contract's "
            f"version {config.engine_version!r}"
        )
    return reported


def dock_one(
    compound_id: int,
    ligand: Path,
    receptor: Path,
    out_pose: Path,
    config: DockingConfig,
    binary: Path = VINA_BIN,
    box_size: tuple[float, float, float] | None = None,
) -> DockResult | DockFailure:
    """One ligand into the frozen box. `box_size` overrides only for sensitivity runs."""
    cx, cy, cz = config.box_center
    sx, sy, sz = box_size or config.box_size
    out_pose.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "--receptor",
        str(receptor),
        "--ligand",
        str(ligand),
        "--center_x",
        str(cx),
        "--center_y",
        str(cy),
        "--center_z",
        str(cz),
        "--size_x",
        str(sx),
        "--size_y",
        str(sy),
        "--size_z",
        str(sz),
        "--exhaustiveness",
        str(config.exhaustiveness),
        "--num_modes",
        str(config.num_modes),
        "--seed",
        str(config.seed),
        "--cpu",
        "1",
        "--out",
        str(out_pose),
    ]
    started = time.monotonic()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)  # noqa: S603
    except subprocess.TimeoutExpired:
        return DockFailure(compound_id, "docking", "vina exceeded the 3600s timeout")
    elapsed = time.monotonic() - started

    if done.returncode != 0:
        return DockFailure(
            compound_id, "docking", f"vina exited {done.returncode}: {done.stderr.strip()[:300]}"
        )
    affinity = None
    for line in done.stdout.splitlines():
        m = _MODE_ROW.match(line)
        if m:
            affinity = float(m.group(1))
            break
    if affinity is None:
        return DockFailure(compound_id, "engine_output", "no mode table in vina output")
    if not out_pose.exists():
        return DockFailure(compound_id, "docking", "vina reported a score but wrote no pose")
    return DockResult(compound_id, affinity, out_pose, elapsed)


def dock_many(
    jobs: list[tuple[int, Path, Path]],
    receptor: Path,
    config: DockingConfig,
    binary: Path = VINA_BIN,
    workers: int = 8,
    box_size: tuple[float, float, float] | None = None,
    progress: bool = False,
) -> tuple[list[DockResult], list[DockFailure]]:
    """Dock a cohort. Each vina gets one CPU; parallelism is across ligands."""
    results: list[DockResult] = []
    failures: list[DockFailure] = []
    done_n = 0
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(dock_one, cid, lig, receptor, pose, config, binary, box_size): cid
            for cid, lig, pose in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            out = fut.result()
            (results if isinstance(out, DockResult) else failures).append(out)
            done_n += 1
            if progress and done_n % 25 == 0:
                rate = done_n / (time.monotonic() - started)
                left = (len(jobs) - done_n) / rate if rate else 0
                print(
                    f"    {done_n:,}/{len(jobs):,} docked  {rate:.2f}/s  ~{left / 60:.1f} min left",
                    flush=True,
                )
    results.sort(key=lambda r: r.compound_id)
    failures.sort(key=lambda f: f.compound_id)
    return results, failures
