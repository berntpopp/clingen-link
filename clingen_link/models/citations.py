"""Citation + permalink builders (the house citation contract, spec section 4).

One function per domain turns a raw store row into ``(permalink,
recommended_citation)``. Keeping the exact format strings here means the models
and any tool-level overrides share a single source of truth. Missing fields
degrade gracefully to a neutral placeholder rather than emitting ``None`` into a
human-facing citation.
"""

from __future__ import annotations

from typing import Any

_NA = "n/a"

# Permalink bases.
_VALIDITY_PERMALINK = "https://search.clinicalgenome.org/kb/gene-validity/{perm_id}"
_DOSAGE_PERMALINK = "https://search.clinicalgenome.org/kb/gene-dosage/{ident}"
_ACTIONABILITY_PERMALINK = "https://actionability.clinicalgenome.org/ac/"
_CSPEC_PERMALINK = "https://cspec.genome.network/cspec/ui/svi/doc/{gn_id}"


def _val(row: dict[str, Any], key: str, default: str = _NA) -> str:
    """Return a stringified, non-empty row value or ``default``."""
    value = row.get(key)
    if value is None or value == "":
        return default
    return str(value)


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------
def validity_citation(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(permalink, recommended_citation)`` for a validity assertion.

    ``disease_name`` is externally sourced free text emitted as a fenced
    ``untrusted_text`` object on the record (Response-Envelope v1.1); it MUST NOT be
    duplicated raw into this sibling citation string. The disease is referenced by its
    curated MONDO id instead — a stable identifier, not upstream prose. The human-
    readable label still travels (as typed data) in the record's fenced ``disease_name``.
    """
    perm_id = _val(row, "perm_id", "")
    permalink = _VALIDITY_PERMALINK.format(perm_id=perm_id) if perm_id else _NA
    citation = (
        f"ClinGen Gene-Disease Validity: {_val(row, 'symbol')} — "
        f"{_val(row, 'mondo')} — "
        f"{_val(row, 'classification')} ({_val(row, 'moi')}), "
        f"curated by {_val(row, 'expert_panel')}, "
        f"classified {_val(row, 'classified_date')}. {permalink}"
    )
    return permalink, citation


# ---------------------------------------------------------------------------
# Dosage
# ---------------------------------------------------------------------------
def dosage_citation(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(permalink, recommended_citation)`` for a dosage record.

    The permalink keys on HGNC id when present, then ISCA id (region records),
    then the gene symbol — the dosage feed carries no HGNC id for gene records,
    and the ClinGen dosage page resolves by symbol, so symbol is the working
    fallback rather than emitting ``n/a``.
    """
    ident = _val(row, "hgnc_id", "") or _val(row, "isca_id", "") or _val(row, "symbol", "")
    permalink = _DOSAGE_PERMALINK.format(ident=ident) if ident else _NA
    gene_or_region = _val(row, "symbol", "") or _val(row, "isca_id")
    citation = (
        f"ClinGen Dosage Sensitivity: {gene_or_region} — "
        f"haploinsufficiency: {_val(row, 'haplo_score')}; "
        f"triplosensitivity: {_val(row, 'triplo_score')}, "
        f"evaluated {_val(row, 'date_last_evaluated')}. {permalink}"
    )
    return permalink, citation


# ---------------------------------------------------------------------------
# Actionability
# ---------------------------------------------------------------------------
def actionability_citation(row: dict[str, Any], *, context: str, status: str, release: str) -> str:
    """Return the actionability ``recommended_citation`` for a context.

    Genes is a list on the row; the citation names the curation by disease + doc
    id, so the gene field is omitted to stay stable across multi-gene curations.
    The permalink is the fixed actionability portal base.
    """
    gene = ", ".join(row.get("genes") or []) or _NA
    return (
        f"ClinGen Clinical Actionability: {gene} — "
        f"{_val(row, 'disease')} ({_val(row, 'doc_id')}), "
        f"{context}: {status or _NA}, release {release or _NA}. "
        f"{_ACTIONABILITY_PERMALINK}"
    )


# ---------------------------------------------------------------------------
# ERepo (variant pathogenicity)
# ---------------------------------------------------------------------------
def erepo_citation(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(permalink, recommended_citation)`` for an ERepo interpretation."""
    permalink = _val(row, "repo_link", "")
    hgvs_list = row.get("hgvs") or []
    hgvs0 = str(hgvs_list[0]) if hgvs_list else _NA
    repo_link = permalink if permalink and permalink != _NA else _NA
    citation = (
        f"ClinGen Variant Pathogenicity (ERepo): {hgvs0} "
        f"({_val(row, 'caid')}) — {_val(row, 'disease')} ({_val(row, 'mondo')}): "
        f"{_val(row, 'assertion')} by {_val(row, 'expert_panel')}, "
        f"published {_val(row, 'published_date')}. {repo_link}"
    )
    return (permalink or _NA), citation


# ---------------------------------------------------------------------------
# CSpec (criteria specifications)
# ---------------------------------------------------------------------------
def cspec_citation(row: dict[str, Any]) -> tuple[str, str]:
    """Return ``(permalink, recommended_citation)`` for a criteria specification."""
    gn_id = _val(row, "gn_id", "")
    permalink = row.get("permalink") or (_CSPEC_PERMALINK.format(gn_id=gn_id) if gn_id else _NA)
    citation = (
        f"ClinGen Criteria Specification ({gn_id}): {_val(row, 'label')} "
        f"by {_val(row, 'affiliation_label')}, version {_val(row, 'version')}. {permalink}"
    )
    return permalink, citation
