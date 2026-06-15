# Changelog

All notable changes to clingen-link are documented here.

## [1.0.0] - 2026-06-15

Adopt the **GeneFoundry Tool-Naming Standard v1** so the server composes cleanly
behind [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router)
(tools surface as `clingen_<tool>` at the gateway). This is a **breaking**
release: tool and argument names change with no deprecation aliases, per the
standard's project decision (Rule 7).

### Changed (BREAKING)

- Renamed the discovery tool `get_clingen_diagnostics` → **`get_diagnostics`**.
  The embedded `clingen` source token was redundant under the gateway's
  `clingen_` namespace prefix (it produced `clingen_get_clingen_diagnostics`).
  The gateway-qualified name is now `clingen_get_diagnostics`. The payload,
  behaviour, and the service method are unchanged; update any direct callers of
  the tool name.
- Renamed the gene argument `gene` → **`gene_symbol`** (accepts a symbol or
  `HGNC:<id>`) on every gene-accepting tool: `get_gene_actionability`,
  `search_actionability`, `get_gene_dosage`, `get_gene_validity`,
  `search_validity`, `get_gene_summary`, `get_variant_interpretations`,
  `list_cspecs`, and `get_cspec`. `search_genes` keeps its free-text `query`
  argument.
- Renamed the ERepo list argument `condition` → **`disease`** on
  `get_variant_interpretations` (still accepts disease text or a MONDO id),
  aligning with `disease` used elsewhere in the server.
- `_meta.next_commands` now emit the canonical argument keys (`gene_symbol`,
  `disease`); any consumer that executed these follow-ups verbatim continues to
  work.

### Added

- Tool-name compliance test (`tests/unit/test_tool_names.py`): every registered
  tool must match `^[a-z0-9_]{1,50}$`, start with a canonical verb
  (`get|search|list|resolve|find|compare|compute`), and never embed the
  `clingen` source/self token (the gateway adds the namespace).
- README documents the canonical gateway **namespace token** `clingen` and the
  canonical argument names.

### Fixed

- Reconciled the package version to a single source: `pyproject.toml` and
  `clingen_link.__init__.__version__` are both `1.0.0`, and the FastAPI host
  (`server_manager.py`) now derives its version from `__version__` instead of a
  hard-coded literal.

### Notes

- **Pagination deviation (documented):** search/list tools keep `page` (1-based)
  + `size` (≤100) rather than the fleet's `limit`/`offset`. A `truncated` block
  in `_meta` flags omitted rows. This deviation is documented per the standard's
  pagination clause.
- `serverInfo.name` remains `clingen-link`.

## [0.1.0] - 2026-06-12

- Initial release: MCP server grounding gene/disease/variant questions in
  ClinGen's curated datasets (gene-disease validity, dosage sensitivity, clinical
  actionability, ERepo variant interpretations, and CSpec criteria
  specifications) with a bundled SQLite snapshot, freshness tracking, and a
  refresh CLI.
