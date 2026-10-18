"""Offline tests for scripts/maintenance/omnigent_claude_auth_mode.py.

The script flips polly's claude-sdk brain between subscription and a dedicated API key by
editing a marker-delimited `auth:` block in omnigent's config.yaml. Driven here against a
temp config through the same `apply()` the container run uses; no Docker.
"""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "omnigent_claude_auth_mode.py"
_spec = importlib.util.spec_from_file_location("omnigent_claude_auth_mode", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

SEED = """# Omnigent user configuration
host:
  host_id: host_x
providers:
  openrouter:
    default:
    - pi
    kind: gateway
"""
KEYED = {mod.KEY_VAR: "sk-ant-dummy-not-a-secret"}


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SEED)
    p.chmod(0o600)
    return str(p)


def test_api_mode_adds_a_reference_never_the_value(cfg):
    rc, _ = mod.apply("api", cfg, KEYED)
    assert rc == 0
    text = Path(cfg).read_text()
    assert "sk-ant-dummy" not in text, "config.yaml must hold the $VAR reference only"
    doc = yaml.safe_load(text)
    assert doc["auth"] == {"type": "api_key", "api_key": f"${mod.KEY_VAR}"}
    assert doc["providers"]["openrouter"]["default"] == ["pi"], "the rest of the config is untouched"
    assert doc["host"]["host_id"] == "host_x"
    assert stat.S_IMODE(Path(cfg).stat().st_mode) == 0o600


def test_api_mode_refuses_without_the_key(cfg):
    # An auth block that references an unset variable breaks EVERY omnigent launch.
    rc, msg = mod.apply("api", cfg, {})
    assert rc == 2 and mod.KEY_VAR in msg
    assert Path(cfg).read_text() == SEED, "nothing may be written when the key is missing"


def test_subscription_mode_restores_the_original_file_exactly(cfg):
    mod.apply("api", cfg, KEYED)
    rc, _ = mod.apply("subscription", cfg, KEYED)
    assert rc == 0
    assert Path(cfg).read_text() == SEED


def test_modes_are_idempotent(cfg):
    mod.apply("api", cfg, KEYED)
    once = Path(cfg).read_text()
    assert mod.apply("api", cfg, KEYED) == (0, "already in api mode")
    assert Path(cfg).read_text() == once
    mod.apply("subscription", cfg, KEYED)
    assert mod.apply("subscription", cfg, KEYED)[1] == "already in subscription mode"


def test_never_clobbers_an_unmanaged_auth_block(cfg):
    Path(cfg).write_text(SEED + "auth:\n  type: databricks\n  profile: P\n")
    before = Path(cfg).read_text()
    rc, msg = mod.apply("api", cfg, KEYED)
    assert rc == 2 and "unmanaged" in msg
    assert Path(cfg).read_text() == before
    assert mod.apply("subscription", cfg, KEYED)[0] == 2


def test_status_reports_mode_and_key_presence_without_the_value(cfg):
    rc, msg = mod.apply("status", cfg, {})
    assert (rc, "mode=subscription" in msg, f"{mod.KEY_VAR}=NOT set" in msg) == (0, True, True)
    mod.apply("api", cfg, KEYED)
    _, msg = mod.apply("status", cfg, KEYED)
    assert "mode=api" in msg and f"{mod.KEY_VAR}=set" in msg and "dummy" not in msg


def test_missing_config_is_created_in_api_mode_and_removed_cleanly(tmp_path):
    p = str(tmp_path / "config.yaml")
    assert mod.apply("api", p, KEYED)[0] == 0
    assert yaml.safe_load(Path(p).read_text())["auth"]["type"] == "api_key"
    assert mod.apply("subscription", p, KEYED)[0] == 0
    assert Path(p).read_text() == ""
