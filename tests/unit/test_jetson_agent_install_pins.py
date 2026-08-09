"""Pin the Jetson-agent installer's mempalace/chromadb versions to the requirements SSOT.

``scripts/setup/jetson/install-jetson-agent.sh`` installs mempalace + chromadb into a
side venv (or via ``pip3`` when no venv exists). Unpinned, a resolver installs
chromadb 1.5.x against a 0.6.x on-disk palace, which makes mempalace's
``_fix_blob_seq_ids`` misread the sysdb-10 BLOB format as legacy big-endian and
SILENTLY DROP drawer writes (recovery: ``mempalace repair --mode max-seq-id``).
chromadb 1.5.x additionally pulls numpy 2.x, defeating the ``numpy<2`` ceiling.

Silent data loss leaves no crash to notice, so this is asserted rather than trusted:
BOTH install sites must be pinned, and to the SAME versions the tracked
``services/zoe-data/requirements.txt`` holds — a pin that drifts from the SSOT
reintroduces the version skew it was added to prevent.

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

# A pip install line naming a bare package (no `==`) for one of the two packages.
# Matches `pip install mempalace chromadb`, `"$VENV/bin/pip" install --quiet chromadb`, etc.
_INSTALL_LINE = re.compile(r"^[^#]*\bpip3?\b[^#]*\binstall\b.*$", re.MULTILINE)


def _requirement_pin(package: str) -> str:
    """Exact version `requirements.txt` pins for ``package`` (e.g. '3.3.1')."""
    pattern = re.compile(rf"^{re.escape(package)}==(\S+)\s*$", re.MULTILINE)
    match = pattern.search(_REQUIREMENTS.read_text())
    assert match, f"{package}== pin not found in {_REQUIREMENTS}"
    return match.group(1)


def _unpinned_packages(script_text: str) -> set[str]:
    """Packages that appear on a pip-install line WITHOUT an `==` version.

    This is the instrument under test; ``test_detector_flags_unpinned_install``
    is its negative control.
    """
    offenders: set[str] = set()
    for line in _INSTALL_LINE.findall(script_text):
        for package in _PACKAGES:
            # The package named bare (not immediately followed by `==`) on an install line.
            if re.search(rf"\b{re.escape(package)}\b(?!==)", line):
                offenders.add(package)
    return offenders


def test_installer_pins_mempalace_and_chromadb() -> None:
    """Neither package may be installed unpinned, on either branch of the script."""
    offenders = _unpinned_packages(_SCRIPT.read_text())
    assert not offenders, (
        f"{_SCRIPT} installs {sorted(offenders)} UNPINNED. An unpinned chromadb "
        "resolves to 1.5.x against the 0.6.x palace -> silent drawer-write drops. "
        "Pin to the versions in services/zoe-data/requirements.txt."
    )


@pytest.mark.parametrize("package", _PACKAGES)
def test_installer_pins_match_requirements(package: str) -> None:
    """The installer's pins must equal the requirements.txt SSOT, not merely exist."""
    expected = _requirement_pin(package)
    found = set(re.findall(rf"\b{re.escape(package)}==([\w.]+)", _SCRIPT.read_text()))
    assert found == {expected}, (
        f"{_SCRIPT} pins {package} to {sorted(found) or '(nothing)'} but "
        f"requirements.txt pins {expected}. The two MUST move together — "
        "a skewed side venv is how a 1.5.x-on-0.6.x palace happens."
    )


def test_detector_flags_unpinned_install() -> None:
    """NEGATIVE CONTROL: the detector must go red on a genuinely unpinned script.

    Without this, a regex that silently matched nothing would make
    ``test_installer_pins_mempalace_and_chromadb`` pass unconditionally.
    """
    unpinned = 'pip3 install --quiet mempalace chromadb\n'
    assert _unpinned_packages(unpinned) == {"mempalace", "chromadb"}

    # And the venv branch's quoted form, which is the shape the live script uses.
    venv_form = '  "$VENV/bin/pip" install --quiet mempalace chromadb\n'
    assert _unpinned_packages(venv_form) == {"mempalace", "chromadb"}

    # A correctly pinned line must NOT be flagged (no false positives).
    pinned = 'pip3 install --quiet "mempalace==3.3.1" "chromadb==0.6.3"\n'
    assert _unpinned_packages(pinned) == set()

    # A commented-out unpinned line is not an install the script performs.
    commented = '# pip3 install --quiet mempalace chromadb\n'
    assert _unpinned_packages(commented) == set()
