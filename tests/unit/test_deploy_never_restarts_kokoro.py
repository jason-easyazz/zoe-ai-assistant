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

_MUTATING = r"(?:restart|try-restart|reload-or-restart|reload|try-reload-or-restart|stop|start|kill|disable|mask)"
_DOCKER_MUTATING = r"(?:restart|stop|start|kill|rm|rename|pause|up|down|recreate|run|compose)"
# systemd directives that make one unit's stop / restart / reload follow another's.
_COUPLING = (r"PartOf|BindsTo|Requires|Requisite|Upholds|Conflicts|Wants|"
             r"PropagatesStopTo|StopPropagatedFrom|PropagatesReloadTo|ReloadPropagatedFrom")


def _commands(text: str) -> list[str]:
    """Shell-ish SIMPLE commands: continuations joined, comments dropped, split on && || ; |."""
    joined = re.sub(r"\\\n\s*", " ", text)
    out = []
    for line in joined.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        for seg in re.split(r"&&|\|\||;|\|", line):
            seg = seg.strip()
            if seg:
                out.append(seg)
    return out


def _assignments(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2)
            for m in re.finditer(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=(.*)$", text, re.M)}


def _resolve(var: str, env: dict[str, str], _seen: frozenset = frozenset()) -> str | None:
    """Assigned value of ``var`` with every $REF inside it expanded transitively; None if ``var``
    (or any variable it references) is not assigned in the file."""
    if var not in env:
        return None
    if var in _seen:
        return env[var]
    out = env[var]
    for ref in re.findall(r"\$\{?(\w+)", env[var]):
        sub = _resolve(ref, env, _seen | {var})
        if sub is not None:
            out += " " + sub
    return out


def kokoro_touches(text: str, extra_env: dict[str, str] | None = None) -> list[str]:
    """Commands that stop/restart/reload Kokoro - named, via a glob / --all, or via a variable
    whose assigned value names it or cannot be resolved from the same file."""
    env = {**_assignments(text), **(extra_env or {})}
    bad = []
    for cmd in _commands(text):
        if re.match(r"(echo|printf|log|warn|die)\b", cmd):
            continue  # diagnostic text, not an action
        if re.search(r"\bsystemctl\b", cmd) and re.search(rf"\b{_MUTATING}\b", cmd):
            args = cmd.split("systemctl", 1)[1]
            if re.search(r"kokoro", args, re.I):
                bad.append(cmd)
            elif re.search(r"(^|\s)(--all|'?\S*\*\S*'?)(\s|$)", args):
                bad.append(cmd)  # a glob / --all can reach Kokoro without naming it
            for var in re.findall(r"\$\{?(\w+)", args):
                resolved = _resolve(var, env)
                if resolved is None or re.search(r"kokoro", resolved, re.I):
                    bad.append(cmd)  # unresolvable / kokoro-valued target (chains followed)
        if re.match(r"(sudo\s+)?(docker|podman)\b", cmd) and re.search(rf"\b{_DOCKER_MUTATING}\b", cmd) \
                and re.search(r"kokoro", cmd, re.I):
            bad.append(cmd)
        if re.match(r"(sudo\s+)?(pkill|killall|kill)\b", cmd) and re.search(r"kokoro", cmd, re.I):
            bad.append(cmd)
    return bad


_HELPER = re.compile(r"\b(?:bash|sh|python3?)\s+[\"']?([A-Za-z0-9_./-]*scripts/[A-Za-z0-9_./-]+\.(?:sh|py))")


def invoked_helpers(text: str) -> list[Path]:
    """Repo scripts the deploy files run directly (bash X / python3 X)."""
    found = []
    for cmd in _commands(text):
        if re.match(r"(echo|printf|log|warn|die)\b", cmd):
            continue
        for m in _HELPER.finditer(cmd):
            if (REPO / m.group(1)).is_file():
                found.append(REPO / m.group(1))
    return found


def coupling_directives(unit_text: str, other: str) -> list[str]:
    """Lines in a unit file that make systemd restart/stop it together with ``other``."""
    hits = []
    for line in unit_text.splitlines():
        m = re.match(rf"\s*({_COUPLING})\s*=\s*(.*)", line)
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


def test_helpers_the_deploy_path_runs_never_touch_kokoro_either():
    helpers = {h for p in DEPLOY_PATH for h in invoked_helpers(p.read_text())}
    assert helpers, "the helper discovery found nothing - it is vacuous"
    for h in sorted(helpers):
        assert kokoro_touches(h.read_text()) == [], h


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
    # ...and nothing a deploy restarts (zoe-data, both flue units) may stop/restart Kokoro with it.
    for unit in ("zoe-data", "flue-zoe-brain-2x", "flue-zoe-telegram"):
        assert coupling_directives(_unit_texts(unit), "kokoro") == [], unit


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
    'unit=kokoro-tts.service\nsystemctl --user restart "$unit"',
    'systemctl --user restart "$UNSET_TARGET"',
    'TARGET=kokoro-tts.service\nSERVICE="$TARGET"\nsystemctl --user restart "$SERVICE"',
    "systemctl --user stop kokoro-tts && echo done",
    "docker compose restart kokoro",
])
def test_negative_control_scanner_catches_kokoro_touches(snippet):
    assert kokoro_touches(snippet)


@pytest.mark.parametrize("snippet", [
    "systemctl --user restart zoe-data.service",
    "systemctl --user is-active kokoro-tts.service",
    "# systemctl --user restart kokoro-tts.service",
    "curl -fsS http://127.0.0.1:10201/health",
    "systemctl --user restart zoe-data.service && systemctl --user is-active kokoro-tts.service",
    "docker logs kokoro-tts",
    "docker inspect kokoro-tts",
    "systemctl --user restart zoe-data.service # kokoro remains untouched",
    'S="${ZOE_SERVICE:-zoe-data}"\nsystemctl --user restart "$S"',
])
def test_negative_control_scanner_ignores_benign(snippet):
    assert kokoro_touches(snippet) == []


def test_negative_control_coupling_scanner():
    assert coupling_directives("[Unit]\nPartOf=zoe-data.service\n", "zoe-data")
    assert coupling_directives("[Unit]\nBindsTo=kokoro-tts.service\n", "kokoro")
    for d in ("PropagatesStopTo", "StopPropagatedFrom", "PropagatesReloadTo", "ReloadPropagatedFrom"):
        assert coupling_directives(f"[Unit]\n{d}=kokoro-tts.service\n", "kokoro")
    assert coupling_directives("[Unit]\nPropagatesStopTo=kokoro-tts.service\n", "kokoro")  # flue-unit shape
    assert not coupling_directives("[Unit]\nAfter=network.target llama-server.service\n", "kokoro")
