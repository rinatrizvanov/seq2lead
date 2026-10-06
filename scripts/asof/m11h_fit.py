"""The bounded exploratory as-of fit. Verifies, preflights, then fits.

Order: the corrected runner verifies all 14 pinned inputs, the caches, the
extensions, the reuse maps and the emitted dataset digests; every planned output
path is checked for collisions; and only then does anything fit.

The declared separation is kept measurable. `fit_transforms` is handed A-train
and nothing else. Every gradient and boosting update comes from A-train rows.
A-validation reaches a fit only as the per-epoch RMSE that chooses a checkpoint,
and `select_checkpoint` re-derives that cohort's digest to confirm the rows the
fits scored against are the rows the runner holds.
"""

from __future__ import annotations

import json
import platform
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from m11g_bind import ARTIFACTS, sha256  # noqa: E402

from seq2lead.asof.contract import CONTRACT_VERSION, PINNED_SETTINGS, STORED_DIMS  # noqa: E402
from seq2lead.asof.fitting import (  # noqa: E402
    DECLARED_BASELINES,
    DETERMINISTIC,
    FITTING_VERSION,
    MAIN_MODEL,
    FitOutcome,
    FittingError,
    RunPaths,
    build_bank,
    cohort_digest,
    cohort_from_pairs,
    declared_config,
    write_records,
    write_transform,
)
from seq2lead.asof.runner import (  # noqa: E402
    RUNNER_VERSION,
    derive_feature_sources,
    derive_roles,
    run,
)

G = Path("data/asof/m11g")
H = Path("data/asof/m11h")
EVAL_PAIRS = G / "evaluation-pairs.jsonl"
ARMS = ("declared_increment", "cross_slot_excluded")


def baseline_instances():
    from seq2lead.eval.baselines import (
        ConcatMLP,
        GlobalAndTargetMean,
        LigandNeighbour,
        LigandOnly,
        ProteinOnly,
    )

    return {
        "B0-target-mean": GlobalAndTargetMean,
        "B1-ligand-ecfp4-lgbm": LigandOnly,
        "B2-protein-esm2-lgbm": ProteinOnly,
        "B3L-ligand-1nn": LigandNeighbour,
        "B4-concat-mlp": ConcatMLP,
    }


def load_evaluation_pairs() -> list[dict]:
    return [json.loads(line) for line in EVAL_PAIRS.open(encoding="utf-8") if line.strip()]


def main() -> None:
    t0 = time.time()
    pilot = "--pilot" in sys.argv
    seeds = tuple(PINNED_SETTINGS["run_seeds"])
    models = (*DECLARED_BASELINES, MAIN_MODEL)
    if pilot:
        seeds = seeds[:1]

    # ---- 1. output-path preflight, before anything is verified or fitted ----
    run_id = "pilot" if pilot else time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    paths = RunPaths(root=H / f"run-{run_id}", run_id=run_id)
    plan = paths.preflight(models, seeds)
    print(f"output preflight OK: {paths.root}")

    # ---- 2. verify every input through the corrected runner ----------------
    digests = {name: sha256(path) for name, path in ARTIFACTS.items()}
    sources = derive_feature_sources(dict(ARTIFACTS))
    roles = derive_roles(dict(ARTIFACTS))
    print(f"verified {len(digests)} pinned inputs; roles derived: "
          + ", ".join(f"{r} {len(e.compounds):,}c/{len(e.sequences):,}s"
                      for r, e in sorted(roles.items())))

    from seq2lead.asof.features import load_binding

    bindings = {
        kind: load_binding(
            kind,
            accepted_path=s.accepted_path, accepted_sha256=s.accepted_sha256,
            reuse_map=s.reuse_map, stored_dim=STORED_DIMS[kind],
            extension_path=s.extension_path, extension_sha256=s.extension_sha256,
        )
        for kind, s in sources.items()
    }

    # ---- 3. the entity universe and the bank -------------------------------
    evaluation_rows = load_evaluation_pairs()
    eval_c = {r["pair"].split("|", 1)[0] for r in evaluation_rows}
    eval_t = {r["pair"].split("|", 1)[1] for r in evaluation_rows}
    all_c = roles["train"].compounds | roles["validation"].compounds | eval_c
    all_t = roles["train"].sequences | roles["validation"].sequences | eval_t
    bank, compound_ix, sequence_ix = build_bank(bindings, all_c, all_t)
    print(f"bank: {len(compound_ix.keys):,} compounds, {len(sequence_ix.keys):,} sequences")

    evaluation = cohort_from_pairs(
        [r["pair"] for r in evaluation_rows], None, compound_ix, sequence_ix,
        partition="b_increment",
        labels=[r["arms"]["declared_increment"]["label"] or "none" for r in evaluation_rows],
        strata=[r["stratum"] for r in evaluation_rows],
    )

    # ---- 4. the callbacks --------------------------------------------------
    state: dict[str, object] = {}
    outcomes: list[FitOutcome] = []

    def fit_transforms(train_ds):
        """A-train only. The one learned transform, fitted and then frozen."""
        from seq2lead.models.dual_encoder import ProteinTransform

        train = cohort_from_pairs(
            train_ds.pairs, train_ds.targets, compound_ix, sequence_ix, partition="a_train"
        )
        state["train_cohort"] = train
        state["train_digest"] = cohort_digest(train)
        transform = ProteinTransform.fit(bank.protein(train.target_id), fitted_on="a_train")
        state["transform"] = transform
        print(f"transform fitted on {transform.n_fitted:,} A-train protein rows")
        return transform

    def fit_model(train_ds, transform):
        """Gradient and boosting updates from A-train rows only.

        The validation cohort is built here so that per-epoch selection can run,
        and its digest is recorded so `select_checkpoint` can confirm the rows
        scored against were the rows the runner itself holds. No validation row
        enters a loss that is differentiated or a boosting residual.
        """
        train = state["train_cohort"]
        validation = cohort_from_pairs(
            state["validation_pairs"], state["validation_targets"],
            compound_ix, sequence_ix, partition="a_validation",
        )
        state["validation_cohort"] = validation
        state["validation_digest_used_for_selection"] = cohort_digest(validation)
        classes = baseline_instances()
        fitted: dict[tuple[str, int | None], object] = {}

        for name in models:
            use_seeds = (seeds[0],) if name in DETERMINISTIC else seeds
            for seed in use_seeds:
                tag = name if name in DETERMINISTIC else f"{name}-seed{seed}"
                started = time.perf_counter()
                try:
                    if name == MAIN_MODEL:
                        from seq2lead.models.dual_encoder import save_checkpoint
                        from seq2lead.models.train import fit as fit_dual

                        config = declared_config(seed, PINNED_SETTINGS)
                        model, record = fit_dual(
                            bank, train, validation, config,
                            split_name="asof-202601-202609", run_dir=paths.root,
                        )
                        # The transform train.fit derives must be the shared one.
                        assert np.allclose(model.transform.mean, transform.mean)
                        assert np.allclose(model.transform.scale, transform.scale)
                        ckpt = paths.checkpoint(name, seed)
                        save_checkpoint(model, ckpt, extra={
                            "contract": CONTRACT_VERSION,
                            "runner_version": RUNNER_VERSION,
                            "train_digest": state["train_digest"],
                            "validation_digest": state["validation_digest_used_for_selection"],
                        })
                        outcomes.append(FitOutcome(
                            model=name, seed=seed, seconds=record.seconds,
                            n_train=record.n_train, n_validation=record.n_validation,
                            selected_on="A-validation RMSE per epoch",
                            best_epoch=record.best_epoch,
                            best_validation_rmse=record.best_validation_rmse,
                            epochs_run=record.epochs_run,
                            stopped_early=record.stopped_early,
                            n_parameters=record.n_parameters,
                            history=record.history, checkpoint=str(ckpt),
                            notes={"device": record.device,
                                   "config_digest": record.config_digest,
                                   "curve_recorded": "per-epoch validation RMSE"},
                        ))
                        fitted[(name, seed)] = model
                    else:
                        instance = classes[name]()
                        report = instance.fit(bank, train, validation, seed)
                        notes = dict(report.notes)
                        # What curve detail each accepted model actually exposes,
                        # recorded rather than claimed. Only the dual encoder keeps
                        # a per-epoch history; ConcatMLP restores its best epoch but
                        # records no curve and has no early stopping; the LightGBM
                        # models report only the selected round count.
                        cap = notes.get("best_iteration")
                        if cap is not None:
                            notes["round_cap"] = 400
                            notes["round_cap_was_binding"] = cap >= 400
                            notes["selection_note"] = (
                                "validation RMSE with 50-round early stopping was wired and "
                                "did NOT trigger: validation RMSE was still improving at the "
                                "400-round cap, so the cap bound rather than validation."
                                if cap >= 400
                                else "early stopping on validation RMSE selected this round count"
                            )
                        notes["curve_recorded"] = (
                            "per-epoch validation RMSE"
                            if name == MAIN_MODEL
                            else "best epoch and best validation RMSE only; this model "
                                 "records no per-epoch curve"
                            if "best_epoch" in notes
                            else "none; this model has no epochs"
                        )
                        outcomes.append(FitOutcome(
                            model=name,
                            seed=None if name in DETERMINISTIC else seed,
                            seconds=report.seconds, n_train=report.n_train,
                            n_validation=len(validation),
                            selected_on=str(notes.get("selection_note")
                                             or notes.get("selected_on", "n/a")),
                            best_epoch=notes.get("best_epoch"),
                            best_validation_rmse=notes.get("best_validation_rmse"),
                            epochs_run=notes.get("best_epoch"),
                            stopped_early=(
                                None if cap is None else bool(cap < 400)
                            ),
                            notes=notes,
                        ))
                        fitted[(name, None if name in DETERMINISTIC else seed)] = instance
                    print(f"  fitted {tag:<30} {time.perf_counter() - started:>7.1f}s",
                          flush=True)
                except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
                    outcomes.append(FitOutcome(
                        model=name, seed=seed, seconds=time.perf_counter() - started,
                        n_train=len(train), n_validation=len(validation),
                        selected_on="n/a", failure=f"{type(exc).__name__}: {exc}",
                        notes={"traceback": traceback.format_exc()[-2000:]},
                    ))
                    print(f"  FAILED {tag:<30} {type(exc).__name__}: {exc}", flush=True)
        state["fitted"] = fitted
        return fitted

    def select_checkpoint(validation_ds, fitted):
        """Confirm the selection set, and that each run restored its best epoch."""
        rebuilt = cohort_from_pairs(
            validation_ds.pairs, validation_ds.targets,
            compound_ix, sequence_ix, partition="a_validation",
        )
        held = cohort_digest(rebuilt)
        used = state["validation_digest_used_for_selection"]
        if held != used:
            msg = (
                f"the cohort used for checkpoint selection ({used[:16]}…) is not the "
                f"A-validation set the runner holds ({held[:16]}…)"
            )
            raise FittingError(msg)
        restored = []
        ordered = sorted(fitted, key=lambda k: (k[0], k[1] or 0))
        for name, seed in ordered:
            match = [
                o for o in outcomes
                if o.model == name and o.seed == seed and o.failure is None
            ]
            if not match:
                continue
            o = match[0]
            if name == MAIN_MODEL:
                best = min(o.history, key=lambda h: h["validation_rmse"])
                if abs(best["validation_rmse"] - o.best_validation_rmse) > 1e-9:
                    msg = f"{name} seed {seed}: restored checkpoint is not the best epoch"
                    raise FittingError(msg)
                if best["epoch"] != o.best_epoch:
                    msg = f"{name} seed {seed}: best epoch disagrees with the history"
                    raise FittingError(msg)
            restored.append({"model": name, "seed": seed, "best_epoch": o.best_epoch,
                             "best_validation_rmse": o.best_validation_rmse})
        return {
            "validation_digest": held,
            "validation_pairs": len(rebuilt),
            "selection_metric": PINNED_SETTINGS["checkpoint_selection_metric"],
            "selection_direction": PINNED_SETTINGS["checkpoint_selection_direction"],
            "restored": restored,
            "never_refit_on_train_plus_validation": True,
        }

    # The validation pairs the fits will score against come from the runner's own
    # loader; they are placed here before `fit_model` runs and re-checked after.
    from seq2lead.asof.datasets import load_dataset

    v = load_dataset(
        ARTIFACTS["a_membership"], "validation",
        usable_compounds=roles["validation"].compounds,
        usable_sequences=roles["validation"].sequences,
    )
    state["validation_pairs"], state["validation_targets"] = v.pairs, v.targets

    print(f"\nfitting {len(models)} declared models, seeds {seeds}"
          f"{' (PILOT)' if pilot else ''}\n")
    out = run(
        expected_digests=digests, artifacts=dict(ARTIFACTS), config=dict(PINNED_SETTINGS),
        fit_transforms=fit_transforms, fit_model=fit_model,
        select_checkpoint=select_checkpoint,
    )
    fitted = out["selection"]

    # ---- 5. predictions, saved with digests --------------------------------
    print("\npredicting on the evaluation cohort")
    predictions, names = {}, []
    # The runner returns the selection record; the fitted objects themselves come
    # from the closure the callbacks share, which is also what was checkpointed.
    for (name, seed), model in sorted(
        state["fitted"].items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)
    ):
        tag = name if seed is None else f"{name}-seed{seed}"
        started = time.perf_counter()
        if name == MAIN_MODEL:
            from seq2lead.models.train import predict as predict_dual

            scores = predict_dual(model, bank, evaluation)
        else:
            scores = model.predict(bank, evaluation)
        predictions[tag] = np.asarray(scores, dtype=np.float64)
        names.append(tag)
        print(f"  {tag:<30} {time.perf_counter() - started:>7.1f}s", flush=True)

    np.savez_compressed(
        paths.planned["predictions"],
        pair=np.asarray([r["pair"] for r in evaluation_rows], dtype=object),
        **predictions,
    )
    prediction_digest = sha256(paths.planned["predictions"])
    transform_digest = write_transform(state["transform"], paths.planned["transform"])
    records_digest = write_records(outcomes, paths.planned["training_records"])
    # The evaluation table is copied into the run so the run is self-contained;
    # the original stays where it was built and is unchanged.
    paths.planned["evaluation_table"].write_text(
        EVAL_PAIRS.read_text(encoding="utf-8"), encoding="utf-8"
    )

    manifest = {
        "run": "m11h-asof-fit-v1",
        "run_id": run_id,
        "pilot": pilot,
        "fitting_version": FITTING_VERSION,
        "runner_version": out["runner_version"],
        "contract_version": out["contract_version"],
        "inputs_verified": out["inputs_verified"],
        "roles_derived": out["roles_derived"],
        "feature_bindings": out["features"]["reuse_maps_verified"],
        "emitted_matches_pinned": out["emitted_matches_pinned"],
        "transform": {
            "path": str(paths.planned["transform"]), "sha256": transform_digest,
            "fitted_on": state["transform"].fitted_on,
            "n_fitted": state["transform"].n_fitted,
        },
        "training_records": {
            "path": str(paths.planned["training_records"]), "sha256": records_digest,
        },
        "predictions": {
            "path": str(paths.planned["predictions"]), "sha256": prediction_digest,
            "models": sorted(names), "rows": len(evaluation_rows),
        },
        "evaluation_table": {
            "path": str(paths.planned["evaluation_table"]),
            "sha256": sha256(paths.planned["evaluation_table"]),
        },
        "checkpoints": {
            o.checkpoint: sha256(Path(o.checkpoint))
            for o in outcomes if o.checkpoint and Path(o.checkpoint).exists()
        },
        "local_index": {
            "compound": compound_ix.as_dict(), "sequence": sequence_ix.as_dict(),
        },
        "selection": fitted,
        "output_preflight": plan,
        "seeds": list(seeds),
        "failures": [
            {"model": o.model, "seed": o.seed, "failure": o.failure}
            for o in outcomes if o.failure
        ],
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "seconds": round(time.time() - t0, 1),
        "qualifications": [
            "EXPLORATORY. Snapshot B is our already-inspected 202609.",
            "PROVISIONAL POOLING. Whether Ki may be pooled is M5's open question.",
            "The confirmatory freeze is NOT signed.",
            "Retrieval stays unbuilt; evaluation evidence display stays off.",
            "label_reversal-v3 is NOT scored here.",
            "Seed spread is training variation, not a confidence interval.",
        ],
    }
    paths.planned["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {paths.planned['manifest']}")
    print(f"failures: {manifest['failures'] or 'none'}")
    print(f"{time.time() - t0:.1f}s total")


if __name__ == "__main__":
    main()
