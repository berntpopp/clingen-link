"""Read-only SQLite store: open a selected snapshot + resolve genes + freshness.

The store is the serve-time read boundary. It opens an externally materialized snapshot
**read-only** (the ETL is the only writer) and is safe to share across threads:
every read borrows a fresh short-lived :class:`sqlite3.Connection` from a small
internal pool guarded by a lock, with ``check_same_thread=False`` so a
connection can be handed to whichever thread next needs it.

Snapshot resolution (constructor):

* A ``.sqlite`` path is opened directly read-only (mode=ro, immutable).
* A ``.zst`` path is rejected; the hardened init path is the only decompressor.
* A missing snapshot raises :class:`SnapshotUnavailableError` telling the caller
  to run ``clingen-link refresh``.

Gene resolution priority (spec section 3): exact symbol, ``HGNC:n``, alias
table, case-insensitive symbol/alias — in that order. The alias table already
carries case-folded HGNC ids and symbols (see ``etl.parse.build_gene_index``),
so most case-insensitive hits resolve via a single alias lookup.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import DataRequirement
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

# --- External immutable bundle verification ---------------------------------
#
# The init path verifies the operator-reviewed compressed digest before bounded
# streaming decompression. The application never opens a compressed artifact.
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


def canonical_expanded_digest(path: Path, *, member_name: str) -> str:
    """Hash the canonical one-member expanded-tree identity."""
    file_digest = _file_sha256(path)
    record = f"{member_name}\0{0o444:o}\0{path.stat().st_size}\0{file_digest}"
    return hashlib.sha256(record.encode()).hexdigest()


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


def _verify_schema(path: Path, expected: str) -> None:
    try:
        uri = f"file:{path.resolve()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute("SELECT DISTINCT snapshot_version FROM meta").fetchall()
    except sqlite3.Error as exc:
        raise SnapshotUnavailableError(f"ClinGen snapshot schema probe failed: {exc}") from exc
    actual = {str(row[0]) if "." in str(row[0]) else f"{row[0]}.0.0" for row in rows}
    if actual != {expected}:
        raise SnapshotUnavailableError(
            f"ClinGen snapshot schema is incompatible (expected {expected}, got {sorted(actual)})"
        )


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def materialize_bundle(requirement: DataRequirement, root: Path) -> Path:
    """Verify and atomically select an immutable external ClinGen snapshot."""
    bundle = requirement.bundle_path
    root.mkdir(parents=True, exist_ok=True)
    verified_bundle = root / f".verified-bundle-{os.getpid()}"
    compressed_size = 0
    digest = hashlib.sha256()
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(bundle, flags)
        try:
            source_stat = os.fstat(fd)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                raise SnapshotUnavailableError(
                    f"ClinGen bundle is not a private regular file: {bundle}"
                )
            with os.fdopen(fd, "rb", closefd=False) as src, verified_bundle.open("xb") as dst:
                for chunk in iter(lambda: src.read(_STREAM_CHUNK), b""):
                    compressed_size += len(chunk)
                    if compressed_size > requirement.max_compressed_bytes:
                        raise SnapshotUnavailableError(
                            "ClinGen bundle exceeds the compressed size ceiling"
                        )
                    digest.update(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
        finally:
            os.close(fd)
        if digest.hexdigest() != requirement.compressed_sha256:
            raise SnapshotUnavailableError(
                "ClinGen snapshot bundle failed its compressed SHA-256 integrity check"
            )

        lock_path = root / ".materialize.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            version_dir = root / requirement.compressed_sha256[:16]
            selected = version_dir / "clingen.sqlite"
            identity_path = version_dir / "identity.json"
            if not selected.exists():
                staging = root / f".{requirement.compressed_sha256[:16]}.staging-{os.getpid()}"
                staging.mkdir(mode=0o700)
                staged = staging / "clingen.sqlite"
                try:
                    _decompress_capped(
                        verified_bundle, staged, max_bytes=requirement.max_expanded_bytes
                    )
                    expanded_digest = canonical_expanded_digest(
                        staged, member_name="clingen.sqlite"
                    )
                    if expanded_digest != requirement.expanded_tree_sha256:
                        raise SnapshotUnavailableError(
                            "ClinGen snapshot expanded-tree SHA-256 does not match deployment pin"
                        )
                    _verify_schema(staged, requirement.schema_version)
                    staged.chmod(0o444)
                    identity = {
                        "mode": "external-reference",
                        "compressed_sha256": requirement.compressed_sha256,
                        "expanded_tree_sha256": expanded_digest,
                        "schema_version": requirement.schema_version,
                        "schema_minimum": requirement.schema_minimum,
                        "schema_maximum": requirement.schema_maximum,
                        "compressed_bytes": compressed_size,
                        "expanded_bytes": staged.stat().st_size,
                    }
                    identity_tmp = staging / "identity.json.tmp"
                    identity_tmp.write_text(
                        json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    identity_tmp.chmod(0o444)
                    os.replace(identity_tmp, staging / "identity.json")
                    with staged.open("rb") as handle:
                        os.fsync(handle.fileno())
                    _fsync_directory(staging)
                    os.replace(staging, version_dir)
                    _fsync_directory(root)
                except BaseException:
                    for child in staging.iterdir() if staging.exists() else ():
                        child.unlink(missing_ok=True)
                    staging.rmdir() if staging.exists() else None
                    raise
            else:
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                expected_identity = {
                    "compressed_sha256": requirement.compressed_sha256,
                    "expanded_tree_sha256": requirement.expanded_tree_sha256,
                    "schema_version": requirement.schema_version,
                    "schema_minimum": requirement.schema_minimum,
                    "schema_maximum": requirement.schema_maximum,
                }
                if any(identity.get(key) != value for key, value in expected_identity.items()):
                    raise SnapshotUnavailableError(
                        "existing ClinGen materialization identity mismatch"
                    )
                if (
                    canonical_expanded_digest(selected, member_name="clingen.sqlite")
                    != requirement.expanded_tree_sha256
                ):
                    raise SnapshotUnavailableError(
                        "existing ClinGen materialization expanded-tree digest mismatch"
                    )
                _verify_schema(selected, requirement.schema_version)

            current_tmp = root / ".current.tmp"
            current_tmp.unlink(missing_ok=True)
            current_tmp.symlink_to(version_dir.name, target_is_directory=True)
            os.replace(current_tmp, root / "current")
            _fsync_directory(root)
            return selected
    except OSError as exc:
        raise SnapshotUnavailableError(f"could not read ClinGen bundle safely: {exc}") from exc
    finally:
        verified_bundle.unlink(missing_ok=True)


class Store:
    """Read-only accessor for a selected external ClinGen SQLite snapshot."""

    def __init__(self, path: str | Path) -> None:
        """Open a materialized SQLite ``path`` read-only and immutable.

        Raises:
            SnapshotUnavailableError: the snapshot file is absent or unreadable.
        """
        source = Path(path)
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

    @property
    def data_identity(self) -> dict[str, Any]:
        """Return the materializer-written identity beside the selected database."""
        identity_path = self._db_path.with_name("identity.json")
        if not identity_path.is_file():
            return {"mode": "test-fixture", "schema_version": self._schema_version()}
        value = json.loads(identity_path.read_text(encoding="utf-8"))
        identity = dict(value) if isinstance(value, dict) else {}
        expected = identity.get("expanded_tree_sha256")
        if expected != canonical_expanded_digest(self._db_path, member_name="clingen.sqlite"):
            raise SnapshotUnavailableError("selected ClinGen expanded-tree identity is invalid")
        return identity

    def _schema_version(self) -> str:
        with self.connection() as conn:
            rows = conn.execute("SELECT DISTINCT snapshot_version FROM meta").fetchall()
        versions = sorted(str(row[0]) for row in rows)
        return versions[0] if len(versions) == 1 else "unknown"

    # ------------------------------------------------------------------
    # Snapshot resolution
    # ------------------------------------------------------------------
    def _resolve_db_path(self, source: Path) -> Path:
        """Return a selected ``.sqlite`` path and reject compressed inputs."""
        if source.suffix == ".zst":
            raise SnapshotUnavailableError(
                "Compressed ClinGen bundles must be materialized by `clingen-link "
                "materialize-data`; the application only opens a selected read-only SQLite file."
            )
        if not source.exists():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot not found at {source}. {_REFRESH_HINT}"
            )
        return source

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
        """Close every pooled connection."""
        with self._lock:
            self._closed = True
            while self._pool:
                self._pool.popleft().close()

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
