"""The operative endpoint status, rendered into every downstream report.

M5 concluded that Ki is pooled across assays *provisionally*. That conclusion has
to travel with every number computed on pooled Ki, or it stops being a
qualification and becomes a footnote nobody reads.
"""

from __future__ import annotations

from seq2lead.analysis.assay_variance import OPERATIVE_STATUS, OPERATIVE_STATUS_NOTE


def banner() -> list[str]:
    """Markdown lines to emit near the top of any report built on pooled Ki."""
    return [
        f"> **Endpoint status: `{OPERATIVE_STATUS}`**",
        ">",
        "> " + OPERATIVE_STATUS_NOTE.replace("\n", "\n> "),
        ">",
        "> See [`assay_variance.md`](assay_variance.md) for the evidence and its limits.",
        "",
    ]
