"""Live HTTP drill-down layer (httpx.AsyncClient).

The snapshot covers search and list paths; this package fetches single records
live for ERepo variant ACMG detail and actionability SEPIO documents.
:class:`~clingen_link.api.base_client.BaseClient` is the resilience layer
(bounded concurrency + jittered retry + typed faults) and
:class:`~clingen_link.api.clingen_client.ClingenClient` adds the ClinGen
endpoint methods.
"""

from __future__ import annotations

from .base_client import BaseClient
from .clingen_client import ClingenClient

__all__ = ["BaseClient", "ClingenClient"]
