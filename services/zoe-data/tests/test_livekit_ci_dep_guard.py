"""Dep guard: the LiveKit ingest-fidelity suite must never SKIP in the required gate.

``test_livekit_audio_frame_bytes.py`` calls ``pytest.importorskip("av")`` /
``pytest.importorskip("aiortc")`` at module scope, and the module it exercises
(``livekit_aiortc``) additionally imports ``livekit.protocol`` at top level. If any
of those wheels stops being installed in ``validate.yml``'s slim lane, that whole
fidelity suite skips — and **pytest reports a skip as green**. The required gate then
proves nothing about the ``_AudioStream`` padding fix, and a reversion to
``bytes(planes[0])`` merges clean. That is precisely the state #1636 was opened in.

So this file makes the dependency LOUD instead of silent: on a CI runner, missing
wheels are a FAILURE. Off CI (a dev box that never installed them) it skips, because
a laptop is not the merge gate.

It also pins the *other* silent way the suite can leave the lane: the ``ci_safe``
marker. Dropping that line removes the suite from ``-m ci_safe`` with no other signal.

Stdlib only, no imports of the guarded packages — this file must stay collectable
in the very environment whose gaps it is reporting.
"""
import importlib.util
import os
import pathlib

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: validate.yml's `-m ci_safe` lane

_TESTS_DIR = pathlib.Path(__file__).resolve().parent
_FIDELITY_SUITE = _TESTS_DIR / "test_livekit_audio_frame_bytes.py"

# What `test_livekit_audio_frame_bytes.py` needs to run rather than skip:
# av + aiortc are its own importorskips; numpy builds every fixture; livekit.protocol
# is imported at the top of `livekit_aiortc` itself (a plain ImportError, not a skip).
_REQUIRED_MODULES = ("av", "aiortc", "numpy", "livekit.protocol")

_TRUTHY = {"1", "true", "yes", "on"}


def _missing(modules):
    """Names in `modules` that cannot be imported in this interpreter."""
    absent = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:
                absent.append(name)
        except (ImportError, ValueError):
            # Parent package absent (find_spec imports it), or a broken spec.
            absent.append(name)
    return absent


def _in_ci():
    return os.environ.get("CI", "").strip().lower() in _TRUTHY


def test_missing_helper_detects_an_absent_module():
    """NEGATIVE CONTROL, every run: prove this guard can still go red.

    If `_missing` ever silently reports nothing — a swallowed exception, a rename,
    an importlib behaviour change — the guard below becomes a yes-machine that
    passes in exactly the situation it exists to catch. Feed it a module that
    cannot exist and require it to be reported.
    """
    sentinel = "zoe_no_such_module_livekit_ci_dep_guard"
    assert _missing([sentinel]) == [sentinel], (
        "_missing() failed to report an impossible module — this guard is not guarding"
    )
    # And it must not cry wolf on a module that certainly does exist.
    assert _missing(["os", "pathlib"]) == []


def test_fidelity_suite_still_opts_into_the_ci_safe_lane():
    """The suite reaches `validate` by marker, not by enumeration — so the marker IS the wiring."""
    if not _FIDELITY_SUITE.exists():
        pytest.skip(f"{_FIDELITY_SUITE.name} has been retired; nothing to guard")
    source = _FIDELITY_SUITE.read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.ci_safe" in source, (
        f"{_FIDELITY_SUITE.name} lost its `ci_safe` marker — it has silently dropped "
        f"out of validate.yml's `-m ci_safe` lane and no longer gates anything. "
        f"See services/zoe-data/tests/AGENTS.md."
    )


def test_livekit_fidelity_deps_are_installed_in_ci():
    """On a CI runner the fidelity suite must RUN. A skip here is a hole in the gate."""
    if not _FIDELITY_SUITE.exists():
        pytest.skip(f"{_FIDELITY_SUITE.name} has been retired; nothing to guard")
    if not _in_ci():
        pytest.skip(
            "not a CI runner (CI unset) — a dev box without av/aiortc is fine; "
            "this guard only binds the lane that gates merges"
        )
    absent = _missing(_REQUIRED_MODULES)
    assert not absent, (
        f"{', '.join(absent)} not importable on this CI runner, so "
        f"{_FIDELITY_SUITE.name} will importorskip and the required `validate` gate "
        f"stops verifying the LiveKit _AudioStream padding fix — a SKIP reads green. "
        f"Fix: restore the `pip install av aiortc livekit-protocol` step in "
        f".github/workflows/validate.yml's unit-test job (all three resolve to "
        f"manylinux x86_64 wheels for cp310; no source build)."
    )
