"""Pydantic v2 response models, one per ClinGen domain + the gene hub.

Each model has a ``from_row`` classmethod that maps a raw store row (the ``dict``
shapes returned by :mod:`clingen_link.store.queries`) into a typed response with
a stable ``permalink`` and a verbatim ``recommended_citation``. Extra upstream
fields are ignored (``model_config extra='ignore'``) so a snapshot schema
addition never breaks construction.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import citations


class _Base(BaseModel):
    """Shared config: ignore unknown row keys, allow population by field name."""

    model_config = ConfigDict(extra="ignore")


class ValidityAssertion(_Base):
    """A gene-disease validity assertion (Definitive … Refuted)."""

    symbol: str
    hgnc_id: str | None = None
    disease_name: str | None = None
    disease_obsolete: bool = False
    mondo: str | None = None
    moi: str | None = None
    classification: str | None = None
    expert_panel: str | None = None
    sop: str | None = None
    perm_id: str | None = None
    classified_date: str | None = None
    released: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ValidityAssertion:
        """Build from a ``validity`` store row (disease_name HTML-sanitized).

        Defense-in-depth: the ETL also sanitizes labels at build time, but sanitizing here too means
        an older snapshot that still carries ``<span>…Obsolete Term</span>`` markup never leaks raw
        HTML into the citation, and ``disease_obsolete`` is derived from the label when the snapshot
        lacks the structured column (assessment M1).
        """
        from ..etl.sanitize import is_obsolete_label, strip_html

        raw_disease = row.get("disease_name")
        clean = dict(row)
        clean["disease_name"] = strip_html(raw_disease) or None
        clean["disease_obsolete"] = bool(row.get("disease_obsolete")) or is_obsolete_label(
            raw_disease
        )
        permalink, citation = citations.validity_citation(clean)
        return cls(permalink=permalink, recommended_citation=citation, **_pick(clean, cls))


class DosageRecord(_Base):
    """A gene or region dosage-sensitivity record (haplo + triplo)."""

    record_type: str
    symbol: str | None = None
    hgnc_id: str | None = None
    isca_id: str | None = None
    cytoband: str | None = None
    grch37: str | None = None
    grch38: str | None = None
    haplo_score: str | None = None
    haplo_description: str | None = None
    haplo_mondo: str | None = None
    haplo_pmids: list[str] = Field(default_factory=list)
    triplo_score: str | None = None
    triplo_description: str | None = None
    triplo_mondo: str | None = None
    triplo_pmids: list[str] = Field(default_factory=list)
    date_last_evaluated: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> DosageRecord:
        """Build from a ``dosage`` store row (PMID lists already decoded)."""
        permalink, citation = citations.dosage_citation(row)
        return cls(permalink=permalink, recommended_citation=citation, **_pick(row, cls))


class ActionabilityCuration(_Base):
    """A clinical-actionability curation (adult + pediatric assertions)."""

    doc_id: str
    disease: str | None = None
    curation_type: str | None = None
    genes: list[str] = Field(default_factory=list)
    modes_of_inheritance: list[str] = Field(default_factory=list)
    adult_status: str | None = None
    adult_release: str | None = None
    adult_sepio_iri: str | None = None
    pediatric_status: str | None = None
    pediatric_release: str | None = None
    pediatric_sepio_iri: str | None = None
    last_updated: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any], *, context: str = "Adult") -> ActionabilityCuration:
        """Build from an ``actionability`` store row.

        ``context`` selects which status/release seeds the recommended citation
        (the SEPIO IRIs for both contexts remain on the model).
        """
        if context == "Pediatric":
            status, release = row.get("pediatric_status"), row.get("pediatric_release")
        else:
            status, release = row.get("adult_status"), row.get("adult_release")
        citation = citations.actionability_citation(
            row, context=context, status=str(status or ""), release=str(release or "")
        )
        permalink = "https://actionability.clinicalgenome.org/ac/"
        return cls(permalink=permalink, recommended_citation=citation, **_pick(row, cls))


class VariantInterpretation(_Base):
    """An ERepo expert-panel ACMG variant interpretation."""

    caid: str | None = None
    clinvar_variation_id: str | None = None
    gene: str | None = None
    disease: str | None = None
    mondo: str | None = None
    moi: str | None = None
    assertion: str | None = None
    hgvs: list[str] = Field(default_factory=list)
    evidence_codes_met: list[str] = Field(default_factory=list)
    evidence_codes_not_met: list[str] = Field(default_factory=list)
    summary: str | None = None
    pubmed: list[str] = Field(default_factory=list)
    expert_panel: str | None = None
    guideline_cspec: str | None = None
    approval_date: str | None = None
    published_date: str | None = None
    retracted: bool = False
    uuid: str | None = None
    repo_link: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> VariantInterpretation:
        """Build from an ``erepo`` store row (list fields already decoded)."""
        permalink, citation = citations.erepo_citation(row)
        data = _pick(row, cls)
        data["retracted"] = bool(row.get("retracted"))
        return cls(permalink=permalink, recommended_citation=citation, **data)


class ExpertPanel(_Base):
    """A ClinGen GCEP/VCEP affiliate with its curation count."""

    affiliate_id: str
    label: str | None = None
    total_curations: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ExpertPanel:
        """Build from an ``expert_panel`` store row."""
        return cls(**_pick(row, cls))


class GeneSummary(_Base):
    """Cross-domain one-call overview for a gene (flagship hub response)."""

    symbol: str
    hgnc_id: str | None = None
    name: str | None = None
    has_validity: bool = False
    has_dosage: bool = False
    has_actionability: bool = False
    validity_count: int = 0
    dosage_count: int = 0
    actionability_count: int = 0
    erepo_count: int = 0
    validity: list[ValidityAssertion] = Field(default_factory=list)
    dosage: list[DosageRecord] = Field(default_factory=list)
    actionability: list[ActionabilityCuration] = Field(default_factory=list)
    recommended_citation: str

    @classmethod
    def from_counts(
        cls,
        counts: dict[str, Any],
        *,
        validity: list[ValidityAssertion],
        dosage: list[DosageRecord],
        actionability: list[ActionabilityCuration],
    ) -> GeneSummary:
        """Build the summary from a ``gene_summary_counts`` row + domain models."""
        symbol = str(counts.get("symbol"))
        citation = (
            f"ClinGen gene summary for {symbol}: "
            f"{counts.get('validity_count', 0)} validity, "
            f"{counts.get('dosage_count', 0)} dosage, "
            f"{counts.get('actionability_count', 0)} actionability, "
            f"{counts.get('erepo_count', 0)} ERepo variant interpretations. "
            f"https://search.clinicalgenome.org/kb/genes/?search={symbol}"
        )
        return cls(
            symbol=symbol,
            hgnc_id=counts.get("hgnc_id"),
            name=counts.get("name"),
            has_validity=bool(counts.get("has_validity")),
            has_dosage=bool(counts.get("has_dosage")),
            has_actionability=bool(counts.get("has_actionability")),
            validity_count=int(counts.get("validity_count", 0)),
            dosage_count=int(counts.get("dosage_count", 0)),
            actionability_count=int(counts.get("actionability_count", 0)),
            erepo_count=int(counts.get("erepo_count", 0)),
            validity=validity,
            dosage=dosage,
            actionability=actionability,
            recommended_citation=citation,
        )


class EvidenceStrength(_Base):
    """One strength level for a criterion (applicability + optional spec text)."""

    strength_label: str | None = None
    applicability: str | None = None
    description: str | None = None


class CspecFile(_Base):
    """A supplementary guidance attachment for a spec or criterion."""

    file_uuid: str
    criteria_id: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None


class CriteriaCode(_Base):
    """One ACMG/AMP criterion as specified by a VCEP."""

    criteria_id: str
    gn_id: str
    rule_set_id: str | None = None
    code: str
    description: str | None = None
    strengths: list[EvidenceStrength] = Field(default_factory=list)
    files: list[CspecFile] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CriteriaCode:
        """Build from a criterion store row (strengths/files already attached)."""
        strengths = [
            EvidenceStrength(**_pick(s, EvidenceStrength)) for s in row.get("strengths", [])
        ]
        files = [CspecFile(**_pick(f, CspecFile)) for f in row.get("files", [])]
        data = _pick(row, cls)
        data.update(strengths=strengths, files=files)
        return cls(**data)


class CspecGene(_Base):
    """A gene/disease covered by a spec's rule set."""

    gene_symbol: str | None = None
    hgnc_id: str | None = None
    mondo: str | None = None
    moi: str | None = None


class CspecSummary(_Base):
    """Spec header (catalog row)."""

    gn_id: str
    affiliation_id: str | None = None
    affiliation_label: str | None = None
    label: str | None = None
    version: str | None = None
    cspec_status: str | None = None
    current_status: str | None = None
    last_updated: str | None = None
    permalink: str
    recommended_citation: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CspecSummary:
        """Build a spec header with permalink + citation."""
        permalink, citation = citations.cspec_citation(row)
        return cls(permalink=permalink, recommended_citation=citation, **_pick(row, cls))


class CspecDetail(CspecSummary):
    """Spec header plus its genes, criteria, and file catalog."""

    genes: list[CspecGene] = Field(default_factory=list)
    criteria: list[CriteriaCode] = Field(default_factory=list)
    files: list[CspecFile] = Field(default_factory=list)

    @classmethod
    def assemble(
        cls,
        spec_row: dict[str, Any],
        *,
        genes: list[dict[str, Any]],
        criteria: list[dict[str, Any]],
        files: list[dict[str, Any]],
    ) -> CspecDetail:
        """Build a full detail object from store rows."""
        permalink, citation = citations.cspec_citation(spec_row)
        return cls(
            permalink=permalink,
            recommended_citation=citation,
            genes=[CspecGene(**_pick(g, CspecGene)) for g in genes],
            criteria=[CriteriaCode.from_row(c) for c in criteria],
            files=[CspecFile(**_pick(f, CspecFile)) for f in files],
            **_pick(spec_row, CspecSummary),
        )


def _pick(row: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """Project ``row`` onto the model's own fields (minus the derived two).

    ``permalink`` / ``recommended_citation`` are passed explicitly by each
    ``from_row`` so they are excluded here to avoid duplicate-keyword errors.
    """
    derived = {"permalink", "recommended_citation"}
    fields = set(model.model_fields) - derived
    return {k: v for k, v in row.items() if k in fields}
