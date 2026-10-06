"""Measure ESM-2 embedding throughput on this machine.

The number that matters for M7 is how long it takes to embed every distinct
target sequence once, since embeddings are cached and reused. Batch size is 1:
target sequences vary from tens to thousands of residues, so padded batching
wastes most of its compute, and per-sequence timing is the honest figure.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

DEFAULT_MODEL = "facebook/esm2_t33_650M_UR50D"

# ESM-2's learned positional embeddings cap the context at 1024 including the
# BOS/EOS tokens, so residues beyond 1022 are truncated.
MAX_RESIDUES = 1022

# Truncation is acceptable for a throughput measurement and NOT acceptable for a
# cached embedding. When M7 writes a cache, its key must carry this signature —
# model name, resolved revision, pooling rule, and whether the input was truncated
# along with the effective residue count. A truncated vector stored under a bare
# `sequence_sha256` would claim to represent a protein the model never saw in
# full, and every downstream number computed from it would inherit that claim
# silently. `representation_signature` exists so that key is hard to get wrong.
CACHE_KEY_FIELDS = ("model_name", "revision", "pooling", "truncated", "effective_residues")


def representation_signature(
    model_name: str,
    revision: str,
    pooling: str,
    sequence_length: int,
    max_residues: int = MAX_RESIDUES,
) -> dict[str, object]:
    """The identity a cached embedding must be stored under. See CACHE_KEY_FIELDS."""
    effective = min(sequence_length, max_residues)
    return {
        "model_name": model_name,
        "revision": revision,
        "pooling": pooling,
        "truncated": sequence_length > max_residues,
        "effective_residues": effective,
    }


@dataclass
class Esm2Profile:
    model_name: str
    device: str
    dtype: str
    embedding_dim: int
    n_sequences: int
    n_truncated: int
    residues_processed: int
    seconds: float
    per_sequence_seconds: list[float] = field(default_factory=list)
    model_load_seconds: float = 0.0

    @property
    def sequences_per_second(self) -> float:
        return self.n_sequences / self.seconds if self.seconds else 0.0

    @property
    def residues_per_second(self) -> float:
        return self.residues_processed / self.seconds if self.seconds else 0.0

    @property
    def median_seconds(self) -> float:
        return statistics.median(self.per_sequence_seconds) if self.per_sequence_seconds else 0.0


def select_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def measure(
    sequences: list[str],
    model_name: str = DEFAULT_MODEL,
    device: str | None = None,
    warmup: int = 2,
) -> Esm2Profile:
    """Embed `sequences` one at a time and report throughput."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    device = device or select_device()

    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    # The ESM checkpoint has no pooler, so the default AutoModel head is randomly
    # initialized. We mean-pool `last_hidden_state` ourselves and never read
    # `pooler_output`, but an unused random head is a trap for whoever reads this
    # next — so don't build one. Older/other architectures may not accept the
    # argument, hence the fallback.
    try:
        model = AutoModel.from_pretrained(model_name, add_pooling_layer=False)
    except TypeError:
        model = AutoModel.from_pretrained(model_name)
    model.eval().to(device)
    model_load_seconds = time.perf_counter() - t0

    truncated = [s[:MAX_RESIDUES] for s in sequences]
    n_truncated = sum(1 for s in sequences if len(s) > MAX_RESIDUES)

    def embed(seq: str) -> int:
        batch = tokenizer(seq, return_tensors="pt")
        batch = {k: v.to(device) for k, v in batch.items()}
        with torch.no_grad():
            out = model(**batch).last_hidden_state
            # Mean-pool over real residues, excluding BOS/EOS — the M7 representation.
            out[:, 1:-1, :].mean(dim=1)
        if device == "mps":
            torch.mps.synchronize()
        return int(batch["input_ids"].shape[1])

    for seq in truncated[:warmup]:
        embed(seq)

    per_sequence: list[float] = []
    residues = 0
    started = time.perf_counter()
    for seq in truncated:
        t = time.perf_counter()
        embed(seq)
        per_sequence.append(time.perf_counter() - t)
        residues += len(seq)
    elapsed = time.perf_counter() - started

    return Esm2Profile(
        model_name=model_name,
        device=device,
        dtype=str(next(model.parameters()).dtype).replace("torch.", ""),
        embedding_dim=int(model.config.hidden_size),
        n_sequences=len(truncated),
        n_truncated=n_truncated,
        residues_processed=residues,
        seconds=elapsed,
        per_sequence_seconds=per_sequence,
        model_load_seconds=model_load_seconds,
    )
