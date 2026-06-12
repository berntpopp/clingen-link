"""Select the few load-bearing HGVS expressions from a large ERepo ``hgvs`` array.

An ERepo interpretation can carry ~50 HGVS strings (every transcript across both assemblies); at
the default page size that single array dominates token cost (~2.5 kB/variant; ~60 kB at size=25 —
assessment M2). For minimal/compact we keep only the canonical genomic (GRCh38, ``NC_…:g.``), the
coding/MANE transcript (``NM_…:c.``), and the protein (``NP_…:p.`` or ``…:p.``) — enough to identify
the variant — and gate the full list behind standard/full.
"""

from __future__ import annotations

_MAX_CANONICAL = 3


def _is_grch38_genomic(h: str) -> bool:
    """True for a RefSeq chromosome genomic HGVS (``NC_…:g.``)."""
    return h.startswith("NC_") and ":g." in h


def _accession(h: str) -> str:
    """The accession (before the first colon), used to prefer the highest version (GRCh38)."""
    return h.split(":", 1)[0]


def canonical_hgvs(hgvs: list[str]) -> list[str]:
    """Return up to three identifying HGVS expressions (genomic GRCh38, coding, protein).

    Order-stable and deduped. A short list (≤3) passes through unchanged. If a category is empty,
    the result is backfilled from the head of the input so the answer stays informative.
    """
    if len(hgvs) <= _MAX_CANONICAL:
        return list(hgvs)
    # Prefer the highest accession version (GRCh38 NC_…​.11 sorts above GRCh37 NC_…​.10).
    genomic = sorted((h for h in hgvs if _is_grch38_genomic(h)), key=_accession, reverse=True)
    coding = [h for h in hgvs if h.startswith("NM_") and ":c." in h]
    protein = [h for h in hgvs if h.startswith("NP_") or ":p." in h]
    out: list[str] = []
    for group in (genomic, coding, protein):
        if group and group[0] not in out:
            out.append(group[0])
    for h in hgvs:
        if len(out) >= _MAX_CANONICAL:
            break
        if h not in out:
            out.append(h)
    return out[:_MAX_CANONICAL]
