"""ClinGen closed vocabularies — the single source of truth for the ETL *and* the tool schemas.

A vocabulary declared in only one of those two places is how the fleet's
"silently-empty filter" bug is born: the schema advertises a value the stored data
can never match, so the filter returns ``success: true`` with zero rows and the
caller cannot tell "no such code" from "no gene carries it" (issue #46).

Declaring it once means the MCP schema's ``enum``, the runtime rejection, and the
ETL's drift guard cannot disagree.

Dosage
------
``ClinGen_gene_curation_list_GRCh38.tsv`` / ``ClinGen_region_curation_list_GRCh38.tsv``
publish a *Score* column (a code) and a separate *Description* column (its prose).
The score vocabulary is closed:

===== ==============================================================
code   meaning
===== ==============================================================
0      no evidence
1      little evidence
2      some evidence (emerging)
3      sufficient evidence for dosage pathogenicity
30     gene associated with an autosomal-recessive phenotype
40     dosage sensitivity unlikely
===== ==============================================================

``30`` and ``40`` are **not ordinal** — they are flags, not "more evidence than 3".
The triplosensitivity column additionally ships the literal sentinel
``Not yet evaluated`` (and blanks) *in the score column*; that is an absent score,
not a code, and the ETL stores it as ``NULL``.
"""

from __future__ import annotations

from typing import Literal, get_args

# The closed dosage score vocabulary. `Literal` (not a bare `str`) so an `enum`
# appears in the tool's inputSchema and FastMCP rejects anything outside it.
DosageScoreCode = Literal["0", "1", "2", "3", "30", "40"]

DOSAGE_SCORE_CODES: frozenset[str] = frozenset(get_args(DosageScoreCode))

# Upstream ships this sentence *in the score column* for un-evaluated triplosensitivity
# (211 gene rows as of the 2026-07-14 release). It is an absent score, not a code.
DOSAGE_NOT_EVALUATED = "Not yet evaluated"

# Plain-English reading of each code, so a model does not have to memorise the scale.
DOSAGE_SCORE_TEXT: dict[str, str] = {
    "0": "No evidence",
    "1": "Little evidence",
    "2": "Some evidence (emerging)",
    "3": "Sufficient evidence for dosage pathogenicity",
    "30": "Gene associated with autosomal recessive phenotype",
    "40": "Dosage sensitivity unlikely",
}
