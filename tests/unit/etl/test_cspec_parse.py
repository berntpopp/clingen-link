# tests/unit/etl/test_cspec_parse.py
from clingen_link.etl import cspec_parse

_JSONLD = {
    "@id": "https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/GN164",
    "@type": "Criteria Specification",
    "affiliation": {
        "@id": "https://cspec.genome.network/cspec/api/Organization/id/50140",
        "label": "ABCA4 Variant Curation Expert Panel ",
        "url": "https://clinicalgenome.org/affiliation/50140",
    },
    "label": "ClinGen ABCA4 Expert Panel Specifications ... Version 1.0.0",
    "version": "1.0.0",
    "cspecStatus": "Released",
    "currentStatus": "Pilot Rules In Prep",
    "lastUpdated": "2024-02-06T00:00:00.000Z",
    "ruleSets": [
        {
            "@id": "https://cspec.genome.network/cspec/api/RuleSet/id/777",
            "genes": [
                {
                    "@id": "https://www.genenames.org/tools/search/#!/?query=ABCA4",
                    "diseases": [
                        {
                            "@id": "http://purl.obolibrary.org/obo/MONDO_0800406",
                            "label": "MONDO:0800406",
                        }
                    ],
                    "modeOfInheritance": "Autosomal recessive",
                }
            ],
            "criteriaCodes": [
                {
                    "@id": "https://cspec.genome.network/cspec/api/CriteriaCode/id/538211541",
                    "label": "BS3",
                    "description": "Well-established functional studies show no damaging effect.",
                    "evidenceStrengths": [
                        {
                            "label": "Supporting",
                            "applicability": "Applicable",
                            "description": "See PS3/BS3 spreadsheet below.",
                        },
                        {"label": "Strong", "applicability": "Not Applicable"},
                    ],
                },
            ],
        }
    ],
}

# Real registry markup: every attachment lives in a "Files & Images" panel and is
# preceded by its own authored <span class="file-label"> title. The first file's
# title names a code (BS3 -> attributes); the second is a spec-wide title (no code
# -> spec-level), which also pins consume-once (BS3 must NOT leak onto it).
_DOC_HTML = """
<div class="panel files-panel"><div id="files" class="files">
  <div class="image-file-item"><span class="file-content">
    <span class="file-label">BS3 functional assay spreadsheet</span>
    <span class="file-entry-tools">
      <a href="/cspec/File/id/abc-123/data">spreadsheet</a></span></span></div>
  <div class="image-file-item"><span class="file-content">
    <span class="file-label">General guidance document</span>
    <span class="file-entry-tools">
      <a href="https://cspec.genome.network/cspec/File/id/def-456/data">general</a>
    </span></span></div>
</div></div>
"""
_HEADS = {
    "https://cspec.genome.network/cspec/File/id/abc-123/data": {
        "content-disposition": "attachment; filename=PS3-BS3-list.xlsx",
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-length": "13962",
    },
    "https://cspec.genome.network/cspec/File/id/def-456/data": {
        "content-disposition": "attachment; filename=General.pdf",
        "content-type": "application/octet-stream",
        "content-length": "100",
    },
}


def _file_entry(label: str, file_id: str, *, img: bool = False) -> str:
    """One registry file-panel entry: a file-label title + a download link/image."""
    link = (
        f'<img src="https://cspec.genome.network/cspec/File/id/{file_id}/data">'
        if img
        else f'<a href="/cspec/File/id/{file_id}/data">x</a>'
    )
    return (
        '<div class="image-file-item"><span class="file-content">'
        f'<span class="file-label">{label}</span>'
        f'<span class="file-entry-tools">{link}</span></span></div>'
    )


def test_is_published_uses_cspec_status_not_current_status() -> None:
    assert cspec_parse.is_published(_JSONLD) is True  # Released + 1 criterion
    deleted = {**_JSONLD, "cspecStatus": "CSpec Deleted", "ruleSets": []}
    assert cspec_parse.is_published(deleted) is False
    baseline = {**_JSONLD, "cspecStatus": None, "@id": ".../id/GN001"}
    assert cspec_parse.is_published(baseline) is True  # GN001 baseline allowlisted


def test_parse_spec_structures_rows() -> None:
    parsed = cspec_parse.parse_spec(_JSONLD, _DOC_HTML, _HEADS)
    assert parsed.spec["gn_id"] == "GN164"
    assert parsed.spec["affiliation_id"] == "50140"
    assert parsed.spec["affiliation_label"] == "ABCA4 Variant Curation Expert Panel"
    assert parsed.spec["cspec_status"] == "Released"
    assert [rs["rule_set_id"] for rs in parsed.rule_sets] == ["777"]
    assert parsed.genes[0]["gene_symbol"] == "ABCA4"
    assert parsed.genes[0]["mondo"] == "MONDO:0800406"
    assert parsed.criteria[0]["criteria_id"] == "538211541"
    assert parsed.criteria[0]["code"] == "BS3"
    strengths = {s["strength_label"]: s["applicability"] for s in parsed.strengths}
    assert strengths == {"Supporting": "Applicable", "Strong": "Not Applicable"}
    files = {f["filename"]: f for f in parsed.files}
    assert files["PS3-BS3-list.xlsx"]["size_bytes"] == 13962
    # The file whose own label names BS3 attaches to that criterion; the
    # spec-wide "General guidance document" stays spec-level (BS3 does not leak).
    assert files["PS3-BS3-list.xlsx"]["criteria_id"] == "538211541"
    assert files["General.pdf"]["criteria_id"] is None


def _criteria_code(criteria_id: str, label: str) -> dict[str, object]:
    """Minimal criteriaCode entry (only the keys parse_spec reads)."""
    return {
        "@id": f"https://cspec.genome.network/cspec/api/CriteriaCode/id/{criteria_id}",
        "label": label,
    }


def _rule_set(rule_set_id: str, criteria_codes: list[dict[str, object]]) -> dict[str, object]:
    """Minimal ruleSet entry (only the keys parse_spec reads)."""
    return {
        "@id": f"https://cspec.genome.network/cspec/api/RuleSet/id/{rule_set_id}",
        "criteriaCodes": criteria_codes,
    }


def _spec_jsonld(rule_sets: list[dict[str, object]]) -> dict[str, object]:
    """Minimal spec JSON-LD wrapping the given rule sets."""
    return {
        "@id": "https://cspec.genome.network/cspec/api/SequenceVariantInterpretation/id/GN164",
        "ruleSets": rule_sets,
    }


def test_label_code_attributes_to_its_own_criterion() -> None:
    # A file whose label names exactly one resolvable code binds to that criterion.
    jsonld = _spec_jsonld([_rule_set("777", [_criteria_code("301", "PVS1")])])
    doc_html = _file_entry("PVS1 Decision Tree", "f1")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] == "301"


def test_image_entry_label_attributes() -> None:
    # Image-style entries (<img src=.../File/id/...>) carry the same file-label.
    jsonld = _spec_jsonld([_rule_set("777", [_criteria_code("301", "PVS1")])])
    doc_html = _file_entry("VHL PVS1 Decision Tree", "f1", img=True)
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] == "301"


def test_underscore_delimited_label_attributes() -> None:
    # Filename-style labels delimit the code with underscores/dots, not spaces.
    jsonld = _spec_jsonld([_rule_set("777", [_criteria_code("301", "PS3")])])
    doc_html = _file_entry("GALT_PS3_functionalassay_071224", "f1")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] == "301"


def test_specwide_label_is_spec_level() -> None:
    # A spec-wide title that names no code stays spec-level (the GN092 regression):
    # "Specifications_V1.2", "Appendix", "Supplementary Tables" attach to no criterion.
    jsonld = _spec_jsonld([_rule_set("777", [_criteria_code("301", "BA1")])])
    doc_html = (
        _file_entry("Specifications_V1.2", "f1")
        + _file_entry("Appendix document", "f2")
        + _file_entry("Supplementary Tables", "f3")
    )
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    assert len(parsed.files) == 3
    assert {f["criteria_id"] for f in parsed.files} == {None}


def test_multi_code_label_is_spec_level() -> None:
    # A title that names two distinct codes (a shared doc) is genuinely not tied to
    # one criterion, so it surfaces at spec level rather than picking one.
    jsonld = _spec_jsonld(
        [_rule_set("777", [_criteria_code("301", "PS3"), _criteria_code("302", "BS3")])]
    )
    doc_html = _file_entry("PS3 and BS3 flowchart", "f1")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] is None


def test_label_does_not_leak_to_unlabeled_file() -> None:
    # Consume-once: a code-named file is followed by a generic file. The second must
    # NOT inherit the first's code (the old persistent-cursor leak).
    jsonld = _spec_jsonld([_rule_set("777", [_criteria_code("301", "PM3")])])
    doc_html = _file_entry("PM3 table", "f1") + _file_entry("Functional assay Guidance", "f2")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})
    by_id = {f["file_uuid"]: f["criteria_id"] for f in parsed.files}
    assert by_id == {"f1": "301", "f2": None}


def test_multi_rule_set_ambiguity_collapses_to_spec_level() -> None:
    # Two rule sets each define "PM2" with DIFFERENT criteria_ids -> ambiguous,
    # so a file whose label names PM2 must collapse to spec-level (None).
    jsonld = _spec_jsonld(
        [
            _rule_set("777", [_criteria_code("111", "PM2")]),
            _rule_set("888", [_criteria_code("222", "PM2")]),
        ]
    )
    # NB: file id must be hex (_FILE_RE assumes hex UUIDs); a non-hex id like
    # "xyz-1" would silently not match, so we use a hex id to exercise the
    # ambiguity collapse rather than the drop path.
    doc_html = _file_entry("PM2 table", "abc-1")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})

    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] is None

    pm2 = [c for c in parsed.criteria if c["code"] == "PM2"]
    assert len(pm2) == 2
    assert {c["criteria_id"] for c in pm2} == {"111", "222"}


def test_extract_file_urls_dedups_and_normalizes() -> None:
    # Order: relative aaa, absolute bbb, duplicate aaa. Expect absolute URLs,
    # de-duplicated, in first-occurrence order.
    doc_html = (
        '<a href="/cspec/File/id/aaa/data">1</a>'
        '<a href="https://cspec.genome.network/cspec/File/id/bbb/data">2</a>'
        '<a href="/cspec/File/id/aaa/data">3</a>'
    )
    assert cspec_parse.extract_file_urls(doc_html) == [
        "https://cspec.genome.network/cspec/File/id/aaa/data",
        "https://cspec.genome.network/cspec/File/id/bbb/data",
    ]


def test_prose_tokens_do_not_redirect_attribution() -> None:
    # Attribution keys on the file's OWN label, not document position, so a prose
    # mention of "BS3" before a generically-titled file no longer redirects it.
    # (This pins the FIX for the former Task-12 nearest-token limitation.)
    jsonld = _spec_jsonld(
        [
            _rule_set(
                "777",
                [
                    _criteria_code("301", "PS3"),
                    _criteria_code("302", "BS3"),
                ],
            )
        ]
    )
    doc_html = "<p>note: differs from BS3 below</p>" + _file_entry("Functional assay results", "f1")
    parsed = cspec_parse.parse_spec(jsonld, doc_html, {})

    assert len(parsed.files) == 1
    assert parsed.files[0]["criteria_id"] is None  # spec-level, NOT BS3
