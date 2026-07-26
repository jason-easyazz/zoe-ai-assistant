import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

import runtime_env  # noqa: F401  (ensures repo sys.path is set up like siblings)

pytestmark = pytest.mark.ci_safe


def _module():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/maintenance/zoe_cheap_pr_agent.py"
    )
    spec = importlib.util.spec_from_file_location("zoe_cheap_pr_agent", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_redact_scrubs_secrets():
    mod = _module()
    out = mod.redact("OPENROUTER_API_KEY=sk-abc123 and authorization: bearer tok-xyz")
    assert "sk-abc123" not in out
    assert "tok-xyz" not in out
    assert "<redacted>" in out


def test_parse_response_handles_wrapped_json():
    mod = _module()
    parsed = mod.parse_response('Sure!\n{"blocked": "ambiguous"}\nThanks')
    assert parsed == {"blocked": "ambiguous"}


def test_apply_edits_requires_unique_match():
    mod = _module()
    with pytest.raises(ValueError):
        mod.apply_edits("a = 1\na = 1\n", [{"old_string": "a = 1", "new_string": "a = 2"}])
    with pytest.raises(ValueError):
        mod.apply_edits("a = 1\n", [{"old_string": "missing", "new_string": "x"}])
    out = mod.apply_edits("a = 1\nb = 2\n", [{"old_string": "b = 2", "new_string": "b = 3"}])
    assert out == "a = 1\nb = 3\n"


def test_git_rejects_forbidden_flags(tmp_path):
    mod = _module()
    for flag in ("--force", "--force-with-lease", "-f", "--amend", "--no-verify"):
        with pytest.raises(AssertionError):
            mod._git(["push", flag], tmp_path)


def test_run_reverts_staged_change_when_commit_fails(tmp_path, monkeypatch):
    mod = _module()
    repo = _init_repo_with_remote(tmp_path)

    monkeypatch.setattr(mod, "_load_openrouter_key", lambda: "test-key")
    monkeypatch.setattr(
        mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {"edits": [{"old_string": "x = 1", "new_string": "x = 2"}], "summary": "bump x"}
        ),
    )
    # Force `git commit` to fail after `git add` has already staged the file.
    real_git = mod._git

    def _fake_git(args, cwd):
        if args[:1] == ["commit"]:
            return subprocess.CompletedProcess(args, 1, "", "hook rejected")
        return real_git(args, cwd)

    monkeypatch.setattr(mod, "_git", _fake_git)

    packet = {
        "task_type": "FIX_GREPTILE_FINDING",
        "allowed_files": ["mod.py"],
        "issue_text": "x should be 2",
        "max_changed_lines": 120,
    }
    rc = mod.run(packet, repo)
    assert rc == 0
    # No partial edit left behind: working tree and index both restored.
    assert (repo / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "mod.py"],
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert porcelain == ""


def test_run_rolls_back_local_commit_when_push_fails(tmp_path, monkeypatch):
    mod = _module()
    repo = _init_repo_with_remote(tmp_path)
    head_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True
    ).stdout.strip()

    monkeypatch.setattr(mod, "_load_openrouter_key", lambda: "test-key")
    monkeypatch.setattr(
        mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {"edits": [{"old_string": "x = 1", "new_string": "x = 2"}], "summary": "bump x"}
        ),
    )
    real_git = mod._git

    def _fake_git(args, cwd):
        if args[:1] == ["push"]:
            return subprocess.CompletedProcess(args, 1, "", "remote rejected")
        return real_git(args, cwd)

    monkeypatch.setattr(mod, "_git", _fake_git)

    packet = {
        "task_type": "FIX_GREPTILE_FINDING",
        "allowed_files": ["mod.py"],
        "issue_text": "x should be 2",
        "max_changed_lines": 120,
    }
    rc = mod.run(packet, repo)
    assert rc == 0
    # Local commit rolled back: HEAD unchanged and no lingering tracked change.
    head_after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True
    ).stdout.strip()
    assert head_after == head_before
    assert (repo / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "mod.py"], text=True, capture_output=True
    ).stdout.strip()
    assert porcelain == ""


def test_run_blocks_on_multiple_allowed_files(capsys):
    mod = _module()
    rc = mod.run({"task_type": "FIX_GREPTILE_FINDING", "allowed_files": ["a.py", "b.py"]}, Path("/tmp"))
    assert rc == 0
    assert "BLOCKED" in capsys.readouterr().out


def test_run_blocks_on_unsupported_task_type(capsys):
    mod = _module()
    rc = mod.run({"task_type": "FIX_CI_FAILURE", "allowed_files": ["a.py"]}, Path("/tmp"))
    assert rc == 0
    assert "BLOCKED" in capsys.readouterr().out


def _init_repo_with_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    for cfg in (["user.email", "t@t"], ["user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), "config", *cfg], check=True, capture_output=True)
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "origin", "HEAD:main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "fix/feature"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "push", "-u", "origin", "HEAD:fix/feature"],
        check=True,
        capture_output=True,
    )
    return repo


def test_run_applies_commits_and_pushes(tmp_path, monkeypatch):
    mod = _module()
    repo = _init_repo_with_remote(tmp_path)

    monkeypatch.setattr(mod, "_load_openrouter_key", lambda: "test-key")
    monkeypatch.setattr(
        mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {"edits": [{"old_string": "x = 1", "new_string": "x = 2"}], "summary": "bump x"}
        ),
    )

    packet = {
        "task_type": "FIX_GREPTILE_FINDING",
        "allowed_files": ["mod.py"],
        "issue_text": "x should be 2",
        "max_changed_lines": 120,
        "commands_to_run": ["python3 -c \"import ast; ast.parse(open('mod.py').read())\""],
    }
    rc = mod.run(packet, repo)
    assert rc == 0
    assert (repo / "mod.py").read_text(encoding="utf-8") == "x = 2\n"
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%s"], text=True, capture_output=True
    ).stdout.strip()
    assert log == "fix(review): bump x"
    # pushed: local branch and origin ref point at the same commit
    local = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "origin/fix/feature"], text=True, capture_output=True
    ).stdout.strip()
    assert local == remote


def test_run_reverts_when_validation_fails(tmp_path, monkeypatch):
    mod = _module()
    repo = _init_repo_with_remote(tmp_path)

    monkeypatch.setattr(mod, "_load_openrouter_key", lambda: "test-key")
    monkeypatch.setattr(
        mod,
        "call_llm",
        lambda *a, **k: json.dumps(
            {"edits": [{"old_string": "x = 1", "new_string": "x = 2"}], "summary": "bump x"}
        ),
    )

    packet = {
        "task_type": "FIX_GREPTILE_FINDING",
        "allowed_files": ["mod.py"],
        "issue_text": "x should be 2",
        "max_changed_lines": 120,
        "commands_to_run": ["false"],
    }
    rc = mod.run(packet, repo)
    assert rc == 0
    # reverted to original — no lingering tracked change
    assert (repo / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
    tracked_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only"], text=True, capture_output=True
    ).stdout.strip()
    assert tracked_diff == ""
