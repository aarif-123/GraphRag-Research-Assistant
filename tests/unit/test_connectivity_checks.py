from __future__ import annotations

import importlib.util
from pathlib import Path

module_path = Path(__file__).resolve().parents[2] / "tests" / "test_connectivity.py"
spec = importlib.util.spec_from_file_location("connectivity_check_script", module_path)
connectivity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connectivity)


def test_database_checks_are_skipped_when_retrieval_is_frozen(monkeypatch):
    monkeypatch.setattr(connectivity, "RETRIEVAL_FROZEN", True)
    monkeypatch.setitem(connectivity.results["supabase"], "status", "UNTESTED")
    monkeypatch.setitem(connectivity.results["neo4j"], "status", "UNTESTED")

    connectivity.test_supabase()
    connectivity.test_neo4j()

    assert connectivity.results["supabase"]["status"] == "SKIPPED"
    assert connectivity.results["neo4j"]["status"] == "SKIPPED"
