"""Offline coverage for `scripts/maintenance/requirements_drift_check.py`.

The drift check itself can only tell the truth on the Jetson (that is where the
zoe-data packages are installed), so this suite does NOT assert anything about
the live environment. It asserts the COMPARATOR — the instrument — because a
detector that silently matches nothing would report "no drift" forever and be
indistinguishable from a healthy box.

Every positive assertion here is paired with a negative control: the detector is
shown going red on input it must reject, not merely green on input it accepts
(`[[feedback_verify_your_instruments]]`).

Stdlib only (no pip, no network, no live host) — safe on the slim GitHub lane.
"""
from __future__ import annotations

import importlib.util
import re
from importlib.metadata import version
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_TOOL = ROOT / "scripts" / "maintenance" / "requirements_drift_check.py"
_REQUIREMENTS = ROOT / "services" / "zoe-data" / "requirements.txt"


def _load_tool():
    spec = importlib.util.spec_from_file_location("requirements_drift_check", _TOOL)
    assert spec and spec.loader, f"cannot load {_TOOL}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = _load_tool()


def _verdicts(text: str) -> dict[str, str]:
    return {f.name: f.verdict for f in drift.check(text)}


# ── version comparison ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "have,operator,want,expected",
    [
        ("0.34.0", "==", "0.32.0", False),   # the live uvicorn drift
        ("0.34.0", "==", "0.34.0", True),
        ("0.28.1", "==", "0.28.0", False),   # the live httpx drift
        ("14.1", "==", "14.0", False),       # the live websockets drift
        ("2.0.51", ">=", "2.0", True),
        ("1.13.0", ">=", "1.18.5", False),
        ("3.3.1", "!=", "3.6.0", True),
        ("1.7.2", "~=", "1.7", True),
        ("2.0.0", "~=", "1.7", False),
    ],
)
def test_satisfies(have: str, operator: str, want: str, expected: bool) -> None:
    assert drift.satisfies(have, operator, want) is expected


def test_numpy_2_ceiling_is_actually_enforced() -> None:
    """`numpy<2` guards both canonical rocks — the checker MUST catch a 2.x install.

    A NumPy 2 install is a C-ABI break for kokoro, moonshine-voice, chroma-hnswlib,
    ctranslate2, soundfile, soxr and Resemblyzer. `mempalace + chromadb` alone
    resolves to numpy 2.2.6 (measured 2026-08-02), so this is a live failure mode,
    not a hypothetical one.
    """
    assert drift.satisfies("2.2.6", "<", "2") is False   # must be caught
    assert drift.satisfies("1.26.4", "<", "2") is True   # must not false-positive
    assert drift.satisfies("2.0.0", "<", "2") is False   # boundary: 2.0.0 is NOT < 2

    # End-to-end through check(), using a name that is installed everywhere this
    # suite runs, so the verdict is deterministic rather than runner-dependent.
    have = version("pytest")
    assert _verdicts(f"pytest<{have}\n") == {"pytest": "MISMATCH"}
    assert _verdicts(f"pytest<={have}\n") == {"pytest": "match"}


def test_unparseable_version_is_uncheckable_not_silently_ok() -> None:
    """An unreadable version must NOT be treated as satisfied."""
    assert drift.parse_version("not-a-version") is None
    assert drift.satisfies("not-a-version", ">=", "1.0") is None


# ── end-to-end classification, with negative controls ───────────────────────

def test_matching_pin_reports_match() -> None:
    text = f"pytest=={version('pytest')}\n"
    assert _verdicts(text) == {"pytest": "match"}


def test_negative_control_drifted_pin_is_detected() -> None:
    """NEGATIVE CONTROL: the detector must go RED on a pin that disagrees.

    Without this, a comparator that returned "match" unconditionally would make
    every other assertion in this file pass while detecting nothing.
    """
    text = "pytest==0.0.0\n"
    findings = drift.check(text)
    assert [f.verdict for f in findings] == ["MISMATCH"]
    assert findings[0].is_drift is True
    assert findings[0].installed == version("pytest")


def test_absent_package_is_drift_but_optional_marker_downgrades_it() -> None:
    absent = "zoe-definitely-not-installed-xyz>=1.0\n"
    assert _verdicts(absent) == {"zoe-definitely-not-installed-xyz": "MISSING"}
    assert drift.check(absent)[0].is_drift is True

    marked = "zoe-definitely-not-installed-xyz>=1.0  # drift-optional: degrades\n"
    assert _verdicts(marked) == {"zoe-definitely-not-installed-xyz": "missing-optional"}
    assert drift.check(marked)[0].is_drift is False


def test_optional_marker_never_excuses_a_version_mismatch() -> None:
    """`drift-optional` means "may be absent", NOT "may be any version"."""
    text = "pytest==0.0.0  # drift-optional: degrades\n"
    findings = drift.check(text)
    assert [f.verdict for f in findings] == ["MISMATCH"]
    assert findings[0].is_drift is True


def test_extras_and_unpinned_and_markers_parse() -> None:
    text = (
        "# a comment\n"
        "\n"
        "-r other.txt\n"
        "uvicorn[standard]==0.34.0\n"
        "segno\n"
        "aiosqlite==0.22.1 ; python_version >= '3.8'\n"
    )
    parsed = {name: spec for name, spec, _ in drift.iter_requirements(text)}
    assert parsed == {
        "uvicorn": "==0.34.0",
        "segno": "",
        "aiosqlite": "==0.22.1",
    }
    assert _verdicts("segno\n")["segno"] in {"unpinned", "MISSING"}


# ── the parser must actually see the tracked file ───────────────────────────

def test_tracked_requirements_file_parses_completely() -> None:
    """Vacuity guard: every `==` pin in the real file must be picked up.

    The tracked file is 90% prose comments. A regex that quietly skipped its
    requirement lines would make the Jetson lane report a clean environment
    regardless of the truth — the exact failure this whole change is about.
    """
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    parsed = list(drift.iter_requirements(text))
    assert len(parsed) >= 30, f"only parsed {len(parsed)} requirements — parser is broken"

    # Count `==` pins in the raw file (ignoring comment lines) and match the parse.
    raw_exact = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#") and "==" in line.split("#", 1)[0]
    ]
    parsed_exact = [name for name, spec, _ in parsed if "==" in spec]
    assert len(parsed_exact) == len(raw_exact), (
        f"raw file has {len(raw_exact)} exact pins, parser found {len(parsed_exact)}"
    )


def test_onnxruntime_is_declared() -> None:
    """`voice_vad.py` / `voice_turn.py` import it directly — it must be declared.

    It was a load-bearing undeclared voice-path dependency until 2026-08-06.
    """
    names = {name for name, _, _ in drift.iter_requirements(_REQUIREMENTS.read_text())}
    assert "onnxruntime" in names

    importers = [
        p for p in (ROOT / "services" / "zoe-data").glob("*.py")
        if re.search(r"^\s*import onnxruntime", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert importers, "no module imports onnxruntime — drop the pin instead of keeping it"
