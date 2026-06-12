"""Read-only SQLite store + per-domain query layer (serve-time read path).

The :class:`~clingen_link.store.db.Store` opens the bundled snapshot read-only
(decompressing the shipped ``.zst`` once if needed) and resolves free-text gene
input to a canonical symbol. :mod:`clingen_link.store.queries` holds the pure
per-domain query functions that turn the snapshot into raw ``dict`` rows.
"""

from __future__ import annotations

from .db import Store

__all__ = ["Store"]
