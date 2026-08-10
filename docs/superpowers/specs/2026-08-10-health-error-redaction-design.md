# ClinGen Link Health Error Redaction Design

> Historical record

## Context

CodeQL alert #2 (`py/stack-trace-exposure`, CWE-209/CWE-497) is a true
caller-visible information leak. The `/health` readiness endpoint currently
copies exception text into its JSON `reason` field when runtime identity
verification fails. Startup snapshot failures are also stored as exception text
and later returned by the same endpoint. Filesystem paths or other internal
details carried by either exception can therefore reach an unauthenticated
caller.

## Chosen approach

Keep the endpoint's existing readiness contract—HTTP 503, `status=degraded`,
`data_available=false`, and a `reason` field—but make the reason a fixed generic
sentence for both startup and runtime verification failures. Preserve the full
exception only in server-side logs so operators retain diagnostic detail without
exposing it over HTTP.

The change is deliberately limited to `clingen_link/server_manager.py` and
focused unit regressions in `tests/unit/test_server_manager.py`. It does not
change successful health responses, data identity validation, service startup,
MCP behavior, scanner policy, or deployment configuration.

## Error handling and tests

Two hostile-vector tests will be written and observed failing before production
code changes:

1. A startup `SnapshotUnavailableError` containing a sentinel path must be
   present in server logs but absent from the health JSON; the response must use
   the fixed generic reason.
2. A runtime `OSError` from identity verification containing a sentinel path
   must be present in server logs but absent from the health JSON; the response
   must use the same fixed generic reason.

The minimal implementation will introduce one module-level fixed readiness
message, log exceptions with traceback context at the point they are caught,
and assign only that constant to caller-visible state or JSON. The focused tests,
full local CI, Compose/build checks, image vulnerability gate, and exact-head
remote CI/CodeQL checks must pass before merge. A patch release will then be
tagged and its exact revision, manifest digest, release, provenance, and SBOM
attestations verified before the root release ledger is updated. No deployment
is part of this work.
