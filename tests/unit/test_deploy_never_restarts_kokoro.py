"""A deploy must never touch kokoro-tts.service.

Kokoro is Zoe's ONLY voice (ZOE_TTS_MODE=kokoro, no fallback): ~2.3 GB, seconds to minutes to
reload, and Zoe is mute while it is down. A zoe-data deploy restart therefore must not stop,
restart or reload it, and nothing may couple the two units so systemd does it for us
(PartOf= / BindsTo= / Requires= / Upholds= / Conflicts=). Voice probes legitimately stop and
restart Kokoro, but those live in scripts/landing and scripts/perf - not in the deploy path.

Static pin over the deploy path: .github/workflows/deploy.yml, scripts/maintenance/deploy_live.sh,
scripts/maintenance/deploy_zoe_data_when_ready.sh, and the zoe-data / kokoro-tts unit files.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
DEPLOY_PATH = [
    REPO / ".github" / "workflows" / "deploy.yml",
    REPO / "scripts" / "maintenance" / "deploy_live.sh",
    REPO / "scripts" / "maintenance" / "deploy_zoe_data_when_ready.sh",
]
SYSTEMD = REPO / "scripts" / "setup" / "systemd"

_MUTATING = r"(?:restart|try-restart|reload-or-restart|reload|stop|start|kill|disable|mask|kill)"


def _commands(text: str) -> list[str]:
    """Shell-ish logical lines: backslash continuations joined, comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", text)
    out = []
    for line in joined.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def kokoro_touches(text: str) -> list[str]:
    """Commands that stop/restart/reload Kokoro, or do so through a glob / --all / bare variable."""
    bad = []
    for cmd in _commands(text):
        if re.search(r"\bsystemctl\b", cmd) and re.search(rf"\b{_MUTATING}\b", cmd):
            if re.search(r"kokoro", cmd, re.I):
                bad.append(cmd)
            elif re.search(r"\s(--all|'?\*'?|\S*\*\S*)(\s|$)", cmd.split("systemctl", 1)[1]):
                bad.append(cmd)  # a glob / --all can reach Kokoro without naming it
        if re.search(r"\b(docker|podman)\b", cmd) and re.search(r"kokoro", cmd, re.I):
            bad.append(cmd)
        if re.search(r"\b(pkill|killall|kill)\b", cmd) and re.search(r"kokoro", cmd, re.I):
            bad.append(cmd)
    return bad


def coupling_directives(unit_text: str, other: str) -> list[str]:
    """Lines in a unit file that make systemd restart/stop it together with ``other``."""
    hits = []
    for line in unit_text.splitlines():
        m = re.match(r"\s*(PartOf|BindsTo|Requires|Requisite|Upholds|Conflicts|Wants)\s*=\s*(.*)", line)
        if m and re.search(other, m.group(2), re.I):
            hits.append(line.strip())
    return hits


def _unit_texts(unit: str) -> str:
    parts = [(SYSTEMD / f"{unit}.service").read_text()]
    dropins = SYSTEMD / f"{unit}.service.d"
    if dropins.is_dir():
        parts += [p.read_text() for p in sorted(dropins.glob("*.conf"))]
    return "\n".join(parts)


@pytest.mark.parametrize("path", DEPLOY_PATH, ids=lambda p: p.name)
def test_deploy_path_never_stops_restarts_or_reloads_kokoro(path):
    assert path.exists(), f"{path} moved - update this pin, do not delete it"
    assert kokoro_touches(path.read_text()) == []


def test_deploy_path_actually_restarts_zoe_data_so_the_scan_is_not_vacuous():
    text = DEPLOY_PATH[0].read_text()
    assert re.search(r"systemctl --user restart zoe-data\.service", text)


def test_zoe_data_and_kokoro_units_are_not_coupled():
    zoe = _unit_texts("zoe-data")
    kok = _unit_texts("kokoro-tts")
    assert coupling_directives(zoe, "kokoro") == []
    assert coupling_directives(kok, "zoe-data") == []
    # Kokoro must not be tied to ANYTHING a deploy restarts (zoe-data, the flue units).
    for deployed in ("zoe-data", "flue-zoe"):
        assert coupling_directives(kok, deployed) == []


# ── negative controls: prove the scanners actually catch the thing they pin ────────────────
@pytest.mark.parametrize("snippet", [
    "systemctl --user restart kokoro-tts.service",
    "systemctl --user restart zoe-data.service kokoro-tts",
    "systemctl --user restart zoe-data.service \\\n  kokoro-tts.service",
    "systemctl --user try-restart 'kokoro*'",
    "systemctl --user restart *.service",
    "systemctl --user restart --all",
    "docker restart kokoro-tts",
    "pkill -f kokoro_sidecar",
])
def test_negative_control_scanner_catches_kokoro_touches(snippet):
    assert kokoro_touches(snippet)


@pytest.mark.parametrize("snippet", [
    "systemctl --user restart zoe-data.service",
    "systemctl --user is-active kokoro-tts.service",
    "# systemctl --user restart kokoro-tts.service",
    "curl -fsS http://127.0.0.1:10201/health",
])
def test_negative_control_scanner_ignores_benign(snippet):
    assert kokoro_touches(snippet) == []


def test_negative_control_coupling_scanner():
    assert coupling_directives("[Unit]\nPartOf=zoe-data.service\n", "zoe-data")
    assert coupling_directives("[Unit]\nBindsTo=kokoro-tts.service\n", "kokoro")
    assert not coupling_directives("[Unit]\nAfter=network.target llama-server.service\n", "kokoro")
