"""Behavioural tests for the `verdict` job in .github/workflows/voice-gate.yml.

The verdict is what actually publishes the REQUIRED `voice-gate` context, so it
is the single point where the whole gate becomes real or becomes theatre. These
tests execute the real embedded script against a stubbed GitHub API.

The competing-producer guard is the subtle one. Branch protection matches a
required context BY NAME and cannot authenticate its producer — and GitHub
publishes a check run for every job automatically, with no `checks: write`
needed. So a PR that adds `.github/workflows/anything.yml` containing a job named
`voice-gate` publishes its own passing gate, on its own head, BEFORE merge. The
guard lives in this base-owned `pull_request_target` workflow precisely so the PR
under review cannot delete it.
"""
from __future__ import annotations

import base64
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
        pulls: {
          listFiles: async () => {
            if (OPTS.listFilesFails) throw new Error('listFiles unavailable');
            return (OPTS.changedFiles || []).map(
              (f) => (typeof f === 'string' ? { filename: f, status: 'modified' } : f));
          },
        },
        repos: {
          getContent: async (o) => {
            const body = (OPTS.contents || {})[o.path];
            if (body === undefined) throw new Error('not found');
            return { data: { content: Buffer.from(body, 'utf8').toString('base64') } };
          },
        },
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
    process.env.VOICE = OPTS.voice || 'false';
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
    """The common case, and the reason this context is safe to require on every PR."""
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


def test_fork_without_approval_is_named_explicitly(tmp_path):
    """A gated-off fork looks identical to a missing runner unless named."""
    r = _run(tmp_path, voice="true", evidenceResult="skipped", fork=True)
    assert r["check"]["conclusion"] == "failure"
    assert "maintainer approval" in r["check"]["title"]
    assert "voice-gate-approved" in r["check"]["summary"]


# --- the competing-producer guard -------------------------------------------
COMPETING = """name: helper
on: [pull_request]
jobs:
  voice-gate:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
"""


def test_a_second_voice_gate_producer_fails_the_verdict(tmp_path):
    """THE guard. A job named `voice-gate` in any other workflow publishes a check
    of that name automatically — satisfying a name-matched required context on the
    PR's OWN head, before merge, with no `checks: write` anywhere."""
    r = _run(tmp_path, voice="false",
             changedFiles=[".github/workflows/helper.yml"],
             contents={".github/workflows/helper.yml": COMPETING})
    assert r["check"]["conclusion"] == "failure", r["check"]
    assert "second `voice-gate` producer" in r["check"]["title"]
    assert "helper.yml" in r["check"]["summary"]
    assert "job id" in r["check"]["summary"]


def test_a_name_override_producer_is_caught(tmp_path):
    """The job id is not the only way to name a check — a `name:` override does it
    too, and would slip past an id-only match."""
    wf = ("name: helper\non: [pull_request]\njobs:\n  innocuous:\n"
          "    name: voice-gate\n    runs-on: ubuntu-latest\n    steps:\n      - run: \"true\"\n")
    r = _run(tmp_path, changedFiles=[".github/workflows/helper.yml"],
             contents={".github/workflows/helper.yml": wf})
    assert r["check"]["conclusion"] == "failure"
    assert "`name:` override" in r["check"]["summary"]


def test_a_checks_api_producer_is_caught(tmp_path):
    """Publishing via the Checks API rather than a job name."""
    wf = ("name: helper\non: [pull_request]\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
          "    steps:\n      - uses: actions/github-script@v7\n        with:\n"
          "          script: |\n"
          "            await github.rest.checks.create({name: 'voice-gate', conclusion: 'success'});\n")
    r = _run(tmp_path, changedFiles=[".github/workflows/helper.yml"],
             contents={".github/workflows/helper.yml": wf})
    assert r["check"]["conclusion"] == "failure"
    assert "Checks API call" in r["check"]["summary"]


def test_an_unreadable_changed_workflow_fails_closed(tmp_path):
    """Unreadable is not evidence of innocence."""
    r = _run(tmp_path, changedFiles=[".github/workflows/helper.yml"], contents={})
    assert r["check"]["conclusion"] == "failure"
    assert "could not be read" in r["check"]["summary"]


def test_a_failing_guard_does_not_wave_the_pr_through(tmp_path):
    """The guard itself erroring is not permission to skip it."""
    r = _run(tmp_path, listFilesFails=True)
    assert r["check"]["conclusion"] == "failure"
    assert "guard could not run" in r["check"]["summary"]


def test_the_sanctioned_workflow_itself_is_not_flagged(tmp_path):
    """Editing voice-gate.yml must not self-trip the guard — that edit cannot
    affect this PR's own run anyway (the definition comes from the base ref), and
    flagging it would make the gate unmaintainable."""
    r = _run(tmp_path, changedFiles=[".github/workflows/voice-gate.yml"],
             contents={".github/workflows/voice-gate.yml": WORKFLOW.read_text()})
    assert r["check"]["conclusion"] == "success", r["check"]


def test_an_unrelated_workflow_mentioning_voice_gate_is_not_flagged(tmp_path):
    """A comment referencing the gate is legitimate and common. A bare substring
    match would block ordinary CI work with no override, so the guard keys on the
    three ways a file can actually PUBLISH the context."""
    wf = ("name: other\non: [pull_request]\njobs:\n  build:\n"
          "    # see voice-gate.yml for the replay-gate contract\n"
          "    runs-on: ubuntu-latest\n    steps:\n      - run: \"true\"\n")
    r = _run(tmp_path, changedFiles=[".github/workflows/other.yml"],
             contents={".github/workflows/other.yml": wf})
    assert r["check"]["conclusion"] == "success", r["check"]


def test_a_deleted_workflow_is_not_flagged(tmp_path):
    """A removed file publishes nothing."""
    r = _run(tmp_path,
             changedFiles=[{"filename": ".github/workflows/helper.yml", "status": "removed"}],
             contents={})
    assert r["check"]["conclusion"] == "success", r["check"]


def test_non_workflow_files_are_not_scanned(tmp_path):
    """The guard is about workflow files; ordinary source must not be fetched or
    matched (a python file containing the string `voice-gate` is routine)."""
    r = _run(tmp_path, changedFiles=["scripts/maintenance/voice_gate_check.py"],
             contents={})
    assert r["check"]["conclusion"] == "success", r["check"]
