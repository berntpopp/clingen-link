"""HTML / whitespace sanitizers + obsolescence detection for ClinGen free-text fields.

ClinGen's gene-validity export embeds presentation markup (e.g.
``<span class="badge">Obsolete Term</span>``) inside ``disease_name``. Left intact it propagates
verbatim into the ``recommended_citation`` (which the citation contract says to paste as-is) and is
an unsanitized passthrough surface. These pure helpers strip tags, unescape entities, collapse
whitespace, and surface obsolescence as a structured boolean (assessment M1).
"""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OBSOLETE_RE = re.compile(r"obsolete", re.IGNORECASE)


def strip_html(value: str | None) -> str:
    """Remove tags, unescape HTML entities, and collapse whitespace. ``None`` → ``""``."""
    if not value:
        return ""
    no_tags = _TAG_RE.sub(" ", value)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def is_obsolete_label(value: str | None) -> bool:
    """True when a disease label carries the ClinGen/MONDO 'obsolete' marker."""
    if not value:
        return False
    return bool(_OBSOLETE_RE.search(value))
