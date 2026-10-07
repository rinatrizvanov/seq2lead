"""Reading one query sequence, from a file or from pasted text.

The database-backed path's reader stops at the second ``>`` and ranks the first
record. That is a silent choice between two proteins the caller supplied, so this
reader refuses instead and names the records it found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RESIDUES = set("ACDEFGHIKLMNPQRSTVWYBXZUO")
#: Codes that are legal but ambiguous. Worth saying so rather than silently
#: embedding them: ESM-2 has tokens for them, but they are not a single residue.
AMBIGUOUS = {"B": "Asx (Asn or Asp)", "Z": "Glx (Gln or Glu)", "X": "any residue"}


class SequenceError(ValueError):
    """The query sequence is missing, malformed or unusable."""


@dataclass
class Query:
    header: str
    sequence: str
    source: str


def parse_fasta(text: str, source: str) -> Query:
    """One record only. Two or more is an error, not a choice made for the caller."""
    if not text.strip():
        raise SequenceError(f"{source} is empty")

    records: list[tuple[str, list[str]]] = []
    bare: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            records.append((stripped[1:].strip(), []))
            continue
        if records:
            records[-1][1].append(stripped)
        else:
            bare.append(stripped)

    if len(records) > 1:
        names = [r[0][:40] or "(no description)" for r in records[:4]]
        more = f" and {len(records) - 4} more" if len(records) > 4 else ""
        raise SequenceError(
            f"{source} holds {len(records)} FASTA records: {names}{more}. This "
            "command ranks a library against ONE protein, and picking one of them "
            "for you would answer a question you did not ask. Supply a single "
            "record, or split the file and run once per protein."
        )
    if records:
        header, lines = records[0]
        if not lines:
            raise SequenceError(f"{source} has a FASTA header but no sequence under it")
        return Query(header=header, sequence="".join(lines), source=source)
    if bare:
        return Query(header="", sequence="".join(bare), source=source)
    raise SequenceError(f"{source} contains no sequence data")


def read_query(*, sequence: str | None = None, path: Path | None = None) -> Query:
    """Accept pasted text or a FASTA file. Exactly one of the two."""
    if (sequence is None) == (path is None):
        raise SequenceError(
            "supply either a pasted sequence or a FASTA file, not both and not neither"
        )
    if path is not None:
        if not path.exists():
            raise SequenceError(f"{path} does not exist")
        return parse_fasta(path.read_text(encoding="utf-8"), str(path))
    return parse_fasta(sequence or "", "the pasted sequence")


def validate(
    query: Query, *, training_window: int, max_length: int, min_length: int = 20
) -> tuple[str, list[str]]:
    """Clean and check one sequence against the bundle's recorded limits.

    Over-length input is refused rather than truncated: scoring a prefix answers a
    question about a different protein.
    """
    cleaned = re.sub(r"\s+", "", query.sequence).upper()
    if not cleaned:
        raise SequenceError("sequence is empty after stripping whitespace")

    if cleaned.startswith(("ATG", "AUG")) and set(cleaned) <= set("ACGTU"):
        raise SequenceError(
            "this looks like a nucleotide sequence: it uses only A, C, G, T/U. "
            "Seq2Lead takes a protein amino-acid sequence. Translate it first."
        )
    illegal = sorted(set(cleaned) - RESIDUES)
    if illegal:
        raise SequenceError(
            f"sequence contains characters that are not amino-acid codes: {illegal[:8]}. "
            "Remove them, or supply the sequence in plain one-letter code."
        )
    if len(cleaned) < min_length:
        raise SequenceError(
            f"sequence is {len(cleaned)} residues. Below {min_length} there is not "
            "enough of a protein to represent, and the result would not mean "
            "anything. This is a refusal, not a limit of the model."
        )
    if len(cleaned) > max_length:
        raise SequenceError(
            f"sequence is {len(cleaned):,} residues, past the {max_length:,}-residue "
            "refusal point recorded for this model. It is refused rather than "
            "truncated: scoring a prefix would answer a question about a different "
            "protein."
        )

    notes: list[str] = []
    if len(cleaned) > training_window:
        notes.append(
            f"sequence is {len(cleaned):,} residues, past the {training_window:,}-residue "
            "pre-training window of the protein language model. It is embedded in "
            "full under the recorded policy; representation quality beyond that "
            "window is untested."
        )
    present = sorted(set(cleaned) & set(AMBIGUOUS))
    if present:
        described = ", ".join(f"{code} = {AMBIGUOUS[code]}" for code in present)
        notes.append(
            f"sequence contains ambiguous residue codes ({described}). They are "
            "embedded as supplied; the ranking inherits that ambiguity."
        )
    return cleaned, notes
