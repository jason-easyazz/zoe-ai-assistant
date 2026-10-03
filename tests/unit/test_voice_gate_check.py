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
        "scripts/lib/service_python.py",
        "scripts/setup/systemd/zoe-voice-regression.service",
        "scripts/deploy/zoe_data_python.sh",
    ]
    hits = vgc.touched_voice_files(changed, pats)
    assert "services/zoe-data/routers/voice_tts.py" in hits
    assert "scripts/setup/kokoro_sidecar.py" in hits  # *kokoro* glob
    # The interpreter ladder + the nightly unit decide which stack produces the
    # evidence (Codex P2, #1811): a change there must force re-measurement.
    assert "scripts/lib/service_python.py" in hits
    assert "scripts/setup/systemd/zoe-voice-regression.service" in hits
    assert "scripts/deploy/zoe_data_python.sh" in hits   # the ladder's systemd authority
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


def test_revision_binding_is_opt_in_at_the_function_level():
    """evaluate() binds only when asked. Both callers now ask — the PR gate with
    --expect-revision, the deploy gates with --expect-tree-of (pinned in
    test_deploy_voice_gate_binding.py) — so this is the unbound `--require` path."""
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


# --- deploy gate: the artifact is bound to the DEPLOYED TREE ------------------
# Incident 2026-09-28: the deploy gate checked freshness + status only, so a
# replay produced for an UNRELATED earlier landing cleared #1745 (a voice-path
# pin) and the deploy moved code with no evidence for it. `--expect-tree-of
# <target>` binds the artifact to the code going live. By TREE, not commit: a
# squash merge mints a new sha, but for an up-to-date branch its tree is
# byte-identical to the PR head's — so the PR-head-bound artifact still lands,
# and is refused whenever the deployed code differs.
def _git_env():
    import os as _os
    return {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}


def _rev(repo, ref):
    import subprocess as sp
    return sp.run(["git", "-C", str(repo), "rev-parse", ref],
                  capture_output=True, text=True, check=True).stdout.strip()


def squash_repo(tmp_path):
    """prev -> PR head (touches a voice file) on a branch, then the SQUASH MERGE:
    a new commit on main with prev as parent and the PR head's exact tree. Plus an
    UNRELATED commit whose tree differs (the incident's artifact source).
    Returns (repo, shas) with keys prev/head/merge/other."""
    import subprocess as sp
    repo = tmp_path / "sq"
    (repo / "services" / "zoe-data").mkdir(parents=True)
    env = _git_env()
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "services" / "zoe-data" / "requirements-py312.txt").write_text("chromadb==0.6.3\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "prev"], check=True, env=env)
    prev = _rev(repo, "HEAD")
    sp.run(["git", "-C", str(repo), "checkout", "-qb", "pr"], check=True)
    (repo / "services" / "zoe-data" / "requirements-py312.txt").write_text("chromadb==1.5.9\n")
    sp.run(["git", "-C", str(repo), "commit", "-qam", "pr head"], check=True, env=env)
    head = _rev(repo, "HEAD")
    merge = sp.run(["git", "-C", str(repo), "commit-tree", f"{head}^{{tree}}", "-p", prev,
                    "-m", "squash (#1745)"], capture_output=True, text=True, check=True,
                   env=env).stdout.strip()
    sp.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    (repo / "unrelated.txt").write_text("another landing\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "unrelated"], check=True, env=env)
    other = _rev(repo, "HEAD")
    sp.run(["git", "-C", str(repo), "reset", "-q", "--hard", prev], check=True)
    return repo, {"prev": prev, "head": head, "merge": merge, "other": other}


def artifact_for(repo, commit, *, dirty=False, clean_verified=True, **kw):
    art = artifact(**kw)
    art["revision"] = {"commit": commit, "tree": _rev(repo, f"{commit}^{{tree}}"),
                       "dirty": dirty, "clean_verified": clean_verified,
                       "service_dir": f"{repo}/services/zoe-data"}
    return art


def _tree_of(repo, sha):
    return (sha, vgc.resolve_tree(repo, sha))


def test_squash_merge_has_a_new_sha_but_the_same_tree(tmp_path):
    """The premise the tree binding rests on — checked, not assumed."""
    repo, s = squash_repo(tmp_path)
    assert s["merge"] != s["head"]
    assert vgc.resolve_tree(repo, s["merge"]) == vgc.resolve_tree(repo, s["head"])
    assert vgc.resolve_tree(repo, s["other"]) != vgc.resolve_tree(repo, s["merge"])


def test_deploy_refuses_an_artifact_for_a_different_tree(tmp_path):
    """THE incident: fresh, passing, current-baseline evidence for an UNRELATED
    landing must not clear a voice-path deploy."""
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact_for(repo, s["other"]), now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is False
    assert "DIFFERENT" in why and s["merge"][:8] in why and s["other"][:8] in why


def test_deploy_accepts_the_pr_head_artifact_when_the_tree_is_identical(tmp_path):
    """Positive control, and the reason the binding is by tree: the up-to-date
    squash merge is the same code as the PR head the probe ran against."""
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact_for(repo, s["head"]), now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is True, why
    assert "tree-identical" in why and s["merge"][:8] in why


def test_deploy_accepts_an_artifact_for_the_exact_target_commit(tmp_path):
    """Recipe (a): the probe was re-run against a checkout of the merged commit."""
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact_for(repo, s["merge"]), now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is True, why
    assert f"bound to {s['merge'][:8]}" in why


def test_deploy_refuses_a_dirty_run_even_on_the_same_tree(tmp_path):
    """A dirty worktree's recorded tree is HEAD's, not what ran — a tree match
    must not launder it."""
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact_for(repo, s["head"], dirty=True), now_epoch=NOW,
                           max_age_s=DAY, expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is False and "DIRTY" in why
    ok, why = vgc.evaluate(artifact_for(repo, s["merge"], dirty=True), now_epoch=NOW,
                           max_age_s=DAY, expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is False and "DIRTY" in why


def test_deploy_refuses_unverifiable_cleanliness_on_the_same_tree(tmp_path):
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact_for(repo, s["head"], clean_verified=False),
                           now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is False and "could NOT verify" in why


def test_deploy_refuses_an_unattributed_artifact(tmp_path):
    """A pre-revision artifact (no `revision`) was exactly what cleared #1745."""
    repo, s = squash_repo(tmp_path)
    ok, why = vgc.evaluate(artifact(), now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=_tree_of(repo, s["merge"]))
    assert ok is False and "NO revision" in why


def test_unresolvable_target_tree_is_not_a_match(tmp_path):
    """If the target's tree cannot be resolved, only an exact commit match may
    clear — `None == None` must never read as the same tree."""
    art = artifact_with_revision(commit=OTHER_SHA)
    art["revision"]["tree"] = None
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY,
                           expect_tree_of=(PR_SHA, None))
    assert ok is False and "could not resolve" in why
    assert vgc.evaluate(artifact_with_revision(commit=PR_SHA), now_epoch=NOW,
                        max_age_s=DAY, expect_tree_of=(PR_SHA, None))[0] is True


def test_tree_binding_does_not_rescue_a_bad_artifact(tmp_path):
    repo, s = squash_repo(tmp_path)
    tgt = _tree_of(repo, s["merge"])
    for bad in (artifact_for(repo, s["head"], age_h=48.0),
                artifact_for(repo, s["head"], status="skip")):
        assert vgc.evaluate(bad, now_epoch=NOW, max_age_s=DAY, expect_tree_of=tgt)[0] is False


def _write_artifact(tmp_path, art):
    import json as _json
    p = tmp_path / "artifact.json"
    p.write_text(_json.dumps(art))
    return p


def _now_art(repo, commit, **kw):
    art = artifact_for(repo, commit, **kw)
    art["timestamp"] = art["created_at"] = _iso(time.time() - 600)
    return art


def test_deploy_cli_end_to_end_mirrors_the_incident(tmp_path, capsys):
    """Through main() exactly as deploy.yml calls it: `--diff prev..target
    --expect-tree-of target`, with the target's tree resolved from --repo."""
    repo, s = squash_repo(tmp_path)
    base = ["--repo", str(repo), "--diff", f"{s['prev']}..{s['merge']}",
            "--baseline", str(tmp_path / "no-baseline.json"),
            "--expect-tree-of", s["merge"]]
    # the incident: an unrelated landing's artifact -> REFUSED
    assert vgc.main(base + ["--artifact", str(_write_artifact(tmp_path, _now_art(repo, s["other"])))]) == 1
    err = capsys.readouterr().err
    assert "DIFFERENT" in err and "voice-gate-deploy" in err, err
    _assert_recipe_copies_env(err, "~/.worktrees/voice-gate-deploy")
    # the PR head's artifact, squash tree-identical -> ALLOWED
    assert vgc.main(base + ["--artifact", str(_write_artifact(tmp_path, _now_art(repo, s["head"])))]) == 0
    assert "tree-identical" in capsys.readouterr().out
    # dirty -> REFUSED
    assert vgc.main(base + ["--artifact", str(_write_artifact(
        tmp_path, _now_art(repo, s["head"], dirty=True)))]) == 1


def _assert_recipe_copies_env(text, wt):
    """Greptile P1 on #1754: a fresh worktree has no gitignored .env, and the
    recipe's explicit --service-dir bypasses the probe's live-env fallback, so a
    recipe without this copy step records status=error and can never unwedge."""
    dst = f"{wt}/services/zoe-data/.env"
    copy = f"cp -n /home/zoe/assistant/services/zoe-data/.env {dst} && chmod 600 {dst}"
    assert copy in text, text
    assert text.index(copy) < text.index(f"--service-dir {wt}/services/zoe-data"), text


def test_pr_gate_recipe_copies_the_env_before_the_probe(tmp_path, capsys):
    assert vgc.main(["--require", "--expect-revision", PR_SHA,
                     "--artifact", str(tmp_path / "none.json")]) == 1
    _assert_recipe_copies_env(capsys.readouterr().err, "~/.worktrees/voice-gate")


def test_deploy_cli_non_voice_diff_needs_no_artifact(tmp_path):
    """The binding must not tax ordinary deploys: a non-voice range is still a
    no-op pass, with no artifact at all."""
    repo, s = squash_repo(tmp_path)
    assert vgc.main(["--repo", str(repo), "--diff", f"{s['prev']}..{s['other']}",
                     "--expect-tree-of", s["other"],
                     "--artifact", str(tmp_path / "none.json")]) == 0


def test_the_two_bindings_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        vgc.main(["--require", "--expect-revision", PR_SHA, "--expect-tree-of", PR_SHA])


def test_malformed_expect_tree_of_is_refused_loudly(tmp_path, capsys):
    assert vgc.main(["--require", "--expect-tree-of", "HEAD",
                     "--artifact", str(tmp_path / "none.json")]) == 1
    assert "40-char hex" in capsys.readouterr().err


def test_probe_artifact_satisfies_the_tree_binding_after_a_squash(tmp_path):
    """Producer + consumer together: the REAL probe's recorded `revision.tree`
    (not a hand-built dict) is what the deploy gate compares."""
    import json as _json
    import subprocess as sp
    repo, s = squash_repo(tmp_path)
    sp.run(["git", "-C", str(repo), "checkout", "-q", s["head"]], check=True)
    args = _Args(tmp_path)
    args.service_dir = str(repo / "services" / "zoe-data")
    vrp.emit_result(args, status="pass", summary={"n_samples": 20},
                    said_vs_did=[], speed_deltas={}, baseline={})
    payload = _json.loads(args.results.read_text())
    assert payload["revision"]["tree"] == vgc.resolve_tree(repo, s["merge"])
    assert vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                        expect_tree_of=_tree_of(repo, s["merge"]))[0] is True
    assert vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                        expect_tree_of=_tree_of(repo, s["other"]))[0] is False


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


def test_zoe_data_requirements_is_voice_path():
    """The zoe-data dependency manifest pins the STT/TTS/router stack the voice path
    loads (moonshine-voice, edge-tts, fastembed, numpy). A dep bump — even a pin change
    with no code diff — can change what the voice path RUNS, so a diff touching ONLY
    requirements.txt must still require the replay gate."""
    from voice_gate_check import scope_verdict, touched_voice_files, voice_path_patterns
    pats = voice_path_patterns()
    assert touched_voice_files(
        ["services/zoe-data/requirements.txt", "docs/PLANS.md"], pats
    ) == ["services/zoe-data/requirements.txt"]
    # end to end through the classifier the deploy/PR path calls
    needs, hits, _ = scope_verdict(["services/zoe-data/requirements.txt"], pats)
    assert needs is True and hits == ["services/zoe-data/requirements.txt"]


def test_zoe_data_py312_manifest_and_installer_are_voice_path():
    """B0.7 (#1706): `requirements-py312.txt` is the INSTALLER of the zoe-data 3.12
    venv — it pins moonshine-voice, onnxruntime, numpy and the CPU torch wheel —
    and `build_py312_venv.sh` decides what else goes in (the `--no-deps` phase).
    A diff touching ONLY either must gate like `requirements.txt` does. voice-gate
    classifies with the BASE ref's tuple, so this must be on main before any PR
    that makes the venv the service interpreter (Codex P1, #1706)."""
    from voice_gate_check import scope_verdict, touched_voice_files, voice_path_patterns
    pats = voice_path_patterns()
    changed = ["services/zoe-data/requirements-py312.txt",
               "scripts/setup/build_py312_venv.sh",
               "docs/knowledge/python-312-venv-migration.md"]
    assert touched_voice_files(changed, pats) == changed[:2]
    for f in changed[:2]:
        needs, hits, _ = scope_verdict([f], pats)
        assert needs is True and hits == [f]
    # negative control: an unrelated setup script / manifest stays CLEAR
    assert touched_voice_files(["scripts/setup/pi-requirements.txt",
                                "services/zoe-auth/requirements.txt"], pats) == []


def test_zoe_core_lockfile_is_voice_path():
    """A Pi/transitive-dep bump can be regenerated into package-lock.json WITHOUT
    touching package.json, so a diff touching ONLY the lockfile must still require
    the replay gate — otherwise `npm ci` could change the brain's dependency graph
    while bypassing the gate (Codex P1, #1602)."""
    from voice_gate_check import scope_verdict, touched_voice_files, voice_path_patterns
    pats = voice_path_patterns()
    assert touched_voice_files(
        ["services/zoe-core/package-lock.json", "docs/PLANS.md"], pats
    ) == ["services/zoe-core/package-lock.json"]
    # end to end through the classifier the deploy/PR path calls
    needs, hits, _ = scope_verdict(["services/zoe-core/package-lock.json"], pats)
    assert needs is True and hits == ["services/zoe-core/package-lock.json"]


# --- the LIVE brain lane (ZOE_BRAIN_BACKEND=flue) ---------------------------
# The gate matched services/zoe-core (the DORMANT fallback) but not the Flue
# sidecar that has actually answered voice turns since 2026-07-03. Because
# deploy.yml calls this same module, the post-merge deploy gate inherited the
# identical blind spot: a change to the speaking brain took the no-op pass at
# BOTH ends.
@pytest.mark.parametrize("path", [
    "labs/flue-zoe-brain-2x/src/app.ts",
    "labs/flue-zoe-brain-2x/src/agents/zoe.ts",    # nested, via the src/* glob
    "labs/flue-zoe-brain-2x/package.json",
    "labs/flue-zoe-brain-2x/package-lock.json",
    "labs/flue-zoe-brain-2x/flue.config.ts",
    "labs/flue-zoe-brain-2x/tsconfig.json",
    "labs/flue-zoe-brain-2x/vite.config.ts",
    "scripts/setup/systemd/flue-zoe-brain-2x.service",
    "services/zoe-data/zoe_flue_client.py",
    "services/zoe-data/brain_dispatch.py",
    # what the lane injects in front of the brain (recall packet, user model)
    "services/zoe-data/routers/memories.py",
    "services/zoe-data/user_portrait.py",
    "services/zoe-data/memory_gate.py",
])
def test_live_flue_brain_lane_is_voice_path(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    # end to end through the classifier both the deploy and PR paths call
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    # Not deployed: never compiled into dist/server.mjs, never served — so a diff
    # touching only these must NOT charge a 20-sample Kokoro replay.
    "labs/flue-zoe-brain-2x/test/route_identity.test.ts",
    "labs/flue-zoe-brain-2x/parity/hard_gate.py",
    "labs/flue-zoe-brain-2x/README.md",
    "labs/flue-zoe-brain-2x/LANDING.md",
])
def test_flue_non_deployed_paths_do_not_gate(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == []
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is False and hits == []


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


# --- the LIVE ROUTER'S MODEL ARTIFACTS (ZOE_ROUTER_HEAD=active) -------------
# The stage-1 checkpoint decides which tool a voice turn fires, and swapping the
# file re-routes every turn with NO code diff — quieter than any logic edit.
#
# It was excluded on the belief that router_selftrain.py's `replay_gate_passed`
# ratchet already covered it. Verified false: that script has no reference to a
# head/MLP/.joblib anywhere; it promotes the stage-2 FunctionGemma GGUF to
# ~/models/functiongemma-router/, outside the repo. Nothing automated ever
# commits a stage-1 head, so every commit here is a hand-commit and the ratchet
# was covering none of them.
@pytest.mark.parametrize("path", [
    # Stage 1 of the live two-stage decision (docs/CANONICAL.md stage1_artifact).
    "services/zoe-data/models/router_head_mlp.joblib",
    # Loaded into the live zoe-data process on every non-`off` mode, and one
    # flag value from being the head whose predictions feed the ratchet's miner.
    "services/zoe-data/models/router_head_logreg.joblib",
    # The numpy exports zoe-data actually SERVES (ZOE_ROUTER_HEADS_BACKEND=numpy):
    # the weights and the sidecar that names their sha256.
    "services/zoe-data/models/router_head_mlp.npz",
    "services/zoe-data/models/router_head_mlp.json",
    "services/zoe-data/models/router_head_logreg.npz",
    "services/zoe-data/models/router_head_logreg.json",
])
def test_live_router_head_artifacts_are_voice_path(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    # end to end through the classifier both the deploy and PR paths call
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    "services/zoe-data/models/router_head_v3.joblib",
    "services/zoe-data/models/router_head.onnx",
    "services/zoe-data/models/nested/head.joblib",
])
def test_new_router_head_artifacts_gate_by_default(path):
    """The glob is a DIRECTORY glob, and that is the point.

    Two literal paths would close today's hole and leave the next head added to
    the live service's model directory in exactly the same ungated state this
    test exists to prevent. Anything dropped in there is model material."""
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == [path]
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    # The OFFLINE training copies. Same filenames, same bytes at promotion time,
    # but they are never loaded at runtime — the runtime path is
    # services/zoe-data/models/. A `*.joblib` or `*head*` wildcard would sweep
    # these in and charge a 20-sample Kokoro replay for a training-only diff.
    "labs/setfit-router/artifacts/head_mlp.joblib",
    "labs/setfit-router/artifacts/head_logreg.joblib",
    "labs/setfit-router/train.py",
    # Mechanism tests + offline tooling: never run inside a voice turn. Note
    # router_selftrain.py in particular — its promotion target is the stage-2
    # GGUF outside the repo, so it cannot change a tracked artifact at all.
    "services/zoe-data/tests/test_router_head_shadow.py",
    "scripts/maintenance/router_selftrain.py",
    "scripts/maintenance/router_shadow_report.py",
])
def test_offline_router_artifacts_and_tooling_do_not_gate(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == []
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is False and hits == []


@pytest.mark.parametrize("near_miss", [
    # `services/zoe-data/models*` (no slash) would sweep this in — pin that the
    # glob is anchored on the directory, not on its name as a prefix.
    "services/zoe-data/models_old/head.joblib",
    "services/zoe-ui/models/head.joblib",
    "labs/models/head.joblib",
])
def test_router_head_glob_is_the_live_service_model_dir_only(near_miss):
    """Prefix-exactness: a sibling directory must not inherit the glob."""
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([near_miss], pats) == []
    needs, hits, _ = vgc.scope_verdict([near_miss], pats)
    assert needs is False and hits == []


def test_router_head_pattern_is_the_directory_glob():
    """The literal the three tests above are all reasoning about."""
    assert "services/zoe-data/models/*" in vgc.voice_path_patterns()


# --- the two-stage router: SERVING CONFIG + ROUTING DECISION ----------------
# The third and fourth legs of the same router. Its model artifacts are gated
# above; these are the serving unit and the decision code. docs/CANONICAL.md
# puts the two-stage router ON the voice path (ZOE_ROUTER_HEAD=active), but
# neither matched a glob, so a change to either took the no-op pass at PR time
# AND at deploy. Both are pinned separately because they are gated for DIFFERENT
# reasons — the unit is serving config (which GGUF, --ctx-size, --parallel,
# memory caps), the modules are the logic that picks the tool — and either alone
# leaves a real hole.
#
# This is the worst class to leave ungated because it is SILENT by construction:
# router_two_stage.decide() returns None from a blanket `except Exception`
# (router_two_stage.py:291-294) and semantic_router keeps the weaker similarity
# route on None (:546-566, "brain-safe"), while a plain logic edit just returns a
# different tool without erroring at all. Nothing goes red anywhere; only a
# said-vs-did replay sees it.
def test_router_sidecar_unit_is_voice_path():
    path = "scripts/setup/systemd/functiongemma-router.service"
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    "services/zoe-data/router_two_stage.py",
    "services/zoe-data/semantic_router.py",
    # the stage-1 heads' inference: an edit here re-scores every turn exactly
    # like swapping a models/* artifact would
    "services/zoe-data/router_heads_numpy.py",
])
def test_router_decision_modules_are_voice_path(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    # end to end through the classifier both the deploy and PR paths call
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    # Prefix-exactness, same discipline as the flue-zoe-brain-2x case: the glob
    # is one literal unit file, not a systemd-directory wildcard. Unrelated
    # units must NOT start charging a 20-sample Kokoro replay.
    "scripts/setup/systemd/serena-mcp.service",
    "scripts/setup/systemd/zoe-memory-export.service",
    "scripts/setup/systemd/functiongemma-router.service.d/override.conf",
])
def test_unrelated_systemd_units_do_not_gate(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == []
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is False and hits == []


@pytest.mark.parametrize("path", [
    # Co-located tests: mechanism-only, never run inside a voice turn. Gating
    # them would charge a 20-sample Kokoro replay for a test-only diff.
    "services/zoe-data/tests/test_router_two_stage.py",
    "services/zoe-data/tests/test_router_head_shadow.py",
    # Lab eval + training harnesses — offline, not deployed.
    "labs/router-90-campaign/prod_path_eval.py",
    "labs/two-stage-router-eval/run_two_stage.py",
    # Offline scoring / self-train tooling, outside the request path. Note that
    # router_selftrain.py's promotion target is the stage-2 GGUF OUTSIDE the
    # repo, so it cannot change a tracked artifact at all.
    "scripts/maintenance/router_shadow_report.py",
    "scripts/maintenance/router_selftrain.py",
])
def test_router_offline_paths_do_not_gate(path):
    """The globs are LITERAL module paths, not a `*router*` wildcard.

    A wildcard would sweep in every one of these — and the two it would sweep
    in most often (the co-located tests and the self-train scripts) are the
    ones that change most, which is how a gate stops being worth having.

    NOTE: services/zoe-data/models/router_head_mlp.joblib is deliberately NOT in
    this list. An earlier draft excluded it on the belief that the self-train
    ratchet already replay-gated its promotion; that was falsified (the ratchet
    never touches stage 1), and it is now GATED by the models/* directory glob —
    see test_live_router_head_artifacts_are_voice_path above, which asserts the
    opposite of what that draft asserted."""
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == []
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is False and hits == []


def test_a_new_pattern_cannot_gate_the_pr_that_adds_it():
    """Pins the base-ref property that #1620 got wrong, twice.

    voice-gate.yml triggers on `pull_request_target` and pins `base.sha` on
    every checkout, so the classifier that runs against a PR is always the BASE
    ref's copy of this module — a pattern added by the PR does not exist for the
    run that classifies it. #1620 asserted the opposite in a commit message and
    in its PR body; run 30804036970 then reported scope=non-voice /
    replay-evidence=SKIPPED while the PR's own head added three patterns.

    This is asserted against a SIMULATED base tuple rather than by reading the
    workflow, because the property being pinned is behavioural: classification
    uses whatever tuple it was given, so a file newly listed in the HEAD tuple
    still classifies CLEAR under the base one."""
    head_tuple = vgc.VOICE_PATH_PATTERNS
    newly_added = "scripts/setup/systemd/functiongemma-router.service"
    assert newly_added in head_tuple, "precondition: the pattern is in HEAD"

    base_tuple = tuple(p for p in head_tuple if p != newly_added)
    assert vgc.touched_voice_files([newly_added], base_tuple) == [], (
        "under the BASE tuple the newly-gated file must classify CLEAR — that "
        "is exactly why a gate-extending PR cannot gate itself"
    )
    needs, hits, _ = vgc.scope_verdict([newly_added], base_tuple)
    assert needs is False and hits == []
    # ...and it DOES gate once the pattern is on main (the next PR's base).
    assert vgc.touched_voice_files([newly_added], head_tuple) == [newly_added]


# --- the LIVEKIT / WebRTC INGEST LANE ---------------------------------------
# The selected production WebRTC backend (ZOE_LK_USE_AIORTC=1 overrides a code
# default of 0), its router (registered unconditionally, main.py:2213-2219), and
# the media server's serving config. None of the three matched any glob, so PR
# #1636 — which fixed ~25-33% of every frame on this path being FFmpeg plane
# padding carrying stale PCM — tripped no gate at PR time or at deploy.
#
# READ THE EVIDENCE BOUNDARY. These are the only entries in the tuple the replay
# corpus cannot exercise at all; test_replay_corpus_does_not_traverse_the_livekit_lane
# below pins that premise, and the block comment beside the patterns in
# voice_gate_check.py states exactly what a green artifact does and does not
# certify for them. The gate here is a forcing function; the deterministic
# ci_safe suites in `validate` are the verification.
@pytest.mark.parametrize("path", [
    "services/zoe-data/livekit_aiortc.py",
    "services/zoe-data/routers/voice_livekit.py",
    # The on-demand container's serving config (port 7880, the 50000-50200 RTC
    # range, keys), mounted read-only by docker-compose.yml:154 — the same class
    # as llama-server.service / flue-zoe-brain.service, gated for the same reason.
    "services/livekit/config.yaml",
])
def test_livekit_lane_is_voice_path(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    # end to end through the classifier both the deploy and PR paths call
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    # The VENDORED BROWSER PUBLISHER. Genuinely on the voice path, and
    # deliberately ungated: it runs in the panel's browser, not on the Jetson, so
    # a deploy gate on the box governs nothing about it and no probe on the box
    # could exercise a vendor bump. Gating it would charge a 20-sample Kokoro
    # replay for a minified blob the probe cannot touch.
    "services/zoe-ui/dist/lib/livekit/livekit-client.umd.min.js",
    # Co-located tests: mechanism-only, never run inside a voice turn. Same rule
    # as every other test exclusion in this tuple.
    "services/zoe-data/tests/test_livekit_aiortc_tasks.py",
    "services/zoe-data/tests/test_voice_livekit_lifecycle.py",
    # Prefix-exactness: three LITERAL paths, not `*livekit*`. A wildcard would
    # sweep in every line above plus the docs, which is how a gate stops being
    # worth having.
    "services/zoe-data/livekit_aiortc_old.py",
    "services/zoe-data/routers/voice_livekit_v2.py",
    "services/livekit/config.yaml.bak",
    "services/livekit/README.md",
    "docs/knowledge/livekit-notes.md",
])
def test_livekit_non_runtime_paths_do_not_gate(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path], pats) == []
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is False and hits == []


def test_replay_corpus_does_not_traverse_the_livekit_lane():
    """THE PREMISE of the LiveKit entries' evidence statement — pinned, not assumed.

    The whole honest-but-partial framing beside those patterns rests on one
    factual claim: ~/.zoe-voice-samples is replayed through POST
    /api/voice/transcribe (the HTTP lane) and never reaches the WebRTC ingest
    code. If that stops being true — someone adds a LiveKit stage to the probe —
    the artifact starts certifying MORE than the comment says, and the comment
    plus the three docs that enumerate the gated set become wrong in the
    direction that overstates the evidence. Better to go red here and be told.

    Asserted against the probe chain's SOURCE rather than by running it: the
    harness needs the Jetson, ~2.3 GB of Kokoro, and the shared flock, none of
    which exist in the ci_safe lane. This is the cheap structural counterpart —
    the same reason voice_gate_check.py reads an artifact instead of producing
    one.
    """
    chain = [
        "scripts/maintenance/voice_regression_probe.py",
        "scripts/perf/measure_voice.py",
        "services/zoe-data/tests/replay_samples.py",
    ]
    offenders = {}
    for rel in chain:
        src = (REPO / rel)
        assert src.exists(), f"probe chain moved: {rel} is gone — re-verify the claim"
        lowered = src.read_text(encoding="utf-8").lower()
        hits = [tok for tok in ("livekit", "webrtc", "aiortc") if tok in lowered]
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "the replay corpus now appears to touch the LiveKit/WebRTC lane "
        f"({offenders}) — the evidence statement in "
        "scripts/maintenance/voice_gate_check.py (and docs/knowledge/voice-pipeline.md, "
        "docs/knowledge/merge-and-deploy.md, scripts/AGENTS.md) says it does NOT. "
        "Update all four, or revert the probe change."
    )


def test_livekit_patterns_are_literal_paths_not_a_wildcard():
    """The literals the exclusion test above is reasoning about.

    Stated as an explicit assertion because the exclusion list is only
    non-vacuous if the tuple really does carry literals: swap any of these for
    `*livekit*` and test_livekit_non_runtime_paths_do_not_gate goes red, which
    is the negative control this pins in place."""
    pats = vgc.voice_path_patterns()
    for literal in ("services/zoe-data/livekit_aiortc.py",
                    "services/zoe-data/routers/voice_livekit.py",
                    "services/livekit/config.yaml"):
        assert literal in pats
    assert not any("*livekit*" in p or p == "services/livekit/*" for p in pats)


# --- the VAD stage block (2026-09-26 Silero model-swap incident) -------------
# A Silero v6.2.1 export replaced the v6.0 model file, loaded cleanly and scored
# ~0.001 on real speech — barge-in / idle listening silently off for a day, every
# gate green (the replay starts at STT). The probe now runs the real VAD and
# records a `vad` block; the checker blocks on a failed or missing one for any
# artifact that CLAIMS the stage, and is agnostic for older artifacts.
def vad_artifact(vad=None, *, claim=True, **kw):
    art = artifact(**kw)
    if claim:
        art["vad_stage"] = True
    if vad is not None:
        art["vad"] = vad
    return art


def _vad(status="pass", clips=24, detected=23, **extra):
    return {"status": status, "clips": clips, "speech_detected": detected,
            "min_pass_frac": 0.6, "threshold": 0.5, "reason": extra.pop("reason", ""),
            **extra}


def test_passing_vad_block_clears_and_is_reported():
    ok, why = vgc.evaluate(vad_artifact(_vad()), now_epoch=NOW, max_age_s=DAY)
    assert ok, why
    assert "VAD 23/24" in why


def test_failed_vad_block_blocks_even_when_status_says_pass():
    """Defence in depth: the run status is folded by the probe, but the checker
    must not depend on that — a failed VAD block blocks on its own."""
    ok, why = vgc.evaluate(vad_artifact(_vad("fail", detected=0,
                                             reason="speech detected in 0/24")),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "VAD stage fail" in why and "0/24" in why


def test_errored_vad_block_blocks():
    ok, why = vgc.evaluate(vad_artifact(_vad("error", clips=0, detected=0)),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False and "VAD stage error" in why


def test_claimed_but_missing_vad_block_blocks():
    ok, why = vgc.evaluate(vad_artifact(None), now_epoch=NOW, max_age_s=DAY)
    assert ok is False and "no `vad` block" in why


@pytest.mark.parametrize("bad", ["garbage", ["pass"], 7])
def test_malformed_vad_block_blocks(bad):
    art = vad_artifact(None)
    art["vad"] = bad
    ok, _ = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY)
    assert ok is False


def test_unknown_vad_status_blocks():
    ok, why = vgc.evaluate(vad_artifact(_vad("maybe")), now_epoch=NOW, max_age_s=DAY)
    assert ok is False and "unrecognised" in why


@pytest.mark.parametrize("clips,detected", [
    (24, 14),        # 58% — the label says pass, the counts say fail
    (0, 0),          # nothing scored
    (10, 11),        # impossible counts
    ("24", 23),      # wrong types
    (True, True),
])
def test_pass_label_is_re_derived_from_the_counts(clips, detected):
    """The producer does not grade its own homework: a `pass` whose counts do
    not clear the checker's own 60% floor is not a pass."""
    ok, why = vgc.evaluate(vad_artifact(_vad("pass", clips=clips, detected=detected)),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False, why


def test_pass_at_exactly_the_floor_clears():
    ok, why = vgc.evaluate(vad_artifact(_vad("pass", clips=10, detected=6)),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok, why


def test_lowered_min_pass_frac_in_the_artifact_does_not_lower_the_bar():
    ok, _ = vgc.evaluate(vad_artifact(_vad("pass", clips=10, detected=2, min_pass_frac=0.1)),
                         now_epoch=NOW, max_age_s=DAY)
    assert ok is False


def test_skipped_vad_is_no_opinion_but_surfaced():
    ok, why = vgc.evaluate(vad_artifact(_vad("skip", clips=0, detected=0,
                                             reason="Silero model not present at /m")),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok, why
    assert "VAD stage skipped" in why and "not present" in why


def test_pre_vad_artifact_is_no_opinion_backward_compat():
    """An artifact written before the stage existed carries neither the claim nor
    the block: no opinion on VAD, so it still clears (and says nothing about VAD)."""
    art = artifact()
    assert "vad_stage" not in art and "vad" not in art
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY)
    assert ok, why
    assert "VAD" not in why


def test_unclaimed_failed_block_is_ignored_only_without_the_claim():
    """The claim is what makes the block binding; with it, the same block blocks."""
    bad = _vad("fail", detected=0)
    assert vgc.evaluate(vad_artifact(bad, claim=False), now_epoch=NOW, max_age_s=DAY)[0] is True
    assert vgc.evaluate(vad_artifact(bad, claim=True), now_epoch=NOW, max_age_s=DAY)[0] is False


def test_probe_always_claims_the_vad_stage(tmp_path):
    """Every artifact the CURRENT probe writes claims the stage and carries a
    block — even on a path that passed none (recorded as a skip with a reason)."""
    import json as _json
    args = _Args(tmp_path)
    vrp.emit_result(args, status="skip", summary=dict(vrp.EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline={}, reason="tight")
    payload = _json.loads(args.results.read_text())
    assert payload["vad_stage"] is True
    assert payload["vad"]["status"] == "skip" and payload["vad"]["reason"]


def test_checker_floor_matches_the_probe_floor():
    assert vgc.VAD_MIN_PASS_FRAC == vrp.VAD_MIN_PASS_FRAC == 0.60


@pytest.mark.parametrize("path", [
    "services/zoe-data/voice_vad.py",
    "services/zoe-data/voice_turn.py",
    # *silero*: any tracked file that fetches / pins / points at the model.
    "scripts/setup/fetch_silero_vad.sh",
    "config/silero_vad.sha256",
])
def test_vad_modules_and_model_material_are_voice_path(path):
    pats = vgc.voice_path_patterns()
    assert vgc.touched_voice_files([path, "docs/PLANS.md"], pats) == [path]
    needs, hits, _ = vgc.scope_verdict([path], pats)
    assert needs is True and hits == [path]


@pytest.mark.parametrize("path", [
    # Co-located tests never run inside a voice turn — same rule as every entry.
    "services/zoe-data/tests/test_voice_turn.py",
    "services/zoe-data/tests/test_voice_barge_in.py",
    "services/zoe-data/tests/test_livekit_vad_segmentation.py",
    # Literal-exactness: near-miss module names do not gate.
    "services/zoe-data/voice_vad_old.py",
    "services/zoe-data/voice_turns.py",
])
def test_vad_non_runtime_paths_do_not_gate(path):
    assert vgc.touched_voice_files([path], vgc.voice_path_patterns()) == []
