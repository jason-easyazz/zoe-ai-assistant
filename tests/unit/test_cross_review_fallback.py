"""Offline tests for scripts/maintenance/cross_review.sh (the fallback dispatcher).

The dispatcher tries polly (Omnigent) first, then `codex exec` run DIRECTLY, then ONE lean
tool-less pi pass, and exits non-zero with "cross-review unavailable" when none of them
produces a cross-vendor report. Every backend is a STUB here (polly script, codex, pi, gh),
selected through the CROSS_REVIEW_*_BIN / CROSS_REVIEW_POLLY_SCRIPT overrides, so nothing
touches Docker, Omnigent, OpenRouter or GitHub.

Each routing rule has a test that goes red when the rule is broken (verified by hand with
a mutated script, see the PR description): polly-ok uses polly; polly-fail uses codex;
codex-quota uses pi with the cost bound; all-fail exits non-zero; a same-vendor polly
report is never accepted as cross-vendor.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "maintenance" / "cross_review.sh"
_POLLY = _REPO_ROOT / "scripts" / "maintenance" / "cross_review_polly.sh"
_CALLER = _REPO_ROOT / "services" / "zoe-data" / "pipeline_cross_review.py"

_STUB_POLLY = r"""#!/usr/bin/env bash
echo "polly $* T=${CROSS_REVIEW_TIMEOUT_S:-} D=${CROSS_REVIEW_DIR:-}" >> "$STUB_LOG"
[ -f "${CROSS_REVIEW_DIR:-/nonexistent}/pr-head/pr_change.txt" ] && echo "polly-saw-pr-head" >> "$STUB_LOG"
OK='REVIEWER-VENDOR: openai/codex\nblocking: none\nCLEAN\n'
case "${STUB_POLLY:-ok}" in
  ok)          printf "$OK" ;;
  # The 2026-11-02 incident, reproduced against the fixture's "live" checkout: the reviewer
  # detaches the live HEAD (same commit, so ONLY the symbolic ref changes) ...
  detach)      git -C "$STUB_LIVE" checkout -q --detach; printf "$OK" ;;
  # ... or moves the checked-out branch onto the unmerged PR head (only the sha changes).
  mainto_pr)   git -C "$STUB_LIVE" update-ref refs/heads/main "$(git -C "$CROSS_REVIEW_DIR/pr-head" rev-parse HEAD)"; printf "$OK" ;;
  # A legitimate CD deploy fast-forwarding main to the commit on refs/zoe-deploy/main.
  deploy_ff)   git -C "$STUB_LIVE" update-ref refs/heads/main refs/zoe-deploy/main; printf "$OK" ;;
  claude)      printf 'REVIEWER-VENDOR: anthropic/claude-opus-4-8\nCLEAN\n' ;;
  noattest)    printf 'looks fine to me, CLEAN\n' ;;
  # A VALID non-Anthropic attestation on purpose: only the sentinel may reject this report
  # (without it the attestation check alone would, and the sentinel check could rot unseen).
  unavailable) printf 'REVIEWER-VENDOR: openai/codex\ntried codex and pi, both out of quota\nCROSS-VENDOR-REVIEW-UNAVAILABLE\n' ;;
  empty)       : ;;
  authfail)    echo "ALARM: session ended in error status 'failed'" >&2; exit 2 ;;
esac
"""

_STUB_CODEX = r"""#!/usr/bin/env bash
echo "codex $*" >> "$STUB_LOG"
prev=""; for a in "$@"; do [ "$prev" = "-C" ] && [ -f "$a/pr-head/pr_change.txt" ] && echo "codex-saw-pr-head" >> "$STUB_LOG"; prev="$a"; done
cat > "$STUB_DIR/codex.stdin"
case "${STUB_CODEX:-ok}" in
  ok)    printf 'blocking: none\nnon-blocking: none\nVERDICT: CLEAN (codex)\npadding to clear the minimum report size ........................\n' ;;
  quota) echo "ERROR: You've hit your usage limit. Try again at 3:07 AM." >&2; exit 1 ;;
  fail)  echo "codex: runner crashed" >&2; exit 3 ;;
esac
"""

_STUB_PI = r"""#!/usr/bin/env bash
echo "pi $*" >> "$STUB_LOG"
cat > "$STUB_DIR/pi.stdin"
case "${STUB_PI:-ok}" in
  ok)      printf 'VERDICT: CLEAN (pi)\npadding to clear the minimum report size ........................\n' ;;
  short)   printf 'VERDICT: PARTIAL (diff only; 1 file reviewed)\n' ;;
  partial) printf '1. blocking: first finding, streamed before the cut-off ...................\nVERDICT: CLEAN\n'; sleep 30 ;;
  quota)   echo "429 Too Many Requests: spend limit exceeded" >&2; exit 1 ;;
esac
"""

_STUB_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
if [ "${STUB_GH:-ok}" = fail ]; then echo "HTTP 502" >&2; exit 1; fi
python3 - "${STUB_DIFF_LINES:-20}" <<'PY'
import sys
n = int(sys.argv[1])
print("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py")
for i in range(n):
    print(f"+line {i} " + "x" * 60)
PY
"""


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _make_live_checkout(tmp_path: Path) -> Path:
    """A stand-in for /home/zoe/assistant: branch main checked out, an `origin` that serves
    refs/pull/123/head (the PR, one commit past main), and refs/zoe-deploy/main one commit
    past main as well (what the CD deploy would fast-forward to)."""
    live = tmp_path / "live"
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(origin))
    _git("init", "-q", str(live))
    _git("commit", "-q", "--allow-empty", "-m", "base", cwd=live)
    _git("remote", "add", "origin", str(origin), cwd=live)
    base = _git("rev-parse", "HEAD", cwd=live)
    (live / "pr_change.txt").write_text("the PR's change\n")
    _git("add", "pr_change.txt", cwd=live)
    _git("commit", "-q", "-m", "the PR", cwd=live)
    _git("push", "-q", "origin", "HEAD:refs/pull/123/head", cwd=live)
    _git("reset", "-q", "--hard", base, cwd=live)
    _git("commit", "-q", "--allow-empty", "-m", "next main", cwd=live)
    _git("update-ref", "refs/zoe-deploy/main", "HEAD", cwd=live)
    _git("reset", "-q", "--hard", base, cwd=live)
    return live


@pytest.fixture()
def rig(tmp_path):
    live = _make_live_checkout(tmp_path)
    wt_root = tmp_path / "worktrees"

    def stub(name: str, body: str) -> Path:
        p = tmp_path / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    log = tmp_path / "calls.log"
    log.write_text("")
    env = {
        **os.environ,
        "STUB_LOG": str(log),
        "STUB_DIR": str(tmp_path),
        "CROSS_REVIEW_POLLY_SCRIPT": str(stub("polly.sh", _STUB_POLLY)),
        "CROSS_REVIEW_CODEX_BIN": str(stub("codex", _STUB_CODEX)),
        "CROSS_REVIEW_PI_BIN": str(stub("pi", _STUB_PI)),
        "CROSS_REVIEW_GH_BIN": str(stub("gh", _STUB_GH)),
        # NEVER the real /home/zoe/assistant: the tripwire and the review worktree run git
        # against this, so every test gets its own throwaway "live" checkout.
        "CROSS_REVIEW_LIVE_CHECKOUT": str(live),
        "CROSS_REVIEW_WORKTREE_ROOT": str(wt_root),
        "STUB_LIVE": str(live),
    }

    class Rig:
        live_dir = live
        review_root = wt_root

        def run(self, script: Path = _SCRIPT, **overrides):
            e = {**env, **{k: str(v) for k, v in overrides.items()}}
            return subprocess.run(
                [str(script), "123", "The PR must not break X."],
                env=e, capture_output=True, text=True, timeout=120, check=False,
            )

        def calls(self) -> list[str]:
            return [
                ln.split()[0] for ln in log.read_text().splitlines()
                if ln.strip() and not ln.startswith(("polly-saw", "codex-saw"))
            ]

        def log_text(self) -> str:
            return log.read_text()

        def live_head(self) -> tuple[str, str]:
            sym = subprocess.run(["git", "-C", str(live), "symbolic-ref", "-q", "HEAD"],
                                 capture_output=True, text=True, check=False).stdout.strip()
            return _git("-C", str(live), "rev-parse", "HEAD"), sym

        def call_lines(self, who: str) -> list[str]:
            return [ln for ln in log.read_text().splitlines() if ln.startswith(who + " ")]

        def stdin_of(self, who: str) -> str:
            return (tmp_path / f"{who}.stdin").read_text()

    return Rig()


# ------------------------------------------------------------ routing: the four cases ---

def test_polly_ok_uses_polly_and_nothing_else(rig):
    r = rig.run()
    assert r.returncode == 0, r.stderr
    assert "CLEAN" in r.stdout and "REVIEWER-VENDOR: openai/codex" in r.stdout
    assert rig.calls() == ["polly"], "a good polly report must not spend codex or pi"
    assert "reviewed via polly" in r.stderr


def test_polly_failure_falls_back_to_codex_direct(rig):
    r = rig.run(STUB_POLLY="authfail")
    assert r.returncode == 0, r.stderr
    assert rig.calls() == ["polly", "gh", "codex"], rig.calls()
    # same contract + same output shape: contract in the prompt, attested reviewer first
    assert "The PR must not break X." in rig.stdin_of("codex")
    assert "<diff>" in rig.stdin_of("codex") and "+line 0" in rig.stdin_of("codex")
    assert r.stdout.startswith("REVIEWER-VENDOR: openai/codex")
    assert "VERDICT: CLEAN (codex)" in r.stdout
    assert "polly unavailable" in r.stderr


def test_codex_quota_falls_back_to_pi_with_the_cost_bound(rig):
    r = rig.run(STUB_POLLY="authfail", STUB_CODEX="quota")
    assert r.returncode == 0, r.stderr
    assert rig.calls() == ["polly", "gh", "codex", "pi"], rig.calls()
    pi_call = rig.call_lines("pi")[0]
    # The deterministic cap: a single tool-less completion, no agent loop, no session.
    # The model is the 2026-10-19 bake-off winner (docs/knowledge/reviewer-bakeoff-2026-10-19.md).
    for flag in ("--no-tools", "--no-session", "--provider openrouter", "--model openai/gpt-6.1-sol"):
        assert flag in pi_call, f"pi must run with {flag}: {pi_call}"
    assert r.stdout.startswith("REVIEWER-VENDOR: openai/gpt-6.1-sol")
    assert "VERDICT: CLEAN (pi)" in r.stdout
    assert "codex unavailable — quota" in r.stderr
    # fetched once, reused by both direct backends
    assert rig.calls().count("gh") == 1


def test_all_backends_failing_exits_nonzero_with_unavailable_message(rig):
    r = rig.run(STUB_POLLY="authfail", STUB_CODEX="quota", STUB_PI="quota")
    assert r.returncode == 2
    assert r.stdout == "", "no report may be printed when nothing reviewed"
    assert "cross-review unavailable" in r.stderr
    assert "NOT a clean review" in r.stderr
    for who in ("polly", "codex", "pi"):
        assert f"{who} unavailable" in r.stderr, f"the {who} reason must be reported"


# ------------------------------------------------ never a same-vendor "cross" review ---

@pytest.mark.parametrize(
    ("mode", "why"),
    [
        ("claude", "same-vendor"),
        ("noattest", "no REVIEWER-VENDOR attestation"),
        ("unavailable", "no non-Anthropic worker"),
        ("empty", "empty report"),
    ],
)
def test_polly_report_that_is_not_provably_cross_vendor_is_rejected(rig, mode, why):
    r = rig.run(STUB_POLLY=mode)
    assert r.returncode == 0, r.stderr
    assert rig.calls()[:1] == ["polly"] and "codex" in rig.calls(), (
        f"a polly report with {why} must trigger the codex fallback"
    )
    assert "anthropic" not in r.stdout.lower() and "claude" not in r.stdout.lower()


def test_there_is_no_claude_backend(rig):
    r = rig.run(CROSS_REVIEW_BACKENDS="polly claude")
    assert r.returncode == 1
    assert "no Claude backend" in r.stderr or "same-vendor" in r.stderr
    assert rig.calls() == [], "bad config must fail before any backend is touched"


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/auto",
        "openrouter/auto-beta",       # served anthropic/claude-sonnet-5.5 in the bake-off
        "openrouter/pareto-code",     # its default tier served anthropic/claude-fable-5.1
        "typesafe/jev-router",
        "nvidia/switchyard",
        "~z-ai/glm-latest",
        "@preset/zoe-reviewer",
        "anthropic/claude-sonnet-5.5",
    ],
)
def test_pi_model_must_be_one_concrete_non_anthropic_model(rig, model):
    # A router/alias hides the model that answered, so REVIEWER-VENDOR could not be trusted.
    r = rig.run(CROSS_REVIEW_PI_MODEL=model, STUB_POLLY="authfail", STUB_CODEX="quota")
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "ALARM: CROSS_REVIEW_PI_MODEL" in r.stderr
    assert rig.calls() == [], "bad config must fail before any backend is touched"


def test_a_concrete_pi_model_override_is_accepted(rig):
    # Negative control for the guard above: a concrete non-Anthropic id still runs.
    r = rig.run(CROSS_REVIEW_PI_MODEL="google/gemini-4-argon", STUB_POLLY="authfail", STUB_CODEX="quota")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("REVIEWER-VENDOR: google/gemini-4-argon")


def test_pi_never_runs_before_codex_has_been_tried(rig):
    # Review finding: the "pi only when codex is unavailable" rule must be a guard, not
    # merely the default order — "polly pi codex" would otherwise make pi a routine pass.
    r = rig.run(CROSS_REVIEW_BACKENDS="polly pi codex", STUB_POLLY="authfail", STUB_CODEX="ok")
    assert r.returncode == 0, r.stderr
    assert "pi" not in rig.calls() and "codex" in rig.calls()
    assert "skipped: codex has not been tried yet" in r.stderr


def test_codex_succeeding_never_spends_pi(rig):
    r = rig.run(STUB_POLLY="authfail")
    assert r.returncode == 0
    assert "pi" not in rig.calls(), "pi is the quota/unavailable fallback, not a routine extra pass"


def test_codex_is_called_without_a_model_override(rig):
    # polly/codex routing failure (b): invented ids (gpt-5-codex) are HTTP-400 on the
    # ChatGPT account; the direct path must use codex's own default.
    rig.run(STUB_POLLY="authfail")
    line = rig.call_lines("codex")[0]
    assert " -m " not in line and "--model" not in line, line
    assert "exec -s read-only" in line, "reviewer must run in a read-only sandbox"


# ------------------------------------------------------------------- pi cost bound ---

def test_pi_diff_is_capped_and_the_verdict_is_labelled_partial(rig):
    r = rig.run(
        STUB_POLLY="authfail", STUB_CODEX="quota", STUB_DIFF_LINES=4000,
        CROSS_REVIEW_PI_DIFF_MAX_BYTES=5000,
    )
    assert r.returncode == 0, r.stderr
    prompt = rig.stdin_of("pi")
    diff_part = prompt.split("<diff>", 1)[1].split("</diff>", 1)[0]
    assert len(diff_part.encode()) <= 5000 + 2, "pi must never be sent more than the cap"
    assert "PARTIAL COVERAGE" in prompt, "the reviewer must be told it saw a truncated diff"
    assert "COVERAGE: PARTIAL COVERAGE" in r.stdout, "the truncation must surface in the output"


def test_pi_timeout_returns_the_partial_output_instead_of_nothing(rig):
    r = rig.run(STUB_POLLY="authfail", STUB_CODEX="quota", STUB_PI="partial", CROSS_REVIEW_PI_TIMEOUT_S=1)
    assert r.returncode == 0, r.stderr
    assert "first finding, streamed before the cut-off" in r.stdout
    assert "PARTIAL — pi timed out" in r.stdout
    # a verdict pi streamed before being cut off must not read CLEAN under a PARTIAL banner
    assert "VERDICT: CLEAN" not in r.stdout and "VERDICT: PARTIAL (cut off; pi had said: CLEAN)" in r.stdout


def test_diff_fetch_failure_is_reported_not_swallowed(rig):
    r = rig.run(STUB_POLLY="authfail", STUB_GH="fail")
    assert r.returncode == 2
    assert "could not fetch the PR diff" in r.stderr
    assert "codex" not in rig.calls() and "pi" not in rig.calls()


# ------------------------------------------------------- time budget (polly last) ---

def test_polly_window_is_clamped_to_what_the_caller_timeout_leaves(rig):
    # polly's worst-case wall is its poll window + ~570 s of bounded phases. Started late
    # (non-default order) with too little left, it must be SKIPPED, never started and then
    # killed by the caller mid-review (that orphans the detached worker).
    r = rig.run(
        CROSS_REVIEW_BACKENDS="codex pi polly", STUB_CODEX="quota", STUB_PI="quota",
        CROSS_REVIEW_CALLER_TIMEOUT_S=700,
    )
    assert r.returncode == 2
    assert "polly" not in rig.calls(), "polly must not start with less than its overhead + minimum window left"
    assert "polly unavailable — insufficient time budget" in r.stderr


def test_polly_window_shrinks_to_fit_but_never_exceeds_its_default(rig):
    full = rig.run(STUB_POLLY="ok")
    assert "T=1200" in rig.call_lines("polly")[0], "default window is 1200 s"
    tight = rig.run(
        CROSS_REVIEW_BACKENDS="codex pi polly", STUB_CODEX="quota", STUB_PI="quota",
        CROSS_REVIEW_CALLER_TIMEOUT_S=1700,
    )
    assert full.returncode == 0 and tight.returncode == 0, tight.stderr
    t = int(re.search(r"T=(\d+)", rig.call_lines("polly")[1]).group(1))
    # 1700 - 20 margin - 600 polly overhead - a second or two already spent
    assert 1060 <= t <= 1080, t


def test_a_short_pi_report_below_the_old_80_byte_bar_is_still_kept(rig):
    # Review finding on this PR: an 80-byte floor discarded a genuine short/PARTIAL report.
    r = rig.run(STUB_POLLY="authfail", STUB_CODEX="quota", STUB_PI="short")
    assert r.returncode == 0, r.stderr
    assert "VERDICT: PARTIAL" in r.stdout


# ------------------------------------------------ wiring that tests cannot exercise ---

def test_caller_timeout_mirrors_the_pipeline_caller():
    caller = int(re.search(r"^_CROSS_REVIEW_TIMEOUT_S = (\d+)", _CALLER.read_text(), re.M).group(1))
    mine = int(re.search(r"^CALLER_TIMEOUT_S=(\d+)", _SCRIPT.read_text(), re.M).group(1))
    assert mine == caller, "the dispatcher clamps its phases to the caller's subprocess timeout"


def test_polly_brief_carries_the_cross_vendor_and_model_rules():
    sh = _POLLY.read_text()
    brief = re.search(r'^BRIEF="(.*)"$', sh, re.M).group(1)
    assert "OMIT args.model" in brief, "codex model ids are rejected on the ChatGPT account"
    assert "CROSS-VENDOR-REVIEW-UNAVAILABLE" in brief
    assert "REVIEWER-VENDOR:" in brief
    assert "NEVER by a Claude/Anthropic sub-agent" in brief


def test_scripts_are_executable_and_parse():
    for p in (_SCRIPT, _POLLY):
        assert os.access(p, os.X_OK), f"{p.name} must stay executable (callers exec it directly)"
        subprocess.run(["bash", "-n", str(p)], check=True)


@pytest.mark.parametrize("sep", ["\n", "\t", " \n\t "], ids=["newline", "tab", "mixed"])
def test_pi_model_guard_sees_pi_in_any_whitespace_separated_backend_list(rig, sep):
    # Review finding: the loops word-split (tabs/newlines accepted) but the guard matched a
    # literal space-delimited `pi`, so $'polly\ncodex\npi' skipped the Anthropic-model check.
    r = rig.run(
        CROSS_REVIEW_BACKENDS=sep.join(["polly", "codex", "pi"]),
        CROSS_REVIEW_PI_MODEL="anthropic/claude-sonnet-5.5",
        STUB_POLLY="authfail", STUB_CODEX="quota",
    )
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "ALARM: CROSS_REVIEW_PI_MODEL" in r.stderr
    assert rig.calls() == [], "bad config must fail before any backend is touched"


def test_newline_separated_backends_without_pi_do_not_need_a_pi_model(rig):
    # Negative control: the guard fires on `pi` itself, not on any multi-line list.
    r = rig.run(
        CROSS_REVIEW_BACKENDS="polly\ncodex",
        CROSS_REVIEW_PI_MODEL="anthropic/claude-sonnet-5.5",
        STUB_POLLY="authfail", STUB_CODEX="ok",
    )
    assert r.returncode == 0, r.stderr
    assert [c for c in rig.calls() if c != "gh"] == ["polly", "codex"]


def test_newline_separated_backends_keep_pi_behind_codex(rig):
    # The codex-before-pi ordering guard shares the same space-delimited match.
    r = rig.run(CROSS_REVIEW_BACKENDS="polly\npi\ncodex", STUB_POLLY="authfail", STUB_CODEX="ok")
    assert r.returncode == 0, r.stderr
    assert "pi" not in rig.calls() and "codex" in rig.calls()
    assert "skipped: codex has not been tried yet" in r.stderr


# ------------------------------- the live checkout is never touched (2026-11-02, #2255) ---
# polly ran `cd /workspace && git fetch origin pull/2253/head; git checkout FETCH_HEAD` in
# the then-writable live checkout: production HEAD detached onto an unmerged PR. The mount
# is read-only now (pinned in tests/unit/modules/test_omnigent_live_checkout_readonly.py);
# these pin the script half: reviewers get their own dir, and a moved live HEAD is an ALARM.

def test_reviewers_get_a_per_review_dir_with_the_pr_head_never_the_live_checkout(rig):
    r = rig.run(STUB_POLLY="authfail")
    assert r.returncode == 0, r.stderr
    polly_dir = re.search(r" D=(\S+)", rig.call_lines("polly")[0]).group(1)
    codex_line = rig.call_lines("codex")[0]
    codex_dir = re.search(r" -C (\S+)", codex_line).group(1)
    for d in (polly_dir, codex_dir):
        assert Path(d).parent == rig.review_root, d
        assert re.fullmatch(r"review-123-\d+-[A-Za-z0-9]{6}", Path(d).name), d
    assert "/workspace" not in codex_line and str(rig.live_dir) not in codex_line
    # Both reviewers could actually SEE the PR head (it is not in the live checkout).
    assert "polly-saw-pr-head" in rig.log_text() and "codex-saw-pr-head" in rig.log_text()
    assert not (rig.live_dir / "pr_change.txt").exists()
    # The prompt names the read-only live checkout, and the PR head as untrusted data.
    prompt = rig.stdin_of("codex")
    assert "NEVER run git checkout/switch/reset/fetch/commit" in prompt
    assert f"{codex_dir}/pr-head" in prompt


def test_review_dir_worktree_and_ref_are_removed_afterwards(rig):
    r = rig.run(STUB_POLLY="authfail")
    assert r.returncode == 0, r.stderr
    assert list(rig.review_root.iterdir()) == [], "the per-review dir must not outlive the review"
    worktrees = _git("-C", str(rig.live_dir), "worktree", "list", "--porcelain")
    assert worktrees.count("worktree ") == 1, worktrees
    assert _git("-C", str(rig.live_dir), "for-each-ref", "refs/zoe-review/") == ""


def test_a_stale_review_dir_from_a_dead_run_is_swept_but_foreign_dirs_are_not(rig):
    rig.review_root.mkdir()
    gone = subprocess.Popen(["true"])
    gone.wait()
    dead = gone.pid                                            # a pid that has exited
    alive = os.getpid()                                        # a pid that is running
    stale = rig.review_root / f"review-99-{dead}-AbC123"
    stale.mkdir()
    (stale / ".zoe-cross-review").write_text("")
    live_run = rig.review_root / f"review-97-{alive}-QrS456"   # a concurrent review: keep
    live_run.mkdir()
    (live_run / ".zoe-cross-review").write_text("")
    unmarked = rig.review_root / f"review-98-{dead}-XyZ789"   # same shape, no marker: not ours
    unmarked.mkdir()
    foreign = rig.review_root / "wt-1234"                     # a Multica task worktree
    foreign.mkdir()
    r = rig.run(STUB_POLLY="ok")
    assert r.returncode == 0, r.stderr
    assert not stale.exists(), "a dead run's review dir must be swept"
    assert live_run.exists() and unmarked.exists() and foreign.exists()


@pytest.mark.parametrize("mode", ["detach", "mainto_pr"])
def test_a_reviewer_that_moves_the_live_head_is_an_alarm_not_a_review(rig, mode):
    before = rig.live_head()
    r = rig.run(STUB_POLLY=mode)
    assert r.returncode == 3, (r.returncode, r.stderr)
    assert "ALARM: LIVE CHECKOUT MODIFIED" in r.stderr
    assert "Live checkout moved" in r.stderr, "the alarm must point at the recovery runbook"
    assert r.stdout == "", "a review during which the live checkout moved must not be reported"
    # Detect, never repair: the moved state is left for the owner to inspect.
    assert rig.live_head() != before


def test_a_deploy_fast_forward_during_the_review_is_not_an_alarm(rig):
    r = rig.run(STUB_POLLY="deploy_ff")
    assert r.returncode == 0, r.stderr
    assert "fast-forwarded by a deploy" in r.stderr
    assert "REVIEWER-VENDOR: openai/codex" in r.stdout


def test_the_tripwire_needs_a_readable_live_checkout(rig, tmp_path):
    r = rig.run(CROSS_REVIEW_LIVE_CHECKOUT=str(tmp_path / "not-a-repo"))
    assert r.returncode == 1
    assert "cannot read the live checkout" in r.stderr
    assert rig.calls() == [], "no backend may run without the tripwire armed"


@pytest.mark.parametrize("root", ["{live}/.claude/worktrees", "/workspace/x", "/home/zoe/assistant/x",
                                  "/home/zoe/.worktrees/../assistant"])
def test_the_review_root_can_never_be_inside_the_live_checkout(rig, root):
    r = rig.run(CROSS_REVIEW_WORKTREE_ROOT=root.format(live=rig.live_dir))
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "inside the live checkout" in r.stderr
    assert rig.calls() == []


def test_negative_control_without_the_tripwire_the_incident_passes_silently(rig, tmp_path):
    """Executed negative control: neutralise assert_live_untouched and the detach scenario
    goes green — so the alarm tests above are what catch a removed guard."""
    src = _SCRIPT.read_text()
    marker = "assert_live_untouched() {\n"
    assert src.count(marker) == 1
    mutated = tmp_path / "cross_review_no_guard.sh"
    mutated.write_text(src.replace(marker, marker + "  return 0\n"))
    mutated.chmod(0o755)
    r = rig.run(script=mutated, STUB_POLLY="detach")
    assert r.returncode == 0 and "LIVE CHECKOUT MODIFIED" not in r.stderr


def test_no_script_uses_the_live_checkout_as_a_review_cwd():
    for p in (_SCRIPT, _POLLY):
        code = "\n".join(ln for ln in p.read_text().splitlines() if not ln.lstrip().startswith("#"))
        assert "cd /workspace" not in code, f"{p.name} starts a reviewer in the live checkout"
        assert "-C /workspace" not in code and "-C /home/zoe/assistant" not in code, p.name
    polly = _POLLY.read_text()
    assert re.search(r'sh -c "cd \$KICK_CWD && omnigent run', polly), "the kick cwd must be the review dir"
    brief = re.search(r'^BRIEF="(.*)"$', polly, re.M).group(1)
    assert "NEVER run git checkout, switch, reset, fetch or commit" in brief
    assert "/workspace or /home/zoe/assistant" in brief


@pytest.mark.parametrize("bad", ["/workspace", "/workspace/x", "/home/zoe/assistant/.claude/worktrees/x",
                                 "/home/zoe/.worktrees/../assistant", "/tmp/x;rm -rf /", "relative/dir"])
def test_polly_refuses_a_review_dir_in_the_live_checkout_or_with_shell_metachars(bad):
    # Exits at validation, BEFORE any session is created (and no server is reachable here).
    r = subprocess.run(
        [str(_POLLY), "123", "contract"],
        env={**os.environ, "CROSS_REVIEW_DIR": bad, "OMNIGENT_SERVER": "http://127.0.0.1:9"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "CROSS_REVIEW_DIR" in r.stderr


# ------------------------------------- the codex app-server leak (2026-11-04/05) ---
#
# Host-side `timeout` signals the `docker exec` CLIENT only; the process INSIDE the container
# keeps running, and a codex review leaves `codex app-server` trees (~45 MB each; 93 of them
# held ~15 GB of swap) behind. Fix: an in-container `timeout` + a teardown sweep rooted in the
# per-review dir + a concurrency cap. These tests drive the REAL `docker exec` path against a
# fake `docker` that runs the command locally, so "the container" is this machine and a
# straggler is a real process we can check is dead.

_FAKE_DOCKER = r"""#!/usr/bin/env bash
[ "$1" = exec ] || exit 99
shift; dir=""
while [ $# -gt 0 ]; do
  case "$1" in -i|-t|-d) shift ;; -w) dir="$2"; shift 2 ;; *) break ;; esac
done
shift   # the container name
[ -n "$dir" ] && cd "$dir"
echo "dockerexec $1 $2 $3 $4 $5" >> "$STUB_LOG"
exec "$@"
"""

# A codex that hangs past its deadline AND leaves a detached straggler (its own session, like an
# app-server): `timeout` signals only its own process group, so only the sweep can reach it.
_HANGING_CODEX = r"""#!/usr/bin/env bash
cat > /dev/null
setsid sleep 300 &
echo $! >> "$STUB_PIDS"
echo $$ >> "$STUB_PIDS"
exec sleep 300
"""

_STUB_REAPER = r"""#!/usr/bin/env python3
import os, sys
if "--count-app-servers" in sys.argv:
    print(os.environ.get("STUB_COUNT", "0"))
else:
    open(os.environ["STUB_LOG"], "a").write("reaper " + " ".join(sys.argv[1:]) + "\n")
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is dead for our purposes
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@pytest.fixture()
def leakrig(rig, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", _FAKE_DOCKER), ("codex", _HANGING_CODEX)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    pids = tmp_path / "pids"
    pids.write_text("")
    reaper = tmp_path / "reaper_stub.py"
    reaper.write_text(_STUB_REAPER)

    class LeakRig:
        def run(self, script: Path = _SCRIPT, **overrides):
            env = {
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "STUB_PIDS": pids,
                "CROSS_REVIEW_CODEX_BIN": "",     # the REAL `docker exec ... codex` path
                "CROSS_REVIEW_PI_BIN": "",
                "CROSS_REVIEW_BACKENDS": "codex",
                "CROSS_REVIEW_CODEX_TIMEOUT_S": 6,
                **overrides,
            }
            return rig.run(script, **env)

        def pids(self) -> list[int]:
            return [int(x) for x in pids.read_text().split()]

        reaper_stub = reaper

    lr = LeakRig()
    yield lr, rig
    for pid in lr.pids():   # never leave a straggler behind, whatever the assertions did
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def test_codex_runs_under_an_in_container_timeout_inside_the_host_deadline(leakrig):
    lr, rig = leakrig
    r = lr.run()
    assert r.returncode == 2, r.stderr
    line = next(ln for ln in rig.log_text().splitlines() if ln.startswith("dockerexec timeout"))
    # dockerexec timeout -k 5 <inner> codex
    toks = line.split()
    assert toks[1:4] == ["timeout", "-k", "5"] and toks[5] == "codex", line
    assert 1 <= int(toks[4]) < 6, "the in-container deadline must fire before the host one"
    assert "timed out" in r.stderr


def test_a_timed_out_review_leaves_zero_children(leakrig):
    lr, rig = leakrig
    r = lr.run()
    assert r.returncode == 2, r.stderr
    pids = lr.pids()
    assert len(pids) == 2
    assert not [p for p in pids if _alive(p)], f"leaked review processes: {[p for p in pids if _alive(p)]}"
    assert not list(rig.review_root.glob("review-*")), "review dir must be removed"


def test_negative_control_without_the_sweep_the_straggler_survives(leakrig, tmp_path):
    """Remove only the teardown sweep: the detached straggler must outlive the review. This is
    what proves the test above can go red (and that `timeout` alone does not reach it)."""
    lr, rig = leakrig
    broken = tmp_path / "cross_review_nosweep.sh"
    text = _SCRIPT.read_text()
    assert text.count("\n  kill_review_dir_procs\n") == 1
    broken.write_text(text.replace("\n  kill_review_dir_procs\n", "\n"))
    broken.chmod(0o755)
    r = lr.run(broken)
    assert r.returncode == 2, r.stderr
    survivors = [p for p in lr.pids() if _alive(p)]
    assert survivors, "expected the detached straggler to survive without the sweep"


def test_the_sweep_only_touches_the_review_dirs_own_processes(leakrig, tmp_path):
    lr, rig = leakrig
    bystander = subprocess.Popen(["sleep", "300"], cwd=tmp_path)   # not under the review dir
    try:
        r = lr.run()
        assert r.returncode == 2, r.stderr
        assert bystander.poll() is None, "the sweep killed a process outside the review dir"
    finally:
        bystander.kill()
        bystander.wait()


def test_app_server_cap_skips_polly_and_falls_back_to_codex(leakrig):
    lr, rig = leakrig
    r = lr.run(
        CROSS_REVIEW_BACKENDS="polly codex",
        CROSS_REVIEW_REAPER_SCRIPT=lr.reaper_stub, STUB_COUNT=9,
        CROSS_REVIEW_MAX_APP_SERVERS=6,
    )
    assert "polly" not in rig.calls(), "polly must not add an app-server tree above the cap"
    assert "codex app-server trees already resident (cap 6)" in r.stderr
    assert "reaper --app-servers-only --execute" in rig.log_text(), "orphans must be swept first"


def test_negative_control_under_the_cap_polly_runs(leakrig):
    lr, rig = leakrig
    r = lr.run(
        CROSS_REVIEW_BACKENDS="polly codex",
        CROSS_REVIEW_REAPER_SCRIPT=lr.reaper_stub, STUB_COUNT=2,
        CROSS_REVIEW_MAX_APP_SERVERS=6,
    )
    assert r.returncode == 0, r.stderr
    assert rig.calls()[0] == "polly"


def test_a_polly_review_collects_orphaned_app_servers_on_exit(leakrig):
    lr, rig = leakrig
    lr.run(CROSS_REVIEW_BACKENDS="polly", CROSS_REVIEW_REAPER_SCRIPT=lr.reaper_stub)
    assert "reaper --app-servers-only --execute" in rig.log_text()


def test_a_codex_only_review_does_not_run_the_app_server_sweep(leakrig):
    lr, rig = leakrig
    lr.run(CROSS_REVIEW_REAPER_SCRIPT=lr.reaper_stub)
    assert "reaper " not in rig.log_text()


def test_no_omnigent_script_puts_a_secret_on_a_command_line():
    """A key was seen in a process list on 2026-11-05. Secrets travel by environment or file,
    never argv: no key-shaped literal, no --api-key style flag, no inline KEY=value prefix."""
    import re
    pat = re.compile(
        r"(--(api[-_]?key|token|password|secret)\b|\b[A-Z_]*(API_KEY|TOKEN|SECRET|PASSWORD)=\S|"
        r"Bearer\s+[A-Za-z0-9]|\bsk-[A-Za-z0-9]{8,})"
    )
    for f in (_SCRIPT, _POLLY, _REPO_ROOT / "scripts/maintenance/cross_review_poll.py",
              _REPO_ROOT / "scripts/maintenance/reap_stale_omnigent_runners.py"):
        for n, ln in enumerate(f.read_text().splitlines(), 1):
            code = ln.split("#", 1)[0] if f.suffix == ".sh" else ln
            if f.suffix == ".py" and ln.lstrip().startswith(("#", '"', "'")):
                continue
            assert not pat.search(code), f"{f.name}:{n} looks like a secret on a command line: {ln.strip()}"
