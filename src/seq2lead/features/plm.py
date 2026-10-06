"""ESM-2 embeddings for every curated target sequence.

Input-only, like the fingerprints: an embedding is a function of the sequence
and reads no measured activity, so it may be computed for held-out targets.

**On the "1,022-residue limit".** It is widely repeated and, for these
checkpoints, not a hard limit. HF's ESM-2 configs set
`position_embedding_type='rotary'`, so `max_position_embeddings=1026` never
indexes a learned table and nothing raises on a longer input; measured on this
corpus, sequences of 1,023 through 34,350 residues all embed successfully.

What is real is that ESM-2 was pre-trained on crops of 1,024 tokens, so past
that the rotary embedding is extrapolating beyond anything it saw in training.
That is a soft quality question, not a hard failure, and the policy below is
chosen on measured evidence rather than on the folklore.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

from seq2lead.features.identity import FeatureSpec
from seq2lead.features.manifest import build_manifest, library_versions
from seq2lead.features.store import FeatureStore

if TYPE_CHECKING:
    import psycopg

    from seq2lead.features.manifest import InputManifest

MODEL = "facebook/esm2_t33_650M_UR50D"
#: The commit, not the tag. Tags move; a benchmark pinned to one must not.
MODEL_REVISION = "08e4846e537177426273712802403f7ba8261b6c"
DIM = 1280

#: The window ESM-2 was pre-trained on. Not a ceiling -- a regime boundary.
TRAINING_WINDOW = 1022

#: **Policy: `full`, and provisional.** Every residue is embedded in a single
#: forward pass, with no truncation and no chunking. See `docs/FEATURES.md` for
#: the evidence and its limits: the probes establish that nothing fails and that
#: a shared prefix moves little at 1,208-2,549 residues. They do **not**
#: establish representation quality at 34,350 residues, and no downstream task
#: has been scored either way. Targets past the training window are flagged so
#: the policy can be revisited against a metric rather than a vibe.
LENGTH_POLICY = "full"

#: Policies this builder implements. Anything else is refused rather than
#: silently treated as truncation -- a typo in a policy string must not quietly
#: produce a differently-built cache under a name that claims otherwise.
SUPPORTED_LENGTH_POLICIES = ("full", "truncate")

#: A refusal point, not a truncation point. Nothing in this corpus reaches it;
#: it exists so a future release with a pathological entry fails loudly instead
#: of silently embedding something meaningless.
MAX_LENGTH = 40_000

POOLING = "mean_over_residues_excluding_special_tokens"


def parse_length_policy(policy: str) -> tuple[str, int | None]:
    """`full` or `truncate:<n>`. Anything else raises."""
    kind, _, value = policy.partition(":")
    if kind == "full" and not value:
        return "full", None
    if kind == "truncate":
        if not value.isdigit() or int(value) <= 0:
            raise ValueError(
                f"length policy {policy!r} must name a positive residue count, e.g. 'truncate:1022'"
            )
        return "truncate", int(value)
    raise ValueError(
        f"unsupported length policy {policy!r}. Supported: "
        f"{', '.join(SUPPORTED_LENGTH_POLICIES)}. Refusing rather than guessing: "
        "silently treating an unknown policy as truncation would build a cache "
        "whose name claims a policy it did not follow."
    )


def esm_spec(
    model: str = MODEL,
    revision: str = MODEL_REVISION,
    length_policy: str = LENGTH_POLICY,
    max_length: int = MAX_LENGTH,
) -> FeatureSpec:
    parse_length_policy(length_policy)
    return FeatureSpec(
        kind="esm2",
        entity="target",
        model=model,
        model_revision=revision,
        pooling=POOLING,
        length_policy=length_policy,
        max_length=max_length,
        dtype="float32",
        extra={"training_window": TRAINING_WINDOW},
    )


def _resolve_device(device: str | None) -> str:
    import torch

    if device:
        return device
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def build_target_features(
    conn: psycopg.Connection,
    population: str,
    device: str | None = None,
    length_policy: str = LENGTH_POLICY,
    max_length: int = MAX_LENGTH,
    limit: int | None = None,
    progress_every: int = 250,
) -> tuple[FeatureStore, InputManifest, list[int]]:
    """Embed every curated target. Sequences past the training window are flagged.

    The flag is the point: it costs nothing, and it lets every downstream score
    be reported with and without the targets whose embedding was extrapolated.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    started = time.perf_counter()
    spec = esm_spec(length_policy=length_policy, max_length=max_length)
    policy_kind, policy_length = parse_length_policy(length_policy)
    manifest, entity_ids = build_manifest(conn, "target", population, limit=limit)
    resolved = _resolve_device(device)

    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=MODEL_REVISION)
    model = (
        AutoModel.from_pretrained(MODEL, revision=MODEL_REVISION, dtype=torch.float32)
        .eval()
        .to(resolved)
    )

    rows = conn.execute(
        "SELECT id, sequence FROM target WHERE id = ANY(%s) ORDER BY length(sequence), id",
        (entity_ids,),
    ).fetchall()

    ids = np.zeros(len(rows), dtype=np.int64)
    vectors = np.zeros((len(rows), DIM), dtype=np.float32)
    flags: dict[int, list[tuple[str, dict]]] = {}

    for written, (target_id, sequence) in enumerate(rows):
        length = len(sequence)
        if length > max_length:
            raise ValueError(
                f"target {target_id} is {length:,} residues, past the {max_length:,} "
                "refusal point. Decide a policy for it rather than embedding it blindly."
            )
        text = sequence if policy_kind == "full" else sequence[:policy_length]
        encoded = {k: v.to(resolved) for k, v in tokenizer(text, return_tensors="pt").items()}
        with torch.no_grad():
            hidden = model(**encoded).last_hidden_state[0]
        # Drop BOS/EOS before pooling: they are not residues, and including them
        # makes the mean depend on sequence length in a way nothing else does.
        pooled = hidden[1:-1].mean(dim=0).float().cpu().numpy()

        ids[written] = int(target_id)
        vectors[written] = pooled
        if length > TRAINING_WINDOW:
            flags[int(target_id)] = [
                (
                    "over_training_window",
                    {
                        "length": length,
                        "training_window": TRAINING_WINDOW,
                        "excess": length - TRAINING_WINDOW,
                    },
                )
            ]
        if progress_every and (written + 1) % progress_every == 0:
            done = written + 1
            rate = done / (time.perf_counter() - started)
            print(f"    {done:,}/{len(rows):,} targets  {rate:.1f}/s", flush=True)  # noqa: T201
        if resolved == "mps":
            torch.mps.empty_cache()

    return (
        FeatureStore(
            spec=spec,
            ids=ids,
            vectors=vectors,
            flags=flags,
            seconds=time.perf_counter() - started,
            manifest=manifest,
            library_versions=library_versions("esm2"),
        ),
        manifest,
        entity_ids,
    )
