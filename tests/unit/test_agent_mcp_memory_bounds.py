"""Pins how agent configs launch the two code-intel MCP servers.

Both are per-agent memory hazards on a 15.6GB box whose brain + TTS hold ~9GB,
but they need OPPOSITE fixes, and conflating them is how the bug survived:

* Serena speaks streamable-http, so the fleet shares ONE server and no agent
  config may spawn its own (`command`).
* codebase-memory-mcp 0.8.1 is stdio-only — its `--port` is the UI graph viewer,
  not a transport — so per-agent spawning is forced by the tool. It must
  therefore go through the memory-capping launcher, never the raw binary.

Measured 2026-08-02, with both regressions live at once: seven private Serenas
(~1.4GB) from two Codex sessions, and fourteen codebase-memory instances
(~1.27GB, one 473MB, one resident 2.3 days).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
MCP_JSON = ROOT / ".mcp.json"
CODEX_TOML = ROOT / ".codex" / "config.toml"
CAPPED = "scripts/maintenance/codebase_memory_capped.sh"
OMNIGENT_CODEX_TOML = ROOT / "modules" / "omnigent" / "codex-mcp.toml"
OMNIGENT_MCP_JSON = ROOT / "modules" / "omnigent" / ".mcp.json"
SHARED_SERENA = "http://127.0.0.1:9121/mcp"


def _load_toml_servers(path: Path) -> dict:
    try:
        import tomllib as toml_mod  # py3.11+
    except ModuleNotFoundError:
        import tomli as toml_mod  # py3.10
    with path.open("rb") as fh:
        return toml_mod.load(fh).get("mcp_servers", {})


def _codex_servers() -> dict:
    return _load_toml_servers(CODEX_TOML)


def _claude_servers() -> dict:
    return json.loads(MCP_JSON.read_text()).get("mcpServers", {})


@pytest.fixture(params=["claude", "codex"])
def servers(request):
    return _claude_servers() if request.param == "claude" else _codex_servers()


def test_serena_attaches_to_the_shared_server(servers):
    """A `command` entry silently reintroduces per-agent stdio spawning, and
    nothing alarms: the shared unit stays healthy and the health check passes."""
    entry = servers["serena"]
    assert "command" not in entry, (
        "serena must attach to the shared server by url, never spawn per-agent"
    )
    assert entry.get("url") == SHARED_SERENA


def test_codebase_memory_goes_through_the_capping_launcher(servers):
    """It cannot be consolidated (stdio-only), so each spawn must be bounded."""
    entry = servers["codebase-memory"]
    assert entry.get("command", "").endswith(CAPPED), (
        "codebase-memory must launch via codebase_memory_capped.sh so each "
        "per-agent spawn is memory-capped; the raw binary is unbounded"
    )


def test_no_config_references_the_raw_codebase_memory_binary():
    for path in (MCP_JSON, CODEX_TOML):
        assert "local/bin/codebase-memory-mcp" not in path.read_text(), (
            f"{path.name} still launches the raw uncapped binary"
        )


def test_capping_launcher_exists_and_is_executable():
    script = ROOT / CAPPED
    assert script.exists(), f"{CAPPED} is referenced by agent configs but missing"
    assert script.stat().st_mode & 0o111, f"{CAPPED} is not executable"


def test_launcher_caps_swap_as_well_as_rss():
    """Capping RSS without swap just relocates a leak into swap — measured on
    Serena, which leaked 2.1GB into swap under a MemoryMax that looked correct."""
    body = (ROOT / CAPPED).read_text()
    for prop in ("MemoryHigh", "MemoryMax", "MemorySwapMax"):
        assert prop in body, f"{CAPPED} must set {prop}"


def test_launcher_falls_back_rather_than_failing_closed():
    """No systemd user bus (container, no session) must degrade to an uncapped
    launch, not break code-intel entirely."""
    body = (ROOT / CAPPED).read_text()
    assert "launching uncapped" in body


def test_omnigent_container_codex_serena_attaches_by_url():
    """The zoe-omnigent container's Codex config is the SAME hazard one hop away:
    /root/.codex/config.toml lives in a volume nothing tracked used to own, was
    hand-patched live on 2026-09-25 (two private ~700MB Serenas measured), and a
    volume reset silently brought the stdio spawn back. The tracked template
    (baked into the image, seeded by the entrypoint) must attach by url — the
    zoe-codeintel gateway, not loopback, which inside the container is the
    container itself — and must agree with the container's Claude config."""
    entry = _load_toml_servers(OMNIGENT_CODEX_TOML)["serena"]
    assert "command" not in entry, (
        "modules/omnigent/codex-mcp.toml spawns a per-session Serena again"
    )
    claude_entry = json.loads(OMNIGENT_MCP_JSON.read_text())["mcpServers"]["serena"]
    assert entry.get("url") == claude_entry["url"]
    assert not entry["url"].startswith("http://127."), entry["url"]
