import json
import subprocess
from pathlib import Path

import pytest
import yaml

import hermes_model_profiles as hmp

pytestmark = pytest.mark.ci_safe


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


def test_apply_profiles_preserves_unrelated_yaml_comments(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    config = hermes_home / "profiles/zoe-coder/config.yaml"
    config.write_text(
        """# operator notes stay
model: # model selection
  # provider rationale
  provider: openrouter # keep provider note
  default: openrouter/free # keep default note
  context_length: 128000

fallback_providers: # failover order
  # fallback rationale
  - provider: openrouter
    model: openrouter/free

kept:
  # unrelated nested note
  value: true
""",
        encoding="utf-8",
    )

    hmp.apply_profiles(
        [
            {
                "name": "zoe-coder",
                "provider": "openai-codex",
                "model": "gpt-5.5-medium",
                "fallbacks": [{"provider": "openrouter", "model": "openrouter/auto"}],
            }
        ],
        actor="tester",
        confirm_paid_auto=True,
    )

    text = config.read_text(encoding="utf-8")
    assert "# operator notes stay" in text
    assert "# provider rationale" in text
    assert "provider: openai-codex # keep provider note" in text
    assert "default: gpt-5.5-medium # keep default note" in text
    assert "fallback_providers: # failover order" in text
    assert "# fallback rationale" in text
    assert "# unrelated nested note" in text
    parsed = yaml.safe_load(text)
    assert parsed["fallback_providers"] == [{"provider": "openrouter", "model": "openrouter/auto"}]


def test_apply_profiles_returns_audit_warning_after_successful_write(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)

    def fail_audit(record):
        raise OSError("audit read-only")

    monkeypatch.setattr(hmp, "_append_audit", fail_audit)

    result = hmp.apply_profiles(
        [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
        actor="tester",
    )

    data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert data["model"]["default"] == "changed/model"
    assert result["audit_warning"] == "Profiles applied, but audit append failed: audit read-only"


def test_restart_apply_blocks_when_workers_are_running(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 2)

    with pytest.raises(RuntimeError, match="worker"):
        hmp.apply_profiles(
            [{"name": "main", "provider": "openrouter", "model": "openrouter/free", "fallbacks": []}],
            actor="tester",
            restart=True,
        )


def test_restart_apply_fails_closed_when_worker_count_times_out(hermes_home, monkeypatch):
    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pgrep", timeout=5)

    monkeypatch.setattr(hmp.subprocess, "run", timeout_run)

    with pytest.raises(OSError, match="Unable to determine"):
        hmp.apply_profiles(
            [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
            actor="tester",
            restart=True,
        )

    data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert data["model"]["default"] == "openrouter/free"


def test_restart_timeout_is_reported_after_successful_apply(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)

    def timeout_restart(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=30)

    monkeypatch.setattr(hmp.subprocess, "run", timeout_restart)

    result = hmp.apply_profiles(
        [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
        actor="tester",
        restart=True,
    )

    data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    audit = json.loads((hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert data["model"]["default"] == "changed/model"
    assert result["restart"]["returncode"] == -1
    assert "timed out" in result["restart"]["stderr"]
    assert audit["restart_result"]["returncode"] == -1


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
    audit = json.loads((hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert audit["status"] == "rolled_back"


def test_apply_failure_writes_failed_audit_and_restores_written_profiles(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    original_dump = hmp._dump_yaml
    calls = {"count": 0}

    def flaky_dump(path, data):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full")
        return original_dump(path, data)

    monkeypatch.setattr(hmp, "_dump_yaml", flaky_dump)

    with pytest.raises(OSError, match="disk full"):
        hmp.apply_profiles(
            [
                {"name": "main", "provider": "openrouter", "model": "changed/main", "fallbacks": []},
                {"name": "zoe-coder", "provider": "openrouter", "model": "changed/coder", "fallbacks": []},
            ],
            actor="tester",
        )

    main = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    audit = json.loads((hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert main["model"]["default"] == "openrouter/free"
    assert audit["status"] == "failed"
    assert audit["restored"] == ["main"]


def test_apply_failure_preserves_original_error_when_audit_fails(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)

    def fail_dump(path, profile):
        raise OSError("disk full")

    def fail_audit(record):
        raise OSError("audit read-only")

    monkeypatch.setattr(hmp, "_dump_yaml", fail_dump)
    monkeypatch.setattr(hmp, "_append_audit", fail_audit)

    with pytest.raises(OSError, match="disk full"):
        hmp.apply_profiles(
            [{"name": "main", "provider": "openrouter", "model": "changed/main", "fallbacks": []}],
            actor="tester",
        )


def test_apply_failure_preserves_original_error_when_restore_fails(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    original_dump = hmp._dump_yaml
    original_copy = hmp.shutil.copy2
    calls = {"count": 0}

    def flaky_dump(path, profile):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk full")
        return original_dump(path, profile)

    def flaky_copy(src, dst):
        if "rollback/model-profiles" in str(src):
            raise OSError("restore failed")
        return original_copy(src, dst)

    monkeypatch.setattr(hmp, "_dump_yaml", flaky_dump)
    monkeypatch.setattr(hmp.shutil, "copy2", flaky_copy)

    with pytest.raises(OSError, match="disk full"):
        hmp.apply_profiles(
            [
                {"name": "main", "provider": "openrouter", "model": "changed/main", "fallbacks": []},
                {"name": "zoe-coder", "provider": "openrouter", "model": "changed/coder", "fallbacks": []},
            ],
            actor="tester",
        )

    audit = json.loads((hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert audit["status"] == "failed"
    assert audit["restore_errors"] == [{"profile": "main", "error": "restore failed"}]


def test_rollback_rejects_rollback_root(hermes_home):
    root = hmp.rollback_dir()
    root.mkdir(parents=True)

    with pytest.raises(ValueError, match="escapes"):
        hmp.rollback_profiles(str(root), actor="tester")


def test_rollback_failure_writes_failed_audit(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    result = hmp.apply_profiles(
        [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
        actor="tester",
    )

    def fail_copy(src, dst):
        raise OSError("restore failed")

    monkeypatch.setattr(hmp.shutil, "copy2", fail_copy)

    with pytest.raises(OSError, match="restore failed"):
        hmp.rollback_profiles(result["backup_dir"], actor="tester")

    audit = json.loads((hermes_home / "model-profile-audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert audit["status"] == "rollback_failed"
    assert audit["rollback_dir"] == result["backup_dir"]


def test_rollback_failure_preserves_original_error_when_audit_fails(hermes_home, monkeypatch):
    monkeypatch.setattr(hmp, "count_running_workers", lambda: 0)
    result = hmp.apply_profiles(
        [{"name": "main", "provider": "openrouter", "model": "changed/model", "fallbacks": []}],
        actor="tester",
    )

    def fail_copy(src, dst):
        raise OSError("restore failed")

    def fail_audit(record):
        raise OSError("audit read-only")

    monkeypatch.setattr(hmp.shutil, "copy2", fail_copy)
    monkeypatch.setattr(hmp, "_append_audit", fail_audit)

    with pytest.raises(OSError, match="restore failed"):
        hmp.rollback_profiles(result["backup_dir"], actor="tester")

