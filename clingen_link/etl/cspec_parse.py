"""Pure parsers for the ClinGen Criteria Specification Registry (cspec domain).

No I/O: every function takes already-fetched JSON-LD / HTML / header dicts and
returns plain row containers, so the build path is deterministic and unit-tested
from inline inputs. Attachment links are not present in the JSON-LD; they are
harvested from the rendered doc-page HTML and associated to the nearest enclosing
criterion code (spec-level when ambiguous).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_BASE = "https://cspec.genome.network"
# Deliberately assumes hex UUID file ids (the registry's id scheme); a non-hex
# id would silently not match and its attachment would be dropped.
_FILE_RE = re.compile(r"/cspec/File/id/([0-9a-fA-F-]+)/data")
_FILENAME_RE = re.compile(r'filename="?([^"\r\n;]+)"?')
# An ACMG/AMP code token as it appears in a doc-page heading (PVS1, PS3, PM2, BA1, BS3, BP7...).
_CODE_RE = re.compile(r"\b(P(VS|S|M|P)\d|B(A|S|P)\d)[A-Za-z0-9_]*\b")
_BASELINE_GN = {"GN001"}


@dataclass
class ParsedSpec:
    """Normalized rows for one criteria specification."""

    spec: dict[str, Any]
    rule_sets: list[dict[str, Any]] = field(default_factory=list)
    genes: list[dict[str, Any]] = field(default_factory=list)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    strengths: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)


def _tail_id(iri: str | None) -> str | None:
    """Return the trailing id segment of a `.../id/<val>` IRI."""
    if not iri:
        return None
    return iri.rsplit("/id/", 1)[-1] if "/id/" in iri else iri


def gn_id_of(jsonld: dict[str, Any]) -> str | None:
    """Return the GN id of a spec JSON-LD document."""
    return _tail_id(jsonld.get("@id"))


def criteria_count(jsonld: dict[str, Any]) -> int:
    """Total criteria codes across all rule sets."""
    return sum(len(rs.get("criteriaCodes", []) or []) for rs in jsonld.get("ruleSets", []) or [])


def is_published(jsonld: dict[str, Any]) -> bool:
    """Inclusion gate: Released-with-criteria, or the baseline doc GN001.

    Gates on ``cspecStatus`` (not ``currentStatus``, which drifts to e.g.
    'Pilot Rules In Prep' while a spec stays Released).
    """
    if criteria_count(jsonld) == 0:
        return False
    if (gn_id_of(jsonld) or "") in _BASELINE_GN:
        return True
    return (jsonld.get("cspecStatus") or "").strip() == "Released"


def _gene_symbol(gene: dict[str, Any]) -> str | None:
    iri = gene.get("@id") or ""
    if "query=" in iri:
        return iri.split("query=", 1)[-1].strip() or None
    return (gene.get("label") or "").strip() or None


def extract_file_urls(doc_html: str) -> list[str]:
    """Return absolute, de-duplicated attachment URLs in doc-page order."""
    out: list[str] = []
    for m in _FILE_RE.finditer(doc_html):
        url = f"{_BASE}/cspec/File/id/{m.group(1)}/data"
        if url not in out:
            out.append(url)
    return out


def _filename(headers: dict[str, str]) -> str | None:
    cd = headers.get("content-disposition") or ""
    m = _FILENAME_RE.search(cd)
    return m.group(1).strip() if m else None


def _associate_files(
    doc_html: str,
    gn_id: str,
    code_to_criteria: dict[str, str],
    heads: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Walk the doc HTML in order, tracking the current criterion heading.

    A file link is attributed to the most recent unambiguous code heading seen
    before it; spec-level (``criteria_id = None``) when none/ambiguous.

    Note: tracking keys on the most recent ACMG/AMP **code token**, not strictly
    a heading, so a code mentioned in prose (e.g. "differs from BS3 below") can
    redirect attribution to the wrong criterion. The spec-level fallback
    (``criteria_id = None`` for ambiguous codes) keeps the build correct, and
    this heuristic is validated against a real GN164 doc page in Task 12.
    """
    events: list[tuple[int, str, str]] = []
    for m in _CODE_RE.finditer(doc_html):
        events.append((m.start(), "code", m.group(0)))
    for m in _FILE_RE.finditer(doc_html):
        events.append((m.start(), "file", m.group(1)))
    events.sort(key=lambda e: e[0])

    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = None
    for _pos, kind, value in events:
        if kind == "code":
            current = code_to_criteria.get(value)
            continue
        if value in seen:
            continue
        seen.add(value)
        url = f"{_BASE}/cspec/File/id/{value}/data"
        headers = heads.get(url, {})
        size = headers.get("content-length")
        files.append(
            {
                "file_uuid": value,
                "gn_id": gn_id,
                "criteria_id": current,
                "filename": _filename(headers),
                "content_type": headers.get("content-type"),
                "size_bytes": int(size) if size and size.isdigit() else None,
                "download_url": url,
            }
        )
    return files


def parse_spec(
    jsonld: dict[str, Any],
    doc_html: str,
    heads: dict[str, dict[str, str]],
) -> ParsedSpec:
    """Normalize one spec's JSON-LD + doc-page attachments into row containers."""
    gn_id = gn_id_of(jsonld) or ""
    affiliation = jsonld.get("affiliation") or {}
    spec = {
        "gn_id": gn_id,
        "affiliation_id": _tail_id(affiliation.get("@id")),
        "affiliation_label": (affiliation.get("label") or "").strip() or None,
        "label": (jsonld.get("label") or "").strip() or None,
        "version": jsonld.get("version"),
        "cspec_status": jsonld.get("cspecStatus"),
        "current_status": jsonld.get("currentStatus"),
        "last_updated": jsonld.get("lastUpdated"),
        "permalink": f"{_BASE}/cspec/ui/svi/doc/{gn_id}",
    }
    parsed = ParsedSpec(spec=spec)
    code_to_criteria: dict[str, str] = {}
    for rs in jsonld.get("ruleSets", []) or []:
        rule_set_id = _tail_id(rs.get("@id")) or ""
        parsed.rule_sets.append({"rule_set_id": rule_set_id, "gn_id": gn_id})
        for gene in rs.get("genes", []) or []:
            symbol = _gene_symbol(gene)
            moi = gene.get("modeOfInheritance")
            diseases = gene.get("diseases", []) or [{}]
            for disease in diseases:
                parsed.genes.append(
                    {
                        "rule_set_id": rule_set_id,
                        "gn_id": gn_id,
                        "gene_symbol": symbol,
                        "hgnc_id": None,
                        "mondo": (disease.get("label") or None),
                        "moi": moi,
                    }
                )
        for ord_, code in enumerate(rs.get("criteriaCodes", []) or []):
            criteria_id = _tail_id(code.get("@id")) or ""
            label = code.get("label") or ""
            # A code repeated across rule sets with a different criteria_id is
            # ambiguous -> map it to spec-level ("") so its files attach to no
            # single criterion.
            if label in code_to_criteria and code_to_criteria[label] != criteria_id:
                code_to_criteria[label] = ""
            else:
                code_to_criteria.setdefault(label, criteria_id)
            parsed.criteria.append(
                {
                    "criteria_id": criteria_id,
                    "rule_set_id": rule_set_id,
                    "gn_id": gn_id,
                    "code": label,
                    "description": code.get("description"),
                    "ord": ord_,
                }
            )
            for s_ord, strength in enumerate(code.get("evidenceStrengths", []) or []):
                parsed.strengths.append(
                    {
                        "criteria_id": criteria_id,
                        "strength_label": strength.get("label"),
                        "applicability": strength.get("applicability"),
                        "description": strength.get("description"),
                        "ord": s_ord,
                    }
                )
    resolved = {k: v for k, v in code_to_criteria.items() if v}
    parsed.files = _associate_files(doc_html, gn_id, resolved, heads)
    return parsed
