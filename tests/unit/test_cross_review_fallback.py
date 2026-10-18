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
echo "polly $* T=${CROSS_REVIEW_TIMEOUT_S:-}" >> "$STUB_LOG"
case "${STUB_POLLY:-ok}" in
  ok)          printf 'REVIEWER-VENDOR: openai/codex\nblocking: none\nCLEAN\n' ;;
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


@pytest.fixture()
def rig(tmp_path):
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
    }

    class Rig:
        def run(self, **overrides):
            e = {**env, **{k: str(v) for k, v in overrides.items()}}
            return subprocess.run(
                [str(_SCRIPT), "123", "The PR must not break X."],
                env=e, capture_output=True, text=True, timeout=120, check=False,
            )

        def calls(self) -> list[str]:
            return [ln.split()[0] for ln in log.read_text().splitlines() if ln.strip()]

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
    for flag in ("--no-tools", "--no-session", "--provider openrouter", "--model z-ai/glm-5.2"):
        assert flag in pi_call, f"pi must run with {flag}: {pi_call}"
    assert r.stdout.startswith("REVIEWER-VENDOR: z-ai/glm-5.2")
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
