"""Explicit real Azure gate. NEVER silently skipped; not part of default pytest."""
import os
from pathlib import Path

from config import SampleError, load_config
from runner import cases, quiet_dependencies, run


def test_azure_pinned_fixture_twice():
    if os.environ.get("VECTOR_SEARCH_LIVE_TEST") != "1":
        raise SampleError("live gate requires VECTOR_SEARCH_LIVE_TEST=1; no network calls made")
    quiet_dependencies()
    c = load_config()
    comparison = (Path(__file__).resolve().parents[1] / "src/compare_all.py").exists()
    selected = cases(comparison)
    first = run(c, selected, pinned=True)
    second = run(c, selected, pinned=True)
    assert first["failed"] == 0, first
    assert second["failed"] == 0, second
    assert first["succeeded"] == second["succeeded"] == len(selected)
    for row in second["cases"]:
        assert row["hits"] == 5
        assert row["seed"] == dict(total=50, inserted=0, existing=50)
        assert row["quality"] == "exact-metric-top-k-verified"
        if row["metric"] in ("COS", "L2"):
            assert row["self_match"] is True
