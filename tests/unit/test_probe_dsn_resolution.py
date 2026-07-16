"""Pin the DSN resolution for the voice probe's replay-artifact sweep.

The documented agent workflow runs scripts/maintenance/voice_regression_probe.py
from a git WORKTREE, which has NO gitignored services/zoe-data/.env of its own.
The cleanup sweep must still resolve the Postgres DSN — otherwise the replay
corpus's REAL command writes (events / list_items) are left in the live DB
(operator bug report during PR #1354's baseline refresh).

These tests pin the resolution precedence of `_resolve_dsn`:
  1. explicit POSTGRES_URL in the env,
  2. --service-dir/.env  (the fix: works from a worktree pointed at the live dir),
  3. REPO/services/zoe-data/.env  (last-ditch in-tree fallback),
  4. genuinely unresolvable -> "" (caller must fail loudly).

Pure resolution logic only — NO live DB and NO asyncpg connection.
"""
from __future__ import annotations

import importlib.util
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
    args = SimpleNamespace(service_dir=str(tmp_path / "also-nope"))
    assert vrp._resolve_dsn(args) == ""
