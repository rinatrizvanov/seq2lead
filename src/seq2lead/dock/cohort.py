"""The measured cohort the gate is scored on, frozen before any docking.

Two things this module refuses to do, because both would manufacture signal:

* treat an unmeasured pair as a negative -- only measured labels are eligible,
  and the demonstration library never enters here;
* flatten a censored record into a point value -- a decisive censored negative
  keeps its relation and its bound, and an ambiguous one is not a negative at
  all.

The cohort is frozen to a file before a single ligand is docked, and the gate
reads only that file.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from seq2lead.dock.artifacts import ArtifactCollision, sha256_text, utc_now

if TYPE_CHECKING:
    import psycopg

    from seq2lead.dock.config import DockingConfig

COHORT_PATH = Path("reports/results/m10_cohort.json")

#: A gate class is a decisive measured label. Nothing else is admissible.
ALLOWED_LABELS = frozenset({"active", "inactive"})
ALLOWED_EVIDENCE = frozenset({"exact", "censored", "both"})


def _num(value: float | None) -> str:
    """Canonical text for a nullable float, so the digest is stable."""
    return "" if value is None else repr(float(value))


@dataclass(frozen=True)
class CohortMember:
    compound_id: int
    label: str
    evidence: str  # exact | censored | both
    smiles: str
    n_heavy_atoms: int
    lo_pki: float | None  # censoring bounds, preserved as stored
    hi_pki: float | None
    exact_median_pki: float | None
    n_exact: int
    n_censored: int

    @property
    def is_active(self) -> bool:
        return self.label == "active"


@dataclass
class Cohort:
    name: str
    experiment: str
    config_sha256: str
    selection_sha256: str
    endpoint: str
    endpoint_id: int
    target_id: int
    method: str
    seed: int
    n_active: int
    n_inactive: int
    pool_active: int
    pool_inactive: int
    evidence_breakdown: dict[str, int]
    created_at: str
    members: list[CohortMember] = field(default_factory=list)

    def sha256(self) -> str:
        """Digest of the file as written, including when."""
        return sha256_text(self.to_json())

    def content_sha256(self) -> str:
        """Digest of everything about every member that the docking actually uses.

        `membership_sha256` covers only (id, label, evidence), which is enough to
        say the same compounds were selected but not enough to say the same
        *molecules* were docked: swapping a member's SMILES for `CCO` leaves it
        untouched. Every field that reaches ligand preparation or the gate is
        folded in here, so a tampered structure or a rewritten bound changes the
        identity the runner checks.
        """
        rows = [
            "\t".join(
                (
                    str(m.compound_id),
                    m.label,
                    m.evidence,
                    m.smiles,
                    str(m.n_heavy_atoms),
                    _num(m.lo_pki),
                    _num(m.hi_pki),
                    _num(m.exact_median_pki),
                    str(m.n_exact),
                    str(m.n_censored),
                )
            )
            for m in self.members
        ]
        header = "\t".join(
            (self.name, self.endpoint, str(self.endpoint_id), str(self.target_id), str(self.seed))
        )
        return sha256_text("\n".join([header, *sorted(rows)]))

    def validate(self) -> list[str]:
        """Structural checks a cohort must pass however it arrived.

        A cohort handed in as an object gets the same scrutiny as one loaded from
        the frozen file -- otherwise the file is guarded and the in-memory path
        is not.
        """
        problems: list[str] = []
        ids = [m.compound_id for m in self.members]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            problems.append(f"duplicate compound ids: {duplicates[:10]}")
        bad_labels = sorted({m.label for m in self.members} - set(ALLOWED_LABELS))
        if bad_labels:
            problems.append(
                f"labels outside {sorted(ALLOWED_LABELS)}: {bad_labels}. Ambiguous or "
                "unlabelled evidence is never a gate class."
            )
        bad_evidence = sorted({m.evidence for m in self.members} - set(ALLOWED_EVIDENCE))
        if bad_evidence:
            problems.append(f"evidence outside {sorted(ALLOWED_EVIDENCE)}: {bad_evidence}")
        n_act = sum(1 for m in self.members if m.label == "active")
        n_inact = sum(1 for m in self.members if m.label == "inactive")
        if n_act != self.n_active or n_inact != self.n_inactive:
            problems.append(
                f"declared {self.n_active} active / {self.n_inactive} inactive but holds "
                f"{n_act} / {n_inact}"
            )
        if len(self.members) != self.n_active + self.n_inactive:
            problems.append(
                f"{len(self.members)} members against a declared total of "
                f"{self.n_active + self.n_inactive}"
            )
        missing_smiles = [m.compound_id for m in self.members if not m.smiles]
        if missing_smiles:
            problems.append(f"members with no SMILES: {missing_smiles[:10]}")
        return problems

    def membership_sha256(self) -> str:
        """Digest of WHICH compounds and labels, independent of when it was built.

        `sha256()` covers `created_at`, so two builds of the same cohort differ
        there. This one does not: it is what must stay constant for a published
        gate decision to still refer to the same experiment.
        """
        rows = [f"{m.compound_id}\t{m.label}\t{m.evidence}" for m in self.members]
        return sha256_text(
            "\n".join(
                [f"{self.name}\t{self.endpoint}\t{self.target_id}\t{self.seed}", *sorted(rows)]
            )
        )

    def to_json(self) -> str:
        body = asdict(self)
        return json.dumps(body, indent=2, sort_keys=True) + "\n"


def eligible_rows(
    conn: psycopg.Connection, endpoint_id: int, target_id: int
) -> list[dict[str, Any]]:
    """Measured, decisive, in-scope labels for one target. Nothing else qualifies."""
    rows = conn.execute(
        """
        SELECT l.compound_id, l.label, l.evidence, c.canonical_smiles, c.n_heavy_atoms,
               l.lo_pki, l.hi_pki, l.exact_median_pki, l.n_exact, l.n_censored
        FROM pair_label l JOIN compound c ON c.id = l.compound_id
        WHERE l.endpoint_id = %s AND l.target_id = %s
          AND l.status = 'ok'
          AND NOT l.excluded_from_eval
          AND l.in_benchmark_scope
          AND l.label IN ('active', 'inactive')
        ORDER BY l.compound_id
        """,
        (endpoint_id, target_id),
    ).fetchall()
    keys = (
        "compound_id",
        "label",
        "evidence",
        "smiles",
        "n_heavy_atoms",
        "lo_pki",
        "hi_pki",
        "exact_median_pki",
        "n_exact",
        "n_censored",
    )
    return [dict(zip(keys, r, strict=True)) for r in rows]


def build_cohort(conn: psycopg.Connection, config: DockingConfig) -> Cohort:
    """Sample the frozen cohort. Deterministic given the config's seed."""
    spec = config.cohort_spec
    rows = eligible_rows(conn, config.endpoint_id, config.target_id)
    actives = [r for r in rows if r["label"] == "active"]
    inactives = [r for r in rows if r["label"] == "inactive"]

    want_a, want_i = int(spec["n_active"]), int(spec["n_inactive"])
    if len(actives) < want_a or len(inactives) < want_i:
        raise ValueError(
            f"cohort asks for {want_a} active / {want_i} inactive but the eligible pool "
            f"holds {len(actives)} / {len(inactives)}"
        )

    # ORDER BY compound_id above plus a seeded shuffle here is what makes this
    # reproducible: Postgres gives no order guarantee without it.
    rng = random.Random(int(spec["seed"]))
    chosen = sorted(rng.sample(actives, want_a), key=lambda r: r["compound_id"])
    chosen += sorted(rng.sample(inactives, want_i), key=lambda r: r["compound_id"])

    breakdown: dict[str, int] = {}
    for r in chosen:
        breakdown[f"{r['label']}/{r['evidence']}"] = (
            breakdown.get(f"{r['label']}/{r['evidence']}", 0) + 1
        )

    return Cohort(
        name=str(spec["name"]),
        experiment=config.version,
        config_sha256=config.config_sha256,
        selection_sha256=config.selection_sha256(),
        endpoint=config.endpoint_name,
        endpoint_id=config.endpoint_id,
        target_id=config.target_id,
        method=str(spec["method"]),
        seed=int(spec["seed"]),
        n_active=want_a,
        n_inactive=want_i,
        pool_active=len(actives),
        pool_inactive=len(inactives),
        evidence_breakdown=breakdown,
        created_at=utc_now(),
        members=[CohortMember(**r) for r in chosen],
    )


def write_cohort(cohort: Cohort, path: Path = COHORT_PATH, *, overwrite: bool = False) -> Path:
    """Freeze it. A conflicting rewrite refuses; an identical one is a no-op."""
    body = cohort.to_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        if path.read_text(encoding="utf-8") == body:
            return path
        raise ArtifactCollision(
            f"{path} already holds a different frozen cohort. Refusing to replace it: the "
            "gate reads this file, so changing it would change which compounds a published "
            "gate decision was made on. Write to a new path, or pass overwrite explicitly."
        )
    path.write_text(body, encoding="utf-8")
    return path


def read_cohort(path: Path = COHORT_PATH) -> Cohort:
    raw = json.loads(path.read_text(encoding="utf-8"))
    members = [CohortMember(**m) for m in raw.pop("members")]
    return Cohort(**raw, members=members)
