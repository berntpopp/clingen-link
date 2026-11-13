"""Guard the reviewed router reusable-workflow revision."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTER_WORKFLOW_SHA = "2cd86e35373cc7b1bdd8686ed9c69ca584b00ed9"  # genefoundry-router v0.10.17


def test_reusable_container_workflows_use_reviewed_router_revision() -> None:
    for workflow in ("container-ci.yml", "container-release.yml"):
        content = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "berntpopp/genefoundry-router/.github/workflows/_container-" in content
        assert f"@{ROUTER_WORKFLOW_SHA}" in content
