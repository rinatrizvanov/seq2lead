"""Run-scoped docking artifacts, checked before the first ligand is docked.

Docking a cohort costs wall-clock time measured in tens of minutes. A collision
discovered at write time would mean discarding all of it, so every destination
is checked up front and a collision refuses with nothing written -- the same
discipline M9 arrived at, applied before the expensive part rather than after.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

RUN_ROOT = Path("data/m10/runs")
MANIFEST_PATH = Path("configs/manifests/m10_docking.json")
MANIFEST_VERSION = "m10-docking-v1"


class ArtifactCollision(RuntimeError):
    """A destination is already occupied. Nothing has been written."""


@dataclass(frozen=True)
class DockRunPaths:
    """Where one docking run's artifacts live, scoped by run label."""

    run: str
    root: Path
    receptor_pdbqt: Path
    receptor_clean_pdb: Path
    ligand_dir: Path
    pose_dir: Path
    scores: Path
    failures: Path
    record: Path

    def all(self) -> tuple[Path, ...]:
        return (
            self.receptor_pdbqt,
            self.receptor_clean_pdb,
            self.scores,
            self.failures,
            self.record,
        )


def run_paths(run_label: str, run_root: Path = RUN_ROOT) -> DockRunPaths:
    root = run_root / run_label
    return DockRunPaths(
        run=run_label,
        root=root,
        receptor_pdbqt=root / "receptor.pdbqt",
        receptor_clean_pdb=root / "receptor_clean.pdb",
        ligand_dir=root / "ligands",
        pose_dir=root / "poses",
        scores=root / "scores.json",
        failures=root / "failures.json",
        record=root / "run_record.json",
    )


def preflight(paths: DockRunPaths, *, overwrite: bool = False) -> None:
    """Refuse before docking starts if any destination is taken."""
    if overwrite:
        return
    occupied = [p for p in paths.all() if p.exists()]
    for d in (paths.ligand_dir, paths.pose_dir):
        if d.exists() and any(d.iterdir()):
            occupied.append(d)
    if occupied:
        listed = "\n  ".join(str(p) for p in occupied)
        raise ArtifactCollision(
            f"run {paths.run!r} already has artifacts on disk:\n  {listed}\n"
            "Refusing before any docking: a cohort run costs tens of minutes and "
            "overwriting would destroy the results it took. Use a new run label, "
            "or pass overwrite explicitly."
        )


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_tree(directory: Path) -> tuple[str, int]:
    """One digest for a whole directory, and how many files it covers.

    The prepared ligands and the output poses run to thousands of files and tens
    of megabytes, so they are referenced rather than shipped. Recording a digest
    per file would bloat the manifest; recording nothing would make them
    unverifiable. This hashes the sorted (name, file digest) pairs, so any
    changed, added or removed file changes the result.
    """
    if not directory.exists():
        return ("", 0)
    entries = sorted(p for p in directory.iterdir() if p.is_file())
    h = hashlib.sha256()
    for entry in entries:
        h.update(entry.name.encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(entry).encode("ascii"))
        h.update(b"\n")
    return (h.hexdigest(), len(entries))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class RunRecord:
    """Everything needed to recompute the gate without docking again."""

    run: str
    experiment: str
    config_sha256: str
    engine_version: str
    engine_sha256: str
    receptor_sha256: str
    cohort_sha256: str
    cohort_path: str
    scores_sha256: str
    failures_sha256: str
    box_center: list[float]
    box_size: list[float]
    exhaustiveness: int
    num_modes: int
    seed: int
    n_requested: int
    n_scored: int
    n_failed: int
    wall_seconds: float
    created_at: str
    provenance: str


def write_record(record: RunRecord, path: Path, *, overwrite: bool = False) -> Path:
    body = json.dumps(asdict(record), indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        if path.read_text(encoding="utf-8") == body:
            return path
        raise ArtifactCollision(
            f"{path} already holds a different run record. Refusing to replace it."
        )
    path.write_text(body, encoding="utf-8")
    return path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------ manifest

REQUIRED_ARTIFACTS = (
    ("receptor", "receptor_pdbqt", "receptor_sha256"),
    ("cohort", "cohort_path", "cohort_sha256"),
    ("scores", "scores_path", "scores_sha256"),
    ("failures", "failures_path", "failures_sha256"),
    ("run record", "record_path", "record_sha256"),
)

#: Directories referenced by a digest over their contents rather than shipped.
#: They are the bulk of the run and were previously recorded but never checked.
REQUIRED_TREES = (
    ("prepared ligands", "ligand_dir", "ligand_dir_sha256", "ligand_dir_files"),
    ("output poses", "pose_dir", "pose_dir_sha256", "pose_dir_files"),
)

#: Published artifacts the manifest binds so a result cannot be edited in place.
PUBLISHED_ARTIFACTS = ("gate", "qa", "sensitivity")


def freeze_manifest(
    runs: list[dict[str, Any]],
    provenance: str,
    path: Path = MANIFEST_PATH,
    published: dict[str, list[dict[str, str]]] | None = None,
    config_path: Path | None = None,
    superseded_contracts: list[dict[str, str]] | None = None,
) -> Path:
    """Record every run's artifacts, and the published results they produced."""
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "frozen_at": utc_now(),
        "n_runs": len(runs),
        "provenance": provenance,
        "runs": runs,
    }
    if published:
        manifest["published"] = published
    if config_path is not None and config_path.exists():
        manifest["config_path"] = str(config_path)
        manifest["config_sha256"] = sha256_file(config_path)
    if superseded_contracts:
        # A run produced under an earlier contract keeps that digest: rewriting it
        # would falsify the record. Declaring it here is what distinguishes a
        # documented amendment from a contract that changed behind the results.
        manifest["superseded_contracts"] = superseded_contracts
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@dataclass
class VerificationScope:
    """What a verification run was actually able to look at.

    A review ZIP deliberately omits the large artifacts. Reporting those as
    verified would be a lie; omitting them silently would be worse, because a
    clean result would then mean two different things depending on where it ran.
    So they are counted separately and named.
    """

    verified: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def complete(self) -> bool:
        return not self.problems and not self.unavailable

    def summary(self) -> str:
        head = "verifies" if self.ok else f"{len(self.problems)} problem(s)"
        if self.unavailable:
            return (
                f"{head}: {len(self.verified)} artifact(s) checked, "
                f"{len(self.unavailable)} not present locally"
            )
        return f"{head}: {len(self.verified)} artifact(s) checked, nothing missing"


VERIFICATION_CONTRACT_PATH = Path("configs/manifests/m10_verification.json")


class VerificationContractError(RuntimeError):
    """The verification contract is absent or does not say what it must."""


@dataclass(frozen=True)
class VerificationContract:
    """What a complete M10 manifest must contain, declared independently of it."""

    version: str
    primary_runs: tuple[str, ...]
    sensitivity_runs: tuple[str, ...]
    required_publications: dict[str, dict[str, Any]]

    @property
    def expected_runs(self) -> tuple[str, ...]:
        return (*self.primary_runs, *self.sensitivity_runs)


def load_verification_contract(
    path: Path = VERIFICATION_CONTRACT_PATH,
) -> VerificationContract:
    if not path.exists():
        raise VerificationContractError(
            f"no verification contract at {path}. Verification refuses to infer what the "
            "manifest should contain from the manifest itself."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        runs = raw["expected_runs"]
        contract = VerificationContract(
            version=str(raw["verification_contract_version"]),
            primary_runs=tuple(runs["primary"]),
            sensitivity_runs=tuple(runs["sensitivity"]),
            required_publications=dict(raw["required_publications"]),
        )
    except KeyError as exc:
        raise VerificationContractError(f"{path} does not declare {exc}") from exc
    if not contract.primary_runs:
        raise VerificationContractError(f"{path} declares no primary run")
    if not contract.required_publications:
        raise VerificationContractError(f"{path} declares no required publications")
    return contract


def verify_manifest_scoped(
    path: Path = MANIFEST_PATH,
    *,
    config_path: Path = Path("configs/experiments/m10-docking-v1.yaml"),
    expected_runs: tuple[str, ...] | None = None,
    allow_missing_trees: bool = False,
    contract: VerificationContract | None = None,
) -> VerificationScope:
    """Verify every binding the manifest records, and say what it could not see.

    `allow_missing_trees` is for the review ZIP, which ships the manifest but not
    the thousands of ligand and pose files. With it set, an absent directory is
    reported as unavailable rather than as a problem -- but a directory that *is*
    present is still checked, and a wrong digest is still a problem.
    """
    scope = VerificationScope()
    if not path.exists():
        scope.problems.append(f"no docking manifest at {path}")
        return scope
    manifest = json.loads(path.read_text(encoding="utf-8"))
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        scope.problems.append("the manifest has no `runs` list")
        return scope

    # ---- the manifest's own self-consistency, which an empty list used to pass
    declared = manifest.get("n_runs")
    if declared is None:
        scope.problems.append("the manifest does not declare n_runs")
    elif declared != len(runs):
        scope.problems.append(
            f"the manifest declares n_runs={declared} but holds {len(runs)} run(s)"
        )
    if not runs:
        scope.problems.append("the manifest records no runs at all")
    labels = [r.get("run") for r in runs]
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        scope.problems.append(f"duplicate run labels: {duplicates}")
    # The expected set comes from the contract when one is supplied, so a manifest
    # cannot define its own completeness by omission.
    wanted = contract.expected_runs if contract is not None else expected_runs
    if wanted is not None:
        missing = sorted(set(wanted) - set(labels))
        unexpected = sorted(set(labels) - set(wanted))
        if missing:
            scope.problems.append(f"expected run(s) absent from the manifest: {missing}")
        if unexpected:
            scope.problems.append(f"run(s) not in the expected set: {unexpected}")
        if contract is not None:
            for primary in contract.primary_runs:
                if primary not in labels:
                    scope.problems.append(f"the primary run {primary!r} is not in the manifest")

    # ---- the contract, as it stood when this manifest was frozen
    declared_config = manifest.get("config_sha256")
    if config_path.exists():
        actual_config = sha256_file(config_path)
        scope.verified.append(str(config_path))
        if declared_config is None:
            scope.problems.append(
                "the manifest does not record the contract digest it was frozen against"
            )
        elif declared_config != actual_config:
            scope.problems.append(
                f"the contract has changed since this manifest was frozen: "
                f"{config_path} is {actual_config[:16]}… but the manifest was frozen "
                f"against {declared_config[:16]}…"
            )
    else:
        scope.unavailable.append(str(config_path))

    # A run may have been produced under an earlier contract, but only one this
    # manifest explicitly declares. An undeclared difference is a problem.
    allowed = {declared_config} if declared_config else set()
    allowed |= {c.get("sha256") for c in manifest.get("superseded_contracts", [])}
    for run in runs:
        recorded = run.get("config_sha256")
        if recorded is None:
            scope.problems.append(f"{run.get('run')}: no config_sha256 recorded")
        elif declared_config and recorded not in allowed:
            scope.problems.append(
                f"{run.get('run')}: produced under contract {recorded[:16]}…, which the "
                "manifest neither declares as current nor lists in superseded_contracts"
            )

    # ---- per-run files and directories
    for run in runs:
        label = run.get("run", "<unnamed>")
        for name, file_key, digest_key in REQUIRED_ARTIFACTS:
            target, expected = run.get(file_key), run.get(digest_key)
            if target is None or expected is None:
                absent = [k for k, v in ((file_key, target), (digest_key, expected)) if v is None]
                scope.problems.append(
                    f"{label}: {name} cannot be verified -- the manifest records no {absent}"
                )
                continue
            candidate = Path(target)
            if not candidate.exists():
                scope.problems.append(f"{label}: {name} missing at {candidate}")
            elif sha256_file(candidate) != expected:
                scope.problems.append(f"{label}: {name} bytes changed at {candidate}")
            else:
                scope.verified.append(str(candidate))

        for name, dir_key, digest_key, count_key in REQUIRED_TREES:
            target = run.get(dir_key)
            expected = run.get(digest_key)
            expected_n = run.get(count_key)
            if target is None or expected is None or expected_n is None:
                absent = [
                    k
                    for k, v in ((dir_key, target), (digest_key, expected), (count_key, expected_n))
                    if v is None
                ]
                scope.problems.append(
                    f"{label}: {name} cannot be verified -- the manifest records no {absent}"
                )
                continue
            directory = Path(target)
            if not directory.exists():
                if allow_missing_trees:
                    scope.unavailable.append(f"{directory} ({name}, {expected_n} files)")
                else:
                    scope.problems.append(f"{label}: {name} missing at {directory}")
                continue
            actual, actual_n = sha256_tree(directory)
            if actual_n != expected_n:
                scope.problems.append(
                    f"{label}: {name} holds {actual_n} files, the manifest records {expected_n}"
                )
            if actual != expected:
                scope.problems.append(
                    f"{label}: {name} contents changed at {directory} "
                    f"({actual[:16]}… against {expected[:16]}…)"
                )
            if actual == expected and actual_n == expected_n:
                scope.verified.append(f"{directory} ({actual_n} files)")

    # ---- published results, so a decision cannot be edited in place
    published = manifest.get("published") or {}
    if contract is not None:
        if not published:
            scope.problems.append(
                "the manifest records no published results at all; the verification "
                f"contract requires {sorted(contract.required_publications)}"
            )
        for key, want in sorted(contract.required_publications.items()):
            rows = published.get(key)
            if not rows:
                scope.problems.append(f"required publication group {key!r} is missing or empty")
                continue
            if len(rows) != int(want["count"]):
                scope.problems.append(
                    f"publication group {key!r} holds {len(rows)} entr(y/ies), the contract "
                    f"requires {want['count']}"
                )
            paths = [r.get("path") for r in rows]
            duplicates = sorted({p for p in paths if paths.count(p) > 1})
            if duplicates:
                scope.problems.append(
                    f"publication group {key!r} lists duplicate paths: {duplicates}"
                )
            absent = sorted(set(want.get("paths", [])) - set(paths))
            if absent:
                scope.problems.append(
                    f"publication group {key!r} does not list required path(s): {absent}"
                )
        unexpected_groups = sorted(set(published) - set(contract.required_publications))
        if unexpected_groups:
            scope.problems.append(
                f"publication group(s) not declared in the contract: {unexpected_groups}"
            )
    for key in PUBLISHED_ARTIFACTS:
        for entry in published.get(key, []) or []:
            target, expected = entry.get("path"), entry.get("sha256")
            if target is None or expected is None:
                scope.problems.append(f"published {key}: entry missing path or digest")
                continue
            candidate = Path(target)
            if not candidate.exists():
                scope.problems.append(f"published {key} missing at {candidate}")
            elif sha256_file(candidate) != expected:
                scope.problems.append(f"published {key} bytes changed at {candidate}")
            else:
                scope.verified.append(str(candidate))
    return scope


def verify_manifest(path: Path = MANIFEST_PATH, **kwargs: Any) -> list[str]:
    """Problems only, for callers that just want a pass/fail."""
    return verify_manifest_scoped(path, **kwargs).problems
