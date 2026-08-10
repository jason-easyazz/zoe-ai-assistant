"""TTS-cadence guard — the instrument that makes a prosody/rhythm regression visible.

The replay voice gate is warm and STOPS BEFORE TTS (docs/knowledge/voice-pipeline.md),
so it can see said-vs-did and per-stage speed but is BLIND to rhythm by construction:
nothing it measures depends on the cadence at which sentences reach Kokoro. The
Flue-1.x → 2.x brain flip (2026-08-10) regressed exactly that invisible axis — the
2.x sidecar flushes its NDJSON deltas in time-bursts, so several sentences complete
at once and the voice path fires Kokoro /synthesize back-to-back (rushed), then stalls
~1s (frozen). "Bursty then stalled" is what an operator hears as uneven, less-natural
rhythm.

This module is the pure, stdlib-only core of a cadence check: given a TRACE of spoken
sentences (each a monotonic delivery timestamp + a character length — i.e. the schedule
of Kokoro /synthesize calls a turn produces) it computes rhythm metrics and compares
them to a recorded baseline BAND. It needs no live Kokoro and no live brain, so it is
ci_safe against a recorded fixture; a live capture script (scripts/perf/measure_tts_cadence.py)
records real traces and can refresh the baseline.

The load-bearing metric is RUSH FRACTION — the share of consecutive deliveries closer
than a natural inter-sentence gap. An even stream (Flue 1.x, or the 2.x stream after the
inter-sentence pacer in routers/voice_tts._pace_delivery) has a rush fraction near zero;
a bursty stream has a high one. GAP CV (coefficient of variation) is the secondary
signal — a burst-then-stall stream is far more variable than an evenly-paced one. Both
are direction-correct: the consumer-side pacer drives them both DOWN, so this guard's
red→green is the fix's before→after.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


# A consecutive-delivery gap below this many ms is "rushed" — closer than any
# natural inter-sentence boundary in speech. Sentences delivered this close pile
# up at the client and play with no breath between them.
DEFAULT_RUSH_MS = 80.0


@dataclass(frozen=True)
class CadenceMetrics:
    """Rhythm metrics for one turn's spoken-sentence delivery schedule."""

    n_emits: int
    n_gaps: int
    rush_fraction: float          # share of inter-emit gaps < rush_ms
    gap_cv: float                 # stdev/mean of inter-emit gaps (0 if <2 gaps)
    gap_median_ms: float
    gap_max_ms: float             # the stall
    size_min_chars: int
    size_median_chars: float
    rush_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_emits": self.n_emits,
            "n_gaps": self.n_gaps,
            "rush_fraction": round(self.rush_fraction, 4),
            "gap_cv": round(self.gap_cv, 4),
            "gap_median_ms": round(self.gap_median_ms, 2),
            "gap_max_ms": round(self.gap_max_ms, 2),
            "size_min_chars": self.size_min_chars,
            "size_median_chars": round(self.size_median_chars, 2),
            "rush_ms": self.rush_ms,
        }


def _coerce_trace(trace: Iterable[Any]) -> list[tuple[float, int]]:
    """Normalise a trace into [(t_ms, chars), ...].

    Accepts dicts ({"t_ms":.., "chars":..}) or (t_ms, chars) pairs. A missing
    ``chars`` defaults to 0 — timestamps alone still yield cadence metrics.
    """
    out: list[tuple[float, int]] = []
    for item in trace:
        if isinstance(item, dict):
            t = float(item.get("t_ms", item.get("t", 0.0)))
            c = int(item.get("chars", item.get("len", 0)) or 0)
        else:
            seq = list(item)
            t = float(seq[0])
            c = int(seq[1]) if len(seq) > 1 else 0
        out.append((t, c))
    return out


def compute_cadence_metrics(
    trace: Iterable[Any], *, rush_ms: float = DEFAULT_RUSH_MS
) -> CadenceMetrics:
    """Compute rhythm metrics from a spoken-sentence delivery trace.

    ``trace`` is the ordered schedule of /synthesize deliveries: each entry a
    monotonic timestamp (ms, any origin — only differences are used) and the
    sentence's character length. Deliberately timestamp-difference based, so a
    trace captured against any clock origin is comparable.
    """
    pts = _coerce_trace(trace)
    pts.sort(key=lambda p: p[0])
    n = len(pts)
    sizes = [c for _, c in pts]
    gaps = [pts[i][0] - pts[i - 1][0] for i in range(1, n)]

    if gaps:
        rush = sum(1 for g in gaps if g < rush_ms) / len(gaps)
        gap_median = statistics.median(gaps)
        gap_max = max(gaps)
        mean = statistics.mean(gaps)
        gap_cv = (statistics.pstdev(gaps) / mean) if mean > 0 else 0.0
    else:
        rush = 0.0
        gap_median = 0.0
        gap_max = 0.0
        gap_cv = 0.0

    return CadenceMetrics(
        n_emits=n,
        n_gaps=len(gaps),
        rush_fraction=rush,
        gap_cv=gap_cv,
        gap_median_ms=gap_median,
        gap_max_ms=gap_max,
        size_min_chars=min(sizes) if sizes else 0,
        size_median_chars=statistics.median(sizes) if sizes else 0.0,
        rush_ms=rush_ms,
    )


@dataclass(frozen=True)
class CadenceBand:
    """The pass band a candidate turn's metrics must stay inside.

    Bands are UPPER bounds on the two disorder metrics: a natural stream is even
    (low rush, low variance); the regression is a HIGH rush fraction and HIGH
    variance. ``min_gaps`` guards against judging a trace too short to have a
    rhythm (a one/two-sentence reply is exempt — nothing to be uneven about).
    """

    rush_fraction_max: float
    gap_cv_max: float
    min_gaps: int = 3
    rush_ms: float = DEFAULT_RUSH_MS

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CadenceBand":
        return cls(
            rush_fraction_max=float(d["rush_fraction_max"]),
            gap_cv_max=float(d["gap_cv_max"]),
            min_gaps=int(d.get("min_gaps", 3)),
            rush_ms=float(d.get("rush_ms", DEFAULT_RUSH_MS)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rush_fraction_max": self.rush_fraction_max,
            "gap_cv_max": self.gap_cv_max,
            "min_gaps": self.min_gaps,
            "rush_ms": self.rush_ms,
        }


@dataclass
class CadenceVerdict:
    ok: bool
    metrics: CadenceMetrics
    failures: list[str] = field(default_factory=list)
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "failures": list(self.failures),
            "metrics": self.metrics.as_dict(),
        }


def evaluate_cadence(trace: Iterable[Any], band: CadenceBand) -> CadenceVerdict:
    """Judge one turn's delivery trace against a band. Never raises on shape."""
    metrics = compute_cadence_metrics(trace, rush_ms=band.rush_ms)
    if metrics.n_gaps < band.min_gaps:
        # Too short to have a rhythm — exempt, not a pass we can vouch for.
        return CadenceVerdict(ok=True, metrics=metrics, skipped=True)

    failures: list[str] = []
    if metrics.rush_fraction > band.rush_fraction_max:
        failures.append(
            f"rush_fraction {metrics.rush_fraction:.3f} > {band.rush_fraction_max:.3f} "
            f"(sentences delivered <{band.rush_ms:.0f}ms apart — bursty synthesis)"
        )
    if metrics.gap_cv > band.gap_cv_max:
        failures.append(
            f"gap_cv {metrics.gap_cv:.3f} > {band.gap_cv_max:.3f} "
            f"(uneven inter-sentence spacing — burst/stall)"
        )
    return CadenceVerdict(ok=not failures, metrics=metrics, failures=failures)


def load_baseline(path: str | Path) -> CadenceBand:
    """Load a recorded baseline band from JSON."""
    data = json.loads(Path(path).read_text())
    return CadenceBand.from_dict(data["band"])
