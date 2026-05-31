import json
from pathlib import Path

import pytest
import yaml

import hermes_model_profiles as hmp


def _write_config(path: Path, provider: str = "openrouter", model: str = "openrouter/free") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "model": {"provider": provider, "default": model, "context_length": 128000},
                "fallback_providers": [{"provider": "openrouter", "model": "openrouter/free"}],
                "kept": {"value": True},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for rel in hmp.PROFILE_PATHS.values():
        _write_config(tmp_path / rel)
    return tmp_path


def test_list_profiles_reads_existing_host_configs(hermes_home):
    profiles = hmp.list_profiles()

    assert [p["name"] for p in profiles] == ["main", "zoe-planner", "zoe-coder", "zoe-reviewer"]
    assert profiles[0]["provider"] == "openrouter"
    assert profiles[0]["fallbacks"] == [{"provider": "openrouter", "model": "openrouter/free"}]


def test_validate_rejects_raw_secret_or_unknown_fields(hermes_home):
    with pytest.raises(ValueError, match="unsupported fields"):
        hmp.validate_profiles(
            [
                {
                    "name": "main",
                    "provider": "openrouter",
                    "model": "openrouter/free",
                    "api_key": "secret",
                }
            ]
        )


def test_openrouter_auto_requires_confirmation(hermes_home):
    payload = [{"name": "main", "provider": "openrouter", "model": "openrouter/auto", "fallbacks": []}]

    with pytest.raises(ValueError, match="requires confirmation"):
        hmp.validate_profiles(payload)

    assert hmp.validate_profiles(payload, confirm_paid_auto=True)["paid_auto_profiles"] == ["main"]


def test_apply_profiles_writes_atomically_and_keeps_other_yaml(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)

    result = hmp.apply_profiles(
        [
            {
                "name": "zoe-coder",
                "provider": "openrouter",
                "model": "deepseek/deepseek-chat-v3.1",
                "fallbacks": [{"provider": "openrouter", "model": "openrouter/free"}],
            }
        ],
        actor="tester",
    )

    updated = yaml.safe_load((hermes_home / "profiles/zoe-coder/config.yaml").read_text(encoding="utf-8"))
    assert updated["model"]["default"] == "deepseek/deepseek-chat-v3.1"
    assert updated["kept"] == {"value": True}
    assert Path(result["backup_dir"]).exists()
    audit = (hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8")
    assert json.loads(audit.splitlines()[-1])["actor"] == "tester"


def test_restart_apply_blocks_when_workers_are_running(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 2)

    with pytest.raises(RuntimeError, match="worker"):
        hmp.apply_profiles(
            [{"name": "main", "provider": "openrouter", "model": "openrouter/free", "fallbacks": []}],
            actor="tester",
            restart=True,
        )


def test_rollback_restores_latest_backup(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    result = hmp.apply_profiles(
        [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
        actor="tester",
    )

    data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert data["model"]["default"] == "changed/model"

    restored = hmp.rollback_profiles(result["backup_dir"], actor="tester")
    data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert restored["restored"] == ["main"]
    assert data["model"]["default"] == "openrouter/free"

