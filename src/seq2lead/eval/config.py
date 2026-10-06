"""Loading an experiment, and refusing one whose inputs have moved.

An experiment names its inputs and their digests. Nothing resolves by recency:
a "latest cache" lookup silently changes what a leaderboard row means as soon as
a new cache is built, and the row keeps its old number.

Loading re-reads every digest from the database and re-hashes every vector file
*before* a model is fitted. A mismatch is a hard failure rather than a warning,
because at that point the experiment is no longer the one the config describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

EXPERIMENT_DIR = Path("configs/experiments")


class ExperimentMismatch(RuntimeError):
    """A pinned input no longer matches what the database or disk holds."""


@dataclass(frozen=True)
class CacheRef:
    kind: str
    name: str
    manifest_sha256: str
    storage_sha256: str
    feature_id: int
    path: Path


@dataclass(frozen=True)
class SplitRef:
    name: str
    id: int
    protocol: str | None = None
    partition_endpoints: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentConfig:
    version: str
    status: str
    endpoint_name: str
    endpoint_id: int
    threshold_pki: float
    caches: dict[str, CacheRef]
    splits: tuple[SplitRef, ...]
    deferred: tuple[tuple[str, str], ...]
    cohort: dict[str, Any]
    objective: dict[str, Any]
    selection: dict[str, Any]
    seeds: tuple[int, ...]
    metrics: dict[str, Any]
    strata: dict[str, Any]
    path: Path
    #: Digest of the config file itself. Result artifacts are bound to it, so a
    #: leaderboard cannot render numbers produced under a different experiment.
    config_sha256: str = ""
    search: dict[str, Any] = field(default_factory=dict)

    def split(self, name: str) -> SplitRef:
        for ref in self.splits:
            if ref.name == name:
                return ref
        raise KeyError(
            f"{name!r} is not in this experiment. Scored splits: "
            f"{[s.name for s in self.splits]}. Deferred: {[d[0] for d in self.deferred]}."
        )


def load_experiment(
    conn: psycopg.Connection, version: str = "baseline-v1", directory: Path = EXPERIMENT_DIR
) -> ExperimentConfig:
    """Read a config and verify every pinned identity against the live database."""
    import yaml

    path = directory / f"{version}.yaml"
    if not path.exists():
        raise ExperimentMismatch(f"no experiment config at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    endpoint = raw["endpoint"]
    row = conn.execute(
        "SELECT id, superseded_by FROM endpoint_version WHERE name=%s", (endpoint["name"],)
    ).fetchone()
    if row is None or int(row[0]) != int(endpoint["id"]):
        raise ExperimentMismatch(
            f"endpoint {endpoint['name']!r} is id {row[0] if row else None}, "
            f"config pins {endpoint['id']}"
        )
    if row[1]:
        raise ExperimentMismatch(
            f"endpoint {endpoint['name']!r} was superseded by {row[1]!r}; "
            "an experiment must not silently run against a retired endpoint"
        )

    caches: dict[str, CacheRef] = {}
    for kind, pinned in raw["features"].items():
        caches[kind] = _verify_cache(conn, kind, pinned)

    splits: list[SplitRef] = []
    for entry in raw["splits"]:
        found = conn.execute(
            "SELECT id, superseded_by, protocol FROM split_version WHERE name=%s",
            (entry["name"],),
        ).fetchone()
        if found is None or int(found[0]) != int(entry["id"]):
            raise ExperimentMismatch(
                f"split {entry['name']!r} is id {found[0] if found else None}, "
                f"config pins {entry['id']}"
            )
        if found[1]:
            raise ExperimentMismatch(f"split {entry['name']!r} was superseded by {found[1]!r}")
        declared = entry.get("protocol")
        if declared and str(found[2]) != declared and declared != "train_only":
            raise ExperimentMismatch(
                f"split {entry['name']!r} has protocol {found[2]!r}, config says {declared!r}"
            )
        splits.append(
            SplitRef(
                name=entry["name"],
                id=int(entry["id"]),
                protocol=entry.get("protocol") or str(found[2]),
                partition_endpoints={
                    k: int(v) for k, v in (entry.get("partition_endpoints") or {}).items()
                },
            )
        )

    from seq2lead.features.store import sha256_file

    return ExperimentConfig(
        config_sha256=sha256_file(path),
        version=raw["version"],
        status=raw["status"],
        endpoint_name=endpoint["name"],
        endpoint_id=int(endpoint["id"]),
        threshold_pki=float(endpoint["threshold_pki"]),
        caches=caches,
        splits=tuple(splits),
        deferred=tuple(
            (d["name"], " ".join(str(d.get("reason", "")).split()))
            for d in raw.get("deferred_splits", [])
        ),
        cohort=raw.get("cohort", {}),
        objective=raw.get("objective", {}),
        selection=raw.get("selection", {}),
        seeds=tuple(int(s) for s in raw.get("seeds", ())),
        metrics=raw.get("metrics", {}),
        strata=raw.get("strata", {}),
        search=raw.get("search", {}),
        path=path,
    )


def _verify_cache(conn: psycopg.Connection, kind: str, pinned: dict[str, str]) -> CacheRef:
    from seq2lead.features.store import sha256_file

    row = conn.execute(
        "SELECT id, manifest_sha256, storage_sha256, storage_path, superseded_by, kind "
        "FROM feature_version WHERE name=%s",
        (pinned["name"],),
    ).fetchone()
    if row is None:
        raise ExperimentMismatch(f"no feature cache named {pinned['name']!r}")
    feature_id, manifest, storage, path_text, superseded, actual_kind = row
    if superseded:
        raise ExperimentMismatch(f"cache {pinned['name']!r} was superseded by {superseded!r}")
    if str(actual_kind) != kind:
        raise ExperimentMismatch(
            f"cache {pinned['name']!r} is kind {actual_kind!r}, config lists it under {kind!r}"
        )
    if str(manifest) != pinned["manifest_sha256"]:
        raise ExperimentMismatch(
            f"cache {pinned['name']!r} manifest digest changed:\n"
            f"  config   {pinned['manifest_sha256']}\n  database {manifest}\n"
            "The cache now covers different inputs than the experiment was written for."
        )
    path = Path(str(path_text))
    if not path.exists():
        raise ExperimentMismatch(f"cache {pinned['name']!r} vectors are missing at {path}")
    on_disk = sha256_file(path)
    if on_disk != pinned["storage_sha256"] or on_disk != str(storage):
        raise ExperimentMismatch(
            f"cache {pinned['name']!r} vectors do not match their pinned digest:\n"
            f"  config   {pinned['storage_sha256']}\n  database {storage}\n  on disk  {on_disk}"
        )
    return CacheRef(
        kind=kind,
        name=pinned["name"],
        manifest_sha256=str(manifest),
        storage_sha256=on_disk,
        feature_id=int(feature_id),
        path=path,
    )
