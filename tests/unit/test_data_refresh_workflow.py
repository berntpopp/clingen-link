"""The data publisher must keep transformation code away from credentials."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
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


def test_publisher_derives_a_closed_state_before_any_mutation() -> None:
    """A build stays credential-free, while publication derives closed release states."""
    steps = _steps("publish-release")
    names = [step.get("name", "") for step in steps]
    check = steps[names.index("Determine closed immutable release state")]
    script = check["run"]

    assert names.index(check["name"]) < names.index("Create only an absent draft")
    assert "curl" in script
    assert "published_noop" in script and "collision" in script
    assert "release-state" in script
    assert "curl" in script and "404" in script
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


def test_publisher_refetches_every_asset_and_attestation_before_the_only_promotion() -> None:
    workflow = _workflow()
    publisher = workflow["jobs"]["publish-release"]
    assert publisher["timeout-minutes"] == 20
    names = [step.get("name", "") for step in publisher["steps"]]
    recheck = names.index("Re-fetch exact draft identity immediately before promotion")
    promote = names.index("Promote only the exact rechecked draft")
    assert recheck < promote
    script = publisher["steps"][recheck]["run"]
    assert "clingen.sqlite.zst" in script
    assert ".digest" in script and ".size" in script
    assert 'gh attestation verify "$remote/$asset"' in script
    assert (
        publisher["steps"][recheck].get("if")
        == "steps.release_state.outputs.state != 'published_noop'"
    )
    assert "|| true" not in script and "grep -q '404'" not in script


def test_approval_binds_source_artifact_and_exact_handoff() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for field in ("source_sha256", "artifact_sha256", "handoff_sha256", "artifact_id"):
        assert field in text
    assert "CLINGEN_RIGHTS_RECORD_JSON" in text
    assert "steps.upload_handoff.outputs.artifact-id" in text
    assert "steps.upload_handoff.outputs.artifact-digest" in text
    assert "needs.build.outputs.artifact_id" in text
    assert "needs.validate-publication.outputs.approval_digest" in text


def test_published_release_is_rechecked_before_only_noop() -> None:
    publisher = _workflow()["jobs"]["publish-release"]
    steps = publisher["steps"]
    names = [step.get("name") for step in steps]
    initial = steps[names.index("Determine closed immutable release state")]
    recheck = steps[names.index("Re-fetch exact draft identity immediately before promotion")]
    assert "published_noop" in initial["run"]
    assert "verify_remote_release" in initial["run"]
    assert recheck["if"] == "steps.release_state.outputs.state != 'published_noop'"
    assert (
        'jq -er \'.draft | if type == "boolean" then . else error("draft must be boolean") end\''
        in recheck["run"]
    )


@pytest.mark.parametrize(
    ("draft", "valid"),
    [(True, True), ("true", False), (None, False), (1, False), ({}, False), ("missing", False)],
)
def test_final_draft_recheck_accepts_only_a_literal_json_boolean(
    draft: object, valid: bool
) -> None:
    """The exact workflow jq contract rejects truthy textual or missing values."""
    publisher = _workflow()["jobs"]["publish-release"]
    recheck = next(
        step
        for step in publisher["steps"]
        if step.get("name") == "Re-fetch exact draft identity immediately before promotion"
    )
    strict_filter = '.draft | if type == "boolean" then . else error("draft must be boolean") end'
    assert f"jq -er '{strict_filter}'" in recheck["run"]
    payload = {} if draft == "missing" else {"draft": draft}
    jq = shutil.which("jq")
    assert jq is not None
    result = subprocess.run(  # noqa: S603 - fixed local verifier and fixture exercise failure states
        [jq, "-er", strict_filter],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        text=True,
    )
    assert (result.returncode == 0 and result.stdout.strip() == "true") is valid


def test_every_existing_release_path_rejects_unsafe_asset_ids_and_binds_provenance() -> None:
    publisher = _workflow()["jobs"]["publish-release"]
    steps = publisher["steps"]
    relevant = [
        step
        for step in steps
        if step.get("name")
        in {
            "Determine closed immutable release state",
            "Re-fetch exact draft identity immediately before promotion",
        }
    ]
    assert len(relevant) == 2
    for step in relevant:
        script = step["run"]
        assert "unique | length == 3" in script
        assert '(.id | type) == "number"' in script
        assert ".id | floor" in script
        assert ".id > 0" in script
        assert "repos/$GH_REPO/releases/assets/$id" in script
        assert 'gh attestation verify "$remote/$asset"' in script
        assert "timeout 120s gh attestation verify" in script
        assert (
            '--signer-workflow "berntpopp/clingen-link/.github/workflows/data-refresh.yml"'
            in script
        )
        assert '--source-ref "refs/heads/main"' in script
        assert '--source-digest "$SOURCE_DIGEST"' in script


def test_protected_main_only_jobs_bind_the_build_revision_and_attest_every_asset() -> None:
    workflow = _workflow()
    assert "github.ref == 'refs/heads/main'" in workflow["jobs"]["validate-publication"]["if"]
    assert "github.ref_protected" in workflow["jobs"]["validate-publication"]["if"]
    assert "github.ref == 'refs/heads/main'" in workflow["jobs"]["publish-release"]["if"]
    assert "github.ref_protected" in workflow["jobs"]["publish-release"]["if"]
    assert workflow["jobs"]["build"]["outputs"]["source_digest"] == "${{ github.sha }}"
    assert "BUILD_SOURCE_DIGEST" in WORKFLOW.read_text(encoding="utf-8")
    attest = next(
        step
        for step in workflow["jobs"]["publish-release"]["steps"]
        if step.get("uses", "").startswith("actions/attest-build-provenance@")
    )
    assert "SHA256SUMS" in attest["with"]["subject-path"]
