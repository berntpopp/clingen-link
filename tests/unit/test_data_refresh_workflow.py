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
    assert (
        "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32 # v4.1.0"
    ) in text
    assert 'release verify "$TAG"' not in text
    assert text.count("release verify-asset") == 3
    assert "attestation verify" not in text
    assert "--clobber" not in text


def test_publication_requires_affirmative_redistribution_review() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "redistribution_allowed" in text
    assert "redistribution_review" in text
    assert "data-clingen-" in text


def test_publisher_verifies_the_rollback_target_against_the_latest_release() -> None:
    """A build stays credential-free, while publication refuses a stale rollback pin."""
    steps = _steps("publish")
    names = [step["name"] for step in steps]
    check = steps[names.index("Verify previous known-good rollback target")]
    script = check["run"]

    assert names.index(check["name"]) < names.index("Verify handoff and create matching draft")
    assert "release list" in script
    assert "release download" in script
    assert "data-release-manifest.json" in script
    assert "previous_known_good_digest" in script
    assert ".artifact.sha256" in script
    assert '--arg current "$TAG"' in script
    assert "publish" in _workflow()["jobs"]
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
    for step in _steps("publish"):
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
