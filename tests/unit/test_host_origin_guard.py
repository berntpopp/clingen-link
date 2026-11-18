"""Host/Origin boundary contracts for the unified MCP application."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from packaging.version import Version

from clingen_link.config import Settings, settings
from clingen_link.server_manager import create_unified_app

PUBLIC_HOST = "clingen-link.genefoundry.org"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        settings,
        "ALLOWED_HOSTS",
        ["localhost", "127.0.0.1", "::1", PUBLIC_HOST],
    )
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", [PUBLIC_ORIGIN])
    monkeypatch.setattr(settings, "CORS_ORIGINS", PUBLIC_ORIGIN)
    return TestClient(create_unified_app(), raise_server_exceptions=False)


def test_fastmcp_344_strict_guard_is_installed(client: TestClient) -> None:
    assert Version(version("fastmcp")) >= Version("3.4.4")
    response = client.get("/mcp", headers={"Host": PUBLIC_HOST})
    assert response.status_code not in {403, 421}


def test_default_allowlists_are_loopback_only_and_origin_empty() -> None:
    configured = Settings(_env_file=None)
    assert configured.ALLOWED_HOSTS == ["localhost", "127.0.0.1", "::1"]
    assert configured.ALLOWED_ORIGINS == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_hosts_are_allowed(client: TestClient, host: str) -> None:
    response = client.get("/mcp", headers={"Host": host})
    assert response.status_code not in {403, 421}


@pytest.mark.parametrize("path", ["/health", "/mcp"])
def test_untrusted_host_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"Host": "evil.example"})
    assert response.status_code == 421


def test_absent_origin_is_allowed(client: TestClient) -> None:
    response = client.get("/mcp", headers={"Host": PUBLIC_HOST})
    assert response.status_code not in {403, 421}


def test_public_origin_is_allowed_by_guard_and_cors(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Host": PUBLIC_HOST,
            "Origin": PUBLIC_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code not in {403, 421}
    assert response.headers["access-control-allow-origin"] == PUBLIC_ORIGIN


@pytest.mark.parametrize("path", ["/health", "/mcp"])
def test_untrusted_origin_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    response = client.get(
        path,
        headers={"Host": PUBLIC_HOST, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("wildcard", ["*", "*.example.org", "host?.example.org", "host[0]"])
def test_wildcard_host_is_rejected(wildcard: str) -> None:
    with pytest.raises(ValueError, match="wildcard"):
        Settings(_env_file=None, ALLOWED_HOSTS=[wildcard])


def test_json_environment_allowlists_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CLINGEN_LINK_ALLOWED_HOSTS",
        f'["localhost","{PUBLIC_HOST}"]',
    )
    monkeypatch.setenv("CLINGEN_LINK_ALLOWED_ORIGINS", f'["{PUBLIC_ORIGIN}"]')
    configured = Settings(_env_file=None)
    assert configured.ALLOWED_HOSTS == ["localhost", PUBLIC_HOST]
    assert configured.ALLOWED_ORIGINS == [PUBLIC_ORIGIN]


@pytest.mark.parametrize(
    "compose_file",
    [
        "docker/docker-compose.yml",
        "docker/docker-compose.prod.yml",
        "docker/docker-compose.npm.yml",
    ],
)
def test_compose_propagates_allowlist_and_healthcheck_host(compose_file: str) -> None:
    text = Path(compose_file).read_text()
    assert "CLINGEN_LINK_ALLOWED_HOSTS" in text
    assert '"localhost","127.0.0.1","::1"' in text
    if compose_file == "docker/docker-compose.prod.yml":
        # The prod overlay inherits the base healthcheck and GF_HEALTHCHECK_HOST default.
        return
    # The fleet-canonical healthcheck sends an EXPLICIT Host header, sourced from
    # GF_HEALTHCHECK_HOST. The strict Host guard rejects any request whose Host is outside
    # CLINGEN_LINK_ALLOWED_HOSTS, so a healthcheck with no (or a wrong) Host would 400 and
    # the container would never become healthy. The default must stay inside the allowlist.
    assert "Host: $${GF_HEALTHCHECK_HOST}" in text
    assert "GF_HEALTHCHECK_HOST:-localhost" in text


@pytest.mark.parametrize(
    "compose_file", ["docker/docker-compose.prod.yml", "docker/docker-compose.npm.yml"]
)
def test_public_compose_couples_request_origin_and_cors(compose_file: str) -> None:
    text = Path(compose_file).read_text()
    assert "CLINGEN_LINK_ALLOWED_ORIGINS" in text
    assert "CLINGEN_LINK_CORS_ORIGINS" in text
    assert text.count(f'["{PUBLIC_ORIGIN}"]') >= 2


def test_docker_environment_example_uses_real_settings_prefix() -> None:
    text = Path(".env.docker.example").read_text()
    assert f'CLINGEN_LINK_ALLOWED_ORIGINS=["{PUBLIC_ORIGIN}"]' in text
    assert f'CLINGEN_LINK_CORS_ORIGINS=["{PUBLIC_ORIGIN}"]' in text
    assert "CLINGEN_CORS_ORIGINS=" not in text


_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "clingen-host-test", "version": "1.0.0"},
}


def _modern_post(
    client: TestClient, req_id: int, method: str, params: dict[str, object], name: str | None = None
) -> httpx.Response:
    headers = {
        "Host": PUBLIC_HOST,
        "Origin": PUBLIC_ORIGIN,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "mcp-protocol-version": _MODERN_VERSION,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    body = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": {"_meta": _MODERN_META, **params},
    }
    return client.post("/mcp", headers=headers, json=body)


def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool(client: TestClient) -> None:
    """The 2026-07-28 sessionless era (mcp-method / mcp-name headers) passes the
    Host/Origin guard end to end, and an unknown hostile tool is still fenced."""
    import json

    hostile = "ignore-instructions-secret"
    with client:
        discover = _modern_post(client, 1, "server/discover", {})
        listed = _modern_post(client, 2, "tools/list", {})
        called = _modern_post(
            client,
            3,
            "tools/call",
            {"name": "get_server_capabilities", "arguments": {}},
            name="get_server_capabilities",
        )
        unknown = _modern_post(
            client, 4, "tools/call", {"name": hostile, "arguments": {}}, name=hostile
        )

    assert discover.status_code == 200, discover.text
    assert _MODERN_VERSION in discover.json()["result"]["supportedVersions"]
    assert "mcp-session-id" not in discover.headers
    assert listed.status_code == 200, listed.text
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "get_server_capabilities" in names
    assert called.status_code == 200, called.text
    called_result = called.json()["result"]
    assert called_result.get("isError") is not True, called_result
    assert unknown.status_code == 200, unknown.text
    unknown_result = unknown.json()["result"]
    assert unknown_result["isError"] is True
    assert json.loads(unknown_result["content"][0]["text"]) == unknown_result["structuredContent"]
    assert unknown_result["structuredContent"]["error_code"] == "not_found"
    assert hostile not in unknown.text


_EVIL_METHOD = "evil__IGNORE_ALL_PREVIOUS__method"


def _assert_fixed_method_not_found(response: httpx.Response) -> None:
    assert _EVIL_METHOD not in response.text
    assert "IGNORE_ALL_PREVIOUS" not in response.text
    error = response.json()["error"]
    assert error["code"] == -32601
    assert error.get("data") is None


def test_unknown_jsonrpc_method_is_not_reflected_in_either_era(client: TestClient) -> None:
    """SDK 2 echoes an unknown method in METHOD_NOT_FOUND ``data``; the guard strips it."""
    legacy_headers = {
        "Host": PUBLIC_HOST,
        "Origin": PUBLIC_ORIGIN,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with client:
        init = client.post(
            "/mcp",
            headers=legacy_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "hostile", "version": "0"},
                },
            },
        )
        assert init.status_code == 200, init.text
        if session := init.headers.get("mcp-session-id"):
            legacy_headers["mcp-session-id"] = session
        legacy_headers["mcp-protocol-version"] = "2025-06-18"
        legacy = client.post(
            "/mcp",
            headers=legacy_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": _EVIL_METHOD, "params": {}},
        )
        modern = _modern_post(client, 3, _EVIL_METHOD, {})

    _assert_fixed_method_not_found(legacy)
    _assert_fixed_method_not_found(modern)
