"""The Jetson host firewall closes what nothing on the LAN needs and never closes what the household uses (2026-11-03).

``scripts/setup/jetson/firewall/zoe-host-fw.nft`` is applied by the OWNER (docs/knowledge/host-firewall-2026-11-03.md);
no agent loads it. This pins its decisions so an edit cannot quietly cut the household off or reopen a closed port:

- the household's ports (22 SSH, 80/443 nginx, 8123 Home Assistant, 5353 mDNS, Music Assistant's 8095/8097) are never in
  a closed set;
- the closed sets hold exactly the reviewed ports, and every closed docker-published port is also closed on INPUT (the
  IPv6 path through docker's userland proxy);
- loopback and the docker bridges are accepted BEFORE any drop (nginx, Home Assistant and Multica reach zoe-data :8000
  over the bridges - a drop ahead of those accepts would take the whole UI down);
- policy stays accept, the table never flushes the ruleset, and the file carries no ``counter`` / ``ct`` expression (this
  Tegra kernel rejects both, so the file would fail to load at boot).

Also pinned here: the tracked zoe-data unit never loads the retired agent's env file (``~/.hermes/.env``) - on the live
box that line let a revoked bot token override the valid one for a week of failed ops alerts.

Negative controls (run 2026-11-03, each turned this file red, then reverted): 443 added to ``closed_tcp``; the
``iifname "br-*" accept`` line moved below the drops; ``policy accept`` -> ``policy drop``; 8002 removed from ``closed_tcp``
only; a ``counter`` appended to a rule.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
RULESET = REPO / "scripts" / "setup" / "jetson" / "firewall" / "zoe-host-fw.nft"
UNIT = REPO / "scripts" / "setup" / "jetson" / "firewall" / "zoe-host-fw.service"

HOUSEHOLD_TCP = {22, 80, 443, 8123, 8095, 8097}
HOUSEHOLD_UDP = {5353}
CLOSED_TCP = {111, 139, 445, 631, 3000, 3579, 3582, 8000, 8002, 8080, 9121}
CLOSED_UDP = {111, 631}
CLOSED_PUBLISHED = {139, 445, 3000, 8002, 8080}


def _rules() -> str:
    return "\n".join(line.split("#", 1)[0] for line in RULESET.read_text(encoding="utf-8").splitlines())


def _set(name: str) -> set[int]:
    m = re.search(r"set " + name + r" \{[^}]*elements = \{([^}]*)\}", _rules(), re.S)
    assert m, f"set {name} missing"
    return {int(x) for x in m.group(1).replace(",", " ").split()}


def _chain(name: str) -> list[str]:
    m = re.search(r"chain " + name + r" \{(.*?)\n\t\}", _rules(), re.S)
    assert m, f"chain {name} missing"
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def test_closed_sets_are_exactly_the_reviewed_ports():
    assert _set("closed_tcp") == CLOSED_TCP
    assert _set("closed_udp") == CLOSED_UDP
    assert _set("closed_published_tcp") == CLOSED_PUBLISHED


def test_household_ports_are_never_closed():
    assert not (HOUSEHOLD_TCP & (_set("closed_tcp") | _set("closed_published_tcp")))
    assert not (HOUSEHOLD_UDP & _set("closed_udp"))


def test_every_closed_published_port_is_closed_on_input_too():
    # docker's userland proxy answers [::]:port on the host, so IPv6 to a published port arrives on INPUT
    assert _set("closed_published_tcp") <= _set("closed_tcp")


@pytest.mark.parametrize("chain", ["input", "forward"])
def test_loopback_and_bridges_are_accepted_before_any_drop(chain):
    rules = _chain(chain)
    assert "policy accept" in rules[0]
    first_drop = next(i for i, r in enumerate(rules) if r.endswith("drop"))
    accepts = rules[1:first_drop]
    assert 'iifname "docker0" accept' in accepts and 'iifname "br-*" accept' in accepts
    if chain == "input":
        assert 'iif "lo" accept' in accepts
    assert all(r.endswith("drop") for r in rules[first_drop:])
    # every drop goes through a reviewed set: a literal `tcp dport 443 drop` would dodge the set checks above
    assert all(re.fullmatch(r"(tcp|udp) dport @closed_\w+ drop", r) for r in rules[first_drop:]), rules[first_drop:]


def test_own_table_only_and_loadable_on_this_kernel():
    body = _rules()
    assert "flush ruleset" not in body
    assert re.search(r"^table inet zoe_host_fw\n+delete table inet zoe_host_fw$", body, re.M)
    assert not re.search(r"\bcounter\b|\bct\b", body), "this Tegra kernel rejects counter/ct: the file would not load"


def test_unit_loads_the_installed_ruleset_and_stops_cleanly():
    unit = UNIT.read_text(encoding="utf-8")
    assert "ExecStart=/usr/sbin/nft -f /etc/zoe-host-fw/ruleset.nft" in unit
    assert "ExecStop=-/usr/sbin/nft delete table inet zoe_host_fw" in unit
    assert "ConditionPathExists=/etc/zoe-host-fw/ruleset.nft" in unit


def test_zoe_data_unit_never_loads_the_retired_agent_env():
    units = [REPO / "scripts" / "setup" / "systemd" / "zoe-data.service",
             *sorted((REPO / "scripts" / "setup" / "systemd" / "zoe-data.service.d").glob("*.conf"))]
    for u in units:
        for line in u.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("EnvironmentFile"):
                assert ".hermes" not in line, f"{u.name}: {line}"
