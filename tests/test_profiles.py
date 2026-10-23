import pytest

from slicer_mcp.models import SlicerError


def test_recursive_inheritance(catalog):
    result = catalog.resolve("test:vendor/process/standard.json")
    assert result["layer_height"] == "0.20"
    assert result["name"] == "standard"
    assert "inherits" not in result


def test_cycle_and_missing_parent(catalog):
    profile = catalog.get("test:vendor/process/base.json")
    profile.data["inherits"] = "standard"
    with pytest.raises(SlicerError, match="Cyclic"):
        catalog.resolve(profile.id)
    profile.data["inherits"] = "missing"
    with pytest.raises(SlicerError, match="missing or ambiguous"):
        catalog.resolve(profile.id)


def test_unknown_profile_path(catalog):
    with pytest.raises(SlicerError, match="Unknown profile"):
        catalog.resolve("test:../../other.json")
