"""Offline ETL package for the clingen-link SQLite snapshot.

This package is used by the ``refresh`` CLI to build the bundled read-only
SQLite snapshot from ClinGen's bulk endpoints. **None of this code runs in the
MCP request path** — the serving layer only opens the finished snapshot.

Modules:

* :mod:`clingen_link.etl.schema` — SQL DDL + ``create_schema``.
* :mod:`clingen_link.etl.parse` — pure JSON/TSV parsers (no I/O).
* :mod:`clingen_link.etl.freshness` — per-domain change-detection signals.
* :mod:`clingen_link.etl.fetch` — httpx fetchers hitting live ClinGen.
* :mod:`clingen_link.etl.build` — orchestrator: schema → writers → meta → atomic swap.
"""

from __future__ import annotations
