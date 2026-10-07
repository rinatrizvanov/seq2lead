"""Optional shortlisting over a ranking. Off unless asked for.

The unfiltered ranking is the default and is never replaced. Shortlisting is a
*view*: every removed compound keeps the score and the rank it had, and is
reported with the reason it was removed, so a reader can see what the filter did
rather than inferring it from an absence.

Two kinds, both opt-in and independent:

* **property filters** -- numeric windows on computed descriptors;
* **diversity selection** -- a greedy pick down the ranking.

**Not applied, silently or otherwise:** drug-likeness scores, PAINS or other
structural-alert lists, and any toxicity or synthesisability heuristic. Those
encode assumptions about what a useful compound looks like, they disagree with
each other, and applying one invisibly would quietly reshape a ranking the rest
of this project goes to some trouble to keep auditable. If you want them, they
belong in a named, separately reported step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seq2lead.inference.rank import RankedRow

#: Stated rather than assumed, because a diversity claim means nothing without it.
DIVERSITY_METHOD = {
    "fingerprint": "Morgan (ECFP-style), radius 2, 2048 bits, chirality on",
    "fingerprint_source": (
        "computed from the bundled SMILES with RDKit at shortlist time. This is the "
        "same family as the fingerprint the model consumes, but it is recomputed "
        "here rather than read from the model's cache, and no claim is made that "
        "the bits are identical to the cached ones."
    ),
    "similarity": "Tanimoto on those bits",
    "rule": (
        "greedy selection down the ranking: walk compounds best-score-first and keep "
        "one if its maximum Tanimoto similarity to everything already kept is below "
        "the threshold. Deterministic, order-dependent by design, and it never "
        "promotes a lower-scoring compound above a higher-scoring one."
    ),
}


class ShortlistError(RuntimeError):
    """Shortlisting was requested but cannot be performed."""


@dataclass
class Removed:
    """A row kept out of the filtered view, with everything it had preserved."""

    compound_id: str
    original_rank: int
    score_pki: float
    reason: str
    detail: str = ""
    smiles: str = ""


@dataclass
class Shortlist:
    kept: list[RankedRow] = field(default_factory=list)
    removed: list[Removed] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    method: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        by_reason: dict[str, int] = {}
        for r in self.removed:
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        return {
            "applied": self.applied,
            "kept": len(self.kept),
            "removed": len(self.removed),
            "removed_by_reason": by_reason,
            "method": self.method,
        }


def _rdkit():
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import Descriptors, rdFingerprintGenerator

        RDLogger.DisableLog("rdApp.*")
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ShortlistError(
            "shortlisting needs RDKit, which is not importable in this environment. "
            "The unfiltered ranking does not need it; install RDKit only if you want "
            "property filters or diversity selection."
        ) from exc
    return Chem, Descriptors, rdFingerprintGenerator


def properties(smiles: str) -> dict[str, float] | None:
    """Molecular weight and TPSA, or None if RDKit cannot parse the SMILES."""
    Chem, Descriptors, _ = _rdkit()
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return {
        "molecular_weight": float(Descriptors.MolWt(mol)),
        "tpsa": float(Descriptors.TPSA(mol)),
    }


def filter_by_properties(
    rows: list[RankedRow],
    *,
    min_mw: float | None = None,
    max_mw: float | None = None,
    min_tpsa: float | None = None,
    max_tpsa: float | None = None,
) -> Shortlist:
    """Keep rows inside the requested windows.

    An unparseable structure is reported as its own removal reason rather than
    dropped silently, because "the filter could not be evaluated" is a different
    statement from "the compound failed the filter".
    """
    windows = {
        "molecular_weight": (min_mw, max_mw),
        "tpsa": (min_tpsa, max_tpsa),
    }
    active = {k: v for k, v in windows.items() if v[0] is not None or v[1] is not None}
    if not active:
        return Shortlist(kept=list(rows), applied=[], method={})

    described = ", ".join(
        f"{name} in [{lo if lo is not None else '-inf'}, {hi if hi is not None else '+inf'}]"
        for name, (lo, hi) in active.items()
    )
    result = Shortlist(applied=[f"property filter: {described}"], method={"windows": active})
    for row in rows:
        props = properties(row.smiles)
        if props is None:
            result.removed.append(
                Removed(
                    row.compound_id,
                    row.original_rank,
                    row.score_pki,
                    "unparseable_structure",
                    "RDKit could not parse the bundled SMILES, so the filter could "
                    "not be evaluated. Removed from the filtered view only.",
                    smiles=row.smiles,
                )
            )
            continue
        failed = [
            f"{name}={props[name]:.1f} outside [{lo}, {hi}]"
            for name, (lo, hi) in active.items()
            if (lo is not None and props[name] < lo) or (hi is not None and props[name] > hi)
        ]
        if failed:
            result.removed.append(
                Removed(
                    row.compound_id,
                    row.original_rank,
                    row.score_pki,
                    "property_filter",
                    "; ".join(failed),
                    smiles=row.smiles,
                )
            )
        else:
            result.kept.append(row)
    return result


def select_diverse(rows: list[RankedRow], *, n: int, threshold: float = 0.7) -> Shortlist:
    """Greedy pick down the ranking, skipping near-duplicates of what is already kept."""
    Chem, _, rdfp = _rdkit()
    if not 0.0 < threshold <= 1.0:
        raise ShortlistError(f"diversity threshold must be in (0, 1]; got {threshold}")

    gen = rdfp.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
    from rdkit import DataStructs

    method = dict(DIVERSITY_METHOD)
    method["threshold"] = threshold
    method["requested"] = n
    result = Shortlist(
        applied=[f"diversity selection: max Tanimoto {threshold} to anything kept, target {n}"],
        method=method,
    )

    kept_fps: list[Any] = []
    for row in rows:
        if len(result.kept) >= n > 0:
            result.removed.append(
                Removed(
                    row.compound_id,
                    row.original_rank,
                    row.score_pki,
                    "beyond_requested_count",
                    f"the diverse set reached the requested {n} before this rank",
                    smiles=row.smiles,
                )
            )
            continue
        mol = Chem.MolFromSmiles(row.smiles)
        if mol is None:
            result.removed.append(
                Removed(
                    row.compound_id,
                    row.original_rank,
                    row.score_pki,
                    "unparseable_structure",
                    "RDKit could not parse the bundled SMILES, so similarity could not be computed",
                    smiles=row.smiles,
                )
            )
            continue
        fp = gen.GetFingerprint(mol)
        if kept_fps:
            similarities = DataStructs.BulkTanimotoSimilarity(fp, kept_fps)
            worst = max(similarities)
            if worst >= threshold:
                closest = max(range(len(similarities)), key=similarities.__getitem__)
                nearest = result.kept[closest]
                result.removed.append(
                    Removed(
                        row.compound_id,
                        row.original_rank,
                        row.score_pki,
                        "too_similar",
                        f"Tanimoto {worst:.2f} to compound {nearest.compound_id} "
                        f"(rank {nearest.original_rank}), at or above {threshold}",
                        smiles=row.smiles,
                    )
                )
                continue
        kept_fps.append(fp)
        result.kept.append(row)
    return result
