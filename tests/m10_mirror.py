"""A temporary mirror of the M10 docking evidence, for tampering tests.

Several tests have to prove that a *changed* artifact is refused. They used to do
that by editing the real tracked file and restoring it in a `finally` block,
which has two problems: an exception or an interrupt between the edit and the
restore leaves a scientific artifact modified on disk, and a concurrent `git add`
can stage the tampered bytes. One nearly did.

This builds a throwaway copy instead, with **every path the verifiers consume**
redirected into it: the contract, the verification contract, the manifest, each
run's `*_path` files and `*_dir` trees, and each published result. Because the
bytes are copied verbatim, the digests the manifest records still match, so a
clean mirror verifies clean and a tampered mirror fails for the real reason. No
digest check is relaxed and nothing is stubbed.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: Keys inside a run entry that name a single file.
RUN_FILE_KEYS = ("cohort_path", "failures_path", "receptor_pdbqt", "record_path", "scores_path")
#: Keys inside a run entry that name a directory of files.
RUN_TREE_KEYS = ("ligand_dir", "pose_dir")

REAL_MANIFEST = Path("configs/manifests/m10_docking.json")
REAL_CONTRACT = Path("configs/experiments/m10-docking-v1.yaml")
REAL_VERIFICATION = Path("configs/manifests/m10_verification.json")
REAL_QA_POSE = Path("data/m10/runs/qa-redock/SUA_redock.pdbqt")
REAL_STRUCTURE = Path("data/m10/structures/3k34.pdb")


@dataclass
class Mirror:
    """A self-contained copy. Every attribute points inside `root`."""

    root: Path
    manifest: Path
    contract: Path
    verification: Path | None
    qa_pose: Path | None
    structure: Path | None
    copied: list[Path] = field(default_factory=list)

    def published(self, kind: str, index: int = 0) -> Path:
        entry = json.loads(self.manifest.read_text(encoding="utf-8"))["published"][kind][index]
        return Path(entry["path"])

    def run_value(self, key: str, index: int = 0) -> Path:
        return Path(json.loads(self.manifest.read_text(encoding="utf-8"))["runs"][index][key])

    def qa(self) -> Path:
        return self.published("qa")


def _mirror_file(real: Path, root: Path) -> Path | None:
    """Copy `real` to the same relative location under `root`, bytes verbatim."""
    if not real.exists():
        return None
    dest = root / real
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real, dest)
    return dest


def _mirror_tree(real: Path, root: Path) -> Path | None:
    if not real.is_dir():
        return None
    dest = root / real
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(real, dest, dirs_exist_ok=True)
    return dest


def build(root: Path, *, trees: bool = False) -> Mirror:
    """Mirror the M10 evidence under `root` and rewrite the manifest's paths.

    `trees` also copies the ligand and pose directories, which the
    directory-integrity tests need and the others do not. Those two trees are
    ~1,160 files, so they are copied only on request.
    """
    root.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []

    contract = _mirror_file(REAL_CONTRACT, root)
    verification = _mirror_file(REAL_VERIFICATION, root)
    qa_pose = _mirror_file(REAL_QA_POSE, root)
    structure = _mirror_file(REAL_STRUCTURE, root)
    copied += [p for p in (contract, verification, qa_pose, structure) if p]

    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    if contract is not None:
        manifest["config_path"] = str(contract)

    for entries in (manifest.get("published") or {}).values():
        for entry in entries:
            dest = _mirror_file(Path(entry["path"]), root)
            if dest is not None:
                entry["path"] = str(dest)
                copied.append(dest)

    for run in manifest.get("runs") or []:
        for key in RUN_FILE_KEYS:
            if key in run:
                dest = _mirror_file(Path(run[key]), root)
                if dest is not None:
                    run[key] = str(dest)
                    copied.append(dest)
        for key in RUN_TREE_KEYS:
            if key not in run:
                continue
            if trees:
                dest = _mirror_tree(Path(run[key]), root)
                if dest is not None:
                    run[key] = str(dest)
                    copied.append(dest)
            else:
                # Point at a path inside the mirror that does not exist, so the
                # verifier reports it unavailable rather than silently reading
                # the real tree. Tests that do not need the trees pass
                # allow_missing_trees=True.
                run[key] = str(root / run[key])

    path = root / "m10_docking.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    copied.append(path)
    return Mirror(
        root=root,
        manifest=path,
        contract=contract or REAL_CONTRACT,
        verification=verification,
        qa_pose=qa_pose,
        structure=structure,
        copied=copied,
    )


def real_digests() -> dict[str, str]:
    """SHA-256 of every real M10 artifact a tampering test might touch.

    Used by the tests to assert that nothing under the real tree moved, which is
    the property the old `finally` blocks could only hope for.
    """
    import hashlib

    def sha(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        return h.hexdigest()

    out: dict[str, str] = {}
    for p in (REAL_MANIFEST, REAL_CONTRACT, REAL_VERIFICATION, REAL_QA_POSE, REAL_STRUCTURE):
        if p.exists():
            out[str(p)] = sha(p)
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    for entries in (manifest.get("published") or {}).values():
        for entry in entries:
            p = Path(entry["path"])
            if p.exists():
                out[str(p)] = sha(p)
    for run in manifest.get("runs") or []:
        for key in RUN_FILE_KEYS:
            p = Path(run.get(key, ""))
            if p.name and p.exists():
                out[str(p)] = sha(p)
        for key in RUN_TREE_KEYS:
            d = Path(run.get(key, ""))
            if d.name and d.is_dir():
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        out[str(f)] = sha(f)
    return out
