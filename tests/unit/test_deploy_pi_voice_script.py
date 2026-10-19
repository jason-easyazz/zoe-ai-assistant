"""Pin scripts/setup/deploy-pi-voice.sh against the 2026-10-04 23:12 incident.

The script used to rsync the repo's Jetson unit TEMPLATE (/home/zoe paths,
WantedBy=multi-user.target) to the Pi and install it over the live user unit,
so the panel daemon died with status=203/EXEC. It also never shipped
zoe_voice_announce.py, which the daemon imports. These tests run the real
script with fake `ssh` / `rsync` / `scp` on PATH that record every invocation
(nothing touches a network or a Pi) and assert the class is closed:

* the template is never shipped by default, and the shipped set covers every
  sibling module the daemon imports;
* an existing unit is NEVER overwritten (refused without --force-unit, backed
  up with it), and --install-unit ships a RENDERED unit with Pi paths only;
* the post-restart verification (active / health / md5) fails the script.

Stdlib only -> runs in the slim `ci_safe` lane.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "setup"
SCRIPT = SETUP / "deploy-pi-voice.sh"

FAKE_SSH = r"""#!/usr/bin/env bash
# Record the remote command (last arg), answer the script's probes.
cmd="${*: -1}"
printf '%s\n---\n' "$cmd" >> "$REC/ssh.log"
case "$cmd" in
  *UNIT_ABSENT*)
    if [ "${FAKE_UNIT_EXISTS:-0}" = 1 ]; then echo UNIT_EXISTS; else echo UNIT_ABSENT; fi ;;
  *md5sum*)
    files="${cmd#*md5sum }"
    # shellcheck disable=SC2086
    out="$(cd "$FAKE_SRC" && md5sum $files)"
    if [ "${FAKE_MD5_BAD:-0}" = 1 ]; then
      out="$(printf '%s\n' "$out" | sed '1s/^[0-9a-f]\{32\}/00000000000000000000000000000000/')"
    fi
    printf '%s\n' "$out"
    ;;
  *--health-state*)
    [ "${FAKE_HELPER_FAIL:-0}" = 1 ] && { echo "ImportError: boom" >&2; exit 1; }
    [ -n "${FAKE_SPEAKER:-}" ] && echo "SPEAKER_STATE ${FAKE_SPEAKER}" ;;
  *ZOE_SPEAKER_MODEL=*)
    [ "${FAKE_CFG_FAIL:-0}" = 1 ] && exit 255
    printf 'CFG_OK %s\n' "${FAKE_SPEAKER_CFG:-}" ;;
  *is-active*)
    [ "${FAKE_ACTIVE:-1}" = 1 ] || exit 1 ;;
  *BUILD.json.tmp*)
    printf '%s' "$cmd" | grep -o '{[^}]*}' > "$REC/build.json" ;;
  *HEALTH_OK*)
    [ "${FAKE_HEALTH:-1}" = 1 ] || exit 1
    sha="$(sed -n 's/.*"sha": "\([^"]*\)".*/\1/p' "$REC/build.json" 2>/dev/null)"
    [ "${FAKE_BUILD_BAD:-0}" = 1 ] && sha="0000000000000000000000000000000000000000"
    printf 'HEALTH_OK {"status": "ok", "build": "%s", "code_sha256": "x"}\n' "$sha" ;;
  *curl*)
    [ "${FAKE_HEALTH:-1}" = 1 ] || exit 1 ;;
esac
exit 0
"""

FAKE_RSYNC = r"""#!/usr/bin/env bash
printf '%s\n' "$@" > "$REC/rsync.args"
exit 0
"""

FAKE_SCP = r"""#!/usr/bin/env bash
printf '%s\n' "$@" >> "$REC/scp.args"
n=$#
src="${*: $((n-1)):1}"
cp "$src" "$REC/scp_$(basename "$src")"
exit 0
"""


@pytest.fixture()
def harness(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC), ("scp", FAKE_SCP)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)

    def run(*args, **env_extra):
        env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "REC": str(rec),
            "FAKE_SRC": str(SETUP),
            "VERIFY_TIMEOUT_S": "1",
        }
        env.update({k: str(v) for k, v in env_extra.items()})
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env, capture_output=True, text=True, timeout=60,
        )

    run.rec = rec
    return run


def _rsync_args(rec: Path) -> list[str]:
    return (rec / "rsync.args").read_text().splitlines()


def _ssh_log(rec: Path) -> str:
    p = rec / "ssh.log"
    return p.read_text() if p.exists() else ""


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_default_ships_daemon_and_announce_never_the_unit(harness):
    r = harness()
    assert r.returncode == 0, r.stdout + r.stderr
    names = {Path(a).name for a in _rsync_args(harness.rec)}
    assert "zoe_voice_daemon.py" in names
    assert "zoe_voice_announce.py" in names
    assert "pi-requirements.txt" in names
    # THE regression: the Jetson template must never reach the Pi by default.
    assert not any("zoe-voice.service" in a for a in _rsync_args(harness.rec))
    assert not (harness.rec / "scp.args").exists()
    # ...and the live unit is not touched: restart only.
    log = _ssh_log(harness.rec)
    assert "systemctl --user restart zoe-voice" in log
    for forbidden in ("zoe-voice.service", "daemon-reload", " enable ", "sed "):
        assert forbidden not in log, forbidden
    # Ships with a backup so "restore the .bak" is always possible.
    assert any(a.startswith("--suffix=.bak-") for a in _rsync_args(harness.rec))


def test_shipped_set_covers_every_sibling_module_the_daemon_imports(harness):
    harness()
    shipped = {Path(a).name for a in _rsync_args(harness.rec)}
    siblings = {p.stem for p in SETUP.glob("*.py")} - {"zoe_voice_daemon"}
    tree = ast.parse((SETUP / "zoe_voice_daemon.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    needed = imported & siblings
    assert "zoe_voice_announce" in needed  # the import that bit us
    missing = {f"{m}.py" for m in needed} - shipped
    assert not missing, f"daemon imports modules the deploy does not ship: {missing}"


def test_install_unit_ships_a_rendered_unit_with_pi_paths_only(harness):
    r = harness("--install-unit")
    assert r.returncode == 0, r.stdout + r.stderr
    unit = (harness.rec / "scp_zoe-voice.service").read_text()
    assert "WorkingDirectory=/home/pi/.zoe-voice\n" in unit
    assert "ExecStart=/home/pi/.zoe-voice/venv/bin/python3 /home/pi/.zoe-voice/zoe_voice_daemon.py\n" in unit
    assert "EnvironmentFile=-/home/pi/.zoe-voice/.env.voice\n" in unit
    assert "WantedBy=default.target\n" in unit
    assert "multi-user.target" not in unit
    assert "\nUser=" not in unit and "\nGroup=" not in unit
    # No Jetson path in the unit, nor in anything addressed to the Pi (local
    # SOURCE paths legitimately live under the checkout, wherever that is).
    to_pi = [a for a in _rsync_args(harness.rec) if "@" in a]
    to_pi += [a for a in (harness.rec / "scp.args").read_text().splitlines() if "@" in a]
    everything = unit + _ssh_log(harness.rec) + "\n".join(to_pi)
    assert "/home/zoe" not in everything
    # The template itself is still never an rsync source.
    assert not any("zoe-voice.service" in a for a in _rsync_args(harness.rec))


def test_install_unit_honours_overridden_target_paths(harness):
    r = harness("--install-unit", PI_DAEMON_DIR="/opt/zv", PI_VENV="/opt/zv/.venv")
    assert r.returncode == 0, r.stdout + r.stderr
    unit = (harness.rec / "scp_zoe-voice.service").read_text()
    assert "ExecStart=/opt/zv/.venv/bin/python3 /opt/zv/zoe_voice_daemon.py\n" in unit
    assert "/home/pi" not in unit and "/home/zoe" not in unit


def test_existing_unit_is_refused_without_force(harness):
    r = harness("--install-unit", FAKE_UNIT_EXISTS=1)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "REFUSED" in r.stderr
    # Refused BEFORE shipping anything.
    assert not (harness.rec / "rsync.args").exists()
    assert not (harness.rec / "scp.args").exists()
    assert "restart" not in _ssh_log(harness.rec)


def test_force_unit_replaces_but_backs_up_first(harness):
    r = harness("--install-unit", "--force-unit", FAKE_UNIT_EXISTS=1)
    assert r.returncode == 0, r.stdout + r.stderr
    log = _ssh_log(harness.rec)
    backup = log.index('cp -p "$HOME/.config/systemd/user/zoe-voice.service"')
    replace = log.index('mv "/home/pi/.zoe-voice/zoe-voice.service.rendered"')
    assert backup < replace
    assert ".bak-" in log[backup:replace]


def test_force_unit_alone_is_a_usage_error(harness):
    r = harness("--force-unit")
    assert r.returncode == 2
    assert not (harness.rec / "rsync.args").exists()


def test_verify_fails_when_remote_md5_differs(harness):
    r = harness(FAKE_MD5_BAD=1)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "md5" in r.stderr
    assert "ROLLBACK" in r.stderr and ".bak-" in r.stderr
    assert "systemctl --user restart zoe-voice" in r.stderr


def test_verify_fails_when_unit_never_becomes_active(harness):
    r = harness(FAKE_ACTIVE=0)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "is-active" in r.stderr and "ROLLBACK" in r.stderr


def test_verify_fails_when_health_does_not_answer(harness):
    r = harness(FAKE_HEALTH=0)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "/health" in r.stderr and "ROLLBACK" in r.stderr


def test_build_json_is_written_before_the_restart_and_health_must_report_it(harness):
    # V-3 (deep dive 2): /health names the build that runs. BUILD.json lands on the Pi BEFORE the
    # restart (the daemon reads it at start) and the verify step requires /health to report it.
    r = harness()
    assert r.returncode == 0, r.stdout + r.stderr
    log = _ssh_log(harness.rec)
    assert log.index("BUILD.json.tmp") < log.index("systemctl --user restart zoe-voice") < log.index("HEALTH_OK")
    import json as _json
    build = _json.loads((harness.rec / "build.json").read_text())
    head = subprocess.run(["git", "-C", str(SETUP), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert build["sha"].split("-dirty")[0] == head and build["deployed_at"].endswith("Z")
    assert f"/health reports build {build['sha']}" in r.stdout


def test_verify_fails_when_health_reports_another_build(harness):
    r = harness(FAKE_BUILD_BAD=1)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "the daemon that answered is not the one just shipped" in r.stderr
    assert "BUILD.json" in r.stderr and "ROLLBACK" in r.stderr   # the rollback also drops the stale build name


def test_dry_run_touches_nothing(harness):
    r = harness("--install-unit", DRY_RUN=1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (harness.rec / "rsync.args").exists()
    assert not (harness.rec / "ssh.log").exists()
    assert "WantedBy=default.target" in r.stdout
    assert "/home/zoe" not in r.stdout


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


# --- the speaker model must be READY when it is the acting one (Codex P1 on #2024) ---------------------------------------

@pytest.mark.parametrize("state, cfg, ok, says", [
    ("resnet34 refused: embedding drift: cosine 0.98", "resnet34", False, "the acting speaker model cannot run"),
    ("resnet34 not loaded", "resnet34", False, "the acting speaker model cannot run"),
    ("resnet34 ready", "resnet34", True, "speaker model resnet34: ready"),                    # negative control
    ("dual refused: model file sha256 mismatch", "dual", True, "WARNING: speaker model dual"),   # shadow-only: loud, not fatal
    ("dual ready", "dual", True, "resnet34 ready (shadow)"),
    ("resemblyzer off", "", True, "speaker model: resemblyzer"),
    ("", "", True, "speaker model: resemblyzer"),               # state unreadable BUT the config proves the legacy mode
])
def test_deploy_verifies_the_acting_speaker_model_is_ready(harness, state, cfg, ok, says):
    r = harness(FAKE_SPEAKER=state, FAKE_SPEAKER_CFG=cfg, RESNET34_READY_TIMEOUT_S=1)
    assert (r.returncode == 0) is ok, r.stdout + r.stderr
    assert says in r.stdout + r.stderr
    assert "--health-state http://127.0.0.1:7777/health 1" in _ssh_log(harness.rec)


# --- fail-CLOSED when the state cannot be read (Codex P1 on #2024) --------------------------------------------------------

@pytest.mark.parametrize("env, says", [
    (dict(FAKE_HELPER_FAIL=1, FAKE_SPEAKER_CFG="resnet34"), "could not read the daemon's speaker-model state"),
    (dict(FAKE_HELPER_FAIL=1, FAKE_SPEAKER_CFG="dual"), "could not read the daemon's speaker-model state"),
    (dict(FAKE_SPEAKER="", FAKE_SPEAKER_CFG="'resnet34'"), "could not read the daemon's speaker-model state"),   # no SPEAKER_STATE line
    (dict(FAKE_SPEAKER="unknown unreachable", FAKE_SPEAKER_CFG="RESNET34"), "could not read the daemon's speaker-model state"),
    (dict(FAKE_HELPER_FAIL=1, FAKE_CFG_FAIL=1), "could not read the daemon's speaker-model state"),   # neither readable: cannot prove legacy
    (dict(FAKE_SPEAKER="resemblyzer off", FAKE_SPEAKER_CFG="resnet34"), "the restart did not pick up the configuration"),
])
def test_deploy_fails_closed_when_the_speaker_state_cannot_be_proven(harness, env, says):
    r = harness(RESNET34_READY_TIMEOUT_S=1, **env)
    assert r.returncode != 0, r.stdout + r.stderr
    assert says in r.stdout + r.stderr


def test_an_unreadable_state_passes_only_when_the_config_proves_the_legacy_mode(harness):
    # negative control for the fail-closed rule: the same helper failure with no ZOE_SPEAKER_MODEL configured is the legacy mode
    r = harness(FAKE_HELPER_FAIL=1, FAKE_SPEAKER_CFG="", RESNET34_READY_TIMEOUT_S=1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the daemon's state was unreadable" in r.stdout
    assert harness(FAKE_HELPER_FAIL=1, FAKE_SPEAKER_CFG="garbage", RESNET34_READY_TIMEOUT_S=1).returncode == 0   # unknown value = legacy, as the daemon reads it
