"""Architecture deep dive B9 (ops hygiene): unit tracking, the one spine health check, container memory caps,
``.env`` reaping, secret file modes, and the VAPID key move. Hermetic: temp trees and fakes, nothing live.
Every guard has a negative control that breaks it and must go red.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
MAINT = REPO / "scripts" / "maintenance"
SYSTEMD = REPO / "scripts" / "setup" / "systemd"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, MAINT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


inv = _load("unit_inventory")
health = _load("zoe_health")
reap = _load("reap_env_copies")
perms = _load("fix_secret_perms")
vapid = _load("migrate_vapid_keys")


# ── every unit installed on the box (2026-10-18) is tracked or retired with a reason ─────────────────────────────

LIVE_UNITS_2026_10_18 = """
bonsai-server.service flue-zoe-brain-2x.service flue-zoe-brain.service flue-zoe-telegram.service
flue-zoe-telegram-watchdog.service flue-zoe-telegram-watchdog.timer functiongemma-router.service github-runner.service
hermes-agent.service kokoro-tts.service llama-server-e2b-fallback.service llama-server.service openclaw-gateway.service
serena-mcp.service zoe-12b-postboot.service zoe-backup.service zoe-backup.timer zoe-backup-verify.service
zoe-backup-verify.timer zoe-brain-boot-check.service zoe-crash-loop-watch.service zoe-crash-loop-watch.timer
zoe-data.service zoe-dreaming.service zoe-dreaming.timer zoe-health.service zoe-health.timer zoe-keepwarm.service
zoe-keepwarm.timer zoe-log-rotate.service zoe-log-rotate.timer zoe-ma-idle-reap.service zoe-ma-idle-reap.timer
zoe-memory-export.service zoe-memory-export.timer zoe-merge-queue.service zoe-merge-queue.timer
zoe-multica-daemon.service zoe-multica-heartbeat.service zoe-multica-heartbeat.timer zoe-night-window.service
zoe-night-window.timer zoe-opensrc-refresh.service zoe-opensrc-refresh.timer zoe-purge-presence.service
zoe-purge-presence.timer zoe-serena-pregate-restart.service zoe-serena-pregate-restart.timer zoe-serena-reaper.service
zoe-serena-reaper.timer zoe-voice-regression.service zoe-voice-regression.timer
""".split()


def test_every_unit_installed_on_the_box_is_tracked_or_retired():
    names = inv.template_names(REPO)
    lost = [u for u in LIVE_UNITS_2026_10_18 if u not in names and u not in inv.RETIRED]
    assert not lost, f"installed on the box with no template and no retirement reason: {lost}"


def test_inventory_classifies_and_flags_untracked(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    for n in ("zoe-data.service", "zoe-dreaming.timer", "mystery.service", "llama-server.service.bak-1"):
        (live / n).write_text("[Unit]\n")
    rows = {r["unit"]: r["class"] for r in inv.inventory(live, REPO)["units"]}
    assert rows == {"zoe-data.service": "tracked", "zoe-dreaming.timer": "retired", "mystery.service": "UNTRACKED"}
    assert inv.inventory(live, REPO)["cruft"] == ["llama-server.service.bak-1"]
    # negative control: an untracked unit makes the table mode exit 1, while --summary always exits 0 (probe line)
    assert inv.main(["--live-dir", str(live)]) == 1
    assert inv.main(["--live-dir", str(live), "--summary"]) == 0
    (live / "mystery.service").unlink()
    assert inv.main(["--live-dir", str(live)]) == 0


def test_retired_units_have_no_template():
    names = inv.template_names(REPO)
    both = [u for u in inv.RETIRED if u in names and u not in ("hermes-agent.service", "openclaw-gateway.service")]
    assert not both, f"a unit cannot be tracked AND retired: {both}"


def test_ground_truth_prints_the_unit_line():
    text = (MAINT / "zoe_ground_truth.sh").read_text()
    assert "unit_inventory.py\" --summary" in text and "timeout 5" in text


# ── one health check on the canonical spine ──────────────────────────────────────────────────────────────────────


def test_health_probes_the_live_spine_and_no_retired_port():
    ports = set(health.SPINE.values())
    assert {8000, 11434, 3579, 10201, 11436} <= ports
    assert not ports & {8642, 18789, 3578}, "a retired lane (Hermes / OpenClaw / Flue 1.x) came back"


def _fake_run(argv, timeout=5.0):
    if argv[:2] == ["docker", "exec"]:
        return 0, ""
    if argv[:2] == ["docker", "ps"]:
        return 0, "zoe-ui\tUp 3 hours\nzoe-multica-backend\tRestarting (1) 3 seconds ago"
    return 0, "zoe-backup.service loaded failed failed Zoe backup"


def test_health_probe_reports_spine_containers_and_failed_units():
    checks = {c.name: c for c in health.probe(http=lambda port: (port != 3579, "x"), run=_fake_run,
                                                night_open=False)}
    assert not checks["flue-zoe-brain-2x :3579"].ok and checks["zoe-data :8000"].ok
    assert not checks["containers"].ok and "zoe-multica-backend" in checks["containers"].detail
    assert not checks["failed units"].ok and "zoe-backup.service" in checks["failed units"].detail


def test_health_skips_the_spine_while_the_night_window_is_open():
    checks = health.probe(http=lambda port: (False, "down"), run=_fake_run, night_open=True)
    assert not any(":" in c.name for c in checks), "the night window stops the spine on purpose"


def test_health_alerts_on_transitions_only():
    down = [health.Check("zoe-data :8000", False, "URLError")]
    up = [health.Check("zoe-data :8000", True, "HTTP 200")]
    lines, st = health.transitions(down, {})
    assert lines == []                                    # one failure = a restart window, not an outage
    lines, st = health.transitions(down, st)
    assert lines == ["DOWN: zoe-data :8000 (URLError)"]
    lines, st = health.transitions(down, st)
    assert lines == []                                    # no repeat every 5 minutes
    lines, st = health.transitions(up, st)
    assert lines == ["recovered: zoe-data :8000 (HTTP 200)"]
    lines, _ = health.transitions(up, st)
    assert lines == []


def test_health_unit_needs_no_secret():
    text = (SYSTEMD / "zoe-health.service").read_text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "EnvironmentFile" not in body and "zoe_health.py" in body


# ── container memory caps: never below the measured peak + 30 %, database exempt ─────────────────────────────────

# Summed per-process VmHWM (MB), the higher of 2026-10-03 (docs/knowledge/docker-log-and-memory-limits.md) and
# 2026-10-18 (cgroup v2 on this kernel has no memory.peak, so VmHWM is the peak instrument).
MEASURED_PEAK_MB = {
    "zoe-ui": 71, "zoe-auth": 83, "homeassistant": 453, "homeassistant-mcp-bridge": 53, "cloudflared": 41,
    "music-assistant": 833, "ytmusic-potoken": 213, "multica-backend": 26, "multica-web": 98, "omnigent": 273,
}
EXEMPT = {
    "zoe-database": "an OOM kill is an outage of every data path; exempt by design",
    "keeper": "profile service, never run on this box: no measurement to size from",
    "livekit": "capped in its own PR (#2108, deep dive A10)",
}
COMPOSE = [REPO / "docker-compose.yml", REPO / "docker-compose.modules.yml",
           REPO / "modules" / "omnigent" / "docker-compose.module.yml"]


def _mb(v) -> float:
    m = re.fullmatch(r"(\d+)([mg])", str(v).lower())
    assert m, v
    return int(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def cap_violations(services: dict) -> list[str]:
    out = []
    for name, svc in services.items():
        if name in EXEMPT:
            continue
        mem, swap = svc.get("mem_limit"), svc.get("memswap_limit")
        if mem is None:
            out.append(f"{name}: no mem_limit")
            continue
        peak = MEASURED_PEAK_MB.get(name)
        if peak is None:
            out.append(f"{name}: capped but no measured peak recorded")
        elif _mb(mem) < 1.3 * peak:
            out.append(f"{name}: {mem} < 1.3 x peak {peak} MB")
        if swap is None or _mb(swap) < _mb(mem):
            out.append(f"{name}: memswap_limit missing or below mem_limit")
    return out


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_every_container_has_a_memory_cap_above_its_peak(path):
    services = yaml.safe_load(path.read_text())["services"]
    assert cap_violations(services) == []


def test_cap_checker_negative_control():
    bad = {"homeassistant": {"mem_limit": "512m", "memswap_limit": "768m"},   # 512 < 1.3 x 453
           "zoe-auth": {"memswap_limit": "384m"},
           "zoe-ui": {"mem_limit": "256m", "memswap_limit": "128m"}}
    v = cap_violations(bad)
    assert len(v) == 3, v


def test_database_stays_uncapped():
    svc = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]["zoe-database"]
    assert "mem_limit" not in svc


# ── .env reaping ─────────────────────────────────────────────────────────────────────────────────────────────────


def _w(p: Path, data: str = "K=v\n", age_days: float = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    if age_days:
        t = p.stat().st_mtime - age_days * 86400
        os.utime(p, (t, t))
    return p


def test_reaper_keeps_live_and_active_and_reaps_stale(tmp_path):
    home, co = tmp_path, tmp_path / "assistant"
    live = _w(co / ".env", "K=new\n")
    _w(co / "labs/flue-zoe-brain-2x/.env")
    retired = _w(co / "labs/flue-zoe-brain/.env")
    _w(co / ".env.example")
    old_bak = _w(co / "services/zoe-data/.env.bak-20260710", age_days=100)
    new_bak = _w(co / "services/zoe-data/.env.bak-20261014", age_days=3)
    wts = co / ".claude/worktrees"
    gitroot = tmp_path / "gitdirs"
    # orphaned worktree: its gitdir is gone
    _w(wts / "orphan/.git", f"gitdir: {gitroot}/orphan\n")
    orphan_env = _w(wts / "orphan/.env", "K=old\n")
    # idle worktree: gitdir untouched for 30 days
    _w(wts / "idle/.git", f"gitdir: {gitroot}/idle\n")
    for f in ("index", "HEAD"):
        _w(gitroot / "idle" / f, age_days=30)
    idle_env = _w(wts / "idle/.env")
    # active worktree, and a worktree a process is working in
    _w(wts / "active/.git", f"gitdir: {gitroot}/active\n")
    _w(gitroot / "active/index")
    _w(gitroot / "active/HEAD")
    active_env = _w(wts / "active/.env")
    _w(wts / "busy/.git", f"gitdir: {gitroot}/busy-gone\n")
    busy_env = _w(wts / "busy/.env")
    rows = reap.classify(home, co, [wts], [home / ".zoe-backups"], 7, 14, busy=[wts / "busy" / "sub"])
    by = {r.path: r for r in rows}
    assert not by[live].stale and by[live].klass == "live"
    assert by[retired].stale and by[retired].klass == "retired"
    assert by[old_bak].stale and not by[new_bak].stale
    assert by[orphan_env].stale and by[idle_env].stale
    assert not by[active_env].stale and not by[busy_env].stale
    assert by[orphan_env].same_as_live is False
    assert co / ".env.example" not in by


def test_reaper_dry_run_deletes_nothing_and_apply_deletes_only_stale(tmp_path, monkeypatch, capsys):
    co = tmp_path / "assistant"
    live = _w(co / ".env")
    old = _w(co / ".env.bak-1", age_days=40)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert reap.main(["--checkout", str(co)]) == 0
    assert old.exists() and "DRY RUN" in capsys.readouterr().out
    assert reap.main(["--checkout", str(co), "--apply"]) == 0
    assert not old.exists() and live.exists()
    assert "K=v" not in capsys.readouterr().out          # values never print


def test_reaper_apply_reports_failure_when_a_copy_survives(tmp_path, monkeypatch):
    co = tmp_path / "assistant"
    _w(co / ".env")
    old = _w(co / ".env.bak-1", age_days=40)
    monkeypatch.setenv("HOME", str(tmp_path))

    def refuse(self, *a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "unlink", refuse)
    assert reap.main(["--checkout", str(co), "--apply"]) == 1, "a surviving secret copy must not report success"
    assert old.exists()


# ── secret file modes ────────────────────────────────────────────────────────────────────────────────────────────


def test_perms_tighten_never_loosen_and_skip_symlinks(tmp_path):
    co = tmp_path / "assistant"
    env = _w(co / ".env")
    env.chmod(0o664)
    tight = _w(co / "services/zoe-data/.env")
    tight.chmod(0o400)
    key = _w(co / "ssl/zoe.key")
    key.chmod(0o600)
    (co / "ssl/zoe.crt").symlink_to("zoe.key")
    tunnel = _w(co / "config/d417c04a-babc-48e4-85e6-a9badc20a7a6.json")
    tunnel.chmod(0o644)
    rows = {p: (cur, tgt, note) for p, cur, tgt, note in perms.plan(tmp_path, co)}
    assert rows[env][:2] == (0o664, 0o600)
    assert tight not in rows and key not in rows          # already tight: untouched, 0400 never loosened
    assert "symlink" in rows[co / "ssl/zoe.crt"][2]
    assert tunnel not in rows, "cloudflared (uid 65532) reads the tunnel credentials: 0600 would kill the tunnel"
    assert perms.main(["--checkout", str(co), "--apply"]) == 0
    assert stat.S_IMODE(env.stat().st_mode) == 0o600 and stat.S_IMODE(tight.stat().st_mode) == 0o400


# ── the VAPID key leaves the checkout ────────────────────────────────────────────────────────────────────────────

KEYS = {"public_key": "pub", "private_key": "priv"}


def test_vapid_migration_dry_run_apply_and_conflict(tmp_path):
    legacy = _w(tmp_path / "co/services/zoe-data/data/vapid_keys.json", json.dumps(KEYS))
    new = tmp_path / "home/.zoe/secrets/vapid_keys.json"
    rc, msg = vapid.migrate(legacy, new, apply=False)
    assert rc == 0 and legacy.exists() and not new.exists()
    rc, msg = vapid.migrate(legacy, new, apply=True)
    assert rc == 0 and not legacy.exists() and json.loads(new.read_text()) == KEYS
    assert stat.S_IMODE(new.stat().st_mode) == 0o600 and stat.S_IMODE(new.parent.stat().st_mode) == 0o700
    _w(legacy, json.dumps({"public_key": "OTHER", "private_key": "OTHER"}))
    rc, msg = vapid.migrate(legacy, new, apply=True)
    assert rc == 1 and "DIFFERENT" in msg and legacy.exists(), "two keys = owner decision, never overwrite"
    _w(legacy, json.dumps(KEYS))
    rc, _ = vapid.migrate(legacy, new, apply=True)
    assert rc == 0 and not legacy.exists()
