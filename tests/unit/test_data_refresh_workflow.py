"""data-refresh.yml must target current main and never silently skip the PR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "data-refresh.yml"


def _steps() -> list[dict[str, Any]]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["refresh"]["steps"]


def _pr_step() -> dict[str, Any]:
    return next(s for s in _steps() if "create-pull-request" in s.get("uses", ""))


def test_pr_targets_main_on_fixed_branch() -> None:
    pr = _pr_step()
    assert pr["with"]["base"] == "main"
    assert pr["with"]["branch"] == "data-refresh/snapshot"
    assert pr.get("id") == "cpr"  # outputs must be referenceable


def test_pr_creation_is_verified() -> None:
    # A "branch pushed but no PR opened" run must fail the job, not rot.
    names = [s.get("name", "") for s in _steps()]
    assert any("Verify pull request" in n for n in names), names
