"""The data publisher must keep transformation code away from credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "data-refresh.yml"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict[str, Any]]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"][job]["steps"]


def test_build_and_publish_jobs_are_separated() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == {"build", "publish"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert jobs["publish"]["needs"] == "build"
    assert not any(
        step.get("uses", "").startswith("actions/checkout") for step in _steps("publish")
    )


def test_publisher_is_draft_first_attested_and_never_clobbers() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "release create" in text and "--draft" in text
    assert "actions/attest-build-provenance@" in text
    assert "release verify" in text and "release verify-asset" in text
    assert "--clobber" not in text


def test_publication_requires_affirmative_redistribution_review() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "redistribution_allowed" in text
    assert "redistribution_review" in text
    assert "data-clingen-" in text


def test_all_actions_are_full_sha_pinned() -> None:
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert len(uses.rsplit("@", 1)[1].split()[0]) == 40, uses
