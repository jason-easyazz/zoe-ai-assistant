"""Kernel service draft: static contracts, fake systemd, and scratch SQLite only."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MAINT = ROOT / "scripts/maintenance"
SCRIPT = MAINT / "zoe_os_kernel.sh"
UNIT = ROOT / "scripts/setup/systemd/zoe-os-authority-kernel.service"
spec = importlib.util.spec_from_file_location("kernel_ops_test", MAINT / "zoe_os_kernel_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
# Unmarked until coordinator proves slim/offline/TZ=UTC as required by tests/AGENTS.md.


def test_unit_is_bounded_optional_and_has_one_template():
    text = UNIT.read_text()
    for setting in ("Before=zoe-data.service", "MemoryHigh=64M", "MemoryMax=96M",
                    "RestartSec=10", "Restart=on-failure", "StartLimitBurst=5", "NoNewPrivileges=yes",
                    "PrivateTmp=yes", "ProtectSystem=strict", "ProtectHome=read-only", "RestrictAddressFamilies=AF_UNIX"):
        assert setting in text
    # It must never take the voice stack's protections: no swap lock, no memory reserve.
    assert "Requires=" not in text and "Wants=" not in text and "MemoryLow=" not in text and "MemorySwapMax" not in text
    assert not (ROOT / "labs/zoe-os/zoe-os-authority-kernel.service").exists()
    assert "Conflicts=zoe-os-authority-kernel.service" in (UNIT.parent / "zoe-os-shadow-kernel.service").read_text()
    assert 'KERNEL_SOURCE_REL=labs/zoe-os' in (MAINT / "zoe_os_kernel.conf").read_text()


def test_workflow_order_and_nonfatal_contract():
    steps = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())["jobs"]["deploy"]["steps"]
    names = [s.get("name") for s in steps]
    pos = names.index("Restart optional Zoe OS kernel (if changed)")
    assert names.index("Pull latest main") < names.index("Restart zoe-data (user service)") < pos
    assert pos < names.index("Health check")
    assert steps[pos]["continue-on-error"] is True
    assert "zoe_os_kernel.sh deploy" in steps[pos]["run"]
    text = SCRIPT.read_text()
    for token in ("list-unit-files", "is-enabled --quiet", "DEPLOY_BASE_SHA", "WINDOW_OPEN", "::warning::", "GITHUB_STEP_SUMMARY"):
        assert token in text
    assert "OLD_SHA" not in text


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(tmp_path / "calls"),
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    fakes = {
        "systemctl": '''#!/bin/bash
echo "$*" >> "$LOG"
case "$*" in
 *list-unit-files*) [ "${INSTALLED:-0}" = 1 ] && echo 'zoe-os-authority-kernel.service enabled'; exit 0;;
 *is-enabled*) [ "${ENABLED:-0}" = 1 ]; exit $?;;
 *is-active*) echo active;;
 *restart*) exit "${RESTART_RC:-0}";;
esac
''',
        "python3": '#!/bin/bash\nexit "${HEALTH_RC:-0}"\n',
        "git": '#!/bin/bash\nexit "${DIFF_RC:-0}"\n',
        "sleep": '#!/bin/bash\nexit 0\n',
    }
    for name, body in fakes.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)
    return home, env, bin_dir


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=20)


def test_report_not_installed_and_dead_socket(world):
    home, env, _ = world
    assert run(env, "report").stdout == "kernel: not installed\n"
    env.update(INSTALLED="1", HEALTH_RC="1")
    db = home / ".zoe/zoe-os/zoe-os.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"synthetic")
    out = run(env, "report").stdout
    assert "unit=active health=dead" in out and "database=present bytes=9 age_s=" in out
    assert str(home) not in out
    env["HEALTH_RC"] = "0"
    assert "health=ok" in run(env, "report").stdout


@pytest.mark.parametrize("installed,enabled", [("0", "0"), ("1", "0")])
def test_deploy_leaves_uninstalled_or_disabled_alone(world, installed, enabled):
    _, env, _ = world
    env.update(INSTALLED=installed, ENABLED=enabled)
    assert run(env, "deploy").returncode == 0
    assert "restart" not in Path(env["LOG"]).read_text()


def test_deploy_warning_retry_and_night_deferral(world):
    home, env, _ = world
    env.update(INSTALLED="1", ENABLED="1", DIFF_RC="1", RESTART_RC="1", DEPLOY_BASE_SHA="base")
    failed = run(env, "deploy")
    assert failed.returncode == 0 and "::warning::" in failed.stdout
    assert "WARNING:" in Path(env["GITHUB_STEP_SUMMARY"]).read_text()
    pending = home / ".zoe/deploy/kernel-update-pending"
    assert pending.exists()
    env.update(DIFF_RC="0", RESTART_RC="0")
    assert "healthy after restart" in run(env, "deploy").stdout
    assert not pending.exists()
    Path(env["LOG"]).write_text("")
    marker = home / ".zoe/night-window/WINDOW_OPEN"
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert "::warning::" in run(env, "deploy").stdout
    assert "restart" not in Path(env["LOG"]).read_text() and pending.exists()


def database(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE meta (key TEXT, value BLOB)")
    db.execute("INSERT INTO meta VALUES ('shield_key', X'01')")
    db.execute("CREATE TABLE synthetic (data BLOB)")
    db.executemany("INSERT INTO synthetic VALUES (?)", [(b"x" * 4096,)] * 200)
    db.commit()
    return db


def test_wal_snapshot_includes_a_commit_during_copy_and_restores(tmp_path):
    source = tmp_path / "source.db"
    writer = database(source)
    try:
        wrote = []
        def during_copy(*_):
            if not wrote:
                writer.execute("INSERT INTO meta VALUES ('mid_backup', X'02')")
                writer.commit()
                wrote.append(True)
        target = tmp_path / "copy.db"
        ops.snapshot(source, target, progress=during_copy)
        assert wrote
        with sqlite3.connect(target) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert restored.execute("SELECT value FROM meta WHERE key='mid_backup'").fetchone() == (b"\x02",)
        root = tmp_path / "backup"
        for _ in range(8):
            ops.backup(source, root)
        assert len(list(root.glob("*.db.gz"))) == 7
        findings, notes = ops.drill(root, tmp_path, True, time.time())
        assert not findings and notes
        assert not list(root.glob(".kernel-*"))
    finally:
        writer.close()


def test_shell_backup_absent_is_silent_and_present_restores(world):
    home, env, bin_dir = world
    # Use the coordinator's real interpreter, not the health fake.
    (bin_dir / "python3").unlink()
    env["PATH"] = f"{Path(sys.executable).parent}:{bin_dir}:/usr/bin:/bin"
    assert run(env, "backup").stdout == ""
    source = home / ".zoe/zoe-os/zoe-os.db"
    source.parent.mkdir(parents=True)
    db = database(source)
    try:
        result = run(env, "backup")
        assert result.returncode == 0 and "snapshot verified" in result.stdout
        assert str(home) not in result.stdout + result.stderr
        assert ops.drill(home / ".zoe-backups/zoe-os", home, True, time.time())[0] == []
    finally:
        db.close()


def test_ground_truth_flags_disclose_presence_only():
    text = (MAINT / "zoe_ground_truth.sh").read_text()
    section = text.split("# Optional kernel:", 1)[1].split("# ── Scheduled jobs", 1)[0]
    assert "ZOE_OS_LIST_SHADOW ZOE_OS_LIST_ADD_AUTHORITY" in section
    assert '"$f=set"' in section and '"$f=unset"' in section
    assert "env_of" not in section and "cut -d=" not in section


def test_ground_truth_flag_block_never_renders_values(tmp_path):
    block = (MAINT / "zoe_ground_truth.sh").read_text().split("if [ \"$_kernel_report\"", 1)[1]
    block = 'if [ "$_kernel_report"' + block.split("# ── Scheduled jobs", 1)[0]
    fake_env = tmp_path / "environ"
    fake_env.write_bytes(b"ZOE_OS_LIST_SHADOW=private-sentinel\0OTHER=secret\0")
    block = block.replace('/proc/$zpid/environ', str(fake_env))
    result = subprocess.run(["bash", "-c", 'dim() { echo "$1"; }; zpid=1; _kernel_report=installed;\n' + block],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout == "ZOE_OS_LIST_SHADOW=set\nZOE_OS_LIST_ADD_AUTHORITY=unset\n"


def test_health_speaks_wire_and_read_probe_is_explicit(tmp_path, monkeypatch):
    import json
    import struct
    state = tmp_path / "state"
    state.mkdir()
    (state / "creds.json").write_text(json.dumps({"creds": {"voice": "01"}}))
    sent = []
    class FakeSocket:
        def __init__(self, *_):
            frames = [{"v": 1, "nonce": "02"}, {"v": 1, "ok": True},
                      {"v": 1, "id": 1, "ok": True, "result": {"alive": True, "schema": 1, "last_write_ok": False}}]
            self.data = b"".join(struct.pack(">I", len(b)) + b for b in [json.dumps(f).encode() for f in frames])
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def settimeout(self, _): pass
        def connect(self, path): assert path == str(state / "k.sock")
        def recv(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk
        def sendall(self, data): sent.append(json.loads(data[4:]))
    monkeypatch.setattr(ops.socket, "socket", FakeSocket)
    assert ops.health(state, read_only=True)
    assert sent[-1]["op"] == "health" and sent[-1]["args"] == {"read_only": 1}
    assert not ops.health(state)  # negative control: failed write cannot pass deploy health
    assert sent[-1]["args"] == {} and "proof" in sent[0] and "credential" not in sent[0]


def test_deploy_dead_health_is_nonfatal_and_unchanged_healthy_skips(world):
    home, env, _ = world
    env.update(INSTALLED="1", ENABLED="1", DEPLOY_BASE_SHA="base", HEALTH_RC="0")
    assert "unchanged and healthy" in run(env, "deploy").stdout
    assert "restart" not in Path(env["LOG"]).read_text()
    env["HEALTH_RC"] = "1"
    result = run(env, "deploy")
    assert result.returncode == 0 and "::warning::" in result.stdout
    assert (home / ".zoe/deploy/kernel-update-pending").exists()
