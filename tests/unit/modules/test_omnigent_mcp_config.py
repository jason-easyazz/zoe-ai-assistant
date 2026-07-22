"""
Regression tests for the zoe-omnigent container's MCP config.

THE REGRESSION BEING PINNED: `modules/omnigent/.mcp.json` used to give serena a
`command` + `--transport stdio`, which means the container spawns its OWN Serena
per agent session — measured at ~900 MB RSS each, on a 15.6 GB box that also
runs llama-server + Kokoro. That pressure starved the deploy gate and
contributed to llama-server CUDA-OOM crashes. The container must instead use the
HOST's shared server (serena-mcp.service) over the internal `zoe-codeintel`
network.

Three files have to keep agreeing for that to work AND stay private: the MCP
url, the compose network (pinned subnet + pinned container address), and the
bridge socket unit (ListenStream + IPAddressAllow). Drift in any one of them
either breaks code intelligence silently or reopens whole-repo read access.
"""
import ipaddress
import json
import re
from pathlib import Path

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_JSON = REPO_ROOT / "modules" / "omnigent" / ".mcp.json"
COMPOSE = REPO_ROOT / "modules" / "omnigent" / "docker-compose.module.yml"
SYSTEMD_SYSTEM = REPO_ROOT / "scripts" / "setup" / "systemd" / "system"
BRIDGE_SOCKET = SYSTEMD_SYSTEM / "serena-bridge.socket"
BRIDGE_SERVICE = SYSTEMD_SYSTEM / "serena-bridge.service"

CODEINTEL_NETWORK = "zoe-codeintel"
SHARED_SERENA_PORT = 9121


def _mcp_servers() -> dict:
    data = json.loads(MCP_JSON.read_text())
    return data["mcpServers"]


def _gateway() -> str:
    """The gateway host the omnigent MCP config points serena at."""
    url = _mcp_servers()["serena"]["url"]
    m = re.match(r"^http://([0-9.]+):(\d+)/mcp$", url)
    assert m, f"serena url is not a plain http://<ip>:<port>/mcp URL: {url!r}"
    assert int(m.group(2)) == SHARED_SERENA_PORT
    return m.group(1)


def _compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text())


def _codeintel_network() -> dict:
    return _compose()["networks"][CODEINTEL_NETWORK]


def _pinned_container_ip() -> str:
    """zoe-omnigent's fixed address on zoe-codeintel — the allowlist entry."""
    return _compose()["services"]["omnigent"]["networks"][CODEINTEL_NETWORK]["ipv4_address"]


def test_omnigent_serena_is_not_a_stdio_spawn():
    """No stdio serena in the container config — that is the 900 MB regression."""
    serena = _mcp_servers()["serena"]

    assert "command" not in serena, (
        "modules/omnigent/.mcp.json spawns its own Serena again. Each spawn is "
        "~900 MB RSS; use the shared serena-mcp.service via the zoe-codeintel "
        "bridge instead."
    )
    assert "stdio" not in json.dumps(serena)
    assert serena.get("type") == "http"


def test_omnigent_serena_points_at_the_shared_server():
    gateway = _gateway()
    # Not loopback: inside the container 127.0.0.1 is the container itself, so a
    # loopback URL here would silently mean "no code intel".
    assert not gateway.startswith("127."), (
        "the container cannot reach the host's loopback; the URL must name the "
        "zoe-codeintel gateway"
    )


def test_other_mcp_servers_are_untouched():
    servers = _mcp_servers()
    assert set(servers) == {"serena", "codebase-memory"}
    # codebase-memory is a small in-container binary from the read-only host
    # bin mount; it is deliberately NOT moved behind the bridge.
    assert servers["codebase-memory"]["command"] == "/home/zoe/.local/bin/codebase-memory-mcp"


def test_bridge_socket_listens_on_the_url_the_container_uses():
    """The socket unit and the MCP url must name the SAME address:port."""
    text = BRIDGE_SOCKET.read_text()
    listen = re.search(r"^ListenStream=(\S+)$", text, re.MULTILINE)
    assert listen, "serena-bridge.socket has no ListenStream="
    assert listen.group(1) == f"{_gateway()}:{SHARED_SERENA_PORT}"

    # FreeBind lets the socket bind before Docker has created the bridge
    # interface; without it the unit dies at boot with EADDRNOTAVAIL.
    assert re.search(r"^FreeBind=true$", text, re.MULTILINE)


def test_bridge_service_proxies_to_loopback_serena():
    """Serena itself must stay loopback-bound; the bridge is the only hop."""
    text = BRIDGE_SERVICE.read_text()
    exec_start = re.search(r"^ExecStart=(.+)$", text, re.MULTILINE)
    assert exec_start, "serena-bridge.service has no ExecStart="
    assert exec_start.group(1).strip() == (
        f"/lib/systemd/systemd-socket-proxyd 127.0.0.1:{SHARED_SERENA_PORT}"
    )


def test_bridge_socket_access_list_is_the_real_boundary():
    """The `internal` network alone does NOT scope a HOST-bound port.

    Measured 2026-07-22: traffic to a bridge GATEWAY is delivered via INPUT,
    which Docker's FORWARD isolation never sees, so a container on zoe-network
    reached a throwaway listener on a throwaway internal bridge. The
    IPAddressAllow= list is what actually keeps the shared Serena scoped to
    zoe-omnigent — deleting it silently reopens whole-repo code intelligence to
    every container on the host (and to LAN hosts with a static route).
    """
    text = BRIDGE_SOCKET.read_text()
    assert re.search(r"^IPAddressDeny=any$", text, re.MULTILINE), (
        "serena-bridge.socket lost its default-deny IP access list"
    )
    # systemd MERGES repeated IPAddressAllow= lines, so check every one of them:
    # matching only the first would let a second line quietly add another
    # container to the allowlist while this test stayed green.
    allow_lines = re.findall(r"^IPAddressAllow=(.+)$", text, re.MULTILINE)
    assert allow_lines, "serena-bridge.socket has no IPAddressAllow="
    allowed = [entry for line in allow_lines for entry in line.split()]
    assert allowed == [f"{_pinned_container_ip()}/32"], (
        "the socket's allowlist must be exactly omnigent's pinned address"
    )


def test_bridge_units_are_system_scope_not_user_scope():
    """A --user unit starts with the IP access list SILENTLY not applied.

    systemd logs "unit configures an IP firewall, but not running as root" and
    proceeds — measured. So these two live under systemd/system/, away from the
    `cp scripts/setup/systemd/*.service ~/.config/systemd/user/` install glob.
    """
    assert BRIDGE_SOCKET.parent.name == "system"
    assert BRIDGE_SERVICE.parent.name == "system"
    user_scope_copies = list(SYSTEMD_SYSTEM.parent.glob("serena-bridge.*"))
    assert user_scope_copies == []


def test_codeintel_network_is_internal_and_pinned_to_the_gateway():
    net = _codeintel_network()
    assert net["internal"] is True, (
        "zoe-codeintel must stay internal: it exists to keep whole-repo code "
        "intelligence off every other network"
    )

    configs = net["ipam"]["config"]
    assert len(configs) == 1
    # The subnet is pinned so the gateway IP is deterministic; the MCP config
    # hardcodes it.
    assert configs[0]["gateway"] == _gateway()

    # The pinned container address must live in the pinned subnet, or the
    # allowlist names an address the container can never hold.
    subnet = ipaddress.ip_network(configs[0]["subnet"])
    assert ipaddress.ip_address(_pinned_container_ip()) in subnet
    assert ipaddress.ip_address(_gateway()) in subnet

    attached = _compose()["services"]["omnigent"]["networks"]
    assert CODEINTEL_NETWORK in attached
    # cloudflared still reaches the UI at http://zoe-omnigent:6767.
    assert "zoe-network" in attached


def test_only_omnigent_is_on_the_codeintel_network():
    """One member, by construction: no other service may join it."""
    members = [
        name
        for name, svc in _compose()["services"].items()
        if CODEINTEL_NETWORK in (svc.get("networks") or {})
    ]
    assert members == ["omnigent"]
