"""B3 (2026-10-18): the OpenClaw / Hermes removal stays removed.

OpenClaw and Hermes are retired (`docs/architecture/multica-executor-migration.md`
section 5; `~/.openclaw` and `~/.hermes` were deleted live 2026-10-18). These tests pin the
bits B3 deleted so a future edit cannot quietly resurrect a probe of a dead port or an
env gate on the live chat path:

* the OpenClaw REST router / manager / proactive trigger are gone;
* nothing probes the dead ports (:8642 Hermes, :18789 OpenClaw);
* the HERMES_FAST_PATH / JETSON_AGENT_MODE / OPENCLAW_* gates no longer steer the chat
  path or boot warmup (the live behaviour they used to select is now the code default);
* the runtime-health dict, the A2A card and `/runtimes` report only what exists.

Pure source/attribute inspection - no DB, no network, no model loads.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.ci_safe


def _src(rel: str) -> str:
    return (ROOT / rel).read_text()


# -- the deleted modules stay deleted -------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    ["routers.openclaw", "openclaw_manager", "proactive.triggers.openclaw_trigger"],
)
def test_deleted_modules_do_not_exist(module: str):
    assert importlib.util.find_spec(module) is None


def test_main_does_not_mount_or_register_openclaw_surfaces():
    main_src = _src("main.py")
    for needle in ("routers.openclaw", "openclaw_router", "OpenClawTrigger", "openclaw_trigger"):
        assert needle not in main_src, f"main.py still references {needle}"


def test_triggers_package_does_not_export_openclaw_trigger():
    import proactive.triggers as triggers

    assert not hasattr(triggers, "OpenClawTrigger")
    assert "OpenClawTrigger" not in triggers.__all__


# -- no probe of a dead port ----------------------------------------------------------------

_PROBED_FILES = ("main.py", "routers/system.py", "mcp_server.py", "zoe_agent.py", "routers/chat.py")
# A probe is a connect / port-check / health GET that names either port. Prose that still
# mentions the ports (the soul text, the unreachable-by-default review URL) is not a probe.
_PROBE_RE = re.compile(r"(create_connection|open_connection|_check_port|_port_open|client\.get\(|client\.post\()[^\n]*\b(8642|18789)\b")


@pytest.mark.parametrize("rel", _PROBED_FILES)
def test_no_probe_of_dead_hermes_or_openclaw_ports(rel: str):
    assert not _PROBE_RE.search(_src(rel)), f"{rel} still probes a retired gateway port"


def test_runtime_health_tracks_only_the_local_llm():
    from main import _RUNTIME_HEALTH

    assert set(_RUNTIME_HEALTH) == {"local_llm"}


def test_hermes_is_never_an_escalation_target(monkeypatch):
    import zoe_agent

    # Even with every legacy opt-in set, there is nothing to escalate to.
    monkeypatch.setenv("ZOE_HERMES_AUTO_ESCALATE", "true")
    monkeypatch.setenv("HERMES_FAST_PATH", "true")
    assert zoe_agent._hermes_available() is False
    names = {t["function"]["name"] for t in zoe_agent._build_tools({"discovery"})}
    assert "escalate_to_hermes" not in names


# -- the env gates are gone; the live behaviour is the default ------------------------------

_GATE_ENVS = (
    "HERMES_FAST_PATH",
    "JETSON_AGENT_MODE",
    "OPENCLAW_GUARDED_AUTO",
    "OPENCLAW_ALL_TOOLS_ENABLED",
    "OPENCLAW_GATEWAY_URL",
    "ZOE_OPENCLAW_GW",
)


@pytest.mark.parametrize("rel", ["routers/chat.py", "main.py", "mcp_server.py"])
def test_no_env_gate_reads_in_chat_boot_or_mcp(rel: str):
    src = _src(rel)
    for name in _GATE_ENVS:
        # A module may EXPLAIN the removal in a comment or docstring, but must not READ the
        # variable: no environ/getenv expression names it.
        read = re.search(r"(environ|getenv|env_str|env_bool|env_int)[^\n]*" + re.escape(name), src)
        assert read is None, f"{rel} reads {name}: {read.group(0) if read else ''}"


def test_chat_lane_constants_are_pinned_to_the_live_values():
    from routers import chat

    assert chat._USE_ZOE_AGENT is True
    assert chat._INTENT_FAST_PATH_ENABLED is True
    assert chat._USE_LOCAL_BRAIN is True
    for gone in ("_GUARDED_AUTO", "_ALL_TOOLS_ENABLED", "_ZOE_AGENT_MODE", "_JETSON_AGENT_MODE", "_OPENCLAW_GW"):
        assert not hasattr(chat, gone), f"chat.{gone} should be deleted"


def test_active_agents_lists_only_the_local_model():
    import mcp_server

    assert mcp_server._active_agents_list() == ["Jetson Agent (Gemma 4 GPU)"]


# -- the A2A card / runtimes report only what exists ----------------------------------------


def test_agent_card_and_runtimes_do_not_advertise_retired_tiers():
    src = _src("routers/system.py")
    assert '_RUNTIME_HEALTH.get("hermes"' not in src
    assert '_RUNTIME_HEALTH.get("openclaw"' not in src
    assert '"openclaw_gateway"' not in src
    assert "openclaw_manual_fallback" not in src


def test_browser_broker_takes_no_gateway_argument():
    import inspect

    import browser_broker

    assert not inspect.signature(browser_broker.create_default_browser_broker).parameters
