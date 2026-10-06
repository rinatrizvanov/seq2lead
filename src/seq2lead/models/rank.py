"""Sequence in, ranked library out.

The query protein is embedded with the **pinned** ESM-2 checkpoint under the
recorded length policy, standardised with the **frozen** training transform from
the checkpoint, and projected. The library is projected once and reused -- the
property the dual encoder's independent towers exist to provide.

Two things this deliberately does not do:

- It does not produce a probability. The score is in pKi units from an affine
  map on cosine. Calling it a confidence would require a reliability diagram and
  an ECE, neither of which exists.
- It does not present known measurements as validation. Prior evidence is
  labelled as prior evidence and kept in its own column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import psycopg

RESIDUES = set("ACDEFGHIKLMNPQRSTVWYBXZUO")


from seq2lead.models.binding import BindingError  # noqa: E402


class SequenceError(ValueError):
    """The query sequence is not usable."""


@dataclass
class RankedCompound:
    rank: int
    compound_id: int
    smiles: str
    score_pki: float
    flags: list[str] = field(default_factory=list)
    prior_evidence: str | None = None


@dataclass
class RankingResult:
    sequence_length: int
    sequence_sha256: str
    library: str
    library_members: int
    model: str
    projection_dim: int
    over_training_window: bool
    rows: list[RankedCompound] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    binding: Any = None


def read_fasta(path: Path) -> tuple[str, str]:
    """First record of a FASTA file. Returns (header, sequence)."""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise SequenceError(f"{path} is empty")
    header, sequence = "", []
    for line in text.splitlines():
        if line.startswith(">"):
            if sequence:
                break
            header = line[1:].strip()
            continue
        sequence.append(line.strip())
    joined = "".join(sequence)
    if not joined:
        raise SequenceError(f"{path} contains no sequence data")
    return header, joined


def validate_sequence(
    sequence: str, training_window: int = 1022, max_length: int | None = None
) -> tuple[str, list[str]]:
    """Uppercase, strip whitespace, reject non-residues and over-length input.

    `max_length` is the checkpoint's recorded **refusal point**, not a truncation
    point: a sequence past it is rejected outright. Silently keeping a prefix
    would return a ranking for a different protein than the one supplied.
    """
    cleaned = re.sub(r"\s+", "", sequence).upper()
    if not cleaned:
        raise SequenceError("sequence is empty after stripping whitespace")
    illegal = sorted(set(cleaned) - RESIDUES)
    if illegal:
        raise SequenceError(
            f"sequence contains characters that are not amino-acid codes: {illegal[:8]}"
        )
    if max_length is not None and len(cleaned) > max_length:
        raise SequenceError(
            f"sequence is {len(cleaned):,} residues, past the {max_length:,}-residue "
            "refusal point recorded for this checkpoint. It is refused rather than "
            "truncated: scoring a prefix would answer a question about a different "
            "protein."
        )
    notes: list[str] = []
    if len(cleaned) > training_window:
        notes.append(
            f"sequence is {len(cleaned):,} residues, past ESM-2's {training_window:,}-residue "
            "pre-training window. It is embedded in full under the recorded `full` policy; "
            "representation quality beyond that window is untested."
        )
    return cleaned, notes


SUPPORTED_POOLING = "mean_over_residues_excluding_special_tokens"
SUPPORTED_DTYPES = {"float32"}


def embed_sequence(sequence: str, spec, device: str | None = None) -> np.ndarray:
    """Embed using the **checkpoint's** recorded settings, not module defaults.

    Taking the current module constants would silently re-represent the query if
    the defaults ever moved, which is exactly the substitution this whole binding
    exists to prevent.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    from seq2lead.features.plm import _resolve_device

    if spec.pooling != SUPPORTED_POOLING:
        raise BindingError(
            f"this checkpoint records pooling {spec.pooling!r}, which this code does "
            f"not implement (only {SUPPORTED_POOLING!r}). Refusing rather than "
            "substituting a different pooling rule."
        )
    if spec.dtype not in SUPPORTED_DTYPES:
        raise BindingError(
            f"this checkpoint records dtype {spec.dtype!r}; supported: {sorted(SUPPORTED_DTYPES)}"
        )
    if spec.length_policy != "full":
        raise BindingError(
            f"this checkpoint records length policy {spec.length_policy!r}; only 'full' "
            "is implemented here, and guessing would change what the query means."
        )

    resolved = _resolve_device(device)
    tokenizer = AutoTokenizer.from_pretrained(spec.model, revision=spec.model_revision)
    model = (
        AutoModel.from_pretrained(spec.model, revision=spec.model_revision, dtype=torch.float32)
        .eval()
        .to(resolved)
    )
    encoded = {k: v.to(resolved) for k, v in tokenizer(sequence, return_tensors="pt").items()}
    with torch.no_grad():
        hidden = model(**encoded).last_hidden_state[0]
    return hidden[1:-1].mean(dim=0).float().cpu().numpy()


def rank_library(
    conn: psycopg.Connection,
    sequence: str,
    checkpoint: Path,
    library_name: str,
    top_k: int = 50,
    device: str | None = None,
    show_evidence: bool = False,
) -> RankingResult:
    """Rank a frozen library against one sequence, bound to the checkpoint's features.

    Order matters here. Everything that can refuse does so **before** ESM-2 is
    loaded or a single vector is read: an over-length query or a broken binding
    should cost nothing.
    """
    import hashlib

    import torch

    from seq2lead.features.store import load_features
    from seq2lead.models.binding import load_binding, verify
    from seq2lead.models.dual_encoder import load_checkpoint
    from seq2lead.models.library import load as load_library

    # ---- 1. binding first: no binding, no inference. No "current cache" fallback.
    model, extra = load_checkpoint(checkpoint, device="cpu")
    binding = load_binding(checkpoint, extra)
    binding_notes = verify(conn, binding)

    # ---- 2. the query, against the checkpoint's own refusal point
    cleaned, notes = validate_sequence(
        sequence, binding.protein.training_window, binding.protein.max_length
    )

    # ---- 3. the library and its features, from the bound cache only
    library, members = load_library(conn, library_name)
    # `allow_superseded` because `verify()` has already confirmed this is the exact
    # cache -- same manifest, same bytes -- that the checkpoint was trained on. The
    # supersession flag says a newer cache exists, not that this one is wrong, and
    # refusing here would make `verify()`'s "still scored against the cache it was
    # trained on" unreachable.
    ids, packed = load_features(conn, binding.compound_cache, allow_superseded=True)
    index = {int(v): i for i, v in enumerate(ids)}
    absent = [c for c in members if c not in index]
    if absent:
        raise LookupError(
            f"{len(absent):,} library compounds are absent from the bound compound "
            f"cache {binding.compound_cache!r}; the library and the cache this model "
            "was trained on do not correspond"
        )
    rows = np.fromiter((index[c] for c in members), dtype=np.int64, count=len(members))
    fingerprints = np.unpackbits(packed[rows], axis=1)[:, :2048].astype(np.float32)

    # ---- 4. only now is the language model loaded
    embedding = embed_sequence(cleaned, binding.protein, device)
    standardised = model.transform.apply(embedding.reshape(1, -1))

    with torch.no_grad():
        compound_z = model.project_compounds(torch.from_numpy(fingerprints))
        scores = (
            model.score_library(compound_z, torch.from_numpy(standardised.reshape(-1)))
            .cpu()
            .numpy()
        )

    # Ranking is by the affine score -- the predicted pKi -- never raw cosine: the
    # learned scale is unconstrained and a negative one reverses the ordering.
    order = np.argsort(-scores, kind="mergesort")[: min(top_k, len(members))]
    top_ids = [members[int(i)] for i in order]
    smiles = _smiles_for(conn, top_ids)
    flagged = _unusable(conn, binding.compound_cache)
    evidence = _prior_evidence(conn, top_ids, cleaned) if show_evidence else {}

    result = RankingResult(
        sequence_length=len(cleaned),
        sequence_sha256=hashlib.sha256(cleaned.encode("ascii")).hexdigest(),
        library=library.name,
        library_members=library.n_members,
        model=str(checkpoint),
        projection_dim=model.config.projection_dim,
        over_training_window=len(cleaned) > binding.protein.training_window,
        notes=notes + binding_notes,
        binding=binding,
    )
    for rank, position in enumerate(order, start=1):
        compound_id = members[int(position)]
        result.rows.append(
            RankedCompound(
                rank=rank,
                compound_id=compound_id,
                smiles=smiles.get(compound_id, ""),
                score_pki=float(scores[int(position)]),
                flags=["unusable_fingerprint"] if compound_id in flagged else [],
                prior_evidence=evidence.get(compound_id),
            )
        )
    return result


def _smiles_for(conn: psycopg.Connection, compound_ids: list[int]) -> dict[int, str]:
    return {
        int(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT id, canonical_smiles FROM compound WHERE id = ANY(%s)", (compound_ids,)
        ).fetchall()
    }


def _unusable(conn: psycopg.Connection, cache_name: str) -> set[int]:
    return {
        int(r[0])
        for r in conn.execute(
            "SELECT f.entity_id FROM feature_entity_flag f "
            "JOIN feature_version v ON v.id = f.feature_id "
            "WHERE v.name=%s AND f.flag='unparseable_smiles'",
            (cache_name,),
        ).fetchall()
    }


def format_evidence(records: list[dict[str, Any]]) -> str:
    """Render measured evidence without flattening censoring into a point value.

    The first version aggregated `min(value)`-`max(value)` across every record,
    dropping the relation. A pair measured only as `>10000 nM` -- a decisive
    *non*-binder -- then displayed as `10000 nM`, which reads as an exact and
    rather good affinity. Exact observations and censored bounds are different
    kinds of statement and are kept apart here.
    """
    exact = [r for r in records if r["relation"] == "="]
    censored = [r for r in records if r["relation"] != "="]
    parts: list[str] = []
    if exact:
        values = [r["value"] for r in exact]
        low, high = min(values), max(values)
        span = f"{low:g}" if low == high else f"{low:g}-{high:g}"
        parts.append(f"{len(exact)} exact {span} nM")
    for record in sorted(censored, key=lambda r: (r["relation"], r["value"])):
        parts.append(f"{record['relation']}{record['value']:g} nM")
    kinds = sorted({r["measurement_type"] for r in records})
    prefix = "/".join(kinds) + " " if kinds != ["KI"] else ""
    return prefix + "; ".join(parts)


def _prior_evidence(
    conn: psycopg.Connection, compound_ids: list[int], sequence: str
) -> dict[int, str]:
    """Measured evidence for these compounds against this exact sequence.

    **Demo only, and context rather than validation.** A compound appearing here
    was measured by someone else; the model was not asked to discover it, and the
    measurement may have been in its training partition.

    Relation, measurement type and units are preserved, and the supporting
    `activity` ids are carried so any line can be traced back to its source rows.
    """
    rows = conn.execute(
        """
        SELECT a.compound_id, a.id, a.measurement_type, a.relation, a.value_numeric,
               a.value_unit
        FROM activity a JOIN target t ON t.id = a.target_id
        WHERE t.sequence = %s AND a.compound_id = ANY(%s) AND a.value_numeric IS NOT NULL
        ORDER BY a.compound_id, a.id
        """,
        (sequence, compound_ids),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for compound_id, activity_id, kind, relation, value, unit in rows:
        grouped.setdefault(int(compound_id), []).append(
            {
                "activity_id": int(activity_id),
                "measurement_type": str(kind),
                "relation": str(relation),
                "value": float(value),
                "unit": str(unit),
            }
        )
    return {cid: format_evidence(records) for cid, records in grouped.items()}


def evidence_records(
    conn: psycopg.Connection, compound_ids: list[int], sequence: str
) -> dict[int, list[dict[str, Any]]]:
    """The same evidence, unformatted, with activity ids for traceability."""
    rows = conn.execute(
        """
        SELECT a.compound_id, a.id, a.measurement_type, a.relation, a.value_numeric,
               a.value_unit, a.raw_measurement_id
        FROM activity a JOIN target t ON t.id = a.target_id
        WHERE t.sequence = %s AND a.compound_id = ANY(%s) AND a.value_numeric IS NOT NULL
        ORDER BY a.compound_id, a.id
        """,
        (sequence, compound_ids),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for compound_id, activity_id, kind, relation, value, unit, raw in rows:
        grouped.setdefault(int(compound_id), []).append(
            {
                "activity_id": int(activity_id),
                "raw_measurement_id": int(raw) if raw is not None else None,
                "measurement_type": str(kind),
                "relation": str(relation),
                "value": float(value),
                "unit": str(unit),
            }
        )
    return grouped


def render(result: RankingResult, evidence_mode: str) -> str:
    lines = [
        f"query: {result.sequence_length:,} residues  sha256 {result.sequence_sha256[:16]}…",
        f"library: {result.library} ({result.library_members:,} compounds, frozen)",
        f"model: {result.model}  projection_dim={result.projection_dim}",
        "",
        "Scores are predicted pKi from an affine map on cosine similarity.",
        "They are NOT probabilities and NOT calibrated confidences.",
        "No compound below has been experimentally tested by this project.",
        "",
    ]
    for note in result.notes:
        lines.append(f"note: {note}")
    if result.notes:
        lines.append("")
    header = f"{'rank':>5}  {'compound':>9}  {'pred pKi':>9}  {'flags':<22}  smiles"
    if evidence_mode == "demo":
        header = (
            f"{'rank':>5}  {'compound':>9}  {'pred pKi':>9}  "
            f"{'prior evidence':<26}  {'flags':<20}  smiles"
        )
    lines.append(header)
    lines.append("-" * len(header))
    for row in result.rows:
        flags = ",".join(row.flags) or "-"
        if evidence_mode == "demo":
            lines.append(
                f"{row.rank:>5}  {row.compound_id:>9}  {row.score_pki:>9.3f}  "
                f"{(row.prior_evidence or '-'):<26}  {flags:<20}  {row.smiles[:56]}"
            )
        else:
            lines.append(
                f"{row.rank:>5}  {row.compound_id:>9}  {row.score_pki:>9.3f}  "
                f"{flags:<22}  {row.smiles[:64]}"
            )
    if evidence_mode == "demo":
        lines += [
            "",
            "`prior evidence` is measured data already in the database. It is shown for",
            "context and is NOT independent validation of this ranking: the model was not",
            "asked to discover those measurements, and they may have been in its training",
            "partition.",
        ]
    return "\n".join(lines) + "\n"
