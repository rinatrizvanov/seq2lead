"""Pre-flight checks on the dual encoder, on small isolated fixtures.

Run before any expensive fit. Each one catches a class of failure that a long
training run would otherwise hide behind a plausible-looking loss curve: a tower
that receives no gradient, an optimiser that is not wired up, a score that
ignores one of its inputs, or a library path that disagrees with the pair path
the model was trained on.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from seq2lead.models.dual_encoder import (  # noqa: E402
    DualEncoder,
    DualEncoderConfig,
    ProteinTransform,
    load_checkpoint,
    save_checkpoint,
)

TOLERANCE = 1e-5


@pytest.fixture
def tiny():
    config = DualEncoderConfig(projection_dim=16, seed=1)
    model = DualEncoder(config)
    rng = np.random.default_rng(0)
    fingerprints = torch.from_numpy(
        (rng.random((8, config.compound_dim)) < 0.02).astype(np.float32)
    )
    embeddings = torch.from_numpy(rng.standard_normal((8, config.protein_dim)).astype(np.float32))
    return model, fingerprints, embeddings


# ============================================================ gradients reach


def test_every_intended_parameter_receives_a_gradient(tiny) -> None:
    model, fingerprints, embeddings = tiny
    model.initialise_head(np.array([5.0, 9.0]))
    loss = ((model.score_pairs(fingerprints, embeddings) - 7.0) ** 2).mean()
    loss.backward()
    missing = [
        name
        for name, parameter in model.module.named_parameters()
        if parameter.grad is None or torch.all(parameter.grad == 0)
    ]
    assert not missing, f"no gradient reached: {missing}"
    assert model.scale.grad is not None
    assert model.offset.grad is not None


def test_the_towers_are_independent(tiny) -> None:
    """Neither tower may see the other's input, or the library cannot be precomputed."""
    model, fingerprints, embeddings = tiny
    compound_z = model.project_compounds(fingerprints)
    compound_z.sum().backward()
    protein_grads = [
        p.grad for p in model.protein_tower.parameters() if p.grad is not None and p.grad.any()
    ]
    assert not protein_grads, "the compound projection depends on protein parameters"


# ======================================================== the optimiser works


def test_a_tiny_training_set_can_be_overfit(tiny) -> None:
    """If this cannot drive the loss down, nothing about a long run is meaningful."""
    model, fingerprints, embeddings = tiny
    targets = torch.tensor([4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0])
    model.initialise_head(targets.numpy())
    optimiser = torch.optim.Adam(model.parameters(), lr=1e-2)
    first = None
    for _ in range(400):
        optimiser.zero_grad()
        loss = ((model.score_pairs(fingerprints, embeddings) - targets) ** 2).mean()
        loss.backward()
        optimiser.step()
        first = first if first is not None else float(loss.detach())
    final = float(loss.detach())
    assert final < first * 0.25, f"loss barely moved: {first:.3f} -> {final:.3f}"


# ================================================== the score uses both inputs


def test_the_score_depends_on_both_inputs(tiny) -> None:
    model, fingerprints, embeddings = tiny
    with torch.no_grad():
        baseline = model.score_pairs(fingerprints, embeddings)
        other_compound = model.score_pairs(fingerprints.flip(0), embeddings)
        other_protein = model.score_pairs(fingerprints, embeddings.flip(0))
    assert not torch.allclose(baseline, other_compound), "score ignores the compound"
    assert not torch.allclose(baseline, other_protein), "score ignores the protein"


# ============================================ the library path equals the pair path


def test_precomputed_library_scoring_matches_pair_scoring(tiny) -> None:
    """The CLI scores a precomputed compound matrix; training scored pairs.

    If these disagree, every ranking the CLI produces is from a different model
    than the one that was evaluated.
    """
    model, fingerprints, embeddings = tiny
    protein = embeddings[0]
    with torch.no_grad():
        compound_z = model.project_compounds(fingerprints)
        library = model.score_library(compound_z, protein)
        pairs = model.score_pairs(fingerprints, protein.expand(fingerprints.shape[0], -1))
    assert torch.allclose(library, pairs, atol=TOLERANCE), (
        f"max difference {float((library - pairs).abs().max()):.3e}"
    )


# ==================================================== persistence round-trips


def test_save_and_load_preserve_predictions(tiny, tmp_path) -> None:
    model, fingerprints, embeddings = tiny
    model.transform = ProteinTransform.fit(embeddings.numpy(), fitted_on="train")
    model.history = [{"epoch": 1, "validation_rmse": 1.23}]
    with torch.no_grad():
        before = model.score_pairs(fingerprints, embeddings).clone()

    path = save_checkpoint(model, tmp_path / "model.pt", extra={"note": "fixture"})
    restored, extra = load_checkpoint(path)
    with torch.no_grad():
        after = restored.score_pairs(fingerprints, embeddings)

    assert torch.allclose(before, after, atol=TOLERANCE)
    assert extra["note"] == "fixture"
    assert restored.history == model.history
    assert restored.n_parameters() == model.n_parameters()
    assert np.allclose(restored.transform.mean, model.transform.mean)


def test_a_checkpoint_without_its_transform_is_refused(tiny, tmp_path) -> None:
    """Weights on differently scaled inputs are a different model."""
    model, _fingerprints, _embeddings = tiny
    with pytest.raises(ValueError, match="without its protein transform"):
        save_checkpoint(model, tmp_path / "bad.pt")


# ================================================= preprocessing is training-only


def test_the_protein_transform_is_fitted_on_training_inputs_only() -> None:
    rng = np.random.default_rng(3)
    train = rng.standard_normal((50, 8)).astype(np.float32)
    held_out = rng.standard_normal((50, 8)).astype(np.float32) * 10.0 + 100.0
    transform = ProteinTransform.fit(train, fitted_on="train")
    assert np.allclose(transform.mean, train.mean(axis=0, keepdims=True), atol=1e-6)
    assert transform.n_fitted == 50  # noqa: PLR2004
    # Applying it to held-out data must not re-derive anything.
    before = transform.mean.copy()
    transform.apply(held_out)
    assert np.allclose(transform.mean, before)


def test_the_head_is_affine_on_cosine_not_a_probability(tiny) -> None:
    """Documented units: pKi. A sigmoid would be neither pKi nor calibrated."""
    model, fingerprints, embeddings = tiny
    model.initialise_head(np.array([2.0, 12.0]))
    with torch.no_grad():
        scores = model.score_pairs(fingerprints, embeddings)
    # Reachable range is offset +/- |scale|; with this init that spans pKi-like values.
    assert float(model.offset.detach()) == pytest.approx(7.0)
    assert float(model.scale.detach()) == pytest.approx(5.0)
    offset, scale = float(model.offset.detach()), abs(float(model.scale.detach()))
    assert scores.min() >= offset - scale - TOLERANCE
    assert scores.max() <= offset + scale + TOLERANCE


def test_parameter_count_is_reported(tiny) -> None:
    model, _f, _e = tiny
    # two towers of (in->h, h->h) plus scale and offset
    expected = (2048 * 16 + 16) + (16 * 16 + 16) + (1280 * 16 + 16) + (16 * 16 + 16) + 2
    assert model.n_parameters() == expected


# ================================================ the head's sign and the CLI


def test_the_scale_is_unconstrained_so_ranking_must_use_the_affine_score(tiny) -> None:
    """A negative `a` reverses the cosine ordering.

    Nothing constrains the sign of the learned scale. If the CLI ranked by raw
    cosine it would be exactly backwards for such a model, so it must rank by the
    affine output -- the predicted pKi -- which carries the sign with it.
    """
    model, fingerprints, embeddings = tiny
    protein = embeddings[0]
    with torch.no_grad():
        compound_z = model.project_compounds(fingerprints)
        cosine = torch.nn.functional.cosine_similarity(
            compound_z, model.project_proteins(protein.reshape(1, -1)), dim=-1
        )

        model.scale.fill_(2.0)
        model.offset.fill_(7.0)
        positive = model.score_library(compound_z, protein)

        model.scale.fill_(-2.0)
        negative = model.score_library(compound_z, protein)

    cosine_order = torch.argsort(-cosine)
    assert torch.equal(torch.argsort(-positive), cosine_order)
    # With a negative scale the predicted-pKi ranking is the reverse of cosine.
    assert torch.equal(torch.argsort(-negative), torch.argsort(cosine))
    assert not torch.equal(torch.argsort(-negative), cosine_order)


@pytest.mark.requires_db
def test_the_cli_ranks_by_predicted_pki_not_raw_cosine(tmp_path) -> None:
    """End-to-end: flip the sign of the trained scale, the ranking must flip."""
    import numpy as np

    from seq2lead.db import connect
    from seq2lead.models.dual_encoder import ProteinTransform, save_checkpoint
    from seq2lead.models.rank import rank_library

    config = DualEncoderConfig(projection_dim=16, seed=5)
    rng = np.random.default_rng(5)
    model = DualEncoder(config)
    model.transform = ProteinTransform.fit(
        rng.standard_normal((4, config.protein_dim)).astype(np.float32)
    )

    with torch.no_grad():
        model.scale.fill_(3.0)
        model.offset.fill_(7.0)
    forward = save_checkpoint(model, tmp_path / "forward.pt")
    with torch.no_grad():
        model.scale.fill_(-3.0)
    reversed_model = save_checkpoint(model, tmp_path / "reversed.pt")

    # Inference is bound to the caches a checkpoint was trained on, so a synthetic
    # checkpoint needs a binding too. Built from the live experiment config, which
    # is what these fixtures' compound features actually come from.
    from seq2lead.eval import load_experiment
    from seq2lead.models.binding import from_experiment, write_sidecar

    with connect() as conn:
        binding = from_experiment(
            conn, load_experiment(conn, "m9-dual-encoder-v1"), provenance="test fixture"
        )
    for path in (forward, reversed_model):
        write_sidecar(path, binding)

    sequence = "MKVLSSAAWQRTTYNEQ" * 6
    with connect() as conn:
        library = conn.execute("SELECT name FROM candidate_library ORDER BY id LIMIT 1").fetchone()
        if library is None:
            pytest.skip("no candidate library built")
        first = rank_library(conn, sequence, forward, str(library[0]), top_k=20)
        second = rank_library(conn, sequence, reversed_model, str(library[0]), top_k=20)

    top_forward = [r.compound_id for r in first.rows]
    top_reversed = [r.compound_id for r in second.rows]
    assert top_forward != top_reversed, (
        "flipping the scale did not change the ranking, so the CLI is not using the affine score"
    )
    # Scores must be descending in predicted pKi under both signs.
    for result in (first, second):
        scores = [r.score_pki for r in result.rows]
        assert scores == sorted(scores, reverse=True)
