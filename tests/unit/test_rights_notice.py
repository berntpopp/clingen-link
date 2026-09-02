from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from clingen_link.etl.rights_notice import (
    DEFAULT_RIGHTS_PATH,
    RightsNoticeError,
    load_rights_notice,
    validate_rights_notice,
)

ROOT = Path(__file__).resolve().parents[2]


def _notice() -> dict[str, object]:
    return json.loads((ROOT / "data" / "RIGHTS.json").read_text(encoding="utf-8"))


def test_committed_notice_is_present_and_well_shaped() -> None:
    notice = load_rights_notice(ROOT / "data" / "RIGHTS.json")
    assert notice.digest.startswith("sha256:")
    assert notice.license_name == "Creative Commons Attribution 4.0 International"
    assert notice.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert notice.terms_url == "https://clinicalgenome.org/docs/terms-of-use/"
    assert date.fromisoformat(notice.terms_reviewed_at) <= date.today()
    assert "ClinGen" in notice.attribution


def test_default_path_resolves_to_the_committed_notice() -> None:
    assert DEFAULT_RIGHTS_PATH == ROOT / "data" / "RIGHTS.json"
    assert load_rights_notice().digest == load_rights_notice(DEFAULT_RIGHTS_PATH).digest


def test_notice_digest_is_canonical_and_order_independent() -> None:
    shuffled = dict(reversed(list(_notice().items())))
    assert validate_rights_notice(shuffled).digest == load_rights_notice().digest


@pytest.mark.parametrize(
    "mutator",
    [
        lambda notice: notice.pop("attribution"),
        lambda notice: notice.update(extra=True),
        lambda notice: notice.update(schema_version=2),
        lambda notice: notice.update(terms_url="http://clinicalgenome.org/"),
        lambda notice: notice.update(license={"name": "CC BY 4.0"}),
        lambda notice: notice.update(citation=""),
        lambda notice: notice.update(terms_reviewed_at="not-a-date"),
        lambda notice: notice.update(terms_reviewed_at="2999-01-01"),
    ],
)
def test_rights_notice_fails_closed(mutator) -> None:
    notice = _notice()
    mutator(notice)
    with pytest.raises(RightsNoticeError):
        validate_rights_notice(notice)


def test_missing_notice_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RightsNoticeError):
        load_rights_notice(tmp_path / "absent.json")
    unparsable = tmp_path / "RIGHTS.json"
    unparsable.write_text("{", encoding="utf-8")
    with pytest.raises(RightsNoticeError):
        load_rights_notice(unparsable)
