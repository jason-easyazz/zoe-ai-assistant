"""B0.7 cutover — zoe-data moves from system Python 3.10 to the uv-built 3.12 venv.

What is pinned here, and why each is a behaviour rather than a string:

* **The drop-in** (`scripts/setup/systemd/zoe-data.service.d/60-py312-venv.conf`)
  changes the interpreter and nothing else: an empty `ExecStart=` reset (without
  it systemd refuses to load a Type=simple unit with two ExecStart lines —
  measured with `systemd-analyze verify`), then the template's exact uvicorn args
  behind the venv the build script creates.
* **The interpreter resolver** (`scripts/deploy/zoe_data_python.sh`) answers
  "what will the service exec?" from systemd, and fails closed on anything it
  cannot prove is a Python.
* **The deploy deps step** is EXECUTED here, from `deploy.yml` itself, against a
  stub systemd/uv/pip3: service on a venv → both install phases go into that venv
  (offline first); service on the system interpreter → the old `pip3 --user`
  list; unresolvable → the step fails before installing anything. The venv's
  mere EXISTENCE never selects it — it is built before the drop-in lands and
  survives a rollback.
* **`migrate.sh`** runs Alembic under the interpreter the deploy resolved.
* **Manifest parity**: every package exact-pinned in BOTH `requirements.txt` and
  `requirements-py312.txt` carries the same version, so the interpreter is the
  only variable in cut 1 (the moonshine 0.0.62 → 0.1.3 move on the box, #1714,
  is the drift this would have caught).

Stdlib + PyYAML + bash only; no network, no live host, no systemd.
"""
from __future__ import annotations

import configparser
import os
import re
import shlex
import stat
import subprocess
import sys
import venv
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SYSTEMD = REPO / "scripts" / "setup" / "systemd"
DROP_IN = SYSTEMD / "zoe-data.service.d" / "60-py312-venv.conf"
TEMPLATE = SYSTEMD / "zoe-data.service"
RESOLVER = REPO / "scripts" / "deploy" / "zoe_data_python.sh"
BUILD = REPO / "scripts" / "setup" / "build_py312_venv.sh"
MIGRATE = REPO / "scripts" / "deploy" / "migrate.sh"
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"
SELF_HOSTED = REPO / ".github" / "workflows" / "self-hosted-tests.yml"
REQ_310 = REPO / "services" / "zoe-data" / "requirements.txt"
REQ_312 = REPO / "services" / "zoe-data" / "requirements-py312.txt"
LIVE_ROOT = "/home/zoe/assistant"

# Divergence between the two manifests' exact pins is allowed ONLY here, each with
# its reason — the runbook's one-at-a-time 3.12 step-ups (websockets 17.1,
# onnxruntime 1.30, numpy 2, ...) land by adding an entry in their own PR.
STEP_UP_DIVERGENCE: dict[str, str] = {
    # B0.8 (2026-09-27): the live palace moved to chromadb 1.5.x WITH the py3.12 venv; the
    # 3.10 lane keeps the 0.6.3 pair and must never open it (format guard refuses).
    "chromadb": "B0.8 palace migration — docs/knowledge/chroma-1-5-migration.md",
    "mempalace": "B0.8 — 3.3.1's _fix_blob_seq_ids is unsafe on a 1.x palace",
    # 2026-09-27 age-waived batch (#1743): taken on the LIVE 3.12 lane only. The 3.10
    # lane is the hand-managed system site-packages, now rollback-only (the service
    # runs the 3.12 venv), and requirements.txt must describe what it actually has
    # (drift check) — nothing installs into it.
    "uvicorn": "3.12 lane 0.53.0 (ws sans-I/O, replay-gated); 3.10 rollback env holds 0.49.0",
    "ag-ui-protocol": "3.12 lane 1.0.0 (SSE byte-identical); 3.10 rollback env holds 0.1.19",
    "fastembed": "3.12 lane 0.8.1 (vectors bit-identical); 3.10 rollback env holds 0.8.0",
    "livekit-protocol": "3.12 lane 1.1.27; 3.10 rollback env holds 1.1.8",
}


# ── helpers ──────────────────────────────────────────────────────────────────


def _exec_lines(path: Path, section: str = "Service") -> list[str]:
    """Every ExecStart= value in *section*, in order (systemd allows repeats)."""
    values: list[str] = []
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            continue
        if current == section and line.split("=", 1)[0] == "ExecStart":
            values.append(line.split("=", 1)[1])
    return values


def _write_exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_systemctl(bindir: Path, exec_start_paths: list[str]) -> None:
    """A `systemctl` that answers `show -p ExecStart --value` like systemd does."""
    lines = "".join(
        f"echo '{{ path={p} ; argv[]={p} -m uvicorn main:app ; ignore_errors=no ; pid=0 }}'\n"
        for p in exec_start_paths
    )
    _write_exe(bindir / "systemctl", f"#!/usr/bin/env bash\n{lines}exit 0\n")


def _system_python() -> str:
    # Running the real binary (not a venv's symlink to it) is never a venv: no
    # pyvenv.cfg sits beside it. Holds whether or not pytest itself runs in one.
    return os.path.realpath(sys.executable)


@pytest.fixture()
def real_venv(tmp_path: Path) -> Path:
    d = tmp_path / "zoe-data-py3x"
    venv.create(d, with_pip=False, symlinks=True)
    return d


def _run_resolver(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "XDG_RUNTIME_DIR": str(tmp_path),
    }
    return subprocess.run(
        ["bash", str(RESOLVER), *args], capture_output=True, text=True, env=env, timeout=60
    )


# ── the drop-in ──────────────────────────────────────────────────────────────


def test_drop_in_resets_then_sets_exactly_one_execstart():
    lines = _exec_lines(DROP_IN)
    assert lines[0] == "", "the empty ExecStart= reset must come FIRST"
    assert len(lines) == 2, lines


def test_drop_in_keeps_the_template_args_and_only_swaps_the_interpreter():
    (template,) = _exec_lines(TEMPLATE)
    new = _exec_lines(DROP_IN)[1]
    t_interp, *t_args = shlex.split(template)
    n_interp, *n_args = shlex.split(new)
    assert t_interp == "/usr/bin/python3"  # the rollback target
    assert n_args == t_args, "the drop-in must mirror the template's uvicorn args exactly"
    assert n_interp == "%h/.zoe/venvs/zoe-data-py312/bin/python"


def test_drop_in_interpreter_is_the_venv_the_build_script_creates():
    m = re.search(r'VENV_DIR="\$\{ZOE_PY312_VENV:-\$HOME/([^}"]+)\}"', BUILD.read_text())
    assert m, "build_py312_venv.sh default VENV_DIR not found"
    n_interp = shlex.split(_exec_lines(DROP_IN)[1])[0]
    assert n_interp == f"%h/{m.group(1)}/bin/python"


def test_drop_in_changes_nothing_but_execstart():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # keep case
    cp.read_string(DROP_IN.read_text(encoding="utf-8"))
    assert cp.sections() == ["Service"]
    assert set(cp["Service"]) == {"ExecStart"}


def test_drop_in_is_not_matched_by_the_template_install_glob():
    # README installs with `cp scripts/setup/systemd/*.service` — the .d dir must
    # never be swept up as if it were a unit.
    assert DROP_IN.parent.name.endswith(".service.d")
    assert not list(SYSTEMD.glob("*.service/*"))


# ── the resolver ─────────────────────────────────────────────────────────────


def test_resolver_reports_a_venv_interpreter_and_its_prefix(tmp_path, real_venv):
    (tmp_path / "bin").mkdir()
    py = real_venv / "bin" / "python"
    _fake_systemctl(tmp_path / "bin", [str(py)])
    r = _run_resolver(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(py)
    r = _run_resolver(tmp_path, "--venv-dir")
    assert r.returncode == 0, r.stderr
    assert os.path.realpath(r.stdout.strip()) == os.path.realpath(real_venv)


def test_resolver_reports_no_venv_for_a_system_interpreter(tmp_path):
    (tmp_path / "bin").mkdir()
    _fake_systemctl(tmp_path / "bin", [_system_python()])
    r = _run_resolver(tmp_path, "--venv-dir")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "\n"


@pytest.mark.parametrize(
    "case",
    ["no-unit", "two-execstart", "not-executable", "not-python-silent", "not-python-chatty"],
)
def test_resolver_fails_closed(tmp_path, case):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if case == "no-unit":
        _fake_systemctl(bindir, [])
    elif case == "two-execstart":
        _fake_systemctl(bindir, [_system_python(), _system_python()])
    elif case == "not-executable":
        f = tmp_path / "python"
        f.write_text("#!/bin/sh\n")
        _fake_systemctl(bindir, [str(f)])
    elif case == "not-python-silent":
        _fake_systemctl(bindir, [str(_write_exe(tmp_path / "true", "#!/bin/sh\nexit 0\n"))])
    else:
        _fake_systemctl(bindir, [str(_write_exe(tmp_path / "chatty", "#!/bin/sh\necho hi\n"))])
    for args in ((), ("--venv-dir",)):
        r = _run_resolver(tmp_path, *args)
        assert r.returncode != 0, (case, args, r.stdout)
        assert r.stdout == "", (case, args, r.stdout)


# ── the deploy deps step, executed from deploy.yml ───────────────────────────


def _deploy_step(name: str) -> str:
    wf = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
    for step in wf["jobs"]["deploy"]["steps"]:
        if step.get("name") == name:
            return step["run"]
    raise AssertionError(f"deploy.yml has no step named {name!r}")


def _run_deps_step(tmp_path: Path, exec_start: list[str], *, uv_offline_fails: bool = False):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    _fake_systemctl(bindir, exec_start)
    _write_exe(bindir / "pip3", f'#!/usr/bin/env bash\necho "pip3 $*" >> {log}\n')
    offline_rule = (
        'case " $* " in *" --offline "*) echo "uv offline miss" >&2; exit 2;; esac\n'
        if uv_offline_fails
        else ""
    )
    uv = _write_exe(
        tmp_path / "uv",
        "#!/usr/bin/env bash\n"
        '[ "$1" = "--version" ] && { echo "uv 0.0.0-stub"; exit 0; }\n'
        f'echo "uv $*" >> {log}\n' + offline_rule,
    )
    gh_env = tmp_path / "github_env"
    gh_env.write_text("")
    script = _deploy_step("Install / refresh Python deps")
    assert f"cd {LIVE_ROOT}\n" in script
    script = script.replace(f"cd {LIVE_ROOT}\n", f"cd {shlex.quote(str(REPO))}\n")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "XDG_RUNTIME_DIR": str(tmp_path),
        "GITHUB_ENV": str(gh_env),
        "UV_BIN": str(uv),
        "ZOE_PY312_PYTHON": "%d.%d" % sys.version_info[:2],
    }
    r = subprocess.run(
        ["bash", "-e", "-c", script], capture_output=True, text=True, env=env, timeout=120
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return r, calls, gh_env.read_text()


def test_deploy_refreshes_the_venv_when_the_service_runs_it(tmp_path, real_venv):
    py = real_venv / "bin" / "python"
    r, calls, gh_env = _run_deps_step(tmp_path, [str(py)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [c for c in calls if c.startswith("pip3")], "3.10 --user install must not run"
    vpy = f"{real_venv}/bin/python"
    assert calls == [
        f"uv pip install --offline --python {vpy} -r {REQ_312}",
        f"uv pip install --offline --python {vpy} --no-deps resemblyzer==0.1.4",
    ], calls
    assert f"ZOE_DATA_PYTHON={py}\n" in gh_env


def test_deploy_falls_back_online_when_the_offline_pass_cannot_satisfy(tmp_path, real_venv):
    r, calls, _ = _run_deps_step(tmp_path, [str(real_venv / "bin" / "python")], uv_offline_fails=True)
    assert r.returncode == 0, r.stdout + r.stderr
    vpy = f"{real_venv}/bin/python"
    assert calls == [
        f"uv pip install --offline --python {vpy} -r {REQ_312}",
        f"uv pip install --python {vpy} -r {REQ_312}",
        f"uv pip install --offline --python {vpy} --no-deps resemblyzer==0.1.4",
        f"uv pip install --python {vpy} --no-deps resemblyzer==0.1.4",
    ], calls


def test_deploy_keeps_the_310_user_install_when_the_service_runs_system_python(tmp_path):
    # The venv EXISTS at its real default path (as it will between build and
    # cutover, and after a rollback) but the service does not run it — so it must
    # be ignored. A presence-keyed guard (`[ -x ~/.zoe/venvs/.../python ]`) fails here.
    default_venv = tmp_path / ".zoe" / "venvs" / "zoe-data-py312"
    venv.create(default_venv, with_pip=False, symlinks=True)
    assert (default_venv / "bin" / "python").exists()
    sys_py = _system_python()
    r, calls, gh_env = _run_deps_step(tmp_path, [sys_py])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [c for c in calls if c.startswith("uv")], calls
    (pip,) = calls
    assert pip.startswith("pip3 install --quiet --user ") and "apscheduler==3.10.4" in pip
    assert f"ZOE_DATA_PYTHON={sys_py}\n" in gh_env


def test_deploy_refuses_when_the_service_interpreter_is_unresolvable(tmp_path):
    r, calls, gh_env = _run_deps_step(tmp_path, [])
    assert r.returncode != 0
    assert calls == [] and gh_env == ""


def test_refresh_never_creates_a_venv(tmp_path):
    # A service pointed at a venv that is gone is an operator problem; the deploy
    # must fail, not quietly build 1.9 GB.
    uv = _write_exe(tmp_path / "uv", '#!/usr/bin/env bash\necho "uv $*" >> "$0.log"\n')
    r = subprocess.run(
        ["bash", str(BUILD), "--refresh"],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "UV_BIN": str(uv),
             "ZOE_PY312_VENV": str(tmp_path / "absent")},
    )
    assert r.returncode != 0
    calls = Path(f"{uv}.log").read_text().splitlines() if Path(f"{uv}.log").exists() else []
    assert [c for c in calls if "install" in c or "venv" in c] == [], calls


def test_migrate_step_hands_the_resolved_interpreter_to_migrate_sh():
    script = _deploy_step("Apply database migrations")
    assert 'ZOE_DATA_PYTHON="${ZOE_DATA_PYTHON}" bash scripts/deploy/migrate.sh' in script
    assert '[ -n "${ZOE_DATA_PYTHON:-}" ] ||' in script  # refuses an unset interpreter


def test_migrate_sh_runs_alembic_under_zoe_data_python(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    fake_py = _write_exe(tmp_path / "svc-python", f'#!/usr/bin/env bash\necho "$PWD|$*" >> {log}\n')
    _write_exe(bindir / "psql", f'#!/usr/bin/env bash\necho "psql" >> {log}\n')
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "POSTGRES_URL": "postgresql://u:p@localhost:5432/zoe",
        "ZOE_DATA_PYTHON": str(fake_py),
    }
    r = subprocess.run(["bash", str(MIGRATE)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text().splitlines()
    assert f"{REPO}/services/zoe-data|-m alembic upgrade head" in calls, calls


def test_self_hosted_drift_check_follows_the_service_interpreter():
    wf = yaml.safe_load(SELF_HOSTED.read_text(encoding="utf-8"))
    runs = [
        s.get("run", "")
        for job in wf["jobs"].values()
        for s in job.get("steps", [])
        if str(s.get("name", "")).startswith("Dependency drift")
    ]
    assert len(runs) == 1
    assert "zoe_data_python.sh --venv-dir" in runs[0]
    assert "requirements-py312.txt" in runs[0] and "requirements.txt" in runs[0]


# ── manifest parity ──────────────────────────────────────────────────────────


def _exact_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;]+)", line)
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(3)
    return pins


def _diverged(req_310: Path, req_312: Path) -> dict[str, tuple[str, str]]:
    a, b = _exact_pins(req_310), _exact_pins(req_312)
    return {
        k: (a[k], b[k])
        for k in set(a) & set(b)
        if a[k] != b[k] and k not in STEP_UP_DIVERGENCE
    }


def test_exact_pins_shared_by_both_manifests_agree():
    shared = set(_exact_pins(REQ_310)) & set(_exact_pins(REQ_312))
    # Guard the instrument: the parser must actually see the load-bearing pins.
    assert {"moonshine-voice", "onnxruntime", "fastapi", "chromadb"} <= shared, shared
    diverged = _diverged(REQ_310, REQ_312)
    assert not diverged, (
        f"requirements.txt vs requirements-py312.txt disagree: {diverged} — move both, "
        "or record a deliberate 3.12 step-up in STEP_UP_DIVERGENCE"
    )


def test_step_up_divergence_is_exactly_the_diverged_set():
    """Every recorded step-up must REALLY diverge — a stale entry would silently
    exempt a package whose manifests later drift apart for no reason."""
    a, b = _exact_pins(REQ_310), _exact_pins(REQ_312)
    really = {k for k in set(a) & set(b) if a[k] != b[k]}
    assert set(STEP_UP_DIVERGENCE) == really, (
        f"STEP_UP_DIVERGENCE {sorted(STEP_UP_DIVERGENCE)} != diverged pins {sorted(really)}"
    )


def test_parity_check_catches_a_diverged_pin(tmp_path):
    # Negative control: the pre-#1714 state (box on 0.1.3, 3.12 manifest on 0.0.62).
    stale = tmp_path / "requirements-py312.txt"
    text = REQ_312.read_text(encoding="utf-8")
    assert "moonshine-voice==0.1.3" in text
    stale.write_text(text.replace("moonshine-voice==0.1.3", "moonshine-voice==0.0.62"))
    assert _diverged(REQ_310, stale) == {"moonshine-voice": ("0.1.3", "0.0.62")}
