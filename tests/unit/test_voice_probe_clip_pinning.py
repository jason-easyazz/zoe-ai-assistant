"""The voice probe replays a PINNED clip set, never 'whatever is newest today'.

Incident (2026-10-13): the probe replayed the newest N clips by mtime, so every
new recording silently changed the set. STT measured ~400 ms against a 921 ms
baseline recorded on a different (longer-clip) set, which means a genuine 2x STT
regression would still have read as faster than baseline and passed.

Contracts pinned here:
  * clip ids are hashes of name+size: stable, content-free, name-free;
  * the baseline records the ids it measured and later runs replay EXACTLY those,
    even after newer recordings land (the drift bug); negative control: the old
    newest-N selection DOES move when a clip is added;
  * an unavailable pinned set, or a legacy unpinned baseline, REFUSES speed with a
    BASELINE-SET reason (never a silent pass); update-baseline re-pins;
  * a 2x STT regression on the pinned set fails; a length-normalised per-audio-
    second STT metric is recorded and gated;
  * the deploy gate still accepts a pinned-run artifact (additive fields only).

Pure logic, stdlib only (synthetic WAVs in tmp_path), no replay, no DB.
"""
from __future__ import annotations

import ast
import glob as _glob
import importlib.util
import json
import os
import sys
import time
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("vrp_clip_pinning", "scripts/maintenance/voice_regression_probe.py")
vgc = _load("vgc_clip_pinning", "scripts/maintenance/voice_gate_check.py")
T0 = 1_700_000_000


def _wav(path: Path, secs: float, mtime: float) -> None:
    n = int(16000 * secs)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x01\x00" * n)
    os.utime(path, (mtime, mtime))


def _corpus(root: Path, durations: list[float]) -> Path:
    d = root / "corpus"
    d.mkdir()
    for i, secs in enumerate(durations):
        _wav(d / f"{100000 + i:06d}_{i:03d}.wav", secs, T0 + i)
    return d


def _rows(names: list[str], stt_ms: int = 100) -> list[dict]:
    # verdict + route/heard hashes as measure_voice writes them: the per-clip checks (T-3) judge every pinned clip
    return [dict(file=n, stt_ms=stt_ms, verdict="OK", route_hash="r0", heard_hash="h0", via_brain=True) for n in names]


def _names(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.wav"))


def _report(rows: list[dict], stt_median: int = 100) -> dict:
    return dict(n_samples=len(rows), verdicts=dict(OK=len(rows)), memory_recall="ok", rows=rows,
                aggregate_ms=dict(stt_ms=dict(median=stt_median), brain_ms=dict(median=200),
                                  e2e_ms=dict(median=300)))


def _baseline_for(corpus: Path, names: list[str], stt_median: int = 100, stt_ms: int = 100) -> dict:
    rep = _report(_rows(names, stt_ms), stt_median)
    summ = dict(vrp.summarize(rep), **vrp.clip_set_summary(rep, str(corpus)))
    return dict(created_at="2026-10-01T00:00:00Z", summary=summ)


# -- ids ---------------------------------------------------------------------
def test_clip_id_is_stable_and_content_and_name_free():
    a = vrp.clip_id("123456_789.wav", 4096)
    assert a == vrp.clip_id("123456_789.wav", 4096)
    assert len(a) == vrp.CLIP_ID_LEN and all(c in "0123456789abcdef" for c in a)
    assert "123456" not in a and "wav" not in a
    assert a != vrp.clip_id("123456_789.wav", 4097)      # size participates
    assert a != vrp.clip_id("123456_790.wav", 4096)      # name participates


def test_baseline_records_ids_but_never_names(tmp_path):
    d = _corpus(tmp_path, [1.0, 2.0, 3.0])
    base = _baseline_for(d, _names(d))
    blob = json.dumps(base)
    assert len(base["summary"]["clip_ids"]) == 3
    assert ".wav" not in blob and "100000" not in blob


# -- the drift bug -----------------------------------------------------------
def test_pinned_set_survives_new_recordings_but_newest_n_does_not(tmp_path):
    d = _corpus(tmp_path, [1.0] * 6)
    pinned_names = _names(d)[:3]
    base = _baseline_for(d, pinned_names)
    before = vrp.plan_clip_set(base, str(d), False)
    for k in range(4):                                   # four NEW recordings land
        _wav(d / f"2{k:05d}_new.wav", 0.5, T0 + 1000 + k)
    after = vrp.plan_clip_set(base, str(d), False)
    assert before["mode"] == after["mode"] == "pinned"
    assert before["names"] == after["names"] == pinned_names
    # NEGATIVE CONTROL: the old selection (newest N by mtime) is NOT stable.
    newest = sorted(_names(d), key=lambda n: os.stat(d / n).st_mtime)[-3:]
    assert newest != pinned_names


def test_plan_without_baseline_takes_newest_and_may_compare(tmp_path):
    d = _corpus(tmp_path, [1.0] * 3)
    plan = vrp.plan_clip_set(dict(), str(d), False)
    assert plan["mode"] == "newest" and plan["speed_ok"] is True and plan["names"] is None


def test_update_baseline_repins_without_a_failure_reason(tmp_path):
    d = _corpus(tmp_path, [1.0] * 3)
    plan = vrp.plan_clip_set(_baseline_for(d, _names(d)), str(d), True)
    assert plan["mode"] == "newest" and plan["speed_ok"] is False and plan["warn"] is False


def test_legacy_unpinned_baseline_refuses_speed_loudly(tmp_path):
    d = _corpus(tmp_path, [1.0] * 3)
    legacy = dict(created_at="x", summary=vrp.summarize(_report(_rows(_names(d)))))
    plan = vrp.plan_clip_set(legacy, str(d), False)
    assert plan["speed_ok"] is False and plan["warn"] is True
    assert "no pinned clip set" in plan["note"] and "--update-baseline" in plan["note"]


def test_missing_pinned_clip_refuses_and_names_the_operator_step(tmp_path):
    d = _corpus(tmp_path, [1.0] * 4)
    base = _baseline_for(d, _names(d))
    (d / _names(d)[0]).unlink()
    plan = vrp.plan_clip_set(base, str(d), False)
    assert plan["mode"] == "missing" and plan["missing"] == 1 and plan["pinned"] == 4
    assert "refusing to compare speed" in plan["note"] and "--update-baseline" in plan["note"]


def test_resized_clip_is_a_different_clip(tmp_path):
    """name+size: a clip re-encoded under the same name is NOT the pinned clip."""
    d = _corpus(tmp_path, [1.0] * 3)
    base = _baseline_for(d, _names(d))
    _wav(d / _names(d)[1], 2.0, T0)                      # same name, different size
    assert vrp.plan_clip_set(base, str(d), False)["mode"] == "missing"


def test_quarantine_subdirectory_is_never_part_of_the_index(tmp_path):
    d = _corpus(tmp_path, [1.0] * 2)
    q = d / "quarantine-x"
    q.mkdir()
    _wav(q / "999999_000.wav", 1.0, T0)
    assert len(vrp.corpus_index(str(d))) == 2


# -- per-audio-second STT ----------------------------------------------------
def test_per_audio_second_is_length_normalised(tmp_path):
    """Same engine speed (200 ms of STT per audio second) over long clips vs short
    clips: the raw median differs ~4x, the per-second metric does not."""
    d = tmp_path / "c"
    d.mkdir()
    for i, secs in enumerate([4.0, 4.0, 1.0, 1.0]):
        _wav(d / f"{i}.wav", secs, T0 + i)
    long_rows = [dict(file="0.wav", stt_ms=800), dict(file="1.wav", stt_ms=800)]
    short_rows = [dict(file="2.wav", stt_ms=200), dict(file="3.wav", stt_ms=200)]
    assert vrp.stt_per_audio_second(long_rows, str(d)) == pytest.approx(200.0, abs=0.5)
    assert vrp.stt_per_audio_second(short_rows, str(d)) == pytest.approx(200.0, abs=0.5)
    assert vrp.stt_per_audio_second([dict(file="gone.wav", stt_ms=5)], str(d)) is None


def _cur(**kw):
    base = dict(ok_rate=1.0, fail=0, empty=0, total=4,
                medians_ms=dict(stt_ms=100, brain_ms=200, e2e_ms=300))
    base.update(kw)
    return base


def test_per_audio_second_regression_is_flagged_even_with_a_flat_median():
    base = dict(summary=_cur(stt_ms_per_audio_s=200.0))
    slow = _cur(stt_ms_per_audio_s=420.0)
    warns = vrp.compare(slow, base, 1.5, 1500)
    assert any(w.startswith("SPEED stt_ms_per_audio_s") for w in warns)
    # control: an unchanged rate, or a tiny absolute move, is not flagged
    assert vrp.compare(_cur(stt_ms_per_audio_s=205.0), base, 1.5, 1500) == []
    tiny = dict(summary=_cur(stt_ms_per_audio_s=10.0))
    assert vrp.compare(_cur(stt_ms_per_audio_s=30.0), tiny, 1.5, 1500) == []


def test_compare_speed_false_skips_speed_but_not_function():
    base = dict(summary=_cur(stt_ms_per_audio_s=200.0))
    slow = _cur(stt_ms_per_audio_s=900.0, medians_ms=dict(stt_ms=900, brain_ms=200, e2e_ms=300))
    assert vrp.compare(slow, base, 1.5, 1500)                                   # control: flagged
    assert vrp.compare(slow, base, 1.5, 1500, compare_speed=False) == []
    broke = _cur(ok_rate=0.5, fail=2)
    warns = vrp.compare(broke, base, 1.5, 1500, compare_speed=False)
    assert any(w.startswith("FUNCTION") for w in warns)


# -- end to end through main() -----------------------------------------------
def _drive(tmp_path, monkeypatch, corpus, baseline, replayed_rows, *, stt_median=100, extra=()):
    calls = []

    def fake_measure(*a, **k):
        calls.append(k.get("names"))
        return _report(replayed_rows, stt_median)

    monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
    monkeypatch.setattr(vrp, "mem_available_mb", lambda: 10_000)
    monkeypatch.setattr(vrp, "cleanup_replay_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(vrp, "service_revision", lambda *_: None)
    monkeypatch.setattr(vrp, "resolve_service_python", lambda x: (sys.executable, "test"))
    monkeypatch.setattr(vrp, "run_measure", fake_measure)
    bpath = tmp_path / "baseline.json"
    if baseline is not None:
        bpath.write_text(json.dumps(baseline))
    results = tmp_path / "last.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(bpath),
        "--trend", str(tmp_path / "t.jsonl"), "--service-dir", str(tmp_path),
        "--sample-dir", str(corpus), "--no-vad-check", "--no-cleanup", *extra])
    rc = vrp.main()
    return rc, json.loads(results.read_text()), json.loads(bpath.read_text()), calls


def test_main_replays_the_pinned_set_after_new_recordings_and_passes(tmp_path, monkeypatch):
    d = _corpus(tmp_path, [1.0] * 6)
    pinned = _names(d)[:4]
    base = _baseline_for(d, pinned)
    for k in range(3):
        _wav(d / f"3{k:05d}_new.wav", 0.4, T0 + 5000 + k)
    rc, art, _, calls = _drive(tmp_path, monkeypatch, d, base, _rows(pinned))
    assert calls == [pinned]                                 # exactly the pinned clips
    assert (rc, art["status"], art["speed_comparison"]) == (0, "pass", "compared")
    assert art["baseline_ref"]["clip_set_size"] == 4
    ok, why = vgc.evaluate(art, now_epoch=time.time(), max_age_s=24 * 3600, baseline=base)
    assert ok, why                                            # additive fields: deploy gate unaffected


def test_main_2x_stt_regression_on_the_pinned_set_fails(tmp_path, monkeypatch):
    d = _corpus(tmp_path, [1.0] * 4)
    names = _names(d)
    base = _baseline_for(d, names, stt_median=921, stt_ms=921)
    rc, art, _, _ = _drive(tmp_path, monkeypatch, d, base, _rows(names, 1900), stt_median=1900)
    assert rc == 1 and art["status"] == "fail"
    assert "SPEED stt_ms" in art["reason"]
    assert art["speed_comparison"] == "compared"


def test_main_missing_pinned_clip_errors_before_replaying(tmp_path, monkeypatch):
    d = _corpus(tmp_path, [1.0] * 4)
    names = _names(d)
    base = _baseline_for(d, names)
    (d / names[2]).unlink()
    rc, art, _, calls = _drive(tmp_path, monkeypatch, d, base, _rows(names))
    assert calls == []                                        # never replayed a different set
    assert rc == 2 and art["status"] == "error"
    assert "BASELINE-SET" in art["reason"] and art["speed_comparison"].startswith("refused")
    ok, _why = vgc.evaluate(art, now_epoch=time.time(), max_age_s=24 * 3600, baseline=base)
    assert ok is False


def test_main_legacy_baseline_is_not_a_speed_pass_and_update_baseline_fixes_it(tmp_path, monkeypatch):
    d = _corpus(tmp_path, [1.0] * 4)
    names = _names(d)
    legacy = dict(created_at="2026-09-01T00:00:00Z", summary=vrp.summarize(_report(_rows(names))))
    rc, art, after, _ = _drive(tmp_path, monkeypatch, d, legacy, _rows(names))
    assert rc == 1 and art["status"] == "fail" and "BASELINE-SET" in art["reason"]
    assert art["speed_comparison"].startswith("refused")
    assert "clip_ids" not in after["summary"]                 # a plain run never re-pins
    # the operator step: update-baseline pins the replayed set, then runs pass
    rc2, art2, pinned, _ = _drive(tmp_path, monkeypatch, d, legacy, _rows(names),
                                  extra=("--update-baseline",))
    assert rc2 == 0 and art2["status"] == "pass" and len(pinned["summary"]["clip_ids"]) == 4
    assert art2["speed_comparison"].startswith("skipped")
    rc3, art3, _, calls3 = _drive(tmp_path, monkeypatch, d, pinned, _rows(names))
    assert (rc3, art3["status"]) == (0, "pass") and calls3 == [names]


def test_main_replay_that_returns_a_different_set_is_not_compared(tmp_path, monkeypatch):
    d = _corpus(tmp_path, [1.0] * 5)
    names = _names(d)
    base = _baseline_for(d, names[:4])
    rc, art, _, _ = _drive(tmp_path, monkeypatch, d, base, _rows(names[1:]))   # swapped one clip
    assert rc == 1 and art["status"] == "fail" and "BASELINE-SET" in art["reason"]


# -- the replay harness side --------------------------------------------------
def _extract(fn_name: str):
    src = (REPO / "services" / "zoe-data" / "tests" / "replay_samples.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    ns: dict = dict(glob=_glob, os=os)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), fn_name, "exec"), ns)
    return ns[fn_name]


def test_replay_files_from_replays_exactly_the_named_clips_in_order(tmp_path):
    select_named = _extract("_select_named")
    d = _corpus(tmp_path, [1.0] * 4)
    names = _names(d)
    lst = tmp_path / "set.txt"
    lst.write_text("\n".join([names[2], names[0]]) + "\n")
    assert [os.path.basename(p) for p in select_named(str(d), str(lst))] == [names[2], names[0]]


def test_replay_files_from_fails_loudly_on_a_missing_or_nested_name(tmp_path):
    select_named = _extract("_select_named")
    d = _corpus(tmp_path, [1.0] * 2)
    lst = tmp_path / "set.txt"
    lst.write_text(_names(d)[0] + "\nnot_there.wav\n")
    with pytest.raises(ValueError, match="not in"):
        select_named(str(d), str(lst))
    lst.write_text("quarantine-x/../" + _names(d)[0] + "\n")
    with pytest.raises(ValueError, match="plain top-level"):
        select_named(str(d), str(lst))


def test_main_repin_run_is_not_judged_against_the_old_set_speed(tmp_path, monkeypatch):
    """A re-pin replays a DIFFERENT set, so its speed must not be compared with the
    old set's (that comparison is exactly the drift bug). Control: the identical
    slow numbers on the pinned set DO fail (see the 2x regression test)."""
    d = _corpus(tmp_path, [1.0] * 4)
    names = _names(d)
    base = _baseline_for(d, names, stt_median=100, stt_ms=100)
    rc, art, new_base, _ = _drive(tmp_path, monkeypatch, d, base, _rows(names, 1900),
                                  stt_median=1900, extra=("--update-baseline",))
    assert rc == 0 and art["status"] == "pass"
    assert art["speed_comparison"].startswith("skipped")
    assert new_base["summary"]["medians_ms"]["stt_ms"] == 1900
