"""Receptor preparation, with every discarded atom accounted for.

Cleaning a structure is a series of judgement calls -- which cofactor is
catalytic, which heteroatom is crystallographic debris, which alternate
conformation to believe. Each one changes the pocket the ligand sees, so each is
recorded rather than applied silently.
"""

from __future__ import annotations

import math
import subprocess  # noqa: S404 - runs meeko, a declared dependency
import sys
from dataclasses import dataclass, field
from pathlib import Path


class PreparationError(RuntimeError):
    """The receptor could not be prepared. No PDBQT was written."""


@dataclass
class CleanReport:
    """What was kept, what was dropped, and why."""

    atoms_kept: int
    kept_heteroatoms: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    altloc_kept: str = "A"
    altloc_atoms_dropped: int = 0


def residue_centroid(
    pdb_path: Path, resname: str, resi: str | None = None
) -> tuple[float, float, float]:
    """Centre of mass (unweighted) of one heteroresidue -- the box anchor."""
    pts = [
        (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        for line in pdb_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("HETATM")
        and line[17:20].strip() == resname
        and (resi is None or line[22:26].strip() == resi)
    ]
    if not pts:
        raise PreparationError(f"{pdb_path} has no heteroresidue {resname!r} (resi={resi!r})")
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


def distance_to(pdb_path: Path, resname: str, point: tuple[float, float, float]) -> float:
    """Closest approach of any atom of `resname` to `point`. Used to justify removals."""
    best = math.inf
    for line in pdb_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("HETATM") and line[17:20].strip() == resname:
            p = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            best = min(best, math.dist(p, point))
    return best


def clean_structure(
    pdb_path: Path,
    out_path: Path,
    keep_heteroatoms: frozenset[str] = frozenset({"ZN"}),
    altloc: str = "A",
) -> CleanReport:
    """Write a receptor PDB holding the polymer plus the declared cofactors only.

    Alternate conformations are resolved by keeping one altloc rather than
    letting both into the same model, which would place two atoms in one site.
    """
    report = CleanReport(atoms_kept=0)
    out: list[str] = []
    for line in pdb_path.read_text(encoding="utf-8").splitlines():
        rec = line[:6].strip()
        resn = line[17:20].strip() if len(line) > 20 else ""
        if rec == "ATOM" or (rec == "HETATM" and resn in keep_heteroatoms):
            flag = line[16]
            if flag.strip() and flag != altloc:
                report.altloc_atoms_dropped += 1
                continue
            if flag == altloc:
                line = line[:16] + " " + line[17:]
            out.append(line)
            report.atoms_kept += 1
            if rec == "HETATM":
                report.kept_heteroatoms[resn] = report.kept_heteroatoms.get(resn, 0) + 1
        elif rec == "HETATM":
            report.dropped[resn] = report.dropped.get(resn, 0) + 1
        elif rec in ("TER", "END"):
            out.append(line)
    if report.atoms_kept == 0:
        raise PreparationError(f"{pdb_path} yielded no receptor atoms")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    report.altloc_kept = altloc
    return report


def prepare_receptor(
    clean_pdb: Path,
    out_basename: Path,
    box_center: tuple[float, float, float],
    box_size: tuple[float, float, float],
) -> Path:
    """Clean PDB -> PDBQT via meeko. Raises with meeko's own message on failure."""
    script = Path(sys.executable).parent / "mk_prepare_receptor.py"
    if not script.exists():
        raise PreparationError(f"meeko receptor script not found at {script}")
    out_basename.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        "--read_pdb",
        str(clean_pdb),
        "-o",
        str(out_basename),
        "-p",
        "-v",
        "--box_center",
        *(f"{v}" for v in box_center),
        "--box_size",
        *(f"{v}" for v in box_size),
    ]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # noqa: S603
    pdbqt = out_basename.with_suffix(".pdbqt")
    if done.returncode != 0 or not pdbqt.exists():
        raise PreparationError(
            f"meeko receptor preparation failed (exit {done.returncode}):\n"
            f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        )
    return pdbqt


def receptor_has_atom_type(pdbqt: Path, atom_type: str) -> bool:
    """Did a declared cofactor survive into the PDBQT?

    Meeko silently drops atoms it cannot type. For a metalloenzyme that is the
    difference between docking into the real site and docking into a hole.
    """
    for line in pdbqt.read_text(encoding="utf-8").splitlines():
        if line.startswith(("ATOM", "HETATM")) and line.split()[-1] == atom_type:
            return True
    return False
