# clingen-link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `clingen-link`, an MCP server grounding gene/disease/variant questions in ClinGen's four curated datasets (gene-disease validity, gene dosage, clinical actionability, variant pathogenicity/ERepo), via a self-contained SQLite snapshot + live drill-down.

**Architecture:** Hand-authored FastMCP v3 facade (gnomad-link house style). Offline ETL builds a SQLite snapshot from ClinGen bulk endpoints; a read-only store + cached services back ~13 MCP tools; a thin httpx layer does live single-record drill-down (ERepo SEPIO, actionability SEPIO). Full canonical response envelope (`success`/`error_code`/`_meta.next_commands`/`recommended_citation`).

**Tech Stack:** Python ≥3.12, uv, hatchling, FastMCP v3 (`fastmcp>=3.2,<4`, `mcp[cli]>=1.27`), httpx, pydantic v2 + pydantic-settings, sqlite3 (stdlib) + FTS5, zstandard, structlog, typer, ruff, mypy strict, pytest + respx, coverage ≥80, 600-LOC/module cap.

**Spec:** `docs/superpowers/specs/2026-06-12-clingen-link-design.md`. **Research:** `.research/{api-findings,clingen-data-sources,siblings,design-notes}.md`. **Real sample data already captured:** `.research/samples/{validity,dosage,actionability_brief,erepo_summary,erepo_news,affiliates}.json`. **Template to copy from:** `/home/bernt-popp/development/gnomad-link`.

Conventions for every task: write the test first, run it red, implement minimally, run it green, commit. `make ci-local` (format-check, lint, lint-loc, typecheck-fast, test-fast) is the completion gate. Keep every module < 600 LOC. Commit messages end with the Co-Authored-By trailer.

---

## Phase 1 — Scaffold (foundation everything depends on)

### Task 1.1: Git + package skeleton
**Files:** Create `clingen_link/__init__.py` (`__version__ = "0.1.0"`), `.python-version` (`3.12`), `.gitignore` (copy gnomad-link's), `LICENSE` (MIT, copy + update holder/year), `.gitattributes`, `.editorconfig`.
- [ ] `git init`; copy `.gitignore`/`.gitattributes`/`.editorconfig`/`LICENSE` from gnomad-link, update LICENSE holder to "Bernt Popp", year 2026.
- [ ] Create `clingen_link/__init__.py` with `__version__`.
- [ ] Commit `chore: initialize clingen-link package skeleton`.

### Task 1.2: pyproject.toml + uv env
**Files:** Create `pyproject.toml`, `Makefile`, `scripts/check_file_size.py`, `.loc-allowlist` (empty), `.pre-commit-config.yaml`.
- [ ] Copy gnomad-link `pyproject.toml`; rename project→`clingen-link`, package→`clingen_link`, scripts→`clingen-link="server:main"` + `clingen-link-mcp="mcp_server:main"`; wheel `packages=["clingen_link"]`, `include=["server.py","mcp_server.py"]`.
- [ ] Deps: drop `gql[aiohttp]`/`async-lru`-graphql specifics; keep `mcp[cli]>=1.27,<2`, `fastmcp>=3.2,<4`, `fastapi`, `uvicorn[standard]`, `pydantic>=2.11`, `pydantic-settings`, `httpx>=0.28`, `structlog`, `orjson`, `rich`, `typer`, `gunicorn`, `asgi-correlation-id`, `prometheus-client`, `async-lru`, `zstandard`.
- [ ] Set `[tool.mypy] strict = true` (+ ignore_missing_imports overrides for `mcp.*`,`fastmcp.*`,`fastapi.*`,`pydantic.*`,`structlog.*`,`zstandard.*`). Copy ruff/pytest/coverage blocks; `coverage fail_under=80`, source `["clingen_link"]`.
- [ ] Copy `scripts/check_file_size.py`; set `DEFAULT_TARGETS=["clingen_link"]`. Copy `Makefile`; rename paths/targets (`clingen_link`, `server.py`, `mcp_server.py`); keep `ci-local` composition incl. `lint-loc`. Copy `.pre-commit-config.yaml`; repoint mypy + file-size hooks to `clingen_link`.
- [ ] `uv sync --group dev`; run `make format lint typecheck` (expect green on empty package).
- [ ] Commit `chore: add pyproject, Makefile, lint/type/test tooling`.

### Task 1.3: config + logging + exceptions (copy/adapt)
**Files:** Create `clingen_link/config.py`, `clingen_link/logging_config.py`, `clingen_link/exceptions.py`. **Test:** `tests/unit/test_config.py`.
- [ ] Copy gnomad-link `logging_config.py` near-verbatim (transport-aware; stdio→stderr; banner/color suppression). Copy `exceptions.py` (`ConfigurationError`/`StartupError`/`MCPIntegrationError`).
- [ ] Write `config.py`: `Settings(BaseSettings)` with `env_prefix="CLINGEN_LINK_"`, fields: `host`, `port`, `log_level`, `request_timeout_s=30`, `max_concurrency=5`, `cache_size=512`, `cache_ttl_minutes=60`, `erepo_api_base`, `actionability_api_base`, `validity_api_base`, `ftp_base`, `snapshot_path` (default bundled), `erepo_cache_ttl_minutes=720`. Module-level `settings = Settings()` + `ServerConfig` dataclass for transport.
- [ ] Test: settings load from env with prefix; defaults present. Run red→green.
- [ ] Commit `feat: add config, logging, exceptions`.

### Task 1.4: MCP envelope infra (copy near-verbatim)
**Files:** Create `clingen_link/mcp/{__init__.py,errors.py,next_commands.py,annotations.py,schema_relax.py,output_validation.py,patterns.py}`. **Test:** `tests/unit/test_errors.py`, `tests/unit/test_schema_relax.py`.
- [ ] Copy `annotations.py` (READ_ONLY_OPEN_WORLD), `schema_relax.py`, `output_validation.py` verbatim.
- [ ] Copy `errors.py`; re-map `_classify()` to ClinGen exception types (added in 1.3/2.x), set error codes per spec §4 (add `snapshot_unavailable`), adjust `_fallback_for()` to ClinGen tools (`search_genes`, `get_clingen_diagnostics`). Keep `run_mcp_tool`, `McpErrorContext`, the success/error envelope, `unsafe_for_clinical_use:true`.
- [ ] Copy `next_commands.py` `cmd()`; write builders `for_gene(symbol)`, `for_disease(mondo)`, `for_variant(caid)`.
- [ ] Write `patterns.py`: `GENE_SYMBOL_PATTERN`, `HGNC_ID_PATTERN=r"^HGNC:\d+$"`, `MONDO_PATTERN=r"^MONDO:\d+$"`, `CAID_PATTERN=r"^CA(R:)?\d+$"`, `HGVS_PATTERN`, `DOC_ID_PATTERN=r"^AC\d+$"`, `CGGV_PATTERN`.
- [ ] Tests: `run_mcp_tool` wraps success → adds `success`+`_meta`; a raised typed error → error envelope with right `error_code`/`retryable`; `relax_output_schema` strips `required` + sets `additionalProperties`.
- [ ] Commit `feat: add MCP response envelope + error taxonomy`.

### Task 1.5: transports + entrypoints + hello facade
**Files:** Create `clingen_link/server_manager.py`, `server.py`, `mcp_server.py`, `clingen_link/mcp/facade.py`, `clingen_link/mcp/service_adapters.py`, `clingen_link/mcp/tools/{__init__.py,metadata.py,diagnostics.py}`. **Test:** `tests/unit/test_mcp_smoke.py`, `tests/conftest.py`.
- [ ] Copy `server_manager.py` (UnifiedServerManager: `start_{unified,http,stdio}_server`, `create_app()`), `server.py` (argparse `--transport`), `mcp_server.py` (stdio + env suppression). Rename imports to `clingen_link`.
- [ ] Write `facade.py`: `create_clingen_mcp(service_factory=None)` → `FastMCP(name="clingen-link", instructions=_INSTRUCTIONS, mask_error_details=True)`, then `register_clingen_tools(mcp, service_factory=...)`. `_INSTRUCTIONS` = canonical workflow + next_commands contract + research-use notice.
- [ ] Write `service_adapters.py`: lazy `@lru_cache(maxsize=1)` `get_services()` + test-injection `set_services()`.
- [ ] Write `tools/__init__.py` `register_clingen_tools(mcp, *, service_factory)` calling `register_metadata_tools` + `register_diagnostics_tools`. Write `metadata.py` `get_server_capabilities` (stub: server, version, tools list, resources, research-use) + register `clingen://capabilities` resource. Write `diagnostics.py` `get_clingen_diagnostics` (recent-errors deque + snapshot freshness stub).
- [ ] Test: build mcp via facade; `await mcp.call_tool("get_server_capabilities",{})` returns dict with `success`; tool list non-empty; conftest provides `mcp` fixture + resets `service_adapters` singleton.
- [ ] `make ci-local` green. Commit `feat: add transports, entrypoints, capabilities/diagnostics scaffolding`.

---

## Phase 2 — ETL + SQLite snapshot

### Task 2.1: snapshot schema + builder
**Files:** Create `clingen_link/etl/__init__.py`, `clingen_link/etl/schema.py` (DDL), `clingen_link/etl/build.py`. **Test:** `tests/unit/test_etl_schema.py`.
- [ ] `schema.py`: SQL DDL strings for tables `gene, gene_alias, validity, dosage, actionability, erepo, expert_panel, meta` + FTS5 virtual tables (spec §3). Provide `create_schema(conn)`.
- [ ] `build.py`: `build_snapshot(out_path, *, fetch=...)` orchestrator: open temp DB, `create_schema`, write each domain via writer fns, write `meta` rows, `PRAGMA optimize`, atomically `os.replace` temp→out. `open_readonly(path)` helper (mode=ro, immutable).
- [ ] Test: `create_schema` then introspect `sqlite_master` → all tables/fts present; insert+select a `meta` row.
- [ ] Commit `feat: add SQLite snapshot schema + builder skeleton`.

### Task 2.2: source fetchers + freshness signals
**Files:** Create `clingen_link/etl/fetch.py`, `clingen_link/etl/freshness.py`. **Test:** `tests/unit/test_freshness.py` (respx + fixtures).
- [ ] `fetch.py` (httpx, sync ok for ETL): `fetch_validity()→list[dict]` (`/api/validity` rows), `fetch_dosage()→tuple(gene_tsv,region_tsv, etags)` (FTP GRCh38+GRCh37 gene+region; capture ETag/Last-Modified), `fetch_actionability()→(brief,adult,ped)`, `fetch_erepo_tsv()→bytes` + `fetch_erepo_news()→list[dict]`, `fetch_affiliates()→list[dict]`. Respect timeouts; raise typed errors.
- [ ] `freshness.py`: `validity_signal(rows)→(max_date,count,sha256)`, `dosage_signal(etags)`, `actionability_signal(brief)→(max_lastUpdated,count,sha256)`, `erepo_signal(news,tsv_bytes)→(version,count,sha256)`; `sha256_rows(rows, key_fields)` canonical hasher (sorted, banner-excluded).
- [ ] Tests against `.research/samples/*` copied into `tests/fixtures/`: signals deterministic; hash stable across reorder.
- [ ] Commit `feat: add ClinGen source fetchers + freshness signals`.

### Task 2.3: parsers (validity, dosage, actionability, erepo)
**Files:** Create `clingen_link/etl/parse.py`. **Test:** `tests/unit/test_parse.py`.
- [ ] `parse_validity(rows)→list[ValidityRow]`: map JSON → normalized (strip trailing spaces in disease_name; keep perm_id, mondo, moi, classification, expert_panel, dates).
- [ ] `parse_dosage(gene_tsv, region_tsv)→list[DosageRow]`: skip `#` comment lines; map 23-col TSV; decode score codes (40→"unlikely", 30→"AR"); collect PMID1..6 → json; record_type gene|region; merge GRCh37/38 coords.
- [ ] `parse_actionability(brief, adult, ped)→list[ActionabilityRow]`: index by docId; merge adult/ped status+release+sepio_iri; embed genes+assertions json.
- [ ] `parse_erepo(tsv_bytes)→list[ErepoRow]`: 20-col TSV; split HGVS/PubMed/evidence-codes lists; bool Retracted; keep caid, uuid, dates, cspec.
- [ ] `build_gene_index(validity, dosage, actionability, erepo_summary)→(genes, aliases)`.
- [ ] Tests: each parser on a 2-3 row fixture → exact expected normalized dicts; score-code decode; PMID splitting; comment-line skipping.
- [ ] Commit `feat: add ClinGen ETL parsers`.

### Task 2.4: wire writers + `clingen-link refresh` CLI + produce real snapshot
**Files:** Create `clingen_link/cli.py`; modify `clingen_link/etl/build.py` (writer fns). **Test:** `tests/unit/test_build_snapshot.py`.
- [ ] Add writer fns in `build.py` consuming parser outputs → INSERT into tables + FTS; populate `meta` from freshness signals + `fetched_at`.
- [ ] `cli.py` (typer): `serve` (delegate to server_manager), `refresh [--check] [--out PATH]`, `health`. `refresh` = fetch→parse→build; `--check` = fetch signals only, compare to existing `meta`, print staleness, exit 1 if stale (no writes).
- [ ] Test: `build_snapshot` on fixtures → query counts match; `refresh --check` against a stale fixture meta reports stale.
- [ ] **Run real ETL:** `uv run clingen-link refresh --out clingen_link/data/clingen.sqlite`; assert validity≥3000, dosage≥2000, actionability≥150, erepo≥10000 rows. Compress → `clingen.sqlite.zst` + `.sha256`; gitignore the raw `.sqlite`, commit the `.zst`.
- [ ] Commit `feat: add refresh CLI and build initial ClinGen snapshot`.

---

## Phase 3 — Store + services (read layer)

### Task 3.1: read-only store + gene resolution
**Files:** Create `clingen_link/store/__init__.py`, `clingen_link/store/db.py`, `clingen_link/store/queries.py`. **Test:** `tests/unit/test_store.py` (fixture `.sqlite` from a session fixture built off `tests/fixtures`).
- [ ] `db.py`: `Store` opening bundled `.zst`→temp `.sqlite` (or raw path) read-only; `resolve_gene(query)→canonical symbol|None` (exact symbol, hgnc_id, alias, case-insensitive); `meta()→dict[domain→freshness]`.
- [ ] `queries.py`: per-domain functions returning raw rows: `validity_for_gene`, `search_validity`, `dosage_for_gene`, `search_dosage`, `actionability_for_gene`, `search_actionability`, `erepo_for_gene`, `erepo_by_caid/hgvs`, `gene_summary_counts`, `expert_panels`, `search_genes`.
- [ ] Tests: resolve `BRCA1`/`HGNC:1100`/alias; each query returns expected fixture rows; FTS search matches disease substrings.
- [ ] Commit `feat: add read-only SQLite store + queries`.

### Task 3.2: live drill-down client
**Files:** Create `clingen_link/api/__init__.py`, `clingen_link/api/base_client.py`, `clingen_link/api/clingen_client.py`. **Test:** `tests/unit/test_clingen_client.py` (respx).
- [ ] `base_client.py`: copy gnomad pattern over `httpx.AsyncClient` — `asyncio.Semaphore(max_concurrency)`, jittered exp backoff retry on {429,500,502,503,504}+transport, queue-wait→`RateLimitedError`; typed faults `ClingenApiError`/`DataNotFoundError`/`UpstreamInputError`/`RateLimitedError`.
- [ ] `clingen_client.py`: `erepo_interpretation(uuid|caid|hgvs)→dict` (`/evrepo/api/classifications?caid=…&format=json` / `/interpretation/{uuid}?format=json`), `actionability_sepio(doc_id, context)→dict`, `erepo_news()→list`.
- [ ] Tests: respx mock endpoints; retry on 503 then 200; 404→DataNotFoundError.
- [ ] Commit `feat: add live ClinGen httpx client + resilience`.

### Task 3.3: services (cache + merge store/live) + models
**Files:** Create `clingen_link/models/*.py` (pydantic response models per domain), `clingen_link/services/{gene_service,validity_service,dosage_service,actionability_service,erepo_service}.py`. **Test:** `tests/unit/test_services.py`.
- [ ] Models: typed response models (ValidityAssertion, DosageRecord, ActionabilityCuration, VariantInterpretation, GeneSummary, ExpertPanel) with `recommended_citation` + permalink fields.
- [ ] Services wrap store (+ client for drill-down); `async-lru` `@alru_cache` on hot reads; `erepo_service.get_interpretation` prefers snapshot, live on `refresh=true` or cache miss, TTL keyed to ERepo `news` version. Build `recommended_citation` strings per domain.
- [ ] `gene_service.get_summary(symbol)` aggregates all four domains from store.
- [ ] Tests: services return models from a fixture store; citation strings well-formed; erepo refresh path calls client (respx).
- [ ] Commit `feat: add domain services, models, citation builders`.

---

## Phase 4 — MCP tools (parallelizable per domain)

Each tool file follows the house pattern: `Annotated[..., Field(description, pattern, examples)]`, `Literal` enums, `response_mode`, `output_schema=relax_output_schema(...)`, `READ_ONLY_OPEN_WORLD`, inner `async def call()` wrapped by `run_mcp_tool`, `_meta.next_commands` + `recommended_citation`. Each tool tested via `mcp.call_tool(...)` against a fixture store + injected services. Shapers live in `clingen_link/mcp/shaping.py`.

### Task 4.1: shaping helpers
**Files:** Create `clingen_link/mcp/shaping.py`. **Test:** `tests/unit/test_shaping.py`.
- [ ] `compact/standard/full/minimal` shapers per domain (drop nulls/verbose fields in compact; `minimal`=headline+counts). `truncated` block builder `{kind,dropped,to_disable,to_restore,filter}`.
- [ ] Commit `feat: add response shaping helpers`.

### Task 4.2: gene hub tools  (`search_genes`, `get_gene_summary`)
**Files:** Create `clingen_link/mcp/tools/genes.py`; register in `tools/__init__.py`. **Test:** `tests/unit/test_tool_genes.py`.
- [ ] Implement both tools per spec §4; `get_gene_summary` flagship cross-domain; next_commands → per-domain tools.
- [ ] Tests: `search_genes("BRCA1")` resolves + lists domains; `get_gene_summary` returns all four sections; unknown gene → `not_found` envelope with fallback.
- [ ] Commit `feat: add gene hub tools`.

### Task 4.3: validity tools
**Files:** `clingen_link/mcp/tools/validity.py`. **Test:** `tests/unit/test_tool_validity.py`.
- [ ] `get_gene_validity`, `search_validity`; filters classification/moi/expert_panel; CGGV permalink + citation.
- [ ] Tests incl. classification filter + pagination + truncation.
- [ ] Commit `feat: add gene-disease validity tools`.

### Task 4.4: dosage tools
**Files:** `clingen_link/mcp/tools/dosage.py`. **Test:** `tests/unit/test_tool_dosage.py`.
- [ ] `get_gene_dosage`, `search_dosage` (gene+region, score filters, cytoband); haplo/triplo interpretation text.
- [ ] Commit `feat: add gene dosage tools`.

### Task 4.5: actionability tools
**Files:** `clingen_link/mcp/tools/actionability.py`. **Test:** `tests/unit/test_tool_actionability.py`.
- [ ] `get_gene_actionability` (adult/ped; `include_detail` live SEPIO via service/client), `search_actionability`.
- [ ] Tests incl. `include_detail=true` live path (respx) and snapshot-only path.
- [ ] Commit `feat: add clinical actionability tools`.

### Task 4.6: erepo + reference tools
**Files:** `clingen_link/mcp/tools/erepo.py`, `clingen_link/mcp/tools/reference.py`. **Test:** `tests/unit/test_tool_erepo.py`, `test_tool_reference.py`.
- [ ] `get_variant_interpretations` (by gene/condition/expert_panel, snapshot), `get_variant_interpretation` (caid/hgvs; full ACMG; `refresh` live), `list_expert_panels`.
- [ ] Commit `feat: add ERepo variant + expert-panel tools`.

### Task 4.7: finalize capabilities + resources + instructions
**Files:** Modify `clingen_link/mcp/tools/metadata.py`, `clingen_link/mcp/resources.py`. **Test:** `tests/unit/test_capabilities.py`.
- [ ] Full capabilities: datasets+versions (from store.meta), all tools, token_cost_hints, error_codes, parameter_conventions, resources map, `capabilities_version` sha256. Resources `clingen://{capabilities,usage,reference,freshness,research-use,citations}` + `RESEARCH_USE_NOTICE`. Finalize `_INSTRUCTIONS`.
- [ ] Test: every registered tool appears in capabilities; resources resolve; `capabilities_version` stable.
- [ ] Commit `feat: finalize capabilities, resources, server instructions`.

---

## Phase 5 — Polish, docs, deploy, verify

### Task 5.1: docs
**Files:** `README.md`, `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md`), `docs/architecture.md`, `docs/usage.md`, `.env.example`.
- [ ] README (features, quick start `make install`/`make dev`, stdio `uv run clingen-link-mcp`, `refresh` CLI, Claude Desktop block, env vars, license/citation). AGENTS.md (source of truth: areas, uv-only, Makefile-first, 600-LOC, refresh workflow). Commit.

### Task 5.2: Docker + CI workflows
**Files:** `docker/` (Dockerfile + compose overlays + README), `.github/workflows/{ci.yml,docker.yml,release.yml,security.yml,data-refresh.yml}`, `dependabot.yml`.
- [ ] Copy gtex-link/pubtator-link workflows; rename. `ci.yml`: uv sync + `make ci-local` + `make test-cov`. `data-refresh.yml`: weekly cron `clingen-link refresh --check`; on change rebuild + commit `.zst` + publish Release asset. Dockerfile multi-stage; CMD `clingen-link --transport unified`. Commit.

### Task 5.3: integration drift tests + final verification
**Files:** `tests/integration/test_clingen_live.py`.
- [ ] `@pytest.mark.integration` live tests: validity total ≥3000, dosage ≥2000, actionability brief parseable, erepo `news` has `relatedVersion`, erepo gene query returns interpretations. (Excluded from default CI.)
- [ ] Run full `make ci-local` + `make test-cov`; confirm coverage ≥80; fix gaps. Verify stdio server boots (`uv run clingen-link-mcp` handshake) and `get_server_capabilities` lists all tools.
- [ ] Commit `test: add live drift tests; finalize coverage`.

---

## Self-review (coverage of spec)
- §2 acquisition → Tasks 2.1–2.4, 3.2. §2.2 freshness → 2.2, 2.4, 4.7. §3 schema → 2.1. §4 tools → 4.2–4.7 (all 13). §4 envelope/citation → 1.4, 3.3, 4.x. §5 layout → all. §6 testing → every task + 5.3. §7 phases → Phases 1–5. §8 risks → 5.3 (drift), 1.2 (LOC cap), 3.2 (live latency/cache), 4.7 (safety).
- No placeholders: boilerplate tasks specify exact copy-source + adaptations; ClinGen-specific tasks specify concrete signatures/behavior.
