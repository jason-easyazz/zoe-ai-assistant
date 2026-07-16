import os
from pathlib import Path

import pytest

from pi_runtime_probe import PI_INSTALL_COMMAND, PiRuntimeConfig, PiRuntimeConfigError, probe_pi_runtime

pytestmark = pytest.mark.ci_safe


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


def test_default_probe_discovers_nvm_pi_install(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bindir = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    bindir.mkdir(parents=True)
    versions = {"node": "v22.22.0", "npm": "10.9.4"}
    for command in ("node", "npm"):
        path = bindir / command
        path.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
        path.chmod(0o755)
    pi = bindir / "pi"
    pi.write_text("#!/bin/sh\necho 0.79.3\n", encoding="utf-8")
    pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")

    result = probe_pi_runtime()

    assert result.status == "available_execution_disabled"
    assert result.tools == {"node": str(bindir / "node"), "npm": str(bindir / "npm"), "pi": str(pi)}
    assert result.tool_versions["node"] == "v22.22.0"
    assert result.tool_versions["npm"] == "10.9.4"
    assert result.to_dict()["requirements"]["node"]["status"] == "ok"



def test_default_probe_ignores_openclaw_pi_without_standalone_install(tmp_path, monkeypatch):
    home = tmp_path / "home"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    openclaw_bin = home / ".openclaw" / "npm" / "node_modules" / ".bin"
    nvm_bin.mkdir(parents=True)
    openclaw_bin.mkdir(parents=True)
    versions = {"node": "v22.22.0", "npm": "10.9.4"}
    for command in ("node", "npm"):
        runtime = nvm_bin / command
        runtime.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
        runtime.chmod(0o755)
    bundled_pi = openclaw_bin / "pi"
    bundled_pi.write_text("#!/bin/sh\necho 0.74.0\n", encoding="utf-8")
    bundled_pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")

    result = probe_pi_runtime()

    assert result.status == "missing_pi"
    assert result.tools == {"node": str(nvm_bin / "node"), "npm": str(nvm_bin / "npm"), "pi": None}
    assert result.tool_versions["node"] == "v22.22.0"
    assert result.to_dict()["requirements"]["node"]["status"] == "ok"


def test_default_probe_prefers_standalone_nvm_pi_over_openclaw_bundle(tmp_path, monkeypatch):
    home = tmp_path / "home"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    openclaw_bin = home / ".openclaw" / "npm" / "node_modules" / ".bin"
    nvm_bin.mkdir(parents=True)
    openclaw_bin.mkdir(parents=True)
    versions = {"node": "v22.22.0", "npm": "10.9.4"}
    for command in ("node", "npm"):
        runtime = nvm_bin / command
        runtime.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
        runtime.chmod(0o755)
    standalone_pi = nvm_bin / "pi"
    standalone_pi.write_text("#!/bin/sh\necho 0.79.3\n", encoding="utf-8")
    standalone_pi.chmod(0o755)
    bundled_pi = openclaw_bin / "pi"
    bundled_pi.write_text("#!/bin/sh\necho 0.74.0\n", encoding="utf-8")
    bundled_pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")

    result = probe_pi_runtime()

    assert result.status == "available_execution_disabled"
    assert result.tools["node"] == str(nvm_bin / "node")
    assert result.tools["npm"] == str(nvm_bin / "npm")
    assert result.tools["pi"] == str(standalone_pi)
    assert result.tool_versions["node"] == "v22.22.0"


def test_default_probe_prefers_nvm_node_when_system_node_lacks_pi(tmp_path, monkeypatch):
    home = tmp_path / "home"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    system_bin = tmp_path / "system-bin"
    nvm_bin.mkdir(parents=True)
    system_bin.mkdir()
    for bindir, versions in (
        (system_bin, {"node": "v20.11.1", "npm": "10.2.4"}),
        (nvm_bin, {"node": "v22.22.0", "npm": "10.9.4"}),
    ):
        for command in ("node", "npm"):
            path = bindir / command
            path.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
            path.chmod(0o755)
    pi = nvm_bin / "pi"
    pi.write_text("#!/bin/sh\necho 0.79.3\n", encoding="utf-8")
    pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")

    result = probe_pi_runtime()

    assert result.status == "available_execution_disabled"
    assert result.tools["node"] == str(nvm_bin / "node")
    assert result.tools["npm"] == str(nvm_bin / "npm")
    assert result.tools["pi"] == str(pi)
    assert result.tool_versions["node"] == "v22.22.0"


def test_default_probe_moves_existing_nvm_bin_to_front_when_system_node_lacks_pi(tmp_path, monkeypatch):
    home = tmp_path / "home"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    system_bin = tmp_path / "system-bin"
    nvm_bin.mkdir(parents=True)
    system_bin.mkdir()
    for bindir, versions in (
        (system_bin, {"node": "v20.11.1", "npm": "10.2.4"}),
        (nvm_bin, {"node": "v22.22.0", "npm": "10.9.4"}),
    ):
        for command in ("node", "npm"):
            path = bindir / command
            path.write_text(f"#!/bin/sh\necho {versions[command]}\n", encoding="utf-8")
            path.chmod(0o755)
    pi = nvm_bin / "pi"
    pi.write_text("#!/bin/sh\necho 0.79.3\n", encoding="utf-8")
    pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{system_bin}{os.pathsep}{nvm_bin}")
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")

    result = probe_pi_runtime()

    assert result.status == "available_execution_disabled"
    assert result.tools["node"] == str(nvm_bin / "node")
    assert result.tools["npm"] == str(nvm_bin / "npm")
    assert result.tools["pi"] == str(pi)
    assert result.tool_versions["node"] == "v22.22.0"


def test_default_probe_selects_latest_nvm_version_with_configured_pi_command(tmp_path, monkeypatch):
    home = tmp_path / "home"
    older_bin = home / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"
    newer_bin = home / ".nvm" / "versions" / "node" / "v23.1.0" / "bin"
    older_bin.mkdir(parents=True)
    newer_bin.mkdir(parents=True)
    for bindir, version in ((older_bin, "v22.22.0"), (newer_bin, "v23.1.0")):
        node = bindir / "node"
        node.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        node.chmod(0o755)
        npm = bindir / "npm"
        npm.write_text("#!/bin/sh\necho 10.9.4\n", encoding="utf-8")
        npm.chmod(0o755)
    custom_pi = older_bin / "zoe-pi"
    custom_pi.write_text("#!/bin/sh\necho 0.79.3\n", encoding="utf-8")
    custom_pi.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ZOE_PI_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_COMMAND", "zoe-pi")

    result = probe_pi_runtime()

    assert result.status == "available_execution_disabled"
    assert result.tools["node"] == str(older_bin / "node")
    assert result.tools["npm"] == str(older_bin / "npm")
    assert result.tools["pi"] == str(custom_pi)
    assert result.tool_versions["node"] == "v22.22.0"


def test_probe_reports_unknown_when_node_version_is_unreadable(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    node = bindir / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    npm = bindir / "npm"
    npm.write_text("#!/bin/sh\necho 10.9.3\n", encoding="utf-8")
    npm.chmod(0o755)
    pi = bindir / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pi.chmod(0o755)

    result = probe_pi_runtime(env={"PATH": str(bindir), "ZOE_PI_ENABLED": "true"})

    assert result.status == "available_execution_disabled"
    assert result.tools["node"] == str(node)
    assert result.tool_versions["node"] is None
    assert result.to_dict()["requirements"]["node"] == {
        "minimum": "22.19.0",
        "detected": None,
        "status": "unknown",
    }


def test_probe_respects_configured_version_timeout(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    node = bindir / "node"
    node.write_text("#!/bin/sh\n/bin/sleep 0.2\necho v22.19.0\n", encoding="utf-8")
    node.chmod(0o755)
    npm = bindir / "npm"
    npm.write_text("#!/bin/sh\necho 10.9.3\n", encoding="utf-8")
    npm.chmod(0o755)
    pi = bindir / "pi"
    pi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pi.chmod(0o755)

    result = probe_pi_runtime(
        env={"PATH": str(bindir), "ZOE_PI_ENABLED": "true", "ZOE_PI_TIMEOUT_SECONDS": "0.05"}
    )

    assert result.status == "available_execution_disabled"
    assert result.tool_versions["node"] is None
    assert result.to_dict()["requirements"]["node"]["status"] == "unknown"


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
