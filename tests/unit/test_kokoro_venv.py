"""Kokoro dedicated venv (B5.7) — build script, import blocker, systemd drop-in.

What is pinned, as behaviour:

* **The blocker** (`scripts/setup/kokoro_import_block.py`) makes each blocked
  package answer "not installed" to BOTH probes transformers uses — `import`
  raises ModuleNotFoundError and `importlib.util.find_spec` returns None — and it
  acts only as its INSTALLED name, so importing the repo copy is inert.
* **The build script** (`scripts/setup/build_kokoro_venv.sh`) creates a real
  `--system-site-packages` venv on the running interpreter, installs the blocker
  as a `.pth`, is idempotent, refuses a foreign directory or a venv that cannot
  see the system site-packages, and `--check` goes red when the blocker is
  tampered with. Run against a REAL venv (no pip, no network, no torch — the
  drift check + import smoke are the live box's job and are skipped here).
* **The drop-in** changes the interpreter and nothing else, and points at the venv
  the build script creates.
* **The manifest** is exact-pinned and never lists a blocked package.

Stdlib + bash only; no network, no systemd, no GPU.
"""
from __future__ import annotations

import configparser
import importlib.util
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "scripts" / "setup"
BUILD = SETUP / "build_kokoro_venv.sh"
BLOCK = SETUP / "kokoro_import_block.py"
REQ = SETUP / "requirements-kokoro.txt"
SYSTEMD = SETUP / "systemd"
TEMPLATE = SYSTEMD / "kokoro-tts.service"
DROP_IN = SYSTEMD / "kokoro-tts.service.d" / "60-kokoro-venv.conf"


def _load_block():
    spec = importlib.util.spec_from_file_location("kokoro_import_block", BLOCK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_lines(path: Path) -> list[str]:
    values, current = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = line.strip("[]")
        elif current == "Service" and line.split("=", 1)[0] == "ExecStart":
            values.append(line.split("=", 1)[1])
    return values


# ── the blocker ──────────────────────────────────────────────────────────────


def test_blocker_marks_each_package_unimportable():
    mod = _load_block()
    modules: dict[str, object] = {}
    assert mod.install(modules, environ={}) == list(mod.BLOCKED)
    assert modules == {name: None for name in mod.BLOCKED}


def test_blocker_leaves_an_already_imported_package_alone():
    mod = _load_block()
    live = object()
    modules = {"pandas": live}
    mod.install(modules, environ={})
    assert modules["pandas"] is live


def test_blocker_can_be_disabled_for_one_run():
    mod = _load_block()
    modules: dict[str, object] = {}
    assert mod.install(modules, environ={"ZOE_KOKORO_IMPORT_BLOCK": "0"}) == []
    assert modules == {}


def test_blocker_is_exactly_the_chain_transformers_drags_in():
    assert _load_block().BLOCKED == ("sklearn", "pandas", "pyarrow")


def test_importing_the_repo_copy_is_inert():
    before = {n: sys.modules.get(n, "absent") for n in _load_block().BLOCKED}
    _load_block()
    assert {n: sys.modules.get(n, "absent") for n in before} == before


# ── the build script, against a real venv ────────────────────────────────────


def _build(tmp_path: Path, *args: str, venv_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "ZOE_KOKORO_VENV": str(venv_dir or tmp_path / "kokoro-venv"),
        "ZOE_KOKORO_BASE_PYTHON": os.path.realpath(sys.executable),
        "ZOE_KOKORO_PY_MM": "%d.%d" % sys.version_info[:2],
        "ZOE_KOKORO_VENV_SKIP_RUNTIME": "1",
    }
    return subprocess.run(["bash", str(BUILD), *args], capture_output=True, text=True,
                          env=env, timeout=120)


def _venv_probe(venv_dir: Path, code: str, **env: str) -> str:
    out = subprocess.run([str(venv_dir / "bin" / "python"), "-c", code], capture_output=True,
                         text=True, timeout=60, env={**os.environ, **env}, check=True)
    return out.stdout.strip()


PROBE = ("import sys, importlib.util as u; "
         "print(repr(sys.modules.get('pandas', 'absent')), u.find_spec('pandas'))")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("kokoro")
    r = _build(tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp / "kokoro-venv"


def test_build_creates_a_system_site_packages_venv(built):
    cfg = (built / "pyvenv.cfg").read_text()
    assert re.search(r"^include-system-site-packages\s*=\s*true$", cfg, re.M), cfg


def test_blocker_is_live_in_the_built_venv(built):
    # Both probes transformers uses say "not installed", whether or not the
    # package exists on this host.
    assert _venv_probe(built, PROBE) == "None None"


def test_negative_control_disabled_blocker_leaves_imports_alone(built):
    assert _venv_probe(built, PROBE, ZOE_KOKORO_IMPORT_BLOCK="0").startswith("'absent'")


def test_build_is_idempotent(built, tmp_path):
    site = next(built.glob("lib/python*/site-packages"))
    before = {p.name: p.stat().st_mtime_ns for p in site.glob("zoe_kokoro_import_block.*")}
    r = _build(tmp_path, venv_dir=built)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already current" in r.stdout
    assert {p.name: p.stat().st_mtime_ns for p in site.glob("zoe_kokoro_import_block.*")} == before


def test_check_passes_on_a_built_venv(built, tmp_path):
    r = _build(tmp_path, "--check", venv_dir=built)
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_fails_when_the_blocker_is_tampered_with(tmp_path):
    venv_dir = tmp_path / "v"
    assert _build(tmp_path, venv_dir=venv_dir).returncode == 0
    pth = next(venv_dir.glob("lib/python*/site-packages/zoe_kokoro_import_block.pth"))
    pth.write_text("\n")
    r = _build(tmp_path, "--check", venv_dir=venv_dir)
    assert r.returncode != 0 and "blocker" in r.stderr
    # and a rebuild repairs it
    assert _build(tmp_path, venv_dir=venv_dir).returncode == 0
    assert _venv_probe(venv_dir, PROBE) == "None None"


def test_check_without_a_venv_fails(tmp_path):
    r = _build(tmp_path, "--check")
    assert r.returncode != 0 and "no venv" in r.stderr


def test_refuses_a_directory_that_is_not_a_venv(tmp_path):
    (tmp_path / "kokoro-venv").mkdir()
    r = _build(tmp_path)
    assert r.returncode != 0 and "not a venv" in r.stderr


def test_refuses_a_venv_that_cannot_see_system_site_packages(tmp_path):
    import venv
    d = tmp_path / "kokoro-venv"
    venv.create(d, with_pip=False, symlinks=True, system_site_packages=False)
    r = _build(tmp_path)
    assert r.returncode != 0 and "system-site-packages" in r.stderr


def test_refuses_the_wrong_base_interpreter(tmp_path):
    env_mm = "2.7"
    r = subprocess.run(["bash", str(BUILD)], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "ZOE_KOKORO_VENV": str(tmp_path / "v"),
                            "ZOE_KOKORO_BASE_PYTHON": os.path.realpath(sys.executable),
                            "ZOE_KOKORO_PY_MM": env_mm, "ZOE_KOKORO_VENV_SKIP_RUNTIME": "1"})
    assert r.returncode != 0 and not (tmp_path / "v").exists()


def test_smoke_reads_the_blocked_list_from_the_installed_module():
    # One source of truth: the smoke asserts over blk.BLOCKED, never its own copy.
    text = BUILD.read_text()
    assert "import zoe_kokoro_import_block as blk" in text
    assert "for b in blk.BLOCKED" in text
    assert '"zoe_kokoro_import_block" in sys.modules' in text  # the .pth really ran


# ── the drop-in ──────────────────────────────────────────────────────────────


def test_drop_in_resets_then_sets_exactly_one_execstart():
    lines = _exec_lines(DROP_IN)
    assert lines[0] == "", "the empty ExecStart= reset must come FIRST"
    assert len(lines) == 2, lines


def test_drop_in_keeps_the_template_script_and_only_swaps_the_interpreter():
    (template,) = _exec_lines(TEMPLATE)
    t_interp, *t_args = shlex.split(template)
    n_interp, *n_args = shlex.split(_exec_lines(DROP_IN)[1])
    assert t_interp == "/usr/bin/python3"  # the rollback target
    assert n_args == t_args == ["%h/assistant/scripts/setup/kokoro_sidecar.py"]
    assert n_interp == "%h/.zoe/venvs/kokoro-py310/bin/python"


def test_drop_in_interpreter_is_the_venv_the_build_script_creates():
    m = re.search(r'VENV_DIR="\$\{ZOE_KOKORO_VENV:-\$HOME/([^}"]+)\}"', BUILD.read_text())
    assert m, "build_kokoro_venv.sh default VENV_DIR not found"
    assert shlex.split(_exec_lines(DROP_IN)[1])[0] == f"%h/{m.group(1)}/bin/python"


def test_switch_recipe_renders_the_drop_in_for_an_overridden_venv():
    """Greptile #1750: with ZOE_KOKORO_VENV set, a plain `cp` of the tracked drop-in
    would restart the sidecar on the DEFAULT interpreter (missing -> Restart=always
    loop, voice on fallback TTS). The build-mode recipe must render the drop-in for
    the venv it actually built. The print sits behind the live smoke, so this pins
    the rendering command itself and proves it on the tracked file."""
    src = BUILD.read_text()
    m = re.search(r"sed '(s#[^']+#\$VENV_DIR/bin/python#)' scripts/setup/systemd/kokoro-tts\.service\.d/60-kokoro-venv\.conf", src)
    assert m, "override branch of the switch recipe not found"
    assert 'if [[ "$VENV_DIR" == "$HOME/.zoe/venvs/kokoro-py310" ]]' in src
    override = "/srv/other/kokoro-venv"
    expr = m.group(1).replace("$VENV_DIR", override)
    r = subprocess.run(["sed", expr, str(DROP_IN)], capture_output=True, text=True, check=True)
    rendered = [ln.split("=", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("ExecStart=")]
    assert rendered[0] == "" and len(rendered) == 2
    interp, *args = shlex.split(rendered[1])
    assert interp == f"{override}/bin/python"
    assert args == ["%h/assistant/scripts/setup/kokoro_sidecar.py"]
    # negative control: the untouched tracked file still names the default
    assert shlex.split(_exec_lines(DROP_IN)[1])[0] == "%h/.zoe/venvs/kokoro-py310/bin/python"


def test_drop_in_changes_nothing_but_execstart():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(DROP_IN.read_text(encoding="utf-8"))
    assert cp.sections() == ["Service"]
    assert set(cp["Service"]) == {"ExecStart"}


def test_drop_in_sorts_after_the_memory_tuning_drop_in():
    # Both apply; neither overrides the other's keys. Order is by filename.
    names = sorted(p.name for p in DROP_IN.parent.glob("*.conf"))
    assert names.index("40-memory-tuning.conf") < names.index(DROP_IN.name)


# ── the manifest ─────────────────────────────────────────────────────────────


def _pins() -> dict[str, str]:
    pins = {}
    for raw in REQ.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            name, sep, ver = line.partition("==")
            assert sep and ver, f"not an exact pin: {raw!r}"
            pins[name.lower()] = ver
    return pins


def test_manifest_is_exact_pinned_and_covers_the_model_stack():
    pins = _pins()
    for must in ("torch", "kokoro", "misaki", "transformers", "spacy", "fastapi", "uvicorn"):
        assert must in pins, must


def test_manifest_never_lists_a_blocked_package():
    dist_of = {"sklearn": "scikit-learn", "pandas": "pandas", "pyarrow": "pyarrow"}
    assert not {dist_of[m] for m in _load_block().BLOCKED} & set(_pins())
