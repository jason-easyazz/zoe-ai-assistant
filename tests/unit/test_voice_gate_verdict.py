"""Behavioural tests for the `verdict` job in .github/workflows/voice-gate.yml.

The verdict publishes the `voice-gate` check on the PR head. That check is
INFORMATIONAL — it is deliberately not in `required_status_checks` (descoped
2026-07-31, because a name-only required context cannot authenticate its
producer and guarding that in code proved to be an arms race). Enforcement of
voice regressions lives in the post-merge deploy gate plus review.

Informational is not the same as unimportant: a check nobody can trust is worse
than no check, because a green one gets believed. So these tests execute the real
embedded script against a stubbed GitHub API and pin that the verdict is HONEST —
it reports on every PR, fails closed on every ambiguity, and is addressed to the
commit under review rather than the base.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "voice-gate.yml"

import yaml

node = shutil.which("node")
if not node and os.environ.get("CI"):
    raise RuntimeError("node is required in CI for the voice-gate verdict tests")
pytestmark = [pytestmark, pytest.mark.skipif(not node, reason="node not available (non-CI)")]

HEAD = "1" * 40
BASE = "2" * 40


def _script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["verdict"]["steps"]
    script_steps = [st for st in steps if "github-script" in str(st.get("uses", ""))]
    assert len(script_steps) == 1, f"expected one github-script step, got {len(script_steps)}"
    return script_steps[0]["with"]["script"]


HARNESS = textwrap.dedent(
    """
    const SRC = require('fs').readFileSync(process.argv[2], 'utf8');
    const OPTS = JSON.parse(process.argv[3]);
    const calls = { checksCreated: [], failed: [], notices: [] };

    const github = {
      paginate: async (fn, o) => fn(o),
      rest: {
        checks: {
          create: async (o) => {
            calls.checksCreated.push({ name: o.name, sha: o.head_sha,
                                       conclusion: o.conclusion,
                                       title: (o.output || {}).title || '',
                                       summary: (o.output || {}).summary || '' });
            return {};
          },
        },
      },
    };
    const core = {
      info: () => {}, warning: () => {},
      notice: (m) => calls.notices.push(String(m)),
      setFailed: (m) => calls.failed.push(String(m)),
      summary: { addHeading() { return this; }, addRaw() { return this; },
                 async write() {} },
    };
    const context = {
      repo: { owner: 'jason-easyazz', repo: 'zoe-ai-assistant' },
      payload: { pull_request: {
        number: 7,
        head: { sha: '""" + HEAD + """',
                repo: { full_name: OPTS.fork ? 'someone/fork'
                                             : 'jason-easyazz/zoe-ai-assistant' } },
      } },
    };
    process.env.SCOPE_RESULT = OPTS.scopeResult || 'success';
    // `=== undefined` (not `||`): a real scope job with a missing/empty
    // GITHUB_OUTPUT value sets this env var to the literal empty string, which
    // is exactly the case under test — `||` would silently coerce that back to
    // 'false' and hide the bug. Only an unspecified option defaults to 'false'.
    process.env.VOICE = OPTS.voice === undefined ? 'false' : OPTS.voice;
    process.env.FILES = OPTS.files || '';
    process.env.EVIDENCE_RESULT = OPTS.evidenceResult || 'skipped';

    (async () => {
      await new Function('github', 'context', 'core',
        `return (async () => { ${SRC} })()`)(github, context, core);
      console.log(JSON.stringify(calls));
    })();
    """
)


def _run(tmp_path: Path, **opts) -> dict:
    sp = tmp_path / "script.js"
    sp.write_text(_script())
    hp = tmp_path / "harness.js"
    hp.write_text(HARNESS)
    proc = subprocess.run([node, str(hp), str(sp), json.dumps(opts)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"harness failed: {proc.stderr[-2000:]}"
    r = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(r["checksCreated"]) == 1, (
        f"the verdict must publish exactly one context, got {r['checksCreated']}")
    r["check"] = r["checksCreated"][0]
    return r


# --- the context is published, correctly addressed --------------------------
def test_verdict_publishes_voice_gate_against_the_pr_head(tmp_path):
    r = _run(tmp_path)
    assert r["check"]["name"] == "voice-gate"
    assert r["check"]["sha"] == HEAD, "must publish against the PR head, not the base"


def test_non_voice_pr_passes_trivially(tmp_path):
    """The common case: no voice files, no Jetson involvement, honest green."""
    r = _run(tmp_path, voice="false")
    assert r["check"]["conclusion"] == "success"
    assert "Not applicable" in r["check"]["title"]
    assert r["failed"] == []


def test_voice_pr_with_evidence_passes(tmp_path):
    r = _run(tmp_path, voice="true", evidenceResult="success", files="fast_tiers.py")
    assert r["check"]["conclusion"] == "success"


def test_voice_pr_without_evidence_fails_closed(tmp_path):
    r = _run(tmp_path, voice="true", evidenceResult="failure", files="fast_tiers.py")
    assert r["check"]["conclusion"] == "failure"
    assert r["failed"], "the run must go red alongside the published context"


def test_unreadable_scope_fails_closed(tmp_path):
    """An unreadable scope job is not evidence of a non-voice PR."""
    r = _run(tmp_path, scopeResult="failure")
    assert r["check"]["conclusion"] == "failure"


# --- FIX: a missing/empty/unexpected `voice` output must fail closed --------
# scope can report `success` while still publishing no usable `voice` value —
# e.g. it could not write $GITHUB_OUTPUT. Only an EXPLICIT 'false' may be read
# as non-voice; anything else must be treated as unclassified.
def test_missing_voice_output_fails_closed_even_when_scope_succeeded(tmp_path):
    """The exact bug: scope job succeeds, but `voice` never got published (the
    harness's default env leaves VOICE unset -> the empty string here)."""
    r = _run(tmp_path, voice="")
    assert r["check"]["conclusion"] == "failure"
    assert r["failed"], "the run must go red alongside the published context"


def test_unexpected_voice_value_fails_closed(tmp_path):
    r = _run(tmp_path, voice="garbage")
    assert r["check"]["conclusion"] == "failure"


def test_explicit_false_is_the_only_value_that_clears(tmp_path):
    """Positive control — without it, the fail-closed tests above could pass by
    blocking unconditionally regardless of `voice`."""
    r = _run(tmp_path, voice="false")
    assert r["check"]["conclusion"] == "success"


def test_fork_without_approval_is_named_explicitly(tmp_path):
    """A gated-off fork looks identical to a missing runner unless named."""
    r = _run(tmp_path, voice="true", evidenceResult="skipped", fork=True)
    assert r["check"]["conclusion"] == "failure"
    assert "maintainer approval" in r["check"]["title"]
    assert "voice-gate-approved" in r["check"]["summary"]
