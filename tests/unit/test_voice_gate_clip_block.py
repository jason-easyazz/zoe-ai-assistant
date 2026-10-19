"""voice_gate_check.py after T-3 (deep dive 2, 2026-10-19): the replay's own decision path and the instrument are
voice scope, the per-clip block is re-derived from its counts (never trusted), and a self-test artifact is never
evidence. Pure: no git, no artifact on disk."""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vgc_clip_block", REPO / "scripts" / "maintenance" / "voice_gate_check.py")
vgc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vgc)
DAY = 24 * 3600


@pytest.mark.parametrize("path", [
    "services/zoe-data/intent_router.py", "services/zoe-data/stt_snap.py", "services/zoe-data/memory_service.py",
    "services/zoe-data/auth.py", "services/zoe-data/panel_principal.py",
    "scripts/maintenance/voice_regression_probe.py", "scripts/maintenance/voice_clip_checks.py",
    "scripts/perf/measure_voice.py",
])
def test_t3_widened_voice_scope(path):
    assert vgc.touched_voice_files([path], vgc.VOICE_PATH_PATTERNS) == [path]


def test_t3_widening_does_not_sweep_in_neighbours():
    """Literal paths, not globs: the tests, the zoe-auth service and other maintenance scripts stay out."""
    near = ["services/zoe-data/tests/test_intent_router.py", "services/zoe-auth/auth.py",
            "services/zoe-data/routers/auth.py", "scripts/maintenance/voice_gate_check.py",
            "scripts/maintenance/voice_corpus_strata.py", "scripts/perf/measure_speed.py"]
    assert vgc.touched_voice_files(near, vgc.VOICE_PATH_PATTERNS) == []


def _clip_art(**over):
    art = {"clip_checks": True, "clip_summary": {"checked": 3, "passed": 3, "failed": 0, "unchecked": 0},
           "clip_results": [{"clip_id": c * 12, "result": "pass"} for c in "abc"]}
    art.update(over)
    return art


def test_clip_block_passes_a_clean_block_and_ignores_a_pre_t3_artifact():
    assert vgc.clip_block_ok(_clip_art()) == (True, "3/3 clips held their expected outcome")
    assert vgc.clip_block_ok({"status": "pass"}) == (True, "")


_ROWS = [{"clip_id": c * 12, "result": "pass"} for c in "abc"]


@pytest.mark.parametrize("over,needle", [
    ({"clip_summary": None}, "well-formed"),
    ({"clip_summary": {"checked": "3", "passed": 3, "failed": 0, "unchecked": 0}}, "well-formed"),
    ({"clip_summary": {"checked": 3, "passed": 3, "failed": 0}}, "well-formed"),          # no unchecked count
    ({"clip_summary": {"checked": 0, "passed": 0, "failed": 0, "unchecked": 0}, "clip_results": []}, "judged 0 clips"),
    ({"clip_summary": {"checked": 3, "passed": 2, "failed": 1, "unchecked": 0},
      "clip_results": _ROWS[:2] + [{"clip_id": "c" * 12, "result": "fail"}]}, "1 of 3"),
    # a producer that miscounts: the rows say a clip failed, the summary says none did
    ({"clip_results": _ROWS[:2] + [{"clip_id": "c" * 12, "result": "fail"}]}, "disagrees"),
    # truncated / padded / duplicated / unrelated evidence (Codex, review of #2154)
    ({"clip_results": []}, "disagrees"),
    ({"clip_results": _ROWS[:2]}, "disagrees"),
    ({"clip_results": _ROWS + [{"clip_id": "d" * 12, "result": "pass"}]}, "disagrees"),
    ({"clip_results": _ROWS[:2] + [dict(_ROWS[0])]}, "duplicate"),
    ({"clip_results": _ROWS[:2] + [{"clip_id": "c" * 12, "result": "ok"}]}, "malformed"),
    ({"clip_results": _ROWS[:2] + [{"result": "pass"}]}, "malformed"),
    ({"clip_results": None}, "malformed"),
])
def test_clip_block_refuses(over, needle):
    ok, why = vgc.clip_block_ok(_clip_art(**over))
    assert ok is False and needle in why


def test_evaluate_blocks_a_pass_whose_clips_failed_and_any_self_test_artifact():
    now = time.time()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    art = dict(_clip_art(clip_summary={"checked": 3, "passed": 2, "failed": 1, "unchecked": 0},
                         clip_results=_ROWS[:2] + [{"clip_id": "c" * 12, "result": "fail"}]), status="pass", timestamp=ts)
    assert vgc.evaluate(art, now_epoch=now, max_age_s=DAY)[0] is False
    assert vgc.evaluate(dict(_clip_art(), status="pass", timestamp=ts), now_epoch=now, max_age_s=DAY)[0] is True
    st = dict(_clip_art(), status="pass", timestamp=ts, self_test=True)
    ok, why = vgc.evaluate(st, now_epoch=now, max_age_s=DAY)
    assert ok is False and "SELF-TEST" in why


def test_every_pinned_clip_must_be_accounted_for():
    ok, why = vgc.clip_block_ok(_clip_art(baseline_ref={"clip_set_size": 20}))
    assert ok is False and "3 per-clip rows for a 20-clip pinned set" in why
    assert vgc.clip_block_ok(_clip_art(baseline_ref={"clip_set_size": 3}))[0] is True
