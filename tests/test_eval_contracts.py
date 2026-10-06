"""The evaluation contract, enforced.

Each test here corresponds to a way the leaderboard could be wrong while every
number still looked plausible: labels drawn from the wrong endpoint,
preprocessing fitted on held-out rows, censored bounds used as regression
targets, ties broken by row order, or models compared on different cohorts.
"""

from __future__ import annotations

import numpy as np
import pytest

from seq2lead.db import connect
from seq2lead.eval import load_experiment
from seq2lead.eval.cohort import build_cohort, endpoint_for, ranking_cohort, unusable_compounds


@pytest.fixture(scope="module")
def experiment():
    with connect() as conn:
        yield load_experiment(conn)


# ============================================== experiment inputs are pinned


@pytest.mark.requires_db
def test_experiment_pins_identities_and_verifies_them(experiment) -> None:
    """A 'latest cache' lookup would change what a leaderboard row means."""
    assert experiment.status == "provisional_pooled_for_exploratory_benchmark"
    for ref in experiment.caches.values():
        assert len(ref.manifest_sha256) == 64  # noqa: PLR2004
        assert len(ref.storage_sha256) == 64  # noqa: PLR2004
        assert ref.path.exists()


@pytest.mark.requires_db
def test_a_changed_cache_digest_fails_the_experiment(experiment) -> None:
    from seq2lead.eval.config import ExperimentMismatch, _verify_cache

    with connect() as conn:
        pinned = {
            "name": experiment.caches["ecfp4"].name,
            "manifest_sha256": "0" * 64,
            "storage_sha256": experiment.caches["ecfp4"].storage_sha256,
        }
        with pytest.raises(ExperimentMismatch, match="manifest digest changed"):
            _verify_cache(conn, "ecfp4", pinned)


@pytest.mark.requires_db
def test_label_reversal_is_absent_from_the_scored_splits(experiment) -> None:
    """It is diagnostic_frozen; scoring it during M8 would contaminate M9 choices."""
    assert "label_reversal-v3" not in [s.name for s in experiment.splits]
    assert "label_reversal-v3" in [d[0] for d in experiment.deferred]
    with pytest.raises(KeyError, match="not in this experiment"):
        experiment.split("label_reversal-v3")


# =================================================== temporal label provenance


@pytest.mark.requires_db
def test_temporal_labels_come_from_the_partition_endpoint(experiment) -> None:
    """The global aggregate summarises a pair's whole history, including its future."""
    split = experiment.split("temporal_proxy-v4")
    endpoints = {p: endpoint_for(experiment, split, p) for p in ("train", "validation", "test")}
    assert len(set(endpoints.values())) == 3, endpoints
    assert experiment.endpoint_id not in endpoints.values()
    with connect() as conn:
        for partition, endpoint_id in endpoints.items():
            cohort = build_cohort(conn, experiment, "temporal_proxy-v4", partition)
            assert cohort.endpoint_id == endpoint_id
            name = conn.execute(
                "SELECT name FROM endpoint_version WHERE id=%s", (endpoint_id,)
            ).fetchone()[0]
            assert str(name) == f"temporal_proxy-v4--{partition}"


@pytest.mark.requires_db
def test_temporal_labels_are_supported_only_by_their_own_partition(experiment) -> None:
    """The regression M6 fixed, re-asserted from the evaluation side."""
    split = experiment.split("temporal_proxy-v4")
    with connect() as conn:
        foreign = conn.execute(
            """
            SELECT count(*) FROM split_partition_endpoint spe
            JOIN pair_label l ON l.endpoint_id = spe.endpoint_id
            JOIN pair_label_support sup ON sup.pair_id = l.id
            LEFT JOIN split_activity_assignment s
              ON s.split_id = spe.split_id AND s.activity_id = sup.activity_id
            WHERE spe.split_id = %s
              AND (s.activity_id IS NULL OR s.partition <> spe.partition)
            """,
            (split.id,),
        ).fetchone()[0]
    assert foreign == 0


@pytest.mark.requires_db
def test_a_split_declaring_partition_endpoints_never_falls_back(experiment) -> None:
    from seq2lead.eval.config import SplitRef

    ref = SplitRef(name="x", id=1, partition_endpoints={"train": 42})
    assert endpoint_for(experiment, ref, "train") == 42  # noqa: PLR2004
    with pytest.raises(KeyError, match="refusing to fall back"):
        endpoint_for(experiment, ref, "test")


# ============================================================ shared cohort


@pytest.mark.requires_db
def test_the_cohort_excludes_unusable_fingerprints(experiment) -> None:
    with connect() as conn:
        excluded = set(unusable_compounds(conn, experiment.caches["ecfp4"].name))
        cohort = build_cohort(conn, experiment, "random_pair-v3", "test")
    assert excluded
    assert not (set(cohort.compound_id.tolist()) & excluded)
    assert cohort.n_unusable_fingerprint > 0


@pytest.mark.requires_db
def test_every_model_sees_the_same_cohort(experiment) -> None:
    """Models scoring different rows are not comparable."""
    with connect() as conn:
        first = build_cohort(conn, experiment, "random_pair-v3", "test")
        second = build_cohort(conn, experiment, "random_pair-v3", "test")
    assert np.array_equal(first.compound_id, second.compound_id)
    assert np.array_equal(first.target_id, second.target_id)


@pytest.mark.requires_db
def test_cohort_rows_are_covered_by_the_pinned_caches(experiment) -> None:
    from seq2lead.eval.features import FeatureBank

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        cohort = build_cohort(conn, experiment, "cold_protein-v3", "test")
    assert bank.covers(cohort.compound_id[:5000], cohort.target_id[:5000])


# ====================================================== censored label handling


@pytest.mark.requires_db
def test_censored_pairs_never_become_regression_targets(experiment) -> None:
    """An interval is not a point; assigning it would invent a measurement.

    The invariant is about *evidence counts*, not the `evidence` provenance
    label: that field records what determined the class label, so an excluded
    ambiguous pair can carry `evidence = 'censored'` while still having exact
    measurements. What must never happen is a regression aggregate existing for a
    pair with no exact measurement at all, or one built over more observations
    than it has exact records.
    """
    with connect() as conn:
        no_exact = conn.execute(
            """
            SELECT count(*) FROM pair_regression r
            JOIN pair_label l ON l.endpoint_id = r.endpoint_id
              AND l.compound_id = r.compound_id AND l.target_id = r.target_id
            WHERE r.endpoint_id = %s AND l.n_exact = 0
            """,
            (experiment.endpoint_id,),
        ).fetchone()[0]
        miscounted = conn.execute(
            """
            SELECT count(*) FROM pair_regression r
            JOIN pair_label l ON l.endpoint_id = r.endpoint_id
              AND l.compound_id = r.compound_id AND l.target_id = r.target_id
            WHERE r.endpoint_id = %s AND r.n_obs <> l.n_exact
            """,
            (experiment.endpoint_id,),
        ).fetchone()[0]
        cohort = build_cohort(conn, experiment, "random_pair-v3", "test")
    assert no_exact == 0, "a pair with no exact measurement has a regression aggregate"
    assert miscounted == 0, "a regression aggregate counts more than the pair's exact records"
    assert np.isfinite(cohort.y).all(), "a regression cohort row has no exact value"


@pytest.mark.requires_db
def test_eligible_pairs_are_never_endpoint_excluded(experiment) -> None:
    """Contradictory and discordant pairs cannot arbitrate a score."""
    with connect() as conn:
        cohort = build_cohort(conn, experiment, "random_pair-v3", "test")
        leaked = conn.execute(
            """
            SELECT count(*) FROM pair_label l
            WHERE l.endpoint_id = %s AND l.excluded_from_eval
              AND (l.compound_id, l.target_id) IN (
                  SELECT unnest(%s::bigint[]), unnest(%s::bigint[]))
            """,
            (
                experiment.endpoint_id,
                cohort.compound_id[:20000].tolist(),
                cohort.target_id[:20000].tolist(),
            ),
        ).fetchone()[0]
    assert leaked == 0


@pytest.mark.requires_db
def test_decisive_censored_labels_are_used_for_ranking(experiment) -> None:
    """They are real measured class labels even with no point value."""
    with connect() as conn:
        regression = build_cohort(conn, experiment, "random_pair-v3", "test")
        ranking = ranking_cohort(conn, experiment, "random_pair-v3", "test")
    assert len(ranking) > len(regression), (
        "ranking should admit decisive censored pairs the regression cohort cannot use"
    )
    assert set(ranking.label.tolist()) <= {"active", "inactive"}


# ================================================ training-only preprocessing


@pytest.mark.requires_db
def test_mlp_standardisation_is_fitted_on_training_inputs_only(experiment) -> None:
    from seq2lead.eval.baselines import ConcatMLP
    from seq2lead.eval.cohort import Cohort
    from seq2lead.eval.features import FeatureBank

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        train = build_cohort(conn, experiment, "random_pair-v3", "train")
        validation = build_cohort(conn, experiment, "random_pair-v3", "validation")

    rng = np.random.default_rng(0)
    small = lambda c, n: Cohort(  # noqa: E731
        c.split,
        c.partition,
        c.endpoint_id,
        *(
            arr[rng.choice(len(c), n, replace=False)]
            for arr in (c.compound_id, c.target_id, c.y, c.label, c.stratum)
        ),
    )
    train_small = small(train, 256)
    model = ConcatMLP(hidden=16, epochs=1, batch_size=64)
    model.fit(bank, train_small, small(validation, 128), seed=1)

    expected = bank.protein(train_small.target_id).mean(axis=0, keepdims=True)
    assert np.allclose(model._mean, expected, atol=1e-5), (  # noqa: SLF001
        "standardisation was not fitted on the training inputs alone"
    )


# ==================================================== prediction/label alignment


@pytest.mark.requires_db
def test_predictions_align_with_their_cohort_rows(experiment) -> None:
    """A shuffled prediction vector must not still look correct."""
    from seq2lead.eval.baselines import GlobalAndTargetMean
    from seq2lead.eval.features import FeatureBank
    from seq2lead.eval.runner import evaluate_regression

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        train = build_cohort(conn, experiment, "random_pair-v3", "train")
        test = build_cohort(conn, experiment, "random_pair-v3", "test")
    model = GlobalAndTargetMean()
    model.fit(bank, train, train, seed=0)
    predictions = model.predict(bank, test)
    assert predictions.shape == test.y.shape

    honest = evaluate_regression(test, predictions)["rmse"]["mean"]
    rng = np.random.default_rng(1)
    shuffled = evaluate_regression(test, predictions[rng.permutation(len(predictions))])
    assert shuffled["rmse"]["mean"] > honest, (
        "shuffling predictions did not worsen RMSE, so alignment is not being tested"
    )


@pytest.mark.requires_db
def test_a_tied_model_scores_exactly_chance_on_a_real_split(experiment) -> None:
    """B0 ties within a target; row order must not rescue it."""
    from seq2lead.eval.baselines import GlobalAndTargetMean
    from seq2lead.eval.features import FeatureBank
    from seq2lead.eval.runner import evaluate_ranking

    with connect() as conn:
        bank = FeatureBank.load(conn, experiment)
        train = build_cohort(conn, experiment, "random_pair-v3", "train")
        ranking = ranking_cohort(conn, experiment, "random_pair-v3", "test")
    model = GlobalAndTargetMean()
    model.fit(bank, train, train, seed=0)
    scored = evaluate_ranking(ranking, model.predict(bank, ranking))
    assert scored["auroc"]["mean"] == pytest.approx(0.5, abs=1e-9)
    assert scored["n_targets_all_tied"] == scored["n_targets_scored"]
    assert scored["average_precision"]["mean"] == pytest.approx(
        scored["prevalence"]["mean"], abs=1e-9
    )
