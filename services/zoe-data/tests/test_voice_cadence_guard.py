"""TTS-cadence guard: the gate that makes a voice-RHYTHM regression visible.

The replay voice gate stops before TTS, so it is blind to prosody/rhythm by
construction (docs/knowledge/voice-pipeline.md). voice_cadence_guard judges the
schedule of Kokoro /synthesize calls a turn produces against a recorded band.

The load-bearing assertion here is the NEGATIVE CONTROL: an evenly-paced trace
(Flue 1.x, or the Flue-2.x stream after the inter-sentence pacer) PASSES, and a
bursty trace with the burst-then-stall signature that the Flue-2.x flip
introduced FAILS. A guard that cannot go red on the exact regression it exists to
catch is not an instrument — so both directions are asserted, against the same
committed baseline the live check uses.
"""
import json
from pathlib import Path

import pytest

import voice_cadence_guard as vcg

pytestmark = pytest.mark.ci_safe


_FIXTURE = Path(__file__).parent / "fixtures" / "voice_cadence_baseline.json"


# An evenly-paced spoken stream: sentences delivered ~200-1200ms apart, none
# rushed. This is the Flue-1.x shape and the post-pacer Flue-2.x shape.
_EVEN_TRACE = [
    {"t_ms": 0.0, "chars": 81},
    {"t_ms": 260.0, "chars": 44},
    {"t_ms": 540.0, "chars": 77},
    {"t_ms": 900.0, "chars": 60},
    {"t_ms": 1300.0, "chars": 90},
]

# The regression: bursts of near-0ms deliveries (rushed, no breath) punctuated by
# ~1s stalls. This is the pre-fix Flue-2.x shape — several sentences complete at
# once when a delta burst lands, then silence until the next burst.
_BURSTY_TRACE = [
    {"t_ms": 0.0, "chars": 89},
    {"t_ms": 0.3, "chars": 48},     # rushed
    {"t_ms": 1.0, "chars": 40},     # rushed
    {"t_ms": 1053.0, "chars": 89},  # stall
    {"t_ms": 1053.4, "chars": 70},  # rushed
    {"t_ms": 1600.0, "chars": 124},
]


def _band() -> vcg.CadenceBand:
    return vcg.load_baseline(_FIXTURE)


def test_baseline_fixture_loads_and_is_well_formed():
    band = _band()
    assert band.rush_fraction_max > 0.0
    assert band.gap_cv_max > 0.0
    assert band.min_gaps >= 1


def test_even_trace_passes():
    verdict = vcg.evaluate_cadence(_EVEN_TRACE, _band())
    assert verdict.ok, verdict.failures
    assert not verdict.skipped
    assert verdict.metrics.rush_fraction == 0.0


def test_bursty_trace_fails_the_negative_control():
    # THE negative control: the burst-then-stall signature must go red, or the
    # guard is decorative. A high rush_fraction is the load-bearing signal.
    verdict = vcg.evaluate_cadence(_BURSTY_TRACE, _band())
    assert not verdict.ok
    assert any("rush_fraction" in f for f in verdict.failures)
    assert verdict.metrics.rush_fraction > _band().rush_fraction_max


def test_pacer_repairs_a_bursty_trace():
    # Simulate what routers/voice_tts._pace_delivery does: enforce a minimum
    # gap between consecutive deliveries. The repaired trace must flip red->green
    # against the SAME band — proving the guard tracks the consumer-side fix.
    floor_ms = 200.0
    repaired = []
    last = None
    for pt in _BURSTY_TRACE:
        t = pt["t_ms"]
        if last is not None and t - last < floor_ms:
            t = last + floor_ms
        repaired.append({"t_ms": t, "chars": pt["chars"]})
        last = t
    verdict = vcg.evaluate_cadence(repaired, _band())
    assert verdict.ok, verdict.failures
    assert verdict.metrics.rush_fraction == 0.0


def test_short_reply_is_exempt_not_a_false_pass():
    # A one/two-sentence reply has no rhythm to be uneven — it must be SKIPPED
    # (exempt), never counted as a vouched pass.
    verdict = vcg.evaluate_cadence(
        [{"t_ms": 0.0, "chars": 40}, {"t_ms": 5.0, "chars": 50}], _band()
    )
    assert verdict.ok
    assert verdict.skipped


def test_metrics_are_timestamp_origin_independent():
    # Only differences matter — shifting every timestamp must not change metrics.
    a = vcg.compute_cadence_metrics(_EVEN_TRACE)
    shifted = [{"t_ms": pt["t_ms"] + 10_000.0, "chars": pt["chars"]} for pt in _EVEN_TRACE]
    b = vcg.compute_cadence_metrics(shifted)
    assert a.rush_fraction == b.rush_fraction
    assert a.gap_cv == pytest.approx(b.gap_cv)


def test_accepts_tuple_pairs_and_dicts_alike():
    pairs = [(pt["t_ms"], pt["chars"]) for pt in _EVEN_TRACE]
    m_pairs = vcg.compute_cadence_metrics(pairs)
    m_dicts = vcg.compute_cadence_metrics(_EVEN_TRACE)
    assert m_pairs.gap_cv == pytest.approx(m_dicts.gap_cv)
    assert m_pairs.size_min_chars == m_dicts.size_min_chars
