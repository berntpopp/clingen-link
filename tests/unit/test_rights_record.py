from __future__ import annotations

import pytest

from clingen_link.etl.release_identity import sealed_identity
from clingen_link.etl.rights_record import RightsRecordError, validate_rights_record


def _identity():
    return sealed_identity(
        {
            "schema_version": 1,
            "dataset": {
                "source": {
                    "identifier": "ClinGen",
                    "url": "https://clinicalgenome.org/",
                    "sha256": "a" * 64,
                }
            },
            "schema": {"actual": "2"},
            "record_counts": {"x": 1},
            "artifact": {
                "filename": "clingen.sqlite.zst",
                "sha256": "b" * 64,
                "compressed_size": 1,
                "expanded_tree_sha256": "c" * 64,
                "expanded_size": 1,
                "member_count": 1,
            },
        }
    )


def _record() -> dict[str, object]:
    identity = _identity()
    return {
        "schema_version": 1,
        "decision": "affirmative",
        "decision_at": "2026-08-30T12:00:00Z",
        "terms_reviewed_at": "2026-08-30",
        "permitted_use": "immutable ClinGen reference snapshot redistribution",
        "attribution": "ClinGen data © ClinGen",
        "evidence_uri": "https://example.org/rights/42",
        "reviewer": "rights-reviewer",
        "authority": "ClinGen rights authority",
        "tag": identity.tag,
        "source_sha256": identity.source_sha256,
        "artifact_sha256": identity.artifact_sha256,
    }


def test_affirmative_record_must_bind_every_sealed_identity_field() -> None:
    record = _record()
    assert validate_rights_record(record, _identity()).digest.startswith("sha256:")


@pytest.mark.parametrize(
    "mutator",
    [
        lambda record: record.update(decision="yes"),
        lambda record: record.update(terms_reviewed_at="2020-01-01"),
        lambda record: record.update(artifact_sha256="d" * 64),
        lambda record: record.update(evidence_uri=""),
        lambda record: record.update(extra=True),
        lambda record: record.update(reviewer=1),
    ],
)
def test_rights_record_fails_closed(mutator) -> None:
    record = _record()
    mutator(record)
    with pytest.raises(RightsRecordError):
        validate_rights_record(record, _identity())
