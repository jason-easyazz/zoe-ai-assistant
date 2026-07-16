import pytest
import os
import shlex
import shutil
import subprocess
from pathlib import Path

pytestmark = pytest.mark.ci_safe


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
    main.write_text(
        "import asyncio\n\n"
        "async def startup():\n"
        "    asyncio.create_task(warm_moonshine(), name=\"moonshine_warmup\")\n"
    )
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


def _copy_wrapper_bundle(tmp_path: Path, deploy_live_body: str | None = None):
    bundle = tmp_path / "bundle" / "scripts" / "maintenance"
    bundle.mkdir(parents=True)
    wrapper = bundle / "deploy_zoe_data_when_ready.sh"
    deploy_live = bundle / "deploy_live.sh"
    marker = tmp_path / "deploy-called"
    shutil.copy2(SCRIPT, wrapper)
    wrapper.chmod(0o755)
    deploy_live.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"{deploy_live_body if deploy_live_body is not None else f'touch {marker}'}\n"
    )
    deploy_live.chmod(0o755)
    return wrapper, deploy_live, marker


def _sh(path: Path) -> str:
    return shlex.quote(str(path))


def _install_url_aware_curl_shim(tmp_path: Path, expected_url: str):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    curl_log = tmp_path / "curl-argv.log"
    (fake_bin / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$*\" >> {curl_log}\n"
        "url=''\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in http://*) url=\"$arg\" ;; esac\n"
        "done\n"
        f"if [[ \"$url\" == {expected_url!r} ]]; then\n"
        "  printf '200'\n"
        "else\n"
        "  printf '503'\n"
        "fi\n"
    )
    (fake_bin / "curl").chmod(0o755)
    return fake_bin, curl_log


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
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(tmp_path)

    proc = _run([str(wrapper), "--deploy"], env=_env(live))

    assert proc.returncode != 0
    assert "REFUSING" in proc.stdout
    assert "--yes-restart-production" in proc.stdout
    assert not marker.exists()


def test_deploy_with_confirmation_invokes_deploy_live_stub_and_post_verify(tmp_path):
    live = _seed_live_tree(tmp_path)
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(tmp_path)
    expected_url = "http://127.0.0.1:18000/health"
    fake_bin, curl_log = _install_url_aware_curl_shim(tmp_path, expected_url)

    proc = _run(
        [str(wrapper), "--deploy", "--yes-restart-production"],
        env=_env(live, {"PATH": f"{fake_bin}:{os.environ['PATH']}"}),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()
    assert "PRE-FLIGHT TARGET CONTENT PASS" in proc.stdout
    assert "PASS post-health" in proc.stdout
    assert expected_url in curl_log.read_text()


def test_confirmed_deploy_pins_origin_main_when_real_origin_drifts(tmp_path):
    live = _seed_live_tree(tmp_path)
    (live / "README.md").write_text("preflighted origin update\n")
    _git(live, "add", "README.md")
    _git(live, "commit", "-m", "preflighted update")
    _git(live, "push", "origin", "main")
    _git(live, "reset", "--hard", "HEAD~1")

    drift_work = tmp_path / "drift-work"
    remote_url = _git(live, "config", "--get", "remote.origin.url").stdout.strip()
    subprocess.run(["git", "clone", remote_url, str(drift_work)], check=True, capture_output=True)
    _git(drift_work, "checkout", "main")
    _git(drift_work, "config", "user.email", "test@example.com")
    _git(drift_work, "config", "user.name", "Test User")

    marker_path = tmp_path / "deploy-called"
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(
        tmp_path,
        deploy_live_body=(
            f"printf 'drifted origin update\\n' > {_sh(drift_work / 'README.md')}\n"
            f"git -C {_sh(drift_work)} add README.md\n"
            f"git -C {_sh(drift_work)} commit -m 'drift origin main'\n"
            f"env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -C {_sh(drift_work)} push {_sh(Path(remote_url))} main\n"
            "git -C \"$ZOE_LIVE_TREE\" fetch origin main\n"
            "git -C \"$ZOE_LIVE_TREE\" reset --hard refs/remotes/origin/main\n"
            f"touch {_sh(marker_path)}"
        ),
    )

    proc = _run([str(wrapper), "--deploy", "--yes-restart-production"], env=_env(live))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()
    assert "PRE-FLIGHT TARGET CONTENT PASS" in proc.stdout
    assert "PASS post-target-sha" in proc.stdout
    assert (live / "README.md").read_text() == "preflighted origin update\n"


def test_confirmed_deploy_aborts_when_deploy_live_ships_unverified_sha(tmp_path):
    live = _seed_live_tree(tmp_path)
    (live / "README.md").write_text("preflighted origin update\n")
    _git(live, "add", "README.md")
    _git(live, "commit", "-m", "preflighted update")
    _git(live, "push", "origin", "main")
    _git(live, "reset", "--hard", "HEAD~1")

    drift_work = tmp_path / "drift-work"
    remote_url = _git(live, "config", "--get", "remote.origin.url").stdout.strip()
    subprocess.run(["git", "clone", remote_url, str(drift_work)], check=True, capture_output=True)
    _git(drift_work, "checkout", "main")
    _git(drift_work, "config", "user.email", "test@example.com")
    _git(drift_work, "config", "user.name", "Test User")

    marker_path = tmp_path / "deploy-called"
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(
        tmp_path,
        deploy_live_body=(
            f"printf 'drifted origin update\\n' > {_sh(drift_work / 'README.md')}\n"
            f"git -C {_sh(drift_work)} add README.md\n"
            f"git -C {_sh(drift_work)} commit -m 'drift origin main'\n"
            f"env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -C {_sh(drift_work)} push {_sh(Path(remote_url))} main\n"
            f"env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -C \"$ZOE_LIVE_TREE\" fetch {_sh(Path(remote_url))} main\n"
            "git -C \"$ZOE_LIVE_TREE\" reset --hard FETCH_HEAD\n"
            f"touch {_sh(marker_path)}"
        ),
    )

    proc = _run([str(wrapper), "--deploy", "--yes-restart-production"], env=_env(live))

    assert proc.returncode != 0
    assert marker.exists()
    assert "PRE-FLIGHT TARGET CONTENT PASS" in proc.stdout
    assert "FAIL post-target-sha" in proc.stdout
    assert "does not match pre-flighted target" in proc.stdout


def test_confirmed_deploy_preflight_allows_faster_whisper_docstring(tmp_path):
    live = _seed_live_tree(tmp_path)
    main = live / "services" / "zoe-data" / "main.py"
    main.write_text(
        '"""Binary -> transcribed via faster-whisper then routed as text."""\n'
        "import asyncio\n\n"
        "async def startup():\n"
        "    asyncio.create_task(warm_moonshine(), name=\"moonshine_warmup\")\n"
    )
    _git(live, "add", "services/zoe-data/main.py")
    _git(live, "commit", "-m", "docstring mentions faster-whisper")
    _git(live, "push", "origin", "main")
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(tmp_path)
    expected_url = "http://127.0.0.1:18000/health"
    fake_bin, curl_log = _install_url_aware_curl_shim(tmp_path, expected_url)

    proc = _run(
        [str(wrapper), "--deploy", "--yes-restart-production"],
        env=_env(live, {"PATH": f"{fake_bin}:{os.environ['PATH']}"}),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()
    assert "PASS target-main" in proc.stdout
    assert "PASS target-main-whisper" in proc.stdout
    assert "PRE-FLIGHT TARGET CONTENT PASS" in proc.stdout
    assert expected_url in curl_log.read_text()


def test_confirmed_deploy_with_failed_gates_aborts_before_deploy_live(tmp_path):
    live = _seed_live_tree(tmp_path)
    _git(live, "checkout", "-b", "feature/not-ready")
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(tmp_path)

    proc = _run([str(wrapper), "--deploy", "--yes-restart-production"], env=_env(live))

    assert proc.returncode != 0
    assert "deploy aborted: gates are NOT-READY" in proc.stdout
    assert not marker.exists()


def test_confirmed_deploy_with_bad_target_content_aborts_before_deploy_live(tmp_path):
    live = _seed_live_tree(tmp_path)
    voice = live / "services" / "zoe-data" / "routers" / "voice_tts.py"
    voice.write_text("def other_warmup():\n    pass\n")
    _git(live, "add", "services/zoe-data/routers/voice_tts.py")
    _git(live, "commit", "-m", "remove target marker")
    _git(live, "push", "origin", "main")
    _git(live, "reset", "--hard", "HEAD~1")
    wrapper, _deploy_live, marker = _copy_wrapper_bundle(tmp_path)

    proc = _run([str(wrapper), "--deploy", "--yes-restart-production"], env=_env(live))

    assert proc.returncode != 0
    assert "PRE-FLIGHT TARGET CONTENT FAIL" in proc.stdout
    assert "deploy aborted: target commit content is NOT-READY" in proc.stdout
    assert not marker.exists()


def test_yes_restart_without_deploy_is_usage_error(tmp_path):
    live = _seed_live_tree(tmp_path)

    proc = _run([str(SCRIPT), "--yes-restart-production"], env=_env(live))

    assert proc.returncode == 2
    assert "--yes-restart-production is only valid with --deploy" in proc.stderr
