"""Reject a filter value the snapshot cannot match — never answer it with zero rows.

The fleet's most common defect: a filter accepts a value it does not understand, matches
nothing, and returns ``success: true`` with an empty list. The caller cannot tell a typo, a
wrong vocabulary, or a stale identifier from "the data genuinely has none" — so an agent
confidently reports "there are no such genes" (issue #46; Response-Envelope v1.1: *silent
omission is not compliant*).

This module handles the OPEN-ended filters — identifiers with too many values to enumerate:

* :class:`Identifier` — a gene, an ISCA region, a cytoband, a MONDO id, an expert panel, a
  disease name. Membership is checked against the snapshot's own index and a miss is
  ``not_found``, naming the parameter and the tool that resolves it.

CLOSED vocabularies (dosage score codes, validity classification/MOI, curation statuses) are
instead declared as schema ``enum``s in their tool signatures, so an out-of-vocabulary value is
rejected by validation before the tool body runs; ``test_closed_enums_are_supersets_of_data``
proves every such enum stays a superset of the snapshot data on each rebuild.

The message never echoes the caller's value: a filter value is attacker-controlled text, and
sanitisation strips code points, not prose. The parameter NAME and the resolver are safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clingen_link.exceptions import DataNotFoundError
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
