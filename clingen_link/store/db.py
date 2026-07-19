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
from ..runtime_data_identity import (
    build_identity_manifest,
    canonical_json_bytes,
    verify_runtime_identity,
)
from .materialization_scratch import (
    cleanup_stale_materialization_scratch,
    create_staging_scratch,
    create_verified_bundle_scratch,
    remove_materialization_scratch,
)

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


def _fsync_regular_file_no_follow(path: Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise SnapshotUnavailableError(f"materialized identity is not a private file: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_runtime_identity_manifest(root: Path, release_tag: str, files: list[Path]) -> None:
    manifest = build_identity_manifest(root, release_tag, files)
    destination = root / "data-identity-manifest.json"
    temporary = root / "data-identity-manifest.json.tmp"
    payload = canonical_json_bytes(manifest) + b"\n"
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(root)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    verify_runtime_identity(root)


def _materialized_version_key(requirement: DataRequirement) -> str:
    key_material = (
        f"runtime-v1\0{requirement.compressed_sha256}\0{requirement.release_tag}".encode()
    )
    identity_hash = hashlib.sha256(key_material).hexdigest()
    return f"{requirement.compressed_sha256[:16]}-{identity_hash}"


def materialize_bundle(requirement: DataRequirement, root: Path) -> Path:
    """Verify and atomically select an immutable external ClinGen snapshot."""
    bundle = requirement.bundle_path
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise SnapshotUnavailableError(f"ClinGen data root must not be a symlink: {root}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise SnapshotUnavailableError(f"ClinGen data root is not a directory: {root}")
    try:
        lock_path = root / ".materialize.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            cleanup_stale_materialization_scratch(root)
            verified_fd, verified_bundle = create_verified_bundle_scratch(root)
            compressed_size = 0
            digest = hashlib.sha256()
            try:
                with os.fdopen(verified_fd, "wb") as dst:
                    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
                    source_fd = os.open(bundle, flags)
                    try:
                        source_stat = os.fstat(source_fd)
                        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                            raise SnapshotUnavailableError(
                                f"ClinGen bundle is not a private regular file: {bundle}"
                            )
                        with os.fdopen(source_fd, "rb", closefd=False) as src:
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
                        os.close(source_fd)
                if digest.hexdigest() != requirement.compressed_sha256:
                    raise SnapshotUnavailableError(
                        "ClinGen snapshot bundle failed its compressed SHA-256 integrity check"
                    )

                version_key = _materialized_version_key(requirement)
                version_dir = root / version_key
                selected = version_dir / "clingen.sqlite"
                identity_path = version_dir / "identity.json"
                if not selected.exists():
                    staging = create_staging_scratch(root, version_key)
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
                        materialized_identity = staging / "identity.json"
                        os.replace(identity_tmp, materialized_identity)
                        _fsync_regular_file_no_follow(materialized_identity)
                        _write_runtime_identity_manifest(
                            staging,
                            requirement.release_tag,
                            [staged, materialized_identity],
                        )
                        with staged.open("rb") as handle:
                            os.fsync(handle.fileno())
                        _fsync_directory(staging)
                        os.replace(staging, version_dir)
                        _fsync_directory(root)
                    finally:
                        remove_materialization_scratch(root, staging)
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
                    _write_runtime_identity_manifest(
                        version_dir,
                        requirement.release_tag,
                        [selected, identity_path],
                    )

                current_tmp = root / ".current.tmp"
                current_tmp.unlink(missing_ok=True)
                current_tmp.symlink_to(version_dir.name, target_is_directory=True)
                os.replace(current_tmp, root / "current")
                _fsync_directory(root)
                return selected
            finally:
                remove_materialization_scratch(root, verified_bundle)
    except OSError as exc:
        raise SnapshotUnavailableError(f"could not read ClinGen bundle safely: {exc}") from exc


class Store:
    """Read-only accessor for a selected external ClinGen SQLite snapshot."""

    def __init__(self, path: str | Path, *, data_root: str | Path | None = None) -> None:
        """Open a materialized SQLite ``path`` read-only and immutable.

        Raises:
            SnapshotUnavailableError: the snapshot file is absent or unreadable.
        """
        source = Path(path)
        resolved_data_root = self._resolve_data_root(Path(data_root)) if data_root else None
        self._db_path = self._resolve_db_path(source, resolved_data_root)
        self._materialized_root = self._db_path.parent
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

    @property
    def materialized_root(self) -> Path:
        """Return the immutable version directory bound when this store opened."""
        return self._materialized_root

    def _schema_version(self) -> str:
        with self.connection() as conn:
            rows = conn.execute("SELECT DISTINCT snapshot_version FROM meta").fetchall()
        versions = sorted(str(row[0]) for row in rows)
        return versions[0] if len(versions) == 1 else "unknown"

    # ------------------------------------------------------------------
    # Snapshot resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_data_root(root: Path) -> Path:
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise SnapshotUnavailableError(
                f"configured ClinGen data root is unavailable at {root}: {exc}"
            ) from exc
        if not resolved.is_dir():
            raise SnapshotUnavailableError(
                f"configured ClinGen data root is not a directory: {resolved}"
            )
        return resolved

    def _resolve_db_path(self, source: Path, data_root: Path | None) -> Path:
        """Bind one selected regular ``.sqlite`` file and reject mutable aliases."""
        if source.suffix == ".zst":
            raise SnapshotUnavailableError(
                "Compressed ClinGen bundles must be materialized by `clingen-link "
                "materialize-data`; the application only opens a selected read-only SQLite file."
            )
        if source.is_symlink():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot must not be a direct symlink: {source}. {_REFRESH_HINT}"
            )
        if not source.exists():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot not found at {source}. {_REFRESH_HINT}"
            )
        try:
            selected = source.resolve(strict=True)
        except OSError as exc:
            raise SnapshotUnavailableError(
                f"ClinGen snapshot selection failed at {source}: {exc}. {_REFRESH_HINT}"
            ) from exc
        if not selected.is_file():
            raise SnapshotUnavailableError(
                f"ClinGen snapshot is not a regular file at {selected}. {_REFRESH_HINT}"
            )
        if data_root is not None:
            try:
                selected.relative_to(data_root)
            except ValueError as exc:
                raise SnapshotUnavailableError(
                    f"selected ClinGen snapshot is outside configured data root {data_root}: "
                    f"{selected}"
                ) from exc
            if selected.parent == data_root:
                raise SnapshotUnavailableError(
                    "selected ClinGen snapshot must be inside a version directory beneath "
                    f"configured data root {data_root}"
                )
        return selected

    def _open(self) -> sqlite3.Connection:
        """Open a fresh read-only connection with row access by column name.

        ``check_same_thread=False`` lets a pooled connection be reused by any
        thread; the store's lock serialises hand-off so no two threads share a
        connection concurrently. ``immutable=1`` is safe because the ETL is the
        only writer and never touches a live snapshot in place.
        """
        uri = f"file:{self._db_path}?mode=ro&immutable=1"
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
