"""The production entry point for an as-of run. Refuses before it fits.

`prepare_fitting` is an orchestration primitive: it takes its fit callables from
the caller, which is what lets a test inspect them. That flexibility made it the
wrong thing to call in production -- its preconditions were optional, so a caller
could skip every check by passing nothing, and `verify_preconditions` treated an
empty digest set as "all verified".

`run` closes those, and a second round closed a subtler class. Verifying an
artifact's digest proves nothing about a run that consumes something else, and
three parameters did exactly that:

* `membership_path` was loaded while `artifacts["a_membership"]` was the thing
  verified, so a substituted file changed the fitted rows and their targets with
  every gate reporting success;
* `FeatureSource.reuse_map` arrived from the caller, so **swapping two entries
  changed the vectors bound to those keys while storage digests, coverage and
  even the emitted dataset digest stayed identical**;
* `roles` arrived from the caller, so dropping the evaluation role skipped its
  coverage check and shrinking the train role silently filtered fitting rows
  into an exclusion counter instead of refusing.

So nothing the run consumes is a parameter any more. The membership, the feature
caches, the reuse maps and the role entities are all **derived** from the bound
artifact set, after its digests are verified -- and the membership's bytes are
re-verified at the moment of consumption, because a gate that fires before a read
does not protect the read. The emitted datasets are then checked against the
pinned `model_facing_datasets` record before any callback.

Nothing here fits. The callables stay parameters so this module can be exercised
with mocks -- which is how the guarantees above are demonstrated rather than
asserted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.asof.contract import (
    CONTRACT_VERSION,
    DERIVED_FROM,
    PINNED_SETTINGS,
    REQUIRED_DIGESTS,
    STORED_DIMS,
    check_digest_set,
    check_settings,
)
from seq2lead.asof.datasets import (
    TRAIN_ROLE,
    VALIDATION_ROLE,
    PreflightError,
    load_dataset,
    prepare_fitting,
)
from seq2lead.asof.features import BindingError, load_binding

if TYPE_CHECKING:
    from collections.abc import Callable

#: Bumped when the gates, their order, or what the run derives changes.
RUNNER_VERSION = "m11g/runner/v2"

#: The evaluation role carries no fitting standing. Its coverage is still
#: required: a cohort missing vectors is a different cohort, so the preflight
#: counts and the feasibility verdict would describe a population no run scores.
EVALUATION_ROLE = "evaluation"

#: Which feature kind keys on which entity identity.
KIND_ENTITY = {"ecfp4": "compounds", "esm2": "sequences"}


@dataclass(frozen=True)
class FeatureSource:
    """One feature kind's artifacts and its reuse map, both digest-bound.

    Built by `derive_feature_sources` from the verified `feature_binding` and
    `feature_resolution` artifacts. It is deliberately not something a caller
    hands to `run`: `reuse_map` decides which cached row each content key gets,
    so a caller-supplied one can repoint every vector without altering a single
    digest the run reports.
    """

    kind: str
    accepted_path: str | Path
    accepted_sha256: str
    reuse_map: dict[str, int]
    extension_path: str | Path | None = None
    extension_sha256: str | None = None
    reuse_map_path: str | Path | None = None
    reuse_map_sha256: str | None = None


@dataclass(frozen=True)
class RoleEntities:
    """The content identities one role needs a vector for."""

    compounds: set[str] = field(default_factory=set)
    sequences: set[str] = field(default_factory=set)


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(artifacts: dict[str, str | Path], name: str) -> dict:
    try:
        return json.loads(Path(artifacts[name]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"the bound input {name!r} could not be read as JSON: {exc}"
        raise PreflightError(msg) from exc


def validate_settings(config: dict[str, Any]) -> None:
    """Every declared setting must be present AND equal to the frozen value."""
    faults = check_settings(config)
    if faults:
        msg = (
            "configuration departs from the frozen contract, so the run is refused "
            "before fitting -- " + "; ".join(faults)
        )
        raise PreflightError(msg)


def validate_inputs(expected: dict[str, str], artifacts: dict[str, str | Path]) -> dict[str, str]:
    """The complete pinned set, each digest recomputed from the bytes on disk."""
    faults = check_digest_set(expected)
    if faults:
        msg = "the bound input set is wrong, so the run is refused -- " + "; ".join(faults)
        raise PreflightError(msg)
    verified = {}
    for name in sorted(expected):
        path = artifacts.get(name)
        if path is None:
            msg = f"no artifact supplied for the bound input {name!r}; refusing before fitting"
            raise PreflightError(msg)
        try:
            actual = _sha256(path)
        except OSError as exc:
            msg = f"the bound input {name!r} could not be read: {exc}"
            raise PreflightError(msg) from exc
        if actual != expected[name]:
            msg = (
                f"{name} digest {actual[:16]}… does not match the bound "
                f"{expected[name][:16]}…; refusing before fitting"
            )
            raise PreflightError(msg)
        verified[name] = actual
    return verified


def derive_feature_sources(artifacts: dict[str, str | Path]) -> dict[str, FeatureSource]:
    """Build the feature sources from the two pinned artifacts that govern them.

    The caches come from `feature_binding` and the reuse maps from
    `feature_resolution`, whose recorded digest for each map is verified against
    the map's own bytes. The two are then cross-checked: the binding's declared
    entry count must equal the loaded map's size, and its stored dimension must
    equal the frozen one. A replacement mapping has nowhere to enter.
    """
    binding = _read_json(artifacts, DERIVED_FROM["feature_caches"])
    resolution = _read_json(artifacts, DERIVED_FROM["reuse_maps"])
    declared = binding.get("sources")
    if not isinstance(declared, dict) or set(declared) != set(STORED_DIMS):
        msg = (
            f"the verified feature binding must declare sources {sorted(STORED_DIMS)}, "
            f"got {sorted(declared) if isinstance(declared, dict) else type(declared).__name__}"
        )
        raise PreflightError(msg)
    maps = resolution.get("reuse_maps")
    if not isinstance(maps, dict) or set(maps) != set(STORED_DIMS):
        msg = (
            "the verified feature resolution must record a reuse map per kind "
            f"{sorted(STORED_DIMS)}, got "
            f"{sorted(maps) if isinstance(maps, dict) else type(maps).__name__}"
        )
        raise PreflightError(msg)

    sources = {}
    for kind in sorted(STORED_DIMS):
        spec, mapping = declared[kind], maps[kind]
        if spec.get("stored_dim") != STORED_DIMS[kind]:
            msg = (
                f"{kind}: the binding declares stored_dim {spec.get('stored_dim')!r}, "
                f"frozen is {STORED_DIMS[kind]}"
            )
            raise PreflightError(msg)
        actual = _sha256(mapping["path"])
        if actual != mapping["sha256"]:
            msg = (
                f"{kind}: reuse map {mapping['path']} digest {actual[:16]}… does not "
                f"match the {mapping['sha256'][:16]}… recorded in the verified "
                "resolution; refusing before fitting"
            )
            raise PreflightError(msg)
        reuse = {
            str(k): int(v)
            for k, v in json.loads(Path(mapping["path"]).read_text(encoding="utf-8")).items()
        }
        if len(reuse) != spec.get("reuse_map_entries"):
            msg = (
                f"{kind}: the reuse map holds {len(reuse):,} entries, the verified binding "
                f"declares {spec.get('reuse_map_entries')!r}; refusing"
            )
            raise PreflightError(msg)
        if len(reuse) != mapping.get("entries"):
            msg = (
                f"{kind}: the reuse map holds {len(reuse):,} entries, the verified "
                f"resolution declares {mapping.get('entries')!r}; refusing"
            )
            raise PreflightError(msg)
        sources[kind] = FeatureSource(
            kind=kind,
            accepted_path=spec["accepted_path"],
            accepted_sha256=spec["accepted_sha256"],
            reuse_map=reuse,
            extension_path=spec.get("extension_path"),
            extension_sha256=spec.get("extension_sha256"),
            reuse_map_path=mapping["path"],
            reuse_map_sha256=mapping["sha256"],
        )
    return sources


def derive_roles(artifacts: dict[str, str | Path]) -> dict[str, RoleEntities]:
    """Derive every role's required entities from the pinned artifacts.

    The fitting roles come from the verified membership itself, counted over
    exactly the rows the loader will keep -- so a role cannot under-declare and
    turn fitting rows into an exclusion count. The evaluation role comes from the
    pinned `evaluation_entities` artifact. All three are required.
    """
    membership = Path(artifacts[DERIVED_FROM["membership"]])
    roles: dict[str, dict[str, set[str]]] = {
        TRAIN_ROLE: {"compounds": set(), "sequences": set()},
        VALIDATION_ROLE: {"compounds": set(), "sequences": set()},
    }
    with membership.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            role = rec["partition"]
            if role not in roles or not rec.get("regression_eligible"):
                continue
            if role == VALIDATION_ROLE and not rec.get("validation_rmse_eligible"):
                continue
            compound, sequence = rec["pair"].split("|", 1)
            roles[role]["compounds"].add(compound)
            roles[role]["sequences"].add(sequence)

    evaluation = _read_json(artifacts, DERIVED_FROM["evaluation_entities"])
    for key in ("compounds", "sequences"):
        if not isinstance(evaluation.get(key), list) or not evaluation[key]:
            msg = f"the pinned evaluation entities must list {key!r}; refusing"
            raise PreflightError(msg)
    out = {
        TRAIN_ROLE: RoleEntities(**roles[TRAIN_ROLE]),
        VALIDATION_ROLE: RoleEntities(**roles[VALIDATION_ROLE]),
        EVALUATION_ROLE: RoleEntities(
            compounds=set(evaluation["compounds"]), sequences=set(evaluation["sequences"])
        ),
    }
    missing = sorted(r for r, e in out.items() if not e.compounds or not e.sequences)
    if missing:
        msg = f"no entities could be derived for the roles {missing}; refusing before fitting"
        raise PreflightError(msg)
    return out


def verify_bindings_and_coverage(
    sources: dict[str, FeatureSource],
    roles: dict[str, RoleEntities],
) -> dict[str, Any]:
    """Load every binding, then require complete coverage for every role.

    Coverage is checked per role because the roles are reached by different
    paths and a shortfall in one says nothing about another. Missing and
    unusable are counted apart and either refuses the run: nothing is imputed,
    so fitting on a silently reduced cohort would change the thing being
    measured without changing the reported figures.
    """
    required = {TRAIN_ROLE, VALIDATION_ROLE, EVALUATION_ROLE}
    if set(roles) != required:
        msg = f"coverage must be checked for exactly {sorted(required)}, got {sorted(roles)}"
        raise PreflightError(msg)

    bindings = {}
    for kind, source in sorted(sources.items()):
        try:
            bindings[kind] = load_binding(
                kind,
                accepted_path=source.accepted_path,
                accepted_sha256=source.accepted_sha256,
                reuse_map=source.reuse_map,
                stored_dim=STORED_DIMS[kind],
                extension_path=source.extension_path,
                extension_sha256=source.extension_sha256,
            )
        except BindingError as exc:
            msg = f"feature binding refused, so the run is refused: {exc}"
            raise PreflightError(msg) from exc

    coverage, incomplete = {}, []
    for role, wanted in sorted(roles.items()):
        for kind in sorted(STORED_DIMS):
            keys = getattr(wanted, KIND_ENTITY[kind])
            cov = bindings[kind].coverage(role, keys)
            coverage[f"{role}/{kind}"] = cov.as_dict()
            if not cov.complete:
                incomplete.append(
                    f"{role}/{kind}: {len(cov.missing)} missing, {len(cov.unusable)} unusable"
                )
    if incomplete:
        msg = (
            "feature coverage is incomplete and nothing is imputed, so the run is refused "
            "before fitting -- " + "; ".join(incomplete)
        )
        raise PreflightError(msg)
    return {
        "stored_dims_verified": dict(sorted(STORED_DIMS.items())),
        "reuse_maps_verified": {
            kind: {
                "path": str(s.reuse_map_path),
                "sha256": s.reuse_map_sha256,
                "entries": len(s.reuse_map),
            }
            for kind, s in sorted(sources.items())
        },
        "coverage": coverage,
        "nothing_imputed": True,
    }


def load_verified_datasets(
    artifacts: dict[str, str | Path],
    expected_digests: dict[str, str],
    roles: dict[str, RoleEntities],
) -> tuple[Any, Any]:
    """Re-verify the membership's bytes, then load both roles from that file.

    Re-verified at the moment of consumption rather than trusted from the gate:
    a digest checked earlier does not protect a file that changed since, and the
    file loaded here is the artifact itself rather than a path supplied beside it.
    """
    name = DERIVED_FROM["membership"]
    path = Path(artifacts[name])
    actual = _sha256(path)
    if actual != expected_digests[name]:
        msg = (
            f"{name} changed between verification and use: {actual[:16]}… is not the "
            f"bound {expected_digests[name][:16]}…; refusing before fitting"
        )
        raise PreflightError(msg)
    return (
        load_dataset(
            path,
            TRAIN_ROLE,
            usable_compounds=roles[TRAIN_ROLE].compounds,
            usable_sequences=roles[TRAIN_ROLE].sequences,
        ),
        load_dataset(
            path,
            VALIDATION_ROLE,
            usable_compounds=roles[VALIDATION_ROLE].compounds,
            usable_sequences=roles[VALIDATION_ROLE].sequences,
        ),
    )


def check_against_pinned_datasets(
    train: Any, validation: Any, artifacts: dict[str, str | Path]
) -> dict[str, Any]:
    """The emitted datasets must be the pinned ones, by count AND by digest.

    A digest alone would be enough to detect a change, and the counts are
    compared too so a mismatch says which way it moved rather than only that it
    did. This is the gate a substituted membership or an under-declared role
    cannot pass: both change what is emitted.
    """
    pinned = _read_json(artifacts, DERIVED_FROM["emitted_datasets"])
    faults, report = [], {}
    for role, dataset in ((TRAIN_ROLE, train), (VALIDATION_ROLE, validation)):
        want = pinned.get(role)
        if not isinstance(want, dict):
            faults.append(f"{role}: the pinned record declares no dataset")
            continue
        got = {
            "pairs": len(dataset),
            "distinct_compounds": len(dataset.compounds),
            "distinct_sequences": len(dataset.sequences),
            "digest": dataset.digest(),
        }
        report[role] = got
        for key, value in got.items():
            if key in want and want[key] != value:
                faults.append(f"{role}.{key}: emitted {value!r}, pinned {want[key]!r}")
    if faults:
        msg = (
            "the emitted datasets are not the pinned ones, so the run is refused before "
            "fitting -- " + "; ".join(faults)
        )
        raise PreflightError(msg)
    return report


def run(
    *,
    expected_digests: dict[str, str],
    artifacts: dict[str, str | Path],
    config: dict[str, Any],
    fit_transforms: Callable[[Any], Any],
    fit_model: Callable[[Any, Any], Any],
    select_checkpoint: Callable[[Any, Any], Any],
) -> dict[str, Any]:
    """Validate everything, then hand A-train to the fit and A-validation to selection.

    The order is the guarantee, and so is the absence of parameters: everything
    the run consumes is derived from the bound artifact set, so there is nothing
    to substitute. A failure at any gate raises, and nothing is fitted and
    nothing is published.
    """
    validate_settings(config)
    verified = validate_inputs(expected_digests, artifacts)
    sources = derive_feature_sources(artifacts)
    roles = derive_roles(artifacts)
    features = verify_bindings_and_coverage(sources, roles)
    train, validation = load_verified_datasets(artifacts, expected_digests, roles)
    emitted = check_against_pinned_datasets(train, validation, artifacts)

    outcome = prepare_fitting(
        train,
        validation,
        fit_transforms=fit_transforms,
        fit_model=fit_model,
        select_checkpoint=select_checkpoint,
        expected_digests=expected_digests,
        artifacts=artifacts,
        config=config,
    )
    return {
        "runner_version": RUNNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "settings_validated": sorted(PINNED_SETTINGS),
        "inputs_verified": verified,
        "inputs_required": sorted(REQUIRED_DIGESTS),
        "derived_from": dict(sorted(DERIVED_FROM.items())),
        "roles_derived": {
            role: {"compounds": len(e.compounds), "sequences": len(e.sequences)}
            for role, e in sorted(roles.items())
        },
        "features": features,
        "emitted_matches_pinned": emitted,
        **outcome,
    }
