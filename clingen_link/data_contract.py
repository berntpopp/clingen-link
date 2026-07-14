"""The snapshot data contract — one version string, imported by everything that pins it.

The ETL stamps ``SNAPSHOT_SCHEMA_VERSION`` into every ``meta`` row; the deployment pins
``SNAPSHOT_SCHEMA_SEMVER`` (``CLINGEN_LINK_DATA_SCHEMA_*``); and ``store.db._verify_schema``
refuses to materialize a bundle whose stamp does not match the pin.

Keeping the two forms in one leaf module is deliberate: as two hardcoded constants they
could silently disagree, and the failure mode of *that* is a server that happily serves a
bundle built under a different contract.

**2.0.0** — dosage score columns hold ClinGen's CODE (``0``-``3``, ``30``, ``40``, or NULL),
never its description text (issue #46). A 1.x bundle stores prose in those columns, so
``search_dosage(haplo_score="30")`` would silently return zero rows: refusing to load it is
the point.
"""

from __future__ import annotations

# Stamped into meta.snapshot_version by the ETL.
SNAPSHOT_SCHEMA_VERSION = "2"

# The deployment pin. `_verify_schema` normalizes a bare "2" to "2.0.0" before comparing.
SNAPSHOT_SCHEMA_SEMVER = f"{SNAPSHOT_SCHEMA_VERSION}.0.0"
