"""Tests for clingen_link.etl.fetch and refresh CLI (respx-mocked httpx)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from clingen_link.config import settings
from clingen_link.etl import fetch, refresh
from clingen_link.exceptions import SourceFetchError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fetch.* happy paths
# ---------------------------------------------------------------------------


@respx.mock
def test_fetch_validity_returns_rows() -> None:
    payload = _load_json("validity_api_small.json")
    respx.get(f"{settings.validity_api_base}/validity").mock(
        return_value=httpx.Response(200, json=payload)
    )
    rows = fetch.fetch_validity()
    assert len(rows) == 5
    assert rows[0]["symbol"] == "AARS1"


@respx.mock
def test_fetch_dosage_captures_etags() -> None:
    base = settings.dosage_ftp_base
    respx.get(f"{base}/ClinGen_gene_curation_list_GRCh38.tsv").mock(
        return_value=httpx.Response(
            200, text=_read("dosage_gene_GRCh38.head.tsv"), headers={"ETag": '"g1"'}
        )
    )
    respx.get(f"{base}/ClinGen_region_curation_list_GRCh38.tsv").mock(
        return_value=httpx.Response(
            200, text=_read("dosage_region_GRCh38.head.tsv"), headers={"ETag": '"r1"'}
        )
    )
    # GRCh37 files are now fetched too (L5: backfill the second coordinate set).
    respx.get(f"{base}/ClinGen_gene_curation_list_GRCh37.tsv").mock(
        return_value=httpx.Response(200, text="GRCh37-gene", headers={"ETag": '"g37"'})
    )
    respx.get(f"{base}/ClinGen_region_curation_list_GRCh37.tsv").mock(
        return_value=httpx.Response(200, text="GRCh37-region", headers={"ETag": '"r37"'})
    )
    bundle = fetch.fetch_dosage()
    assert bundle.gene_tsv.startswith("#ClinGen Gene")
    assert bundle.gene_tsv_grch37 == "GRCh37-gene"
    assert bundle.region_tsv_grch37 == "GRCh37-region"
    # Only the GRCh38 ETags form the canonical freshness signal.
    assert bundle.etags["ClinGen_gene_curation_list_GRCh38.tsv"] == '"g1"'
    assert bundle.etags["ClinGen_region_curation_list_GRCh38.tsv"] == '"r1"'
    assert "ClinGen_gene_curation_list_GRCh37.tsv" not in bundle.etags


@respx.mock
def test_fetch_actionability_returns_list() -> None:
    respx.get(f"{settings.actionability_api_base}/api/summ/brief").mock(
        return_value=httpx.Response(200, json=_load_json("actionability_brief_small.json"))
    )
    out = fetch.fetch_actionability()
    assert len(out) == 5


@respx.mock
def test_fetch_erepo_returns_bundle() -> None:
    base = settings.erepo_api_base
    respx.get(f"{base}/api/summary/classifications/download").mock(
        return_value=httpx.Response(200, text=_read("erepo_bulk.head.tsv"))
    )
    respx.get(f"{base}/api/summary/news/").mock(
        return_value=httpx.Response(200, json=_load_json("erepo_news_sample.json"))
    )
    bundle = fetch.fetch_erepo()
    assert bundle.tsv_text.startswith("Variation")
    assert bundle.news[0]["relatedVersion"] == "2.5.6"


@respx.mock
def test_fetch_affiliates_unwraps_rows() -> None:
    respx.get(f"{settings.validity_api_base}/affiliates").mock(
        return_value=httpx.Response(200, json=_load_json("affiliates_sample.json"))
    )
    rows = fetch.fetch_affiliates()
    assert len(rows) == 59
    assert rows[0]["label"].endswith("Expert Panel")


# ---------------------------------------------------------------------------
# fetch.* error handling
# ---------------------------------------------------------------------------


@respx.mock
def test_fetch_validity_raises_tagged_error_on_500() -> None:
    respx.get(f"{settings.validity_api_base}/validity").mock(return_value=httpx.Response(500))
    with pytest.raises(SourceFetchError) as exc:
        fetch.fetch_validity()
    assert exc.value.source == "validity"


@respx.mock
def test_fetch_actionability_raises_on_transport_error() -> None:
    respx.get(f"{settings.actionability_api_base}/api/summ/brief").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with pytest.raises(SourceFetchError):
        fetch.fetch_actionability()


# ---------------------------------------------------------------------------
# refresh --check staleness
# ---------------------------------------------------------------------------


def _full_sources() -> Any:
    from clingen_link.etl.build import Sources

    return Sources(
        validity_rows=_load_json("validity_api_small.json")["rows"],
        dosage_gene_tsv=_read("dosage_gene_GRCh38.head.tsv"),
        dosage_region_tsv=_read("dosage_region_GRCh38.head.tsv"),
        dosage_etags={"ClinGen_gene_curation_list_GRCh38.tsv": '"abc"'},
        actionability_brief=_load_json("actionability_brief_small.json"),
        erepo_tsv=_read("erepo_bulk.head.tsv"),
        erepo_news=_load_json("erepo_news_sample.json")["data"],
        erepo_summary=_load_json("erepo_summary_sample.json"),
        affiliates=_load_json("affiliates_sample.json")["rows"],
    )


def test_run_check_missing_snapshot_is_stale(tmp_path: Path) -> None:
    out = tmp_path / "absent.sqlite"
    assert refresh.run_check(out) == 1


def test_run_check_up_to_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from clingen_link.etl.build import build_snapshot

    out = tmp_path / "clingen.sqlite"
    sources = _full_sources()
    build_snapshot(out, sources, "2026-06-12T00:00:00+00:00")
    monkeypatch.setattr(refresh, "gather_sources", lambda: (sources, []))
    assert refresh.run_check(out) == 0


def test_run_check_detects_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from clingen_link.etl.build import Sources, build_snapshot

    out = tmp_path / "clingen.sqlite"
    build_snapshot(out, _full_sources(), "2026-06-12T00:00:00+00:00")
    # Live sources with a different validity row set => different hash => stale.
    drifted = _full_sources()
    drifted.validity_rows = drifted.validity_rows[:2]
    assert isinstance(drifted, Sources)
    monkeypatch.setattr(refresh, "gather_sources", lambda: (drifted, []))
    assert refresh.run_check(out) == 1


def test_run_refresh_writes_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "clingen.sqlite"
    monkeypatch.setattr(refresh, "gather_sources", lambda: (_full_sources(), []))
    assert refresh.run_refresh(out) == 0
    assert out.exists()


def test_handle_refresh_dispatch_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    out = tmp_path / "absent.sqlite"
    args = argparse.Namespace(check=True, out=str(out))
    assert refresh.handle_refresh(args) == 1


@respx.mock
def test_gather_sources_collects_all_domains() -> None:
    base_v = settings.validity_api_base
    base_d = settings.dosage_ftp_base
    base_a = settings.actionability_api_base
    base_e = settings.erepo_api_base
    respx.get(f"{base_v}/validity").mock(
        return_value=httpx.Response(200, json=_load_json("validity_api_small.json"))
    )
    respx.get(f"{base_v}/affiliates").mock(
        return_value=httpx.Response(200, json=_load_json("affiliates_sample.json"))
    )
    respx.get(f"{base_d}/ClinGen_gene_curation_list_GRCh38.tsv").mock(
        return_value=httpx.Response(
            200, text=_read("dosage_gene_GRCh38.head.tsv"), headers={"ETag": '"g"'}
        )
    )
    respx.get(f"{base_d}/ClinGen_region_curation_list_GRCh38.tsv").mock(
        return_value=httpx.Response(200, text=_read("dosage_region_GRCh38.head.tsv"))
    )
    respx.get(f"{base_d}/ClinGen_gene_curation_list_GRCh37.tsv").mock(
        return_value=httpx.Response(200, text=_read("dosage_gene_GRCh38.head.tsv"))
    )
    respx.get(f"{base_d}/ClinGen_region_curation_list_GRCh37.tsv").mock(
        return_value=httpx.Response(200, text=_read("dosage_region_GRCh38.head.tsv"))
    )
    respx.get(f"{base_a}/api/summ/brief").mock(
        return_value=httpx.Response(200, json=_load_json("actionability_brief_small.json"))
    )
    respx.get(f"{base_e}/api/summary/classifications/download").mock(
        return_value=httpx.Response(200, text=_read("erepo_bulk.head.tsv"))
    )
    respx.get(f"{base_e}/api/summary/news/").mock(
        return_value=httpx.Response(200, json=_load_json("erepo_news_sample.json"))
    )
    respx.get(f"{base_e}/api/summary/classifications/summary/gene").mock(
        return_value=httpx.Response(200, json=_load_json("erepo_summary_sample.json"))
    )
    respx.get(settings.hgnc_complete_set_url).mock(
        return_value=httpx.Response(
            200,
            text="hgnc_id\tsymbol\tname\talias_symbol\tprev_symbol\nHGNC:20\tAARS1\tAla tRNA\t\t\n",
        )
    )
    sources, failures = refresh.gather_sources()
    assert failures == []
    assert len(sources.validity_rows) == 5
    assert sources.dosage_gene_tsv.startswith("#ClinGen")
    assert len(sources.actionability_brief) == 5
    assert sources.erepo_tsv.startswith("Variation")
    assert len(sources.affiliates) == 59
    assert "ABCA4" in sources.erepo_summary["data"]
    assert sources.hgnc_rows and sources.hgnc_rows[0]["symbol"] == "AARS1"


@respx.mock
def test_gather_sources_records_failures() -> None:
    # Only validity succeeds; the rest 500 and are recorded as failures.
    respx.get(f"{settings.validity_api_base}/validity").mock(
        return_value=httpx.Response(200, json=_load_json("validity_api_small.json"))
    )
    respx.route().mock(return_value=httpx.Response(500))
    sources, failures = refresh.gather_sources()
    assert len(sources.validity_rows) == 5
    assert "dosage" in failures
    assert "erepo" in failures


def test_console_main_check_missing(tmp_path: Path) -> None:
    out = tmp_path / "absent.sqlite"
    assert refresh.main(["--check", "--out", str(out)]) == 1


def test_etl_dunder_main_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from clingen_link.etl import __main__ as etl_main

    monkeypatch.setattr(refresh, "gather_sources", lambda: (_full_sources(), []))
    out = tmp_path / "clingen.sqlite"
    assert etl_main.main(["refresh", "--out", str(out)]) == 0
    assert out.exists()


def test_etl_dunder_main_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    from clingen_link.etl import __main__ as etl_main

    assert etl_main.main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()
