"""HGNC complete-set ingestion: gene full name + alias / previous symbols.

The bundled snapshot's ``gene.name`` was never populated and alias resolution only covered HGNC ids
and case-folded symbols, so official aliases (e.g. ``FANCD1`` → ``BRCA2``) returned ``not_found``
(assessment L2/L3). This module parses the HGNC ``hgnc_complete_set`` TSV — the authoritative
symbol ↔ alias ↔ id ↔ name table — into a per-symbol map the gene-index builder annotates onto the
genes ClinGen actually curates (keeping the index lean, HGNC as the naming authority).
"""

from __future__ import annotations

import csv
import io
from typing import Any


def parse_hgnc(tsv_text: str) -> list[dict[str, Any]]:
    """Parse the HGNC complete-set TSV into ``{hgnc_id, symbol, name, aliases:[...]}`` rows.

    ``alias_symbol`` and ``prev_symbol`` are pipe-delimited multi-value cells; both are split and
    merged into a single deduped ``aliases`` list (excluding the canonical symbol itself).
    """
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    out: list[dict[str, Any]] = []
    for row in reader:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        aliases: list[str] = []
        for col in ("alias_symbol", "prev_symbol"):
            cell = (row.get(col) or "").strip()
            for token in cell.split("|"):
                token = token.strip()
                if token and token != symbol and token not in aliases:
                    aliases.append(token)
        out.append(
            {
                "hgnc_id": (row.get("hgnc_id") or "").strip() or None,
                "symbol": symbol,
                "name": (row.get("name") or "").strip() or None,
                "aliases": aliases,
            }
        )
    return out


def index_by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return an HGNC map keyed by canonical symbol for the gene-index builder."""
    return {row["symbol"]: row for row in rows}
