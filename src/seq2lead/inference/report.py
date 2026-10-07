"""Rendering a ranking: a readable table and a CSV.

Both carry the same framing, because a CSV outlives the terminal it was produced
in and is the thing most likely to be pasted into a slide with the caveats lost.
"""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from seq2lead.inference.rank import Ranking
    from seq2lead.inference.shortlist import Shortlist

#: One sentence, used verbatim in the table header, the CSV preamble and the docs.
WHAT_THESE_ARE = (
    "Prioritised candidates for testing. Predicted pKi is a ranking score, not a "
    "binding probability, not a calibrated confidence, and not evidence that any "
    "compound binds. Nothing here has been experimentally tested by this project."
)

CSV_COLUMNS = ["rank", "compound_id", "smiles", "predicted_pki", "warnings"]


def render(result: Ranking, shortlist: Shortlist | None = None) -> str:
    lines = [
        f"query: {result.sequence_length:,} residues  sha256 {result.sequence_sha256[:16]}…",
    ]
    if result.header:
        lines.append(f"       {result.header}")
    lines += [
        f"library: {result.library_name} ({result.library_members:,} compounds, bundled)",
        f"bundle: {result.bundle_version}  projection_dim={result.projection_dim}  "
        f"device={result.device}",
        "",
        WHAT_THESE_ARE,
        "",
    ]
    for note in result.notes:
        lines.append(f"note: {note}")
    for warning in result.warnings:
        lines.append(f"note: {warning}")
    if result.notes or result.warnings:
        lines.append("")

    rows = shortlist.kept if shortlist else result.rows
    if shortlist:
        for applied in shortlist.applied:
            lines.append(f"shortlist: {applied}")
        lines.append(
            f"shortlist: {len(shortlist.kept):,} kept, {len(shortlist.removed):,} removed. "
            "Removed compounds keep the rank and score they had; the unfiltered "
            "ranking is unchanged."
        )
        lines.append("")

    header = f"{'rank':>5}  {'compound':>12}  {'pred pKi':>9}  {'warnings':<24}  smiles"
    lines += [header, "-" * len(header)]
    for row in rows:
        marks = list(row.flags)
        if row.tied_with:
            marks.append(f"tied×{row.tied_with + 1}")
        lines.append(
            f"{row.rank:>5}  {row.compound_id:>12}  {row.score_pki:>9.3f}  "
            f"{(','.join(marks) or '-'):<24}  {row.smiles[:60]}"
        )

    if shortlist and shortlist.removed:
        lines += ["", "Removed from this view, with their original rank and score kept:"]
        head = f"{'orig rank':>9}  {'compound':>12}  {'pred pKi':>9}  {'reason':<22}  detail"
        lines += [head, "-" * len(head)]
        for gone in shortlist.removed[:40]:
            lines.append(
                f"{gone.original_rank:>9}  {gone.compound_id:>12}  {gone.score_pki:>9.3f}  "
                f"{gone.reason:<22}  {gone.detail[:52]}"
            )
        if len(shortlist.removed) > 40:
            lines.append(f"{'':>9}  … {len(shortlist.removed) - 40:,} more, all in the CSV")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, result: Ranking, shortlist: Shortlist | None = None) -> Path:
    """The ranking as CSV, with the framing carried in a comment preamble."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = shortlist.kept if shortlist else result.rows
    with path.open("w", newline="", encoding="utf-8") as fh:
        for line in [
            WHAT_THESE_ARE,
            f"query sha256: {result.sequence_sha256}",
            f"query length: {result.sequence_length} residues",
            f"library: {result.library_name} ({result.library_members} compounds, bundled)",
            f"bundle: {result.bundle_version}",
            "score: predicted pKi = scale * cosine(compound, protein) + offset",
        ]:
            fh.write(f"# {line}\n")
        for note in result.notes + result.warnings:
            fh.write(f"# note: {note}\n")
        if shortlist:
            for applied in shortlist.applied:
                fh.write(f"# shortlist: {applied}\n")

        writer = csv.writer(fh)
        writer.writerow([*CSV_COLUMNS, "in_shortlist", "removed_reason", "removed_detail"])
        for row in rows:
            marks = list(row.flags)
            if row.tied_with:
                marks.append(f"tied_with_{row.tied_with}_others")
            writer.writerow(
                [
                    row.rank,
                    row.compound_id,
                    row.smiles,
                    f"{row.score_pki:.6f}",
                    ";".join(marks),
                    "yes" if shortlist else "",
                    "",
                    "",
                ]
            )
        if shortlist:
            for gone in shortlist.removed:
                writer.writerow(
                    [
                        gone.original_rank,
                        gone.compound_id,
                        gone.smiles,
                        f"{gone.score_pki:.6f}",
                        "",
                        "no",
                        gone.reason,
                        gone.detail,
                    ]
                )
    return path
