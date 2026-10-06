"""Named, bound result versions. Nothing is selected by recency.

`load_results()` used to try a fixed list of filenames and return the first that
existed, always preferring the corrected summary. A later fitting run writing a
fresh `baseline_summary.json` would therefore be invisible: the renderer kept
showing the older corrected file, and the leaderboard would silently be stale.

So a result set is now a **named version**, registered with the experiment and
metric version that produced it and the digest of the config file it ran under.
Callers name the version they want. A mismatch is refused rather than rendered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seq2lead.eval.config import ExperimentConfig

RESULT_DIR = Path("reports/results")
INDEX_NAME = "index.json"


class ResultSelectionError(RuntimeError):
    """A result version is missing, ambiguous, or bound to a different experiment."""


@dataclass
class ResultVersion:
    version: str
    filename: str
    metric_version: str
    experiment_version: str
    config_sha256: str
    n_runs: int
    created_at: str
    sha256: str
    source_version: str | None = None
    note: str = ""
    superseded_by: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _index_path(directory: Path) -> Path:
    return directory / INDEX_NAME


def read_index(directory: Path = RESULT_DIR) -> dict[str, ResultVersion]:
    path = _index_path(directory)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: ResultVersion(**v) for k, v in raw.get("versions", {}).items()}


def write_index(versions: dict[str, ResultVersion], directory: Path = RESULT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = _index_path(directory)
    payload = {
        "description": (
            "Named result versions. Rendering selects one explicitly; nothing is "
            "chosen by recency or filename order."
        ),
        "versions": {k: vars(v) for k, v in sorted(versions.items())},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def owner_of(filename: str, directory: Path = RESULT_DIR) -> str | None:
    """Which registered version, if any, owns this filename."""
    for version, entry in read_index(directory).items():
        if entry.filename == filename:
            return version
    return None


def check_publishable(
    *,
    version: str,
    filename: str,
    body: str,
    metric_version: str,
    config: ExperimentConfig,
    directory: Path = RESULT_DIR,
    also_reserving: tuple[str, ...] = (),
) -> ResultVersion | None:
    """Raise unless this publication is safe. Returns the existing entry, if any.

    Checks every way a write could damage something already published, **before**
    any file is touched: a filename owned by a different version, a same-version
    republication whose content or bindings changed, or an existing artifact that
    no longer matches its registered digest.
    """
    from seq2lead.features.store import sha256_file

    index = read_index(directory)
    for candidate in (filename, *also_reserving):
        owner = next((v for v, e in index.items() if e.filename == candidate), None)
        if owner is not None and owner != version:
            raise ResultSelectionError(
                f"cannot write {candidate!r} for version {version!r}: it is already "
                f"registered to version {owner!r}. Overwriting it would leave "
                f"{owner!r} failing its own checksum."
            )

    existing = index.get(version)
    if existing is None:
        return None

    if existing.metric_version != metric_version:
        raise ResultSelectionError(
            f"version {version!r} is published at metric version "
            f"{existing.metric_version!r}; this run is {metric_version!r}. Publish "
            "under a new version."
        )
    if existing.experiment_version != config.version or (
        existing.config_sha256 and existing.config_sha256 != config.config_sha256
    ):
        raise ResultSelectionError(
            f"version {version!r} is published for experiment "
            f"{existing.experiment_version!r} under config "
            f"{existing.config_sha256[:16]}…; this run is {config.version!r} under "
            f"{config.config_sha256[:16]}…. Publish under a new version."
        )

    registered = directory / existing.filename
    if registered.exists():
        if sha256_file(registered) != existing.sha256:
            raise ResultSelectionError(
                f"version {version!r} is registered at {existing.filename} but that "
                "file no longer matches its recorded digest. Refusing to publish over "
                "an artifact whose provenance is already broken."
            )
        if registered.read_text(encoding="utf-8") != body:
            raise ResultSelectionError(
                f"result version {version!r} is already published as "
                f"{existing.filename} with different content. Publish under a new "
                "version rather than redefining one: anything already quoting these "
                "numbers would silently change meaning."
            )
    return existing


def publish(
    runs: list[dict[str, Any]],
    *,
    version: str,
    filename: str,
    metric_version: str,
    config: ExperimentConfig,
    directory: Path = RESULT_DIR,
    source_version: str | None = None,
    note: str = "",
    extra: dict[str, Any] | None = None,
    staged: dict[str, str] | None = None,
) -> ResultVersion:
    """Write a result set under a name, and register it.

    All checks run before anything is written, content is staged and moved into
    place, and the index is written last. A failure therefore leaves every
    existing file and the index byte-identical.

    `staged` optionally carries extra files (filename -> body) to commit in the
    same atomic step, so a caller writing a full record alongside its summary
    cannot leave one without the other.
    """
    from seq2lead.features.store import sha256_file

    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(runs, indent=1, sort_keys=True, default=str) + "\n"
    staged = staged or {}

    existing = check_publishable(
        version=version,
        filename=filename,
        body=body,
        metric_version=metric_version,
        config=config,
        directory=directory,
        also_reserving=tuple(staged),
    )
    if existing is not None and existing.filename != filename:
        # Same content under a different name: keep the published filename so the
        # registered digest and anything referencing it stay valid.
        filename = existing.filename

    # ---- commit: temp files first, then move, then the index
    pending = {filename: body, **staged}
    temporary: list[tuple[Path, Path]] = []
    try:
        for name, content in pending.items():
            target = directory / name
            scratch = target.with_suffix(target.suffix + ".staging")
            scratch.write_text(content, encoding="utf-8")
            temporary.append((scratch, target))
        for scratch, target in temporary:
            scratch.replace(target)
    except BaseException:
        for scratch, _target in temporary:
            scratch.unlink(missing_ok=True)
        raise

    entry = ResultVersion(
        version=version,
        filename=filename,
        metric_version=metric_version,
        experiment_version=config.version,
        config_sha256=config.config_sha256,
        n_runs=len(runs),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sha256=sha256_file(directory / filename),
        source_version=source_version,
        note=note,
        extra=extra or {},
    )
    if existing is not None:
        # An identical rerun keeps its published identity.
        entry.created_at = existing.created_at
        entry.superseded_by = existing.superseded_by
    index = read_index(directory)
    index[version] = entry
    write_index(index, directory)
    return entry


def supersede(version: str, by: str, directory: Path = RESULT_DIR) -> None:
    index = read_index(directory)
    if version not in index:
        raise ResultSelectionError(f"no result version {version!r} to supersede")
    index[version].superseded_by = by
    write_index(index, directory)


def load(
    version: str,
    config: ExperimentConfig | None = None,
    directory: Path = RESULT_DIR,
    *,
    allow_superseded: bool = True,
    require_metric_version: str | None = None,
) -> tuple[list[dict[str, Any]], ResultVersion]:
    """Load one named result version, refusing a mismatch.

    `version` is required. There is deliberately no default and no fallback: the
    previous fallback chain is exactly how a stale corrected file kept being
    rendered after a newer fitting run.
    """
    from seq2lead.features.store import sha256_file

    index = read_index(directory)
    if version not in index:
        known = ", ".join(sorted(index)) or "none registered"
        raise ResultSelectionError(
            f"no result version {version!r} in {_index_path(directory)}. Known: {known}."
        )
    entry = index[version]
    path = directory / entry.filename
    if not path.exists():
        raise ResultSelectionError(f"result version {version!r} names {path}, which is gone")
    actual = sha256_file(path)
    if actual != entry.sha256:
        raise ResultSelectionError(
            f"result version {version!r} at {path} does not match its registered digest.\n"
            f"  registered {entry.sha256}\n  actual     {actual}"
        )
    if entry.superseded_by and not allow_superseded:
        raise ResultSelectionError(
            f"result version {version!r} was superseded by {entry.superseded_by!r}"
        )
    if require_metric_version and entry.metric_version != require_metric_version:
        raise ResultSelectionError(
            f"result version {version!r} was computed at metric version "
            f"{entry.metric_version!r}, {require_metric_version!r} was required"
        )
    if config is not None:
        if entry.experiment_version != config.version:
            raise ResultSelectionError(
                f"result version {version!r} was produced for experiment "
                f"{entry.experiment_version!r}, the loaded config is {config.version!r}"
            )
        if entry.config_sha256 and entry.config_sha256 != config.config_sha256:
            raise ResultSelectionError(
                f"result version {version!r} was produced under a different "
                f"{config.version!r} config file.\n"
                f"  results ran under {entry.config_sha256[:16]}…\n"
                f"  loaded config is  {config.config_sha256[:16]}…\n"
                "The pinned inputs may differ, so the numbers are not comparable."
            )
    return json.loads(path.read_text(encoding="utf-8")), entry
