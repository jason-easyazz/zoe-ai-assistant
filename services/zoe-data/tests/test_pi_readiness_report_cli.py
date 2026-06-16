import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_readiness_report.py"
    spec = importlib.util.spec_from_file_location("pi_readiness_report_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def _report(state="shadow_collecting"):
    return {
        "report_kind": "zoe_pi_readiness_report",
        "state": state,
        "summary": {
            "state": state,
            "ready": state != "configuration_blocked",
            "promotion_ready_groups": [],
        },
        "hybrid": {"ready": state != "configuration_blocked"},
        "evidence": {"labeled_sample_count": 4},
        "next_actions": [
            {
                "kind": "continue_shadow_mode",
                "priority": "p2",
                "detail": "Keep Pi in shadow mode.",
            }
        ],
    }


def test_cli_prints_full_report_json(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "pi_readiness_report", lambda: _report())

    exit_code = module.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["report_kind"] == "zoe_pi_readiness_report"
    assert payload["state"] == "shadow_collecting"
    assert payload["evidence"] == {"labeled_sample_count": 4}


def test_cli_can_write_compact_summary_to_file(monkeypatch, tmp_path, capsys):
    module = _load_module()
    output_path = tmp_path / "reports" / "pi-readiness.json"
    monkeypatch.setattr(module, "pi_readiness_report", lambda: _report("promotion_apply_ready"))

    exit_code = module.main(["--summary", "--output", str(output_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "report_kind": "zoe_pi_readiness_report",
        "state": "promotion_apply_ready",
        "summary": {
            "state": "promotion_apply_ready",
            "ready": True,
            "promotion_ready_groups": [],
        },
        "next_actions": _report("promotion_apply_ready")["next_actions"],
    }


def test_cli_fail_when_blocked_exits_nonzero_only_for_blocked_states(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "pi_readiness_report", lambda: _report("configuration_blocked"))
    assert module.main(["--fail-when-blocked"]) == 2

    monkeypatch.setattr(module, "pi_readiness_report", lambda: _report("rollback_required"))
    assert module.main(["--fail-when-blocked"]) == 2

    monkeypatch.setattr(module, "pi_readiness_report", lambda: _report("collect_more_evidence"))
