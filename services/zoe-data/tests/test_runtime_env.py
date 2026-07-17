import pytest
import os

import runtime_env

pytestmark = pytest.mark.ci_safe


def test_bootstrap_runtime_env_loads_hermes_key_from_service_env(monkeypatch, tmp_path):
    env_file = tmp_path / "zoe-data.env"
    env_file.write_text("HERMES_API_KEY=test-bootstrap-key\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(env_file),))
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)

    runtime_env.bootstrap_runtime_env()

    assert os.environ.get("HERMES_API_KEY") == "test-bootstrap-key"


def test_real_environment_overrides_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MULTICA_BASE_URL=http://fixture:8080\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(env_file),))
    monkeypatch.setenv("MULTICA_BASE_URL", "http://real:9090")
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)

    runtime_env.bootstrap_runtime_env()

    assert os.environ.get("MULTICA_BASE_URL") == "http://real:9090"


def test_bootstrap_runtime_env_skips_non_bootstrap_keys(monkeypatch, tmp_path):
    env_file = tmp_path / "zoe-data.env"
    env_file.write_text(
        "HERMES_API_KEY=test-bootstrap-key\nDEBUG=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(env_file),))
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)

    runtime_env.bootstrap_runtime_env()

    assert os.environ.get("HERMES_API_KEY") == "test-bootstrap-key"
    assert "DEBUG" not in os.environ
