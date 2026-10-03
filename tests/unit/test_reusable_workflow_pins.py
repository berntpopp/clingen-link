"""Guard the reviewed router reusable-workflow revision."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTER_WORKFLOW_SHA = "0122f6e6d8f6a9057b80134d7cacbf61c5bd2e84"  # genefoundry-router v0.9.3


def test_reusable_container_workflows_use_reviewed_router_revision() -> None:
    for workflow in ("container-ci.yml", "container-release.yml"):
        content = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "berntpopp/genefoundry-router/.github/workflows/_container-" in content
        assert f"@{ROUTER_WORKFLOW_SHA}" in content
