"""Every port Zoe publishes from Docker is loopback-only unless it is on the LAN ledger.

A bare ``"5432:5432"`` (or ``8007:8007``) publishes on ``0.0.0.0`` + ``[::]``. Until
2026-09-27 that exposed two services to every host on the LAN:

  - ``zoe-database`` (PostgreSQL, ``pg_hba`` = ``host all all all scram-sha-256``), so any
    LAN client could attempt a login against a server carrying login-gated CVSS 8.8 bugs;
  - ``homeassistant-mcp-bridge``, which has NO inbound auth at all, so any LAN client
    could drive Home Assistant through it.

Neither had a LAN consumer: host-native zoe-data uses ``localhost``/``127.0.0.1`` and
containers use the ``zoe-network`` service names. So the rule is inverted into a ledger:
a published port is loopback-bound, or it is listed in ``LAN_LEDGER`` with the reason it
must be reachable from the LAN. Adding a new LAN-facing port is then a reviewed decision
in this file instead of a YAML default nobody looked at.

The second guard is the coupling that made the fix non-trivial: a container that reaches
a host port through ``host.docker.internal`` (the docker0 gateway, NOT loopback) cannot
reach a ``127.0.0.1`` publish. ``multica-backend`` did exactly that for Postgres, so the
loopback bind would have silently cut Multica off. Any such pairing is red here.

Negative controls (run 2026-09-27, each turned this file red, then reverted):
  - ``"127.0.0.1:5432:5432"`` -> ``"5432:5432"`` and -> ``"0.0.0.0:5432:5432"``
  - ``"127.0.0.1:8007:8007"`` -> ``8007:8007``
  - multica ``DATABASE_URL`` host ``zoe-database`` -> ``host.docker.internal``
  - a ledger entry for a service/port that no longer exists (stale ledger)
  - pgvector image back to the floating ``pgvector/pgvector:pg17`` tag
  - ``--no-deps`` dropped from one deploy.yml ``compose up`` / from system_updates.py

``pytest.importorskip("yaml")``: PyYAML reaches the slim GitHub lane only transitively,
and per tests/AGENTS.md a tests/unit module must at least COLLECT there. The Jetson
catch-all lane runs this directory unconditionally.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.modules.yml",
    "modules/omnigent/docker-compose.module.yml",
)

# (compose file, service, container port[/proto]) -> why it must be reachable from the LAN.
# Keep reasons concrete. "not reviewed" entries are pre-existing exposures this guard
# grandfathers; narrowing them is a separate, deliberate change.
LAN_LEDGER: dict[tuple[str, str, str], str] = {
    ("docker-compose.yml", "zoe-ui", "80"): "front door: browsers + Pi kiosk (nginx)",
    ("docker-compose.yml", "zoe-ui", "443"): "front door: browsers + Pi kiosk (nginx, TLS)",
    ("docker-compose.yml", "zoe-ui", "18790"): (
        "OpenClaw Control UI TLS port (nginx) — not reviewed; OpenClaw is being retired"
    ),
    ("docker-compose.yml", "zoe-auth", "8002"): (
        "OIDC issuer at zoe.local:8002 (Omnigent/browser OIDC path) — not reviewed; "
        "nginx also proxies it internally via zoe-auth:8002"
    ),
    ("docker-compose.yml", "homeassistant", "8123"): "Home Assistant UI + companion apps",
    ("docker-compose.yml", "livekit", "7880"): "LiveKit signalling for LAN browsers",
    ("docker-compose.yml", "livekit", "50000-50200/udp"): "LiveKit WebRTC media (UDP)",
    ("docker-compose.yml", "keeper", "8787"): (
        "keeper calendar sync (opt-in profile) — not reviewed"
    ),
    ("docker-compose.modules.yml", "multica-web", "3000"): (
        "Multica board UI at http://zoe.local:3000 (FRONTEND_ORIGIN)"
    ),
    ("docker-compose.modules.yml", "multica-backend", "8080"): (
        "Multica API for the board UI / CLI on the LAN — not reviewed"
    ),
}


def _load(rel: str) -> dict:
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8")) or {}


def _parse_port(entry) -> tuple[str | None, str, str]:
    """Return (host_ip, published, target[/proto]) for a compose `ports:` entry."""
    if isinstance(entry, dict):  # long syntax
        proto = entry.get("protocol")
        target = str(entry.get("target"))
        if proto and proto != "tcp":
            target = f"{target}/{proto}"
        return entry.get("host_ip"), str(entry.get("published", "")), target
    if not isinstance(entry, str):
        # An unquoted `22:22` is a YAML 1.1 base-60 INT (1342) — refuse to guess.
        raise AssertionError(f"port entry {entry!r} is not a string; quote it")
    text, _, proto = entry.partition("/")
    suffix = f"/{proto}" if proto else ""
    m = re.fullmatch(r"\[([0-9a-fA-F:]+)\]:([^:]+):([^:]+)", text)  # [::1]:h:c
    if m:
        return m.group(1), m.group(2), m.group(3) + suffix
    parts = text.split(":")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2] + suffix
    if len(parts) == 2:
        return None, parts[0], parts[1] + suffix
    return None, "", parts[0] + suffix  # container port only: ephemeral host port


def _published_ports():
    for rel in COMPOSE_FILES:
        for name, svc in (_load(rel).get("services") or {}).items():
            for entry in (svc or {}).get("ports") or []:
                host_ip, published, target = _parse_port(entry)
                yield rel, name, entry, host_ip, published, target


def _is_loopback(host_ip: str | None) -> bool:
    if not host_ip:
        return False
    try:
        return ipaddress.ip_address(host_ip.strip("[]")).is_loopback
    except ValueError:
        return False


def test_parser_reads_the_bind_forms_compose_accepts():
    assert _parse_port("5432:5432") == (None, "5432", "5432")
    assert _parse_port("127.0.0.1:5432:5432") == ("127.0.0.1", "5432", "5432")
    assert _parse_port("0.0.0.0:5432:5432") == ("0.0.0.0", "5432", "5432")
    assert _parse_port("[::1]:5432:5432") == ("::1", "5432", "5432")
    assert _parse_port("50000-50200:50000-50200/udp") == (
        None, "50000-50200", "50000-50200/udp",
    )
    assert _parse_port({"target": 80, "published": 8080, "host_ip": "127.0.0.1"}) == (
        "127.0.0.1", "8080", "80",
    )
    assert not _is_loopback(None) and not _is_loopback("0.0.0.0")
    assert _is_loopback("127.0.0.1") and _is_loopback("::1")


def test_every_published_port_is_loopback_or_on_the_lan_ledger():
    offenders = []
    for rel, name, entry, host_ip, _published, target in _published_ports():
        if (rel, name, target) in LAN_LEDGER:
            continue
        if not _is_loopback(host_ip):
            offenders.append(f"{rel}: {name} publishes {entry!r}")
    assert not offenders, (
        "These ports are published beyond loopback (a bare 'H:C' binds 0.0.0.0 + [::]) "
        "and are not on LAN_LEDGER. Bind them as '127.0.0.1:H:C', or — if a LAN host "
        "genuinely needs them — add a ledger entry saying who:\n  " + "\n  ".join(offenders)
    )


def test_database_and_ha_bridge_are_loopback_only():
    """Named explicitly so a ledger edit cannot quietly re-expose them."""
    wanted = {("zoe-database", "5432"), ("homeassistant-mcp-bridge", "8007")}
    seen = set()
    for rel, name, entry, host_ip, _published, target in _published_ports():
        if (name, target) in wanted:
            seen.add((name, target))
            assert rel == "docker-compose.yml"
            assert (rel, name, target) not in LAN_LEDGER
            assert host_ip == "127.0.0.1", f"{name} must publish on 127.0.0.1, got {entry!r}"
    assert seen == wanted, f"expected published ports {sorted(wanted)}, found {sorted(seen)}"


def test_lan_ledger_has_no_stale_entries():
    live = {(rel, name, target) for rel, name, _e, _h, _p, target in _published_ports()}
    stale = sorted(set(LAN_LEDGER) - live)
    assert not stale, f"LAN_LEDGER entries with no matching published port: {stale}"


def _env_values(svc: dict) -> list[str]:
    env = svc.get("environment") or []
    if isinstance(env, dict):
        return [str(v) for v in env.values() if v is not None]
    return [str(e) for e in env]


def test_no_container_reaches_a_loopback_publish_through_the_docker_gateway():
    loopback_host_ports = {
        published
        for _rel, _name, _entry, host_ip, published, _target in _published_ports()
        if _is_loopback(host_ip) and published
    }
    assert {"5432", "8007"} <= loopback_host_ports
    offenders = []
    for rel in COMPOSE_FILES:
        for name, svc in (_load(rel).get("services") or {}).items():
            for value in _env_values(svc or {}):
                for port in re.findall(r"host\.docker\.internal:(\d+)", value):
                    if port in loopback_host_ports:
                        # Report the host:port only — env values can carry credentials.
                        offenders.append(f"{rel}: {name} -> host.docker.internal:{port}")
    assert not offenders, (
        "host.docker.internal is the docker0 gateway, not loopback, so it cannot reach a "
        "127.0.0.1 publish. Use the zoe-network service name instead "
        "(e.g. zoe-database:5432):\n  " + "\n  ".join(offenders)
    )


def test_multica_reaches_postgres_by_service_name():
    svc = _load("docker-compose.modules.yml")["services"]["multica-backend"]
    url = (svc.get("environment") or {}).get("DATABASE_URL", "")
    assert re.search(r"@zoe-database:5432/multica\b", url), (
        "multica-backend must reach Postgres over zoe-network as zoe-database:5432"
    )


def test_database_image_is_digest_pinned_on_pg17():
    image = _load("docker-compose.yml")["services"]["zoe-database"]["image"]
    assert image.startswith("pgvector/pgvector:"), image
    assert "-pg17@sha256:" in image, (
        "zoe-database must be pinned by digest to a pgvector *-pg17 tag (a floating tag "
        "is how the box sat on PostgreSQL 17.10; a new MAJOR needs dump/restore)"
    )


def test_automated_compose_ups_never_converge_the_database():
    """CD and the in-app updater must not recreate Postgres as a side effect.

    `docker compose up -d zoe-auth` also converges zoe-auth's depends_on target, so any
    edit to the zoe-database block (this PR's image bump + loopback bind) would have been
    applied by the NEXT deploy, without a pg_dumpall and without recreating
    multica-backend (a different compose file). Measured with `docker compose --dry-run`.
    """
    deploy = (REPO / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    ups = re.findall(r"^\s*docker compose up\b[^\n]*", deploy, flags=re.M)
    assert ups, "deploy.yml no longer runs `docker compose up` — update this guard"
    for cmd in ups:
        assert "--no-deps" in cmd, f"deploy.yml compose up without --no-deps: {cmd.strip()!r}"

    updater = (REPO / "services/zoe-data/system_updates.py").read_text(encoding="utf-8")
    m = re.search(r'"docker", "compose", "-f", COMPOSE_FILE,\s*"up",([^\n]*)', updater)
    assert m, "system_updates.py compose-up call not found — update this guard"
    assert '"--no-deps"' in m.group(1), "system_updates.py compose up must pass --no-deps"
