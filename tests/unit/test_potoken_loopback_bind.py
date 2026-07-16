"""Guards that the YouTube PO-token generator stays bound to loopback.

`ytmusic-potoken` (bgutil) mints YouTube proof-of-origin tokens against Zoe's
YouTube session and has NO authentication. Published as a bare "4416:4416" it
listens on 0.0.0.0 (+ [::]), so every host on the LAN could mint tokens.
Verified against the live box before the fix: `curl http://<lan-ip>:4416/ping`
returned 200 from the LAN.

Every real consumer is host-local, so loopback costs nothing:
  - music-assistant is network_mode:host and reaches it via
    po_token_server_url = http://localhost:4416 (filled in by zoe-data's
    music_service.py, override ZOE_YTMUSIC_POTOKEN_URL).
  - the container healthcheck runs inside the container against its own
    127.0.0.1 and never touches the published port.

`pytest.importorskip("yaml")` rather than a hard import: PyYAML reaches the slim
GitHub lane only transitively (via uvicorn[standard]), and per tests/AGENTS.md a
tests/unit module must at least COLLECT under the slim dep list. The Jetson
catch-all lane runs this directory unconditionally, so the guard always runs.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.modules.yml"


def _potoken_service() -> dict:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    services = data.get("services") or {}
    assert "ytmusic-potoken" in services, (
        "ytmusic-potoken service missing from docker-compose.modules.yml"
    )
    return services["ytmusic-potoken"]


def test_potoken_port_is_published_on_loopback_only():
    ports = _potoken_service().get("ports") or []
    assert ports, "ytmusic-potoken must publish its port explicitly"

    for entry in ports:
        # Short syntax ("127.0.0.1:4416:4416") or long syntax ({host_ip: ...}).
        if isinstance(entry, dict):
            host_ip = entry.get("host_ip")
        else:
            parts = str(entry).split(":")
            # host_ip is only present with 3+ colon-separated parts.
            host_ip = parts[0] if len(parts) >= 3 else None

        assert host_ip == "127.0.0.1", (
            f"ytmusic-potoken port {entry!r} is not bound to loopback. The "
            "PO-token generator is unauthenticated; publishing it on 0.0.0.0 "
            "exposes it to the whole LAN. Use '127.0.0.1:4416:4416'."
        )


def test_potoken_healthcheck_targets_container_local_loopback():
    """The healthcheck must not depend on the published host port."""
    test = (_potoken_service().get("healthcheck") or {}).get("test") or []
    joined = " ".join(str(t) for t in test)
    assert "127.0.0.1:4416" in joined, (
        "healthcheck should hit the container's own 127.0.0.1:4416 so it stays "
        f"independent of how the port is published; got: {joined!r}"
    )
