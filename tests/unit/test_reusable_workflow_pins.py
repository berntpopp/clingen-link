"""Guard the reviewed router reusable-workflow revision."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTER_WORKFLOW_SHA = "d3e029603806428dbd5ea6843383e85054a66e3b"


def test_reusable_container_workflows_use_reviewed_router_revision() -> None:
    for workflow in ("container-ci.yml", "container-release.yml"):
        content = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "berntpopp/genefoundry-router/.github/workflows/_container-" in content
        assert f"@{ROUTER_WORKFLOW_SHA}" in content
