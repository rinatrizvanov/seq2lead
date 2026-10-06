"""Reporting rules the contract fixes, enforced on the renderer itself.

A correct metric reported in the wrong place is still a wrong leaderboard, so
these test the renderer rather than the maths.
"""

from __future__ import annotations

import json

import pytest

from seq2lead.profiling import leaderboard as L


def _run(model: str, split: str, seed, combined: float, new: float, recurrent: float) -> dict:
    def block(auroc: float, targets: int = 100) -> dict:
        return {
            "auroc": {"mean": auroc, "n_targets": targets},
            "average_precision": {"mean": 0.7, "n_targets": targets},
            "prevalence": {"mean": 0.6, "n_targets": targets},
            "recall_at_10": {"mean": 0.2, "n_targets": targets},
            "enrichment_at_1pct": {"mean": 1.1, "n_targets": targets},
            "n_targets_scored": targets,
            "n_targets_all_tied": 0,
            "n_targets_skipped": 0,
            "n_pairs_skipped": 0,
            "skip_reasons": {},
        }

    return {
        "model": model,
        "split": split,
        "seed": seed,
        "deterministic": seed is None,
        "fit_seconds": 1.0,
        "predict_seconds": 0.1,
        "regression": {
            "mae": {"mean": 1.0, "n_targets": 100},
            "rmse": {"mean": 1.1, "n_targets": 100},
            "spearman": {"mean": 0.2, "n_targets": 100},
            "concordance_index": {"mean": 0.6, "n_targets": 100},
            "n_targets": 100,
        },
        "ranking": block(combined),
        "strata": {
            "temporal:new": {**block(new, 90), "n_pairs": 9000, "n_targets": 90},
            "temporal:recurrent": {**block(recurrent, 10), "n_pairs": 500, "n_targets": 10},
            "temporal_regression:new": {
                "mae": {"mean": 1.2},
                "rmse": {"mean": 1.3},
                "spearman": {"mean": 0.1},
                "n_pairs": 8000,
                "n_targets": 88,
            },
            "temporal_regression:recurrent": {
                "mae": {"mean": 0.8},
                "rmse": {"mean": 0.9},
                "spearman": {"mean": 0.4},
                "n_pairs": 400,
                "n_targets": 9,
            },
        },
    }


@pytest.fixture
def rendered(monkeypatch, tmp_path):
    """Render with a synthetic result set where new and combined differ sharply."""
    runs = [
        _run(
            "B4-concat-mlp", "temporal_proxy-v4", 1, combined=0.9999, new=0.1111, recurrent=0.8888
        ),
        _run(
            "B1-ligand-ecfp4-lgbm",
            "temporal_proxy-v4",
            1,
            combined=0.7777,
            new=0.2222,
            recurrent=0.6666,
        ),
    ]

    class Config:
        version = "test-v1"
        status = "provisional_pooled_for_exploratory_benchmark"
        endpoint_name = "ki-pki6-v2"
        endpoint_id = 96
        threshold_pki = 6.0
        caches = {}
        deferred = (("label_reversal-v3", "frozen"),)
        seeds = (1,)

        class _Split:
            name = "temporal_proxy-v4"
            partition_endpoints = {"train": 1, "validation": 2, "test": 3}

        splits = (_Split(),)

        def split(self, name):
            return self._Split()

    monkeypatch.chdir(tmp_path)
    return L.render(conn=None, config=Config(), results_version="m8/test", results=runs, entry=None)


def test_the_headline_table_uses_the_new_stratum_not_the_combined_score(rendered) -> None:
    """The regression: a combined figure exists, differs, and must not be used."""
    headline = rendered.split("## Ranking (primary)")[1].split("## Regression")[0]
    assert "0.1111" in headline, "new-pair AUROC is missing from the headline table"
    assert "0.9999" not in headline, "the combined AUROC reached the headline table"
    assert "0.2222" in headline
    assert "0.7777" not in headline


def test_the_headline_says_which_stratum_it_scored(rendered) -> None:
    headline = rendered.split("## Ranking (primary)")[1].split("## Regression")[0]
    assert "`new` stratum only" in headline


def test_recurrent_is_reported_but_kept_out_of_the_headline(rendered) -> None:
    assert "0.8888" in rendered, "recurrent result is missing entirely"
    headline = rendered.split("## Ranking (primary)")[1].split("## Regression")[0]
    assert "0.8888" not in headline, "recurrent leaked into the headline table"


def test_temporal_regression_is_split_by_stratum(rendered) -> None:
    section = rendered.split("## Regression (secondary)")[1]
    assert "regression, by stratum" in section
    assert "1.300" in section  # new RMSE
    assert "0.900" in section  # recurrent RMSE


def test_a_non_temporal_split_still_uses_the_combined_ranking(monkeypatch, tmp_path) -> None:
    """The stratum rule is specific to temporal; it must not leak elsewhere."""
    runs = [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.9999, 0.1111, 0.8888)]

    class Config:
        version = "test-v1"
        status = "s"
        endpoint_name = "e"
        endpoint_id = 1
        threshold_pki = 6.0
        caches = {}
        deferred = ()
        seeds = (1,)

        class _Split:
            name = "random_pair-v3"
            partition_endpoints = {}

        splits = (_Split(),)

        def split(self, name):
            return self._Split()

    monkeypatch.chdir(tmp_path)
    rendered = L.render(
        conn=None, config=Config(), results_version="m8/test", results=runs, entry=None
    )
    headline = rendered.split("## Ranking (primary)")[1].split("## Regression")[0]
    assert "0.9999" in headline
    assert "`new` stratum only" not in headline


def test_the_status_banner_survives(rendered) -> None:
    assert "provisional_pooled_for_exploratory_benchmark" in rendered


@pytest.mark.requires_db
def test_the_generated_report_scores_temporal_on_new_pairs() -> None:
    """The real report, not a synthetic one."""
    from pathlib import Path

    report = Path("reports/leaderboard.md")
    if not report.exists():
        pytest.skip("leaderboard not generated")
    text = report.read_text(encoding="utf-8")
    headline = text.split("**`temporal_proxy-v4`**")[1].split("## Regression")[0]
    assert "`new` stratum only" in headline

    summary = Path("reports/results/baseline_summary_v2.json")
    if not summary.exists():
        pytest.skip("no corrected summary")
    runs = json.loads(summary.read_text(encoding="utf-8"))
    b4 = [r for r in runs if r["model"] == "B4-concat-mlp" and r["split"] == "temporal_proxy-v4"]
    new = sum(r["strata"]["temporal:new"]["auroc"]["mean"] for r in b4) / len(b4)
    combined = sum(r["ranking"]["auroc"]["mean"] for r in b4) / len(b4)
    assert abs(new - combined) > 1e-3, "the two must differ for this test to discriminate"
    assert f"{new:.4f}" in headline
    assert f"{combined:.4f}" not in headline


# ===================================================== explicit result selection


class _Cfg:
    version = "baseline-v1"
    config_sha256 = "a" * 64
    status = "provisional_pooled_for_exploratory_benchmark"
    endpoint_name = "ki-pki6-v2"
    endpoint_id = 96
    threshold_pki = 6.0
    caches = {}
    deferred = ()
    seeds = (1,)

    class _Split:
        name = "random_pair-v3"
        partition_endpoints = {}

    splits = (_Split(),)

    def split(self, name):
        return self._Split()


def test_a_newer_fitting_run_cannot_be_shadowed_by_an_older_corrected_result(tmp_path) -> None:
    """The stale-selection scenario, reproduced.

    The old loader tried `baseline_summary_v2.json` first and fell back, so a
    fresh fitting run writing different numbers stayed invisible behind the older
    corrected file. Selection is now by explicit version, and each version is
    bound to the experiment that produced it.
    """
    from seq2lead.eval.results import ResultSelectionError, load, publish

    config = _Cfg()
    old_corrected = [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.1111, 0.1, 0.1)]
    new_fitted = [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.9999, 0.9, 0.9)]

    publish(
        old_corrected,
        version="m8/v2",
        filename="m8_v2_summary.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )
    publish(
        new_fitted,
        version="m8/v3",
        filename="m8_v3_summary.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )

    newer, entry = load("m8/v3", config, tmp_path)
    assert entry.version == "m8/v3"
    assert newer[0]["ranking"]["auroc"]["mean"] == pytest.approx(0.9999)

    older, _ = load("m8/v2", config, tmp_path)
    assert older[0]["ranking"]["auroc"]["mean"] == pytest.approx(0.1111)

    # No default, no fallback: an unnamed or unknown version fails loudly.
    with pytest.raises(ResultSelectionError, match="no result version"):
        load("m8/v9", config, tmp_path)


def test_rendering_uses_the_requested_version_not_the_newest(tmp_path, monkeypatch) -> None:
    from seq2lead.eval.results import load, publish

    config = _Cfg()
    publish(
        [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.1111, 0.1, 0.1)],
        version="m8/v2",
        filename="a.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )
    publish(
        [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.9999, 0.9, 0.9)],
        version="m8/v3",
        filename="b.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )
    monkeypatch.chdir(tmp_path)
    for version, expected, absent in (("m8/v2", "0.1111", "0.9999"), ("m8/v3", "0.9999", "0.1111")):
        runs, entry = load(version, config, tmp_path)
        rendered = L.render(
            conn=None, config=config, results_version=version, results=runs, entry=entry
        )
        assert expected in rendered
        assert absent not in rendered
        assert version in rendered


def test_a_published_version_cannot_be_redefined(tmp_path) -> None:
    from seq2lead.eval.results import ResultSelectionError, publish

    config = _Cfg()
    runs = [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.5, 0.5, 0.5)]
    publish(
        runs,
        version="m8/v2",
        filename="a.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )
    # Same content is a no-op, so a rerun is safe.
    publish(
        runs,
        version="m8/v2",
        filename="a.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )
    changed = [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.6, 0.5, 0.5)]
    with pytest.raises(ResultSelectionError, match="already published"):
        publish(
            changed,
            version="m8/v2",
            filename="a.json",
            metric_version="m8/v2",
            config=config,
            directory=tmp_path,
        )
    # ...including under a different filename, which would otherwise slip past.
    with pytest.raises(ResultSelectionError, match="already published"):
        publish(
            changed,
            version="m8/v2",
            filename="different.json",
            metric_version="m8/v2",
            config=config,
            directory=tmp_path,
        )


def test_results_are_bound_to_the_config_that_produced_them(tmp_path) -> None:
    from seq2lead.eval.results import ResultSelectionError, load, publish

    config = _Cfg()
    publish(
        [_run("B1-ligand-ecfp4-lgbm", "random_pair-v3", 1, 0.5, 0.5, 0.5)],
        version="m8/v2",
        filename="a.json",
        metric_version="m8/v2",
        config=config,
        directory=tmp_path,
    )

    class Changed(_Cfg):
        config_sha256 = "b" * 64

    with pytest.raises(ResultSelectionError, match="different .* config file"):
        load("m8/v2", Changed(), tmp_path)
    with pytest.raises(ResultSelectionError, match="metric version"):
        load("m8/v2", config, tmp_path, require_metric_version="m8/v9")


# =========================================================== the stratum footer


def test_the_temporal_footer_counts_the_stratum_the_table_scored(monkeypatch, tmp_path) -> None:
    """Deliberately different combined and new-pair skip counts."""
    run = _run("B4-concat-mlp", "temporal_proxy-v4", 1, combined=0.9, new=0.1, recurrent=0.8)
    run["ranking"]["n_targets_skipped"] = 992
    run["ranking"]["n_pairs_skipped"] = 17808
    run["ranking"]["skip_reasons"] = {"combined reason": 992}
    run["strata"]["temporal:new"]["n_targets_skipped"] = 959
    run["strata"]["temporal:new"]["n_pairs_skipped"] = 17559
    run["strata"]["temporal:new"]["skip_reasons"] = {"new-stratum reason": 959}

    class Config(_Cfg):
        class _Split:
            name = "temporal_proxy-v4"
            partition_endpoints = {"train": 1, "validation": 2, "test": 3}

        splits = (_Split(),)

        def split(self, name):
            return self._Split()

    monkeypatch.chdir(tmp_path)
    rendered = L.render(
        conn=None, config=Config(), results_version="m8/v2", results=[run], entry=None
    )
    footer = rendered.split("**`temporal_proxy-v4`**")[1].split("## Regression")[0]
    assert "959" in footer and "17,559" in footer, "footer did not use the new stratum"
    assert "992" not in footer and "17,808" not in footer, "combined counts reached the footer"
    assert "new-stratum reason" in footer
    assert "combined reason" not in footer
    assert "`new` stratum" in footer
