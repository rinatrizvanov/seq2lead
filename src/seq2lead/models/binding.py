"""What a checkpoint must know about the features it was trained on.

A model is only meaningful against the representation it saw. An ECFP4 cache
built with different chirality settings, or an ESM-2 embedding taken with a
different pooling rule, produces numerically valid inputs that mean something
else -- and nothing about the weights reveals the substitution.

The first M9 checkpoints recorded none of this, and `rank_library()` fell back to
"whatever ECFP4 cache is current", so a newer cache would have been picked up
silently. This binds each checkpoint to the exact feature identities it was
trained against, and inference refuses to run without them.

For the 20 checkpoints already fitted, the binding is **recovered from the
verified experiment record** rather than re-derived from whatever is current, and
is stored in a versioned sidecar so the original checkpoint bytes and digests
stay exactly as they were.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

    from seq2lead.eval.config import ExperimentConfig

BINDING_VERSION = "m9-binding-v1"
SIDECAR_SUFFIX = ".binding.json"
SIDECAR_VERSION = "m9-sidecar-v1"


class BindingError(RuntimeError):
    """A checkpoint's feature binding is missing, incomplete or incompatible."""


@dataclass(frozen=True)
class ProteinSpec:
    """Everything about how the protein side was represented."""

    model: str
    model_revision: str
    pooling: str
    dtype: str
    length_policy: str
    max_length: int
    training_window: int


@dataclass(frozen=True)
class FeatureBinding:
    """The feature identities a checkpoint was trained against."""

    binding_version: str
    compound_cache: str
    compound_manifest_sha256: str
    compound_storage_sha256: str
    protein_cache: str
    protein_manifest_sha256: str
    protein_storage_sha256: str
    protein: ProteinSpec
    experiment: str
    config_sha256: str
    provenance: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["protein"] = asdict(self.protein)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FeatureBinding:
        data = dict(payload)
        protein = data.pop("protein")
        return cls(protein=ProteinSpec(**protein), **data)


def from_experiment(
    conn: psycopg.Connection, config: ExperimentConfig, provenance: str = ""
) -> FeatureBinding:
    """Build a binding from the pinned experiment config and the cache registry."""
    ecfp = config.caches["ecfp4"]
    esm = config.caches["esm2"]
    spec = conn.execute("SELECT params FROM feature_version WHERE name=%s", (esm.name,)).fetchone()
    params = json.loads(spec[0]) if isinstance(spec[0], str) else (spec[0] or {})
    return FeatureBinding(
        binding_version=BINDING_VERSION,
        compound_cache=ecfp.name,
        compound_manifest_sha256=ecfp.manifest_sha256,
        compound_storage_sha256=ecfp.storage_sha256,
        protein_cache=esm.name,
        protein_manifest_sha256=esm.manifest_sha256,
        protein_storage_sha256=esm.storage_sha256,
        protein=protein_spec_from_params(esm.name, params),
        experiment=config.version,
        config_sha256=config.config_sha256,
        provenance=provenance,
    )


#: Fields the protein cache must record. Falling back to the module constants
#: would describe *today's* defaults rather than what the cache was built with,
#: which is the substitution this module exists to prevent.
REQUIRED_PROTEIN_PARAMS = (
    "model",
    "model_revision",
    "pooling",
    "dtype",
    "length_policy",
    "max_length",
)


def protein_spec_from_params(cache_name: str, params: dict[str, Any]) -> ProteinSpec:
    """Read the representation spec a cache recorded. Missing fields are an error."""
    missing = [k for k in REQUIRED_PROTEIN_PARAMS if params.get(k) in (None, "")]
    if missing:
        raise BindingError(
            f"protein cache {cache_name!r} records no {missing} in its spec. Refusing "
            "to substitute the current module defaults: they describe today's "
            "settings, not what this cache was built with."
        )
    window = (params.get("extra") or {}).get("training_window")
    if window is None:
        raise BindingError(
            f"protein cache {cache_name!r} records no training_window; it is needed to "
            "decide which queries are flagged as extrapolated."
        )
    return ProteinSpec(
        model=str(params["model"]),
        model_revision=str(params["model_revision"]),
        pooling=str(params["pooling"]),
        dtype=str(params["dtype"]),
        length_policy=str(params["length_policy"]),
        max_length=int(params["max_length"]),
        training_window=int(window),
    )


def sidecar_path(checkpoint: Path) -> Path:
    return checkpoint.with_suffix(checkpoint.suffix + SIDECAR_SUFFIX)


def write_sidecar(checkpoint: Path, binding: FeatureBinding, *, provenance: str = "") -> Path:
    """Store a binding beside a checkpoint, bound to that checkpoint's bytes.

    The envelope carries the checkpoint's SHA-256. Without it a sidecar is just a
    file with a matching name: copy it next to a different checkpoint and that
    checkpoint silently inherits a binding describing features it never saw.

    An identical rewrite is a no-op; a conflicting one is refused, because
    overwriting would erase the record of what the checkpoint was actually
    trained against.
    """
    from seq2lead.features.store import sha256_file

    path = sidecar_path(checkpoint)
    envelope = {
        "sidecar_version": SIDECAR_VERSION,
        "checkpoint_name": checkpoint.name,
        "checkpoint_sha256": sha256_file(checkpoint),
        "binding": binding.to_dict(),
        "provenance": provenance or binding.provenance,
    }
    body = json.dumps(envelope, indent=2, sort_keys=True) + "\n"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == body:
            return path
        raise BindingError(
            f"{path.name} already exists with different content. Refusing to "
            "overwrite: it records what this checkpoint was trained against, and "
            "replacing it would erase that."
        )
    path.write_text(body, encoding="utf-8")
    return path


def read_sidecar(checkpoint: Path) -> tuple[FeatureBinding, dict[str, Any]]:
    """Read a sidecar and confirm it belongs to *this* checkpoint."""
    from seq2lead.features.store import sha256_file

    path = sidecar_path(checkpoint)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "binding" not in payload:  # pre-envelope sidecar
        raise BindingError(
            f"{path.name} predates the sidecar envelope and is not bound to any "
            "checkpoint. Rewrite it with `write_sidecar` so it records the "
            "checkpoint digest it belongs to."
        )
    recorded = payload.get("checkpoint_sha256")
    actual = sha256_file(checkpoint)
    if recorded != actual:
        raise BindingError(
            f"{path.name} was written for a checkpoint with digest "
            f"{str(recorded)[:16]}…, but {checkpoint.name} hashes to {actual[:16]}…. "
            "A sidecar copied beside a different checkpoint would hand it a binding "
            "for features it never saw."
        )
    return FeatureBinding.from_dict(payload["binding"]), payload


def load_binding(checkpoint: Path, extra: dict[str, Any] | None = None) -> FeatureBinding:
    """From the checkpoint itself, else its sidecar. Absent is an error, not a default.

    A binding embedded in the checkpoint is self-evidently about that checkpoint.
    A sidecar is not, so it is checked against the checkpoint's digest, and its
    experiment and config identity are cross-checked against what the checkpoint
    itself recorded.
    """
    if extra and extra.get("feature_binding"):
        return FeatureBinding.from_dict(extra["feature_binding"])
    path = sidecar_path(checkpoint)
    if not path.exists():
        raise BindingError(
            f"{checkpoint.name} records no feature binding and has no sidecar at "
            f"{path.name}. Inference is refused: without it, the caches used at "
            "scoring time are whatever happens to be current, which may not be what "
            "this model was trained on."
        )
    binding, _envelope = read_sidecar(checkpoint)
    if extra:
        for field_name, recorded in (
            ("experiment", extra.get("experiment")),
            ("config_digest", extra.get("config_digest")),
        ):
            expected = binding.experiment if field_name == "experiment" else binding.config_sha256
            if recorded is not None and expected and recorded != expected:
                raise BindingError(
                    f"{path.name} records {field_name} {expected!r}, but "
                    f"{checkpoint.name} itself records {recorded!r}. The sidecar "
                    "describes a different run."
                )
    return binding


def verify(conn: psycopg.Connection, binding: FeatureBinding) -> list[str]:
    """Check the bound caches still exist with the recorded digests.

    Returns the notes worth surfacing. Raises on anything that makes the
    checkpoint unusable rather than merely worth mentioning.
    """
    from seq2lead.features.store import sha256_file

    notes: list[str] = []
    for label, name, manifest, storage in (
        (
            "compound",
            binding.compound_cache,
            binding.compound_manifest_sha256,
            binding.compound_storage_sha256,
        ),
        (
            "protein",
            binding.protein_cache,
            binding.protein_manifest_sha256,
            binding.protein_storage_sha256,
        ),
    ):
        row = conn.execute(
            "SELECT manifest_sha256, storage_sha256, storage_path, superseded_by "
            "FROM feature_version WHERE name=%s",
            (name,),
        ).fetchone()
        if row is None:
            raise BindingError(
                f"the {label} cache this checkpoint was trained on ({name!r}) is not "
                "registered any more. Refusing rather than substituting another."
            )
        if str(row[0]) != manifest:
            raise BindingError(
                f"the {label} cache {name!r} now has manifest digest {str(row[0])[:16]}…, "
                f"the checkpoint was trained against {manifest[:16]}…. It covers "
                "different inputs; refusing."
            )
        path = Path(str(row[2]))
        if not path.exists():
            raise BindingError(f"{label} cache {name!r} is registered but {path} is gone")
        actual = sha256_file(path)
        if actual != storage:
            raise BindingError(
                f"the {label} cache {name!r} bytes changed since this checkpoint was "
                f"trained ({actual[:16]}… vs {storage[:16]}…); refusing."
            )
        if row[3]:
            notes.append(
                f"the {label} cache {name!r} has been superseded by {row[3]!r}; this "
                "checkpoint is still scored against the cache it was trained on"
            )

    # Digests prove the *vectors* are the ones this model saw. They say nothing
    # about how a NEW query will be represented, which is decided by the spec --
    # so a binding with correct digests but a different pooling rule would score
    # the library correctly and embed the query wrongly.
    row = conn.execute(
        "SELECT params FROM feature_version WHERE name=%s", (binding.protein_cache,)
    ).fetchone()
    params = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    recorded = protein_spec_from_params(binding.protein_cache, params)
    differences = [
        f"{field}: binding says {getattr(binding.protein, field)!r}, cache records "
        f"{getattr(recorded, field)!r}"
        for field in (
            "model",
            "model_revision",
            "pooling",
            "dtype",
            "length_policy",
            "max_length",
            "training_window",
        )
        if getattr(binding.protein, field) != getattr(recorded, field)
    ]
    if differences:
        raise BindingError(
            "the query representation this binding declares does not match what the "
            f"bound protein cache {binding.protein_cache!r} records:\n  - "
            + "\n  - ".join(differences)
            + "\nThe query would be embedded differently from the library; refusing."
        )
    return notes
