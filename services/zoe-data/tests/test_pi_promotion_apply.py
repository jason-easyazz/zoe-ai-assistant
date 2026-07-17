import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_promotion_apply.py"
    spec = importlib.util.spec_from_file_location("pi_promotion_apply_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _report(value="weather,reminders", requires_apply=True):
    return {
        "promotion_actions": {
            "promote_groups": ["weather"] if requires_apply else [],
            "rollback_groups": [],
            "keep_promoted_groups": ["reminders"],
            "next_promoted_groups": ["reminders", "weather"],
            "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": value},
            "requires_operator_apply": requires_apply,
        }
    }


def test_build_env_update_preserves_existing_lines_and_sorts_groups():
    module = _load_module()
    updated, meta = module.build_env_update("A=1\nZOE_PI_INTENT_PROMOTED_GROUPS=weather\nB=2\n", "timers,weather")

    assert updated == "A=1\nZOE_PI_INTENT_PROMOTED_GROUPS=timers,weather\nB=2\n"
    assert meta == {
        "key": "ZOE_PI_INTENT_PROMOTED_GROUPS",
        "old_value": "weather",
        "next_value": "timers,weather",
        "line_present": True,
        "changed": True,
    }


def test_dry_run_does_not_write_env_file(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n", encoding="utf-8")

    result = module.apply_pi_promotion_report(_report("weather"), env_path, apply_changes=False, confirm=None)

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["update"]["next_value"] == "weather"
    assert env_path.read_text(encoding="utf-8") == "A=1\n"


def test_apply_requires_confirmation(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env"

    result = module.apply_pi_promotion_report(_report("weather"), env_path, apply_changes=True, confirm=None)

    assert result["ok"] is False
    assert result["mode"] == "apply_rejected"
    assert not env_path.exists()


def test_apply_writes_only_promoted_groups_key(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=value\n", encoding="utf-8")

    result = module.apply_pi_promotion_report(
        _report("weather,reminders"),
        env_path,
        apply_changes=True,
        confirm=module.CONFIRM_TOKEN,
    )

    assert result["ok"] is True
    assert result["mode"] == "applied"
    assert env_path.read_text(encoding="utf-8") == "OTHER=value\nZOE_PI_INTENT_PROMOTED_GROUPS=reminders,weather\n"


def test_rejects_unexpected_env_keys():
    module = _load_module()
    report = _report("weather")
    report["promotion_actions"]["env"]["OPENAI_API_KEY"] = "nope"

    with pytest.raises(module.PiPromotionApplyError, match="unsupported promotion env keys"):
        module.promotion_env_value(report)


def test_cli_requires_env_file(tmp_path):
    module = _load_module()
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"promotion_report": _report("weather")}), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        module.main(["--report", str(report_path)])

    assert excinfo.value.code == 2


def test_cli_reads_full_eval_payload_and_dry_runs(tmp_path, capsys):
    module = _load_module()
    report_path = tmp_path / "report.json"
    env_path = tmp_path / ".env"
    report_path.write_text(json.dumps({"promotion_report": _report("weather")}), encoding="utf-8")

    exit_code = module.main(["--report", str(report_path), "--env-file", str(env_path)])

    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["mode"] == "dry_run"
    assert out["update"]["next_value"] == "weather"
    assert not env_path.exists()
