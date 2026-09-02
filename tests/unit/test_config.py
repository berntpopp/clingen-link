"""Tests for clingen_link.config.Settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clingen_link.config import ServerConfig, Settings, settings

_DATA_RELEASE_TAG = "data-clingen-83dcb565417a23bd"
_DATA_IDENTITY_DIGEST = "sha256:38ff5ebb9b299f313ff23fa8def48ca5a97284c5fea85db21fe3ae06734a2c5b"


def test_defaults_present() -> None:
    """The default Settings carry the documented ClinGen endpoints and knobs."""
    s = Settings()
    assert s.validity_api_base.startswith("https://")
    assert s.dosage_ftp_base.startswith("https://")
    assert s.actionability_api_base.startswith("https://")
    assert s.erepo_api_base.startswith("https://")
    assert s.snapshot_path == "/data/current/clingen.sqlite"
    assert s.data_root == "/data"
    assert s.data_release_tag == _DATA_RELEASE_TAG
    assert s.data_identity_digest == _DATA_IDENTITY_DIGEST
    assert s.max_concurrency == 5
    assert s.request_timeout_s == 30
    assert s.cache_size == 512
    assert s.cache_ttl_minutes == 60
    assert s.erepo_cache_ttl_minutes == 720


def test_external_data_contract_requires_exact_identity(tmp_path) -> None:
    s = Settings(data_bundle_path=str(tmp_path / "bundle.zst"))
    try:
        s.data_requirement()
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("missing bundle digests must fail closed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_release_tag", "latest"),
        ("data_release_tag", "bad tag"),
        ("data_identity_digest", "9b8ef209"),
        ("data_identity_digest", "sha256:" + "A" * 64),
    ],
)
def test_runtime_data_identity_settings_are_strict(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_env_prefix_override(monkeypatch) -> None:
    """Settings load from the CLINGEN_LINK_ prefixed environment."""
    monkeypatch.setenv("CLINGEN_LINK_MAX_CONCURRENCY", "11")
    monkeypatch.setenv("CLINGEN_LINK_CACHE_SIZE", "999")
    monkeypatch.setenv("CLINGEN_LINK_VALIDITY_API_BASE", "https://example.test/api")
    s = Settings()
    assert s.max_concurrency == 11
    assert s.cache_size == 999
    assert s.validity_api_base == "https://example.test/api"


def test_mcp_path_normalized() -> None:
    """A path missing the leading slash is normalized."""
    s = Settings(MCP_PATH="mcp")
    assert s.MCP_PATH == "/mcp"


def test_cors_origins_list() -> None:
    """The wildcard and comma-separated CORS forms both parse."""
    assert Settings(CORS_ORIGINS="*").cors_origins_list == ["*"]
    parsed = Settings(CORS_ORIGINS="https://a.test, https://b.test").cors_origins_list
    assert parsed == ["https://a.test", "https://b.test"]


def test_server_config_from_env() -> None:
    """ServerConfig.from_env reflects the module-level settings singleton."""
    config = ServerConfig.from_env()
    assert config.transport == settings.MCP_TRANSPORT
    assert config.host == settings.MCP_HOST
    assert config.port == settings.MCP_PORT
