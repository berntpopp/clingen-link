# tests/unit/mcp/test_cspec_resources.py
from clingen_link.mcp import resources


def test_cspec_dataset_label_present() -> None:
    assert "cspec" in resources._DATASET_LABELS
    assert "Criteria Specification" in resources._DATASET_LABELS["cspec"]["label"]


def test_cspec_tools_listed() -> None:
    for tool in ("list_cspecs", "get_cspec", "get_cspec_criterion", "search_cspec"):
        assert tool in resources._TOOLS
