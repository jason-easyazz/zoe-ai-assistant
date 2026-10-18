"""scripts/maintenance/migrate_hermes_env.py - the owner-run ~/.hermes/.env -> unit-env migration (architecture deep dive A6).

Every test uses fake env files in tmp_path. The properties that matter are the ones that, broken, would either lose a
secret or leak one: never overwrite, never print a value, keep file modes, idempotent, back up first.
"""
from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "migrate_hermes_env.py"

# Fake secrets: distinctive so a leak into output is unmistakable.
SECRET_A = "sk-FAKE-leakcheck-aaaaaaaa"
SECRET_B = "tok-FAKE-leakcheck-bbbbbbbb"
SECRET_OLD = "own-FAKE-keepme-cccccccc"
SECRET_NEW = "hermes-FAKE-differs-dddddddd"


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("migrate_hermes_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def box(tmp_path, mig, monkeypatch):
    """A fake home with a hermes env, a repo, and three units: data (python), health (shell), retired."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))          # nothing here may ever resolve to the real ~/.zoe or ~/.hermes
    repo = home / "assistant"
    (repo / "services" / "zoe-data").mkdir(parents=True)
    (repo / "scripts").mkdir()
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    (home / "bin").mkdir()
    hermes = home / ".hermes" / ".env"
    hermes.parent.mkdir()
    hermes.write_text(
        f"OPENAI_API_KEY={SECRET_A}\n"
        f"ZOE_DEVICE_TOKEN={SECRET_B}\n"
        f"TELEGRAM_BOT_TOKEN={SECRET_B}\n"
        "TELEGRAM_HOME_CHANNEL=12345\n"
        "NOTIFY_KEY=aliasme\n"
        f"OWN_ALREADY={SECRET_NEW}\n"
        "SHARED_SAME=samevalue\n"
        "BROWSERBASE_PROXIES=true\n"
        "TERMINAL_TIMEOUT=60\n"
        "API_SERVER_PORT=8642\n"
        "UNUSED_THING=zzz\n"
        "EMPTY_ONE=\n"
        "# a comment line\n"
        "export EXPORTED_KEY=expv\n"
    )
    hermes.chmod(0o600)
    # the code the units run
    (repo / "services" / "zoe-data" / "main.py").write_text(
        'import os\nTOKEN = os.environ["ZOE_DEVICE_TOKEN"]\nX = os.getenv("ZOE_NOTIFY_KEY")\n'
        'os.getenv("EXPORTED_KEY"); os.getenv("OWN_ALREADY"); os.getenv("SHARED_SAME")\n')
    (repo / "scripts" / "thing.py").write_text("# nothing relevant\n")
    own = repo / "services" / "zoe-data" / ".env"
    own.write_text(f"OWN_ALREADY={SECRET_OLD}\nSHARED_SAME=samevalue\nKEEP=1")      # no trailing newline, on purpose
    own.chmod(0o600)
    (home / "bin" / "health.sh").write_text('#!/bin/sh\necho "$ZOE_DEVICE_TOKEN"\n')
    (units / "zoe-data.service").write_text(
        "[Service]\nWorkingDirectory=%h/assistant/services/zoe-data\nExecStart=/usr/bin/python3 -m uvicorn main:app\n"
        "EnvironmentFile=%h/assistant/services/zoe-data/.env\nEnvironmentFile=-%h/.hermes/.env\n")
    (units / "zoe-health.service").write_text(
        "[Service]\nExecStart=%h/bin/health.sh\nEnvironmentFile=-%h/.hermes/.env\nEnvironment=\"INLINE_ONE=1\"\n")
    (units / "hermes-agent.service").write_text("[Service]\nExecStart=/x\nEnvironmentFile=-%h/.hermes/.env\n")
    (units / "zoe-other.service").write_text("[Service]\nExecStart=/x\nEnvironmentFile=%h/assistant/services/zoe-data/.env\n")
    return {"home": home, "repo": repo, "units": units, "hermes": hermes, "own": own}


def run_plan(mig, box, **kw):
    return mig.plan_migration(box["units"], box["hermes"], box["repo"], box["home"], **kw)


def by_unit(plans):
    return {p.unit: p for p in plans}


def test_only_units_that_load_hermes_and_are_not_retired(mig, box):
    plans = by_unit(run_plan(mig, box))
    assert set(plans) == {"zoe-data.service", "zoe-health.service"}      # hermes-agent retired; zoe-other never loaded it


def test_zoe_data_plan_copy_skip_drop(mig, box):
    p = by_unit(run_plan(mig, box))["zoe-data.service"]
    assert p.target == box["repo"] / "services" / "zoe-data" / ".env"
    assert "ZOE_DEVICE_TOKEN" in p.copy                       # read by the unit's code
    assert "OPENAI_API_KEY" in p.copy                         # SDK-implicit name in a python unit
    assert "EXPORTED_KEY" in p.copy                           # `export` lines parse
    assert set(p.dropped_revoked) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"}   # revoked: dead whatever the code says
    assert "TELEGRAM_BOT_TOKEN" not in p.copy and "TELEGRAM_BOT_TOKEN" not in p.skipped_unused
    assert "NOTIFY_KEY" in p.skipped_unused                    # the code reads ZOE_NOTIFY_KEY: a different name...
    assert p.hints["NOTIFY_KEY"] == ["ZOE_NOTIFY_KEY"]         # ...reported, not silently aliased
    assert "UNUSED_THING" in p.skipped_unused
    assert set(p.dropped_dead) >= {"BROWSERBASE_PROXIES", "TERMINAL_TIMEOUT", "API_SERVER_PORT"}
    assert "EMPTY_ONE" in p.skipped_empty
    assert "SHARED_SAME" in p.shadowed_same
    assert "OWN_ALREADY" in p.shadowed_differs                # hermes used to win; now reported, never copied
    assert "OWN_ALREADY" not in p.copy and "SHARED_SAME" not in p.copy


def test_dead_name_is_kept_when_the_code_reads_it(mig, box):
    (box["repo"] / "services" / "zoe-data" / "main.py").write_text('import os\nos.getenv("API_SERVER_PORT")\n')
    p = by_unit(run_plan(mig, box))["zoe-data.service"]
    assert "API_SERVER_PORT" in p.copy and "API_SERVER_PORT" not in p.dropped_dead      # drop only after the grep finds nothing


def test_shell_unit_gets_private_file_and_no_sdk_implicit_copy(mig, box):
    p = by_unit(run_plan(mig, box))["zoe-health.service"]
    assert p.target == box["home"] / ".zoe" / "env" / "zoe-health.env"        # never the 664 repo-root .env
    assert p.copy == ["ZOE_DEVICE_TOKEN"]                                      # the script names it; a shell script needs no SDK key
    assert "OPENAI_API_KEY" in p.skipped_unused
    assert p.needs_envfile_line == "EnvironmentFile=-%h/.zoe/env/zoe-health.env"


def test_unit_needing_nothing_needs_no_new_envfile_line(mig, box):
    (box["home"] / "bin" / "health.sh").write_text("#!/bin/sh\ntrue\n")
    p = by_unit(run_plan(mig, box))["zoe-health.service"]
    assert p.copy == [] and p.needs_envfile_line is None


def test_inline_environment_names_count_as_already_provided(mig, box):
    (box["hermes"]).write_text(box["hermes"].read_text() + "INLINE_ONE=fromhermes\n")
    (box["home"] / "bin" / "health.sh").write_text('#!/bin/sh\necho "$INLINE_ONE"\n')
    p = by_unit(run_plan(mig, box))["zoe-health.service"]
    assert "INLINE_ONE" not in p.copy


def test_unknown_code_location_defaults_to_copy(mig, box):
    (box["units"] / "zoe-health.service").write_text("[Service]\nExecStart=/nonexistent/bin/x\nEnvironmentFile=-%h/.hermes/.env\n")
    p = by_unit(run_plan(mig, box))["zoe-health.service"]
    assert "UNUSED_THING" in p.copy and "OPENAI_API_KEY" in p.copy                # unsure -> copy


def test_copy_unread_flag_copies_everything_non_empty(mig, box):
    p = by_unit(run_plan(mig, box, copy_unread=True))["zoe-data.service"]
    assert "UNUSED_THING" in p.copy and "EMPTY_ONE" not in p.copy
    assert "TELEGRAM_BOT_TOKEN" not in p.copy and "TELEGRAM_HOME_CHANNEL" not in p.copy        # revoked beats --copy-unread


def test_a_revoked_token_never_reaches_a_target_file_and_the_report_says_why(mig, box, capsys):
    """Break: remove the REVOKED_NAMES branch -> the revoked token is copied (the shell-unit plan copies it too)."""
    assert _apply(mig, box) == 0
    out = capsys.readouterr().out
    assert "DROP TELEGRAM_BOT_TOKEN: revoked" in out and "DROP TELEGRAM_HOME_CHANNEL" in out
    for f in (box["own"], box["home"] / ".zoe" / "env" / "zoe-health.env"):
        if f.exists():
            assert "TELEGRAM" not in f.read_text()


def test_dry_run_writes_nothing_and_prints_no_value(mig, box, capsys):
    before = {f: f.read_bytes() for f in (box["own"], box["hermes"])}
    assert mig.main(["--home", str(box["home"]), "--units-dir", str(box["units"]), "--hermes-env", str(box["hermes"]), "--repo-root", str(box["repo"]),
                     "--backup-root", str(box["home"] / "bk")]) == 0
    out = capsys.readouterr()
    text = out.out + out.err
    for secret in (SECRET_A, SECRET_B, SECRET_OLD, SECRET_NEW, "samevalue", "expv"):
        assert secret not in text
    assert "ZOE_DEVICE_TOKEN" in text and "DRY-RUN" in text
    assert {f: f.read_bytes() for f in before} == before
    assert not (box["home"] / ".zoe").exists() and not (box["home"] / "bk").exists()


def _apply(mig, box):
    return mig.main(["--apply", "--home", str(box["home"]), "--units-dir", str(box["units"]), "--hermes-env", str(box["hermes"]),
                     "--repo-root", str(box["repo"]), "--backup-root", str(box["home"] / "bk")])


def test_apply_copies_keeps_existing_backs_up_and_keeps_mode(mig, box, capsys):
    old_own = box["own"].read_text()
    assert _apply(mig, box) == 0
    out = capsys.readouterr()
    for secret in (SECRET_A, SECRET_B, SECRET_OLD, SECRET_NEW):
        assert secret not in out.out + out.err                                  # --apply prints names/paths only too
    body = box["own"].read_text()
    assert f"OPENAI_API_KEY={SECRET_A}" in body and f"ZOE_DEVICE_TOKEN={SECRET_B}" in body
    assert "export EXPORTED_KEY=expv" in body                                   # verbatim line copied
    assert f"OWN_ALREADY={SECRET_OLD}" in body and SECRET_NEW not in body       # never overwritten
    assert "UNUSED_THING" not in body and "BROWSERBASE_PROXIES" not in body
    assert "KEEP=1\n" in body                                                   # a missing trailing newline did not glue lines together
    assert stat.S_IMODE(box["own"].stat().st_mode) == 0o600
    backups = list((box["home"] / "bk").glob("migrate-hermes-env-*/.env"))
    assert len(backups) == 1 and backups[0].read_text() == old_own              # backup is the pre-edit content
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(backups[0].parent.stat().st_mode) == 0o700
    new_file = box["home"] / ".zoe" / "env" / "zoe-health.env"
    assert f"ZOE_DEVICE_TOKEN={SECRET_B}" in new_file.read_text() and stat.S_IMODE(new_file.stat().st_mode) == 0o600
    assert box["hermes"].read_text().count("OPENAI_API_KEY") == 1               # the source is never modified


def test_apply_is_idempotent(mig, box):
    _apply(mig, box)
    first = {f: f.read_bytes() for f in (box["own"], box["home"] / ".zoe" / "env" / "zoe-health.env")}
    n_backups = len(list((box["home"] / "bk").rglob("*")))
    _apply(mig, box)
    assert {f: f.read_bytes() for f in first} == first
    assert len(list((box["home"] / "bk").rglob("*"))) == n_backups               # a no-op run makes no new backup


def test_apply_preserves_a_looser_existing_mode(mig, box):
    box["own"].chmod(0o640)
    _apply(mig, box)
    assert stat.S_IMODE(box["own"].stat().st_mode) == 0o640                      # keeps the file's mode; never silently widens or narrows


def test_missing_hermes_env_is_a_clean_noop(mig, box, capsys):
    box["hermes"].unlink()
    assert _apply(mig, box) == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_break_the_fix_overwrite_would_be_caught(mig, box):
    """Negative control: a naive 'append every hermes name' would clobber OWN_ALREADY; this suite must see that."""
    _apply(mig, box)
    values = mig.parse_env(box["own"]).values
    assert values["OWN_ALREADY"] == SECRET_OLD
    assert values["OWN_ALREADY"] != SECRET_NEW
