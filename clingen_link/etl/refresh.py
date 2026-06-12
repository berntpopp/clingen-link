"""``refresh`` command implementation: fetch → build, or ``--check`` staleness.

This is the operator-facing ETL entry point, wired both as a console script
(``clingen-link-refresh``) and via ``python -m clingen_link.etl refresh``. It is
deliberately separate from :mod:`clingen_link.etl.build` so the heavy pure-build
logic stays import-light and under the LOC cap.

``refresh`` fetches every domain, builds a new snapshot, and reports counts.
``refresh --check`` fetches only the cheap freshness signals, compares them to
the ``meta`` of the existing snapshot, prints a staleness report, exits non-zero
if stale, and writes nothing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..exceptions import SourceFetchError
from . import fetch, freshness, parse
from .build import Sources, build_snapshot, default_snapshot_path, open_readonly

_DOMAINS = ("validity", "dosage", "actionability", "erepo")


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (only impure spot)."""
    return datetime.now(UTC).isoformat()


def gather_sources() -> tuple[Sources, list[str]]:
    """Fetch every domain. Returns ``(sources, failures)``.

    A single failing domain is recorded in ``failures`` and the others continue,
    so a partial outage still produces a usable (partial) snapshot.
    """
    sources = Sources()
    failures: list[str] = []
    with httpx.Client(timeout=fetch._DEFAULT_TIMEOUT) as client:
        _try(lambda: _load_validity(sources, client), "validity", failures)
        _try(lambda: _load_dosage(sources, client), "dosage", failures)
        _try(lambda: _load_actionability(sources, client), "actionability", failures)
        _try(lambda: _load_erepo(sources, client), "erepo", failures)
        _try(lambda: _load_erepo_summary(sources, client), "erepo_summary", failures)
        _try(lambda: _load_affiliates(sources, client), "affiliates", failures)
    return sources, failures


def _try(fn: Any, source: str, failures: list[str]) -> None:
    try:
        fn()
    except SourceFetchError as exc:
        print(f"  ! {source} fetch failed: {exc}", file=sys.stderr)
        failures.append(source)


def _load_validity(sources: Sources, client: httpx.Client) -> None:
    sources.validity_rows = fetch.fetch_validity(client)


def _load_dosage(sources: Sources, client: httpx.Client) -> None:
    bundle = fetch.fetch_dosage(client)
    sources.dosage_gene_tsv = bundle.gene_tsv
    sources.dosage_region_tsv = bundle.region_tsv
    sources.dosage_etags = bundle.etags


def _load_actionability(sources: Sources, client: httpx.Client) -> None:
    sources.actionability_brief = fetch.fetch_actionability(client)


def _load_erepo(sources: Sources, client: httpx.Client) -> None:
    bundle = fetch.fetch_erepo(client)
    sources.erepo_tsv = bundle.tsv_text
    sources.erepo_news = bundle.news


def _load_erepo_summary(sources: Sources, client: httpx.Client) -> None:
    sources.erepo_summary = fetch.fetch_erepo_summary(client)


def _load_affiliates(sources: Sources, client: httpx.Client) -> None:
    sources.affiliates = fetch.fetch_affiliates(client)


def _compute_signals(sources: Sources) -> dict[str, dict[str, Any]]:
    """Compute the per-domain freshness signals from fetched sources."""
    return {
        "validity": freshness.validity_signal(parse.parse_validity(sources.validity_rows)),
        "dosage": freshness.dosage_signal(sources.dosage_etags),
        "actionability": freshness.actionability_signal(sources.actionability_brief),
        "erepo": freshness.erepo_signal(sources.erepo_news, sources.erepo_tsv),
    }


def _read_meta(path: Path) -> dict[str, dict[str, Any]]:
    """Read existing ``meta`` rows keyed by domain (empty if no snapshot)."""
    if not path.exists():
        return {}
    conn = open_readonly(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT domain, signal_value, content_sha256, record_count FROM meta"
        ).fetchall()
    finally:
        conn.close()
    return {row["domain"]: dict(row) for row in rows}


def run_check(out_path: Path) -> int:
    """Fetch signals, compare to the existing snapshot meta, print a report.

    Returns the process exit code: ``0`` up to date, ``1`` stale / missing.
    """
    existing = _read_meta(out_path)
    if not existing:
        print(f"No snapshot at {out_path}; run 'refresh' to build one. STALE")
        return 1
    sources, failures = gather_sources()
    signals = _compute_signals(sources)
    stale = False
    print(f"Freshness check against {out_path}:")
    for domain in _DOMAINS:
        if domain in failures or (domain == "dosage" and not sources.dosage_etags):
            print(f"  {domain:14s} UNKNOWN (source unreachable)")
            continue
        live = signals[domain]
        prior = existing.get(domain)
        if prior is None or prior.get("content_sha256") != live["content_sha256"]:
            stale = True
            print(
                f"  {domain:14s} STALE   live={live['signal_value']!r} count={live['record_count']}"
            )
        else:
            print(f"  {domain:14s} up to date ({live['record_count']} records)")
    if stale:
        print("Snapshot is STALE; run 'refresh' to rebuild.")
        return 1
    print("Snapshot is up to date.")
    return 0


def run_refresh(out_path: Path) -> int:
    """Fetch all domains, build the snapshot, print counts. Returns exit code."""
    print(f"Fetching ClinGen sources -> building snapshot at {out_path}")
    sources, failures = gather_sources()
    if failures:
        print(f"  (continuing despite failures in: {', '.join(failures)})")
    counts = build_snapshot(out_path, sources, _now_iso())
    print("Snapshot built. Row counts:")
    for table, count in sorted(counts.items()):
        print(f"  {table:14s} {count}")
    # A totally empty build is a failure even if no exception was raised.
    if counts.get("validity", 0) == 0 and counts.get("erepo", 0) == 0:
        print("Refresh produced an empty snapshot.", file=sys.stderr)
        return 1
    return 0


def add_refresh_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the ``--check`` / ``--out`` options to a refresh subparser."""
    parser.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: fetch only freshness signals, report staleness, write nothing.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Snapshot output path (default: bundled clingen_link/data/clingen.sqlite).",
    )


def handle_refresh(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``refresh`` invocation to check or build."""
    out_path = Path(args.out) if getattr(args, "out", None) else default_snapshot_path()
    if getattr(args, "check", False):
        return run_check(out_path)
    return run_refresh(out_path)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for ``clingen-link-refresh``."""
    parser = argparse.ArgumentParser(
        prog="clingen-link-refresh",
        description="Build or check the bundled ClinGen SQLite snapshot.",
    )
    add_refresh_arguments(parser)
    args = parser.parse_args(argv)
    return handle_refresh(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
