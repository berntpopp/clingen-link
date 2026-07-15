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

# A ClinVar VariationID is a bare integer.
CLINVAR_VARIATION_ID_PATTERN = r"^\d+$"

# The three identifier shapes `get_variant_interpretation` accepts, as ONE required parameter:
# a CAID, a ClinVar VariationID, or an HGVS expression. Declaring the union in the schema means
# the tool advertises exactly the calls it can answer — three optional selectors advertised a
# no-argument call it always refused (issue #46).
VARIANT_ID_PATTERN = (
    rf"{CAID_PATTERN}|{CLINVAR_VARIATION_ID_PATTERN}|"
    r"^[A-Za-z0-9_.]+:[gcmnrp]\.[A-Za-z0-9_>+\-*()=?]+$"
)

# HGVS expression (genomic/coding/protein); intentionally permissive.
HGVS_PATTERN = r"^[A-Za-z0-9_.]+:[gcmnrp]\.[A-Za-z0-9_>+\-*()=?]+$"

# Clinical actionability document id, e.g. AC161.
DOC_ID_PATTERN = r"^AC\d+$"

# ClinGen Genome / Gene Variant (CGGV) validity permalink / perm_id token.
CGGV_PATTERN = r"^[A-Za-z0-9:_-]{1,64}$"

# ClinGen criteria-specification (CSpec) GN identifier, e.g. GN092.
GN_ID_PATTERN = r"^GN\d{1,4}$"

# An ACMG/AMP criterion code: PVS1, PS3, PM2, PP5, BA1, BS3, BP4… (optionally suffixed by a
# VCEP, e.g. PM2_Supporting).
ACMG_CODE_PATTERN = r"^(P(VS|S|M|P)|B(A|S|P))\d+(_[A-Za-z]+)?$"
