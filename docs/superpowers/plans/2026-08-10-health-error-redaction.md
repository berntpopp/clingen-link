# ClinGen Link Health Error Redaction Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close CodeQL alert #2 by preventing exception details from reaching the public health response while retaining operator diagnostics in server logs, then publish and admit a verified v4.0.5 image.

**Architecture:** Both readiness failure paths use one fixed module-level caller message. Each exception is logged at its catch boundary with traceback context, while health JSON receives only the fixed message; successful readiness behavior is unchanged.

**Tech Stack:** Python 3.12+, FastAPI, structlog/stdlib logging, pytest, uv, Docker Compose, Trivy, GitHub Actions, GHCR.

## Global Constraints

- Preserve HTTP 503, `status=degraded`, `data_available=false`, and the `reason` field.
- Never copy exception text, paths, tracebacks, or other internal details into caller-visible JSON.
- Preserve exception details only in server-side logs.
- Do not change MCP behavior, data identity verification, scanner policy, tags other than the new patch release, fleet.lock, or any deployment.
- Release tag is `v4.0.5`; the tag must identify the exact reviewed merge commit.

---

### Task 1: Add hostile readiness regressions and redact both public paths

**Files:**
- Modify: `tests/unit/test_server_manager.py`
- Modify: `clingen_link/server_manager.py`

**Interfaces:**
- Consumes: `UnifiedServerManager._build_fastapi_app`, `SnapshotUnavailableError`, and `verify_runtime_identity`.
- Produces: module constant `PUBLIC_DATA_UNAVAILABLE_REASON: str` and unchanged health response schema with fixed public text.

- [ ] **Step 1: Write the failing startup regression**

Add an async test that replaces `manager._create_services` with a function raising `SnapshotUnavailableError("snapshot missing at /srv/private/sentinel.sqlite")`, enters the real FastAPI lifespan under `caplog`, invokes the real health endpoint, and asserts:

```python
assert body["reason"] == "ClinGen reference data is unavailable."
assert "/srv/private/sentinel.sqlite" not in response.body.decode()
assert "/srv/private/sentinel.sqlite" in caplog.text
```

- [ ] **Step 2: Write the failing runtime regression**

Using `injected_services`, monkeypatch the module-level `verify_runtime_identity` to raise `OSError("read failed at /srv/private/sentinel.sqlite")`, invoke the real health endpoint under `caplog`, and assert the same fixed public reason, sentinel absence from the response, and sentinel presence in server logs.

- [ ] **Step 3: Verify RED for the intended reason**

Run:

```bash
uv run pytest \
  tests/unit/test_server_manager.py::test_health_redacts_startup_snapshot_exception \
  tests/unit/test_server_manager.py::test_health_redacts_runtime_identity_exception -q
```

Expected: both tests fail because the response still contains the hostile exception text; they must not fail from fixture or import errors.

- [ ] **Step 4: Implement the minimal redaction**

Add the fixed constant near the imports:

```python
PUBLIC_DATA_UNAVAILABLE_REASON = "ClinGen reference data is unavailable."
```

In the startup `SnapshotUnavailableError` handler, call `self.logger.exception("ClinGen reference snapshot unavailable")` while the exception is active, and assign only `PUBLIC_DATA_UNAVAILABLE_REASON` to `app.state.clingen_data_error`.

In the runtime `(OSError, RuntimeDataIdentityError)` handler, call `self.logger.exception("ClinGen runtime data identity verification failed")` and assign only `PUBLIC_DATA_UNAVAILABLE_REASON` to `result["reason"]`.

- [ ] **Step 5: Verify GREEN and full local gates**

Run:

```bash
uv run pytest \
  tests/unit/test_server_manager.py::test_health_redacts_startup_snapshot_exception \
  tests/unit/test_server_manager.py::test_health_redacts_runtime_identity_exception -q
make ci-local
make docker-prod-config
make docker-npm-config
git diff --check
```

Expected: focused tests pass; complete suite and configuration gates pass with no new warning or whitespace failure.

- [ ] **Step 6: Commit the security fix**

```bash
git add clingen_link/server_manager.py tests/unit/test_server_manager.py
git commit -m "fix: redact readiness exception details"
```

### Task 2: Prepare the v4.0.5 release identity

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `CITATION.cff`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the verified security fix from Task 1.
- Produces: a single-source installed version `4.0.5` and unused release tag `v4.0.5`.

- [ ] **Step 1: Confirm the tag is unused**

Run `git ls-remote --exit-code --tags origin refs/tags/v4.0.5`; expected exit status is 2 with no matching ref.

- [ ] **Step 2: Update release metadata**

Set the project version to `4.0.5`, regenerate `uv.lock` with `uv lock`, set `CITATION.cff` version to `4.0.5` and date to `2026-08-10`, and add:

```markdown
## [4.0.5] - 2026-08-10

### Security

- Redacted exception details from degraded `/health` responses while retaining
  complete diagnostic context in server-only logs, closing CWE-209/CWE-497.
```

- [ ] **Step 3: Verify release identity and repeat full gates**

Run:

```bash
uv lock --check
uv run python -c 'import clingen_link; assert clingen_link.__version__ == "4.0.5"'
make ci-local
make docker-prod-config
make docker-npm-config
git diff --check
```

- [ ] **Step 4: Commit release metadata**

```bash
git add pyproject.toml uv.lock CITATION.cff CHANGELOG.md
git commit -m "chore: prepare v4.0.5"
```

### Task 3: Publish and merge the exact reviewed candidate

**Files:**
- No additional repository-file changes.

**Interfaces:**
- Consumes: Tasks 1–2 commits.
- Produces: a squash merge on `main` whose exact candidate head passed all remote checks including CodeQL and container security.

- [ ] **Step 1: Push and open a draft PR**

Push `codex/clingen-health-error-redaction` and open a draft PR describing the true CodeQL flow, hostile red/green proof, fixed caller contract, server-only logging, v4.0.5 identity, and no-deployment scope.

- [ ] **Step 2: Require exact-head checks**

Require every CI, Compose/build, conformance, dependency review, CodeQL, and container scan/SBOM check on the exact PR head to finish `SUCCESS`. Inspect logs and reproduce any failure before modifying code.

- [ ] **Step 3: Mark ready and SHA-guarded squash merge**

Re-read the PR head OID and merge state, mark ready, and use a squash merge guarded by that exact head. Record the merge commit and confirm CodeQL alert #2 becomes fixed on `main`; do not dismiss it.

### Task 4: Publish and verify v4.0.5

**Files:**
- No repository-file changes.

**Interfaces:**
- Consumes: exact reviewed merge commit from Task 3 and existing tag/release policy.
- Produces: signed annotated tag `v4.0.5`, published GHCR manifest, GitHub release, provenance attestation, and SPDX attestation bound to the merge revision.

- [ ] **Step 1: Require exact-main checks and create the tag**

Wait for all checks on the exact merge commit to succeed, verify `pyproject.toml`, installed metadata, and `v4.0.5` identity, then create and push a signed annotated `v4.0.5` tag at that exact commit.

- [ ] **Step 2: Approve only the expected release environment**

Identify the exact tag-triggered container-release run. If pending, require exactly one `release` environment with `current_user_can_approve=true`, approve it with a release-specific comment, and wait for success.

- [ ] **Step 3: Verify immutable publication evidence**

Resolve `ghcr.io/berntpopp/clingen-link:4.0.5` with checksum-pinned ORAS, require its OCI revision label to equal the tagged merge commit, require the GitHub release to be published/non-draft/non-prerelease, and verify both provenance and SPDX attestations against the published digest and source tag/revision. Run a fresh fail-closed scan of that exact digest.

### Task 5: Admit complete Batch A release coordinates

**Files:**
- Modify: root `config/release-ledger.yaml`
- Must not modify: root `config/fleet.lock.yaml`

**Interfaces:**
- Consumes: all ten verified Batch A published tag/revision/digest mappings, zero open Dependabot PRs/alerts, fixed ClinGen CodeQL #2, and HGNC cleanup PR #52.
- Produces: ten `disposition: release` rows with exact coordinates and fully resolved audited items.

- [ ] **Step 1: Re-query every admission condition**

For all ten repositories, require zero open Dependabot PRs and alerts, published release state, exact tag peel, exact registry digest and revision label, and successful release workflows. Require ClinGen CodeQL #2 state `fixed` on the v4.0.5 revision. Require HGNC one-shot workflow and test absent from current main.

- [ ] **Step 2: Update only the ten ledger rows**

Set each Batch A row to `disposition: release`, replace version/source/digest/compose coordinates with the verified release values, and record only resolved alerts and merged successful PRs with item disposition `release`. Leave all non-Batch-A rows byte-for-byte unchanged and do not touch `config/fleet.lock.yaml`.

- [ ] **Step 3: Run the root ledger gate and commit**

Run:

```bash
uv run python scripts/manage.py ledger-check --ledger config/release-ledger.yaml
git diff --check
git diff --exit-code -- config/fleet.lock.yaml
```

Review the scoped diff, then commit only `config/release-ledger.yaml` as `chore: admit verified Batch A releases`.

- [ ] **Step 4: Return final evidence**

Report all ten tag/revision/digest/release mappings, exact security/cleanup PRs and merges, release run and attestation results, zero-open re-query, ledger-check result, ledger commit, and explicit no-deployment confirmation.
