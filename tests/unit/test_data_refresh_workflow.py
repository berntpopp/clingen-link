"""The data publisher must keep transformation code away from credentials."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "data-refresh.yml"

HANDOFF_DIR = 'cd "$RUNNER_TEMP/data-release"'

# A handoff filename written as a bare token -- i.e. resolved against the shell's cwd,
# not against a directory it carries with it. The lookbehind excludes any occurrence
# already prefixed with a path (``$RUNNER_TEMP/existing/clingen.sqlite.zst``).
BARE_HANDOFF_FILE = re.compile(
    r"(?<![\w/.$-])(?:clingen\.sqlite\.zst|data-release-manifest\.json|SHA256SUMS)\b"
)


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict[str, Any]]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"][job]["steps"]


def test_build_and_publish_jobs_are_separated() -> None:
    jobs = _workflow()["jobs"]
    assert set(jobs) == {"build", "validate-publication", "publish-release"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["validate-publication"]["permissions"] == {"contents": "read"}
    assert jobs["publish-release"]["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert jobs["publish-release"]["needs"] == ["build", "validate-publication"]
    assert not any(
        step.get("uses", "").startswith("actions/checkout") for step in _steps("publish-release")
    )


def test_publisher_is_draft_first_attested_and_never_clobbers() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "release create" in text and "--draft" in text and "--verify-tag" in text
    assert (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2"
    ) in text
    assert "attestation verify" in text
    assert "release delete" not in text
    assert "release upload" in text
    assert "--clobber" not in text


def test_publication_requires_a_protected_complete_rights_record() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CLINGEN_RIGHTS_RECORD_JSON" in text
    assert "redistribution_allowed" not in text
    assert "redistribution_review" not in text
    assert text.index("validate_rights_record") < text.index("gh api")


def test_publisher_verifies_the_rollback_target_against_the_latest_release() -> None:
    """A build stays credential-free, while publication refuses a stale rollback pin."""
    steps = _steps("publish-release")
    names = [step.get("name", "") for step in steps]
    check = steps[names.index("Verify previous known-good rollback target")]
    script = check["run"]

    assert names.index(check["name"]) < names.index(
        "Create only an absent draft or publish exact existing draft"
    )
    assert "gh api" in script
    assert "data-release-manifest.json" in script
    assert "release-state" in script
    assert "cmp SHA256SUMS" in script
    assert "publish-release" in _workflow()["jobs"]
    assert "release list" not in "\n".join(step.get("run", "") for step in _steps("build"))


def test_publish_steps_naming_handoff_files_run_from_the_handoff_dir() -> None:
    """Every publish step that names a handoff file by bare name must cd to it first.

    Each ``run:`` block starts a fresh shell in the workspace, so a ``cd`` in the
    preceding step does not carry over. The publish job never checks out the source
    and consumes only the downloaded handoff, so a bare filename there resolves to
    nothing unless the block cds itself.

    This is not hypothetical: run 29279142644 flipped the release to public, then
    failed on ``gh release verify-asset`` with "failed to open local artifact: open
    clingen.sqlite.zst: no such file or directory" -- leaving a published release
    behind a red workflow. The structural guards above all passed, because a missing
    cd is invisible to them.

    Scoped to ``publish``: only that job resolves names against the handoff dir. The
    build job writes the same names from Python, where they are paths under ``out``.
    """
    for step in _steps("publish-release"):
        script = step.get("run")
        if not script:
            continue
        offenders = sorted(
            {
                line.strip()
                for line in script.splitlines()
                if BARE_HANDOFF_FILE.search(line.split("#", 1)[0])
            }
        )
        if offenders:
            listing = "\n  ".join(offenders)
            assert HANDOFF_DIR in script, (
                f"publish step {step['name']!r} names handoff files by bare name but "
                f"never cds into the handoff directory, so they resolve against the "
                f"workspace and do not exist:\n  {listing}\n"
                f"Add `{HANDOFF_DIR}` to this step -- a `run:` block does not inherit "
                f"the previous step's cwd."
            )


def test_all_actions_are_full_sha_pinned() -> None:
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert len(uses.rsplit("@", 1)[1].split()[0]) == 40, uses
