"""The work the browser asks for, expressed over the standalone inference API.

Nothing here re-implements scoring. It calls `seq2lead.inference` exactly as the
CLI does, so the interface cannot drift from the command line: a test asserts the
two produce the same ranking for the same query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from seq2lead.inference.rank import ProteinEncoder, rank
from seq2lead.inference.report import WHAT_THESE_ARE, write_csv
from seq2lead.inference.sequence import SequenceError, parse_fasta, validate
from seq2lead.inference.shortlist import ShortlistError, filter_by_properties, select_diverse
from seq2lead.web.jobs import Cancelled

if TYPE_CHECKING:
    from seq2lead.inference.bundle import InferenceBundle
    from seq2lead.web.jobs import Job

#: Depictions are expensive to draw and never change for a given compound, so
#: each is drawn once. Keyed by (bundle identity, row): keying on the row alone
#: would serve one bundle's structure for another bundle's compound, because row
#: 0 means a different molecule in every library.
# Keyed by bundle identity, row AND size. Dropping the size from the key meant
# whichever size was drawn first was served for every later size, so the detail
# panel's large depiction came back at thumbnail resolution.
_DEPICTION_CACHE: dict[tuple[str, int, int, int], str] = {}

#: Descriptors are pure functions of a SMILES string, and the filtered library
#: browser asked for them again on every page. Cached by SMILES, so a second page
#: of the same filter costs nothing rather than recomputing all 25,000.
_DESCRIPTOR_CACHE: dict[str, dict[str, float] | None] = {}

# The detail panel shows more than the two descriptors the filters use. Keeping
# it in its own cache leaves `properties()` — and therefore the filter
# semantics shared with the CLI — untouched.
_DETAIL_CACHE: dict[str, dict[str, Any] | None] = {}


@dataclass
class Session:
    """Bundle and encoder, loaded once and shared by every request."""

    bundle: InferenceBundle
    encoder: ProteinEncoder

    @classmethod
    def open(cls, bundle: InferenceBundle, device: str | None = None) -> Session:
        return cls(bundle=bundle, encoder=ProteinEncoder(bundle.protein_spec, device))

    @property
    def identity(self) -> str:
        """What distinguishes this bundle's rows from another bundle's.

        The library member digest if the bundle records one, else the projection
        file's digest, which changes whenever the compounds do.
        """
        library = self.bundle.manifest.get("library", {})
        recorded = library.get("member_sha256")
        if recorded:
            return str(recorded)
        files = self.bundle.manifest.get("files", {})
        return str(files.get("library/projections.npy", {}).get("sha256", "unknown"))


def bundle_summary(session: Session) -> dict[str, Any]:
    m = session.bundle.manifest
    return {
        "bundle_version": m["bundle_version"],
        "library": m["library"],
        "counts": m["counts"],
        "model": {k: m["model"][k] for k in ("projection_dim", "scoring_rule")},
        "protein": m["representation"]["protein"],
        "device": session.encoder.device,
        "encoder_loaded": session.encoder._model is not None,
        "disclaimer": WHAT_THESE_ARE,
    }


def descriptors(smiles: str) -> dict[str, float] | None:
    """Molecular weight and TPSA for one SMILES, computed at most once."""
    if smiles not in _DESCRIPTOR_CACHE:
        from seq2lead.inference.shortlist import properties

        _DESCRIPTOR_CACHE[smiles] = properties(smiles)
    return _DESCRIPTOR_CACHE[smiles]


def detail_descriptors(smiles: str) -> dict[str, Any] | None:
    """Standard medicinal-chemistry readouts for one compound, computed once.

    These describe the molecule as drawn. None is predicted, and none took part
    in ranking: the model sees only the ECFP4 fingerprint.
    """
    if smiles in _DETAIL_CACHE:
        return _DETAIL_CACHE[smiles]
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors

        RDLogger.DisableLog("rdApp.*")
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError("unparseable")
        out: dict[str, Any] = {
            "formula": rdMolDescriptors.CalcMolFormula(mol),
            "molecular_weight": round(float(Descriptors.MolWt(mol)), 2),
            "clogp": round(float(Crippen.MolLogP(mol)), 2),
            "tpsa": round(float(Descriptors.TPSA(mol)), 1),
            "h_bond_donors": int(rdMolDescriptors.CalcNumHBD(mol)),
            "h_bond_acceptors": int(rdMolDescriptors.CalcNumHBA(mol)),
            "rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(mol)),
            "rings": int(rdMolDescriptors.CalcNumRings(mol)),
            "aromatic_rings": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
            "heavy_atoms": int(mol.GetNumHeavyAtoms()),
            "stereocentres": len(Chem.FindMolChiralCenters(mol, includeUnassigned=True)),
        }
    except Exception:
        out = None
    _DETAIL_CACHE[smiles] = out
    return out


def compound_detail(session: Session, row: int) -> dict[str, Any]:
    """Everything the detail panel shows for one library member."""
    if not 0 <= row < session.bundle.n_compounds:
        raise IndexError(f"row {row} is outside this library's 0-{session.bundle.n_compounds - 1}")
    smiles = session.bundle.smiles[row]
    return {
        "row": row,
        "compound_id": session.bundle.compound_ids[row],
        "smiles": smiles,
        "descriptors": detail_descriptors(smiles),
        "library": session.bundle.library.get("name", "unknown"),
    }


def depict(session: Session, row: int, width: int = 260, height: int = 200) -> str:
    """An SVG depiction of one library compound, or a readable placeholder."""
    key = (session.identity, row, width, height)
    if key in _DEPICTION_CACHE:
        return _DEPICTION_CACHE[key]
    if not 0 <= row < session.bundle.n_compounds:
        raise IndexError(f"row {row} is outside this library's 0-{session.bundle.n_compounds - 1}")
    smiles = session.bundle.smiles[row]
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem.Draw import rdMolDraw2D

        RDLogger.DisableLog("rdApp.*")
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError("unparseable")
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
    except Exception:
        svg = (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>"
            f"<rect width='100%' height='100%' fill='#f6f6f6'/>"
            f"<text x='50%' y='50%' text-anchor='middle' font-size='11' fill='#999'>"
            f"no depiction</text></svg>"
        )
    _DEPICTION_CACHE[key] = svg
    return svg


def browse(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 24,
    query: str = "",
    min_mw: float | None = None,
    max_mw: float | None = None,
    min_tpsa: float | None = None,
    max_tpsa: float | None = None,
) -> dict[str, Any]:
    """Paginated library browser. Filters here are a *view*, as on the CLI."""
    ids, smiles = session.bundle.compound_ids, session.bundle.smiles
    rows = list(range(len(ids)))

    needle = query.strip().lower()
    if needle:
        rows = [i for i in rows if needle in ids[i].lower() or needle in smiles[i].lower()]

    filtered_out = 0
    if any(v is not None for v in (min_mw, max_mw, min_tpsa, max_tpsa)):
        kept = []
        for i in rows:
            props = descriptors(smiles[i])
            if props is None:
                filtered_out += 1
                continue
            windows = {"molecular_weight": (min_mw, max_mw), "tpsa": (min_tpsa, max_tpsa)}
            if all(
                (lo is None or props[name] >= lo) and (hi is None or props[name] <= hi)
                for name, (lo, hi) in windows.items()
            ):
                kept.append(i)
            else:
                filtered_out += 1
        rows = kept

    total = len(rows)
    page_size = max(1, min(page_size, 96))
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    window = rows[(page - 1) * page_size : page * page_size]
    return {
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "total": total,
        "library_total": len(ids),
        "filtered_out": filtered_out,
        # Descriptors for the 24 rows actually shown, from the shared cache: the
        # browser displays them per tile, and recomputing a page is free once the
        # cache is warm.
        "rows": [
            {
                "row": i,
                "compound_id": ids[i],
                "smiles": smiles[i],
                "properties": descriptors(smiles[i]),
            }
            for i in window
        ],
    }


def run_ranking(session: Session, job: Job, request: dict[str, Any]) -> dict[str, Any]:
    """The ranking a browser request asks for. Phases report progress and check cancellation."""

    def checkpoint(phase: str, progress: float) -> None:
        if job.cancelled:
            raise Cancelled
        job.phase, job.progress = phase, progress

    checkpoint("reading the sequence", 0.05)
    text = str(request.get("sequence") or "")
    source = str(request.get("source") or "pasted sequence")
    query = parse_fasta(text, source)
    spec = session.bundle.protein_spec
    cleaned, notes = validate(
        query, training_window=spec["training_window"], max_length=spec["max_length"]
    )

    checkpoint("loading the protein encoder", 0.15)
    encoder_warnings = session.encoder.load()

    checkpoint("embedding the query", 0.35)
    if job.cancelled:
        raise Cancelled
    top_n = int(request.get("top_n") or 50)
    result = rank(
        session.bundle,
        cleaned,
        header=query.header,
        top_n=top_n,
        encoder=session.encoder,
        notes=notes,
    )
    result.warnings.extend(w for w in encoder_warnings if w not in result.warnings)

    checkpoint("scoring the library", 0.8)
    shortlist = None
    windows = {
        k: (float(request[k]) if request.get(k) not in (None, "", 0) else None)
        for k in ("min_mw", "max_mw", "min_tpsa", "max_tpsa")
    }
    diverse = int(request.get("diverse") or 0)
    try:
        if any(v is not None for v in windows.values()):
            checkpoint("applying property filters", 0.85)
            shortlist = filter_by_properties(result.rows, **windows)
        if diverse > 0:
            checkpoint("selecting a diverse subset", 0.92)
            source_rows = shortlist.kept if shortlist else result.rows
            picked = select_diverse(
                source_rows, n=diverse, threshold=float(request.get("diversity_threshold") or 0.7)
            )
            if shortlist:
                picked.removed = shortlist.removed + picked.removed
                picked.applied = shortlist.applied + picked.applied
            shortlist = picked
    except ShortlistError as exc:
        result.warnings.append(f"shortlisting was requested but could not run: {exc}")

    checkpoint("done", 1.0)
    return {"ranking": result, "shortlist": shortlist}


def ranking_payload(outcome: dict[str, Any], session: Session) -> dict[str, Any]:
    """Shape a finished ranking for the browser. The unfiltered rows are always present."""
    result = outcome["ranking"]
    shortlist = outcome["shortlist"]
    # The bundle row is what the depiction endpoint keys on. Carrying it here
    # means a ranked row can be drawn without the library browser having been
    # opened first, which an earlier version silently depended on.
    row_of = {cid: i for i, cid in enumerate(session.bundle.compound_ids)}
    rows = [
        {
            "rank": r.rank,
            "row": row_of.get(r.compound_id, -1),
            "compound_id": r.compound_id,
            "smiles": r.smiles,
            "score_pki": round(r.score_pki, 6),
            "tied_with": r.tied_with,
            "flags": r.flags,
        }
        for r in result.rows
    ]
    payload: dict[str, Any] = {
        "disclaimer": WHAT_THESE_ARE,
        "query": {
            "header": result.header,
            "length": result.sequence_length,
            "sha256": result.sequence_sha256,
        },
        "library": {"name": result.library_name, "members": result.library_members},
        "device": result.device,
        "notes": result.notes,
        "warnings": result.warnings,
        "distribution": result.distribution,
        "rows": rows,
        "shortlist": None,
    }
    if shortlist is not None:
        kept_ids = {r.compound_id for r in shortlist.kept}
        payload["shortlist"] = {
            **shortlist.summary(),
            "kept_ids": sorted(kept_ids),
            # `removed` is the count, from summary(). The rows get their own key:
            # one field meaning both a number and a list is a trap for any caller.
            "removed_rows": [
                {
                    "compound_id": g.compound_id,
                    "original_rank": g.original_rank,
                    "score_pki": round(g.score_pki, 6),
                    "smiles": g.smiles,
                    "reason": g.reason,
                    "detail": g.detail,
                }
                for g in shortlist.removed
            ],
        }
    return payload


def ranking_csv(outcome: dict[str, Any]) -> bytes:
    """The same CSV the CLI writes, produced in memory."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(Path(tmp) / "ranking.csv", outcome["ranking"], outcome["shortlist"])
        return path.read_bytes()


__all__ = [
    "Cancelled",
    "SequenceError",
    "Session",
    "browse",
    "bundle_summary",
    "compound_detail",
    "depict",
    "detail_descriptors",
    "ranking_csv",
    "ranking_payload",
    "run_ranking",
]
