"""Pin the DSN + service-dir resolution for the voice regression probe.

The documented agent workflow runs scripts/maintenance/voice_regression_probe.py
from a git WORKTREE, which has NO gitignored services/zoe-data/.env of its own.
Two things must still resolve to the LIVE services/zoe-data:

  * `_resolve_service_dir` — else measure_voice skips ("no .env in <worktree>/…")
    and the probe errors on every worktree run unless --service-dir is passed by
    hand;
  * `_resolve_dsn` — else the replay corpus's REAL command writes (events /
    list_items) are left in the live DB (operator bug report during PR #1354's
    baseline refresh).

`_resolve_dsn` precedence:
  1. explicit POSTGRES_URL in the env,
  2. --service-dir/.env  (works from a worktree pointed at the live dir),
  3. each service-dir candidate's .env  (in-tree, then the main worktree),
  4. genuinely unresolvable -> "" (caller must fail loudly).

`_resolve_service_dir` precedence:
  1. explicit --service-dir  (always wins),
  2. REPO/services/zoe-data      if it has a .env,
  3. <main worktree>/services/zoe-data  if it has a .env  (the worktree fix),
  4. nothing resolvable -> REPO/services/zoe-data, so the EXISTING loud error
     still fires downstream. A skip must never equal a pass.

Pure resolution logic only — NO live DB, NO asyncpg connection, NO replay run.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")


def _write_env(dir_path: Path, dsn: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / ".env").write_text(
        f'FOO=bar\nPOSTGRES_URL="{dsn}"\nBAZ=qux\n', encoding="utf-8"
    )
    return dir_path


def test_resolves_from_service_dir_env_when_repo_env_absent(tmp_path, monkeypatch):
    """The load-bearing case: run from a worktree (no REPO .env), --service-dir
    pointed at the live services/zoe-data — DSN must come from that dir's .env."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    # Point REPO at an empty tmp dir so the last-ditch REPO/.env fallback is absent.
    monkeypatch.setattr(vrp, "REPO", tmp_path / "worktree")
    service_dir = _write_env(tmp_path / "live" / "services" / "zoe-data",
                             "postgresql://svc/live")
    args = SimpleNamespace(service_dir=str(service_dir))
    assert vrp._resolve_dsn(args) == "postgresql://svc/live"


def test_env_var_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://env/win")
    service_dir = _write_env(tmp_path / "svc", "postgresql://svc/lose")
    args = SimpleNamespace(service_dir=str(service_dir))
    assert vrp._resolve_dsn(args) == "postgresql://env/win"


def test_falls_back_to_repo_env_when_service_dir_has_none(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    repo = tmp_path / "repo"
    _write_env(repo / "services" / "zoe-data", "postgresql://repo/lastditch")
    monkeypatch.setattr(vrp, "REPO", repo)
    empty_service_dir = tmp_path / "empty"
    empty_service_dir.mkdir()
    args = SimpleNamespace(service_dir=str(empty_service_dir))
    assert vrp._resolve_dsn(args) == "postgresql://repo/lastditch"


def test_unresolvable_returns_empty(tmp_path, monkeypatch):
    """No env, no .env anywhere -> "" so the caller fails loudly (not a silent pass)."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(vrp, "REPO", tmp_path / "nope")
    monkeypatch.setattr(vrp, "_main_worktree_root", lambda: None)
    args = SimpleNamespace(service_dir=str(tmp_path / "also-nope"))
    assert vrp._resolve_dsn(args) == ""


def test_dsn_falls_back_to_main_worktree_env(tmp_path, monkeypatch):
    """DSN ladder and service-dir ladder share `_service_dir_candidates`, so a
    worktree run with NO --service-dir still reaches the live .env."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(vrp, "REPO", tmp_path / "worktree")
    main = tmp_path / "main"
    _write_env(main / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(vrp, "_main_worktree_root", lambda: main)
    assert vrp._resolve_dsn(SimpleNamespace(service_dir=None)) == "postgresql://main/live"


# --- service-dir resolution ladder -----------------------------------------


def test_service_dir_explicit_always_wins(tmp_path, monkeypatch):
    """An explicit --service-dir is never second-guessed, even when it has no
    .env — the operator's choice must reach the existing loud error, not be
    silently rewritten to some other directory."""
    monkeypatch.setattr(vrp, "REPO", tmp_path / "repo")
    _write_env(tmp_path / "repo" / "services" / "zoe-data", "postgresql://repo/x")
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert vrp._resolve_service_dir(str(explicit)) == explicit


def test_service_dir_prefers_in_tree_env_when_present(tmp_path, monkeypatch):
    """The live checkout / in-tree run: REPO has a .env, so no git lookup wins."""
    repo = tmp_path / "repo"
    _write_env(repo / "services" / "zoe-data", "postgresql://repo/x")
    monkeypatch.setattr(vrp, "REPO", repo)
    monkeypatch.setattr(vrp, "_main_worktree_root", lambda: tmp_path / "main")
    assert vrp._resolve_service_dir(None) == repo / "services" / "zoe-data"


def test_service_dir_resolves_main_worktree_when_repo_has_no_env(tmp_path, monkeypatch):
    """THE FIX: run from a worktree (no .env of its own) with no --service-dir —
    resolution must land on the MAIN checkout's services/zoe-data."""
    monkeypatch.setattr(vrp, "REPO", tmp_path / "worktree")
    (tmp_path / "worktree" / "services" / "zoe-data").mkdir(parents=True)
    main = tmp_path / "main"
    _write_env(main / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(vrp, "_main_worktree_root", lambda: main)
    assert vrp._resolve_service_dir(None) == main / "services" / "zoe-data"


def test_service_dir_falls_back_to_repo_so_the_loud_error_still_fires(tmp_path, monkeypatch):
    """No .env anywhere: resolution must NOT invent a path or quietly skip. It
    returns the in-tree default, which measure_voice rejects -> status=error /
    exit 2. Skip != pass is the doctrine this gate exists to enforce."""
    repo = tmp_path / "repo"
    monkeypatch.setattr(vrp, "REPO", repo)
    monkeypatch.setattr(vrp, "_main_worktree_root", lambda: None)
    assert vrp._resolve_service_dir(None) == repo / "services" / "zoe-data"


def test_main_worktree_root_survives_a_non_git_repo(tmp_path, monkeypatch):
    """`git rev-parse` failing (no git, not a repo) must degrade to None, never
    raise — the probe still runs and still errors loudly downstream."""
    monkeypatch.setattr(vrp, "REPO", tmp_path / "definitely-not-a-git-repo")
    assert vrp._main_worktree_root() is None


def test_main_worktree_root_resolves_from_a_real_linked_worktree(tmp_path):
    """The load-bearing mechanism, against real git: from a linked worktree,
    --git-common-dir's parent is the MAIN checkout."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")

    def _git(*args: str, cwd: Path) -> None:
        subprocess.run([git, *args], cwd=str(cwd), check=True,
                       capture_output=True, text=True)

    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=main)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init", cwd=main)
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", "-b", "wt", str(linked), cwd=main)

    with pytest.MonkeyPatch.context() as mp:
        # From the linked worktree: resolves to the MAIN checkout.
        mp.setattr(vrp, "REPO", linked)
        assert vrp._main_worktree_root().resolve() == main.resolve()
        # From the main checkout: a no-op (resolves to itself).
        mp.setattr(vrp, "REPO", main)
        assert vrp._main_worktree_root().resolve() == main.resolve()
