"""The voice probe tolerates a FLAKY brain-wording verdict, and nothing else.

Incident (2026-10-15): the probe pins a fixed 20-clip set. One short, garbled clip
routes to chat, so the BRAIN's free-text reply decides its verdict, and that reply
varies run to run: sometimes it matches replay_samples._CANT_DO_RE ("can't tell
you..."). Same code scored OK, OK, CANT_DO, so a PR failed the gate ~1/3 of the time
with no regression at all.

Contract pinned here (synthetic replay reports, no real clip, no replay, no DB):
  * a baseline-OK clip that is CANT_DO *via the brain*, on the baseline's route and
    transcript, is re-run (<= 2 more times, only that clip). It is regressed only if
    it fails in >= 2 of its 3 runs; 1/3 passes and is recorded as flaky;
  * a deterministic regression fails EVERY run, so it still fails: 2/3 fails, and a
    route / tier / transcript change, an ERROR, or a non-brain CANT_DO fails on the
    FIRST run with no retry at all (negative controls);
  * more than 3 candidates, or a retry that cannot run / has no recall evidence / came
    back on a different route, is never tolerated;
  * the result JSON always carries flaky_retried / flaky_clips; the artifact's
    commit/tree binding is untouched; --update-baseline never retries.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
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


vrp = _load("vrp_flaky_retry", "scripts/maintenance/voice_regression_probe.py")
vgc = _load("vgc_flaky_retry", "scripts/maintenance/voice_gate_check.py")
mv = _load("mv_flaky_retry", "scripts/perf/measure_voice.py")
T0 = 1_700_000_000
BRAIN = "brain"
REV = dict(commit="a" * 40, tree="b" * 40, dirty=False, clean_verified=True, service_dir="/x")


def _corpus(root: Path, n: int) -> Path:
    d = root / "corpus"
    d.mkdir()
    for i in range(n):
        p = d / f"{100000 + i:06d}_{i:03d}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x01\x00" * (8000 + 100 * i))   # distinct sizes => distinct ids
        os.utime(p, (T0 + i, T0 + i))
    return d


def _names(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.wav"))


def _row(name: str, verdict: str = "OK", *, brain: bool = True, route: str | None = None,
         heard: str = "h0") -> dict:
    return dict(file=name, verdict=verdict, stt_ms=100, resolve_ms=5, brain_ms=200, e2e_ms=300,
                heard_hash=heard, via_brain=brain,
                route_hash=route if route is not None else (BRAIN if brain else "tier"))


def _report(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return dict(n_samples=len(rows), verdicts=counts, memory_recall="ok", rows=rows,
                aggregate_ms=dict(stt_ms=dict(median=100), brain_ms=dict(median=200),
                                  e2e_ms=dict(median=300)))


def _baseline(corpus: Path, names: list[str], *, profile: bool = True, rows=None) -> dict:
    rep = _report(rows or [_row(n) for n in names])
    summ = dict(vrp.summarize(rep), **vrp.clip_set_summary(rep, str(corpus)))
    if not profile:
        summ.pop("clip_profile")
    return dict(created_at="2026-10-14T00:00:00Z", summary=summ)


def _drive(tmp_path, monkeypatch, corpus, baseline, scripted, *, extra=(), revision=None):
    """scripted: one list of rows per run_measure call (first = the full run)."""
    calls: list = []

    def fake_measure(*a, **k):
        calls.append(k.get("names"))
        i = len(calls) - 1
        item = scripted[min(i, len(scripted) - 1)]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, dict) else _report(item)

    monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
    monkeypatch.setattr(vrp, "mem_available_mb", lambda: 10_000)
    monkeypatch.setattr(vrp, "cleanup_replay_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(vrp, "service_revision", lambda *_: revision)
    monkeypatch.setattr(vrp, "resolve_service_python", lambda x: (sys.executable, "test"))
    monkeypatch.setattr(vrp, "run_measure", fake_measure)
    bpath = tmp_path / "baseline.json"
    bpath.write_text(json.dumps(baseline))
    results = tmp_path / "last.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(bpath),
        "--trend", str(tmp_path / "t.jsonl"), "--service-dir", str(tmp_path),
        "--sample-dir", str(corpus), "--no-vad-check", "--no-cleanup", *extra])
    rc = vrp.main()
    return rc, json.loads(results.read_text()), calls


@pytest.fixture
def world(tmp_path):
    d = _corpus(tmp_path, 6)
    names = _names(d)
    return d, names, _baseline(d, names)


def _with(names, **by_index):
    """All-OK rows with per-index overrides: _with(names, **{"2": dict(verdict="CANT_DO")})"""
    rows = [_row(n) for n in names]
    for i, kw in by_index.items():
        rows[int(i)] = _row(names[int(i)], **kw)
    return rows


def _id(corpus, name):
    return vrp.clip_id(name, os.stat(corpus / name).st_size)


# -- the incident ------------------------------------------------------------
def test_flaky_clip_failing_one_of_three_passes_and_is_recorded(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    ok = [_row(names[2])]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok])
    assert (rc, art["status"]) == (0, "pass"), art
    assert calls == [names, [names[2]], [names[2]]]          # ONLY the failing clip was re-run, twice
    assert art["flaky_retried"] is True and len(art["flaky_clips"]) == 1
    c = art["flaky_clips"][0]
    assert c["clip_id"] == _id(d, names[2]) and c["runs"] == ["CANT_DO", "OK", "OK"]
    assert (c["failures"], c["of"], c["regressed"]) == (1, 3, False)
    assert "flaky: 1/3 CANT_DO (brain wording)" in c["note"] and "not counted" in c["note"]
    assert art["summary"]["ok_rate"] == 1.0 and art["said_vs_did_regressions"] == []
    assert names[2] not in json.dumps(art)                   # public repo: hashes only, never a clip name


def test_negative_control_without_the_tolerance_the_same_run_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    monkeypatch.setattr(vrp, "apply_flaky_tolerance",
                        lambda report, *a, **k: dict(retried=False, report=report, clips=[], note=""))
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail") and calls == [names]
    assert any(w.startswith("FUNCTION") for w in art["said_vs_did_regressions"])


def test_clip_failing_two_of_three_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, [_row(names[2])], [_row(names[2], "CANT_DO")]])
    assert (rc, art["status"]) == (1, "fail")
    assert len(calls) == 3
    c = art["flaky_clips"][0]
    assert c["runs"] == ["CANT_DO", "OK", "CANT_DO"] and c["regressed"] is True
    assert art["summary"]["ok_rate"] < 1.0 and art["said_vs_did_regressions"]


def test_deterministic_cant_do_fails_after_one_retry_not_three_runs(tmp_path, monkeypatch, world):
    d, names, base = world
    bad = [_row(names[2], "CANT_DO")]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [_with(names, **{"2": dict(verdict="CANT_DO")}), bad, bad])
    assert (rc, art["status"]) == (1, "fail")
    assert len(calls) == 2                                   # decided at 2/2: no pointless third run
    assert art["flaky_clips"][0]["regressed"] is True


# -- first-run hard failures (deterministic): never retried --------------------
@pytest.mark.parametrize("label,row_kw", [
    ("route/tier change", dict(verdict="CANT_DO", route="other-route")),
    ("STT transcript change", dict(verdict="CANT_DO", heard="changed")),
    ("non-brain CANT_DO (extractor empty)", dict(verdict="CANT_DO", brain=False, route="tier")),
    ("ERROR (brain lane dead / crash)", dict(verdict="ERROR")),
])
def test_deterministic_failures_fail_on_the_first_run_with_no_retry(
        tmp_path, monkeypatch, world, label, row_kw):
    d, names, base = world
    run1 = _with(names, **{"2": row_kw})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(names[2])], [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail"), label
    assert calls == [names], label                           # exactly one replay
    assert art["flaky_retried"] is False and art["flaky_clips"] == []


def test_hard_failure_alongside_a_flaky_one_is_not_retried(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"1": dict(verdict="CANT_DO"), "3": dict(verdict="ERROR")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(names[1])]])
    assert (rc, art["status"]) == (1, "fail") and calls == [names]
    assert "deterministic" in art["flaky_note"]


def test_more_than_three_failing_clips_fail_without_retrying(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{str(i): dict(verdict="CANT_DO") for i in range(4)})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(n) for n in names[:4]]])
    assert (rc, art["status"]) == (1, "fail") and calls == [names]
    assert art["flaky_retried"] is False and "cap" in art["flaky_note"]


def test_exactly_three_flaky_clips_are_retried_together(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{str(i): dict(verdict="CANT_DO") for i in range(3)})
    ok = [_row(n) for n in names[:3]]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok])
    assert (rc, art["status"]) == (0, "pass")
    assert calls == [names, names[:3], names[:3]]
    assert len(art["flaky_clips"]) == 3 and not any(c["regressed"] for c in art["flaky_clips"])


# -- tolerance needs POSITIVE evidence -------------------------------------------
def test_retry_that_cannot_run_is_never_tolerated(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [run1, RuntimeError("harness died")])
    assert (rc, art["status"]) == (1, "fail")
    assert "retry did not run" in art["flaky_note"] and art["flaky_clips"][0]["regressed"] is True


def test_retry_without_recall_evidence_is_never_tolerated(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    norecall = dict(_report([_row(names[2])]), memory_recall="error")
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [run1, norecall])
    assert (rc, art["status"]) == (1, "fail") and "recall" in art["flaky_note"]


def test_retry_ok_on_a_different_route_is_not_a_flaky_pass(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    moved = [_row(names[2], "OK", brain=False, route="tier")]
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [run1, moved, moved])
    assert (rc, art["status"]) == (1, "fail")
    assert art["flaky_clips"][0]["runs"] == ["CANT_DO", "ROUTE-CHANGED"]


def test_clip_already_failing_in_the_baseline_is_not_retried_or_a_regression(tmp_path, monkeypatch):
    d = _corpus(tmp_path, 6)
    names = _names(d)
    base = _baseline(d, names, rows=_with(names, **{"2": dict(verdict="CANT_DO")}))
    assert base["summary"]["fail"] == 1
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [_with(names, **{"2": dict(verdict="CANT_DO")})])
    assert (rc, art["status"]) == (0, "pass") and calls == [names]
    assert art["flaky_retried"] is False


def test_legacy_baseline_without_a_clip_profile_still_gets_the_tolerance(tmp_path, monkeypatch):
    d = _corpus(tmp_path, 6)
    names = _names(d)
    legacy = _baseline(d, names, profile=False)
    assert "clip_profile" not in legacy["summary"]
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    ok = [_row(names[2])]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, legacy, [run1, ok, ok])
    assert len(calls) == 3 and art["flaky_clips"][0]["regressed"] is False   # the tolerance still applies...
    # ...but since T-3 a baseline with no per-clip profile judges no clip, and judging nothing is never a pass
    assert (rc, art["status"]) == (1, "fail")
    assert any("no pinned clip has a per-clip expectation" in w for w in art["said_vs_did_regressions"])
    # ...but a deterministic non-brain failure on a legacy baseline (which had 0 fails) is hard
    rc2, art2, calls2 = _drive(tmp_path, monkeypatch, d, legacy,
                               [_with(names, **{"2": dict(verdict="CANT_DO"), "4": dict(verdict="ERROR")}), ok])
    assert (rc2, art2["status"]) == (1, "fail") and calls2 == [names]


def test_update_baseline_never_retries_and_records_first_run_truth(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(names[2])]],
                            extra=("--update-baseline",))
    assert calls == [None] and art["flaky_retried"] is False
    saved = json.loads((tmp_path / "baseline.json").read_text())["summary"]
    assert saved["clip_profile"][_id(d, names[2])]["verdict"] == "CANT_DO"
    assert saved["verdicts"].get("CANT_DO") == 1


# -- artifact contract --------------------------------------------------------
def test_result_json_always_carries_the_flaky_fields(tmp_path, monkeypatch, world):
    d, names, base = world
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [[_row(n) for n in names]])
    assert (rc, art["status"]) == (0, "pass") and calls == [names]
    assert art["flaky_retried"] is False and art["flaky_clips"] == [] and art["flaky_note"] == ""
    # and on a path that never replays at all
    out = vrp.emit_result(types.SimpleNamespace(results=tmp_path / "x.json", trend=tmp_path / "x.jsonl",
                                                baseline=tmp_path / "b.json", service_dir=None),
                          status="skip", summary=dict(vrp.EMPTY_SUMMARY), said_vs_did=[],
                          speed_deltas={}, baseline={})
    assert out["flaky_retried"] is False and out["flaky_clips"] == []


def test_artifact_commit_and_tree_binding_is_unchanged_by_retries(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    ok = [_row(names[2])]
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok], revision=dict(REV))
    assert (rc, art["status"]) == (0, "pass") and art["revision"] == REV
    now = time.time()
    good, why = vgc.evaluate(art, now_epoch=now, max_age_s=3600, baseline=None, expect_revision=REV["commit"])
    assert good, why
    assert "1 flaky brain-wording clip(s) tolerated" in why
    bad, _ = vgc.evaluate(art, now_epoch=now, max_age_s=3600, baseline=None, expect_revision="c" * 40)
    assert bad is False                                      # still bound to the commit it exercised
    bad_tree, _ = vgc.evaluate(art, now_epoch=now, max_age_s=3600, baseline=None,
                               expect_tree_of=("d" * 40, "e" * 40))
    assert bad_tree is False
    dirty = dict(art, revision=dict(REV, dirty=True))
    bad_dirty, _ = vgc.evaluate(dirty, now_epoch=now, max_age_s=3600, baseline=None,
                                expect_revision=REV["commit"])
    assert bad_dirty is False


def test_a_regressed_clip_does_not_show_as_tolerated_in_the_gate_message(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [run1, [_row(names[2], "CANT_DO")]])
    assert art["status"] == "fail"
    # forced past BOTH the status and the per-clip block (which would refuse the failed clip on its own)
    forced = dict(art, status="pass", clip_checks=False)
    ok, why = vgc.evaluate(forced, now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert ok and "tolerated" not in why                     # only non-regressed clips are reported tolerated


# -- the replay rows carry the identity the tolerance needs --------------------
def test_measure_voice_rows_carry_content_free_route_identity(tmp_path, monkeypatch):
    replay = tmp_path / "replay.json"
    rows = [dict(file="a.wav", verdict="OK", stt_ms=10, resolve_ms=1, brain_ms=5, outcome="brain",
                 transcript="turn the lamp on please"),
            dict(file="b.wav", verdict="OK", stt_ms=10, resolve_ms=1, outcome="tier1:lights:on",
                 transcript="x")]
    replay.write_text(json.dumps(dict(counts=dict(OK=2), rows=rows, memory_recall="ok")))
    out = tmp_path / "out.json"
    monkeypatch.setattr(mv.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    rc = mv._run_and_report(["x"], str(tmp_path), str(replay), types.SimpleNamespace(timeout=5, json=str(out)))
    assert rc == 0
    got = json.loads(out.read_text())["rows"]
    assert (got[0]["via_brain"], got[1]["via_brain"]) == (True, False)
    assert got[0]["route_hash"] != got[1]["route_hash"] and len(got[0]["route_hash"]) == 8
    blob = json.dumps(got)
    assert "lamp" not in blob and "lights" not in blob       # hashes only, never the outcome label or text
