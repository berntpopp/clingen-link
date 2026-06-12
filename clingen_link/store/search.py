"""FTS5 query escaping + pagination helpers for the store query layer.

Split from ``queries.py`` to keep both files under the 600-LOC cap. Two
concerns live here:

* :func:`fts_match` turns arbitrary user text into a safe FTS5 ``MATCH``
  expression. Bare user input can contain FTS5 operators (``"``, ``*``, ``:``,
  ``(`` ...) that would either error or silently change the query. We allow an
  explicit column filter of the form ``col:"value"`` (used internally for exact
  gene/HGVS token matches) and otherwise quote every bare token as a phrase and
  AND them together.
* :func:`paginate` clamps a 1-based ``page`` / ``size`` into a bounded
  ``(limit, offset)`` so no query can request an unbounded scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens used for bare-text matching: alphanumerics plus a few id-safe chars.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+")
# An internal exact-column filter, e.g. ``gene:"BRCA1"`` or ``hgvs:"NM_..."``.
_COLUMN_FILTER_RE = re.compile(r'^(?P<col>[a-z_]+):"(?P<val>[^"]+)"$')

# Pagination bounds (mirrors settings.MAX_PAGE_SIZE without importing settings so
# the helper stays pure/testable). The service layer may pass its own size.
_MIN_SIZE = 1
_MAX_SIZE = 100
_DEFAULT_SIZE = 25


@dataclass(frozen=True)
class Page:
    """A clamped pagination window."""

    page: int
    size: int

    @property
    def offset(self) -> int:
        """Zero-based row offset for the ``LIMIT ? OFFSET ?`` clause."""
        return (self.page - 1) * self.size


def paginate(page: int, size: int) -> Page:
    """Clamp ``page`` (1-based) and ``size`` into a bounded :class:`Page`."""
    safe_page = page if page >= 1 else 1
    if size < _MIN_SIZE:
        safe_size = _DEFAULT_SIZE
    else:
        safe_size = min(size, _MAX_SIZE)
    return Page(page=safe_page, size=safe_size)


def fts_match(text: str) -> str | None:
    """Return a safe FTS5 ``MATCH`` expression for ``text``, or ``None``.

    ``None`` means "no usable search tokens" so the caller can short-circuit to
    an empty result set rather than running a malformed MATCH.
    """
    raw = text.strip()
    if not raw:
        return None
    # Honour an internal exact column filter unchanged (already quoted).
    col_filter = _COLUMN_FILTER_RE.match(raw)
    if col_filter is not None:
        col = col_filter.group("col")
        val = col_filter.group("val").replace('"', '""')
        return f'{col}:"{val}"'
    tokens = _TOKEN_RE.findall(raw)
    if not tokens:
        return None
    # Quote every token as a phrase and AND them together. Quoting neutralises
    # FTS5 operator characters and makes the match deterministic.
    return " AND ".join(f'"{tok}"' for tok in tokens)
