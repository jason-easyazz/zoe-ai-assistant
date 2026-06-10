from __future__ import annotations

import importlib.util
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_helper():
    root = Path(__file__).resolve().parents[3]
    helper_path = root / "scripts/maintenance/run_harness_followup_test.py"
    spec = importlib.util.spec_from_file_location("run_harness_followup_test", helper_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_harness_followup_helper_maps_blockers_to_exact_tests():
    helper = _load_helper()

    assert helper.test_target_for_blocker("ITERATION_BUDGET").endswith(
        "test_record_blocked_multica_chain_creates_iteration_budget_followup"
    )
    assert helper.test_target_for_blocker("IMPLEMENT_BUDGET").endswith(
        "test_record_blocked_multica_chain_creates_budget_followup_once"
    )
    assert helper.test_target_for_blocker("IMPLEMENT_HANDOFF_DRIFT").endswith(
        "test_record_blocked_multica_chain_creates_budget_followup_once"
    )
    assert helper.test_target_for_blocker("PROTOCOL_VIOLATION").endswith(
        "test_record_blocked_multica_chain_creates_protocol_followup"
    )
    assert helper.test_target_for_blocker("unknown") == "services/zoe-data/tests/test_main_multica_poll.py"


def test_short_harness_followup_alias_delegates_to_helper(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    alias_path = root / "scripts/maintenance/r"
    calls = []
    fake_helper = types.SimpleNamespace(main=lambda argv=None: calls.append(argv) or 17)
    monkeypatch.setitem(sys.modules, "run_harness_followup_test", fake_helper)
    loader = SourceFileLoader("harness_followup_short_alias", str(alias_path))
    spec = importlib.util.spec_from_loader("harness_followup_short_alias", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.run_harness_followup_test.main(["IMPLEMENT_BUDGET"]) == 17
    assert calls == [["IMPLEMENT_BUDGET"]]
