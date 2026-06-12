"""Adapt live ERepo payloads into the normalized snapshot-row shape.

Two distinct live shapes exist:

* the classifications-search *summary* (``/api/classifications?caid=…&format=json``): ``gene`` is a
  ``{label, NCBI_id}`` dict and keys are camelCase (``publishedDate``, ``variationId``);
* the full SEPIO *interpretation* (``/api/interpretation/{uuid}?format=json``): ACMG criteria live
  under ``evidenceLine`` with ``statementOutcome`` for the call.

Feeding either straight into :meth:`VariantInterpretation.from_row` raises a Pydantic
``ValidationError`` (e.g. a ``gene`` dict where a ``str`` is expected) — the assessment's **H1**
bug, which was then mis-coded as ``validation_failed`` and told the LLM its valid CAID was malformed.
This pure adapter normalizes both shapes into the flat ``dict`` ``from_row`` consumes, leniently:
missing fields never raise.
"""

from __future__ import annotations

from typing import Any


def _str(value: Any) -> str | None:
    """Coerce a scalar / labelled-dict to a string, or ``None``."""
    if value is None:
        return None
    if isinstance(value, dict):
        label = (
            value.get("label") or value.get("preferredName") or value.get("@id") or value.get("id")
        )
        return str(label) if label is not None else None
    return str(value)


def _condition(value: Any) -> tuple[str | None, str | None]:
    """Return ``(disease_label, mondo)`` from a condition dict / string."""
    if isinstance(value, dict):
        raw = value.get("mondo") or value.get("curie") or value.get("@id") or value.get("id")
        mondo = str(raw) if isinstance(raw, str) and "MONDO:" in raw else None
        if mondo and "MONDO:" in mondo:
            mondo = "MONDO:" + mondo.split("MONDO:", 1)[1].split("/")[0]
        return _str(value), mondo
    return _str(value), None


def _evidence_codes(sepio: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split ``evidenceLine`` entries into (met, not_met) ACMG criterion labels."""
    met: list[str] = []
    not_met: list[str] = []
    for line in sepio.get("evidenceLine", []) or []:
        if not isinstance(line, dict):
            continue
        crit = line.get("evidenceCriterion") or line.get("criterion")
        label = crit if isinstance(crit, str) else _str(crit)
        if not label:
            continue
        if line.get("criterionMet") is True or line.get("met") is True:
            met.append(label)
        else:
            not_met.append(label)
    return met, not_met


def erepo_live_to_row(
    summary: dict[str, Any], *, sepio: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Normalize a live ERepo classifications summary (+ optional SEPIO doc) into a snapshot row."""
    disease, mondo = _condition(summary.get("condition"))
    row: dict[str, Any] = {
        "caid": _str(summary.get("caid")),
        "clinvar_variation_id": _str(
            summary.get("variationId") or summary.get("clinvarVariationId")
        ),
        "variation": _str(summary.get("label") or summary.get("variation")),
        "hgvs": [str(h) for h in (summary.get("hgvs") or []) if h],
        "gene": _str(summary.get("gene")),
        "disease": disease,
        "mondo": mondo or _str(summary.get("mondo")),
        "moi": _str(summary.get("modeOfInheritance") or summary.get("moi")),
        "assertion": _str(summary.get("assertion")),
        "evidence_codes_met": [],
        "evidence_codes_not_met": [],
        "summary": _str(summary.get("summary")),
        "pubmed": [str(p) for p in (summary.get("pubmed") or []) if p],
        "expert_panel": _str(summary.get("expertPanel") or summary.get("affiliation")),
        "guideline_cspec": _str(summary.get("guideline")),
        "approval_date": _str(summary.get("approvalDate")),
        "published_date": _str(summary.get("publishedDate")),
        "retracted": bool(summary.get("retracted")),
        "uuid": _str(summary.get("uuid")),
        "repo_link": _str(summary.get("@id") or summary.get("repoLink")),
    }
    if sepio:
        outcome = sepio.get("statementOutcome")
        if outcome is not None:
            row["assertion"] = _str(outcome) or row["assertion"]
        met, not_met = _evidence_codes(sepio)
        if met or not_met:
            row["evidence_codes_met"] = met
            row["evidence_codes_not_met"] = not_met
        row["summary"] = _str(sepio.get("summary")) or row["summary"]
    return row
