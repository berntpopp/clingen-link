"""Shared MCP input-schema regex patterns for clingen-link identifiers."""

from __future__ import annotations

# Gene symbol: HGNC-style approved symbols (letters, digits, a few separators).
GENE_SYMBOL_PATTERN = r"^[A-Za-z0-9._-]{1,32}$"

# HGNC gene identifier, e.g. HGNC:1100.
HGNC_ID_PATTERN = r"^HGNC:\d+$"

# MONDO disease identifier, e.g. MONDO:0007254.
MONDO_PATTERN = r"^MONDO:\d+$"

# ClinGen Allele Registry canonical allele id, e.g. CA123456 or CAR:CA123456.
CAID_PATTERN = r"^CA(R:)?\d+$"

# HGVS expression (genomic/coding/protein); intentionally permissive.
HGVS_PATTERN = r"^[A-Za-z0-9_.]+:[gcmnrp]\.[A-Za-z0-9_>+\-*()=?]+$"

# Clinical actionability document id, e.g. AC161.
DOC_ID_PATTERN = r"^AC\d+$"

# ClinGen Genome / Gene Variant (CGGV) validity permalink / perm_id token.
CGGV_PATTERN = r"^[A-Za-z0-9:_-]{1,64}$"
