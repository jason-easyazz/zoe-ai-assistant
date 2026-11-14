"""B4 (docs/architecture/p0-deroot.md section 8): the agent-orchestrator exec helper and the callers' routing.

  - libexec/zoe-omnigent-exec passes exactly `docker exec -u 1000:1000 <validated flags> zoe-omnigent <argv>` and
    refuses every other container, user, flag and program before Docker is called (fake docker on PATH);
  - cross_review.sh / cross_review_polly.sh / omnigent_claude_auth_mode.py / zoe-data's omnigent_issue_executor.py
    use the helper only when it is installed AND sudo allows it; otherwise the argv is byte-identical to the
    pre-P0-2 `docker exec ...` (the dry run below is the proof for today's box, where the helper is absent)."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts/setup/p0-deroot/libexec/zoe-omnigent-exec"
CROSS = REPO / "scripts/maintenance/cross_review.sh"
POLLY = REPO / "scripts/maintenance/cross_review_polly.sh"


@pytest.fixture
def fake_helper(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    (bindir / "docker").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\n')
    (bindir / "docker").chmod(0o755)
    text = re.sub(r"^PATH=.*$", f"PATH={bindir}:/usr/bin:/bin", HELPER.read_text(), count=1, flags=re.M)
    h = tmp_path / "zoe-omnigent-exec"
    h.write_text(text)

    def call(*argv):
        if log.exists():
            log.unlink()
        r = subprocess.run(["sh", str(h), *argv], capture_output=True, text=True)
        return r.returncode, (log.read_text().split("\n")[:-1] if log.exists() else None)
    return call


def test_helper_allowed_calls_pin_user_and_container(fake_helper):
    rc, argv = fake_helper("-i", "-w", "/tmp/review-1", "zoe-omnigent", "timeout", "-k", "5", "10", "codex")
    assert rc == 0 and argv == ["exec", "-u", "1000:1000", "-i", "-w", "/tmp/review-1", "zoe-omnigent",
                                "timeout", "-k", "5", "10", "codex"]
    rc, argv = fake_helper("-d", "zoe-omnigent", "sh", "-c", "cd / && echo 'a b'", "sh", "")
    assert rc == 0 and argv == ["exec", "-u", "1000:1000", "-d", "zoe-omnigent", "sh", "-c",
                                "cd / && echo 'a b'", "sh", ""]


@pytest.mark.parametrize("argv", [
    ["homeassistant", "sh"], ["-u", "0", "zoe-omnigent", "sh"], ["--user=root", "zoe-omnigent", "sh"],
    ["--privileged", "zoe-omnigent", "sh"], ["-e", "A=1", "zoe-omnigent", "sh"], ["-t", "zoe-omnigent", "sh"],
    ["-w", "rel", "zoe-omnigent", "sh"], ["-w", "/tmp/../root", "zoe-omnigent", "sh"], ["-w"],
    ["zoe-omnigent", "bash"], ["zoe-omnigent", "/bin/sh"], ["zoe-omnigent"], []])
def test_helper_refuses_before_docker(fake_helper, argv):
    rc, called = fake_helper(*argv)
    assert rc == 2 and called is None


def _prefix_fn(script: Path) -> str:
    m = re.search(r"^omnigent_exec_prefix\(\) \{\n.*?^\}\n", script.read_text(), re.M | re.S)
    assert m, f"{script.name}: omnigent_exec_prefix() not found"
    return m.group(0)


def test_both_shell_callers_carry_the_same_routing():
    assert _prefix_fn(CROSS) == _prefix_fn(POLLY)
    for s in (CROSS, POLLY):  # every real exec goes through the prefix; only comments and echoed hints remain
        code = [ln.split(" #", 1)[0] for ln in s.read_text().splitlines() if not ln.lstrip().startswith("#")]
        left = [ln for ln in code if "docker exec" in ln
                and "echo" not in ln and "OMNI_EXEC=(docker exec)" not in ln]
        assert left == [], (s.name, left)


def _route(tmp_path, helper_exists: bool, sudo_rc: int | None) -> str:
    """Dry-run of cross_review.sh's routing + its in_container() with today's variables; prints CMD."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if sudo_rc is not None:
        (bindir / "sudo").write_text(f"#!/bin/sh\nexit {sudo_rc}\n")
        (bindir / "sudo").chmod(0o755)
    helper = tmp_path / "zoe-omnigent-exec"
    if helper_exists:
        helper.write_text("#!/bin/sh\n")
        helper.chmod(0o755)
    text = CROSS.read_text()
    in_container = re.search(r"^IN_CONTAINER_MARGIN_S=.*?^\}\n", text, re.M | re.S).group(0)
    prog = (f'CONTAINER=zoe-omnigent; REVIEW_DIR=/tmp/review-7; CONTAINER_RUN=""\n{_prefix_fn(CROSS)}'
            f'omnigent_exec_prefix\n{in_container}in_container "" codex 30\nprintf "%s|" "${{CMD[@]}}"\n')
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin", "OMNIGENT_EXEC_HELPER": str(helper)}
    r = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_dry_run_today_is_byte_identical_docker_exec(tmp_path):
    # Today's box: the helper is not installed (sudo is never even asked).
    assert _route(tmp_path, helper_exists=False, sudo_rc=0) == \
        "docker|exec|-i|-w|/tmp/review-7|zoe-omnigent|timeout|-k|5|27|codex|"


def test_dry_run_installed_and_allowed_uses_the_helper(tmp_path):
    out = _route(tmp_path, helper_exists=True, sudo_rc=0)
    assert out == f"sudo|-n|{tmp_path}/zoe-omnigent-exec|-i|-w|/tmp/review-7|zoe-omnigent|timeout|-k|5|27|codex|"


def test_dry_run_installed_but_not_allowed_falls_back(tmp_path):
    assert _route(tmp_path, helper_exists=True, sudo_rc=1).startswith("docker|exec|-i|")


def _load_auth_mode():
    spec = importlib.util.spec_from_file_location("auth_mode", REPO / "scripts/maintenance/omnigent_claude_auth_mode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_python_prefix_routing(tmp_path, monkeypatch):
    mod = _load_auth_mode()
    assert mod._docker_exec_prefix(str(tmp_path / "absent")) == ["docker", "exec"]
    h = tmp_path / "h"
    h.write_text("#!/bin/sh\n")
    h.chmod(0o755)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    assert mod._docker_exec_prefix(str(h)) == ["sudo", "-n", str(h)]
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    assert mod._docker_exec_prefix(str(h)) == ["docker", "exec"]


def test_zoe_data_executor_uses_the_same_routing():
    src = (REPO / "services/zoe-data/omnigent_issue_executor.py").read_text()
    auth = (REPO / "scripts/maintenance/omnigent_claude_auth_mode.py").read_text()
    fn = re.compile(r"^def _docker_exec_prefix\(.*?^    return \[\"docker\", \"exec\"\]\n", re.M | re.S)
    assert fn.search(src).group(0) == fn.search(auth).group(0)
    assert '"docker", "exec"' not in src.replace('return ["docker", "exec"]', "")
    assert src.count("*_docker_exec_prefix()") == 2
