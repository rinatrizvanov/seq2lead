"""Parsing of BindingDB measurement fields.

Everything here is mechanical and pre-registered: no threshold, no filter, and no
choice that depends on looking at outcome distributions. M4 builds provisional Ki endpoint
tables against a predeclared threshold; M5 tests assay comparability and decides
whether pooling Ki across assays is defensible; M6 builds the splits.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

# BindingDB reports all four affinity types in nanomolar, in these columns.
MEASUREMENT_COLUMNS: dict[str, str] = {
    "KI": "Ki (nM)",
    "IC50": "IC50 (nM)",
    "KD": "Kd (nM)",
    "EC50": "EC50 (nM)",
}
VALUE_UNIT = "nM"

SEQUENCE_KEY = "BindingDB Target Chain Sequence 1"
CHAINS_KEY = "Number of Protein Chains in Target (>1 implies a multichain complex)"

HTML_DECODE_VERSION = "html.unescape/v1"

# A leading relation operator, optional whitespace, then the magnitude. Unicode
# comparison signs are accepted because sources use them interchangeably.
_RELATION_RE = re.compile(r"^\s*(<=|>=|≤|≥|≈|<|>|~|=)?\s*(.*)$")
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

#: Canonical relations. Inclusive and exclusive bounds are kept apart on purpose —
#: see `label_at_threshold` for why they are not interchangeable.
RELATIONS = ("=", "<", "<=", ">", ">=", "~", "?")

_CANONICAL = {"≤": "<=", "≥": ">=", "≈": "~"}

#: Whether the parsed bound includes its endpoint. None where the concept does not
#: apply (an exact value, an approximation, or an operator we did not recognize).
_INCLUSIVE: dict[str, bool | None] = {
    "=": None,
    "~": None,
    "?": None,
    "<": False,
    ">": False,
    "<=": True,
    ">=": True,
}

ACTIVE = "active"
INACTIVE = "inactive"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ParsedValue:
    relation: str
    relation_raw: str
    bound_inclusive: bool | None
    value_text: str
    value_numeric: float | None


def parse_value(raw: str) -> ParsedValue | None:
    """Split a measurement field into relation and magnitude.

    The original string is preserved verbatim in `value_text` and the operator
    exactly as it appeared in `relation_raw`; nothing is normalized away.

    **`>=` is not `>`.** An inclusive bound admits equality and an exclusive one
    does not, and at an activity threshold that is the difference between a
    decisive call and an ambiguous one — see `label_at_threshold`. Collapsing them
    would silently convert ambiguity into a confident label.

    A field whose magnitude will not parse still yields a row, with
    `value_numeric = None`; the relation and the original text are themselves
    information. An operator we do not recognize becomes `?`, never `=`.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    match = _RELATION_RE.match(text)
    if match is None:  # pragma: no cover - the pattern always matches
        return None
    operator_raw = match.group(1) or ""
    magnitude = match.group(2).strip().replace(",", "")
    numeric = float(magnitude) if _NUMERIC_RE.match(magnitude) else None

    if operator_raw:
        relation = _CANONICAL.get(operator_raw, operator_raw)
    elif numeric is not None:
        relation = "="
    else:
        # No operator and no parseable number: we do not know what this says, and
        # calling it an exact measurement would be a fabrication.
        relation = "?"

    return ParsedValue(
        relation=relation,
        relation_raw=operator_raw,
        bound_inclusive=_INCLUSIVE.get(relation),
        value_text=text,
        value_numeric=numeric,
    )


def label_at_threshold(relation: str, value_nm: float | None, threshold_nm: float = 1000.0) -> str:
    """How a single record reads against an activity threshold.

    This is the **contract M4 will aggregate over**; it builds no labels itself.
    Stated in nM because that is the unit the source reports. "Active" means
    ``pKi >= threshold``, i.e. ``Ki <= threshold_nm`` — the default 1000 nM is
    pKi 6.0.

    The asymmetry is the whole point. At exactly 1000 nM:

    ====================  ======================  ==============
    record                constraint on pKi       verdict
    ====================  ======================  ==============
    ``= 1000``            ``pKi == 6.0``          active
    ``< 1000``            ``pKi > 6.0``           active
    ``<= 1000``           ``pKi >= 6.0``          active
    ``> 1000``            ``pKi < 6.0``           inactive
    ``>= 1000``           ``pKi <= 6.0``          **ambiguous**
    ``~ 1000``            unspecified             ambiguous
    ====================  ======================  ==============

    ``> 1000`` excludes equality, so the whole admissible range is inactive.
    ``>= 1000`` admits exactly 1000, which is active, so the record cannot decide.
    """
    if value_nm is None:
        return AMBIGUOUS
    if relation == "=":
        return ACTIVE if value_nm <= threshold_nm else INACTIVE
    if relation in ("<", "<="):
        # Upper bound: decisive only when the whole admissible range is active.
        return ACTIVE if value_nm <= threshold_nm else AMBIGUOUS
    if relation == ">":
        # Exclusive lower bound: everything above `value_nm` is inactive once
        # `value_nm` reaches the threshold, because equality is excluded.
        return INACTIVE if value_nm >= threshold_nm else AMBIGUOUS
    if relation == ">=":
        # Inclusive lower bound: equality at the threshold is *active*, so the
        # bound must sit strictly above it to decide.
        return INACTIVE if value_nm > threshold_nm else AMBIGUOUS
    return AMBIGUOUS  # '~' and '?' carry no decidable bound


def sequence_sha256(sequence: str) -> str:
    """Stable target identifier derived from the sequence itself."""
    return hashlib.sha256(sequence.strip().upper().encode("ascii", "ignore")).hexdigest()


def structure_sha256(smiles: str) -> str:
    """Stable identifier for a source structure, used as the standardization key.

    BindingDB's own `Ligand InChI Key` cannot serve here: its second block is
    UHFFFAOYSA (no stereo layer), so stereoisomers collide under one key while
    standardizing to different parents.
    """
    return hashlib.sha256(smiles.strip().encode("utf-8")).hexdigest()


def decode_entities(text: str | None) -> str | None:
    """HTML-entity-decode assay text. The raw form is stored alongside it."""
    return None if text is None else html.unescape(text)


def parse_chain_count(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None
