"""clingen-link - MCP server grounding gene/disease/variant questions in ClinGen."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clingen-link")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
