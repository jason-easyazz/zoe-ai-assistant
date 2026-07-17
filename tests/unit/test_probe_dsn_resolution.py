"""Pin the DSN + service-dir resolution for the voice harness entrypoints.

The documented agent workflow runs the voice harness from a git WORKTREE, which
has NO gitignored services/zoe-data/.env of its own. Three things must still
resolve to the LIVE services/zoe-data:

  * `scripts/lib/service_dir.resolve_service_dir` — the ONE ladder, shared by
    every entrypoint (the probe, measure_voice, measure_tts) so they cannot
    drift; else a run skips ("no .env in <worktree>/…") unless --service-dir is
    passed by hand;
  * `_resolve_dsn` (probe) — else the replay corpus's REAL command writes
    (events / list_items) are left in the live DB (operator bug report during PR
    #1354's baseline refresh).

`resolve_service_dir` precedence:
  1. explicit --service-dir  (always wins),
  2. REPO/services/zoe-data      if it has a .env,
  3. <main worktree>/services/zoe-data  if it has a .env  (the worktree fix),
  4. nothing resolvable -> REPO/services/zoe-data, so the EXISTING loud
     skip/error still fires downstream. A skip must never equal a pass.

`_resolve_dsn` precedence:
  1. explicit POSTGRES_URL in the env,
  2. --service-dir/.env  (works from a worktree pointed at the live dir),
  3. each service-dir candidate's .env  (in-tree, then the main worktree),
  4. genuinely unresolvable -> "" (caller must fail loudly).

Pure resolution logic only — NO live DB, NO asyncpg connection, NO replay run,
NO Kokoro load. The measure_* cases below assert on the resolver's OWN skip
messages, which fire before any subprocess is spawned.
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


# The shared ladder is loaded FIRST and registered under the name the scripts
# import ("service_dir"), so every consumer below binds to THIS module object and
# monkeypatching its REPO / main_worktree_root reaches all of them.
sd = _load("service_dir", "scripts/lib/service_dir.py")
vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")
mv = _load("measure_voice", "scripts/perf/measure_voice.py")
mt = _load("measure_tts", "scripts/perf/measure_tts.py")


def test_every_entrypoint_shares_one_ladder():
    """The anti-drift invariant: the probe and both perf probes must resolve via
    the SAME function object, not a copy each. A duplicated ladder is how the two
    fell out of sync in the first place."""
    assert vrp._resolve_service_dir is sd.resolve_service_dir
    assert mv.resolve_service_dir is sd.resolve_service_dir
    assert mt.resolve_service_dir is sd.resolve_service_dir


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
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
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
    monkeypatch.setattr(sd, "REPO", repo)
    empty_service_dir = tmp_path / "empty"
    empty_service_dir.mkdir()
    args = SimpleNamespace(service_dir=str(empty_service_dir))
    assert vrp._resolve_dsn(args) == "postgresql://repo/lastditch"


def test_unresolvable_returns_empty(tmp_path, monkeypatch):
    """No env, no .env anywhere -> "" so the caller fails loudly (not a silent pass)."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(sd, "REPO", tmp_path / "nope")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: None)
    args = SimpleNamespace(service_dir=str(tmp_path / "also-nope"))
    assert vrp._resolve_dsn(args) == ""


def test_dsn_falls_back_to_main_worktree_env(tmp_path, monkeypatch):
    """DSN ladder and service-dir ladder share `_service_dir_candidates`, so a
    worktree run with NO --service-dir still reaches the live .env."""
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
    main = tmp_path / "main"
    _write_env(main / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: main)
    assert vrp._resolve_dsn(SimpleNamespace(service_dir=None)) == "postgresql://main/live"


# --- service-dir resolution ladder -----------------------------------------


def test_service_dir_explicit_always_wins(tmp_path, monkeypatch):
    """An explicit --service-dir is never second-guessed, even when it has no
    .env — the operator's choice must reach the existing loud error, not be
    silently rewritten to some other directory."""
    monkeypatch.setattr(sd, "REPO", tmp_path / "repo")
    _write_env(tmp_path / "repo" / "services" / "zoe-data", "postgresql://repo/x")
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert sd.resolve_service_dir(str(explicit)) == explicit


def test_service_dir_prefers_in_tree_env_when_present(tmp_path, monkeypatch):
    """The live checkout / in-tree run: REPO has a .env, so no git lookup wins."""
    repo = tmp_path / "repo"
    _write_env(repo / "services" / "zoe-data", "postgresql://repo/x")
    monkeypatch.setattr(sd, "REPO", repo)
    monkeypatch.setattr(sd, "main_worktree_root", lambda: tmp_path / "main")
    assert sd.resolve_service_dir(None) == repo / "services" / "zoe-data"


def test_service_dir_resolves_main_worktree_when_repo_has_no_env(tmp_path, monkeypatch):
    """THE FIX: run from a worktree (no .env of its own) with no --service-dir —
    resolution must land on the MAIN checkout's services/zoe-data."""
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
    (tmp_path / "worktree" / "services" / "zoe-data").mkdir(parents=True)
    main = tmp_path / "main"
    _write_env(main / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: main)
    assert sd.resolve_service_dir(None) == main / "services" / "zoe-data"


def test_service_dir_falls_back_to_repo_so_the_loud_error_still_fires(tmp_path, monkeypatch):
    """No .env anywhere: resolution must NOT invent a path or quietly skip. It
    returns the in-tree default, which measure_voice rejects -> status=error /
    exit 2. Skip != pass is the doctrine this gate exists to enforce."""
    repo = tmp_path / "repo"
    monkeypatch.setattr(sd, "REPO", repo)
    monkeypatch.setattr(sd, "main_worktree_root", lambda: None)
    assert sd.resolve_service_dir(None) == repo / "services" / "zoe-data"


def test_main_worktree_root_survives_a_non_git_repo(tmp_path, monkeypatch):
    """`git rev-parse` failing (no git, not a repo) must degrade to None, never
    raise — the probe still runs and still errors loudly downstream."""
    monkeypatch.setattr(sd, "REPO", tmp_path / "definitely-not-a-git-repo")
    assert sd.main_worktree_root() is None


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
        mp.setattr(sd, "REPO", linked)
        assert sd.main_worktree_root().resolve() == main.resolve()
        # From the main checkout: a no-op (resolves to itself).
        mp.setattr(sd, "REPO", main)
        assert sd.main_worktree_root().resolve() == main.resolve()


# --- the DIRECT-run consumers: measure_voice / measure_tts ------------------
# Before this fix only the probe auto-resolved; running measure_voice.py itself
# from a worktree still needed a hand-passed --service-dir. These pin that the
# shared ladder actually reaches both, AND that the loud skip is untouched.


def _stub_replay_harness(service_dir: Path) -> None:
    """measure_voice checks for the replay harness before the .env — give the
    resolved dir one so the .env gate is what we're actually exercising."""
    (service_dir / "tests").mkdir(parents=True, exist_ok=True)
    (service_dir / "tests" / "replay_samples.py").write_text("", encoding="utf-8")


def test_measure_voice_resolves_main_worktree_env_with_no_flag(tmp_path, monkeypatch, capsys):
    """THE FIX, from measure_voice's own entrypoint: a DIRECT worktree run with
    no --service-dir must reach the MAIN checkout's live services/zoe-data."""
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
    main_service_dir = _write_env(tmp_path / "main" / "services" / "zoe-data",
                                  "postgresql://main/live")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: tmp_path / "main")
    # No replay harness in the resolved dir -> measure_voice stops at that check
    # and NAMES the dir it resolved to, before spawning any subprocess.
    monkeypatch.setattr(sys, "argv", ["measure_voice.py"])
    assert mv.main() == 0
    err = capsys.readouterr().err
    assert "replay harness not found" in err
    assert str(main_service_dir) in err   # resolved to the LIVE dir, not the worktree


def test_measure_voice_skips_loudly_when_no_env_resolves_anywhere(tmp_path, monkeypatch, capsys):
    """The ladder fixes the DEFAULT, never the failure mode: with no .env
    anywhere, measure_voice must still emit its loud result-less skip (which the
    probe turns into status=error / exit 2). Skip != pass."""
    monkeypatch.setenv("ZOE_PERF", "1")
    repo = tmp_path / "repo"
    _stub_replay_harness(repo / "services" / "zoe-data")   # get past the harness check
    monkeypatch.setattr(sd, "REPO", repo)
    monkeypatch.setattr(sd, "main_worktree_root", lambda: None)
    monkeypatch.setattr(sys, "argv", ["measure_voice.py"])
    assert mv.main() == 0
    err = capsys.readouterr().err
    assert "live service env required" in err
    assert str(repo / "services" / "zoe-data") in err


def test_measure_voice_explicit_service_dir_still_wins(tmp_path, monkeypatch, capsys):
    """An explicit flag is never rewritten to some other dir — an operator
    pointing at a wrong path must SEE that path in the error."""
    monkeypatch.setenv("ZOE_PERF", "1")
    _write_env(tmp_path / "main" / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: tmp_path / "main")
    explicit = tmp_path / "explicit"
    _stub_replay_harness(explicit)
    monkeypatch.setattr(sys, "argv", ["measure_voice.py", "--service-dir", str(explicit)])
    assert mv.main() == 0
    err = capsys.readouterr().err
    assert "live service env required" in err
    assert str(explicit) in err


def test_measure_tts_resolves_main_worktree_env_with_no_flag(tmp_path, monkeypatch, capsys):
    """measure_tts had the identical repo-root default. With the shared ladder a
    worktree run clears the .env gate via the MAIN checkout — proven by it
    reaching the next check (no reply source) instead of skipping."""
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sd, "REPO", tmp_path / "worktree")
    _write_env(tmp_path / "main" / "services" / "zoe-data", "postgresql://main/live")
    monkeypatch.setattr(sd, "main_worktree_root", lambda: tmp_path / "main")
    monkeypatch.setattr(mt, "_load_env", lambda _sd: None)   # don't leak the fake .env into os.environ
    monkeypatch.setattr(sys, "argv", ["measure_tts.py"])
    assert mt.main() == 2                                    # "provide --replies-file …"
    out = capsys.readouterr()
    assert "live service env required" not in out.err        # the .env gate was CLEARED
    assert "--replies-file" in out.err


def test_measure_tts_skips_loudly_when_no_env_resolves_anywhere(tmp_path, monkeypatch, capsys):
    """Same doctrine as measure_voice: no .env anywhere is still a loud skip."""
    monkeypatch.setenv("ZOE_PERF", "1")
    repo = tmp_path / "repo"
    monkeypatch.setattr(sd, "REPO", repo)
    monkeypatch.setattr(sd, "main_worktree_root", lambda: None)
    monkeypatch.setattr(sys, "argv", ["measure_tts.py", "--replies-file", "/dev/null"])
    assert mt.main() == 0
    err = capsys.readouterr().err
    assert "live service env required" in err
    assert str(repo / "services" / "zoe-data") in err
