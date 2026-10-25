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


@pytest.fixture(autouse=True)
def _restore_settings_env():
    """The code under test writes ``os.environ`` DIRECTLY (``apply_env_files`` -> ``setdefault``), which ``monkeypatch`` cannot undo
    for a key it never set. Snapshot every key the sweep may set and restore it in a ``finally`` whatever the test did."""
    keys = ("ZOE_TEXT_LOG_RETENTION_DAYS", "ZOE_ROUTER_HEAD_LOG", "ZOE_INTENT_MISS_LOG", "ZOE_ADHOC_BACKUP_DIR")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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


def test_malformed_rows_are_selected_dropped_and_checked(tool, logs, tmp_path, capsys, monkeypatch):
    """Codex round 3 on #2110. Break: skip ``_malformed`` in plan/post-check -> a torn row holding words is neither counted, dropped
    nor flagged."""
    logs.write_text(logs.read_text() + 'torn {"utt": "eeeeeeeeeeee", "utt_text": "TORN-WORDS\n')
    assert run(tool, tmp_path) == 0
    assert "3 selected" in capsys.readouterr().out                      # 2 old text rows + the malformed one
    assert run(tool, tmp_path, "--apply") == 0
    assert "TORN-WORDS" not in logs.read_text() and len(recs(logs)) == 4
    # the post-check flags a malformed row the rewrite failed to remove
    logs.write_text(logs.read_text() + "again TORN-WORDS\n")
    ltl = tool.load_ltl()
    monkeypatch.setattr(tool, "load_ltl", lambda: ltl)
    monkeypatch.setattr(ltl, "rewrite", lambda *a, **k: 0)
    assert run(tool, tmp_path, "--apply") == 1
    assert "malformed rows remain" in capsys.readouterr().out


def test_saved_retention_and_log_path_are_honoured(tool, logs, tmp_path, monkeypatch, capsys):
    """PR #2110: a bare --apply must read the zoe-data env files like the hourly job. Break: drop load_saved_settings() ->
    keep is ignored (30 days applied) and the custom log is never looked at."""
    rotate = importlib.util.spec_from_file_location("rotate_service_logs", SCRIPT.parent / "rotate_service_logs.py")
    rmod = importlib.util.module_from_spec(rotate)
    sys.modules["rotate_service_logs"] = rmod
    rotate.loader.exec_module(rmod)
    custom = tmp_path / "custom.jsonl"
    custom.write_text(logs.read_text())
    env = tmp_path / "zoe-data.env"
    env.write_text(f'ZOE_TEXT_LOG_RETENTION_DAYS=keep\nZOE_ROUTER_HEAD_LOG="{custom}"\n')
    monkeypatch.setattr(rmod, "ENV_FILES", (env,))
    monkeypatch.setenv("ZOE_TEXT_LOG_RETENTION_DAYS", "x")      # registers teardown restore (apply_env_files writes os.environ directly)
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS")
    monkeypatch.delenv("ZOE_ROUTER_HEAD_LOG")
    before = custom.read_bytes()
    assert run(tool, tmp_path, "--apply") == 0
    assert "nothing selected" in capsys.readouterr().out
    assert custom.read_bytes() == before
    env.write_text(f'ZOE_TEXT_LOG_RETENTION_DAYS=30\nZOE_ROUTER_HEAD_LOG="{custom}"\n')
    monkeypatch.delenv("ZOE_TEXT_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("ZOE_ROUTER_HEAD_LOG", raising=False)
    assert run(tool, tmp_path, "--apply") == 0
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(logs))            # restore the fixture pin on teardown (script set it)
    assert "SECRET-OLD-ONE" not in custom.read_text() and "recent-words" in custom.read_text()
    assert "SECRET-OLD-ONE" in logs.read_text()                 # the default-path log was not the one acted on


def test_default_backup_root_is_resolved_after_saved_settings(tool, logs, tmp_path, monkeypatch, capsys):
    """PR #2110 review: the default backup dir must be the one the hourly job PRUNES (ZOE_ADHOC_BACKUP_DIR, which may come from the
    saved env files). Break: resolve the default before load_saved_settings() -> the backup lands in ~/.zoe/backups."""
    pruned = tmp_path / "pruned"
    monkeypatch.setattr(tool, "load_saved_settings", lambda: monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(pruned)))
    assert tool.main(["--apply"]) == 0
    assert len(list(pruned.glob("router-shadow-*"))) == 1
    assert "WARNING" not in capsys.readouterr().err


def test_explicit_backup_root_outside_the_pruned_dir_warns(tool, logs, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "pruned"))
    assert tool.main(["--backup-root", str(tmp_path / "elsewhere"), "--apply"]) == 0
    assert "will NOT be deleted" in capsys.readouterr().err


def test_a_backup_root_nested_under_the_pruned_dir_still_warns(tool, logs, tmp_path, monkeypatch, capsys):
    """PR #2110 review: the hourly prune scans only direct children, so <pruned>/manual/<backup> never expires. Break: accept
    any directory beneath the pruned root -> no warning."""
    monkeypatch.setenv("ZOE_ADHOC_BACKUP_DIR", str(tmp_path / "pruned"))
    assert tool.main(["--backup-root", str(tmp_path / "pruned" / "manual"), "--apply"]) == 0
    assert "will NOT be deleted" in capsys.readouterr().err


def test_a_rotation_between_backup_copies_cannot_happen(tool, logs, tmp_path, monkeypatch):
    """PR #2110 review: the lock is held across enumerate + backup + rewrite, so a rotation attempted between two copies is refused
    (TimeoutError) and every segment that gets stripped was backed up first. Break: take the lock only inside _apply -> the
    attempted rotation succeeds."""
    ltl = tool.load_ltl()
    monkeypatch.setattr(tool, "load_ltl", lambda: ltl)
    monkeypatch.setattr(ltl, "LOCK_WAIT_S", 0.2)
    now = time.time()
    for n in (1, 2):
        seg = logs.with_name(f"{logs.name}.{n}")
        seg.write_text(json.dumps({"ts": now - 90 * DAY, "utt": f"{n}" * 12, "utt_text": f"SEG-{n}-SECRET"}) + "\n")
    outcomes = []
    real = tool.shutil.copy2

    def copy_then_try_rotation(src, dst, *a, **k):
        real(src, dst, *a, **k)
        try:
            with ltl.locked(ltl.ROUTER_SHADOW):
                outcomes.append("rotated")
        except TimeoutError:
            outcomes.append("refused")

    monkeypatch.setattr(tool.shutil, "copy2", copy_then_try_rotation)
    assert run(tool, tmp_path, "--apply") == 0
    assert outcomes and set(outcomes) == {"refused"}
    backed = "".join(p.read_text() for p in (tmp_path / "bk").glob("router-shadow-*/*"))
    assert "SEG-1-SECRET" in backed and "SEG-2-SECRET" in backed and "SECRET-OLD-ONE" in backed
