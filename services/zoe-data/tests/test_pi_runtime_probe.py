import os
from pathlib import Path

import pytest

from pi_runtime_probe import PI_INSTALL_COMMAND, PiRuntimeConfig, PiRuntimeConfigError, probe_pi_runtime


def test_pi_runtime_defaults_are_disabled_and_offline_only():
    config = PiRuntimeConfig.from_env({})

    assert config.enabled is False
    assert config.allow_execution is False
    assert config.offline_only is True
    assert config.local_model_required is True
    assert config.local_model_configured is False
    assert config.command == "pi"


def test_empty_env_does_not_fall_through_to_process_environment(monkeypatch):
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_COMMAND", "process-pi")
    monkeypatch.setenv("PATH", "")

    isolated_config = PiRuntimeConfig.from_env({})
    isolated_probe = probe_pi_runtime(env={})
    process_config = PiRuntimeConfig.from_env()

    assert isolated_config.enabled is False
    assert isolated_config.command == "pi"
    assert isolated_probe.status == "disabled"
    assert isolated_probe.tools["pi"] is None
    assert process_config.enabled is True
    assert process_config.command == "process-pi"


def test_disabled_probe_is_acceptable_even_when_tools_are_missing(monkeypatch):
    monkeypatch.setenv("PATH", "")

    result = probe_pi_runtime(env={"PATH": ""})

    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["acceptable"] is True
    assert payload["status"] == "disabled"
    assert payload["reason"] == "ZOE_PI_ENABLED is false"


def test_enabled_probe_reports_missing_node_before_pi(monkeypatch):
    monkeypatch.setenv("PATH", "")

    result = probe_pi_runtime(env={"ZOE_PI_ENABLED": "true", "PATH": ""})

    assert result.status == "missing_node"
    assert result.acceptable is False
    assert result.reason == "node is required before Pi can run"


def test_probe_reports_current_pi_install_requirements_when_disabled(monkeypatch):
    monkeypatch.setenv("PATH", "")

    payload = probe_pi_runtime(env={"PATH": ""}).to_dict()

    assert payload["requirements"]["node"] == {"minimum": "22.19.0", "detected": None, "status": "missing"}
    assert payload["requirements"]["pi"]["package"] == "@earendil-works/pi-coding-agent"
    assert payload["install_plan"]["install_command"] == PI_INSTALL_COMMAND
    assert payload["install_plan"]["requires_multica_approval"] is True
    assert payload["install_plan"]["probe_executes_pi"] is False


def test_allow_execution_requires_enabled():
    with pytest.raises(PiRuntimeConfigError, match="ALLOW_EXECUTION requires"):
        PiRuntimeConfig.from_env({"ZOE_PI_ALLOW_EXECUTION": "true"}).validate()


def test_execution_requires_local_model_when_offline_only():
    with pytest.raises(PiRuntimeConfigError, match="LOCAL_MODEL_CONFIGURED"):
        PiRuntimeConfig.from_env(
            {
                "ZOE_PI_ENABLED": "true",
                "ZOE_PI_ALLOW_EXECUTION": "true",
            }
        ).validate()


def test_execution_cannot_disable_offline_requirement():
    with pytest.raises(PiRuntimeConfigError, match="OFFLINE_ONLY"):
        PiRuntimeConfig.from_env(
            {
                "ZOE_PI_ENABLED": "true",
                "ZOE_PI_ALLOW_EXECUTION": "true",
                "ZOE_PI_OFFLINE_ONLY": "false",
                "ZOE_PI_LOCAL_MODEL_CONFIGURED": "true",
            }
        ).validate()


def test_execution_cannot_disable_local_model_requirement():
    with pytest.raises(PiRuntimeConfigError, match="LOCAL_MODEL_REQUIRED"):
        PiRuntimeConfig.from_env(
            {
                "ZOE_PI_ENABLED": "true",
                "ZOE_PI_ALLOW_EXECUTION": "true",
                "ZOE_PI_LOCAL_MODEL_REQUIRED": "false",
                "ZOE_PI_LOCAL_MODEL_CONFIGURED": "true",
            }
        ).validate()


def test_execution_can_be_configured_with_explicit_local_model_flag(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for command in ("node", "npm", "pi"):
        path = bindir / command
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    env = {
        "PATH": str(bindir),
        "ZOE_PI_ENABLED": "true",
        "ZOE_PI_ALLOW_EXECUTION": "true",
        "ZOE_PI_LOCAL_MODEL_CONFIGURED": "true",
    }
    result = probe_pi_runtime(env=env)

    assert result.ok is True
    assert result.acceptable is True
    assert result.status == "available"
    assert result.tools["pi"] == str(bindir / "pi")


def test_probe_blocks_enabled_pi_when_node_version_is_too_old(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    versions = {"node": "v20.11.1", "npm": "10.2.4"}
    for command in ("node", "npm"):
        path = bindir / command
        path.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
        path.chmod(0o755)
    pi = bindir / "pi"
    pi_marker = tmp_path / "pi-ran"
    pi.write_text(f"#!/bin/sh\necho should-not-be-executed > {pi_marker}\n", encoding="utf-8")
    pi.chmod(0o755)

    result = probe_pi_runtime(env={"PATH": str(bindir), "ZOE_PI_ENABLED": "true"})

    assert result.status == "node_version_too_old"
    assert result.acceptable is False
    assert result.tool_versions == {"node": "v20.11.1", "npm": "10.2.4", "pi": None}
    assert result.to_dict()["requirements"]["node"] == {
        "minimum": "22.19.0",
        "detected": "v20.11.1",
        "status": "too_old",
    }
    assert not pi_marker.exists()


def test_probe_accepts_enabled_pi_with_supported_node_version(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    versions = {"node": "v22.19.0", "npm": "10.9.3"}
    for command in ("node", "npm"):
        path = bindir / command
        path.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
        path.chmod(0o755)
    pi = bindir / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pi.chmod(0o755)

    result = probe_pi_runtime(env={"PATH": str(bindir), "ZOE_PI_ENABLED": "true"})

    assert result.status == "available_execution_disabled"
    assert result.acceptable is True
    assert result.tool_versions["node"] == "v22.19.0"
    assert result.to_dict()["requirements"]["node"]["status"] == "ok"


def test_disabled_runtime_does_not_surface_agent_files(tmp_path):
    agent_dir = tmp_path / ".pi" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "explorer.md").write_text(
        "---\nname: explorer\ndescription: Fast codebase exploration\nmodel: local/gemma\n---\nPrompt body\n",
        encoding="utf-8",
    )

    result = probe_pi_runtime(env={"ZOE_PI_CWD": str(tmp_path)})

    assert result.status == "disabled"
    assert result.agent_files == ()


def test_enabled_runtime_detects_agent_files(tmp_path):
    agent_dir = tmp_path / ".pi" / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "explorer.md").write_text(
        "---\nname: explorer\ndescription: Fast codebase exploration\nmodel: local/gemma\n---\nPrompt body\n",
        encoding="utf-8",
    )

    result = probe_pi_runtime(env={"ZOE_PI_ENABLED": "true", "ZOE_PI_CWD": str(tmp_path), "PATH": ""})

    assert result.status == "missing_node"
    assert result.agent_files == (
        {
            "path": str(agent_dir / "explorer.md"),
            "name": "explorer",
            "description": "Fast codebase exploration",
            "model": "local/gemma",
        },
    )


def test_malformed_boolean_env_reports_misconfigured():
    result = probe_pi_runtime(env={"ZOE_PI_ENABLED": "enabled"})

    assert result.status == "misconfigured"
    assert result.acceptable is False
    assert result.config["enabled"] == "enabled"
    assert "Unrecognized boolean" in (result.reason or "")


def test_timeout_must_be_positive():
    result = probe_pi_runtime(env={"ZOE_PI_TIMEOUT_SECONDS": "0"})

    assert result.status == "misconfigured"
    assert result.config["timeout_seconds"] == 0.0
    assert "positive" in (result.reason or "")


def test_probe_uses_current_process_path_when_env_omits_path():
    result = probe_pi_runtime(env={})

    assert set(result.tools) == {"node", "npm", "pi"}
    assert result.config["cwd"] == "/home/zoe/assistant"
    assert os.environ.get("PATH") is not None
