"""The Jetson installer must not replace the brain unit outside a gated window (PR #1709).

``scripts/setup/install-jetson.sh`` copies every unit template into
``~/.config/systemd/user``. For ``llama-server.service`` that is unsafe in two cases,
so ``llama_unit_install_mode`` decides first:

* the binary the template's ExecStart names is missing -> ``skip-missing-binary``.
  Otherwise an enabled unit points at a missing executable and fails on the next
  restart or boot.
* an installed unit already exists and differs from the template -> ``keep-existing``.
  A brain build/flag change is applied in a gated window (rollback copy, Kokoro paused,
  replay gate), never by re-running the installer.

The function is extracted from the script and run in bash against temp files, so the
real decision logic is exercised. No systemd, no network.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.ci_safe,
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
]

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup" / "install-jetson.sh"
TEMPLATE = ROOT / "scripts" / "setup" / "systemd" / "llama-server.service"


def _function_src() -> str:
    m = re.search(r"^llama_unit_install_mode\(\) \{.*?^\}\n", SCRIPT.read_text(), re.DOTALL | re.MULTILINE)
    assert m, "install-jetson.sh lost llama_unit_install_mode()"
    return m.group(0)


def _mode(tpl: Path, dst: Path, home: Path) -> str:
    script = _function_src() + 'llama_unit_install_mode "$1" "$2" "$3"\n'
    out = subprocess.run(
        ["bash", "-c", script, "guard", str(tpl), str(dst), str(home)],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _template_binary(home: Path) -> Path:
    m = re.search(r"^ExecStart=(\S+)", TEMPLATE.read_text(), re.MULTILINE)
    assert m
    return Path(m.group(1).replace("%h", str(home)))


def _make_binary(home: Path) -> None:
    b = _template_binary(home)
    b.parent.mkdir(parents=True)
    b.write_text("#!/bin/sh\n")
    b.chmod(0o755)


def test_missing_binary_is_not_installed(tmp_path):
    dst = tmp_path / "installed.service"
    assert _mode(TEMPLATE, dst, tmp_path) == "skip-missing-binary"
    dst.write_text("old b9733 unit\n")  # established host: still must not be replaced
    assert _mode(TEMPLATE, dst, tmp_path) == "skip-missing-binary"


def test_existing_different_unit_is_kept(tmp_path):
    _make_binary(tmp_path)
    dst = tmp_path / "installed.service"
    dst.write_text("old b9733 unit\n")
    assert _mode(TEMPLATE, dst, tmp_path) == "keep-existing"


def test_fresh_host_or_identical_unit_installs(tmp_path):
    _make_binary(tmp_path)
    dst = tmp_path / "installed.service"
    assert _mode(TEMPLATE, dst, tmp_path) == "install"
    shutil.copy(TEMPLATE, dst)
    assert _mode(TEMPLATE, dst, tmp_path) == "install"


def test_copy_loop_honours_the_decision():
    src = SCRIPT.read_text()
    assert 'llama_mode="$(llama_unit_install_mode ' in src
    assert re.search(r'"\$unit_file" == "\$llama_tpl" && "\$llama_mode" != "install"', src), (
        "the unit copy loop must skip llama-server.service unless the guard said install"
    )
    assert not re.search(r'^\s*cp "\$\{ROOT_DIR\}"/scripts/setup/systemd/\*\.service', src, re.MULTILINE), (
        "a blanket *.service copy bypasses the brain-unit guard"
    )
