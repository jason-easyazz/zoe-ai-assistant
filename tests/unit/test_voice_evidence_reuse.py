"""Evidence REUSE for the voice replay gate (voice_gate_check.py --allow-reuse).

A probe that passed for commit P may stand as evidence for a later commit H only when the
TREE diff P -> H touches nothing voice-relevant. Every case here runs against a real,
synthetic, throwaway git repo (no network, no live box) and drives the checker's real CLI
(`main()`), so what is pinned is the shipped behaviour, not a helper in isolation.

Each refusal carries a NEGATIVE CONTROL: the same scenario with the one guard that refuses
it switched off (monkeypatched) is ACCEPTED - proving the refusal comes from that guard and
not from some unrelated failure in the rig. Synthetic paths and data only.
"""
from __future__ import annotations

import calendar
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vgc = _load("voice_gate_check_reuse_under_test", "scripts/maintenance/voice_gate_check.py")

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
BASELINE_CREATED = "2026-07-14T00:00:00Z"

FILES = {
    "services/zoe-data/fast_tiers.py": "TIER = 1\n",                 # VOICE_PATH_PATTERNS
    "services/zoe-data/voice_delivery.py": "SPEED = 1.0\n",          # VOICE_PATH_PATTERNS
    "services/zoe-data/self_model.py": "SELF = 'shadow'\n",          # import closure, NOT a pattern
    "services/zoe-data/tests/test_tiers.py": "def test_x():\n    pass\n",
    "services/zoe-data/requirements.txt": "numpy<2\n",
    "services/zoe-auth/api/dependencies.py": "RATE = 10\n",
    "services/zoe-auth/requirements.txt": "fastapi\nredis\n",
    "services/zoe-ui/dist/page.html": "<p>hi</p>\n",
    "docs/knowledge/note.md": "a note\n",
    "docs/knowledge/flag-inventory.json": "{\"flags\": {}}\n",
    "tests/unit/test_other.py": "def test_y():\n    pass\n",
    "tools/helper.py": "X = 1\n",
    "README.md": "readme\n",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=ENV).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


class Rig:
    """main = M0; the PR branch adds a voice change (P, the probed head); main then moves
    (M1 = `main_change`); the PR is updated with main by a MERGE commit (H), exactly what
    `gh pr update-branch` produces. The artifact is a passing probe of P."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        for rel, text in FILES.items():
            write(self.repo, rel, text)
        self.m0 = commit(self.repo, "m0")
        git(self.repo, "checkout", "-q", "-b", "pr")
        write(self.repo, "services/zoe-data/fast_tiers.py", "TIER = 2  # the PR's voice change\n")
        self.p = commit(self.repo, "pr: voice change")
        self.artifact = tmp / "voice_regression_last.json"
        self.baseline = tmp / "baseline.json"
        self.baseline.write_text(json.dumps({"created_at": BASELINE_CREATED}))
        self.write_artifact()

    def tree(self, rev: str) -> str:
        return git(self.repo, "rev-parse", f"{rev}^{{tree}}")

    def write_artifact(self, commit_sha: str | None = None, *, status="pass", age_h=1.0,
                       dirty=False, clean_verified=True, tree: str | None = "auto") -> None:
        sha = commit_sha or self.p
        rec_tree = self.tree(sha) if tree == "auto" else tree
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_h * 3600))
        self.artifact.write_text(json.dumps({
            "status": status, "timestamp": ts, "created_at": ts, "reason": "",
            "baseline_ref": {"created_at": BASELINE_CREATED},
            "summary": {"n_samples": 20},
            "revision": {"commit": sha, "tree": rec_tree, "dirty": dirty,
                         "clean_verified": clean_verified, "service_dir": "/x/services/zoe-data"},
        }))

    def main_moves(self, change) -> str:
        git(self.repo, "checkout", "-q", "main")
        change(self.repo)
        m1 = commit(self.repo, "main moves")
        git(self.repo, "checkout", "-q", "pr")
        return m1

    def update_branch(self, m1: str, *, evil=None) -> str:
        """Merge main into the PR branch. `evil` edits the tree INSIDE the merge commit."""
        git(self.repo, "merge", "-q", "--no-ff", "--no-commit", m1)
        if evil:
            evil(self.repo)
        return commit(self.repo, "Merge branch 'main' into pr")

    def check(self, target: str, *flags: str) -> tuple[int, str]:
        argv = ["--repo", str(self.repo), "--artifact", str(self.artifact),
                "--baseline", str(self.baseline), "--require", *flags]
        if not any(f.startswith("--expect-") for f in flags):
            argv += ["--expect-revision", target]
        return run_main(argv)

    def pr_gate(self, target: str) -> tuple[int, str]:
        return self.check(target, "--expect-revision", target, "--allow-reuse")

    def deploy_gate(self, target: str) -> tuple[int, str]:
        return self.check(target, "--expect-tree-of", target, "--allow-reuse")


def run_main(argv: list[str]) -> tuple[int, str]:
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = vgc.main(argv)
    return rc, out.getvalue() + err.getvalue()


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def edit(rel: str, text: str = "changed\n"):
    return lambda repo: write(repo, rel, text)


def updated(rig: Rig, change) -> str:
    return rig.update_branch(rig.main_moves(change))


# ---------------------------------------------------------------- accepted ----

def test_a_pure_non_voice_update_reuses_the_evidence(rig):
    """The motivating case (shape of #2403): main gained two zoe-auth files - a module and
    zoe-auth's own requirements.txt - and the PR was updated with main."""
    def zoe_auth(repo):
        write(repo, "services/zoe-auth/api/dependencies.py", "RATE = 20\n")
        write(repo, "services/zoe-auth/requirements.txt", "fastapi\n")
    h = updated(rig, zoe_auth)
    rc, out = rig.pr_gate(h)
    assert rc == 0, out
    assert "REUSED evidence for" in out and rig.p[:8] in out and h[:8] in out
    assert "evidence reuse ACCEPTED" in out          # the log line names the decision


def test_without_allow_reuse_the_strict_binding_is_unchanged(rig):
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.check(h, "--expect-revision", h)
    assert rc == 1 and "evidence for DIFFERENT" in out and "evidence reuse" not in out.lower()


def test_exact_match_never_consults_reuse(rig, monkeypatch):
    called = []
    monkeypatch.setattr(vgc, "reusable_evidence", lambda *a, **k: called.append(1) or (False, "x", []))
    rc, out = rig.pr_gate(rig.p)
    assert rc == 0 and called == [], out


@pytest.mark.parametrize("rel", ["docs/knowledge/note.md", "tests/unit/test_other.py",
                                 "services/zoe-ui/dist/page.html", "tools/helper.py",
                                 "services/zoe-data/tests/test_tiers.py", "README.md"])
def test_other_non_voice_paths_reuse(rig, rel):
    rc, out = rig.pr_gate(updated(rig, edit(rel)))
    assert rc == 0, out


def test_a_later_non_voice_push_on_top_of_the_probed_head_reuses(rig):
    write(rig.repo, "tests/unit/test_other.py", "def test_y():\n    assert 1\n")
    h = commit(rig.repo, "pr: test only")
    rc, out = rig.pr_gate(h)
    assert rc == 0 and "(ancestor)" in out, out


def test_deploy_gate_reuses_across_the_squash_merge(rig):
    """The deploy target is a SQUASH of the updated head on main: never a descendant of
    the probed head, so the deploy binding is tree-only (as tree_matches already was)."""
    h = updated(rig, edit("services/zoe-auth/api/dependencies.py", "RATE = 30\n"))
    git(rig.repo, "checkout", "-q", "main")
    git(rig.repo, "merge", "-q", "--squash", "pr")
    d = commit(rig.repo, "squash (#1)")
    assert rig.tree(d) == rig.tree(h)
    rc, out = rig.deploy_gate(d)
    assert rc == 0 and "REUSED" in out, out
    # strict deploy binding without the flag: refused, exactly as before
    rc, out = rig.check(d, "--expect-tree-of", d)
    assert rc == 1 and "DIFFERENT code" in out


# ------------------------------------------------------------- refused --------
# Each case: (name, main-side change or None, evil-merge edit or None).

def _chmod_x(rel):
    def f(repo):
        git(repo, "update-index", "--chmod=+x", rel)
        os.chmod(repo / rel, 0o755)
    return f


def _rename(src, dst):
    def f(repo):
        (repo / dst).parent.mkdir(parents=True, exist_ok=True)
        git(repo, "mv", src, dst)
    return f


def _delete(rel):
    return lambda repo: git(repo, "rm", "-q", rel)


REFUSED = {
    "voice file changed": edit("services/zoe-data/voice_delivery.py", "SPEED = 1.3\n"),
    "zoe-data requirements changed": edit("services/zoe-data/requirements.txt", "numpy<3\n"),
    "root requirements file added": edit("requirements.txt", "requests\n"),
    "lockfile outside any voice dir": edit("labs/somewhere/package-lock.json", "{}\n"),
    "Dockerfile": edit("tools/Dockerfile", "FROM scratch\n"),
    "compose file": edit("docker-compose.override.yml", "services: {}\n"),
    "env defaults": edit("tools/.env.example", "ZOE_X=1\n"),
    "systemd drop-in": edit("ops/units/zoe-data.service.d/10-x.conf", "[Service]\n"),
    "model weights": edit("tools/model.onnx", "w\n"),
    "a non-Python manifest (go.mod)": edit("tools/go.mod", "module x\n"),
    "a Cargo manifest": edit("tools/rust/Cargo.toml", "[package]\n"),
    "a conda environment": edit("environment.yml", "name: x\n"),
    "checkout control (.gitattributes)": edit(".gitattributes", "*.py text eol=crlf\n"),
    "submodule config (.gitmodules)": edit(".gitmodules", "[submodule \"x\"]\n"),
    "voice file renamed OUT of the voice path": _rename("services/zoe-data/voice_delivery.py", "tools/voice_delivery.py"),
    "file renamed INTO the voice tree": _rename("tools/helper.py", "services/zoe-data/helper.py"),
    "voice file deleted": _delete("services/zoe-data/voice_delivery.py"),
    "mode change on a voice file": _chmod_x("services/zoe-data/voice_delivery.py"),
    "new file under a voice dir": edit("services/zoe-data/brand_new_module.py", "Y = 1\n"),
    "new file in the live brain lane": edit("labs/flue-zoe-brain-2x/src/new_tool.ts", "export {}\n"),
    "import-closure module the patterns miss": edit("services/zoe-data/self_model.py", "SELF = 'on'\n"),
    "runtime-read docs file (flag inventory)": edit("docs/knowledge/flag-inventory.json", "{\"flags\": {\"x\": 1}}\n"),
    "the flag-inventory generator the deploy runs": edit("tools/audit/flag_inventory.py", "GEN = 2\n"),
    "file loaded by path from the closure": edit("scripts/maintenance/harness_quiet.py", "Q = 1\n"),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_voice_relevant_update_is_refused(rig, name):
    h = updated(rig, REFUSED[name])
    rc, out = rig.pr_gate(h)
    assert rc == 1, f"{name}: {out}"
    assert "evidence reuse REFUSED" in out and "voice-relevant change" in out, out


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_negative_control_each_refusal_comes_from_the_relevance_guard(rig, name, monkeypatch):
    """Guard removed (nothing is voice-relevant) -> the SAME scenario is accepted. Proves
    each refusal above is the relevance rule's doing, not a broken rig."""
    h = updated(rig, REFUSED[name])
    monkeypatch.setattr(vgc, "reuse_relevance", lambda path, patterns=None: "")
    monkeypatch.setattr(vgc, "_SPECIAL_MODES", {})
    rc, out = rig.pr_gate(h)
    assert rc == 0, f"{name}: {out}"


def test_the_deploy_gate_refuses_a_voice_change_across_the_squash(rig):
    rig.update_branch(rig.main_moves(edit("services/zoe-data/voice_delivery.py", "SPEED = 2\n")))
    git(rig.repo, "checkout", "-q", "main")
    git(rig.repo, "merge", "-q", "--squash", "pr")
    d = commit(rig.repo, "squash (#2)")
    rc, out = rig.deploy_gate(d)
    assert rc == 1 and "voice_delivery.py" in out, out


def test_a_merge_commit_that_sneaks_in_a_voice_change_is_refused(rig, monkeypatch):
    """main moved by a non-voice file, but the MERGE COMMIT itself edits a voice file
    (an 'evil merge'). The rule is about TREES, not about which commit carried what."""
    m1 = rig.main_moves(edit("docs/knowledge/note.md"))
    h = rig.update_branch(m1, evil=edit("services/zoe-data/voice_delivery.py", "SPEED = 9\n"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "voice_delivery.py" in out, out
    monkeypatch.setattr(vgc, "reuse_relevance", lambda path, patterns=None: "")
    assert rig.pr_gate(h)[0] == 0, "negative control"


def test_a_symlink_anywhere_is_refused(rig, monkeypatch):
    def link(repo):
        (repo / "tools").mkdir(exist_ok=True)
        os.symlink("../services/zoe-data", repo / "tools/zd")
    h = updated(rig, link)
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "symlink" in out, out
    monkeypatch.setattr(vgc, "_SPECIAL_MODES", {})
    assert rig.pr_gate(h)[0] == 0, "negative control: tools/ is otherwise non-voice"


def test_a_submodule_anywhere_is_refused(rig, monkeypatch):
    git(rig.repo, "checkout", "-q", "main")
    git(rig.repo, "update-index", "--add", "--cacheinfo", f"160000,{rig.m0},vendor/sub")
    git(rig.repo, "commit", "-q", "-m", "main gains a submodule")   # no `add -A`: it would drop the gitlink
    m1 = git(rig.repo, "rev-parse", "HEAD")
    git(rig.repo, "checkout", "-q", "pr")
    h = rig.update_branch(m1)
    assert "160000" in git(rig.repo, "ls-tree", h, "vendor/sub")
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "submodule" in out, out
    monkeypatch.setattr(vgc, "_SPECIAL_MODES", {})
    assert rig.pr_gate(h)[0] == 0, "negative control"


def test_a_type_change_is_refused(rig, monkeypatch):
    def to_symlink(repo):
        (repo / "tools/helper.py").unlink()
        os.symlink("../README.md", repo / "tools/helper.py")
    h = updated(rig, to_symlink)
    rc, out = rig.pr_gate(h)
    assert rc == 1, out
    monkeypatch.setattr(vgc, "_SPECIAL_MODES", {})
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "diff status T" in out, "with the mode guard off the T status still refuses: " + out


def test_an_undecodable_path_is_refused(rig):
    def weird(repo):
        d = os.fsencode(str(repo / "tools")) + b"/bad-\xff-name.txt"
        with open(d, "wb") as fh:
            fh.write(b"x")
    rc, out = rig.pr_gate(updated(rig, weird))
    assert rc == 1 and "undecodable path" in out, out


def test_unresolvable_probed_commit_is_refused(rig, monkeypatch):
    ghost = "0123456789abcdef0123456789abcdef01234567"
    rig.write_artifact(ghost, tree="f" * 40)
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "cannot be resolved" in out, out


def test_a_dirty_probe_is_never_reused(rig):
    rig.write_artifact(dirty=True)
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "DIRTY" in out and "reuse ACCEPTED" not in out, out
    rig.write_artifact(clean_verified=False)
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "could NOT verify" in out, out
    rig.write_artifact()
    assert rig.pr_gate(h)[0] == 0, "negative control: the same evidence, clean, is reused"


@pytest.mark.parametrize("status", ["fail", "skip", "error"])
def test_a_failed_probe_is_never_reused(rig, status):
    rig.write_artifact(status=status)
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "NOT a pass" in out, out


def test_stale_evidence_is_never_reused(rig):
    rig.write_artifact(age_h=30)
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "STALE" in out, out
    rig.write_artifact(age_h=1)
    assert rig.pr_gate(h)[0] == 0, "negative control"


def test_evidence_for_an_unrelated_pr_is_refused(rig, monkeypatch):
    """A sibling branch with the IDENTICAL voice change (so the tree diff alone would be
    voice-free) is still not this PR's evidence: the probed commit must be an ancestor."""
    git(rig.repo, "checkout", "-q", "-b", "other", rig.m0)
    write(rig.repo, "services/zoe-data/fast_tiers.py", "TIER = 2  # the PR's voice change\n")
    write(rig.repo, "tools/helper.py", "X = 2\n")
    other = commit(rig.repo, "another PR, same voice edit")
    git(rig.repo, "checkout", "-q", "pr")
    rig.write_artifact(other)
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "not an ancestor" in out, out
    # negative control: drop the ancestry requirement and the tree rule alone accepts it
    real = vgc.reusable_evidence
    monkeypatch.setattr(vgc, "reusable_evidence",
                        lambda *a, **k: real(*a, **{**k, "require_ancestor": False}))
    assert rig.pr_gate(h)[0] == 0


def test_a_rebased_pr_is_refused(rig):
    m1 = rig.main_moves(edit("docs/knowledge/note.md"))
    git(rig.repo, "rebase", "-q", m1)
    h = git(rig.repo, "rev-parse", "HEAD")
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "not an ancestor" in out, out


def test_a_recorded_tree_that_disagrees_with_git_is_refused(rig, monkeypatch):
    rig.write_artifact(tree=rig.tree(rig.m0))
    h = updated(rig, edit("docs/knowledge/note.md"))
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "disagrees with git" in out, out
    rig.write_artifact(tree=None)
    rc, out = rig.pr_gate(h)
    assert rc == 1 and "disagrees with git" in out, "an artifact with NO recorded tree is not reusable"


def test_baseline_drift_still_blocks_reuse(rig):
    rig.baseline.write_text(json.dumps({"created_at": "2026-08-01T00:00:00Z"}))
    rc, out = rig.pr_gate(updated(rig, edit("docs/knowledge/note.md")))
    assert rc == 1 and "the bar moved" in out, out


def test_allow_reuse_without_a_binding_is_a_usage_error(rig):
    rc, out = run_main(["--repo", str(rig.repo), "--artifact", str(rig.artifact), "--require",
                        "--allow-reuse"])
    assert rc == 1 and "needs --expect-revision or --expect-tree-of" in out


def test_print_artifact_commit(rig):
    rc, out = run_main(["--artifact", str(rig.artifact), "--print-artifact-commit"])
    assert rc == 0 and out.strip() == rig.p
    rig.artifact.write_text(json.dumps({"revision": {"commit": "$(rm -rf /)"}}))
    rc, out = run_main(["--artifact", str(rig.artifact), "--print-artifact-commit"])
    assert rc == 1 and out == "", "a non-sha is never printed (the workflow passes it to git)"


# ------------------------------------------------------- the pure classifier ----

@pytest.mark.parametrize("path,relevant", [
    ("services/zoe-data/main.py", True),
    ("services/zoe-data/lexicons_data/en.json", True),
    ("services/zoe-data/tests/test_anything.py", False),
    ("services/zoe-data/tests/replay_samples.py", True),
    ("services/zoe-data/tests/requirements.txt", True),
    ("services/zoe-auth/requirements.txt", False),
    ("services/zoe-auth/Dockerfile", False),
    ("services/zoe-ui/Dockerfile", True),
    ("labs/flue-zoe-brain-2x/README.md", True),
    ("labs/other-lab/notes.md", False),
    ("docs/knowledge/kokoro-onnx-migration.md", False),
    ("docs/knowledge/flag-inventory.json", True),
    ("docs/knowledge/flag-inventory.md", False),
    (".github/workflows/deploy.yml", True),
    (".github/workflows/validate.yml", False),
    ("scripts/setup/systemd/zoe-data.service", True),
    ("scripts/maintenance/greploop_guard.py", False),
    ("go.sum", True),
    ("labs/x/Gemfile", True),
    ("services/zoe-ui/.gitattributes", True),
    ("../escape.py", True),
    ("a//b.py", True),
])
def test_reuse_relevance_classifier(path, relevant):
    assert bool(vgc.reuse_relevance(path)) is relevant, vgc.reuse_relevance(path)


def test_reuse_is_at_least_as_strict_as_the_deploy_scope():
    """Every path the deploy gate would gate is also non-reusable across."""
    for pat in vgc.VOICE_PATH_PATTERNS:
        concrete = pat.replace("*", "x")
        if vgc.touched_voice_files([concrete], vgc.VOICE_PATH_PATTERNS):
            assert vgc.reuse_relevance(concrete), concrete
