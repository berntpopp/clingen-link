"""Reject a filter value the snapshot cannot match — never answer it with zero rows.

The fleet's most common defect: a filter accepts a value it does not understand, matches
nothing, and returns ``success: true`` with an empty list. The caller cannot tell a typo, a
wrong vocabulary, or a stale identifier from "the data genuinely has none" — so an agent
confidently reports "there are no such genes" (issue #46; Response-Envelope v1.1: *silent
omission is not compliant*).

Two kinds of filter, two rejections:

* :class:`Vocabulary` — a small set of values the snapshot itself carries (a curation
  status). Rejected as ``invalid_input``, and the message LISTS the valid values, so the
  model can retry correctly on its next turn. These vocabularies are upstream's, not ours:
  they are read from the data rather than hardcoded, because guessing an enum (and
  advertising a value the runtime never matches) is the very bug this module exists to kill.
  A vocabulary that is genuinely fixed and published — the dosage score codes — is instead
  declared as a schema ``enum`` (see :mod:`clingen_link.vocab`), which rejects the value
  before the tool body even runs.
* :class:`Identifier` — a gene, an ISCA region, a cytoband, a MONDO id, an expert panel, a
  disease name. Too many to enumerate, so membership is checked against the snapshot's own
  index and a miss is ``not_found``, naming the parameter and the tool that resolves it.

Neither message ever echoes the caller's value: a filter value is attacker-controlled text,
and sanitisation strips code points, not prose. The parameter NAME and the server's own
vocabulary are safe; the value is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clingen_link.exceptions import DataNotFoundError
from clingen_link.mcp.errors import ToolInputError
from clingen_link.store import queries
from clingen_link.store.db import Store

MatchMode = Literal["exact", "like", "prefix", "fts"]


@dataclass(frozen=True)
class Identifier:
    """A filter whose value must appear in the snapshot's index for ``column``."""

    param: str
    table: str
    column: str
    match: MatchMode = "exact"
    resolver: str = "get_server_capabilities"


@dataclass(frozen=True)
class Vocabulary:
    """A filter whose values are the distinct values the snapshot carries."""

    param: str
    table: str
    columns: tuple[str, ...]


def ensure_identifier(store: Store, spec: Identifier, value: str | None) -> None:
    """Raise ``not_found`` when ``value`` appears nowhere in the snapshot's index.

    An identifier that matches nothing anywhere is a value the caller must FIX, not an empty
    result they should believe. A combination of two known values that happens to match no
    row is a different thing entirely, and still returns ``success`` with zero rows.
    """
    if not value:
        return
    with store.connection() as conn:
        if queries.value_exists(conn, spec.table, spec.column, value, match=spec.match):
            return
    raise DataNotFoundError(
        f"The {spec.param} you supplied is not in the ClinGen snapshot, so no record can "
        f"match it. Do not retry unchanged: resolve it with {spec.resolver} first, or drop "
        f"the {spec.param} filter."
    )


def ensure_vocabulary(store: Store, spec: Vocabulary, value: str | None) -> None:
    """Raise ``invalid_input`` (listing the valid values) for an out-of-vocabulary value."""
    if not value:
        return
    with store.connection() as conn:
        allowed = queries.distinct_values(conn, spec.table, spec.columns)
    if value in allowed:
        return
    valid = ", ".join(repr(v) for v in sorted(allowed)) or "(none in this snapshot)"
    raise ToolInputError(f"{spec.param} must be one of: {valid}.")


def ensure_gene(store: Store, symbol: str | None, *, param: str = "gene_symbol") -> str | None:
    """Resolve a gene symbol/alias to its canonical symbol, or raise ``not_found``.

    The search tools used to fall back to the caller's raw string when it did not resolve
    (``resolved_gene or gene_symbol``), which turned an unknown gene into a silent zero-row
    answer instead of the not_found the detail tools already returned for it.
    """
    if not symbol:
        return None
    canonical = store.resolve_gene(symbol)
    if canonical is None:
        raise DataNotFoundError(
            f"The {param} you supplied is not in the ClinGen gene index. Resolve it with "
            "search_genes (it matches symbols, aliases and previous symbols) and retry with "
            "the canonical symbol."
        )
    return canonical
