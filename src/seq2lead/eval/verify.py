"""Verify prediction artifacts against a frozen manifest, before anything is recomputed.

The first recomputation recorded the digest of each file it read. That is a
record, not a check: it hashes whatever is there and agrees with itself. It also
globbed the directory, so a missing run silently shrank the result set and a
stray file silently joined it.

This verifies instead. The expected run set and the expected digests come from
`configs/manifests/m8_predictions.json`, frozen from records written *before*
this code existed. Every check runs before a single metric is computed, and a
failure leaves the published results untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

MANIFEST_PATH = Path("configs/manifests/m8_predictions.json")

REQUIRED_ARRAYS = (
    "compound_id",
    "target_id",
    "label",
    "prediction",
    "regression_compound_id",
    "regression_target_id",
    "regression_y",
    "regression_prediction",
)
ALLOWED_LABELS = frozenset({"active", "inactive"})


class VerificationError(RuntimeError):
    """Prediction artifacts do not match the frozen manifest. Nothing is published."""


@dataclass(frozen=True)
class ExpectedRun:
    model: str
    split: str
    seed: int | None
    deterministic: bool
    filename: str
    sha256: str


@dataclass
class PredictionManifest:
    manifest_version: str
    experiment_version: str
    provenance: str
    runs: tuple[ExpectedRun, ...]
    path: Path
    sha256: str

    def by_filename(self) -> dict[str, ExpectedRun]:
        return {r.filename: r for r in self.runs}


@dataclass
class VerificationReport:
    manifest_version: str
    manifest_sha256: str
    n_expected: int
    n_verified: int
    checks: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        self.checks.append(message)


def load_manifest(path: Path = MANIFEST_PATH) -> PredictionManifest:
    from seq2lead.features.store import sha256_file

    if not path.exists():
        raise VerificationError(
            f"no frozen prediction manifest at {path}. Recomputation is refused without "
            "one: verifying files against digests taken from those same files proves "
            "nothing."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    runs = tuple(
        ExpectedRun(
            model=r["model"],
            split=r["split"],
            seed=r["seed"],
            deterministic=r["deterministic"],
            filename=r["filename"],
            sha256=r["sha256"],
        )
        for r in raw["runs"]
    )
    if len({r.filename for r in runs}) != len(runs):
        raise VerificationError(f"{path} lists a filename twice")
    return PredictionManifest(
        manifest_version=raw["manifest_version"],
        experiment_version=raw["experiment"]["version"],
        provenance=raw["provenance"],
        runs=runs,
        path=path,
        sha256=sha256_file(path),
    )


def _cohort_keys(
    conn: psycopg.Connection, config: ExperimentConfig, split: str
) -> tuple[dict[tuple[int, int], str], set[tuple[int, int]], set[tuple[int, int]]]:
    """Legitimate pairs for a split's test partition, from its own endpoints.

    For `temporal_proxy-v4` the labels come from the partition endpoint, so a
    prediction file carrying a pair the test-period cohort does not contain is a
    real error rather than a rounding difference.
    """
    from seq2lead.eval.cohort import build_cohort, ranking_cohort

    ranking = ranking_cohort(conn, config, split, "test")
    regression = build_cohort(conn, config, split, "test")
    labels = {
        (int(c), int(t)): str(lab)
        for c, t, lab in zip(ranking.compound_id, ranking.target_id, ranking.label, strict=True)
    }
    regression_keys = {
        (int(c), int(t)) for c, t in zip(regression.compound_id, regression.target_id, strict=True)
    }
    return labels, set(labels), regression_keys


def _strata_for(conn: psycopg.Connection, split_id: int) -> dict[tuple[int, int], str]:
    return {
        (int(a), int(b)): str(s)
        for a, b, s in conn.execute(
            "SELECT compound_id, target_id, stratum FROM split_pair_assignment "
            "WHERE split_id=%s AND partition='test'",
            (split_id,),
        ).fetchall()
    }


def verify_predictions(  # noqa: PLR0915
    conn: psycopg.Connection,
    config: ExperimentConfig,
    prediction_dir: Path,
    manifest: PredictionManifest,
) -> VerificationReport:
    """Every check, before any metric is computed. Raises on the first problem class."""
    from seq2lead.features.store import sha256_file

    report = VerificationReport(
        manifest_version=manifest.manifest_version,
        manifest_sha256=manifest.sha256,
        n_expected=len(manifest.runs),
        n_verified=0,
    )

    if manifest.experiment_version != config.version:
        raise VerificationError(
            f"manifest was frozen for experiment {manifest.experiment_version!r}, "
            f"the loaded config is {config.version!r}"
        )

    # ---- exactly the expected run set
    present = {p.name for p in prediction_dir.glob("*.npz")}
    expected = manifest.by_filename()
    missing = sorted(set(expected) - present)
    unexpected = sorted(present - set(expected))
    if missing:
        raise VerificationError(
            f"{len(missing)} expected prediction files are missing, e.g. {missing[:3]}. "
            "Recomputing over a partial run set would publish a leaderboard that "
            "silently covers fewer runs than it claims."
        )
    if unexpected:
        raise VerificationError(
            f"{len(unexpected)} prediction files are not in the manifest, e.g. "
            f"{unexpected[:3]}. Refusing rather than deciding for you which runs count."
        )
    report.record(f"run set: exactly the {len(expected)} expected files, none extra")

    # ---- per-file digest and contents
    cohort_cache: dict[str, Any] = {}
    for filename in sorted(expected):
        run = expected[filename]
        path = prediction_dir / filename
        actual = sha256_file(path)
        if actual != run.sha256:
            raise VerificationError(
                f"{filename} does not match its frozen digest.\n"
                f"  expected {run.sha256}\n  actual   {actual}\n"
                "The bytes behind the published numbers have changed."
            )

        with np.load(path, allow_pickle=False) as data:
            arrays = {k: data[k] for k in data.files}

        for name in REQUIRED_ARRAYS:
            if name not in arrays:
                raise VerificationError(f"{filename} has no `{name}` array")

        compound_id, target_id = arrays["compound_id"], arrays["target_id"]
        label, prediction = arrays["label"], arrays["prediction"]
        reg_c, reg_t = arrays["regression_compound_id"], arrays["regression_target_id"]
        reg_y, reg_p = arrays["regression_y"], arrays["regression_prediction"]

        for name, first, second in (
            ("ranking", compound_id, target_id),
            ("ranking labels", compound_id, label),
            ("ranking predictions", compound_id, prediction),
            ("regression", reg_c, reg_t),
            ("regression targets", reg_c, reg_y),
            ("regression predictions", reg_c, reg_p),
        ):
            if first.shape[0] != second.shape[0]:
                raise VerificationError(
                    f"{filename}: {name} arrays have mismatched lengths "
                    f"({first.shape[0]:,} vs {second.shape[0]:,})"
                )
        for name, array in (("prediction", prediction), ("regression_prediction", reg_p)):
            if array.ndim != 1:
                raise VerificationError(f"{filename}: `{name}` is {array.ndim}-dimensional")

        ranking_pairs = list(zip(compound_id.tolist(), target_id.tolist(), strict=True))
        regression_pairs = list(zip(reg_c.tolist(), reg_t.tolist(), strict=True))
        if len(set(ranking_pairs)) != len(ranking_pairs):
            raise VerificationError(
                f"{filename}: {len(ranking_pairs) - len(set(ranking_pairs)):,} duplicate "
                "ranking pair ids"
            )
        if len(set(regression_pairs)) != len(regression_pairs):
            raise VerificationError(f"{filename}: duplicate regression pair ids")

        bad_labels = set(map(str, np.unique(label))) - ALLOWED_LABELS
        if bad_labels:
            raise VerificationError(
                f"{filename}: labels outside {sorted(ALLOWED_LABELS)}: {sorted(bad_labels)}"
            )
        for name, array in (
            ("prediction", prediction),
            ("regression_prediction", reg_p),
            ("regression_y", reg_y),
        ):
            if not np.isfinite(np.asarray(array, dtype=np.float64)).all():
                raise VerificationError(f"{filename}: `{name}` holds non-finite values")

        # ---- cohort membership, against this split's own test endpoints
        if run.split not in cohort_cache:
            cohort_cache[run.split] = _cohort_keys(conn, config, run.split)
        expected_labels, ranking_keys, regression_keys = cohort_cache[run.split]

        unknown = [p for p in ranking_pairs if p not in ranking_keys]
        if unknown:
            raise VerificationError(
                f"{filename}: {len(unknown):,} ranking pairs are not in the test cohort "
                f"for {run.split}, e.g. {unknown[:3]}"
            )
        mismatched = [
            p
            for p, observed in zip(ranking_pairs, map(str, label), strict=True)
            if expected_labels[p] != observed
        ]
        if mismatched:
            raise VerificationError(
                f"{filename}: {len(mismatched):,} ranking labels disagree with the test "
                f"cohort for {run.split}, e.g. {mismatched[:3]}"
            )
        unknown_regression = [p for p in regression_pairs if p not in regression_keys]
        if unknown_regression:
            raise VerificationError(
                f"{filename}: {len(unknown_regression):,} regression pairs are not in the "
                f"test cohort for {run.split}, e.g. {unknown_regression[:3]}"
            )

        # ---- temporal strata must resolve for every scored pair
        split_ref = config.split(run.split)
        if split_ref.partition_endpoints:
            key = f"_strata::{run.split}"
            if key not in cohort_cache:
                cohort_cache[key] = _strata_for(conn, split_ref.id)
            strata = cohort_cache[key]
            missing_strata = [p for p in ranking_pairs if not strata.get(p)]
            if missing_strata:
                raise VerificationError(
                    f"{filename}: {len(missing_strata):,} pairs have no temporal stratum, "
                    f"e.g. {missing_strata[:3]}. Assigning them an empty stratum would "
                    "quietly drop them from both the new and recurrent tables."
                )
        report.n_verified += 1

    report.record(f"digests: all {report.n_verified} files match the frozen manifest")
    report.record("arrays: required names, aligned lengths, 1-D predictions")
    report.record("pair ids: unique within each file")
    report.record(f"labels: within {sorted(ALLOWED_LABELS)}, matching the test cohort")
    report.record("values: predictions and regression targets all finite")
    report.record("cohorts: every pair present in its split's own test cohort")
    report.record("temporal: every scored pair resolves to a new/recurrent stratum")
    return report
