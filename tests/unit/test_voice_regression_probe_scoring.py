"""Scoring tests for the voice replay gate's function-regression verdict.

The gate exists to catch ONE thing: Zoe losing an ability she had before
("can't do it" = a bug). It must not be vetoable by a bad recording.

`replay_samples.py::_classify` emits four verdicts, and only two of them are
about Zoe:

    OK       fast path or brain answered
    CANT_DO  asked for something Zoe couldn't fulfil   <- regression signal
    ERROR    the turn blew up                          <- regression signal
    EMPTY    STT heard nothing (silence / clipped capture)  <- about the TAPE

EMPTY was already excluded from `fail` but left in the `ok_rate` DENOMINATOR,
so one extra silent clip in the corpus read as a said-vs-did regression and
hard-failed the gate — blocking every voice-path deploy. Observed live on
2026-07-26: 18 OK + 2 EMPTY scored 0.900 against a 19 OK + 1 EMPTY baseline of
0.950, with fail=0 on BOTH runs. Nothing had regressed.

These tests pin both halves of the contract, and the negative controls matter
more than the positive one: the fix is worthless if the gate can no longer go
red. A rising CANT_DO/ERROR count MUST still fail — including the case a rate
comparison alone silently missed, where the scoreable denominator grows enough
for the rate to tie while carrying a brand-new regression.

Pure-logic only (stdlib), so this runs in the fast `ci_safe` lane.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "scripts" / "maintenance" / "voice_regression_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("vrp_scoring", PROBE)
    assert spec and spec.loader, f"cannot load {PROBE}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["vrp_scoring"] = module
    spec.loader.exec_module(module)
    return module


vrp = _load_probe()


def _report(**verdicts: int) -> dict[str, Any]:
    """A minimal replay report carrying only the verdict counts under test."""
    return {
        "n_samples": sum(verdicts.values()),
        "verdicts": verdicts,
        # Flat medians: these tests are about the FUNCTION axis, so speed must
        # never be what turns the result red.
        "aggregate_ms": {k: {"median": 100} for k in ("stt_ms", "brain_ms", "e2e_ms")},
    }


def _function_warnings(cur: dict[str, int], base: dict[str, int]) -> list[str]:
    summary = vrp.summarize(_report(**cur))
    baseline = {"summary": vrp.summarize(_report(**base))}
    warnings = vrp.compare(summary, baseline, 1.25, 400)
    return [w for w in warnings if w.startswith("FUNCTION")]


# ── EMPTY is about the recording, never about Zoe ───────────────────────────

def test_extra_silent_recording_does_not_fail_the_gate():
    """The live 2026-07-26 false alarm: one more EMPTY, zero capability lost."""
    assert _function_warnings({"OK": 18, "EMPTY": 2}, {"OK": 19, "EMPTY": 1}) == []


def test_empty_storm_alone_never_vetoes_a_deploy():
    """Even a badly degraded capture run must not block a voice-path release."""
    assert _function_warnings({"OK": 14, "EMPTY": 6}, {"OK": 19, "EMPTY": 1}) == []


def test_empty_is_excluded_from_the_ok_rate_denominator():
    summary = vrp.summarize(_report(OK=18, EMPTY=2))
    assert summary["empty"] == 2
    assert summary["scoreable"] == 18       # EMPTY out of the denominator
    assert summary["total"] == 20           # ...but still reported
    assert summary["ok_rate"] == 1.0


# ── NEGATIVE CONTROLS: the gate must still go red ──────────────────────────

def test_new_cant_do_still_fails():
    warnings = _function_warnings({"OK": 17, "CANT_DO": 1, "EMPTY": 2}, {"OK": 19, "EMPTY": 1})
    assert warnings, "a new CANT_DO must fail the gate"


def test_new_error_still_fails():
    warnings = _function_warnings({"OK": 18, "ERROR": 1, "EMPTY": 1}, {"OK": 19, "EMPTY": 1})
    assert warnings, "a new ERROR must fail the gate"


def test_rising_fail_count_caught_when_the_rate_ties():
    """The case a rate comparison alone missed.

    19 OK + 1 CANT_DO over 20 scoreable = 0.950, which clears a 0.950 bar — yet
    the run carries a regression the corpus did not have before. The module
    contract promises a COUNT check as well as a rate check; this pins it.
    """
    warnings = _function_warnings({"OK": 19, "CANT_DO": 1}, {"OK": 19, "EMPTY": 1})
    assert any("count rose" in w for w in warnings), warnings


def test_steady_state_passes():
    """No change at all must stay green — the gate is not allowed to drift red."""
    assert _function_warnings({"OK": 19, "EMPTY": 1}, {"OK": 19, "EMPTY": 1}) == []
