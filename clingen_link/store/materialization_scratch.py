"""Collision-free, no-follow scratch handling for snapshot materialization."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

_SCRATCH_SUFFIX = r"(?:[0-9]{1,20}|[A-Za-z0-9_-]{6,64})"
_VERIFIED_NAME = re.compile(rf"^\.verified-bundle-{_SCRATCH_SUFFIX}$")
_VERSION_KEY = r"[0-9a-f]{16}-[0-9a-f]{64}"
_STAGING_NAME = re.compile(rf"^\.{_VERSION_KEY}\.staging-{_SCRATCH_SUFFIX}$")
_LEGACY_STAGING_NAME = re.compile(rf"^\.[0-9a-f]{{16}}\.staging-{_SCRATCH_SUFFIX}$")


def _is_scratch_name(name: str) -> bool:
    return bool(
        _VERIFIED_NAME.fullmatch(name)
        or _STAGING_NAME.fullmatch(name)
        or _LEGACY_STAGING_NAME.fullmatch(name)
    )


def _remove_directory_entry_no_follow(parent_fd: int, name: str) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        with os.scandir(directory_fd) as entries:
            names = sorted(entry.name for entry in entries)
        for child_name in names:
            mode = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                _remove_directory_entry_no_follow(directory_fd, child_name)
            else:
                os.unlink(child_name, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def remove_materialization_scratch(root: Path, candidate: Path) -> None:
    """Remove one exact direct-child scratch path without following symlinks."""
    if candidate.parent != root or not _is_scratch_name(candidate.name):
        raise ValueError(f"refusing to remove non-scratch materialization path: {candidate}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, flags)
    try:
        try:
            mode = os.stat(candidate.name, dir_fd=root_fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return
        if stat.S_ISDIR(mode):
            _remove_directory_entry_no_follow(root_fd, candidate.name)
        else:
            os.unlink(candidate.name, dir_fd=root_fd)
    finally:
        os.close(root_fd)


def cleanup_stale_materialization_scratch(root: Path) -> None:
    """Remove only known direct-child verified/staging scratch names."""
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries if _is_scratch_name(entry.name))
    for name in names:
        remove_materialization_scratch(root, root / name)


def create_verified_bundle_scratch(root: Path) -> tuple[int, Path]:
    """Create one collision-free verified-bundle scratch file beneath ``root``."""
    fd, name = tempfile.mkstemp(prefix=".verified-bundle-", dir=root)
    return fd, Path(name)


def create_staging_scratch(root: Path, version_key: str) -> Path:
    """Create one collision-free staging directory for an exact version key."""
    if not re.fullmatch(_VERSION_KEY, version_key):
        raise ValueError(f"invalid materialized version key: {version_key}")
    return Path(tempfile.mkdtemp(prefix=f".{version_key}.staging-", dir=root))
