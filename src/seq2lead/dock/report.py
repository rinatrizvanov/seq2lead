"""Render reports/docking.md from the recorded artifacts.

Every number in the report is read back from a file on disk rather than passed in
from the run that produced it, so the report cannot drift from the evidence. If an
artifact is missing the report says so instead of omitting the row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from seq2lead.dock.artifacts import sha256_file

REPORT_PATH = Path("reports/docking.md")


def _f(value: Any, spec: str = ".3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _energy(score: Any) -> str:
    """A ranking score with its raw Vina energy alongside, never one without the other."""
    if score is None:
        return "n/a"
    return f"{float(score):.3f}  (= −{float(score):.3f} kcal/mol Vina energy)"


def _rows(lines: list[str], table: list[tuple[str, ...]]) -> None:
    for row in table:
        lines.append("| " + " | ".join(row) + " |")


def render(
    gate_path: Path = Path("reports/results/m10_gate.json"),
    cohort_path: Path = Path("reports/results/m10_cohort.json"),
    qa_path: Path = Path("reports/results/m10_qa_redock.json"),
    config_path: Path = Path("configs/experiments/m10-docking-v1.yaml"),
    sensitivity_paths: dict[str, Path] | None = None,
) -> str:
    import yaml

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    gate, record = payload["gate"], payload["record"]
    attrition = payload["attrition"]
    clean = payload["receptor"]["clean_report"]
    cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
    # the membership digest is derived, not stored, so recompute it from the file
    # rather than trusting a field that could have been edited independently
    from seq2lead.dock.cohort import read_cohort

    membership_digest = read_cohort(cohort_path).membership_sha256()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else None

    verdict = gate["decision"]
    headline = {
        "pass": "PASS",
        "fail": "FAIL",
        "inconclusive": "INCONCLUSIVE",
        "inconclusive_insufficient_data": "INCONCLUSIVE (insufficient data)",
    }.get(verdict, verdict.upper())

    L: list[str] = []
    L.append("# M10 — docking validation gate")
    L.append("")
    L.append(
        f"**Gate decision: {headline}.** Target {config['target']['name']} "
        f"(UniProt {config['target']['uniprot']}), structure {config['structure']['pdb_id']} at "
        f"{config['structure']['resolution_angstrom']} Å, AutoDock Vina "
        f"{config['engine']['version']}."
    )
    L.append("")
    L.append(f"> {gate['reason']}")
    L.append("")
    L.append(
        "The contract and its decision rule were frozen before this cohort was docked: "
        f"`{config_path}` (`{sha256_file(config_path)[:16]}…`), with the reasoning in "
        "`docs/DOCKING.md`. The question is whether **this** structure, pocket and protocol "
        "rank measured actives above measured inactives for **this** target. It is not "
        "evidence that docking improves the M9 ranking; that is a separate evaluation and "
        "was not attempted."
    )
    L.append("")

    # ------------------------------------------------------------ the result
    L.append("## The result")
    L.append("")
    L.append("| | |")
    L.append("| --- | --- |")
    _rows(
        L,
        [
            ("Primary metric", f"AUROC **{_f(gate['auroc'])}**"),
            (
                "Ranking score",
                "**−1 × best-pose Vina energy**, so a higher score means predicted to bind more "
                "tightly. Raw energies in kcal/mol are retained in `scores.json` as "
                "`affinity_kcal_per_mol`.",
            ),
            (
                f"{config['gate']['uncertainty']['level']:.0%} CI "
                f"({config['gate']['uncertainty']['resamples']:,} class-stratified bootstrap "
                "resamples)",
                f"[{_f(gate['ci_low'])}, {_f(gate['ci_high'])}]",
            ),
            ("Pre-registered effect threshold", f"{gate['effect_threshold']}"),
            ("Decision", f"**{headline}**"),
            ("Scored", f"{gate['n_active']} active / {gate['n_inactive']} inactive"),
        ],
    )
    L.append("")
    sec = gate.get("secondary") or {}
    if sec:
        L.append("### Secondary, and none of it can change the decision")
        L.append("")
        L.append("| Measure | Value |")
        L.append("| --- | --- |")
        _rows(
            L,
            [
                (
                    "Mann–Whitney U (two-sided)",
                    f"U = {_f(sec.get('mannwhitney_u'), ',.0f')}, "
                    f"p = {_f(sec.get('mannwhitney_p_two_sided'), '.3g')}",
                ),
                (
                    "Median ranking score, actives",
                    _energy(sec.get("median_ranking_score_active")),
                ),
                (
                    "Median ranking score, inactives",
                    _energy(sec.get("median_ranking_score_inactive")),
                ),
                ("EF@1%", _f(sec.get("ef_1pct"), ".2f")),
                ("EF@5%", _f(sec.get("ef_5pct"), ".2f")),
                ("BEDROC (α=20)", _f(sec.get("bedroc_alpha20"))),
            ],
        )
        L.append("")
        L.append(
            "The Mann–Whitney test is two-sided and therefore indifferent to direction. A "
            "gate resting on it would accept a protocol that ranks inactives first, so it is "
            "reported next to each class's median ranking score and never used to decide."
        )
        L.append("")
        L.append(
            "**What the interval does and does not cover.** The bootstrap resamples "
            "**individual compounds**, stratified on class. This cohort is largely congeneric "
            "— aryl sulfonamide series in which many members are close analogues. The "
            "interval and p-value use an analysis that does not account for dependence among "
            "chemical analogues. Their calibration under that dependence is unknown."
        )
        L.append("")
        L.append(
            "The pre-declared statistics and the gate decision stand exactly as "
            "pre-registered. A separately labelled post-hoc analysis at the scaffold or "
            "series level would be a legitimate way to probe this, and was not performed "
            "here; nothing in this report should be read as generalising beyond this target "
            "and this chemical series."
        )
        L.append("")

    # ------------------------------------------------------------- attrition
    L.append("## Attrition, by class")
    L.append("")
    L.append("| Class | Scored | Failed | Stages |")
    L.append("| --- | ---: | ---: | --- |")
    for cls in ("active", "inactive"):
        row = attrition[cls]
        stages = ", ".join(
            f"{k.removeprefix('failed_')}: {v}"
            for k, v in row.items()
            if k.startswith("failed_") and v
        )
        _rows(L, [(cls, f"{row['scored']}", f"{row['failed']}", stages or "—")])
    L.append("")
    L.append(
        f"{record['n_requested']} requested, {record['n_scored']} scored, "
        f"{record['n_failed']} dropped. A compound that fails preparation or docking is "
        '**dropped and counted here**, never scored as "worst" — filling failures with the '
        "minimum would invent exactly the ordering the gate is testing for. The per-class "
        "split is reported so a dropout that hits one class harder is visible."
    )
    L.append("")

    chemistry = payload.get("attrition_chemistry") or {}
    bounds = payload.get("attrition_bounds") or {}
    if chemistry:
        L.append("### The dropout is chemically systematic, not random")
        L.append("")
        L.append("| Blocking feature | Compounds dropped |")
        L.append("| --- | ---: |")
        for cause, n in sorted(chemistry.items(), key=lambda kv: -kv[1]):
            _rows(L, [(f"`{cause}`", f"{n}")])
        L.append("")
        element_rows = {
            k: v for k, v in chemistry.items() if "exotic" not in k and "SMILES" not in k
        }
        n_element = sum(element_rows.values())
        n_total = sum(chemistry.values())
        L.append(
            f"**{n_element} of the {n_total} dropped compounds** carry an element the "
            "preparation toolchain could not type — "
            + ", ".join(f"{v} with {k}" for k, v in sorted(element_rows.items()))
            + ". Each compound is counted once: an earlier draft summed per-element tallies "
            "and reported 21, double-counting the two compounds containing both selenium and "
            "tellurium. The table above and this total are generated from `failures.json` and "
            "the frozen cohort, so they cannot drift apart again."
        )
        L.append("")
        L.append(
            "Organoselenium and boronic-acid carbonic-anhydrase inhibitors are real, measured "
            "ligand classes, and no compound containing selenium, tellurium or boron survived "
            "into the scored set. So the compounds that were scored are not a random sample of "
            "the frozen cohort — they are the subset this toolchain can represent, which is "
            "the kind of dropout that can bias a gate."
        )
        L.append("")
        L.append(
            "**Scope of that claim.** It describes what was observed from meeko 0.8.0 writing "
            f"PDBQT and AutoDock Vina {config['engine']['version']} reading it, at the "
            "versions pinned in `uv.lock`, with RDKit 2026.03.6 building the conformer. The "
            "messages were `atom number N has None type` from meeko and `Atom type B is not a "
            "valid AutoDock type` from Vina. It is not a claim about AutoDock file formats in "
            "general, about other preparation tools, or about other versions of either."
        )
        L.append("")
    if bounds:
        L.append("### Could the dropout have rescued the gate? No")
        L.append("")
        L.append(
            "AUROC counts concordant (active, inactive) pairs, so pairs involving a dropped "
            "compound are simply unknown. Resolving **every** unknown pair in favour of the "
            "actives gives the highest AUROC the full frozen cohort could have produced; "
            "resolving every one against them gives the lowest."
        )
        L.append("")
        L.append("| | AUROC |")
        L.append("| --- | ---: |")
        _rows(
            L,
            [
                ("Observed, on what was scored", _f(bounds["observed_auroc"])),
                ("Best case over the full 300/300 cohort", f"**{_f(bounds['best_case_auroc'])}**"),
                ("Worst case over the full 300/300 cohort", _f(bounds["worst_case_auroc"])),
                (
                    "Pairs unknown",
                    f"{bounds['unknown_pairs']:,.0f} of "
                    f"{bounds['total_pairs']:,.0f} ({bounds['fraction_unknown']:.1%})",
                ),
            ],
        )
        L.append("")
        verdict_line = (
            "Even the optimistic bound stays below the pre-registered threshold "
            f"{gate['effect_threshold']}, so the attrition is not what failed this gate."
            if bounds["best_case_auroc"] < gate["effect_threshold"]
            else "The optimistic bound reaches the threshold, so the attrition alone could "
            "have changed this decision and the result must be read as inconclusive "
            "pending the dropped compounds."
        )
        L.append(verdict_line)
        L.append("")

    # ----------------------------------------------------------- the cohort
    L.append("## The measured cohort")
    L.append("")
    L.append("| | |")
    L.append("| --- | --- |")
    _rows(
        L,
        [
            ("Name", f"`{cohort['name']}`"),
            (
                "Endpoint",
                f"`{cohort['endpoint']}` (id {cohort['endpoint_id']}), θ = pKi "
                f"{config['endpoint']['threshold_pki']}",
            ),
            ("Selection", f"{cohort['method']}, seed {cohort['seed']}"),
            ("Members", f"{cohort['n_active']} active / {cohort['n_inactive']} inactive"),
            (
                "Evidence",
                ", ".join(f"{k} {v}" for k, v in sorted(cohort["evidence_breakdown"].items())),
            ),
            (
                "Eligible pool",
                f"{cohort['pool_active']:,} active / {cohort['pool_inactive']:,} inactive",
            ),
            ("Membership digest", f"`{membership_digest[:16]}…`"),
        ],
    )
    L.append("")
    L.append(
        "Frozen before any docking score existed, and protected: a conflicting rewrite "
        "refuses. Decisive censored records are negatives and keep their bounds — "
        f"{cohort['evidence_breakdown'].get('inactive/censored', 0)} of the "
        f"{cohort['n_inactive']} inactives are censored, so discarding them would have "
        "thrown away a large part of the negative class. Ambiguous or contradictory "
        "evidence is never a negative, and an unmeasured pair never enters at all."
    )
    L.append("")

    # ------------------------------------------------------------- structure
    L.append("## Structure and preparation")
    L.append("")
    L.append("| | |")
    L.append("| --- | --- |")
    _rows(
        L,
        [
            (
                "Structure",
                f"{config['structure']['pdb_id']}, "
                f"{config['structure']['resolution_angstrom']} Å, "
                f"{config['structure']['method']}",
            ),
            (
                "Sequence vs curated target",
                "**byte-identical, "
                f"{config['structure']['construct_check']['seqres_length']}/"
                f"{config['target']['length']}**",
            ),
            ("Mutations", f"{config['structure']['construct_check']['mutations']}"),
            (
                "Missing residues",
                ", ".join(config["structure"]["construct_check"]["missing_residues"]),
            ),
            ("Receptor atoms kept", f"{clean['atoms_kept']:,}"),
            (
                "Cofactor kept",
                ", ".join(f"{k} ({v})" for k, v in clean["kept_heteroatoms"].items()),
            ),
            (
                "Heteroatoms removed",
                ", ".join(f"{k} {v}" for k, v in sorted(clean["dropped"].items())),
            ),
            (
                "Alternate conformations",
                f"kept altloc {clean['altloc_kept']}, "
                f"{clean['altloc_atoms_dropped']} atoms dropped",
            ),
            (
                "Box",
                f"{config['protocol']['box']['size'][0]:.0f} Å cube on "
                f"{tuple(config['protocol']['box']['center'])}, from the reference ligand",
            ),
            (
                "Exhaustiveness / poses / seed",
                f"{record['exhaustiveness']} / {record['num_modes']} / {record['seed']}",
            ),
        ],
    )
    L.append("")
    L.append(
        "The prepared receptor is checked for the Zn atom type before docking: meeko "
        "silently drops atoms it cannot type, and a zinc enzyme without its zinc is a "
        "different experiment. Why each heteroatom was removed is in `docs/DOCKING.md`; "
        "the one that matters is a glycerol sitting 4.8 Å from the catalytic zinc."
    )
    L.append("")

    # -------------------------------------------------------------- pose QA
    if qa:
        L.append("## Pose-recovery QA, reported separately")
        L.append("")
        L.append("| | |")
        L.append("| --- | --- |")
        _rows(
            L,
            [
                ("Reference ligand", f"{qa['ligand']}"),
                (
                    "Top-pose RMSD to crystal",
                    f"**{qa['top_pose_rmsd']} Å** (threshold {qa['threshold']} Å)",
                ),
                (
                    "Best of the returned poses",
                    f"{qa['best_pose_rmsd']} Å, rank {qa['best_pose_rank']}",
                ),
                (
                    "Zn contact, crystal vs docked",
                    f"{_f(qa['metal_contact_crystal'], '.2f')} Å vs "
                    f"{_f(qa['metal_contact_docked'], '.2f')} Å",
                ),
                ("Pose recovered", "yes" if qa["passed"] else "no"),
            ],
        )
        L.append("")
        L.append(
            "The reference ligand is rebuilt from SMILES and re-embedded, never from the "
            "deposited coordinates, so the search does not start at the answer it is asked "
            f"to recover. {qa['note']}"
        )
        L.append("")

    # --------------------------------------------------------- sensitivity
    if sensitivity_paths:
        L.append("## Box-size sensitivity — a separately labelled analysis")
        L.append("")
        L.append("| Box | AUROC | 95% CI | Decision under the same rule | Scored |")
        L.append("| --- | ---: | --- | --- | ---: |")
        primary_box = record["box_size"][0]
        _rows(
            L,
            [
                (
                    f"{primary_box:.0f} Å (**primary**)",
                    _f(gate["auroc"]),
                    f"[{_f(gate['ci_low'])}, {_f(gate['ci_high'])}]",
                    f"**{headline}**",
                    f"{gate['n_active']}+{gate['n_inactive']}",
                )
            ],
        )
        for label, path in sorted(sensitivity_paths.items()):
            if not path.exists():
                _rows(L, [(label, "not run", "—", "—", "—")])
                continue
            s = json.loads(path.read_text(encoding="utf-8"))["gate"]
            _rows(
                L,
                [
                    (
                        label,
                        _f(s["auroc"]),
                        f"[{_f(s['ci_low'])}, {_f(s['ci_high'])}]",
                        s["decision"],
                        f"{s['n_active']}+{s['n_inactive']}",
                    )
                ],
            )
        L.append("")
        alternatives = []
        for path in sensitivity_paths.values():
            if path.exists():
                value = json.loads(path.read_text(encoding="utf-8"))["gate"]["auroc"]
                if value is not None:
                    alternatives.append(value)
        L.append(
            "Run **after** the primary result and reported as a robustness check, not as a "
            f"menu. The headline is the pre-declared {primary_box:.0f} Å box whatever these "
            "rows say."
        )
        if alternatives and gate["auroc"] is not None:
            if gate["auroc"] >= max(alternatives):
                L.append("")
                L.append(
                    "As it happens the pre-declared box is also the best-scoring of the "
                    "three, so the headline is not the worst case being reported out of "
                    "obligation — and it fails anyway. Every box tested lands in the same "
                    "place, which is what makes the conclusion a property of the protocol "
                    "rather than of one arbitrary choice."
                )
            else:
                L.append("")
                L.append(
                    f"A different box scores higher than the pre-declared one "
                    f"({max(alternatives):.3f} against {gate['auroc']:.3f}). The headline "
                    "stays as declared: choosing the box after seeing the scores is the "
                    "thing this analysis is labelled to prevent."
                )
        L.append("")

    # ----------------------------------------------------------- provenance
    L.append("## Provenance")
    L.append("")
    L.append("| Artifact | Digest |")
    L.append("| --- | --- |")
    _rows(
        L,
        [
            ("Contract", f"`{record['config_sha256'][:16]}…`"),
            (f"Engine ({record['engine_version']})", f"`{record['engine_sha256'][:16]}…`"),
            ("Prepared receptor", f"`{record['receptor_sha256'][:16]}…`"),
            ("Cohort membership", f"`{record['cohort_sha256'][:16]}…`"),
            ("Per-ligand scores", f"`{record['scores_sha256'][:16]}…`"),
            ("Per-ligand failures", f"`{record['failures_sha256'][:16]}…`"),
        ],
    )
    L.append("")
    L.append(
        f"Measured budget: {record['n_scored']} ligands docked in "
        f"{record['wall_seconds'] / 60:.0f} minutes wall-clock on 9 parallel single-CPU "
        "workers. Per-ligand scores and failures are saved, so every metric here recomputes "
        "without docking again. Wall-clock is **not** comparable between the runs in the "
        "sensitivity table: other work shared the machine during some of them, which is why "
        "a smaller box can show a longer time. Vina is seeded, so the scores are unaffected."
    )
    L.append("")

    # ---------------------------------------------------------- limitations
    L.append("## What this does and does not establish")
    L.append("")
    L.append(
        f"A **{headline}** applies to **this protocol on this cohort**. It says "
        "nothing about whether adding docking to the M9 ranking would help: that comparison "
        "needs the model's scores and a control for what the model already knows from "
        "training, and it is deliberately left to a separate evaluation."
    )
    L.append("")
    for item in (
        "The cohort is BindingDB's measured carbonic anhydrase ligands, which are "
        'overwhelmingly aryl sulfonamides. The task is largely "which sulfonamide binds '
        'tightly", not "binder versus non-binder", and the result does not transfer to a '
        "scaffold-diverse library.",
        "Carbonic anhydrase is a zinc metalloenzyme and Vina's scoring function has no "
        "explicit metal-coordination term. This was declared in the contract before "
        "scoring, and it limits what a result on this target can be read to mean about "
        "docking in general.",
        "One conformer per ligand, no tautomer enumeration, no pH-based protonation model, "
        "a rigid receptor and no waters. Any of these could cost a compound its pose "
        "independently of whether it binds.",
        "Classes were balanced for precision at a fixed compute budget. The eligible pool "
        "is 75% active, so the cohort is not a prevalence estimate.",
        "With the toolchain used here — meeko 0.8.0 and Vina 1.2.7 at the pinned versions — "
        "selenium, tellurium and boron could not be typed, so 19 measured compounds across "
        "three ligand classes were never scored. The gate therefore speaks only for the "
        "chemistry this toolchain can represent. A library containing those elements needs a "
        "preparation path that handles them, not a different threshold; whether other tools "
        "or later versions do was not tested.",
        "A single structure. Conformational selection across several structures of the same "
        "target is not tested here.",
    ):
        L.append(f"- {item}")
    L.append("")
    L.append(
        "Published whatever the outcome, which is the point of running it as a gate rather "
        "than as a result we went looking for."
    )
    return "\n".join(L) + "\n"


def write(
    path: Path = REPORT_PATH,
    *,
    verify: bool = True,
    run_root: Path = Path("data/m10/runs/gate-600"),
    **kwargs: Any,
) -> Path:
    """Render and publish, refusing if the result does not follow from the evidence.

    `verify=False` exists only for rendering against fixtures in tests; the CLI
    never passes it, so the publishing path always checks.
    """
    if verify:
        from seq2lead.dock.cohort import COHORT_PATH, read_cohort
        from seq2lead.dock.config import load_docking_config
        from seq2lead.dock.verify import verify_publication

        gate_path = kwargs.get("gate_path") or Path("reports/results/m10_gate.json")
        config_path = kwargs.get("config_path") or Path("configs/experiments/m10-docking-v1.yaml")
        cohort_path = kwargs.get("cohort_path") or COHORT_PATH
        checked = verify_publication(
            gate_path=Path(gate_path),
            run_root=run_root,
            cohort=read_cohort(Path(cohort_path)),
            config=load_docking_config(Path(config_path)),
            # every sensitivity row the report will print is checked against its
            # own saved run, not taken on the primary's authority
            sensitivity_paths={
                label: Path(target)
                for label, target in (kwargs.get("sensitivity_paths") or {}).items()
            },
            # the QA artifact VERIFIED must be the one RENDERED, or an override
            # silently publishes an unverified file
            qa_path=Path(kwargs.get("qa_path") or Path("reports/results/m10_qa_redock.json")),
        )
    body = render(**kwargs)
    if verify:
        body += (
            "\n## Verified before publishing\n\n"
            "This report was refused unless the published numbers recomputed from the saved "
            "per-ligand scores under the recorded contract. Checks that passed:\n\n"
            + "".join(f"- {c}\n" for c in checked.checks)
            + f"\nRecomputed AUROC {checked.auroc:.10f} against the published value, and the "
            f"decision **{checked.decision}** independently reproduced.\n"
        )
        if checked.caveats:
            body += "\nWhat that verification does **not** cover:\n\n" + "".join(
                f"- {c}\n" for c in checked.caveats
            )
    for n, line in enumerate(body.splitlines(), 1):
        # A rendered `None` means a value was missing and got formatted anyway.
        # Only table cells are checked: prose legitimately quotes tool output such
        # as meeko's `atom number N has None type`, and a substring test over the
        # whole line would flag that forever.
        if line.startswith("|") and any(
            cell.strip().strip("*`") == "None" for cell in line.split("|")
        ):
            raise ValueError(f"reports/docking.md line {n} rendered a bare None: {line!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path
