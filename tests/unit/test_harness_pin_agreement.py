"""The pi pin is declared in THREE places; nothing but this test keeps them equal.

`services/zoe-core/package.json` (the manifest the brain's extensions are typed against),
`modules/omnigent/Dockerfile` (the pi worker inside the omnigent image), and
`services/zoe-data/pi_runtime_probe.py` (the install command the probe reports to an
operator) each name a version independently. They are only ever correct together:

  * probe hint vs reality — the probe does NOT install anything, it prints a command. If
    its version drifts, an operator following it installs a DIFFERENT pi than the one this
    repo declares, and nothing complains.
  * manifest vs host — zoe-core has no lockfile and no node_modules, and
    `zoe_core_client.py` launches the bare `pi` off PATH, so the manifest is a declaration
    the host-global install must match.

That is three chances to move one and forget the others, which is exactly what happened
before the 0.79 -> 0.82 bump was reconciled by hand. Pin the agreement instead.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _core_manifest_pin() -> str:
    dep = json.loads((REPO / "services/zoe-core/package.json").read_text())
    return dep["dependencies"]["@earendil-works/pi-coding-agent"]


def _dockerfile_pin() -> str:
    txt = (REPO / "modules/omnigent/Dockerfile").read_text()
    m = re.search(r"pi-coding-agent@([^\s\\\"']+)", txt)
    assert m, "no pi pin found in modules/omnigent/Dockerfile"
    return m.group(1)


def _probe_hint_pin() -> str:
    txt = (REPO / "services/zoe-data/pi_runtime_probe.py").read_text()
    m = re.search(r"PI_INSTALL_COMMAND\s*=\s*[\"'][^\"']*pi-coding-agent@([0-9][^\s\"']*)", txt)
    assert m, "PI_INSTALL_COMMAND does not pin a pi version"
    return m.group(1)


def test_pi_pin_is_identical_in_all_three_places():
    core, docker, probe = _core_manifest_pin(), _dockerfile_pin(), _probe_hint_pin()
    assert core == docker == probe, (
        f"pi pin drift — zoe-core/package.json={core!r} "
        f"modules/omnigent/Dockerfile={docker!r} pi_runtime_probe={probe!r}"
    )


def test_pi_pin_is_exact_not_a_range():
    """A caret was tried and rejected: zoe-core has no lockfile, so `^x.y.z` lets a clean
    build pull an unreviewed patch and lets the consumers drift apart."""
    for name, value in (("zoe-core/package.json", _core_manifest_pin()),
                        ("modules/omnigent/Dockerfile", _dockerfile_pin()),
                        ("pi_runtime_probe", _probe_hint_pin())):
        assert re.fullmatch(r"\d+\.\d+\.\d+", value), f"{name} pin is not exact: {value!r}"
