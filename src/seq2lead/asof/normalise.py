"""Declared normalisation for the fields the matcher keys on.

Units are **declared, never inferred**. The source TSV names them in its column
headers -- `Ki (nM)`, `IC50 (nM)`, `Kd (nM)`, `EC50 (nM)`, `pH`, `Temp (C)` --
so the unit of a field is a property of the column it came from, not something to
guess from the text. If a value arrives carrying a unit suffix, that is a sign the
column convention has changed and it is **flagged rather than stripped**.

Every normalisation keeps the original text beside the canonical form. A
canonical form is for matching; the original is what gets reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: Bumped when any rule here changes, because a changed rule changes which rows
#: match and therefore what counts as new evidence.
NORMALISATION_VERSION = "m11-normalise-v2"

#: The unit each keyed numeric field is declared to be in. Not inferred.
DECLARED_UNITS = {
    "value": "nM (nanomolar), from the source columns `Ki (nM)`, `IC50 (nM)`, "
    "`Kd (nM)`, `EC50 (nM)`",
    "ph": "dimensionless, from the source column `pH`",
    "temp_c": "degrees Celsius, from the source column `Temp (C)`",
}

#: Characters that may legitimately precede a censored numeric value.
_RELATION_PREFIXES = ("<", ">", "~", "=")


@dataclass(frozen=True)
class Numeric:
    """A canonical numeric form, its original text, and why it may have failed."""

    canonical: str | None
    original: str
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.canonical is not None

    def key(self) -> str:
        """What the matcher keys on.

        An unparseable value falls back to its **original text**, so two rows that
        we cannot interpret still match each other when they are spelled
        identically, and never match a row we *can* interpret.
        """
        return self.canonical if self.canonical is not None else f"raw:{self.original}"


def normalise_number(text: object, field: str) -> Numeric:
    """Canonical fixed-point decimal, so equivalent spellings match.

    `12`, `12.0`, `12.00` and `1.2e1` are one quantity and must land on one key.
    `12` and `12.5` are different quantities and must not. Exponent notation is
    expanded rather than preserved, because `1.2e1` and `12` are the same number
    and a string comparison would say otherwise.
    """
    original = "" if text is None else str(text).strip()
    if not original:
        return Numeric(canonical=None, original="", note="empty")

    body = " ".join(original.split())
    # a leading relation belongs to `relation`, not to the number
    while body[:1] in _RELATION_PREFIXES:
        body = body[1:].lstrip()
    if not body:
        return Numeric(canonical=None, original=original, note="relation with no number")

    # Parse first. `Decimal` accepts exponent notation, so an alpha-character
    # screen applied beforehand would reject `1.2e1` -- which is the same
    # quantity as `12` and must match it.
    try:
        value = Decimal(body)
    except InvalidOperation:
        # It did not parse. A unit suffix is the likely cause, and the right
        # response is to flag it: silently stripping `nM` would equate quantities
        # that may differ by orders of magnitude.
        note = (
            f"not numeric in a field declared as {DECLARED_UNITS[field]}; not stripped"
            if any(ch.isalpha() for ch in body)
            else "not a decimal number"
        )
        return Numeric(canonical=None, original=original, note=note)
    if not value.is_finite():
        return Numeric(canonical=None, original=original, note="not finite")

    # `normalize()` strips trailing zeros; `:f` forces fixed-point so that
    # 1.2E+1 and 12 render identically. Normalising -0 to 0 keeps the key stable.
    canonical = f"{value.normalize():f}"
    if canonical in ("-0", "-0.0"):
        canonical = "0"
    return Numeric(canonical=canonical, original=original)


def normalise_text(value: object) -> str:
    """Case- and whitespace-insensitive canonical text for non-numeric fields."""
    if value is None:
        return ""
    return " ".join(str(value).strip().split()).upper()
