"""Off-box backup, readiness for the owner's target choice: the target adapters (only a LOCAL-DIRECTORY one is new; no cloud
SDK, no network), the loud failure when the target or a key is missing, the dry-run plan (counts and sizes, never a name),
box-side retention for a target the box owns, and the drill canary that proves an ENCRYPTED restore decrypts.

Hermetic: temp trees, a fake ``age`` / ``age-keygen`` (POSIX sh stubs that keep a recipient header, so a file encrypted to
one key cannot be opened with another), ``restic`` never run (monkeypatched). The keys here are LAB keys made in tmp only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
if "zoe_offbox" in sys.modules:
    ob = sys.modules["zoe_offbox"]
else:
    SPEC = importlib.util.spec_from_file_location("zoe_offbox", REPO / "scripts/maintenance/zoe_offbox.py")
    ob = importlib.util.module_from_spec(SPEC)
    sys.modules["zoe_offbox"] = ob
    SPEC.loader.exec_module(ob)

FAKE_AGE = r"""#!/bin/sh
mode=enc; out=""; key=""; in=""
while [ $# -gt 0 ]; do
  case "$1" in -R) key="$2"; shift 2;; -i) key="$2"; shift 2;; -o) out="$2"; shift 2;; -d) mode=dec; shift;; *) in="$1"; shift;; esac
done
if [ "$mode" = enc ]; then
  r=$(head -n1 "$key")
  { echo "age-encryption.org/v1 $r"; if [ -n "$in" ]; then cat "$in"; else cat; fi; } > "$out"
else
  want=$(grep -m1 '^# public key:' "$key" | sed 's/^# public key: //')
  [ "$(head -n1 "$in")" = "age-encryption.org/v1 $want" ] || { echo "no identity matched" >&2; exit 1; }
  if [ -n "$out" ]; then tail -n +2 "$in" > "$out"; else tail -n +2 "$in"; fi
fi
"""
FAKE_KEYGEN = r"""#!/bin/sh
[ "$1" = "-o" ] || exit 2
printf '# created: lab\n# public key: age1labdrillrecipient\nAGE-SECRET-KEY-1LABDRILLONLY\n' > "$2"
"""
OWNER_RECIPIENT = "age1labownerrecipientqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfgd = home / ".config/zoe-backup"
    cfgd.mkdir(parents=True)
    binp = tmp_path / "bin"
    binp.mkdir()
    (binp / "restic").write_text("#!/bin/sh\nexit 0\n")
    (binp / "age").write_text(FAKE_AGE)
    (binp / "age-keygen").write_text(FAKE_KEYGEN)
    for b in binp.iterdir():
        b.chmod(0o755)
    target = tmp_path / "external-disk"
    target.mkdir()
    (cfgd / "restic.pass").write_text("box-key")
    (cfgd / "age-recipients.txt").write_text(OWNER_RECIPIENT + "\n")
    (cfgd / "offbox.env").write_text(
        f"ZOE_BACKUP_TARGET=local\nZOE_BACKUP_TARGET_DIR={target}\nRESTIC_PASSWORD_FILE={cfgd / 'restic.pass'}\n"
        f"ZOE_BACKUP_AGE_RECIPIENTS_FILE={cfgd / 'age-recipients.txt'}\nRESTIC_BIN={binp / 'restic'}\nAGE_BIN={binp / 'age'}\n")
    for f in cfgd.iterdir():
        f.chmod(0o600)
    monkeypatch.setenv("ZOE_OFFBOX_ALLOW_SAME_DEVICE", "1")      # tmp is on the home device here; the guard has its own test
    p = ob.Paths(home=home, checkout=tmp_path / "co", config_dir=cfgd, state_dir=tmp_path / "state", stage_dir=tmp_path / "stage")
    return p, target, binp


def _main_env(monkeypatch, p, sent):
    monkeypatch.setenv("ZOE_OFFBOX_HOME", str(p.home))
    monkeypatch.setenv("ZOE_OFFBOX_CONFIG_DIR", str(p.config_dir))
    monkeypatch.setenv("ZOE_OFFBOX_STATE_DIR", str(p.state_dir))
    monkeypatch.setenv("ZOE_OFFBOX_STAGE_DIR", str(p.stage_dir))
    monkeypatch.setattr(ob, "alert", lambda text: sent.append(text))


# ── target adapters ─────────────────────────────────────────────────────────────────────────────────────────────

def test_a_local_directory_target_loads_with_no_rest_credentials(box):
    p, target, _ = box
    cfg = ob.load_config(p)
    assert cfg.target.kind == "local" and cfg.target.prunes_on_box
    assert cfg.env["RESTIC_REPOSITORY"] == str(target)
    assert not any(k.startswith("RESTIC_REST_") for k in cfg.env)


def test_the_rest_target_is_still_the_default_and_unchanged(box):
    p, _, _ = box
    env = p.config_dir / "offbox.env"
    env.write_text(env.read_text().replace("ZOE_BACKUP_TARGET=local\n", "")
                   + "RESTIC_REPOSITORY=rest:https://backup.example/hx/\nRESTIC_REST_USERNAME=hx\nRESTIC_REST_PASSWORD=p\n")
    cfg = ob.load_config(p)
    assert cfg.target.kind == "rest" and not cfg.target.prunes_on_box
    assert cfg.env["RESTIC_REST_USERNAME"] == "hx"


def test_an_unknown_target_kind_is_not_configured(box):
    p, _, _ = box
    env = p.config_dir / "offbox.env"
    env.write_text(env.read_text().replace("ZOE_BACKUP_TARGET=local", "ZOE_BACKUP_TARGET=s3"))
    with pytest.raises(ob.NotConfigured, match="not one of"):
        ob.load_config(p)


def test_a_target_on_the_same_device_as_the_live_data_is_refused(box, monkeypatch):
    p, _, _ = box
    monkeypatch.delenv("ZOE_OFFBOX_ALLOW_SAME_DEVICE")
    with pytest.raises(ob.Refused, match="not off-box"):
        ob.load_config(p)


# ── loud failure ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_missing_target_disk_is_an_alerted_failure(box, monkeypatch):
    p, target, _ = box
    target.rmdir()
    with pytest.raises(ob.TargetMissing, match="not there"):
        ob.load_config(p)
    sent: list[str] = []
    _main_env(monkeypatch, p, sent)
    assert ob.main(["backup", "--ignore-window"]) == 3
    assert sent and "not there" in sent[0]


@pytest.mark.parametrize("key", ["restic.pass", "age-recipients.txt"])
def test_a_missing_key_on_a_configured_box_is_an_alerted_failure(box, monkeypatch, key):
    """Negative control: before this change a missing key file was NotConfigured -> exit 2 with NO alert."""
    p, _, _ = box
    (p.config_dir / key).unlink()
    with pytest.raises(ob.KeyMissing, match="missing"):
        ob.load_config(p)
    sent: list[str] = []
    _main_env(monkeypatch, p, sent)
    assert ob.main(["drill", "--force", "--ignore-window"]) == 3
    assert sent and "missing" in sent[0]


def test_a_box_that_was_never_configured_stays_quiet(tmp_path, monkeypatch):
    sent: list[str] = []
    monkeypatch.setenv("ZOE_OFFBOX_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_OFFBOX_CONFIG_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(ob, "alert", lambda text: sent.append(text))
    assert ob.main(["backup", "--ignore-window"]) == 2 and not sent


# ── dry run ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_plan_counts_and_sizes_and_never_names_a_file(box, monkeypatch, capsys):
    p, _, _ = box
    z = p.home / ".zoe"
    (z / "data").mkdir(parents=True)
    (z / "data" / "diary-of-quillon-marsh.jsonl").write_bytes(b"x" * 1000)       # synthetic name in a FILE NAME
    (z / "backups" / "old").mkdir(parents=True)
    (z / "backups" / "old" / "raw.jsonl").write_bytes(b"y" * 5000)              # excluded by policy
    (p.home / ".zoe-voice-samples").mkdir()
    (p.home / ".zoe-voice-samples" / "clip.wav").write_bytes(b"z" * 300)
    pl = ob.plan(p)
    assert pl["household"]["zoe"] == {"files": 1, "bytes": 1000, "unreadable": 0}
    assert pl["household"]["voice-samples"]["files"] == 1
    sent: list[str] = []
    _main_env(monkeypatch, p, sent)
    monkeypatch.setenv("ZOE_OFFBOX_CHECKOUT", str(p.checkout))
    assert ob.main(["plan"]) == 0
    out = capsys.readouterr().out
    assert "household:" in out and "target: local - ready" in out and "drill key: no" in out
    assert "quillon" not in out.lower() and "diary" not in out and "clip.wav" not in out
    assert not p.stage_dir.exists() and not sent                                  # wrote nothing, alerted nothing


def test_plan_reports_not_ready_without_failing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_OFFBOX_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_OFFBOX_CONFIG_DIR", str(tmp_path / "nope"))
    assert ob.main(["plan"]) == 0
    assert "target: NOT READY" in capsys.readouterr().out


# ── box-side retention for a target the box owns ───────────────────────────────────────────────────────────────

def test_a_local_target_applies_the_same_retention_as_the411(box, monkeypatch):
    p, _, _ = box
    cfg = ob.load_config(p)
    calls = []

    class R:
        returncode = 0

    monkeypatch.setattr(ob, "restic", lambda c, *a, check=True, **k: calls.append(a) or R())
    assert ob.apply_retention(cfg) == []
    assert [a[2] for a in calls] == [f"zoe-{t}" for t in ob.RETENTION]
    for a, tier in zip(calls, ob.RETENTION):
        assert a[0] == "forget" and a[-1] == "--prune" and list(a[5:-1]) == ob.forget_flags(tier)
    R.returncode = 1
    assert len(ob.apply_retention(cfg)) == len(ob.RETENTION)                    # a failed prune is a reported problem


# ── the drill canary: restore proof of the encrypted archive ──────────────────────────────────────────────────────

def test_drill_key_canary_roundtrip_and_tamper(box, monkeypatch):
    p, _, binp = box
    cfg = ob.load_config(p)
    stage = p.stage_dir / "household"
    stage.mkdir(parents=True)
    assert ob.write_canary(cfg, p, stage) is None                                 # no drill key: no canary
    f, notes = ob.verify_canary(cfg, p, stage, None)
    assert f == [] and "ciphertext only" in notes[0]
    assert ob.cmd_drill_key(p, age_keygen=str(binp / "age-keygen")) == 0
    ident, recip = ob.drill_key_paths(p)
    assert oct(ident.stat().st_mode & 0o777) == "0o600" and recip.read_text().startswith("age1")
    assert not ident.resolve().is_relative_to(p.config_dir.resolve())           # never where load_config forbids an identity
    ob.load_config(p)                                                           # and the config-dir identity guard still passes
    assert ob.cmd_drill_key(p, age_keygen=str(binp / "age-keygen")) == 2         # never overwrites
    sha = ob.write_canary(cfg, p, stage)
    assert sha and (stage / ob.DRILL_CANARY).read_bytes().startswith(b"age-encryption.org/v1 age1labdrill")
    assert ob.verify_canary(cfg, p, stage, sha)[0] == []
    assert "wrong plaintext" in ob.verify_canary(cfg, p, stage, "0" * 64)[0][0]   # a different canary is caught
    can = stage / ob.DRILL_CANARY
    can.write_bytes(can.read_bytes().replace(b"age1labdrill", b"age1tampered"))
    assert "does not decrypt" in ob.verify_canary(cfg, p, stage, sha)[0][0]
    can.unlink()
    assert "no drill canary" in ob.verify_canary(cfg, p, stage, sha)[0][0]


def test_the_drill_key_cannot_open_household_ciphertext(box, tmp_path):
    """The household tier is encrypted to the OWNER's recipients only: the box-held drill key opens the canary, nothing else."""
    p, _, binp = box
    cfg = ob.load_config(p)
    assert ob.cmd_drill_key(p, age_keygen=str(binp / "age-keygen")) == 0
    src = tmp_path / "fact.txt"
    src.write_text("a planted household line\n")
    dst = tmp_path / "fact.age"
    ob.age_encryptor(cfg.age, cfg.recipients)(src, dst)
    stage = tmp_path / "restored"
    stage.mkdir()
    (stage / ob.DRILL_CANARY).write_bytes(dst.read_bytes())                      # pretend the household file were the canary
    assert "does not decrypt" in ob.verify_canary(cfg, p, stage, None)[0][0]
