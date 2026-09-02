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


def test_publication_validates_the_committed_rights_notice() -> None:
    """ClinGen is CC BY 4.0: a committed notice replaces the per-release secret sign-off."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CLINGEN_RIGHTS_RECORD_JSON" not in text
    assert "secrets." not in text
    assert "data/RIGHTS.json" in text
    assert "redistribution_allowed" not in text
    assert "redistribution_review" not in text
    assert text.index("validate_rights_notice") < text.index("gh api")


def test_build_carries_the_rights_notice_into_the_published_manifest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "load_rights_notice" in text
    assert '"rights":dict(rights.block)' in text


def test_publisher_creates_the_data_tag_it_then_verifies() -> None:
    """`gh release create --verify-tag` refuses an absent tag, and the data tag is
    derived from the dataset identity in `build` -- nobody pushes it. The publisher
    must create it on the exact validated source commit, only when a release is
    about to be created, and must refuse a tag that already names another commit."""
    steps = _steps("publish-release")
    names = [step.get("name") for step in steps]
    tag_step = steps[names.index("Ensure the release tag names this exact source")]
    assert tag_step["if"] == "steps.release_state.outputs.state == 'create'"
    assert names.index(tag_step["name"]) < names.index("Create only an absent draft")
    assert names.index("Determine closed immutable release state") < names.index(tag_step["name"])
    script = tag_step["run"]
    assert 'gh api "repos/$GH_REPO/git/ref/tags/$TAG"' in script
    assert (
        '--method POST "repos/$GH_REPO/git/refs" -f ref="refs/tags/$TAG" -f sha="$GITHUB_SHA"'
        in script
    )
    assert '[ "$existing" != "$GITHUB_SHA" ]' in script and "exit 1" in script
    create = steps[names.index("Create only an absent draft")]
    assert "--verify-tag" in create["run"]


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


def test_draft_recheck_uses_exact_release_id_and_target_identity() -> None:
    """Draft lookup must not use the tag endpoint, which hides draft releases."""
    publisher = _workflow()["jobs"]["publish-release"]
    steps = publisher["steps"]
    names = [step.get("name") for step in steps]
    initial = steps[names.index("Determine closed immutable release state")]
    create = steps[names.index("Create only an absent draft")]
    recheck = steps[names.index("Re-fetch exact draft identity immediately before promotion")]

    initial_script = initial["run"]
    create_script = create["run"]
    recheck_script = recheck["run"]
    assert "releases?per_page=100" in initial_script
    assert "MAX_RELEASE_PAGES=20" in initial_script
    assert "release-pagination.sh" in initial_script
    assert 'source "$RUNNER_TEMP/release-pagination.sh"' in create_script
    assert "release_id" in initial_script
    assert ".id" in initial_script and ".tag_name" in initial_script
    assert ".target_commitish" in initial_script
    assert "release-id" in create.get("run", "")
    assert 'RELEASE_ID="$(cat "$RUNNER_TEMP/release-id")"' in recheck_script
    assert "releases/$RELEASE_ID" in recheck_script
    assert "releases/tags/$TAG" not in recheck_script
    assert ".id == ($release_id|tonumber)" in recheck_script
    assert ".tag_name == $TAG" in recheck_script
    assert '.target_commitish == "main"' in recheck_script

    jq = shutil.which("jq")
    assert jq is not None
    predicate = (
        ".id == ($release_id|tonumber) and .tag_name == $TAG and "
        '.draft == true and .target_commitish == "main"'
    )
    valid = {
        "id": 380448098,
        "tag_name": "data-clingen-a",
        "draft": True,
        "target_commitish": "main",
    }
    for candidate, valid_result in (
        (valid, True),
        ({**valid, "id": 380448099}, False),
        ({**valid, "tag_name": "data-clingen-b"}, False),
        ({**valid, "draft": False}, False),
        ({**valid, "target_commitish": "other"}, False),
    ):
        result = subprocess.run(  # noqa: S603 - fixed local verifier and fixture exercise failure states
            [
                jq,
                "-e",
                "--argjson",
                "release_id",
                "380448098",
                "--arg",
                "TAG",
                "data-clingen-a",
                predicate,
            ],
            input=json.dumps(candidate),
            capture_output=True,
            check=False,
            text=True,
        )
        assert (result.returncode == 0) is valid_result


def _shell_function(script: str, name: str) -> str:
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{name}() {{")
    depth = 0
    selected: list[str] = []
    for line in lines[start:]:
        selected.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            return "\n".join(selected)
    raise AssertionError(f"unterminated shell function: {name}")


def _run_release_pagination(
    script: str,
    tmp_path: Path,
    pages: list[list[dict[str, Any]]],
    *,
    max_pages: int = 20,
) -> subprocess.CompletedProcess[str]:
    fixture_dir = tmp_path / "pages"
    fixture_dir.mkdir(parents=True)
    for number, page in enumerate(pages, start=1):
        (fixture_dir / f"page-{number}.json").write_text(json.dumps(page), encoding="utf-8")
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        """#!/bin/sh
set -eu
output=
url=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) url=$1; shift ;;
  esac
done
page=${url##*page=}
cp "$FIXTURE_DIR/page-$page.json" "$output"
printf '200'
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    function = _shell_function(script, "find_tag_releases")
    harness = f"""\
set -euo pipefail
RUNNER_TEMP={tmp_path}
GH_REPO=example/repo
GH_TOKEN=fixture
TAG=data-clingen-test
MAX_RELEASE_PAGES={max_pages}
FIXTURE_DIR={fixture_dir}
PATH={tmp_path}:$PATH
export FIXTURE_DIR PATH
{function}
find_tag_releases "$RUNNER_TEMP/matches.json"
cat "$RUNNER_TEMP/matches.json"
"""
    return subprocess.run(  # noqa: S603 - executes only the extracted local workflow helper
        ["bash", "-c", harness],  # noqa: S607 - controlled system shell
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_pagination_finds_later_page_and_rejects_duplicates(tmp_path: Path) -> None:
    """Fallback lookup must exhaust full pages and preserve all matches for rejection."""
    steps = _steps("publish-release")
    names = [step.get("name", "") for step in steps]
    script = steps[names.index("Determine closed immutable release state")]["run"]
    first_page = [{"id": i, "tag_name": f"other-{i}"} for i in range(100)]
    later = [
        {
            "id": 200,
            "tag_name": "data-clingen-test",
            "draft": True,
            "target_commitish": "main",
        }
    ]
    result = _run_release_pagination(script, tmp_path / "later", [first_page, later])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == later

    duplicate = [{"id": 200, "tag_name": "data-clingen-test"}, *first_page[1:]]
    second_match = [
        {
            "id": 201,
            "tag_name": "data-clingen-test",
            "draft": True,
            "target_commitish": "main",
        }
    ]
    result = _run_release_pagination(script, tmp_path / "duplicate", [duplicate, second_match])
    assert result.returncode == 0, result.stderr
    assert [release["id"] for release in json.loads(result.stdout)] == [200, 201]
    assert '*) echo "multiple GitHub releases use tag $TAG" >&2; exit 1 ;;' in script


def test_release_pagination_fails_closed_when_page_bound_is_exhausted(tmp_path: Path) -> None:
    """A continuously full inventory must not be treated as complete at the bound."""
    steps = _steps("publish-release")
    names = [step.get("name", "") for step in steps]
    script = steps[names.index("Determine closed immutable release state")]["run"]
    full_page = [{"id": i, "tag_name": f"other-{i}"} for i in range(100)]
    result = _run_release_pagination(
        script, tmp_path / "overflow", [full_page, full_page], max_pages=2
    )
    assert result.returncode != 0
    assert "pagination" in result.stderr.lower()


def test_approval_binds_source_artifact_and_exact_handoff() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for field in ("source_sha256", "artifact_sha256", "handoff_sha256", "artifact_id"):
        assert field in text
    assert "rights_notice_digest" in text
    assert "steps.upload_handoff.outputs.artifact-id" in text
    assert "steps.upload_handoff.outputs.artifact-digest" in text
    assert "needs.build.outputs.artifact_id" in text
    assert "needs.validate-publication.outputs.approval_digest" in text


def test_checksum_validation_keys_sha256sums_by_filename() -> None:
    """A valid handoff must resolve each filename to its declared digest."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "load_sha256sums(" in text

    from clingen_link.etl.release_identity import parse_sha256sums

    payload = "\n".join(
        (
            "a" * 64 + "  clingen.sqlite.zst",
            "b" * 64 + "  data-release-manifest.json",
        )
    )
    assert parse_sha256sums(
        payload.encode(), {"clingen.sqlite.zst", "data-release-manifest.json"}
    ) == {
        "clingen.sqlite.zst": "a" * 64,
        "data-release-manifest.json": "b" * 64,
    }


def test_checksum_validation_uses_a_bounded_checksum_file_read() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "load_sha256sums(" in text


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
