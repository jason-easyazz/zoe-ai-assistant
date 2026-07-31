"""Heartbeat-check tests for the voice replay gate's deploy-path assertion.

"A gate that can silently not-run is not a gate." The voice replay gate
(scripts/maintenance/voice_regression_probe.py) writes a result artifact on
EVERY run; scripts/maintenance/voice_gate_check.py is the cheap deploy-path
counterpart that refuses a voice-path deploy unless that artifact proves a
FRESH pass. These tests pin the three load-bearing cases the fix exists for:
a missing artifact blocks, a stale artifact blocks, a fresh pass clears — plus
skip/error/baseline-drift (skip != pass) and the voice-path diff gate.

Pure-logic only (stdlib), so this runs in the fast `ci_safe` lane.
"""
from __future__ import annotations

import calendar
import importlib.util
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register before exec (dataclass/annotation resolution)
    spec.loader.exec_module(mod)
    return mod


vgc = _load("voice_gate_check", "scripts/maintenance/voice_gate_check.py")
vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")

NOW = calendar.timegm(time.strptime("2026-07-15T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
DAY = 24 * 3600.0


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def artifact(status="pass", age_h=1.0, baseline_created="2026-07-14T00:00:00Z",
             n_samples=20):
    return {
        "status": status,
        "timestamp": _iso(NOW - age_h * 3600.0),
        "created_at": _iso(NOW - age_h * 3600.0),
        "reason": "",
        "said_vs_did_regressions": [],
        "per_stage_speed_deltas": {},
        "baseline_ref": {"path": "/x", "created_at": baseline_created, "ok_rate": 0.9},
        "summary": {"n_samples": n_samples, "ok_rate": 0.95},
    }


# --- the three cases the fix exists for ------------------------------------
def test_missing_artifact_blocks():
    """An ABSENT artifact must never be read as 'nothing wrong' (skip != pass)."""
    ok, why = vgc.evaluate(None, now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "never ran" in why or "NOT a pass" in why


def test_stale_artifact_blocks():
    """A pass that is older than the freshness window is not proof the CURRENT
    voice path works — it must not clear a deploy."""
    ok, why = vgc.evaluate(artifact(status="pass", age_h=48.0),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "STALE" in why


def test_fresh_pass_clears():
    ok, why = vgc.evaluate(artifact(status="pass", age_h=1.0),
                           now_epoch=NOW, max_age_s=DAY,
                           baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is True
    assert "PASS" in why


# --- skip / fail / error are not a pass ------------------------------------
@pytest.mark.parametrize("status", ["skip", "fail", "error", None, "unknown"])
def test_non_pass_status_blocks(status):
    ok, why = vgc.evaluate(artifact(status=status, age_h=0.5),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "NOT a pass" in why


# --- baseline identity ------------------------------------------------------
def test_baseline_drift_blocks():
    """A fresh pass produced against an OLD baseline must not clear the deploy
    once the baseline has moved."""
    art = artifact(status="pass", age_h=1.0, baseline_created="2026-07-01T00:00:00Z")
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY,
                           baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is False
    assert "bar moved" in why


def test_baseline_check_is_lenient_when_baseline_identity_missing():
    """The identity check can only tighten — a baseline without created_at, or no
    baseline at all, must not manufacture a mismatch."""
    art = artifact(status="pass", age_h=1.0)
    assert vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY, baseline=None)[0] is True
    assert vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY, baseline={})[0] is True


def test_unparseable_timestamp_blocks():
    art = artifact()
    art["timestamp"] = art["created_at"] = "not-a-date"
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "timestamp" in why


# --- the voice-path diff gate ----------------------------------------------
def test_voice_path_detection():
    pats = vgc.VOICE_PATH_PATTERNS
    changed = [
        "services/zoe-data/routers/voice_tts.py",
        "services/zoe-ui/index.html",
        "scripts/setup/kokoro_sidecar.py",
        "docs/README.md",
    ]
    hits = vgc.touched_voice_files(changed, pats)
    assert "services/zoe-data/routers/voice_tts.py" in hits
    assert "scripts/setup/kokoro_sidecar.py" in hits  # *kokoro* glob
    assert "services/zoe-ui/index.html" not in hits
    assert "docs/README.md" not in hits


def test_non_voice_change_needs_no_gate():
    assert vgc.touched_voice_files(
        ["services/zoe-ui/index.html", "docs/x.md"], vgc.VOICE_PATH_PATTERNS) == []


def test_parse_iso_z_roundtrips_utc():
    assert vgc.parse_iso_z("2026-07-15T12:00:00Z") == NOW
    assert vgc.parse_iso_z(None) is None
    assert vgc.parse_iso_z("garbage") is None


# --- the producer side: skip/error leave a non-pass artifact, never absent --
class _Args:
    """Minimal stand-in for the probe's argparse namespace."""
    def __init__(self, tmp_path):
        self.results = tmp_path / "voice_regression_last.json"
        self.trend = tmp_path / "trend.jsonl"
        self.baseline = tmp_path / "baseline.json"


def test_probe_skip_emits_non_pass_artifact(tmp_path):
    """The bug this whole change addresses: a skip must leave an artifact whose
    status != 'pass', so the deploy checker sees skip != pass rather than an
    absent file it could misread as 'nothing wrong'."""
    import json as _json
    args = _Args(tmp_path)
    vrp.emit_result(args, status="skip", summary=dict(vrp.EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline={}, reason="box too tight")
    assert args.results.exists(), "skip produced NO artifact — the exact silent-gate bug"
    payload = _json.loads(args.results.read_text())
    assert payload["status"] == "skip"
    # and the checker must block on it
    ok, why = vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY)
    assert ok is False
    assert "NOT a pass" in why


def test_probe_pass_artifact_clears_the_checker(tmp_path):
    """End-to-end contract: a status='pass' artifact the probe writes is accepted
    by the deploy checker while fresh."""
    import json as _json
    args = _Args(tmp_path)
    summary = {"n_samples": 20, "ok_rate": 0.95, "medians_ms": {"stt_ms": 100}}
    vrp.emit_result(args, status="pass", summary=summary, said_vs_did=[],
                    speed_deltas={}, baseline={"created_at": "2026-07-14T00:00:00Z"})
    payload = _json.loads(args.results.read_text())
    assert payload["status"] == "pass"
    ok, _ = vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                         baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is True


# --- revision binding: evidence must name what it is evidence FOR -----------
# Freshness + status do NOT bind an artifact to the code under review. Before
# this, a fresh passing run against `main` cleared every voice PR for the whole
# freshness window — evidence for some other code, presented as evidence for
# this one.
PR_SHA = "1" * 40
OTHER_SHA = "2" * 40


def artifact_with_revision(commit=PR_SHA, dirty=False, **kw):
    art = artifact(**kw)
    art["revision"] = {"commit": commit, "tree": "t" * 40, "dirty": dirty,
                       "service_dir": "/x/services/zoe-data"}
    return art


def test_artifact_for_a_different_sha_is_rejected():
    """THE hole this closes: a fresh, passing, current-baseline artifact produced
    against ANY other commit must not clear this PR."""
    ok, why = vgc.evaluate(artifact_with_revision(commit=OTHER_SHA),
                           now_epoch=NOW, max_age_s=DAY, expect_revision=PR_SHA)
    assert ok is False
    assert "DIFFERENT" in why
    assert OTHER_SHA[:8] in why and PR_SHA[:8] in why


def test_artifact_without_a_revision_is_rejected_when_binding_is_required():
    """Artifacts predating revision-recording (and any probe that could not
    resolve one) are UNATTRIBUTED. Unattributed is not a pass."""
    ok, why = vgc.evaluate(artifact(), now_epoch=NOW, max_age_s=DAY,
                           expect_revision=PR_SHA)
    assert ok is False
    assert "NO revision" in why


def test_dirty_worktree_artifact_is_rejected():
    """A dirty tree cannot be attributed to a commit — the recorded sha would be a
    claim about code that is not the code that ran."""
    ok, why = vgc.evaluate(artifact_with_revision(dirty=True),
                           now_epoch=NOW, max_age_s=DAY, expect_revision=PR_SHA)
    assert ok is False
    assert "DIRTY" in why


def test_matching_revision_clears():
    """Positive control — without it every negative above could pass by blocking
    unconditionally."""
    ok, why = vgc.evaluate(artifact_with_revision(),
                           now_epoch=NOW, max_age_s=DAY, expect_revision=PR_SHA)
    assert ok is True
    assert PR_SHA[:8] in why


def test_revision_binding_is_opt_in_for_the_deploy_path():
    """The deploy path binds by incoming DIFF plus freshness and passes no
    expected revision; adding the PR-side check must not start blocking deploys."""
    ok, _ = vgc.evaluate(artifact(), now_epoch=NOW, max_age_s=DAY,
                         expect_revision=None)
    assert ok is True


def test_revision_binding_composes_with_the_other_gates():
    """A matching revision must not RESCUE an otherwise-bad artifact — a stale or
    non-pass result stays blocked even when the sha lines up."""
    stale = artifact_with_revision(age_h=48.0)
    assert vgc.evaluate(stale, now_epoch=NOW, max_age_s=DAY,
                        expect_revision=PR_SHA)[0] is False
    skipped = artifact_with_revision(status="skip")
    assert vgc.evaluate(skipped, now_epoch=NOW, max_age_s=DAY,
                        expect_revision=PR_SHA)[0] is False


def test_malformed_expect_revision_is_refused_loudly(tmp_path, capsys):
    """A malformed sha must fail as a CONFIGURATION error, not silently mismatch
    and read as an ordinary evidence failure."""
    assert vgc.main(["--require", "--expect-revision", "not-a-sha",
                     "--artifact", str(tmp_path / "none.json")]) == 1
    assert "40-char hex" in capsys.readouterr().err


# --- the probe records what it exercised (producer side) --------------------
def _git_repo(tmp_path, dirty=False):
    """A throwaway git checkout with a services/zoe-data dir, like the real one."""
    import os as _os
    import subprocess as sp
    repo = tmp_path / "r"
    (repo / "services" / "zoe-data").mkdir(parents=True)
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f.txt").write_text("x\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True, env=env)
    head = sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        (repo / "f.txt").write_text("uncommitted edit\n")
    return repo, head


def test_probe_records_the_service_revision(tmp_path):
    """The producer half of the binding: the artifact must carry the commit the
    probe actually ran against, or the gate has nothing to compare."""
    import json as _json
    repo, head = _git_repo(tmp_path)
    args = _Args(tmp_path)
    args.service_dir = str(repo / "services" / "zoe-data")
    vrp.emit_result(args, status="pass", summary={"n_samples": 20},
                    said_vs_did=[], speed_deltas={}, baseline={})
    payload = _json.loads(args.results.read_text())
    assert payload["revision"]["commit"] == head
    assert payload["revision"]["dirty"] is False
    # end to end: this artifact clears its OWN sha and nothing else
    assert vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                        expect_revision=head)[0] is True
    assert vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                        expect_revision=OTHER_SHA)[0] is False


def test_probe_reports_a_dirty_tree_honestly(tmp_path):
    """It must not launder an uncommitted tree into a clean-looking attribution."""
    import json as _json
    repo, head = _git_repo(tmp_path, dirty=True)
    args = _Args(tmp_path)
    args.service_dir = str(repo / "services" / "zoe-data")
    vrp.emit_result(args, status="pass", summary={"n_samples": 20},
                    said_vs_did=[], speed_deltas={}, baseline={})
    payload = _json.loads(args.results.read_text())
    assert payload["revision"]["dirty"] is True
    assert vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                        expect_revision=head)[0] is False


def test_unverifiable_cleanliness_is_rejected(tmp_path):
    """FINDING A, consumer side. `clean_verified: False` means the probe could not
    RUN `git status` at all — cleanliness was never established. Unknown is not
    clean, and a matching commit must not rescue it."""
    art = artifact_with_revision()
    art["revision"]["clean_verified"] = False
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY, expect_revision=PR_SHA)
    assert ok is False
    assert "could NOT verify" in why


def test_older_artifacts_without_clean_verified_still_work(tmp_path):
    """Back-compat: an artifact predating the `clean_verified` key only reaches the
    check with `dirty` explicitly false, so a missing key must not block it."""
    art = artifact_with_revision()
    art["revision"].pop("clean_verified", None)
    assert vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY,
                        expect_revision=PR_SHA)[0] is True


def test_unreadable_git_status_is_recorded_as_dirty(tmp_path, monkeypatch):
    """FINDING A, producer side — a genuine fail-open.

    `_git` returns None both for "git printed nothing" and for "git FAILED"
    (unreadable index, a bad inherited GIT_INDEX_FILE, a permissions problem), and
    `bool(None)` is False — so a failed cleanliness check recorded the worktree as
    CLEAN and a matching commit cleared `--expect-revision` with cleanliness never
    established. Simulate a failing `git status` and require the opposite."""
    repo, head = _git_repo(tmp_path)
    real_run = vrp.subprocess.run

    def fake_run(cmd, **kw):
        if "status" in cmd:
            class Failed:
                returncode = 128
                stdout = ""
                stderr = "fatal: could not read index"
            return Failed()
        return real_run(cmd, **kw)

    monkeypatch.setattr(vrp.subprocess, "run", fake_run)
    rev = vrp.service_revision(str(repo / "services" / "zoe-data"))
    assert rev["commit"] == head, "the commit is still readable"
    assert rev["dirty"] is True, "an unreadable status must NOT read as clean"
    assert rev["clean_verified"] is False

    args = _Args(tmp_path)
    args.service_dir = str(repo / "services" / "zoe-data")
    vrp.emit_result(args, status="pass", summary={"n_samples": 20},
                    said_vs_did=[], speed_deltas={}, baseline={})
    import json as _json
    payload = _json.loads(args.results.read_text())
    ok, why = vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                           expect_revision=head)
    assert ok is False, "a matching commit must not clear an unverifiable tree"
    assert "DIRTY" in why or "could NOT verify" in why


def test_probe_without_a_service_dir_records_no_revision(tmp_path):
    """Back-compat: emit_result is also called with lightweight arg objects. A
    missing revision must be None (and therefore unattributed), never a crash."""
    import json as _json
    args = _Args(tmp_path)
    vrp.emit_result(args, status="pass", summary={"n_samples": 20},
                    said_vs_did=[], speed_deltas={}, baseline={})
    assert _json.loads(args.results.read_text())["revision"] is None


# --- changed-file list as DATA (the PR is never executed) -------------------
def test_changed_files_from_classifies_without_git(tmp_path, monkeypatch, capsys):
    """The PR gate learns what changed from an API-supplied file list, so it never
    has to fetch or run the PR's own tree."""
    listing = tmp_path / "changed.txt"
    listing.write_text("docs/PLANS.md\nservices/zoe-data/fast_tiers.py\n")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc.main(["--scope-only", "--changed-files-from", str(listing)]) == 0
    body = out.read_text()
    assert "voice=true" in body
    assert "services/zoe-data/fast_tiers.py" in body

    listing.write_text("docs/PLANS.md\nREADME.md\n")
    out.write_text("")
    assert vgc.main(["--scope-only", "--changed-files-from", str(listing)]) == 0
    assert "voice=false" in out.read_text()
    assert "CLEAR" in capsys.readouterr().out


def test_unreadable_changed_file_list_fails_closed(tmp_path, monkeypatch):
    """A missing list is UNKNOWN, not 'nothing changed'."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc.main(["--scope-only",
                     "--changed-files-from", str(tmp_path / "nope.txt")]) == 0
    assert "voice=true" in out.read_text()


# --- scope classification (the PR-time gate's first half) -------------------
# `--scope-only` runs on a hosted runner where the replay artifact CANNOT exist.
# It classifies and never asserts, which is what lets the `voice-gate` check
# report a conclusion on every PR instead of only on voice-path ones.
def test_scope_clear_when_no_voice_files():
    needs, hits, why = vgc.scope_verdict(
        ["docs/PLANS.md", "services/zoe-ui/index.html"], vgc.VOICE_PATH_PATTERNS)
    assert needs is False
    assert hits == []
    assert "not required" in why


def test_scope_requires_gate_on_voice_files():
    needs, hits, why = vgc.scope_verdict(
        ["docs/PLANS.md", "services/zoe-data/fast_tiers.py"], vgc.VOICE_PATH_PATTERNS)
    assert needs is True
    assert hits == ["services/zoe-data/fast_tiers.py"]
    assert "REQUIRED" in why


def test_scope_fails_closed_when_the_diff_is_unknown():
    """An uncomputable diff is NOT 'no voice files changed'. Reading it as clear
    would let a voice-path PR through on a git failure — the same fail-closed rule
    the deploy path uses, applied one gate earlier."""
    needs, hits, why = vgc.scope_verdict(None, vgc.VOICE_PATH_PATTERNS)
    assert needs is True
    assert hits == []
    assert "not a pass" in why


def test_scope_only_always_exits_zero(tmp_path, monkeypatch, capsys):
    """The scope job CLASSIFIES; it must never be the thing that fails.

    If scope could exit non-zero it would fail the job, and the summary job would
    then have to distinguish 'scope failed' from 'scope says voice' — the exact
    ambiguity that produces a required check with no conclusion. Both branches,
    including the unreadable-diff branch, exit 0."""
    import os as _os
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert _os.environ["GITHUB_OUTPUT"] == str(out)

    # unreadable range (not a git repo) -> fail-closed classification, still exit 0
    assert vgc.main(["--scope-only", "--repo", str(tmp_path), "--diff", "a...b"]) == 0
    assert "voice=true" in out.read_text()
    assert "VOICE" in capsys.readouterr().out

    # no --diff at all -> unknown -> gate required, still exit 0
    out.write_text("")
    assert vgc.main(["--scope-only", "--repo", str(tmp_path)]) == 0
    assert "voice=true" in out.read_text()


def test_scope_only_reports_clear_for_a_non_voice_diff(tmp_path, monkeypatch, capsys):
    """The common case, end to end through the CLI: a real git repo whose diff
    touches no voice file must publish voice=false so the Jetson is never involved."""
    import os as _os
    import subprocess as sp
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("base\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    (repo / "docs.md").write_text("docs only\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "docs"], check=True, env=env)

    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc.main(["--scope-only", "--repo", str(repo), "--diff", f"{base}...HEAD"]) == 0
    assert "voice=false" in out.read_text()
    assert "CLEAR" in capsys.readouterr().out

    # and the same repo with a voice-path file flips it to true
    (repo / "services" / "zoe-data").mkdir(parents=True)
    (repo / "services" / "zoe-data" / "fast_tiers.py").write_text("x = 1\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "voice"], check=True, env=env)
    out.write_text("")
    assert vgc.main(["--scope-only", "--repo", str(repo), "--diff", f"{base}...HEAD"]) == 0
    body = out.read_text()
    assert "voice=true" in body
    assert "services/zoe-data/fast_tiers.py" in body


def test_w11_delivery_modules_are_voice_path():
    """A deploy touching only the W11 mapper or the waterfall changes what Zoe
    SOUNDS like and must hit the replay gate (Codex P1, #1579 — which was
    itself such a deploy and would have bypassed it)."""
    from voice_gate_check import touched_voice_files, voice_path_patterns
    touched = touched_voice_files(
        ["services/zoe-data/voice_delivery.py",
         "services/zoe-data/tts_waterfall.py",
         "docs/PLANS.md"],
        voice_path_patterns())
    assert touched == ["services/zoe-data/voice_delivery.py",
                       "services/zoe-data/tts_waterfall.py"], touched


# --- FIX: scope must fail CLOSED when it cannot publish its verdict ---------
# `_emit_github_output` used to swallow an OSError and let `_scope_only` return
# 0 anyway — the scope job then "succeeded" with no `voice` output at all, and
# the verdict job's `voice !== 'true'` check read that absence as 'non-voice'
# and published green. A verdict that never reached $GITHUB_OUTPUT must never
# be read downstream as a non-voice PR.
def test_emit_github_output_reports_write_failure(tmp_path, monkeypatch):
    """A directory is not appendable — `open(path, 'a')` raises OSError."""
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path))
    assert vgc._emit_github_output(voice="true") is False


def test_emit_github_output_succeeds_when_writable(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc._emit_github_output(voice="true") is True
    assert "voice=true" in out.read_text()


def test_emit_github_output_is_a_noop_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert vgc._emit_github_output(voice="true") is True


def test_unwritable_github_output_fails_the_scope_job(tmp_path, monkeypatch, capsys):
    """THE fix: an unwritable $GITHUB_OUTPUT must fail the scope job (exit 1),
    not report success with a verdict nobody downstream can see. A failed scope
    job routes into the verdict's existing `scopeResult != 'success'` fail-closed
    branch instead of being silently read as 'non-voice'."""
    listing = tmp_path / "changed.txt"
    listing.write_text("docs/PLANS.md\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path))  # a directory: unwritable
    rc = vgc.main(["--scope-only", "--changed-files-from", str(listing)])
    assert rc == 1, "an unwritable GITHUB_OUTPUT must fail the scope job, never pass silently"
    assert "could not publish" in capsys.readouterr().err.lower()


def test_writable_github_output_still_exits_zero(tmp_path, monkeypatch):
    """Regression guard: the fix above must not make the ordinary, writable case
    fail too — --scope-only still always exits 0 when it CAN publish."""
    listing = tmp_path / "changed.txt"
    listing.write_text("docs/PLANS.md\n")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc.main(["--scope-only", "--changed-files-from", str(listing)]) == 0
    assert "voice=false" in out.read_text()


# --- FIX: a renamed voice-path file must not evade classification -----------
# `git diff --name-only` reports only a rename's DESTINATION path — the source
# never appears, with or without `-M`. Renaming a gated file (e.g.
# services/zoe-data/fast_tiers.py) to a non-matching path therefore made
# `git_changed_files` (used by BOTH deploy.yml and deploy_live.sh via `--diff`)
# report no voice-path change at all.
def _rename_repo(tmp_path):
    """base commit with services/zoe-data/fast_tiers.py, then a commit that
    renames it to a non-matching path. Returns (repo, base_sha)."""
    import os as _os
    import subprocess as sp
    repo = tmp_path / "r"
    (repo / "services" / "zoe-data").mkdir(parents=True)
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "services" / "zoe-data" / "fast_tiers.py").write_text("x = 1\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    sp.run(["git", "-C", str(repo), "mv",
           "services/zoe-data/fast_tiers.py", "services/zoe-data/renamed_away.py"],
          check=True, cwd=str(repo))
    sp.run(["git", "-C", str(repo), "commit", "-qm", "rename"], check=True, env=env)
    return repo, base


def test_git_changed_files_surfaces_both_sides_of_a_rename(tmp_path):
    repo, base = _rename_repo(tmp_path)
    changed = vgc.git_changed_files(repo, f"{base}...HEAD")
    assert "services/zoe-data/fast_tiers.py" in changed, changed
    assert "services/zoe-data/renamed_away.py" in changed, changed


def test_renamed_voice_file_still_classifies_as_voice_path(tmp_path):
    """THE fix, end to end through the classifier the deploy path calls."""
    repo, base = _rename_repo(tmp_path)
    changed = vgc.git_changed_files(repo, f"{base}...HEAD")
    hits = vgc.touched_voice_files(changed, vgc.VOICE_PATH_PATTERNS)
    assert "services/zoe-data/fast_tiers.py" in hits, (
        "renaming a gated voice file away must still require the replay gate")


def test_deploy_path_diff_detects_a_renamed_voice_file(tmp_path, monkeypatch, capsys):
    """Integration: the deploy path (`--diff`, no --require, as called by both
    deploy.yml and deploy_live.sh) must block a rename-away of a gated file
    exactly like it would block the file's ordinary modification."""
    repo, base = _rename_repo(tmp_path)
    rc = vgc.main(["--repo", str(repo), "--diff", f"{base}...HEAD",
                   "--artifact", str(tmp_path / "no-such-artifact.json")])
    assert rc == 1, "a renamed-away voice file must still require (and here fail) the gate"
    assert "fast_tiers.py" in capsys.readouterr().out


def test_ordinary_non_rename_diff_is_unaffected(tmp_path):
    """Regression guard: switching `git_changed_files` from --name-only to
    --name-status -M must not change behaviour for plain adds/modifies."""
    import os as _os
    import subprocess as sp
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("base\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    (repo / "docs.md").write_text("new file\n")
    (repo / "README.md").write_text("modified\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "changes"], check=True, env=env)

    changed = vgc.git_changed_files(repo, f"{base}...HEAD")
    assert sorted(changed) == ["README.md", "docs.md"]
