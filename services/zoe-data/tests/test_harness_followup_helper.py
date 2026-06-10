from __future__ import annotations

import importlib.util
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
