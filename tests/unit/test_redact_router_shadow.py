"""scripts/maintenance/redact_router_shadow.py - the owner-run strip of utterance text from the EXISTING router shadow log (A6).

Throwaway logs only. The properties: dry run changes nothing, apply backs up first (0700/0600), the result keeps every record's hash
and routing fields, nothing prints the words, a failed post-check keeps the backup.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "redact_router_shadow.py"
DAY = 86400.0


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("redact_router_shadow", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def logs(tmp_path, monkeypatch):
    p = tmp_path / "data" / "router_head_shadow.jsonl"
    p.parent.mkdir()
    now = time.time()
    p.write_text("\n".join(json.dumps(r, sort_keys=True) for r in (
        {"ts": now - 80 * DAY, "utt": "a" * 12, "utt_text": "SECRET-OLD-ONE", "mode": "active", "two_stage_domain": "lists"},
        {"ts": now - 40 * DAY, "utt": "b" * 12, "utt_text": "SECRET-OLD-TWO", "mode": "shadow2"},
        {"ts": now - 1 * DAY, "utt": "c" * 12, "utt_text": "recent-words", "mode": "shadow2"},
        {"ts": now - 90 * DAY, "utt": "d" * 12, "mode": "shadow2"},
    )) + "\n")
    p.chmod(0o640)
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(p))
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(tmp_path / "none.jsonl"))
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    return p


def run(tool, tmp_path, *args):
    return tool.main(["--backup-root", str(tmp_path / "bk"), *args])


def recs(p):
    return [json.loads(l) for l in p.read_text().splitlines()]


def test_dry_run_is_the_default_and_changes_nothing(tool, logs, tmp_path, capsys):
    before = logs.read_bytes()
    assert run(tool, tmp_path) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "2 selected" in out and "SECRET" not in out
    assert logs.read_bytes() == before and not (tmp_path / "bk").exists()


def test_apply_backs_up_first_then_strips_the_old_text_only(tool, logs, tmp_path, capsys):
    assert run(tool, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "SECRET" not in out and "STILL HOLDS THE TEXT" in out
    r = recs(logs)
    assert [x["utt"] for x in r] == ["a" * 12, "b" * 12, "c" * 12, "d" * 12]          # every hash survives
    assert "utt_text" not in r[0] and "utt_text" not in r[1] and r[2]["utt_text"] == "recent-words"
    assert r[0]["mode"] == "active" and r[0]["two_stage_domain"] == "lists"           # routing fields intact
    (bdir,) = list((tmp_path / "bk").iterdir())
    assert stat.S_IMODE(bdir.stat().st_mode) == 0o700
    (copy,) = list(bdir.iterdir())
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600 and "SECRET-OLD-ONE" in copy.read_text()   # the backup is the raw copy
    assert stat.S_IMODE(logs.stat().st_mode) == 0o640


def test_apply_is_idempotent(tool, logs, tmp_path, capsys):
    run(tool, tmp_path, "--apply")
    first = logs.read_bytes()
    capsys.readouterr()
    assert run(tool, tmp_path, "--apply") == 0
    assert "nothing to strip" in capsys.readouterr().out and logs.read_bytes() == first
    assert len(list((tmp_path / "bk").iterdir())) == 1                                  # no second backup for a no-op


def test_all_strips_every_record_and_drop_backup_removes_the_raw_copy(tool, logs, tmp_path):
    assert run(tool, tmp_path, "--apply", "--all", "--drop-backup") == 0
    assert all("utt_text" not in r for r in recs(logs))
    assert list((tmp_path / "bk").iterdir()) == []


def test_days_override_widens_or_narrows_the_window(tool, logs, tmp_path, capsys):
    run(tool, tmp_path, "--days", "60")
    assert "1 selected" in capsys.readouterr().out                                      # only the 80-day-old record


def test_a_failed_post_check_keeps_the_backup_and_exits_nonzero(tool, logs, tmp_path, monkeypatch, capsys):
    """Negative control: make the rewrite a no-op - the check must notice text still there and refuse to drop the backup."""
    ltl = tool.load_ltl()
    monkeypatch.setattr(tool, "load_ltl", lambda: ltl)
    monkeypatch.setattr(ltl, "rewrite", lambda *a, **k: 0)
    assert run(tool, tmp_path, "--apply", "--drop-backup") == 1
    assert "CHECK FAILED" in capsys.readouterr().out
    assert len(list((tmp_path / "bk").iterdir())) == 1


def test_keep_without_days_selects_nothing(tool, logs, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "keep")
    assert run(tool, tmp_path, "--apply") == 0
    assert "nothing selected" in capsys.readouterr().out and "SECRET-OLD-ONE" in logs.read_text()


def _backup_dirs(tmp_path):
    return list((tmp_path / "bk").iterdir()) if (tmp_path / "bk").exists() else []


def test_drop_backup_failing_to_delete_is_an_error_not_a_success(tool, logs, tmp_path, monkeypatch, capsys):
    """The backup holds the raw words. Break: restore ``ignore_errors=True`` -> exit 0 and "backup deleted" while the text stays."""
    def boom(path, *a, **k):
        raise PermissionError(13, "synthetic: read-only")
    monkeypatch.setattr(tool.shutil, "rmtree", boom)
    assert run(tool, tmp_path, "--apply", "--all", "--drop-backup") == 1
    cap = capsys.readouterr()
    assert "deleted" not in cap.out.replace("Delete the backup", "")                  # never claims success
    assert "STILL HOLD" in cap.err and str(_backup_dirs(tmp_path)[0]) in cap.err
    assert "SECRET-OLD-ONE" not in cap.out + cap.err                                  # the words are never printed


def test_drop_backup_that_silently_leaves_the_directory_is_still_an_error(tool, logs, tmp_path, monkeypatch, capsys):
    """rmtree returning without raising is not proof: the path must be verified gone before "deleted" is printed."""
    monkeypatch.setattr(tool.shutil, "rmtree", lambda *a, **k: None)
    assert run(tool, tmp_path, "--apply", "--all", "--drop-backup") == 1
    cap = capsys.readouterr()
    assert "STILL PRESENT" in cap.err and "backup deleted" not in cap.out


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_drop_backup_in_a_read_only_directory_fails_loudly(tool, logs, tmp_path, monkeypatch, capsys):
    """A real (unmocked) failure: the backup's own directory is read-only after the copy, so its files cannot be unlinked."""
    real_chmod = os.chmod
    state = {}

    def lock_after_copy(path, mode, *a, **k):
        real_chmod(path, mode, *a, **k)
        if Path(path).name.startswith("00-") and mode == 0o600:       # the last thing --apply does to a backup file
            state["dir"] = Path(path).parent
            real_chmod(state["dir"], 0o500)

    monkeypatch.setattr(os, "chmod", lock_after_copy)
    try:
        code = run(tool, tmp_path, "--apply", "--all", "--drop-backup")
    finally:
        monkeypatch.undo()
        if "dir" in state:
            real_chmod(state["dir"], 0o700)
    cap = capsys.readouterr()
    assert code == 1 and "STILL" in cap.err and "backup deleted" not in cap.out
    assert any(f.name.startswith("00-") for f in state["dir"].iterdir())               # the raw copy really is still on disk


def test_drop_backup_that_works_still_exits_zero_and_says_verified_negative_control(tool, logs, tmp_path, capsys):
    assert run(tool, tmp_path, "--apply", "--all", "--drop-backup") == 0
    assert "verified gone" in capsys.readouterr().out and _backup_dirs(tmp_path) == []
