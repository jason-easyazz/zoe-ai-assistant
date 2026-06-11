import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "refresh_graphify.sh"


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)


def _make_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    root = tmp_path / "repo"
    _run(["git", "init", "--bare", str(origin)])
    _run(["git", "clone", str(origin), str(root)])
    _run(["git", "checkout", "-b", "main"], cwd=root)
    _run(["git", "config", "user.email", "zoe@example.test"], cwd=root)
    _run(["git", "config", "user.name", "Zoe Test"], cwd=root)
    (root / "graphify-out").mkdir()
    (root / "graphify-out" / "GRAPH_REPORT.md").write_text("Built from commit: `00000000`\nold snapshot\n", encoding="utf-8")
    (root / "graphify-out" / "graph.json").write_text("{}\n", encoding="utf-8")
    (root / "tracked.py").write_text("print('hello')\n", encoding="utf-8")
    _run(["git", "add", "."], cwd=root)
    _run(["git", "commit", "-m", "seed"], cwd=root)
    _run(["git", "push", "-u", "origin", "main"], cwd=root)
    return root


def _fake_graphify(path: Path) -> Path:
    fake = path / "graphify"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s|backend=%s|model=%s|key=%s\n' "$*" "${GRAPHIFY_BACKEND:-}" "${GRAPHIFY_OPENROUTER_MODEL:-}" "${OPENROUTER_API_KEY:-}" >>"$GRAPHIFY_CALL_LOG"
if [[ "${GRAPHIFY_FAKE_FAILURE_TEXT:-}" == "1" ]]; then
  echo 'provider error: insufficient_quota'
fi
if [[ "$1" == "extract" ]]; then
  mkdir -p graphify-out/cache
  printf '{"source":"%s"}\n' "$PWD" > graphify-out/graph.json
  printf '# Graph Report\n\nBuilt from commit: `%s`\nsource=%s\n' "$(git rev-parse --short=8 HEAD)" "$PWD" > graphify-out/GRAPH_REPORT.md
elif [[ "$1" == "cluster-only" ]]; then
  :
else
  echo "unexpected graphify command: $*" >&2
  exit 2
fi
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _base_env(tmp_path: Path, root: Path, fake: Path, call_log: Path) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / ".env").write_text("OPENROUTER_API_KEY=hermes-openrouter-key\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "TMPDIR": str(tmp_path / "tmp"),
            "ZOE_ASSISTANT_ROOT": str(root),
            "GRAPHIFY_BIN": str(fake),
            "GRAPHIFY_CALL_LOG": str(call_log),
        }
    )
    Path(env["TMPDIR"]).mkdir()
    env.pop("OPENAI_API_KEY", None)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("GRAPHIFY_BACKEND", None)
    env.pop("GRAPHIFY_MODEL", None)
    env.pop("GRAPHIFY_OPENROUTER_MODEL", None)
    return env


def test_refresh_graphify_prefers_openrouter_and_normalizes_snapshot_paths(tmp_path):
    root = _make_repo(tmp_path)
    fake = _fake_graphify(tmp_path)
    call_log = tmp_path / "calls.log"
    env = _base_env(tmp_path, root, fake, call_log)

    completed = subprocess.run([str(SCRIPT), "--force"], cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    assert completed.returncode == 0, completed.stdout
    calls = call_log.read_text(encoding="utf-8")
    assert "extract . --backend openrouter|backend=openrouter|model=openai/gpt-4.1-mini|key=hermes-openrouter-key" in calls
    report = (root / "graphify-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8")
    graph = (root / "graphify-out" / "graph.json").read_text(encoding="utf-8")
    assert str(root) in report
    assert str(root) in graph
    assert "/zoe-graphify-snapshot." not in report
    assert not (root / "graphify-out" / ".last_refresh_error").exists()


def test_refresh_graphify_blocks_provider_failure_text_before_sync(tmp_path):
    root = _make_repo(tmp_path)
    fake = _fake_graphify(tmp_path)
    call_log = tmp_path / "calls.log"
    env = _base_env(tmp_path, root, fake, call_log)
    env["GRAPHIFY_FAKE_FAILURE_TEXT"] = "1"
    before = (root / "graphify-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8")

    completed = subprocess.run([str(SCRIPT), "--force"], cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    assert completed.returncode == 1
    assert "quota/auth/provider failure" in completed.stdout
    assert (root / "graphify-out" / "GRAPH_REPORT.md").read_text(encoding="utf-8") == before
    marker = root / "graphify-out" / ".last_refresh_error"
    assert marker.exists()
    assert "quota/auth/provider failure" in marker.read_text(encoding="utf-8")
