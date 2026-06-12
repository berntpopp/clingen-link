"""Read-only SQLite store: open the snapshot + resolve genes + read freshness.

The store is the serve-time read boundary. It opens the bundled snapshot
**read-only** (the ETL is the only writer) and is safe to share across threads:
every read borrows a fresh short-lived :class:`sqlite3.Connection` from a small
internal pool guarded by a lock, with ``check_same_thread=False`` so a
connection can be handed to whichever thread next needs it.

Snapshot resolution (constructor):

* A ``.sqlite`` path is opened directly read-only (mode=ro, immutable).
* A ``.zst`` path (the shipped bundle) is decompressed **once** to a temp
  ``.sqlite`` under the OS temp dir and that file is opened read-only. The temp
  file is cleaned up on :meth:`close`.
* A missing snapshot raises :class:`SnapshotUnavailableError` telling the caller
  to run ``clingen-link refresh``.

Gene resolution priority (spec section 3): exact symbol, ``HGNC:n``, alias
table, case-insensitive symbol/alias — in that order. The alias table already
carries case-folded HGNC ids and symbols (see ``etl.parse.build_gene_index``),
so most case-insensitive hits resolve via a single alias lookup.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..exceptions import SnapshotUnavailableError

# How many idle read connections to keep warm. Reads are short; a handful covers
# the bounded concurrency of the live layer without unbounded fd growth.
_POOL_SIZE = 4

_REFRESH_HINT = "Run `clingen-link refresh` to (re)build the snapshot, then restart the server."


class Store:
    """Read-only accessor for the bundled ClinGen SQLite snapshot."""

    def __init__(self, path: str | Path) -> None:
        """Open ``path`` read-only, decompressing a ``.zst`` bundle if needed.

        Raises:
            SnapshotUnavailableError: the snapshot file is absent or unreadable.
        """
        source = Path(path)
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._db_path = self._resolve_db_path(source)
        self._lock = threading.Lock()
        self._pool: deque[sqlite3.Connection] = deque()
        self._closed = False
        # Fail fast: open one connection now so a missing/corrupt snapshot is
        # surfaced at construction rather than on first query.
        try:
            probe = self._open()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise SnapshotUnavailableError(
                f"ClinGen snapshot at {self._db_path} is unreadable: {exc}. {_REFRESH_HINT}"
            ) from exc
        self._pool.append(probe)

    # ------------------------------------------------------------------
    # Snapshot resolution
    # ------------------------------------------------------------------
    def _resolve_db_path(self, source: Path) -> Path:
        """Return a ``.sqlite`` path to open, decompressing a ``.zst`` bundle once."""
        if source.suffix == ".zst":
            return self._decompress_bundle(source)
        if not source.exists():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot not found at {source}. {_REFRESH_HINT}"
            )
        return source

    def _decompress_bundle(self, bundle: Path) -> Path:
        """Decompress the shipped ``.zst`` bundle to a temp ``.sqlite`` once."""
        if not bundle.exists():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot bundle not found at {bundle}. {_REFRESH_HINT}"
            )
        import zstandard

        self._tempdir = tempfile.TemporaryDirectory(prefix="clingen-snapshot-")
        out_path = Path(self._tempdir.name) / "clingen.sqlite"
        dctx = zstandard.ZstdDecompressor()
        with bundle.open("rb") as src, out_path.open("wb") as dst:
            dctx.copy_stream(src, dst)
        return out_path

    def _open(self) -> sqlite3.Connection:
        """Open a fresh read-only connection with row access by column name.

        ``check_same_thread=False`` lets a pooled connection be reused by any
        thread; the store's lock serialises hand-off so no two threads share a
        connection concurrently. ``immutable=1`` is safe because the ETL is the
        only writer and never touches a live snapshot in place.
        """
        uri = f"file:{self._db_path.resolve()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Borrow a pooled read-only connection for the duration of a query.

        Thread-safe: the pool is guarded by a lock and connections are created
        with ``check_same_thread=False`` (via ``open_readonly``'s URI), so a
        connection may be reused by a different thread.
        """
        if self._closed:
            raise SnapshotUnavailableError(f"Store is closed. {_REFRESH_HINT}")
        conn: sqlite3.Connection | None = None
        with self._lock:
            if self._pool:
                conn = self._pool.popleft()
        if conn is None:
            conn = self._open()
        try:
            yield conn
        finally:
            with self._lock:
                if not self._closed and len(self._pool) < _POOL_SIZE:
                    self._pool.append(conn)
                else:
                    conn.close()

    def close(self) -> None:
        """Close every pooled connection and remove any decompressed temp file."""
        with self._lock:
            self._closed = True
            while self._pool:
                self._pool.popleft().close()
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def __enter__(self) -> Store:
        """Context-manager entry returns the store."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Context-manager exit closes the store."""
        self.close()

    # ------------------------------------------------------------------
    # Gene resolution
    # ------------------------------------------------------------------
    def resolve_gene(self, query: str) -> str | None:
        """Resolve free-text input to a canonical gene symbol, or ``None``.

        Priority: exact symbol → exact ``HGNC:n`` (alias) → exact alias →
        case-insensitive symbol → case-insensitive alias. The first hit wins so a
        real symbol is never shadowed by an alias that happens to case-match.
        """
        raw = query.strip()
        if not raw:
            return None
        with self.connection() as conn:
            return self._resolve_with(conn, raw)

    @staticmethod
    def _resolve_with(conn: sqlite3.Connection, raw: str) -> str | None:
        """Run the ordered resolution lookups on an open connection."""
        # 1. Exact canonical symbol (case-sensitive).
        row = conn.execute("SELECT symbol FROM gene WHERE symbol = ?", (raw,)).fetchone()
        if row is not None:
            return str(row["symbol"])
        # 2/3. Exact alias (covers HGNC:n and prev-symbol forms, case-sensitive).
        row = conn.execute(
            "SELECT symbol FROM gene_alias WHERE alias = ? ORDER BY symbol LIMIT 1",
            (raw,),
        ).fetchone()
        if row is not None:
            return str(row["symbol"])
        # 4. Case-insensitive canonical symbol.
        row = conn.execute(
            "SELECT symbol FROM gene WHERE symbol = ? COLLATE NOCASE LIMIT 1",
            (raw,),
        ).fetchone()
        if row is not None:
            return str(row["symbol"])
        # 5. Case-insensitive alias (alias table also stores casefolded forms).
        folded = raw.casefold()
        row = conn.execute(
            "SELECT symbol FROM gene_alias WHERE alias = ? ORDER BY symbol LIMIT 1",
            (folded,),
        ).fetchone()
        if row is not None:
            return str(row["symbol"])
        row = conn.execute(
            "SELECT symbol FROM gene_alias WHERE alias = ? COLLATE NOCASE ORDER BY symbol LIMIT 1",
            (raw,),
        ).fetchone()
        if row is not None:
            return str(row["symbol"])
        return None

    # ------------------------------------------------------------------
    # Freshness / provenance
    # ------------------------------------------------------------------
    def meta(self) -> dict[str, dict[str, Any]]:
        """Return per-domain freshness rows keyed by ``domain``.

        Each value carries ``source_url, fetched_at, signal_type, signal_value,
        content_sha256, record_count, snapshot_version`` — the provenance surfaced
        in ``get_server_capabilities`` and every tool's ``_meta.data_version``.
        """
        out: dict[str, dict[str, Any]] = {}
        with self.connection() as conn:
            for row in conn.execute(
                "SELECT domain, source_url, fetched_at, signal_type, signal_value, "
                "content_sha256, record_count, snapshot_version FROM meta"
            ):
                out[str(row["domain"])] = dict(row)
        return out
