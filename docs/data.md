# Data, freshness & provenance

clingen-link serves an **immutable external SQLite snapshot** built offline from ClinGen's
bulk endpoints, plus a thin live HTTP layer for single-record drill-down. Snapshot building
is never done in the request path. For the pipeline internals see
[`architecture.md`](architecture.md); for the variables named here see
[`configuration.md`](configuration.md).

## Domains and sources

| Domain | Source |
|---|---|
| Gene-Disease Validity | `search.clinicalgenome.org/api/validity` (JSON) |
| Gene Dosage | `ftp.clinicalgenome.org` curation-list TSVs (GRCh38 + GRCh37 gene & region) |
| Clinical Actionability | `actionability.clinicalgenome.org/ac` summary API (+ live SEPIO) |
| Variant Pathogenicity (ERepo) | `erepo.clinicalgenome.org/evrepo` bulk TSV (+ live SEPIO) |
| Criteria Specifications (CSpec) | `cspec.genome.network` paged catalog + per-spec JSON-LD + doc-page scrape |
| Gene nomenclature (ETL support) | HGNC complete-set TSV — full names, aliases, previous symbols |

## The data-bundle contract

The application ships **code-only**. The authoritative snapshot is not baked into the image
and not committed to git: it ships as an immutable, attested GitHub data release built by
this repository's own ETL (e.g. `data-clingen-83dcb565417a23bd`, pinned by digest in
[`container-release.json`](../container-release.json)).

A no-egress init service (`clingen-data-init`, `network_mode: none`) verifies the reviewed
bundle before it is ever served, checking:

1. the exact **compressed** SHA-256 (`CLINGEN_LINK_DATA_BUNDLE_SHA256`),
2. the canonical **expanded-tree** SHA-256 (`CLINGEN_LINK_DATA_EXPANDED_SHA256`),
3. compressed and expanded **size ceilings** (decompression-bomb guard),
4. the **schema version** against the compatible range,

then atomically selects the versioned snapshot into the reference volume. The server mounts
that volume read-only. A mismatch keeps the service **down on purpose** rather than serving
unverified or stale bytes.

The selected version also contains a canonical v1 `data-identity-manifest.json`. It records
the immutable `CLINGEN_LINK_DATA_RELEASE_TAG` and a lexically sorted inventory of the exact
materialized runtime files with byte lengths and SHA-256 hashes. Its canonical JSON SHA-256
is `CLINGEN_LINK_DATA_IDENTITY_DIGEST` and the `data.digest` recorded in
[`container-release.json`](../container-release.json); it is not the compressed archive
digest. The manifest file is flushed and atomically replaced before its directory is synced.
The version-directory key combines the bundle digest with a hash of the release tag, so a
second tag using identical bundle bytes is isolated and cannot rewrite the first version.

Materialize a bundle by hand with:

```bash
uv run clingen-link materialize-data --root /data
```

## Refresh & freshness

```bash
# Report staleness. Fetches only cheap freshness signals, writes nothing,
# exits non-zero if any domain is stale:
uv run clingen-link refresh --check

# Rebuild a raw snapshot from live ClinGen sources:
uv run clingen-link refresh --out /tmp/clingen.sqlite

# Same ETL via the module entry point:
uv run python -m clingen_link.etl refresh --check
```

**Freshness model.** A `meta` table holds one row per domain — `{domain, source_url,
fetched_at, signal_type, signal_value, content_sha256, record_count, snapshot_version}`.
Each domain has a *cheap* change signal, so `--check` costs almost nothing:

- **dosage** — FTP `ETag` / `Last-Modified`.
- **erepo** — pre-checks the `news` feed's top `relatedVersion`.
- **validity** — hashes the canonical JSON rows (max row date).
- **actionability** — hashes `(docId, release, lastUpdated)` tuples.

`refresh --check` compares live signals against the snapshot's `meta` and reports per-domain
`up to date` / `STALE` / `UNKNOWN (source unreachable)`. Provenance is surfaced in
`get_server_capabilities`, in every tool's `_meta`, and in the `clingen://freshness`
resource.

**Release workflow.** The weekly `.github/workflows/data-refresh.yml` Action builds snapshot
assets as a **credential-free workflow artifact**. An explicitly authorized publisher then
verifies the handoff, attests the exact bytes, and publishes a draft-first immutable
`data-clingen-YYYY-MM-DD` release. Deploys pin both the compressed and the canonical
expanded-tree digest, plus the canonical runtime-manifest identity.

**Rights notice.** ClinGen data is CC BY 4.0, so redistribution is gated on honest
attribution rather than a per-release human sign-off. The notice is committed and
versioned in [`data/RIGHTS.json`](../data/RIGHTS.json) — licence name, SPDX id and URL,
attribution and citation strings, the ClinGen terms URL, and `terms_reviewed_at`, the date
those terms were last reviewed. The build step loads it through
`clingen_link/etl/rights_notice.py`, which validates presence and exact shape and fails
closed otherwise, and copies it verbatim into the published, attested
`data-release-manifest.json` under `rights`; the publisher re-validates that the manifest's
notice matches the committed one byte-for-byte and seals its `rights_notice_digest` into the
approval record. No secret is involved: update `data/RIGHTS.json` (and `terms_reviewed_at`)
when ClinGen's terms change, not once per release.

Do **not** hand-edit a snapshot bundle or `tests/fixtures/`.

## Live drill-down

Almost everything is served from the snapshot. Only two paths hit live ClinGen:

- `get_variant_interpretation(refresh=true)` — live ERepo SEPIO (full evidence-code ACMG
  criteria). On upstream failure it **degrades to the snapshot record** (reported via
  `source: "snapshot"` and a `_meta.notice`) rather than failing.
- `get_gene_actionability(include_detail=true)` — the live actionability SEPIO assertion
  document.

## Degraded mode

If the snapshot is missing or unreadable the store raises `SnapshotUnavailableError`, mapped
to the `snapshot_unavailable` error code. The server still starts, and
`get_server_capabilities` / `get_diagnostics` degrade gracefully and tell the operator what
to do.

Readiness binds the resolved version selected at process startup and recomputes its canonical
runtime identity. A corrupt input, unexpected file, manifest error, or configured
tag/digest mismatch returns HTTP 503 `degraded` without a `release_identity` fragment. An
atomic `current` selector change does not mix database connections or change readiness for a
running process; restart the process to adopt and attest the newly selected version.

## Licence & citation

ClinGen data is licensed **CC BY 4.0** (© ClinGen / Clinical Genome Resource). When using
data served by clingen-link, attribute ClinGen and cite the framework paper:

> Strande NT, et al. Evaluating the Clinical Validity of Gene-Disease Associations: An
> Evidence-Based Framework Developed by the Clinical Genome Resource. *Am J Hum Genet.*
> 2017;100(6):895-906. **PMID: 28552198.**

Every record additionally carries a verbatim `recommended_citation` with a stable permalink
— paste it **without paraphrasing**. The framework citation and the licence are also exposed
via the `clingen://citations` resource. Permalink forms per domain are listed in
[`usage.md`](usage.md#citation-contract).
