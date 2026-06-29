import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "deploy_zoe_data_when_ready.sh"


def _run(cmd, cwd=None, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    proc = _run(["git", "-C", str(repo), *args])
    assert proc.returncode == 0, proc.stderr
    return proc


def _seed_live_tree(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    live = tmp_path / "live"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(live)], check=True, capture_output=True)
    _git(live, "checkout", "-b", "main")
    _git(live, "config", "user.email", "test@example.com")
    _git(live, "config", "user.name", "Test User")

    voice = live / "services" / "zoe-data" / "routers" / "voice_tts.py"
    main = live / "services" / "zoe-data" / "main.py"
    voice.parent.mkdir(parents=True)
    voice.write_text("def _prewarm_stt_on_wake():\n    pass\n")
    main.write_text("def startup():\n    warm_moonshine()\n")
    (live / "README.md").write_text("fake live tree\n")
    _git(live, "add", ".")
    _git(live, "commit", "-m", "init")
    _git(live, "push", "-u", "origin", "main")
    return live


def _env(live: Path, extra=None):
    env = {
        **os.environ,
        "ZOE_LIVE_TREE": str(live),
        "ZOE_DEPLOY_MIN_AVAIL_MB": "0",
        "ZOE_PORT": "18000",
    }
    if extra:
        env.update(extra)
    return env


def test_check_fails_on_feature_branch(tmp_path):
    live = _seed_live_tree(tmp_path)
    _git(live, "checkout", "-b", "feature/test")

    proc = _run([str(SCRIPT), "--check"], env=_env(live))

    assert proc.returncode != 0
    assert "FAIL branch" in proc.stdout
    assert "NOT-READY" in proc.stdout


def test_check_fails_on_dirty_tree(tmp_path):
    live = _seed_live_tree(tmp_path)
    (live / "DIRTY.txt").write_text("uncommitted\n")

    proc = _run([str(SCRIPT), "--check"], env=_env(live))

    assert proc.returncode != 0
    assert "FAIL clean-tree" in proc.stdout
    assert "NOT-READY" in proc.stdout


def test_check_passes_on_clean_main_with_low_memory_gate_disabled(tmp_path):
    live = _seed_live_tree(tmp_path)

    proc = _run([str(SCRIPT), "--check"], env=_env(live))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS git-repo" in proc.stdout
    assert "PASS branch" in proc.stdout
    assert "PASS clean-tree" in proc.stdout
    assert "PASS memory" in proc.stdout
    assert "READY" in proc.stdout


def test_deploy_without_confirmation_refuses_and_does_not_invoke_deploy_live(tmp_path):
    live = _seed_live_tree(tmp_path)
    bundle = tmp_path / "bundle" / "scripts" / "maintenance"
    bundle.mkdir(parents=True)
    wrapper = bundle / "deploy_zoe_data_when_ready.sh"
    deploy_live = bundle / "deploy_live.sh"
    marker = tmp_path / "deploy-called"
    shutil.copy2(SCRIPT, wrapper)
    wrapper.chmod(0o755)
    deploy_live.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\ntouch {marker}\n")
    deploy_live.chmod(0o755)

    proc = _run([str(wrapper), "--deploy"], env=_env(live))

    assert proc.returncode != 0
    assert "REFUSING" in proc.stdout
    assert "--yes-restart-production" in proc.stdout
    assert not marker.exists()


def test_deploy_with_confirmation_invokes_deploy_live_stub_and_post_verify(tmp_path):
    live = _seed_live_tree(tmp_path)
    bundle = tmp_path / "bundle" / "scripts" / "maintenance"
    fake_bin = tmp_path / "bin"
    bundle.mkdir(parents=True)
    fake_bin.mkdir()
    wrapper = bundle / "deploy_zoe_data_when_ready.sh"
    deploy_live = bundle / "deploy_live.sh"
    marker = tmp_path / "deploy-called"
    shutil.copy2(SCRIPT, wrapper)
    wrapper.chmod(0o755)
    deploy_live.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\ntouch {marker}\n")
    deploy_live.chmod(0o755)
    (fake_bin / "curl").write_text("#!/usr/bin/env bash\nprintf '200'\n")
    (fake_bin / "curl").chmod(0o755)

    proc = _run(
        [str(wrapper), "--deploy", "--yes-restart-production"],
        env=_env(live, {"PATH": f"{fake_bin}:{os.environ['PATH']}"}),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()
    assert "POST-DEPLOY VERIFY PASS" in proc.stdout
