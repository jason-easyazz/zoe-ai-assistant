"""local_text_logs hardening (cross-review of PR #2110): the late writer, the backup expiry, the fail-closed sweep.

Each test names the line that, removed, turns it red (break-the-fix controls).
"""
from __future__ import annotations

import json
import os

import pytest

import local_text_logs as ltl

pytestmark = pytest.mark.ci_safe

DAY = 86400.0
NOW = 2_000_000_000.0


def rec(utt: str, text: str, age_days: float) -> str:
    return json.dumps({"ts": NOW - age_days * DAY, "utt": utt, "utt_text": text, "mode": "shadow2"}, sort_keys=True)


def load(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    p = tmp_path / "data" / "router_head_shadow.jsonl"
    p.parent.mkdir()
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(p))
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(tmp_path / "misses.jsonl"))
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "adhoc"))
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    p.write_text(rec("a" * 12, "old words", 99) + "\n")
    return p


def test_a_line_appended_between_the_last_read_and_the_replace_is_not_lost(shadow, monkeypatch):
    """The writer opens the file afresh per append, so a line can land in the OLD inode after our last read.
    Break: remove the post-replace catch-up (``late = old.read()``) -> this line vanishes."""
    real_replace = os.replace

    def writer_lands_then_replace(src, dst):
        with open(shadow, "a") as fh:
            fh.write(rec("z" * 12, "late line", 0) + "\n")
        real_replace(src, dst)

    monkeypatch.setattr(ltl.os, "replace", writer_lands_then_replace)
    assert ltl.sweep(NOW)["router_shadow"] == 1
    r = load(shadow)
    assert [x["utt"] for x in r] == ["a" * 12, "z" * 12] and r[1]["utt_text"] == "late line" and "utt_text" not in r[0]


def test_a_writer_holding_the_old_inode_across_the_replace_is_caught_too(shadow, monkeypatch):
    real_replace = os.replace
    held = open(shadow, "a")                                    # a writer that opened the OLD inode before the replace

    def replace_then_late_write(src, dst):
        real_replace(src, dst)
        held.write(rec("y" * 12, "stalled writer", 0) + "\n")
        held.flush()

    monkeypatch.setattr(ltl.os, "replace", replace_then_late_write)
    try:
        ltl.sweep(NOW)
    finally:
        held.close()
    assert [x["utt"] for x in load(shadow)] == ["a" * 12, "y" * 12]


def test_the_rewrite_fsyncs_the_temp_file_and_the_directory(shadow, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(ltl.os, "fsync", lambda fd: synced.append(fd) or real(fd))
    ltl.sweep(NOW)
    assert len(synced) >= 2                                     # the temp file, then the directory


def test_adhoc_backups_expire_after_14_days_and_only_ours(tmp_path, shadow):
    """The redaction backup holds the raw text and a forget cannot rewrite it, so its age bound is ENFORCED, not noted."""
    root = tmp_path / "adhoc"
    old = root / "router-shadow-20260101-000000"
    secret = root / "migrate-hermes-env-20260101-000000"
    fresh = root / "router-shadow-20261017-000000"
    other = root / "homeassistant-config-old"
    for d in (old, secret, fresh, other):
        d.mkdir(parents=True)
        (d / "x").write_text("raw words")
    for d in (old, secret, other):
        os.utime(d, (NOW - 40 * DAY, NOW - 40 * DAY))
    os.utime(fresh, (NOW - 2 * DAY, NOW - 2 * DAY))
    assert ltl.prune_adhoc_backups(NOW) == 2
    assert not old.exists() and not secret.exists() and fresh.exists() and other.exists()      # never an unrelated backup
    assert ltl.sweep(NOW)["adhoc_backups"] == 0


def test_the_dead_units_backup_expires_too(tmp_path, shadow):
    """``cleanup_dead_units.py --apply`` writes ``~/.zoe/backups/dead-units-<ts>/``; it must age out like the other owner-script
    backups. Break: drop ``"dead-units-"`` from ``ADHOC_BACKUP_PREFIXES`` -> the old directory is never pruned."""
    root = tmp_path / "adhoc"
    old = root / "dead-units-20260101-000000"
    fresh = root / "dead-units-20261017-000000"
    for d in (old, fresh):
        d.mkdir(parents=True)
        (d / "zoe-dreaming.service").write_text("[Unit]")
    os.utime(old, (NOW - 40 * DAY, NOW - 40 * DAY))
    os.utime(fresh, (NOW - 2 * DAY, NOW - 2 * DAY))
    assert ltl.prune_adhoc_backups(NOW) == 1
    assert not old.exists() and fresh.exists()


def test_the_backup_expiry_runs_even_when_text_retention_is_off(tmp_path, shadow, monkeypatch):
    d = tmp_path / "adhoc" / "router-shadow-20260101-000000"
    d.mkdir(parents=True)
    os.utime(d, (NOW - 40 * DAY, NOW - 40 * DAY))
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "keep")
    assert ltl.sweep(NOW) == {"adhoc_backups": 1} and not d.exists()


def test_a_failing_part_is_reported_as_minus_one(shadow, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk")
    monkeypatch.setattr(ltl, "rewrite", boom)
    monkeypatch.setattr(ltl, "prune_adhoc_backups", boom)
    out = ltl.sweep(NOW)
    assert out["router_shadow"] == -1 and out["adhoc_backups"] == -1


def test_the_adhoc_backup_dir_is_pinned_off_the_real_one():
    pin = os.environ.get("ZOE_ADHOC_BACKUP_DIR", "")
    assert "zoe-test-stores-" in pin, "tests/conftest.py must pin ZOE_ADHOC_BACKUP_DIR: sweep() deletes backups"
