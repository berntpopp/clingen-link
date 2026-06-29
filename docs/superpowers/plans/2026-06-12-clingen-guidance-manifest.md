# ClinGen Guidance Manifest (`clingen://guidance`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `clingen://guidance` MCP resource that serves a curated, provenance-verified manifest of ClinGen/SVI variant-classification recommendations (codes affected + PMID/PMCID + OA license + fulltext-access map) — pointers only, no paper fulltext.

**Architecture:** A committed JSON data file (`clingen_link/data/svi_guidance.json`) holds the manifest. A cached loader in `resources.py` exposes it via `get_guidance_resource()`; `tools/metadata.py` registers the `clingen://guidance` resource handler. The manifest is reference data, not curated rules — it tells a consuming model *where* the authoritative sources are (VCEP CSpecs via `get_cspec`; papers by PMID via the user's literature MCPs) and which papers are openly licensed. This deliberately avoids the server authoring or redistributing classification logic.

**Tech Stack:** Python 3.12 (strict mypy), FastMCP v3, `importlib.resources` for bundled-data loading, pytest + pytest-asyncio. Hatchling wheel build (`make ci-local` gate).

---

## Background & Non-Goals

- **In scope (Tier 1):** pointers + verified `pmid`/`pmcid`/`oa_license`/`fulltext` per recommendation, plus the GN001 baseline pointer and a precedence note that VCEP CSpecs are authoritative.
- **Out of scope:** ingesting paper fulltext, embeddings/RAG, encoding any thresholds or decision logic (that is curation; CSpec already provides the machine-readable per-gene encoding).
- **Licensing constraint that shapes the design:** ClinGen *data* is CC BY 4.0, but the recommendation *papers* are mostly journal-copyrighted. Verified during research: PP3/BP4 (Pejaver 2022) and PS3/BS3 (Brnich 2019) are **CC BY 4.0** (redistributable); PVS1 (Abou Tayoun 2018), the ACMG/AMP baseline (Richards 2015), the Bayesian framework (Tavtigian 2018), and the point system (Tavtigian 2020) are **NIH/publisher author manuscripts** (free to read, NOT redistributable); BA1 (Ghosh 2018) is **not in PMC**. The manifest records this so a consumer knows which fulltext it may pull directly vs. must reach by PMID through a literature server.

## File Structure

| File | Responsibility |
|------|----------------|
| `clingen_link/data/svi_guidance.json` (create) | The manifest data: baseline + recommendation entries with provenance/license fields. Hand-curated reference data. |
| `clingen_link/mcp/resources.py` (modify) | Add `_guidance_manifest()` cached loader + `get_guidance_resource()`; add `clingen://guidance` to `_RESOURCES`. |
| `clingen_link/mcp/tools/metadata.py` (modify) | Import `get_guidance_resource`; register `@mcp.resource("clingen://guidance")` handler. |
| `clingen_link/mcp/facade.py` (modify) | One-line discovery mention of `clingen://guidance` in the server instructions string. |
| `tests/unit/test_guidance_data.py` (create) | Schema + invariants for the JSON; "no unverified entries ship" guarantee. |
| `tests/unit/test_mcp_infra.py` (modify) | Direct-call shape test for `get_guidance_resource()`. |
| `tests/unit/test_capabilities.py` (modify) | Add `clingen://guidance` to the resolve-list; `read_resource` content test; capabilities-map assertion. |
| `docs/architecture.md` (modify) | One-line note documenting the new resource. |

## Field contract (enforced by tests)

Each entry in `recommendations[]`:
- `id`: stable kebab-case slug (unique).
- `title`: string.
- `category`: one of `general | criteria-specific | endorsed`.
- `codes`: list of ACMG/AMP code strings (may be empty for cross-cutting items).
- `clingen_doc_url`: `https://clinicalgenome.org/docs/...` string.
- `version`: string or `null`.
- `pmid`: digit-string or `null`.
- `pmcid`: `PMC\d+` string or `null`.
- `oa_license`: one of `CC-BY-4.0 | CC-BY-NC-4.0 | CC-BY-NC-ND-4.0 | author-manuscript | not-in-pmc | none | unverified`.
- `fulltext`: one of `redistributable | read-only | unavailable | unverified`.
- `fulltext_access`: string (how to obtain text, e.g. "Fetch passages by PMID via pubtator-link").
- `artifacts`: list of strings (ClinGen-authored attachments, may be empty).

Top-level keys: `source_index`, `source_last_updated`, `baseline`, `recommendations`, `license_legend`, `note`, `research_use_notice`, `unsafe_for_clinical_use`.

---

### Task 1: Manifest data file + schema test

**Files:**
- Create: `clingen_link/data/svi_guidance.json`
- Create: `tests/unit/test_guidance_data.py`

- [ ] **Step 1: Write the failing schema test**

Create `tests/unit/test_guidance_data.py`:

```python
"""Schema + provenance invariants for the clingen://guidance manifest data."""

from __future__ import annotations

import json
from importlib.resources import files

OA_LICENSE = {
    "CC-BY-4.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC-ND-4.0",
    "author-manuscript",
    "not-in-pmc",
    "none",
    "unverified",
}
FULLTEXT = {"redistributable", "read-only", "unavailable", "unverified"}
CATEGORY = {"general", "criteria-specific", "endorsed"}


def _manifest() -> dict:
    raw = files("clingen_link.data").joinpath("svi_guidance.json").read_text(encoding="utf-8")
    return json.loads(raw)


def test_top_level_shape() -> None:
    m = _manifest()
    assert m["source_index"].startswith("https://clinicalgenome.org/")
    assert isinstance(m["recommendations"], list) and m["recommendations"]
    assert m["unsafe_for_clinical_use"] is True
    assert m["research_use_notice"]
    assert m["baseline"]["pmid"] == "25741868"  # ACMG/AMP 2015 baseline


def test_entry_invariants() -> None:
    m = _manifest()
    ids = [e["id"] for e in m["recommendations"]]
    assert len(ids) == len(set(ids)), "entry ids must be unique"
    for e in m["recommendations"]:
        assert e["category"] in CATEGORY, e["id"]
        assert isinstance(e["codes"], list), e["id"]
        assert e["clingen_doc_url"].startswith("https://clinicalgenome.org/docs/"), e["id"]
        assert e["oa_license"] in OA_LICENSE, e["id"]
        assert e["fulltext"] in FULLTEXT, e["id"]
        assert e["pmid"] is None or e["pmid"].isdigit(), e["id"]
        assert e["pmcid"] is None or e["pmcid"].startswith("PMC"), e["id"]


def test_verified_cc_by_entries_tagged() -> None:
    """The two confirmed CC BY papers must be redistributable."""
    m = {e["id"]: e for e in _manifest()["recommendations"]}
    for slug in ("pp3-bp4-calibration", "ps3-bs3-functional"):
        assert m[slug]["oa_license"] == "CC-BY-4.0", slug
        assert m[slug]["fulltext"] == "redistributable", slug
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guidance_data.py -q`
Expected: FAIL — `FileNotFoundError` / resource missing (JSON not created yet).

- [ ] **Step 3: Create the manifest JSON**

Create `clingen_link/data/svi_guidance.json` with the verified seed below. Entries marked `"unverified"` are filled in Task 2.

```json
{
  "source_index": "https://clinicalgenome.org/tools/clingen-variant-classification-guidance/",
  "source_last_updated": "2025-07",
  "baseline": {
    "gn_id": "GN001",
    "title": "Standards and guidelines for the interpretation of sequence variants (ACMG/AMP 2015)",
    "pmid": "25741868",
    "pmcid": "PMC4544753",
    "oa_license": "author-manuscript",
    "fulltext": "read-only",
    "note": "Unmodified 2015 framework. The SVI refinements below are applied per-VCEP, not folded into GN001. Prefer a VCEP CSpec (get_cspec) whenever one exists for the gene."
  },
  "recommendations": [
    {"id": "multi-disorder-genes", "title": "Classifying variants in genes associated with multiple disorders", "category": "general", "codes": [], "clingen_doc_url": "https://clinicalgenome.org/docs/clingens-guidance-classifying-variants-in-genes-associated-with-multiple-disorders/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "gnomad-v4", "title": "Guidance to VCEPs regarding the use of gnomAD v4", "category": "general", "codes": ["BA1", "BS1", "PM2"], "clingen_doc_url": "https://clinicalgenome.org/docs/clingen-guidance-to-vceps-regarding-the-use-of-gnomad-v4-march-2024/", "version": "March 2024", "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "code-strength-renaming", "title": "Recommendations for ACMG/AMP criteria code modifications (strength renaming)", "category": "general", "codes": [], "clingen_doc_url": "https://clinicalgenome.org/docs/clingen-sequence-variant-interpretation-working-group-recommendations-for-acmg-amp-guideline-criteria-code-modifications/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "bayesian-framework", "title": "Modeling the ACMG/AMP variant classification guidelines as a Bayesian classification framework", "category": "general", "codes": [], "clingen_doc_url": "https://clinicalgenome.org/docs/modeling-the-acmg-amp-variant-classification-gudielines-as-a-bayesian-classification-framework/", "version": null, "pmid": "29300386", "pmcid": "PMC6336098", "oa_license": "author-manuscript", "fulltext": "read-only", "fulltext_access": "Fetch passages by PMID via pubtator-link", "artifacts": []},
    {"id": "ba1-stand-alone", "title": "Updated recommendation for the benign stand-alone (BA1) ACMG/AMP criterion", "category": "criteria-specific", "codes": ["BA1"], "clingen_doc_url": "https://clinicalgenome.org/docs/updated-recommendation-for-the-benign-stand-alone-acmg-amp-criterion/", "version": null, "pmid": "29750498", "pmcid": null, "oa_license": "not-in-pmc", "fulltext": "unavailable", "fulltext_access": "Not in PMC; cite via doc page / publisher", "artifacts": ["BA1 Exception List (July 2018)", "Nomination Form"]},
    {"id": "pvs1-lof", "title": "Recommendations for interpreting the loss-of-function PVS1 ACMG/AMP variant criterion", "category": "criteria-specific", "codes": ["PVS1"], "clingen_doc_url": "https://clinicalgenome.org/docs/recommendations-for-interpreting-the-loss-of-function-pvs1-acmg-amp-variant-criterion/", "version": null, "pmid": "30192042", "pmcid": "PMC6185798", "oa_license": "author-manuscript", "fulltext": "read-only", "fulltext_access": "Fetch passages by PMID via pubtator-link", "artifacts": ["ClinGen SVI PVS1 Decision Tree"]},
    {"id": "splicing-framework", "title": "Application of the ACMG/AMP framework to predicted and observed splicing impact", "category": "criteria-specific", "codes": ["PVS1", "PS1", "PP3", "BP4", "BP7"], "clingen_doc_url": "https://clinicalgenome.org/docs/application-of-the-acmg-amp-framework-to-capture-evidence-relevant-to-predicted-and-observed-impact-on-splicing-recommendations/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "ps2-pm6-de-novo", "title": "Recommendation for de novo (PS2 and PM6) ACMG/AMP criteria", "category": "criteria-specific", "codes": ["PS2", "PM6"], "clingen_doc_url": "https://clinicalgenome.org/docs/ps2-pm6-recommendation-for-de-novo-ps2-and-pm6-acmg-amp-criteria-version-1.0/", "version": "1.1", "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "ps3-bs3-functional", "title": "Recommendations for application of the functional evidence PS3/BS3 criterion", "category": "criteria-specific", "codes": ["PS3", "BS3"], "clingen_doc_url": "https://clinicalgenome.org/docs/recommendations-for-application-of-the-functional-evidence-ps3-bs3-criterion-using-the-acmg-amp-sequence-variant-interpretation/", "version": null, "pmid": "31892348", "pmcid": "PMC6938631", "oa_license": "CC-BY-4.0", "fulltext": "redistributable", "fulltext_access": "CC BY 4.0 — may excerpt with attribution; PMID via pubtator-link", "artifacts": ["SVI Functional Assay Documentation Worksheet"]},
    {"id": "pm2-absence-rarity", "title": "Recommendation for absence/rarity (PM2)", "category": "criteria-specific", "codes": ["PM2"], "clingen_doc_url": "https://clinicalgenome.org/docs/pm2-recommendation-for-absence-rarity/", "version": "1.0", "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "pm3-in-trans", "title": "Recommendation for in-trans criterion (PM3)", "category": "criteria-specific", "codes": ["PM3"], "clingen_doc_url": "https://clinicalgenome.org/docs/pm3-recommendation-for-in-trans-criterion-pm3-version-1.0/", "version": "1.0", "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "pp1-bs4-pp4", "title": "Guidance for PP1/BS4 co-segregation and PP4 phenotype-specificity criteria", "category": "criteria-specific", "codes": ["PP1", "BS4", "PP4"], "clingen_doc_url": "https://clinicalgenome.org/docs/clingen-guidance-for-use-of-the-pp1-bs4-co-segregation-and-pp4-phenotype-specificity-criteria-for-sequence-variant/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "pp3-bp4-calibration", "title": "Calibration of computational tools for missense (PP3/BP4)", "category": "criteria-specific", "codes": ["PP3", "BP4"], "clingen_doc_url": "https://clinicalgenome.org/docs/calibration-of-computational-tools-for-missense-variant-pathogenicity-classification-and-clingen-recommendations-for-pp3-bp4-cri/", "version": null, "pmid": "36413997", "pmcid": "PMC9748256", "oa_license": "CC-BY-4.0", "fulltext": "redistributable", "fulltext_access": "CC BY 4.0 — may excerpt with attribution; PMID via pubtator-link", "artifacts": []},
    {"id": "pp5-bp6-reputable-source", "title": "The ACMG/AMP reputable-source criteria (PP5/BP6)", "category": "criteria-specific", "codes": ["PP5", "BP6"], "clingen_doc_url": "https://clinicalgenome.org/docs/the-acmg-amp-reputable-source-criteria-for-the-interpretation-of-sequence-variants/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []},
    {"id": "point-system", "title": "Fitting a naturally scaled point system to the ACMG/AMP guidelines", "category": "endorsed", "codes": [], "clingen_doc_url": "https://clinicalgenome.org/docs/fitting-a-naturally-scaled-point-system-to-the-acmg-amp-variant-classification-guidelines/", "version": null, "pmid": "32720330", "pmcid": "PMC8011844", "oa_license": "author-manuscript", "fulltext": "read-only", "fulltext_access": "Fetch passages by PMID via pubtator-link", "artifacts": []},
    {"id": "non-coding", "title": "Recommendations for clinical interpretation of non-coding region variants", "category": "endorsed", "codes": [], "clingen_doc_url": "https://clinicalgenome.org/docs/recommendations-for-clinical-interpretation-of-variants-found-in-non-coding-regions-of-the-genome/", "version": null, "pmid": null, "pmcid": null, "oa_license": "unverified", "fulltext": "unverified", "fulltext_access": "See ClinGen doc page", "artifacts": []}
  ],
  "license_legend": {
    "CC-BY-4.0": "Open access; may excerpt/redistribute with attribution.",
    "author-manuscript": "Free to read on PMC but copyright retained; do NOT redistribute fulltext. Reach by PMID via a literature MCP.",
    "not-in-pmc": "No PMC fulltext; cite via the ClinGen doc page or publisher.",
    "unverified": "License not yet confirmed; treat as read-only until verified."
  },
  "note": "Pointers + provenance only; no paper fulltext is redistributed here. VCEP CSpecs (get_cspec) are the authoritative, gene-specific, machine-readable encoding of these criteria — prefer them whenever one exists for the gene. GN001 is the unmodified 2015 baseline.",
  "research_use_notice": "Research use only; not for clinical decision support.",
  "unsafe_for_clinical_use": true
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_guidance_data.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/data/svi_guidance.json tests/unit/test_guidance_data.py
git commit -m "feat(guidance): seed SVI variant-classification manifest data + schema test"
```

---

### Task 2: Verify remaining PMIDs/licenses; enforce no `unverified` ships

**Files:**
- Modify: `clingen_link/data/svi_guidance.json`
- Modify: `tests/unit/test_guidance_data.py`

- [ ] **Step 1: Write the failing guarantee test**

Append to `tests/unit/test_guidance_data.py`:

```python
def test_no_unverified_entries_ship() -> None:
    """Tier 1 ships fully verified: no oa_license/fulltext left as 'unverified'."""
    m = _manifest()
    for e in m["recommendations"]:
        assert e["oa_license"] != "unverified", e["id"]
        assert e["fulltext"] != "unverified", e["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guidance_data.py::test_no_unverified_entries_ship -q`
Expected: FAIL (9 entries still `unverified`).

- [ ] **Step 3: Resolve provenance for each `unverified` entry and fill fields**

For each entry whose `oa_license == "unverified"` (`multi-disorder-genes`, `gnomad-v4`, `code-strength-renaming`, `splicing-framework`, `ps2-pm6-de-novo`, `pm2-absence-rarity`, `pm3-in-trans`, `pp1-bs4-pp4`, `pp5-bp6-reputable-source`, `non-coding`), apply this exact procedure:

1. Open the entry's `clingen_doc_url`; read the citation block to get the PMID (some general/web recommendations have **no** journal paper — if so, set `pmid: null`, `pmcid: null`, `oa_license: "none"`, `fulltext: "unavailable"`, `fulltext_access: "ClinGen web recommendation; cite the doc page"`).
2. If a PMID exists, resolve PMID→PMCID:
   `https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?ids=<PMID>&format=json`
   - No PMCID returned → `pmcid: null`, `oa_license: "not-in-pmc"`, `fulltext: "unavailable"`.
3. If a PMCID exists, open `https://pmc.ncbi.nlm.nih.gov/articles/<PMCID>/` and read the license statement. Map per `license_legend`:
   - "open access ... CC BY" → `oa_license: "CC-BY-4.0"`, `fulltext: "redistributable"`.
   - "CC BY-NC" / "CC BY-NC-ND" → tag accordingly, `fulltext: "read-only"` (ND forbids the derivative step).
   - "Author manuscript" / NPG/Wiley reader terms (no CC) → `oa_license: "author-manuscript"`, `fulltext: "read-only"`, `fulltext_access: "Fetch passages by PMID via pubtator-link"`.
4. Set `version` from the doc page where stated (e.g. `pm2-absence-rarity` → "1.0").

Update each entry's fields in `clingen_link/data/svi_guidance.json` accordingly. Leave the 7 already-verified entries (and the baseline) untouched.

- [ ] **Step 4: Run the full data test suite to verify it passes**

Run: `uv run pytest tests/unit/test_guidance_data.py -q`
Expected: PASS (all tests, including `test_no_unverified_entries_ship`).

- [ ] **Step 5: Commit**

```bash
git add clingen_link/data/svi_guidance.json tests/unit/test_guidance_data.py
git commit -m "feat(guidance): verify PMIDs + OA licenses for all manifest entries"
```

---

### Task 3: `get_guidance_resource()` loader + `_RESOURCES` entry

**Files:**
- Modify: `clingen_link/mcp/resources.py`
- Modify: `tests/unit/test_mcp_infra.py`

- [ ] **Step 1: Write the failing direct-call test**

Add to `tests/unit/test_mcp_infra.py` (import alongside the existing `clingen_link.mcp.resources` imports near line 9):

```python
def test_guidance_resource_shape() -> None:
    from clingen_link.mcp.resources import get_guidance_resource

    g = get_guidance_resource()
    assert isinstance(g["recommendations"], list) and g["recommendations"]
    assert g["unsafe_for_clinical_use"] is True
    assert g["research_use_notice"]
    assert g["baseline"]["gn_id"] == "GN001"
    # Every entry carries a license tag the consumer can act on.
    assert all(e["oa_license"] for e in g["recommendations"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mcp_infra.py::test_guidance_resource_shape -q`
Expected: FAIL — `ImportError: cannot import name 'get_guidance_resource'`.

- [ ] **Step 3: Implement the loader + resource function + registry entry**

In `clingen_link/mcp/resources.py`, add imports near the top (after the existing `import json` / `from typing import Any`):

```python
from functools import lru_cache
from importlib.resources import files
```

Add `"clingen://guidance"` to the `_RESOURCES` dict (after the `clingen://citations` line):

```python
    "clingen://guidance": (
        "ClinGen/SVI variant-classification recommendation manifest: codes affected, "
        "PMID/PMCID, OA license + fulltext-access map (JSON). Pointers only."
    ),
```

Add the loader + accessor (place near `get_citations_resource`):

```python
@lru_cache(maxsize=1)
def _guidance_manifest() -> dict[str, Any]:
    """Load and cache the committed SVI guidance manifest (read-only reference data)."""
    raw = files("clingen_link.data").joinpath("svi_guidance.json").read_text(encoding="utf-8")
    return json.loads(raw)


def get_guidance_resource() -> dict[str, Any]:
    """Return the ClinGen/SVI variant-classification guidance manifest.

    Pointers + provenance only: codes affected, PMID/PMCID, OA license, and how to
    obtain fulltext (CC BY papers directly; author manuscripts by PMID via a
    literature MCP). VCEP CSpecs remain the authoritative per-gene encoding.
    """
    manifest = dict(_guidance_manifest())
    manifest.setdefault("research_use_notice", RESEARCH_USE_NOTICE)
    return manifest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_mcp_infra.py::test_guidance_resource_shape -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clingen_link/mcp/resources.py tests/unit/test_mcp_infra.py
git commit -m "feat(guidance): add get_guidance_resource loader + registry entry"
```

---

### Task 4: Register the `clingen://guidance` resource handler

**Files:**
- Modify: `clingen_link/mcp/tools/metadata.py`
- Modify: `tests/unit/test_capabilities.py`

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_capabilities.py`, add `"clingen://guidance"` to the `uris` list inside `test_all_resources_resolve` (after `"clingen://citations"`):

```python
            "clingen://citations",
            "clingen://guidance",
```

And add a content test in the `TestCapabilities` class:

```python
    async def test_guidance_resource_has_recommendations(self, tool_mcp: FastMCP) -> None:
        result = await tool_mcp.read_resource("clingen://guidance")
        import json

        payload = json.loads(result.contents[0].content)
        assert payload["unsafe_for_clinical_use"] is True
        recs = {e["id"]: e for e in payload["recommendations"]}
        # A confirmed CC BY entry is present and tagged redistributable.
        assert recs["pp3-bp4-calibration"]["oa_license"] == "CC-BY-4.0"
        assert recs["pp3-bp4-calibration"]["fulltext"] == "redistributable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_capabilities.py -k "all_resources_resolve or guidance" -q`
Expected: FAIL — `clingen://guidance` not registered, `read_resource` raises.

- [ ] **Step 3: Register the resource handler**

In `clingen_link/mcp/tools/metadata.py`, add `get_guidance_resource` to the imports from `clingen_link.mcp.resources` (the block at lines 13-20):

```python
    get_guidance_resource,
```

Add the handler after the `citations_resource` handler (after line 120):

```python
    @mcp.resource(
        "clingen://guidance",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def guidance_resource() -> dict[str, Any]:
        return get_guidance_resource()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_capabilities.py -k "all_resources_resolve or guidance" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clingen_link/mcp/tools/metadata.py tests/unit/test_capabilities.py
git commit -m "feat(guidance): register clingen://guidance MCP resource"
```

---

### Task 5: Discovery wiring (facade instructions + capabilities map assertion)

**Files:**
- Modify: `clingen_link/mcp/facade.py`
- Modify: `tests/unit/test_capabilities.py`

`_RESOURCES` already feeds the capabilities `resources` map, so `clingen://guidance` surfaces in `get_server_capabilities` automatically once Task 3 lands. This task adds the human-readable instruction mention and locks the capabilities-map presence with a test.

- [ ] **Step 1: Write the failing capabilities-map assertion**

In `tests/unit/test_capabilities.py`, extend `test_error_codes_and_conventions` with:

```python
        assert payload["resources"]["clingen://guidance"]
```

- [ ] **Step 2: Run test to verify it passes already (resources map is auto-fed)**

Run: `uv run pytest tests/unit/test_capabilities.py::TestCapabilities::test_error_codes_and_conventions -q`
Expected: PASS (auto-included via `_RESOURCES`). If it FAILS, Task 3's `_RESOURCES` edit is missing — fix there.

- [ ] **Step 3: Add the discovery mention to the server instructions**

In `clingen_link/mcp/facade.py`, in the instructions string around lines 46-48 (the "Discovery:" sentence listing resources), append a clause:

```python
    "clingen://freshness holds the per-domain snapshot version; "
    "clingen://guidance holds the ClinGen/SVI variant-classification recommendation "
    "manifest (codes, PMIDs, OA license) — pointers only, prefer get_cspec for a "
    "gene's authoritative criteria. "
```

(Match the existing adjacent string-concatenation style; do not introduce a trailing comma that breaks the literal.)

- [ ] **Step 4: Run the capabilities + facade tests**

Run: `uv run pytest tests/unit/test_capabilities.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clingen_link/mcp/facade.py tests/unit/test_capabilities.py
git commit -m "feat(guidance): surface clingen://guidance in discovery instructions"
```

---

### Task 6: Document the resource + final gate

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Add a one-line note to architecture.md**

Find the section that enumerates `clingen://` resources (search for `clingen://citations`) and add:

```markdown
- `clingen://guidance` — curated manifest of ClinGen/SVI variant-classification
  recommendations (codes affected, PMID/PMCID, OA license + fulltext-access map).
  Pointers only; no paper fulltext. VCEP CSpecs (`get_cspec`) remain authoritative
  per gene.
```

- [ ] **Step 2: Verify the JSON is bundled into the wheel**

Run:
```bash
uv build --wheel 2>/dev/null && python -c "import zipfile,glob; w=sorted(glob.glob('dist/*.whl'))[-1]; print([n for n in zipfile.ZipFile(w).namelist() if 'svi_guidance' in n])"
```
Expected: prints `['clingen_link/data/svi_guidance.json']` (non-empty). If empty, add `clingen_link/data/svi_guidance.json` to `[tool.hatch.build.targets.wheel].force-include` in `pyproject.toml`.

- [ ] **Step 3: Run the full local CI gate**

Run: `make ci-local`
Expected: format-check, lint-ci, lint-loc, typecheck-fast, test-fast all PASS. (`resources.py` stays well under the 600-line cap.)

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(guidance): document clingen://guidance resource"
```

---

## Self-Review

**1. Spec coverage:**
- Pointers + `pmid`/`pmcid`/`oa_license`/`fulltext` per recommendation → Task 1 (seed) + Task 2 (verify-all). ✓
- "Verified" guarantee (no unverified ships) → Task 2 `test_no_unverified_entries_ship`. ✓
- Served as an MCP resource → Tasks 3-4 (`get_guidance_resource` + `@mcp.resource`). ✓
- Discoverable → Task 5 (capabilities map auto-fed + instructions mention). ✓
- "Zero curator risk": manifest holds pointers/provenance only, no thresholds/logic; precedence note defers to CSpec → encoded in `note` + license_legend, asserted indirectly by schema (no rule fields). ✓
- Research-use/clinical-safety posture → `research_use_notice` + `unsafe_for_clinical_use` enforced by `test_top_level_shape` / direct-call test. ✓
- Bundling → Task 6 Step 2 wheel check. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". Task 2 Step 3 is a concrete verification procedure (exact URLs + mapping rules), not a placeholder — it is genuine data-gathering work with deterministic outputs. ✓

**3. Type consistency:** `get_guidance_resource()` / `_guidance_manifest()` names match across Tasks 3-4 and tests. JSON keys (`recommendations`, `oa_license`, `fulltext`, `unsafe_for_clinical_use`, `baseline.gn_id`) are identical across the data file, the schema test, the direct-call test, and the resource content test. Resource URI `clingen://guidance` consistent in `_RESOURCES`, the handler, the resolve-list, and the instructions. ✓

## Risks & Notes
- **Manual versioning:** the manifest has no machine-readable upstream to freshness-gate against; when ClinGen bumps the guidance page ("Last Updated"), re-run Task 2's procedure. Out of scope here, but worth a future `refresh --check`-style drift diff.
- **License re-verification:** OA license tags reflect the state verified on 2026-06-12; CC status can change. The `license_legend` + per-entry tags make a periodic re-check cheap.
- **No new dependency:** `importlib.resources` and `functools.lru_cache` are stdlib; nothing added to `uv.lock`.
