# clingen-link

An MCP server grounding gene/disease/variant questions in
[ClinGen](https://clinicalgenome.org/)'s four curated datasets: gene-disease
validity, gene dosage, clinical actionability, and variant pathogenicity (ERepo).

Part of the `*-link` family of MCP servers. Built on the `gnomad-link` house
style: a hand-authored FastMCP v3 facade with the full canonical response
envelope, three transports (unified / http / stdio), and a self-contained
SQLite snapshot for offline, token-efficient queries.

> Research use only; not for clinical decision support.

## Status

Phase 1 (scaffold) — package skeleton, tooling, transports, and a
`get_server_capabilities` discovery tool. ETL, store, services, and the domain
tools land in later phases.

## Development

Uses [uv](https://docs.astral.sh/uv/) exclusively (never `pip`).

```bash
uv sync --group dev      # install
make ci-local            # format-check, lint, lint-loc, typecheck, test
make mcp-serve           # run the stdio MCP server
```

## License

MIT, © 2026 Bernt Popp. ClinGen data is CC BY 4.0.
