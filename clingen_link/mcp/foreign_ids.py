"""Recognise a well-formed identifier from a namespace this server does not hold.

Response-Envelope Standard v1 §2b (genefoundry-router#343). The snapshot's gene index carries
only the approved symbol, the HGNC id and HGNC aliases — no Ensembl, NCBI Gene or RefSeq
cross-reference — and diseases are keyed by MONDO id only. So an ``ENSG…`` / bare-number gene or
an ``OMIM:…`` disease cannot be resolved here, and must never be answered with "no ClinGen
curation": that reads as an absence when the input was simply not understood. It is
``invalid_input`` naming the field, the accepted forms, and a ready-to-send converter call on the
sibling front door (namespaced names; the router passes them through).

Detection runs only AFTER the snapshot failed to resolve the value (or the schema rejected it),
so a real symbol or alias is never shadowed. Every pattern is a strict full match, which is what
makes echoing the value into ``next_commands`` safe.

ponytail: UniProt accessions are not detected — the accession shape also matches real gene
symbols (P2RY10, B3GAT1), so a miss on one stays the honest absence. Add it only if the gene
index gains a UniProt column to check against.
"""

from __future__ import annotations

import re
from typing import Any

from clingen_link.mcp.next_commands import cmd

GENE_FORMS = ["gene symbol (e.g. BRCA1)", "HGNC id (e.g. HGNC:1100)"]
MONDO_FORMS = ["MONDO id (e.g. MONDO:0007254)"]
DISEASE_FORMS = ["disease name text (e.g. cardiomyopathy)", *MONDO_FORMS]

_NCBI_GENE = re.compile(r"(?:(?:NCBIGene|NCBI_Gene|Entrez|GeneID):)?(\d+)", re.IGNORECASE)
_VIA_HGNC = (
    ("Ensembl gene id", re.compile(r"ENSG\d{11}(\.\d+)?", re.IGNORECASE)),
    ("RefSeq accession", re.compile(r"[NX][MRP]_\d+(\.\d+)?", re.IGNORECASE)),
)
# No converter is named for a transcript id: hgnc_resolve_symbol does not resolve ENST ids.
_TRANSCRIPT = re.compile(r"ENST\d{11}(\.\d+)?", re.IGNORECASE)
_FOREIGN_DISEASE = re.compile(r"(OMIM|MIM|ORPHA|Orphanet|DOID):\d+", re.IGNORECASE)


class ForeignIdentifierError(ValueError):
    """A well-formed identifier in a namespace the tool does not take (``invalid_input``).

    Developer-authored text only: the caller's value appears solely in ``next_commands``, and
    only because it matched a strict pattern above.
    """

    def __init__(
        self,
        *,
        field: str,
        form: str,
        allowed_values: list[str],
        hint: str,
        next_commands: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            f"{field} was read as {form}, which this tool does not accept. Nothing was looked "
            "up, so this says nothing about whether ClinGen has curated it."
        )
        self.frame: dict[str, Any] = {
            "field": field,
            "allowed_values": allowed_values,
            "hint": hint,
        }
        self.next_commands = next_commands


def foreign_gene(value: str, field: str) -> ForeignIdentifierError | None:
    """The error for a gene given in a foreign namespace, or ``None`` if it is not one."""
    for form, pattern in _VIA_HGNC:
        if pattern.fullmatch(value):
            return ForeignIdentifierError(
                field=field,
                form=f"an {form}" if form[0] == "E" else f"a {form}",
                allowed_values=GENE_FORMS,
                hint="Convert it to the approved symbol with hgnc_resolve_symbol, then re-call.",
                next_commands=[cmd("hgnc_resolve_symbol", query=value)],
            )
    if _TRANSCRIPT.fullmatch(value):
        return ForeignIdentifierError(
            field=field,
            form="an Ensembl transcript id (this tool takes a gene: symbol or HGNC id)",
            allowed_values=GENE_FORMS,
            hint="Pass the approved symbol or HGNC id of the transcript's gene instead.",
            next_commands=[],
        )
    match = _NCBI_GENE.fullmatch(value)
    if match is None:
        return None
    number = match.group(1)
    bare = value == number
    return ForeignIdentifierError(
        field=field,
        form="an NCBI Gene id (a bare number is not a gene symbol)" if bare else "an NCBI Gene id",
        allowed_values=GENE_FORMS,
        hint=(
            "Convert the NCBI Gene id to the approved symbol with hgnc_resolve_gene_by_xref, "
            "then re-call."
            + (f" If the number is an HGNC id instead, pass HGNC:{number}." if bare else "")
        ),
        next_commands=[cmd("hgnc_resolve_gene_by_xref", source="entrez_id", value=number)],
    )


def foreign_disease(
    value: str, field: str, allowed_values: list[str]
) -> ForeignIdentifierError | None:
    """The error for a disease id from another ontology, or ``None`` if it is not one."""
    if not _FOREIGN_DISEASE.fullmatch(value):
        return None
    return ForeignIdentifierError(
        field=field,
        form="a disease id from another ontology",
        allowed_values=allowed_values,
        hint="Convert it to a MONDO id with mondo_resolve_disease, then re-call.",
        next_commands=[cmd("mondo_resolve_disease", query=value)],
    )
