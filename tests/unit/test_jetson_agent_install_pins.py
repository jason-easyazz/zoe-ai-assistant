"""The system Python 3.10 lane must never install the old mempalace/chromadb pair again.

B0.8 (2026-09-27) moved the palace to chromadb 1.5.x format, and only the zoe-data py3.12
venv opens it (``requirements-py312.txt``). The 3.10 lane used to carry ``mempalace==3.3.1``
+ ``chromadb==0.6.3``, a pair that can only do harm on the migrated palace: 0.6.3 dies on
the 1.x format (``KeyError: '_type'``), and mempalace 3.3.1's ``_fix_blob_seq_ids``
misreads the 1.x sysdb and SILENTLY DROPS drawer writes. B9 (2026-10-18) dropped the pair,
so this asserts it stays dropped:

* ``services/zoe-data/requirements.txt`` (the 3.10 manifest) pins neither package;
* ``scripts/setup/jetson/install-jetson-agent.sh`` installs neither package, pinned or not.

Silent data loss leaves no crash to notice, so this is asserted rather than trusted.
Text-only assertions over tracked files: no pip, no network, no venv (see tests/AGENTS.md).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# stdlib only — safe on the slim GitHub lane.
pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "setup" / "jetson" / "install-jetson-agent.sh"
_REQUIREMENTS = ROOT / "services" / "zoe-data" / "requirements.txt"

_PACKAGES = ("mempalace", "chromadb")

# A pip install line (not commented out), e.g. `pip3 install ...` or `"$VENV/bin/pip" install ...`.
_INSTALL_LINE = re.compile(r"^[^#]*\bpip3?\b[^#]*\binstall\b.*$", re.MULTILINE)


def _installed_packages(script_text: str) -> set[str]:
    """Packages named on a pip-install line, pinned or not, directly or through a shell variable
    whose assignment names the package.

    This is the instrument under test; ``test_detector_sees_every_install_shape`` is its
    negative control.
    """
    named_vars = {
        var for var, value in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", script_text, re.MULTILINE)
        if any(re.search(rf"\b{p}\b", value) for p in _PACKAGES)
    }
    found: set[str] = set()
    for line in _INSTALL_LINE.findall(script_text):
        for package in _PACKAGES:
            if re.search(rf"\b{re.escape(package)}\b", line):
                found.add(package)
        for var in named_vars:
            if re.search(rf"\$\{{?{var}\b", line):
                value = re.search(rf"^\s*{var}=(.*)$", script_text, re.MULTILINE).group(1)
                found.update(p for p in _PACKAGES if re.search(rf"\b{p}\b", value))
    return found


def _manifest_pins(text: str) -> set[str]:
    """Packages the manifest declares as a requirement (any specifier), ignoring comments."""
    return {p for p in _PACKAGES if re.search(rf"^\s*{re.escape(p)}\s*(?:[=<>!~\[;]|$)", text, re.MULTILINE | re.IGNORECASE)}


def test_installer_installs_no_vector_store_package() -> None:
    found = _installed_packages(_SCRIPT.read_text())
    assert not found, (
        f"{_SCRIPT} installs {sorted(found)}. The palace is chromadb 1.x and only the py3.12 venv "
        "(scripts/setup/build_py312_venv.sh) may carry the store client; the old 0.6.3 / 3.3.1 pair "
        "dies on it or silently drops drawer writes."
    )


def test_three_ten_manifest_pins_no_vector_store_package() -> None:
    found = _manifest_pins(_REQUIREMENTS.read_text())
    assert not found, (
        f"{_REQUIREMENTS} declares {sorted(found)}. The 3.10 lane carries no store client since B9; "
        "the live pins live in requirements-py312.txt."
    )


def test_detector_sees_every_install_shape() -> None:
    """NEGATIVE CONTROL: both detectors must go red on the shapes the old script and manifest used.

    Without this, a regex that silently matched nothing would make the two tests above pass
    unconditionally.
    """
    assert _installed_packages('pip3 install --quiet mempalace chromadb\n') == set(_PACKAGES)
    assert _installed_packages('  "$VENV/bin/pip" install --quiet "mempalace==3.3.1" "chromadb==0.6.3"\n') == set(_PACKAGES)
    # The exact pre-B9 shape: pins held in shell variables, expanded on the install line.
    old_script = (
        'MEMPALACE_PIN="mempalace==3.3.1"\nCHROMADB_PIN="chromadb==0.6.3"\n'
        'pip3 install --quiet "$MEMPALACE_PIN" "$CHROMADB_PIN"\n'
    )
    assert _installed_packages(old_script) == set(_PACKAGES)
    # A commented-out install is not an install the script performs.
    assert _installed_packages('# pip3 install --quiet mempalace chromadb\n') == set()

    assert _manifest_pins("mempalace==3.3.1\nchromadb==0.6.3\n") == set(_PACKAGES)
    assert _manifest_pins("chromadb>=0.5\n") == {"chromadb"}
    # Prose that mentions the packages in comments is not a pin.
    assert _manifest_pins("# the old mempalace==3.3.1 / chromadb==0.6.3 pair is gone\n") == set()
