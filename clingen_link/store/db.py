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

import hashlib
import os
import sqlite3
import tempfile
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import settings
from ..exceptions import SnapshotUnavailableError

# How many idle read connections to keep warm. Reads are short; a handful covers
# the bounded concurrency of the live layer without unbounded fd growth.
_POOL_SIZE = 4

# Domain → backing table whose live ``COUNT(*)`` is the authoritative record count surfaced in
# capabilities / diagnostics / every tool's data_version. Recomputing it at serve time keeps the
# count honest even if a stored meta value drifted (e.g. an ETL that derived it from a filename).
_DOMAIN_TABLE: dict[str, str] = {
    "validity": "validity",
    "dosage": "dosage",
    "actionability": "actionability",
    "erepo": "erepo",
}

_REFRESH_HINT = "Run `clingen-link refresh` to (re)build the snapshot, then restart the server."

# --- F-16: .zst authenticity + decompression-bomb guard (EVERY path) --------
#
# ANY ``.zst`` snapshot is authenticity-verified and bomb-guarded BEFORE it is
# decompressed — the shipped/packaged bundle AND the operator-refresh / alternate
# bundle path alike. Before decompression a bundle's SHA-256 must match a trusted
# anchor and its expanded size is bounded, so a tampered / truncated /
# decompression-bomb / unverified bundle fails closed rather than being opened as
# a snapshot (defense in depth against package & supply-chain tampering).
#
# Trusted anchor, in order:
#   * the shipped package artifact  -> committed in-source constant below;
#   * any other bundle              -> operator pin ``settings.snapshot_zst_sha256``
#                                      (env CLINGEN_LINK_SNAPSHOT_ZST_SHA256), else
#                                      a sibling ``<name>.sha256`` sidecar.
# A non-shipped bundle with no anchor has no way to be authenticated, so it is
# REJECTED — never decompressed unverified.
_BUNDLED_ZST_PATH = Path(__file__).resolve().parent.parent / "data" / "clingen.sqlite.zst"

# SHA-256 of the packaged ``clingen.sqlite.zst``, pinned IN SOURCE so it is
# independent of the same-directory ``.sha256`` sidecar (which a package
# tamperer could rewrite alongside the artifact). Regenerated together with the
# bundle by ``.github/workflows/data-refresh.yml``; a drift-guard test keeps it
# in lockstep with the shipped artifact.
_BUNDLED_ZST_SHA256 = "0f9aa4134b8a8ef41b7b2042c0149a8b5205ba143e486e5fc7deeb92eca1f7d9"

# Expanded-size ceiling (decompression-bomb guard). The real snapshot is
# ~58.6 MiB; 256 MiB leaves generous room for growth while bounding a malicious
# bundle to a fixed, streamed cost.
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024

# Chunk size for the streaming hash + streaming decompress passes.
_STREAM_CHUNK = 1024 * 1024


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_STREAM_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_digest(value: str, *, source: str) -> str:
    """Return ``value`` as a validated lowercase 64-char hex SHA-256 digest.

    Raises:
        ValueError: ``value`` is not a 64-character hexadecimal digest.
    """
    candidate = value.strip().lower()
    if len(candidate) != 64 or any(c not in "0123456789abcdef" for c in candidate):
        raise ValueError(f"{source} is not a 64-character hex SHA-256 digest")
    return candidate


def _read_sidecar_digest(sidecar: Path) -> str:
    """Parse a ``<sha256>  <name>`` checksum sidecar, returning the hex digest.

    Raises:
        ValueError: the sidecar does not start with a 64-character hex digest.
    """
    tokens = sidecar.read_text(encoding="utf-8").strip().split()
    return _normalize_digest(tokens[0] if tokens else "", source="checksum sidecar")


def _expected_zst_digest(bundle: Path) -> str | None:
    """Return the SHA-256 to require for ``bundle`` before decompression.

    The shipped package artifact is anchored to the in-repo constant
    (authenticity — the security-relevant case). Any OTHER bundle (operator
    ``refresh`` output / alternate volume) must present an out-of-band anchor:
    the operator pin ``settings.snapshot_zst_sha256`` (env
    ``CLINGEN_LINK_SNAPSHOT_ZST_SHA256``) takes precedence, else a committed
    sibling ``.sha256`` sidecar. ``None`` is returned only when a non-shipped
    bundle has NEITHER anchor — the caller then fails closed rather than
    decompressing an unverified bundle (F-16 residual).

    Raises:
        ValueError: a configured/sidecar anchor is present but malformed.
    """
    try:
        is_shipped = bundle.resolve() == _BUNDLED_ZST_PATH.resolve()
    except OSError:  # pragma: no cover - defensive
        is_shipped = False
    if is_shipped:
        return _BUNDLED_ZST_SHA256
    configured = settings.snapshot_zst_sha256.strip()
    if configured:
        return _normalize_digest(configured, source="CLINGEN_LINK_SNAPSHOT_ZST_SHA256")
    sidecar = bundle.with_suffix(".sha256")
    if sidecar.exists():
        return _read_sidecar_digest(sidecar)
    return None


def _decompress_capped(bundle: Path, dest: Path, *, max_bytes: int) -> None:
    """Stream-decompress ``bundle`` into ``dest`` atomically, bounded by ``max_bytes``.

    Writes to a sibling ``.part`` file and ``os.replace``s it into place only on
    success, so an aborted decompression never leaves a usable snapshot. Fails
    closed on a decompression bomb (expanded size past ``max_bytes``) or a
    corrupt / truncated stream.

    Raises:
        SnapshotUnavailableError: the stream exceeds ``max_bytes`` or is invalid.
    """
    import zstandard

    tmp = dest.with_name(dest.name + ".part")
    total = 0
    try:
        dctx = zstandard.ZstdDecompressor()
        with bundle.open("rb") as src, tmp.open("wb") as dst, dctx.stream_reader(src) as reader:
            while True:
                chunk = reader.read(_STREAM_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SnapshotUnavailableError(
                        f"ClinGen snapshot bundle expands beyond the {max_bytes}-byte "
                        f"ceiling (decompression-bomb guard). {_REFRESH_HINT}"
                    )
                dst.write(chunk)
        os.replace(tmp, dest)
    except zstandard.ZstdError as exc:
        tmp.unlink(missing_ok=True)
        raise SnapshotUnavailableError(
            f"ClinGen snapshot bundle is corrupt or truncated: {exc}. {_REFRESH_HINT}"
        ) from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
        """Verify + decompress ANY ``.zst`` bundle to a temp ``.sqlite`` once.

        Applies to both the shipped/packaged bundle and the operator-refresh /
        alternate bundle path. Fails closed BEFORE decompression on a missing
        authenticity anchor or a checksum mismatch, and during a bounded
        streaming decompress on an expanded-size bomb or a corrupt / truncated
        stream. The output is written atomically.
        """
        if not bundle.exists():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot bundle not found at {bundle}. {_REFRESH_HINT}"
            )
        self._verify_bundle_digest(bundle)
        self._tempdir = tempfile.TemporaryDirectory(prefix="clingen-snapshot-")
        out_path = Path(self._tempdir.name) / "clingen.sqlite"
        try:
            _decompress_capped(bundle, out_path, max_bytes=_MAX_EXPANDED_BYTES)
        except BaseException:
            self._tempdir.cleanup()
            self._tempdir = None
            raise
        return out_path

    @staticmethod
    def _verify_bundle_digest(bundle: Path) -> None:
        """Fail closed unless ``bundle``'s SHA-256 matches a trusted anchor.

        A non-shipped bundle with no operator pin and no ``.sha256`` sidecar has
        no authenticity anchor, so it is REJECTED rather than decompressed
        unverified (F-16 residual — the operator-refresh / alternate path).
        """
        try:
            expected = _expected_zst_digest(bundle)
        except ValueError as exc:
            raise SnapshotUnavailableError(
                f"ClinGen snapshot bundle checksum anchor is malformed: {exc}. {_REFRESH_HINT}"
            ) from exc
        if expected is None:
            raise SnapshotUnavailableError(
                f"ClinGen snapshot bundle at {bundle} has no authenticity anchor: it is not "
                "the packaged bundle and has neither a configured "
                "CLINGEN_LINK_SNAPSHOT_ZST_SHA256 digest nor a sibling '.sha256' sidecar. "
                f"Refusing to decompress an unverified bundle. {_REFRESH_HINT}"
            )
        actual = _file_sha256(bundle)
        if actual != expected:
            raise SnapshotUnavailableError(
                "ClinGen snapshot bundle failed its SHA-256 integrity check "
                f"(expected {expected}, got {actual}). {_REFRESH_HINT}"
            )

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

        ``record_count`` is recomputed from the backing table's ``COUNT(*)`` so it always reflects
        the rows actually served, even if the stored meta value drifted (assessment H2 — the dosage
        count had been derived from the source filenames, not the row count).
        """
        out: dict[str, dict[str, Any]] = {}
        with self.connection() as conn:
            for row in conn.execute(
                "SELECT domain, source_url, fetched_at, signal_type, signal_value, "
                "content_sha256, record_count, snapshot_version FROM meta"
            ):
                entry = dict(row)
                table = _DOMAIN_TABLE.get(str(row["domain"]))
                if table is not None:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
                    if count is not None:
                        entry["record_count"] = int(count[0])
                out[str(row["domain"])] = entry
        return out
