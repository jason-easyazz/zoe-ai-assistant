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
    ("STT transcript change", dict(verdict="CANT_DO", heard="changed")),
    ("STT transcript change on a non-brain CANT_DO", dict(verdict="CANT_DO", heard="changed", brain=False,
                                                          route="tier")),
    ("ERROR with a changed transcript", dict(verdict="ERROR", heard="changed")),
    ("ERROR with no transcript at all", dict(verdict="ERROR", heard=None)),
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
    run1 = _with(names, **{"1": dict(verdict="CANT_DO"), "3": dict(verdict="CANT_DO", heard="changed")})
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


# -- a BLANK retry is inconclusive (owner decision 2026-10-20) ----------------------
# Pattern in 6 of 65 runs: first run CANT_DO or EMPTY, the flaky retry then heard
# nothing (EMPTY) and the clip counted as regressed on an unrelated PR. A blank retry
# is re-run ONCE; only a non-blank result counts; a second blank fails as before.
def _blank(names):
    return [_row(names[2], "EMPTY", heard="")]


@pytest.mark.parametrize("first", ["CANT_DO", "EMPTY"])
def test_blank_retry_then_ok_passes_and_is_flagged(tmp_path, monkeypatch, world, first):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict=first)})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, _blank(names), [_row(names[2])]])
    assert (rc, art["status"]) == (0, "pass"), art
    assert calls == [names, [names[2]], [names[2]]]          # the blank retry re-run exactly once
    c = art["flaky_clips"][0]
    assert c["runs"] == [first, "EMPTY", "OK"] and c["regressed"] is False
    assert c["retry_empty_rerun"] is True and (c["failures"], c["of"]) == (1, 2)
    assert "blank retry was re-run once" in c["note"]
    assert art["said_vs_did_regressions"] == []


def test_blank_retry_then_blank_again_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, _blank(names), _blank(names), [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail")
    assert len(calls) == 3                                   # no third retry: the OK scripted last is never reached
    c = art["flaky_clips"][0]
    assert c["runs"] == ["CANT_DO", "EMPTY", "EMPTY"] and c["regressed"] is True
    assert c["retry_empty_rerun"] is True


def test_blank_retry_then_cant_do_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, _blank(names), [_row(names[2], "CANT_DO")], [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 3
    assert art["flaky_clips"][0]["runs"] == ["CANT_DO", "EMPTY", "CANT_DO"]
    assert art["flaky_clips"][0]["regressed"] is True and art["said_vs_did_regressions"]


def test_negative_control_real_regressions_fail_exactly_as_before(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    # a retry that CANT_DOs (no blank anywhere): decided at 2 runs, no extra retry, no flag
    bad = [_row(names[2], "CANT_DO")]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, bad, [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 2
    c = art["flaky_clips"][0]
    assert c["runs"] == ["CANT_DO", "CANT_DO"] and c["retry_empty_rerun"] is False
    # a retry OK on the WRONG route is a failure, and does not earn the blank's extra retry
    moved = [_row(names[2], "OK", brain=False, route="tier")]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, moved, moved])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 2
    assert art["flaky_clips"][0]["runs"] == ["CANT_DO", "ROUTE-CHANGED"]
    assert art["flaky_clips"][0]["retry_empty_rerun"] is False
    # a first-run change of TRANSCRIPT is still never retried at all
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [_with(names, **{"2": dict(verdict="CANT_DO", heard="other")}), _blank(names)])
    assert (rc, art["status"]) == (1, "fail") and calls == [names]
    # a route change on the SAME transcript is retried (2026-10-27) but never forgiven: blanks do not pass it
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [_with(names, **{"2": dict(verdict="CANT_DO", route="other-route")}), _blank(names)])
    assert (rc, art["status"]) == (1, "fail") and len(calls) >= 2


def test_extra_retry_for_a_blank_happens_at_most_once_per_clip(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO")})
    # blank forever: the old budget of two retries, never more
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, _blank(names)])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 3
    # the worst case is OK, blank, then blank: the hard ceiling of THREE retries per clip
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, [_row(names[2])], _blank(names), _blank(names), [_row(names[2])]])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 4
    assert art["flaky_clips"][0]["runs"] == ["CANT_DO", "OK", "EMPTY", "EMPTY"]
    # and OK, blank, OK passes inside the same ceiling
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, [_row(names[2])], _blank(names), [_row(names[2])]])
    assert (rc, art["status"]) == (0, "pass") and len(calls) == 4
    assert art["flaky_clips"][0]["retry_empty_rerun"] is True


def test_blank_tolerance_unit_counts_calls_directly(world):
    d, names, base = world
    rep = _report(_with(names, **{"2": dict(verdict="CANT_DO")}))
    seen: list = []

    def rerun(ns):
        seen.append(list(ns))
        return _report([_row(n, "EMPTY", heard="") for n in ns])

    out = vrp.apply_flaky_tolerance(rep, base, rerun, str(d))
    assert len(seen) == 2 and out["clips"][0]["regressed"] is True      # the old two retries, nothing more


# -- same transcript, any CANT_DO/ERROR: retried, majority decides (2026-10-27) ---------
# Incident: a clip CANT_DO on one run in three with the STT identical to the baseline's.
# Only brain wording on the baseline route used to be retried; the same heard hash on
# any other route / as an ERROR was a hard first-run failure.
def test_same_heard_hash_cant_do_on_another_route_flaky_passes_after_retry(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO", brain=False, route="tier")})
    ok = [_row(names[2])]                                    # back on the baseline's brain route
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok])
    assert (rc, art["status"]) == (0, "pass"), art
    assert calls == [names, [names[2]], [names[2]]]
    c = art["flaky_clips"][0]
    assert (c["failures"], c["of"], c["regressed"]) == (1, 3, False)


def test_same_heard_hash_error_flaky_passes_after_retry(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="ERROR")})
    ok = [_row(names[2])]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok])
    assert (rc, art["status"]) == (0, "pass") and len(calls) == 3
    assert art["flaky_clips"][0]["runs"] == ["ERROR", "OK", "OK"]


@pytest.mark.parametrize("first_kw", [
    dict(verdict="CANT_DO"),
    dict(verdict="CANT_DO", brain=False, route="tier"),
    dict(verdict="ERROR"),
])
def test_same_heard_hash_consistent_failure_still_fails(tmp_path, monkeypatch, world, first_kw):
    d, names, base = world
    bad = [_row(names[2], first_kw["verdict"], brain=first_kw.get("brain", True),
                route=first_kw.get("route"))]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [_with(names, **{"2": first_kw}), bad, bad])
    assert (rc, art["status"]) == (1, "fail")
    assert len(calls) == 2                                   # decided at 2/2
    assert art["flaky_clips"][0]["regressed"] is True and art["said_vs_did_regressions"]


def test_same_heard_hash_failing_in_the_majority_of_runs_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO", brain=False, route="tier")})
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base,
                            [run1, [_row(names[2])], [_row(names[2], "CANT_DO")]])
    assert (rc, art["status"]) == (1, "fail") and len(calls) == 3
    assert art["flaky_clips"][0]["runs"] == ["CANT_DO", "OK", "CANT_DO"]


def test_changed_heard_hash_cant_do_is_never_retried_even_if_a_retry_would_pass(tmp_path, monkeypatch, world):
    d, names, base = world
    run1 = _with(names, **{"2": dict(verdict="CANT_DO", heard="other-transcript")})
    ok = [_row(names[2])]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [run1, ok, ok])
    assert (rc, art["status"]) == (1, "fail") and calls == [names]
    assert art["flaky_retried"] is False


# -- the dirty-tree verdict (revision.dirty) ---------------------------------------
def _vcs(repo: Path, *a: str) -> None:
    import subprocess
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "wt"
    sd = r / "services" / "zoe-data"
    (sd / "data").mkdir(parents=True)
    (r / ".gitignore").write_text("services/zoe-data/data/router_head_shadow.jsonl\n")
    (sd / ".gitignore").write_text(".env\n__pycache__/\n")
    (sd / "app.py").write_text("x = 1\n")
    _vcs(r, "init", "-q")
    _vcs(r, "add", "-A")
    _vcs(r, "commit", "-q", "-m", "init")
    return r, sd


def test_clean_checkout_is_not_dirty(repo):
    r, sd = repo
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is False and rev["clean_verified"] is True and rev["dirty_paths"] == []


def test_ignored_env_and_pycache_do_not_mark_the_tree_dirty(repo):
    r, sd = repo
    (sd / ".env").write_text("SECRET=1\n")
    (sd / "__pycache__").mkdir()
    (sd / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\0")
    assert vrp.service_revision(sd)["dirty"] is False


def test_unignored_replay_runtime_artifacts_do_not_mark_the_tree_dirty(repo):
    # PR #2110: the router shadow log's .lock + rotated segments were not in .gitignore
    r, sd = repo
    (sd / "data" / "router_head_shadow.jsonl.lock").write_text("")
    (sd / "data" / "router_head_shadow.jsonl.1").write_text("{}\n")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is False
    assert sorted(rev["runtime_artifacts_ignored"]) == [
        "services/zoe-data/data/router_head_shadow.jsonl.1",
        "services/zoe-data/data/router_head_shadow.jsonl.lock"]


def test_modified_tracked_file_marks_the_tree_dirty(repo):
    r, sd = repo
    (sd / "app.py").write_text("x = 2\n")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is True and rev["dirty_paths"] == ["services/zoe-data/app.py"]


def test_staged_deleted_and_untracked_source_files_mark_the_tree_dirty(repo):
    r, sd = repo
    (sd / "new_module.py").write_text("y = 1\n")             # untracked, NOT a known runtime artifact
    assert vrp.service_revision(sd)["dirty"] is True
    (sd / "new_module.py").unlink()
    (sd / "app.py").unlink()
    assert vrp.service_revision(sd)["dirty"] is True
    _vcs(r, "checkout", "--", "services/zoe-data/app.py")
    (sd / "app.py").write_text("x = 3\n")
    _vcs(r, "add", "services/zoe-data/app.py")
    assert vrp.service_revision(sd)["dirty"] is True


def test_runtime_artifact_beside_a_real_modification_is_still_dirty(repo):
    r, sd = repo
    (sd / "data" / "router_head_shadow.jsonl.lock").write_text("")
    (sd / "app.py").write_text("x = 9\n")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is True and rev["dirty_paths"] == ["services/zoe-data/app.py"]


def test_tracked_runtime_artifact_path_modified_is_still_dirty(repo):
    # the allowlist only forgives UNTRACKED files: a committed-then-edited path is a real change
    r, sd = repo
    (sd / "data" / "router_head_shadow.jsonl.1").write_text("a\n")
    _vcs(r, "add", "-f", "services/zoe-data/data/router_head_shadow.jsonl.1")
    _vcs(r, "commit", "-q", "-m", "track it")
    (sd / "data" / "router_head_shadow.jsonl.1").write_text("b\n")
    assert vrp.service_revision(sd)["dirty"] is True


# -- review round: the runtime-artifact patterns are narrow, the -z parser is exact ---------------
def _untracked(repo, rel: str, data: bytes = b"x") -> None:
    p = repo[0] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_root_pycache_pyc_is_a_runtime_artifact(repo):
    _untracked(repo, "__pycache__/mod.cpython-312.pyc")
    _untracked(repo, "pkg/__pycache__/mod.cpython-312.pyc")
    rev = vrp.service_revision(repo[1])
    assert rev["dirty"] is False
    assert sorted(rev["runtime_artifacts_ignored"]) == ["__pycache__/mod.cpython-312.pyc",
                                                        "pkg/__pycache__/mod.cpython-312.pyc"]


def test_pyc_outside_pycache_is_dirty(repo):
    _untracked(repo, "scratch.pyc")
    _untracked(repo, "services/zoe-data/helper.pyc")
    rev = vrp.service_revision(repo[1])
    assert rev["dirty"] is True
    assert sorted(rev["dirty_paths"]) == ["scratch.pyc", "services/zoe-data/helper.pyc"]


def test_non_pyc_file_inside_pycache_is_dirty(repo):
    _untracked(repo, "pkg/__pycache__/notes.txt")
    _untracked(repo, "__pycache__/payload.py")
    rev = vrp.service_revision(repo[1])
    assert rev["dirty"] is True
    assert sorted(rev["dirty_paths"]) == ["__pycache__/payload.py", "pkg/__pycache__/notes.txt"]


def test_nested_path_under_pycache_and_lookalike_names_are_dirty(repo):
    _untracked(repo, "__pycache__/sub/deep.pyc")                  # not a direct child
    _untracked(repo, "notpycache/x.pyc")
    _untracked(repo, "services/zoe-data/data/router_head_shadow.jsonl.bak")
    rev = vrp.service_revision(repo[1])
    assert rev["dirty"] is True and len(rev["dirty_paths"]) == 3


def test_real_staged_rename_is_dirty_and_consumes_its_source_field(repo):
    r, sd = repo
    _untracked(repo, "services/zoe-data/data/router_head_shadow.jsonl.lock", b"")   # forgiven, after the rename
    _vcs(r, "mv", "services/zoe-data/app.py", "services/zoe-data/app2.py")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is True
    assert rev["dirty_paths"] == ["services/zoe-data/app2.py"]            # source path is not a second entry
    assert rev["runtime_artifacts_ignored"] == ["services/zoe-data/data/router_head_shadow.jsonl.lock"]


@pytest.mark.parametrize("code", ["R ", " R", "RM", "MR", "C ", " C", "CM", "MC", "RR", "CC"])
def test_rename_and_copy_in_either_column_consume_the_source_field(code):
    # `git status --porcelain -z`: "XY to\0from\0" when X or Y is R or C. The source here is the
    # NAME of a runtime artifact: it must NOT be read as its own entry (and forgiven or listed).
    out = (f"{code} new_name.py\0services/zoe-data/data/router_head_shadow.jsonl.lock\0"
           "?? services/zoe-data/data/router_head_shadow.jsonl.1\0")
    dirty, runtime = vrp._classify_status(out)
    assert dirty == ["new_name.py"]
    assert runtime == ["services/zoe-data/data/router_head_shadow.jsonl.1"]


@pytest.mark.parametrize("bad", [
    "garbage\0",                       # no XY and separator
    "XYnew.py\0",                      # missing the separating space
    "?? \0",                           # empty path
    "R  to.py\0",                      # rename with no source field
    "!!\0",
])
def test_unparseable_status_fails_dirty(bad):
    dirty, runtime = vrp._classify_status(bad)
    assert dirty and runtime == []


def test_empty_status_is_clean():
    assert vrp._classify_status("") == ([], [])


def test_unreadable_git_status_is_dirty_and_unverified(repo, monkeypatch):
    real = vrp.subprocess.run

    def fake(cmd, *a, **k):
        if "status" in cmd:
            return vrp.subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: bad index")
        return real(cmd, *a, **k)

    monkeypatch.setattr(vrp.subprocess, "run", fake)
    rev = vrp.service_revision(repo[1])
    assert rev["dirty"] is True and rev["clean_verified"] is False
    assert rev["dirty_paths"] == [] and rev["runtime_artifacts_ignored"] == []


def test_dirty_and_ignored_lists_are_capped_at_twenty_but_dirty_uses_the_full_list(repo):
    r, sd = repo
    for i in range(25):
        _untracked(repo, f"extra_{i:02d}.py")
        _untracked(repo, f"pkg/__pycache__/m{i:02d}.cpython-312.pyc")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is True
    assert len(rev["dirty_paths"]) == 20 and len(rev["runtime_artifacts_ignored"]) == 20


def test_cap_never_hides_a_dirty_path_behind_runtime_artifacts(repo):
    r, sd = repo
    for i in range(25):
        _untracked(repo, f"pkg/__pycache__/m{i:02d}.cpython-312.pyc")
    _untracked(repo, "zzz_last.py")
    rev = vrp.service_revision(sd)
    assert rev["dirty"] is True and rev["dirty_paths"] == ["zzz_last.py"]


# -- review round 2: the PRODUCTION .gitignore, and undecodable names -----------------------------
@pytest.mark.parametrize("name,ignored", [
    ("router_head_shadow.jsonl", True),
    ("router_head_shadow.jsonl.lock", True),
    ("router_head_shadow.jsonl.1", True),
    ("router_head_shadow.jsonl.12", True),
    ("router_head_shadow.jsonl.keep", False),
    ("router_head_shadow.jsonl.orig", False),
    ("router_head_shadow.jsonl.1x", False),
    ("router_head_shadow.jsonl.123", False),
])
def test_production_gitignore_hides_only_the_shadow_log_files(name, ignored):
    import subprocess
    proc = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", f"services/zoe-data/data/{name}"],
                          capture_output=True)
    assert (proc.returncode == 0) is ignored, name


def test_untracked_file_with_undecodable_name_is_dirty_not_a_crash(repo, monkeypatch):
    r, sd = repo
    monkeypatch.setenv("LC_ALL", "C")
    try:
        (r.__class__(os.fsdecode(os.fsencode(str(r)) + b"/bad\xff.py"))).write_bytes(b"x")
    except OSError:
        pytest.skip("filesystem rejects non-UTF-8 names")
    rev = vrp.service_revision(sd)
    assert rev is not None and rev["dirty"] is True and rev["clean_verified"] is True
    assert len(rev["dirty_paths"]) == 1 and rev["dirty_paths"][0].startswith("bad")


def test_unterminated_status_output_fails_dirty_even_for_an_allowlisted_tail():
    tail = "?? services/zoe-data/data/router_head_shadow.jsonl.lock"
    dirty, runtime = vrp._classify_status(tail)                   # no trailing NUL: truncated
    assert dirty and runtime == []
    dirty, runtime = vrp._classify_status(tail + "\0")            # terminated: forgiven
    assert dirty == [] and runtime == ["services/zoe-data/data/router_head_shadow.jsonl.lock"]
    dirty, runtime = vrp._classify_status("?? a.py\0" + tail)     # earlier records fine, tail truncated
    assert "a.py" in dirty and any("unterminated" in d for d in dirty) and runtime == []
