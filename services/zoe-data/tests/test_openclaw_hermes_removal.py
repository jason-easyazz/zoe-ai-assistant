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

_REPO = ROOT.parents[1]
_DEAD_PORT_RE = re.compile(r"(?<![\w.])(8642|18789)(?!\w)")
# Anything that makes a nearby port literal a CONNECTION target rather than prose: a URL
# scheme / host, a socket or HTTP-client call, a health-port key, a shell port listing.
_NET_CONTEXT_RE = re.compile(
    r"https?://|wss?://|localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal"
    r"|create_connection|open_connection|socket\b|httpx|requests\.|urlopen|aiohttp"
    r"|\bcurl\b|/dev/tcp|\bnc\b|connect\(|base_url|health_port|_URL\b|proxy_pass"
    r"|\bss -|\.get\(|\.post\(|\.stream\(|_check_port|_port_open",
    re.IGNORECASE,
)
_WINDOW = 3  # lines either side: a multi-line call keeps the port and the client apart
_SCANNED_SUFFIXES = (".py", ".sh", ".ts", ".js", ".yml", ".yaml")
# Not scanned: documentation, vendored bundles, and this test itself (it must name the ports).
_SKIP_PREFIXES = ("docs/", "services/zoe-ui/dist/lib/", "services/zoe-ui/node_modules/")
_SKIP_FILES = ("services/zoe-data/tests/test_openclaw_hermes_removal.py",)

# Files that still carry a port-literal-near-network-context hit, each with the reason it is
# tolerated. The scan also fails when an entry no longer hits, so this list can only shrink.
_ALLOWED = {
    "scripts/maintenance/zoe_ground_truth.sh": "inverse check: reports if a Hermes gateway is unexpectedly LISTENING on :8642 (ss, no connect)",
    "services/zoe-data/agents_registry.yml": "legacy peer-agent registry: /agent/registry lists base_url but never dials it (status comes from _RUNTIME_HEALTH, which no longer holds these agents); follow-up: drop the retired entries",
    "services/zoe-data/chat_hermes_stream.py": "REAL but dormant: HERMES_API_URL default for the retired Hermes stream; reached only via the __ESCALATE_HERMES__ signal, which cannot fire (zoe_agent._hermes_available() is False); follow-up: delete the module and its shim test",
    "services/zoe-data/openclaw_maintenance.py": "REAL: fetch_gateway_status() GETs :18789/health and reports 'offline' (live, fails closed to offline; follow-up: remove with the /api/system openclaw update surface)",
    "services/zoe-data/routers/system.py": "REAL: _hermes_review_proposal() POSTs to :8642 on evolution-approve; it fails open on connection refused (live; removing it changes the approve flow, so it is left for its own PR)",
    "services/zoe-data/zoe_agent.py": "docstring/comment prose and a self-summary string (not a connection)",
    "services/zoe-data/tests/test_nginx_security_headers_helper.py": "nginx fixture text for the security-header helper",
}


def _dead_port_hits(text: str) -> list[tuple[int, str]]:
    """Lines naming a retired port as a connection target (multi-line aware).

    A literal on a pure comment line is documentation, not a call, so it never counts;
    every other occurrence counts when a network context appears within `_WINDOW` lines.
    """
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if not _DEAD_PORT_RE.search(line) or line.lstrip().startswith(("#", "//", "*")):
            continue
        window = "\n".join(lines[max(0, i - _WINDOW) : i + _WINDOW + 1])
        if _NET_CONTEXT_RE.search(window):
            hits.append((i + 1, line.strip()))
    return hits


def _tracked_scan_files() -> list[str]:
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        f for f in out
        if f.endswith(_SCANNED_SUFFIXES)
        and not f.startswith(_SKIP_PREFIXES)
        and f not in _SKIP_FILES
    ]


def test_dead_port_scan_catches_a_multiline_probe():
    # Negative control: the scan MUST flag these, including a call whose port sits on a
    # different line from the client, or the repo-wide test below proves nothing.
    multiline = (
        "async with httpx.AsyncClient() as client:\n"
        "    r = await client.get(\n"
        '        f"{BASE}/health",\n'
        "        params={},\n"
        "    )\n"
        "BASE = os.environ.get('HERMES_URL',\n"
        "                      '127.0.0.1:8642')\n"
    )
    assert _dead_port_hits(multiline)
    assert _dead_port_hits('sock = socket.create_connection(("localhost", 18789))')
    assert _dead_port_hits("curl -fsS http://127.0.0.1:18789/health || true")
    assert _dead_port_hits("ss -tln | grep -c ':8642 '")
    # ...and must not flag prose, comments, or look-alike numbers.
    assert not _dead_port_hits("# Hermes (:8642) is retired, see the migration doc")
    assert not _dead_port_hits('NOTE = "ticket 18789 closed"')
    assert not _dead_port_hits('PORT = 864200\nhttp://localhost:3579')


def test_no_probe_of_dead_hermes_or_openclaw_ports_repo_wide():
    found: dict[str, list[tuple[int, str]]] = {}
    for rel in _tracked_scan_files():
        try:
            text = (_REPO / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits = _dead_port_hits(text)
        if hits:
            found[rel] = hits
    unexplained = {f: h for f, h in found.items() if f not in _ALLOWED}
    assert not unexplained, (
        "retired gateway port (:8642 Hermes / :18789 OpenClaw) used as a connection target; "
        "remove it or add an allowlist entry WITH a reason: "
        + "; ".join(f"{f}:{h[0][0]} {h[0][1][:80]}" for f, h in unexplained.items())
    )
    stale = sorted(set(_ALLOWED) - set(found))
    assert not stale, f"allowlist entries that no longer hit (delete them): {stale}"


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
