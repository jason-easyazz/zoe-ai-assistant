"""Off-box backup (architecture deep dive A5): the mirror, the tiers, retention vs forget-reach, the guards, the
the411 artefacts and the unit templates. Hermetic: a fake encryptor, temp trees, no network, no restic/age.

The end-to-end proof (real restic + age against a real append-only rest-server) is the lab,
``labs/offbox-backup/run.sh`` (19 cells). These tests pin the pieces CI can check on every PR, each with a
break-the-fix negative control.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("zoe_offbox", REPO / "scripts/maintenance/zoe_offbox.py")
ob = importlib.util.module_from_spec(SPEC)
sys.modules["zoe_offbox"] = ob
SPEC.loader.exec_module(ob)
THE411 = REPO / "scripts/setup/offbox-backup/the411"
SYSTEMD = REPO / "scripts/setup/systemd"


def fake_encrypt(src: Path, dst: Path) -> str:
    """'ciphertext' = a marker + the bytes; randomised like age (a fresh salt each call) so dedup must come from
    the mirror keeping the file, never from re-encryption producing the same bytes."""
    data = src.read_bytes()
    dst.write_bytes(b"AGE" + os.urandom(8) + data[::-1])
    return hashlib.sha256(data).hexdigest()


def fake_encrypt_bytes(data: bytes, dst: Path) -> None:
    dst.write_bytes(b"AGE" + data[::-1])


def tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


ZOE_FILES = {
    "data/a.jsonl": b"keep",
    "memory-exports/e.json": b"keep",
    "backups/x/raw.jsonl": b"no",
    "palace-backups/p.tgz": b"no",
    "engineering_pipeline_runs.jsonl.backup-1": b"no",
    "kokoro_cache/c.wav": b"no",
    "venvs/v/python": b"no",
    "logs/l.txt": b"no",
    "poll.log.3": b"no",
    "omi-recordings/u/r.wav": b"no",
    "secrets/vapid_keys.json": b"no",
    "env/x.env": b"no",
    "bakeoff-2026-10/pg/f": b"no",
}


def _mirror(tmp: Path, src: ob.Source):
    return ob.mirror([src], tmp / "stage", tmp / "state.json", b"salt" * 8, fake_encrypt, fake_encrypt_bytes)


def test_mirror_excludes_by_policy_and_the_filter_is_what_keeps_them_out(tmp_path):
    tree(tmp_path / "zoe", ZOE_FILES)
    rels = {r for r, _ in ob.walk_source(ob.Source("zoe", tmp_path / "zoe", ob.ZOE_TREE_EXCLUDES))}
    assert rels == {"zoe/data/a.jsonl", "zoe/memory-exports/e.json"}
    # negative control: the same tree without the policy ships every excluded file
    unfiltered = {r for r, _ in ob.walk_source(ob.Source("zoe", tmp_path / "zoe"))}
    assert len(unfiltered) == len(ZOE_FILES)


def test_mirror_encrypts_new_keeps_unchanged_reencrypts_changed_removes_deleted(tmp_path):
    tree(tmp_path / "src", {"a": b"1", "b": b"2", "c": b"3"})
    src = ob.Source("s", tmp_path / "src")
    r1 = _mirror(tmp_path, src)
    assert (r1.encrypted, r1.kept, r1.removed) == (3, 0, 0)
    before = {p.relative_to(tmp_path / "stage"): p.read_bytes() for p in (tmp_path / "stage").rglob("*.age")
              if p.name != "index.json.age"}
    r2 = _mirror(tmp_path, src)
    assert (r2.encrypted, r2.kept) == (0, 3)
    after = {p.relative_to(tmp_path / "stage"): p.read_bytes() for p in (tmp_path / "stage").rglob("*.age")
             if p.name != "index.json.age"}
    assert before == after, "unchanged files must keep byte-identical ciphertext (restic dedup)"
    (tmp_path / "src/b").write_bytes(b"22")
    (tmp_path / "src/c").unlink()
    r3 = _mirror(tmp_path, src)
    assert (r3.encrypted, r3.kept, r3.removed) == (1, 1, 1)
    names = [p for p in (tmp_path / "stage").rglob("*.age") if p.name != "index.json.age"]
    assert len(names) == 2, "a deleted (forgotten) source must not stay in the stage"


def test_same_size_same_mtime_rewrite_is_still_reencrypted(tmp_path):
    """Codex review: mtime is settable (cp -p, tar, touch -d). A same-size rewrite with the old mtime put back must
    still re-encrypt; the kernel-set ctime is what catches it."""
    f = tmp_path / "src" / "a"
    tree(tmp_path / "src", {"a": b"AAAA"})
    src = ob.Source("s", tmp_path / "src")
    _mirror(tmp_path, src)
    st = f.stat()
    time.sleep(0.05)      # ctime has kernel-tick granularity (<= 10 ms); a real rewrite is hours after the last run
    f.write_bytes(b"BBBB")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_size == st.st_size and f.stat().st_mtime_ns == st.st_mtime_ns
    r = _mirror(tmp_path, src)
    assert r.encrypted == 1, "a changed file with its old size and mtime kept stale ciphertext"
    # negative control: the size+mtime-only fingerprint would have kept it
    assert [st.st_size, st.st_mtime_ns] == ob._fingerprint(f.stat())[:2]


def test_an_unreadable_directory_keeps_its_last_good_copy_and_is_reported(tmp_path, monkeypatch):
    """Codex review: os.walk's error used to be swallowed, so an unreadable subtree looked DELETED and its
    ciphertext was dropped from the next snapshot. It is now reported and its last good copy kept."""
    tree(tmp_path / "src", {"keep/a": b"1", "locked/b": b"2"})
    src = ob.Source("s", tmp_path / "src")
    _mirror(tmp_path, src)
    real_walk = os.walk

    def walk(top, onerror=None, **kw):     # the 'locked' subtree raises like a permission error
        for dirpath, dirnames, filenames in real_walk(top, **kw):
            if dirpath.endswith("locked"):
                onerror(PermissionError(13, "denied", dirpath))
                dirnames[:] = []
                continue
            yield dirpath, dirnames, filenames
    monkeypatch.setattr(ob.os, "walk", walk)
    r = _mirror(tmp_path, src)
    assert r.unreadable == ["s/locked"] and r.removed == 0
    assert len([p for p in (tmp_path / "stage").rglob("*.age") if p.name != "index.json.age"]) == 2


def test_secret_files_are_recursive_under_ssl_and_config(tmp_path):
    tree(tmp_path, {"ssl/relay/household-ca.key": b"x", "config/sub/creds.json": b"x"})
    p = ob.Paths(home=tmp_path / "h", checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    got = {label for label, _ in ob.secret_files(p)}
    assert {"checkout/ssl/relay/household-ca.key", "checkout/config/sub/creds.json"} <= got


def test_secret_trees_are_walked_sources_so_unreadable_subtrees_are_reported(tmp_path):
    """Codex review: ssl/ and config/ were pre-enumerated with glob, which skips an unreadable subtree silently -
    mirror() then dropped its ciphertext as deleted. They are walked Sources now, with walk_source's reporting."""
    p = ob.Paths(home=tmp_path / "h", checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    tree(tmp_path, {"ssl/relay/ca.key": b"x", ".env": b"x"})
    srcs = {s.label: s for s in ob.secret_sources(p)}
    assert srcs["checkout/ssl"].path == tmp_path / "ssl" and srcs["checkout/ssl"].path.is_dir()
    assert "checkout/.env" in srcs and not any(k.startswith("checkout/ssl/") for k in srcs)


def test_a_failed_drill_does_not_advance_the_monthly_clock(tmp_path):
    p = ob.Paths(home=tmp_path, checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    now = datetime(2026, 10, 18, 6, tzinfo=timezone.utc)
    (tmp_path / "s").mkdir()
    f = tmp_path / "s" / "last_drill.json"
    f.write_text(json.dumps({"time": now.isoformat(), "ok": False, "last_ok": (now - timedelta(days=40)).isoformat()}))
    assert ob.drill_due(p, now), "a failed drill must be retried at the next weekly verify"
    f.write_text(json.dumps({"time": now.isoformat(), "ok": True, "last_ok": now.isoformat()}))
    assert not ob.drill_due(p, now + timedelta(days=7))
    assert ob.drill_due(p, now + timedelta(days=28))


def test_a_failing_drill_records_failure_without_moving_last_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OFFBOX_NO_ALERT", "1")
    p = ob.Paths(home=tmp_path, checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    (tmp_path / "s").mkdir()
    old_ok = "2026-09-01T06:00:00+00:00"
    (tmp_path / "s" / "last_drill.json").write_text(json.dumps({"time": old_ok, "ok": True, "last_ok": old_ok}))

    def boom(*a, **k):
        raise RuntimeError("restic snapshots exited 1")
    monkeypatch.setattr(ob, "snapshots", boom)
    now = datetime(2026, 10, 18, 6, tzinfo=timezone.utc)
    assert ob.cmd_drill(p, None, now, force=True) == 1
    state = json.loads((tmp_path / "s" / "last_drill.json").read_text())
    assert state["ok"] is False and state["last_ok"] == old_ok
    assert ob.drill_due(p, now)


def test_mirror_names_hide_paths_and_strays_never_ship(tmp_path):
    tree(tmp_path / "src", {"Dana-birthday-notes.txt": b"x"})
    stray = tmp_path / "stage" / "zz" / "leftover.tmp"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"plaintext from a crashed run")
    _mirror(tmp_path, ob.Source("s", tmp_path / "src"))
    shipped = [p.relative_to(tmp_path / "stage").as_posix() for p in (tmp_path / "stage").rglob("*") if p.is_file()]
    assert not any("Dana" in s for s in shipped)
    assert not stray.exists()
    assert "index.json.age" in shipped


def test_secret_files_take_env_ssl_config_never_example_or_bak(tmp_path):
    tree(tmp_path, {".env": b"x", ".env.example": b"x", ".env.bak-1": b"x", ".env.local": b"x",
                    "services/zoe-data/.env": b"x", "services/zoe-data/.env.bak-20260710": b"x",
                    "labs/flue/.env": b"x", "ssl/zoe.key": b"x", "config/t.json": b"x", "README.md": b"x"})
    (tmp_path / "ssl/zoe.crt").symlink_to("zoe.key")
    p = ob.Paths(home=tmp_path / "h", checkout=tmp_path, config_dir=tmp_path / "c", state_dir=tmp_path / "s",
                 stage_dir=tmp_path / "st")
    got = {label for label, _ in ob.secret_files(p)}
    assert got == {"checkout/.env", "checkout/.env.local", "checkout/services/zoe-data/.env",
                   "checkout/labs/flue/.env", "checkout/ssl/zoe.key", "checkout/config/t.json"}


# ── retention vs forget-reach ────────────────────────────────────────────────────────────────────────────────────


def _days(v: str) -> int:
    m = re.fullmatch(r"(\d+)d", v)
    assert m, f"household retention must be a day duration, got {v!r}"
    return int(m.group(1))


def household_bound_days(policy: dict) -> int | None:
    """The oldest a household snapshot can be under a policy, or None when it is unbounded. Count-based keeps
    (``daily``/``weekly``/``monthly``) are unbounded after a gap - restic keeps the last N periods THAT HAVE snapshots
    (lab cell R2 measured a 40-day-old snapshot surviving 7 daily + 4 weekly)."""
    if any(k in policy for k in ("daily", "weekly", "monthly", "yearly", "last", "hourly")):
        return None
    return max(_days(str(v)) for v in policy.values())


def test_household_tier_is_time_bounded_within_the_forget_reach_35_days():
    bound = household_bound_days(ob.RETENTION["household"])
    # the window plus the time until the next daily prune must never exceed the hard bound
    assert bound is not None and bound + ob.PRUNE_CADENCE_DAYS <= ob.HOUSEHOLD_MAX_AGE_DAYS == 35
    assert not any("monthly" in k for k in ob.RETENTION["household"])


def test_bound_checker_rejects_the_count_based_policy_negative_control():
    assert household_bound_days({"daily": 7, "weekly": 4}) is None
    assert household_bound_days({"within-daily": "7d", "within-weekly": "60d"}) == 60


def test_the411_prune_carries_exactly_the_box_retention():
    text = (THE411 / "prune.sh").read_text()
    for tier in ob.RETENTION:
        lines = [ln for ln in text.splitlines() if f"--tag zoe-{tier} " in ln and "restic forget" in ln]
        assert len(lines) == 1, tier
        flags = re.findall(r"(--keep-[a-z-]+)\s+(\S+)", lines[0])
        assert [x for f in flags for x in f] == ob.forget_flags(tier), tier


def test_retention_findings():
    now = datetime(2026, 10, 18, 6, 0, tzinfo=timezone.utc)
    iso = lambda d: (now - timedelta(days=d)).isoformat()  # noqa: E731
    ok = [{"time": iso(30)}, {"time": iso(0.2)}]
    assert ob.retention_findings("household", ok, now) == []
    stale_prune = [{"time": iso(40)}, {"time": iso(0.2)}]
    assert any("prune is not running" in f for f in ob.retention_findings("household", stale_prune, now))
    # the 35-day bound is HARD: 35 days and one hour is already a finding, 34.9 days is not
    just_over = [{"time": iso(35 + 1 / 24 + 0.2)}, {"time": iso(0.2)}]
    assert ob.retention_findings("household", just_over, now) != []
    assert ob.retention_findings("household", [{"time": iso(35.1)}, {"time": iso(0.2)}], now) == []
    assert ob.retention_findings("secrets", stale_prune, now) == []      # monthly keeps are fine there
    not_landing = [{"time": iso(3)}]
    assert any("not landing" in f for f in ob.retention_findings("secrets", not_landing, now))
    assert ob.retention_findings("household", [], now) == ["household: no snapshot in the repo"]


def test_restic_nanosecond_times_parse():
    t = ob._parse_time("2026-10-18T05:10:01.123456789+08:00")
    assert t.utcoffset() == timedelta(hours=8) and t.microsecond == 123456


@pytest.mark.parametrize("hm,inside", [((1, 34), False), ((1, 35), True), ((3, 0), True), ((4, 54), True),
                                       ((4, 55), False), ((5, 10), False)])
def test_night_window(hm, inside):
    assert ob.in_quiet_hours(datetime(2026, 10, 18, *hm)) is inside


# ── config guards ────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def configured(tmp_path):
    cfg = tmp_path / "home/.config/zoe-backup"
    cfg.mkdir(parents=True)
    binp = tmp_path / "bin"
    binp.mkdir()
    for b in ("restic", "age"):
        (binp / b).write_text("#!/bin/sh\n")
        (binp / b).chmod(0o755)
    (cfg / "restic.pass").write_text("box-key")
    (cfg / "age-recipients.txt").write_text("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n")
    (cfg / "offbox.env").write_text(
        "RESTIC_REPOSITORY=rest:https://backup.example/hx/\nRESTIC_REST_USERNAME=hx\nRESTIC_REST_PASSWORD=p\n"
        f"RESTIC_PASSWORD_FILE={cfg / 'restic.pass'}\nZOE_BACKUP_AGE_RECIPIENTS_FILE={cfg / 'age-recipients.txt'}\n"
        f"RESTIC_BIN={binp / 'restic'}\nAGE_BIN={binp / 'age'}\n")
    for f in cfg.iterdir():
        f.chmod(0o600)
    return ob.Paths(home=tmp_path / "home", checkout=tmp_path / "co", config_dir=cfg, state_dir=tmp_path / "s",
                    stage_dir=tmp_path / "st")


def test_good_config_loads_and_env_carries_no_url_credentials(configured):
    c = ob.load_config(configured)
    assert "@" not in c.env["RESTIC_REPOSITORY"]
    assert c.limit_upload_kib == 2048


def test_age_identity_on_the_box_is_refused(configured):
    (configured.config_dir / "id.txt").write_text("AGE-SECRET-KEY-1QQQQ\n")
    (configured.config_dir / "id.txt").chmod(0o600)
    with pytest.raises(ob.Refused, match="IDENTITY"):
        ob.load_config(configured)


def test_group_readable_password_is_refused(configured):
    (configured.config_dir / "restic.pass").chmod(0o640)
    with pytest.raises(ob.Refused, match="chmod 600"):
        ob.load_config(configured)


def test_credentials_in_the_repo_url_are_refused(configured):
    env = configured.config_dir / "offbox.env"
    env.write_text(env.read_text().replace("rest:https://backup", "rest:https://hx:secret@backup"))
    with pytest.raises(ob.Refused, match="credentials in the URL"):
        ob.load_config(configured)


def test_a_private_key_as_recipient_is_refused(configured):
    (configured.config_dir / "age-recipients.txt").write_text("not-a-recipient\n")
    with pytest.raises(ob.Refused, match="PUBLIC recipients"):
        ob.load_config(configured)


@pytest.mark.parametrize("rel,ok", [("zoe/data/a.json", True), ("/etc/cron.d/x", False), ("../../.bashrc", False),
                                    ("zoe/../../x", False), ("", False), (None, False)])
def test_restore_paths_from_the_index_cannot_escape(tmp_path, rel, ok):
    """Codex review: the index is attacker-writable (the recipient is public), so restore must not trust it."""
    assert (ob.safe_join(tmp_path, rel) is not None) is ok


def test_restore_refuses_on_a_box_that_runs_the_backup(configured, tmp_path):
    units = configured.home / ".config/systemd/user"
    units.mkdir(parents=True)
    (units / "zoe-offbox-backup.timer").write_text("[Timer]\n")
    cfg = ob.load_config(configured)
    with pytest.raises(ob.Refused, match="never be used on the box"):
        ob.cmd_restore(configured, cfg, tmp_path / "id.txt", "household", tmp_path / "out", "latest")
    (units / "zoe-offbox-backup.timer").unlink()
    with pytest.raises(ob.Refused, match="inside the backup config dir"):
        ob.cmd_restore(configured, cfg, configured.config_dir / "id.txt", "household", tmp_path / "out", "latest")


def test_unconfigured_is_exit_2_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OFFBOX_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_OFFBOX_NO_ALERT", "1")
    assert ob.main(["check"]) == 2


# ── the411 artefacts + units ─────────────────────────────────────────────────────────────────────────────────────


def test_the411_compose_is_append_only_private_loopback_and_digest_pinned():
    text = (THE411 / "compose.yaml").read_text()
    imgs = re.findall(r"image:\s*(\S+)", text)
    assert len(imgs) == 2 and all(re.search(r":[\w.]+@sha256:[0-9a-f]{64}$", i) for i in imgs), imgs
    opts = re.search(r"OPTIONS=(.+)", text).group(1)
    assert "--append-only" in opts and "--private-repos" in opts and "--no-auth" not in opts
    ports = re.findall(r'^\s*-\s*"([^"]+)"', text, re.M)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports), ports
    assert "network_mode: none" in text      # the prune job needs no network


def test_restic_version_matches_between_box_and_the411():
    box = re.search(r"^RESTIC_VERSION=(\S+)", (REPO / "scripts/setup/offbox-backup/install_tools.sh").read_text(),
                    re.M).group(1)
    assert f"restic/restic:{box}@" in (THE411 / "compose.yaml").read_text()


def _calendar_hm(unit: Path) -> tuple[int, int]:
    m = re.search(r"OnCalendar=.*?(\d\d):(\d\d)", unit.read_text())
    return int(m.group(1)), int(m.group(2))


@pytest.mark.parametrize("timer", ["zoe-offbox-backup.timer", "zoe-backup-verify.timer"])
def test_offbox_timers_are_outside_the_night_window(timer):
    h, m = _calendar_hm(SYSTEMD / timer)
    assert not ob.in_quiet_hours(datetime(2026, 1, 1, h, m))
    # the randomised delay must not push it back inside: 10 min after the start is still outside
    assert not ob.in_quiet_hours(datetime(2026, 1, 1, h, m) + timedelta(minutes=10))


@pytest.mark.parametrize("unit", ["zoe-backup.service", "zoe-backup-verify.service", "zoe-offbox-backup.service",
                                  "zoe-health.service", "zoe-merge-queue.service", "zoe-opensrc-refresh.service",
                                  "github-runner.service"])
def test_tracked_units_inline_no_secret_and_no_retired_env(unit):
    text = (SYSTEMD / unit).read_text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"(PASSWORD|TOKEN|SECRET|API_KEY)\s*=", body, re.I), unit
    assert ".hermes" not in body and "/home/zoe" not in body, unit     # %h, and no retired Hermes env


def test_offbox_unit_runs_the_tracked_script():
    text = (SYSTEMD / "zoe-offbox-backup.service").read_text()
    assert "scripts/maintenance/zoe_offbox.py backup" in text
    assert (REPO / "scripts/maintenance/zoe_offbox.py").exists()
