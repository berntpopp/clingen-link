"""Pure parsers turning raw ClinGen feeds into normalized snapshot rows.

Every function here is **pure**: it takes already-fetched data (lists of dicts
or raw TSV text) and returns lists of normalized ``dict``s ready for the build
writers. No network, no clock, no filesystem — which makes them trivially
testable against the captured fixtures.

Domain coverage:

* :func:`parse_validity` — ``/api/validity`` rows.
* :func:`parse_dosage` — gene + region FTP TSVs.
* :func:`parse_actionability` — actionability ``brief`` docs.
* :func:`parse_erepo` — ERepo bulk TSV.
* :func:`build_gene_index` — union gene table + alias rows.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..exceptions import SnapshotBuildError
from ..vocab import DOSAGE_NOT_EVALUATED, DOSAGE_SCORE_CODES
from .sanitize import is_obsolete_label, strip_html

# ---------------------------------------------------------------------------
# Dosage score codes
# ---------------------------------------------------------------------------
# The Score column is a CODE; the Description column beside it is that code's
# prose. They are stored separately, verbatim.
#
# This function used to *expand* the codes 30 and 40 into their description text
# before storing, which put a sentence in a numeric column: `haplo_score="30"`
# then matched nothing (the snapshot held prose), and `get_gene_dosage(CFTR)`
# answered a numeric field with "Gene associated with autosomal recessive
# phenotype" (issue #46, D1 + D2). The decode belongs at the presentation edge —
# see `DOSAGE_SCORE_TEXT` — never in the stored column.


def _dosage_score_code(raw: str) -> str | None:
    """Return the upstream score code, ``None`` for an absent score.

    Upstream ships blanks and the literal sentinel ``Not yet evaluated`` *in the
    score column* (211 gene rows carry it for triplosensitivity); both mean "no
    score assigned" and are stored as ``NULL``. The accompanying Description
    column keeps the sentinel verbatim, so nothing is lost.

    Raises:
        SnapshotBuildError: the code is outside ClinGen's published vocabulary.
            Vocabulary drift fails the build loudly rather than shipping a value
            no ``search_dosage`` filter could ever reach.
    """
    score = raw.strip()
    if not score or score == DOSAGE_NOT_EVALUATED:
        return None
    if score not in DOSAGE_SCORE_CODES:
        raise SnapshotBuildError(
            f"unknown ClinGen dosage score code {score!r} (expected one of "
            f"{sorted(DOSAGE_SCORE_CODES)} or {DOSAGE_NOT_EVALUATED!r}). Upstream's vocabulary "
            "changed: add the code to clingen_link.vocab so the tool schema advertises it too."
        )
    return score


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------


def parse_validity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ``/api/validity`` rows into ``validity`` table dicts.

    The API wraps rows in ``{total, rows: [...]}``; callers pass ``rows`` here.
    ``disease_name`` is HTML-sanitized (the feed embeds ``<span>…Obsolete
    Term</span>`` markup) and whitespace-collapsed, with obsolescence surfaced as
    a structured ``disease_obsolete`` flag rather than left as raw markup
    (assessment M1). ``ep`` is renamed ``expert_panel`` and ``date`` to
    ``classified_date``.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        disease = row.get("disease_name") or ""
        out.append(
            {
                "symbol": row.get("symbol"),
                "hgnc_id": row.get("hgnc_id"),
                "disease_name": strip_html(disease) or None,
                "disease_obsolete": is_obsolete_label(disease),
                "mondo": row.get("mondo"),
                "moi": row.get("moi"),
                "sop": row.get("sop"),
                "classification": row.get("classification"),
                "expert_panel": row.get("ep"),
                "affiliate_id": row.get("affiliate_id"),
                "perm_id": row.get("perm_id"),
                "report_id": row.get("report_id"),
                "released": row.get("released"),
                "classified_date": row.get("date"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Dosage
# ---------------------------------------------------------------------------

# Column positions shared by gene + region TSVs (first two columns differ;
# the remaining 21 align by offset starting at index 2).
_DOSAGE_HAPLO_SCORE = 4
_DOSAGE_HAPLO_DESC = 5
_DOSAGE_HAPLO_PMIDS = range(6, 12)
_DOSAGE_TRIPLO_SCORE = 12
_DOSAGE_TRIPLO_DESC = 13
_DOSAGE_TRIPLO_PMIDS = range(14, 20)
_DOSAGE_DATE = 20
_DOSAGE_HAPLO_DISEASE = 21
_DOSAGE_TRIPLO_DISEASE = 22
_DOSAGE_COLS = 23


def _split_dosage_rows(tsv: str) -> list[list[str]]:
    """Return data rows (lists of cells) from a dosage TSV, skipping ``#`` lines."""
    rows: list[list[str]] = []
    for line in tsv.splitlines():
        if not line or line.startswith("#"):
            continue
        rows.append(line.split("\t"))
    return rows


def _collect_pmids(cells: list[str], indices: range) -> list[str]:
    """Collect non-empty PMID cells at ``indices`` into an ordered list."""
    pmids: list[str] = []
    for i in indices:
        if i < len(cells):
            value = cells[i].strip()
            if value:
                pmids.append(value)
    return pmids


def _cell(cells: list[str], index: int) -> str:
    """Safe positional cell access; returns ``""`` past the row end."""
    return cells[index].strip() if index < len(cells) else ""


def _mondo_or_none(value: str) -> str | None:
    """Return a MONDO id or ``None`` for an empty disease cell."""
    return value or None


def _parse_dosage_record(cells: list[str], record_type: str) -> dict[str, Any]:
    """Build one normalized dosage dict from a positional cell list."""
    cytoband = _cell(cells, 2)
    grch38 = _cell(cells, 3)
    haplo_pmids = _collect_pmids(cells, _DOSAGE_HAPLO_PMIDS)
    triplo_pmids = _collect_pmids(cells, _DOSAGE_TRIPLO_PMIDS)
    record: dict[str, Any] = {
        "record_type": record_type,
        "symbol": _cell(cells, 0) if record_type == "gene" else None,
        "isca_id": _cell(cells, 0) if record_type == "region" else None,
        "gene_id": _cell(cells, 1) if record_type == "gene" else None,
        "region_name": _cell(cells, 1) if record_type == "region" else None,
        "cytoband": cytoband,
        "grch38": grch38,
        "grch37": None,
        "haplo_score": _dosage_score_code(_cell(cells, _DOSAGE_HAPLO_SCORE)),
        "haplo_description": _cell(cells, _DOSAGE_HAPLO_DESC),
        "haplo_pmids": haplo_pmids,
        "triplo_score": _dosage_score_code(_cell(cells, _DOSAGE_TRIPLO_SCORE)),
        "triplo_description": _cell(cells, _DOSAGE_TRIPLO_DESC),
        "triplo_pmids": triplo_pmids,
        "date_last_evaluated": _cell(cells, _DOSAGE_DATE),
        "haplo_disease": None,
        "haplo_mondo": _mondo_or_none(_cell(cells, _DOSAGE_HAPLO_DISEASE)),
        "triplo_disease": None,
        "triplo_mondo": _mondo_or_none(_cell(cells, _DOSAGE_TRIPLO_DISEASE)),
    }
    return record


def parse_dosage(
    gene_tsv: str,
    region_tsv: str,
    *,
    gene_tsv_grch37: str | None = None,
    region_tsv_grch37: str | None = None,
) -> list[dict[str, Any]]:
    """Parse the dosage gene + region TSVs into normalized dosage dicts.

    ``record_type`` is ``"gene"`` (Gene Symbol / Gene ID first cols) or
    ``"region"`` (ISCA ID / ISCA Region Name). Score columns keep upstream's
    **code** (``0``–``3``, ``30``, ``40``; ``NULL`` when unscored) with the prose
    left in the Description columns, PMID1..6 are collapsed into ``haplo_pmids`` /
    ``triplo_pmids`` lists, and GRCh38 coordinates are kept. Optional GRCh37 TSVs,
    if provided, backfill the ``grch37`` coordinate by matching on the first column
    (symbol / ISCA id).

    Raises:
        SnapshotBuildError: a score code outside ClinGen's published vocabulary.
    """
    records: list[dict[str, Any]] = []
    for cells in _split_dosage_rows(gene_tsv):
        if len(cells) < _DOSAGE_COLS - 2:
            continue
        records.append(_parse_dosage_record(cells, "gene"))
    for cells in _split_dosage_rows(region_tsv):
        if len(cells) < _DOSAGE_COLS - 2:
            continue
        records.append(_parse_dosage_record(cells, "region"))

    _backfill_grch37(records, gene_tsv_grch37, "gene")
    _backfill_grch37(records, region_tsv_grch37, "region")
    return records


def _backfill_grch37(
    records: list[dict[str, Any]],
    tsv: str | None,
    record_type: str,
) -> None:
    """Fill ``grch37`` on matching records from an optional GRCh37 TSV."""
    if tsv is None:
        return
    key = "symbol" if record_type == "gene" else "isca_id"
    coords: dict[str, str] = {}
    for cells in _split_dosage_rows(tsv):
        ident = _cell(cells, 0)
        if ident:
            coords[ident] = _cell(cells, 3)
    for record in records:
        if record["record_type"] != record_type:
            continue
        record_ident = record.get(key)
        if record_ident and record_ident in coords:
            record["grch37"] = coords[record_ident] or None


# ---------------------------------------------------------------------------
# Actionability
# ---------------------------------------------------------------------------


def _context_release(context: dict[str, Any]) -> str | None:
    """Extract a ``release`` signal (number or date) from a context block."""
    release = context.get("release")
    if not isinstance(release, dict):
        return None
    number = release.get("number")
    if isinstance(number, str) and number:
        return number
    date = release.get("date")
    return date if isinstance(date, str) and date else None


def _context_status(context: dict[str, Any]) -> str | None:
    """Extract the overall status from a context block."""
    status = context.get("status")
    if isinstance(status, dict):
        overall = status.get("overall")
        return overall if isinstance(overall, str) else None
    return None


def _context_genes(context: dict[str, Any]) -> list[str]:
    """Return the gene symbols mentioned in a context block (deduped, ordered)."""
    genes: list[str] = []
    for entry in context.get("genes", []) or []:
        if isinstance(entry, dict):
            gene = entry.get("gene")
            if isinstance(gene, str) and gene and gene not in genes:
                genes.append(gene)
    return genes


def parse_actionability(brief: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize actionability ``brief`` docs into ``actionability`` dicts.

    The brief is canonical: one record per ``docId``. Adult and Pediatric
    contexts contribute ``status.overall``, a ``release`` signal, and the SEPIO
    ``@id``. The union of gene symbols across contexts is embedded as a JSON
    list under ``genes``.
    """
    out: list[dict[str, Any]] = []
    for doc in brief:
        doc_id = doc.get("docId")
        if not doc_id:
            continue
        metadata = doc.get("metadata") or {}
        context = doc.get("context") or {}
        adult = context.get("Adult") or {}
        ped = context.get("Pediatric") or {}
        genes: list[str] = []
        for symbol in (*_context_genes(adult), *_context_genes(ped)):
            if symbol not in genes:
                genes.append(symbol)
        out.append(
            {
                "doc_id": doc_id,
                "curation_type": doc.get("curationType"),
                "disease": doc.get("disease"),
                "modes_of_inheritance": doc.get("modesOfInheritance") or [],
                "last_updated": metadata.get("lastUpdated"),
                "last_author": metadata.get("lastAuthor"),
                "adult_status": _context_status(adult),
                "adult_release": _context_release(adult),
                "adult_sepio_iri": adult.get("@id"),
                "pediatric_status": _context_status(ped),
                "pediatric_release": _context_release(ped),
                "pediatric_sepio_iri": ped.get("@id"),
                "genes": genes,
            }
        )
    return out


# ---------------------------------------------------------------------------
# ERepo
# ---------------------------------------------------------------------------

_EREPO_HEADER = (
    "Variation",
    "ClinVar Variation Id",
    "Allele Registry Id",
    "HGVS Expressions",
    "HGNC Gene Symbol",
    "Disease",
    "Mondo Id",
    "Mode of Inheritance",
    "Assertion",
    "Applied Evidence Codes (Met)",
    "Applied Evidence Codes (Not Met)",
    "Summary of interpretation",
    "PubMed Articles",
    "Expert Panel",
    "Guideline",
    "Approval Date",
    "Published Date",
    "Retracted",
    "Evidence Repo Link",
    "Uuid",
)


def _split_list(value: str, sep: str = ",") -> list[str]:
    """Split a delimited cell into a list of non-empty, stripped tokens."""
    if not value:
        return []
    return [tok.strip() for tok in value.split(sep) if tok.strip()]


def parse_erepo(tsv_text: str) -> list[dict[str, Any]]:
    """Parse the 20-column ERepo bulk TSV into normalized ``erepo`` dicts.

    HGVS expressions, PubMed article ids, and Met / Not-Met evidence codes are
    split into lists. ``Retracted`` is coerced to ``bool``.
    """
    reader = csv.reader(io.StringIO(tsv_text), delimiter="\t")
    rows = list(reader)
    if not rows:
        return []
    # Drop a leading header row if present.
    if tuple(rows[0]) == _EREPO_HEADER:
        rows = rows[1:]
    out: list[dict[str, Any]] = []
    for cells in rows:
        if len(cells) < len(_EREPO_HEADER):
            continue
        out.append(
            {
                "variation": cells[0] or None,
                "clinvar_variation_id": cells[1] or None,
                "caid": cells[2] or None,
                "hgvs": _split_list(cells[3]),
                "gene": cells[4] or None,
                "disease": cells[5] or None,
                "mondo": cells[6] or None,
                "moi": cells[7] or None,
                "assertion": cells[8] or None,
                "evidence_codes_met": _split_list(cells[9]),
                "evidence_codes_not_met": _split_list(cells[10]),
                "summary": cells[11] or None,
                "pubmed": _split_list(cells[12]),
                "expert_panel": cells[13] or None,
                "guideline_cspec": cells[14] or None,
                "approval_date": cells[15] or None,
                "published_date": cells[16] or None,
                "retracted": cells[17].strip().lower() == "true",
                "repo_link": cells[18] or None,
                "uuid": cells[19] or None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Gene index
# ---------------------------------------------------------------------------


def _add_alias(aliases: set[tuple[str, str]], alias: str, symbol: str) -> None:
    """Register a case-folded alias → symbol mapping if it differs from symbol."""
    alias = alias.strip()
    if not alias:
        return
    folded = alias.casefold()
    if folded != symbol.casefold():
        aliases.add((folded, symbol))
    # Also map the upper-case form so an exact-case lookup of an alias works.
    if alias != symbol:
        aliases.add((alias, symbol))


def build_gene_index(
    validity: list[dict[str, Any]],
    dosage: list[dict[str, Any]],
    actionability: list[dict[str, Any]],
    erepo_summary: dict[str, Any],
    *,
    hgnc: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the union ``gene`` table and ``gene_alias`` rows across domains.

    Returns ``(genes, aliases)``. ``genes`` carries per-domain availability
    flags plus the ERepo variant count (summed from the summary feed). Aliases
    are derived from HGNC ids and case-folded symbols so the store can resolve
    ``brca1`` / ``HGNC:1100`` to the canonical symbol.

    When an ``hgnc`` map (symbol → ``{hgnc_id, name, aliases}``) is supplied, the gene's full
    ``name`` is populated and each HGNC alias / previous symbol becomes a ``gene_alias`` row, so an
    official alias such as ``FANCD1`` resolves to ``BRCA2`` (assessment L2/L3).
    """
    genes: dict[str, dict[str, Any]] = {}
    aliases: set[tuple[str, str]] = set()

    def _ensure(symbol: str, hgnc_id: str | None = None) -> dict[str, Any]:
        record = genes.get(symbol)
        if record is None:
            record = {
                "symbol": symbol,
                "hgnc_id": hgnc_id,
                "name": None,
                "has_validity": 0,
                "has_dosage": 0,
                "has_actionability": 0,
                "erepo_variant_count": 0,
            }
            genes[symbol] = record
        elif hgnc_id and not record.get("hgnc_id"):
            record["hgnc_id"] = hgnc_id
        return record

    for row in validity:
        symbol = row.get("symbol")
        if not symbol:
            continue
        record = _ensure(symbol, row.get("hgnc_id"))
        record["has_validity"] = 1

    for row in dosage:
        symbol = row.get("symbol")
        if not symbol:
            continue
        record = _ensure(symbol, row.get("hgnc_id"))
        record["has_dosage"] = 1

    for row in actionability:
        for symbol in row.get("genes", []):
            record = _ensure(symbol)
            record["has_actionability"] = 1

    summary_data = erepo_summary.get("data") if isinstance(erepo_summary, dict) else None
    if isinstance(summary_data, dict):
        for symbol, payload in summary_data.items():
            record = _ensure(symbol)
            classifications = payload.get("classifications") if isinstance(payload, dict) else None
            if isinstance(classifications, dict):
                record["erepo_variant_count"] = sum(
                    int(v) for v in classifications.values() if isinstance(v, int)
                )

    if hgnc:
        for symbol, record in genes.items():
            info = hgnc.get(symbol)
            if info is None:
                continue
            if info.get("name") and not record.get("name"):
                record["name"] = info["name"]
            if info.get("hgnc_id") and not record.get("hgnc_id"):
                record["hgnc_id"] = info["hgnc_id"]
            for alias in info.get("aliases", []):
                _add_alias(aliases, alias, symbol)

    for symbol, record in genes.items():
        hgnc_id = record.get("hgnc_id")
        if isinstance(hgnc_id, str) and hgnc_id:
            aliases.add((hgnc_id, symbol))
            aliases.add((hgnc_id.casefold(), symbol))
        _add_alias(aliases, symbol, symbol)

    gene_rows = [genes[symbol] for symbol in sorted(genes)]
    alias_rows = [{"alias": alias, "symbol": symbol} for alias, symbol in sorted(aliases)]
    return gene_rows, alias_rows


def to_json(value: Any) -> str:
    """Serialize a Python list/dict to compact deterministic JSON for storage."""
    return json.dumps(value, separators=(",", ":"), sort_keys=False)
