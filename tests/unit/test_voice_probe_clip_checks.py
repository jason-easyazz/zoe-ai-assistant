"""T-3 (deep dive 2, 2026-10-19): the voice gate judges WHAT each pinned clip did, and proves it can fail.

Before: "OK" meant "not empty, not an error, not a refusal phrase" (replay_samples._classify), and the per-clip
route hash was read only for clips that had already failed, so a confident wrong action passed. Pinned here, on
synthetic replay reports (no corpus, no replay, no DB, no brain):

  * every pinned clip with an expectation must stay heard, OK and on its route (the legacy route hash, or the
    full signature once the baseline carries one), and owner labels are checked field by field;
  * one failing clip fails the run; a pinned set with NO expectation judged nothing and is never a pass;
  * a transient EMPTY on a baseline-OK clip gets the same 2-of-3 tolerance as brain wording, a persistent one fails;
  * the artifact carries per-clip results with ids, hashes and code labels only - never a clip name - and the
    deploy checker re-derives the verdict from them;
  * --self-test replays with injected faults and exits 0 only when EVERY fault went red; a checker that stays green
    on a fault fails the self-test (the negative control of the negative control); the self-test never touches the
    shared artifact, and a normal run strips / refuses an injected fault.
"""
from __future__ import annotations

import ast
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


vrp = _load("vrp_clip_checks", "scripts/maintenance/voice_regression_probe.py")
vgc = _load("vgc_clip_checks", "scripts/maintenance/voice_gate_check.py")
vcc = sys.modules["voice_clip_checks"]
mv = _load("mv_clip_checks", "scripts/perf/measure_voice.py")
T0 = 1_700_000_000

FAST = dict(lane="fast", tier="tier1", domain="lists", intent="add_item", router_domain="lists",
            router_tool="add_to_list", tier0=None)
BRAIN = dict(lane="brain", tier=None, domain=None, intent=None, router_domain="chat", router_tool=None, tier0=None)


def _corpus(root: Path, n: int) -> Path:
    d = root / "corpus"
    d.mkdir()
    for i in range(n):
        p = d / f"{100000 + i:06d}_{i:03d}.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x01\x00" * (8000 + 100 * i))
        os.utime(p, (T0 + i, T0 + i))
    return d


def _names(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.wav"))


def _row(name: str, verdict: str = "OK", labels: dict | None = None, *, heard: str = "h0", sig: bool = True) -> dict:
    """A measure_voice row: the legacy route hash (of the outcome label) AND, from a T-3 replay, the labels + sig."""
    lab = dict(labels or BRAIN)
    outcome = "brain" if lab["lane"] == "brain" else f"{lab['tier']}:{lab['domain']}:{lab['intent']}"
    if verdict == "EMPTY":
        outcome, lab = "→ brain", dict(BRAIN, lane="deferred")
    row = dict(file=name, verdict=verdict, stt_ms=100, resolve_ms=5, brain_ms=200, e2e_ms=300,
               heard_hash=heard, via_brain=outcome == "brain",
               route_hash=mv.hashlib.sha1(outcome.encode()).hexdigest()[:8])
    if sig:
        row.update(route=lab, route_sig=mv._sig(lab))
    return row


def _report(rows: list[dict], fault: str | None = None) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return dict(n_samples=len(rows), verdicts=counts, memory_recall="ok", rows=rows, fault=fault,
                aggregate_ms=dict(stt_ms=dict(median=100), brain_ms=dict(median=200), e2e_ms=dict(median=300)))


def _labels_for(names: list[str]) -> list[dict]:
    """Clips 0-1 are fast-path list adds, the rest go to the brain (the real pinned set's shape)."""
    return [FAST if i < 2 else BRAIN for i in range(len(names))]


def _baseline(corpus: Path, names: list[str], *, sig: bool = True, rows=None) -> dict:
    rep = _report(rows or [_row(n, labels=lab, sig=sig) for n, lab in zip(names, _labels_for(names))])
    return dict(created_at="2026-10-19T00:00:00Z", summary=dict(vrp.summarize(rep), **vrp.clip_set_summary(rep, str(corpus))))


def _good(names):
    return [_row(n, labels=lab) for n, lab in zip(names, _labels_for(names))]


def _drive(tmp_path, monkeypatch, corpus, baseline, scripted, *, extra=(), env_fault=None):
    """scripted: a list of reports/row-lists (one per run_measure call) or a callable(fault) -> report."""
    calls: list = []

    def fake_measure(*a, **k):
        fault = os.environ.get(vrp.FAULT_ENV)
        calls.append((k.get("names"), fault))
        if callable(scripted):
            return scripted(fault)
        item = scripted[min(len(calls) - 1, len(scripted) - 1)]
        return item if isinstance(item, dict) else _report(item)

    monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
    monkeypatch.setattr(vrp, "mem_available_mb", lambda: 10_000)
    monkeypatch.setattr(vrp, "cleanup_replay_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(vrp, "service_revision", lambda *_: None)
    monkeypatch.setattr(vrp, "resolve_service_python", lambda x: (sys.executable, "test"))
    monkeypatch.setattr(vrp, "run_measure", fake_measure)
    if env_fault:
        monkeypatch.setenv(vrp.FAULT_ENV, env_fault)
    bpath = tmp_path / "baseline.json"
    bpath.write_text(json.dumps(baseline))
    results = tmp_path / "last.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(bpath),
        "--trend", str(tmp_path / "t.jsonl"), "--service-dir", str(tmp_path),
        "--sample-dir", str(corpus), "--no-vad-check", "--no-cleanup",
        "--self-test-results", str(tmp_path / "selftest.json"), *extra])
    rc = vrp.main()
    art = json.loads(results.read_text()) if results.exists() else None
    return rc, art, calls


@pytest.fixture
def world(tmp_path):
    d = _corpus(tmp_path, 6)
    names = _names(d)
    return d, names, _baseline(d, names)


def _cid(corpus, name):
    return vrp.clip_id(name, os.stat(corpus / name).st_size)


# ── the per-clip check itself (pure) ─────────────────────────────────────────
def _prof(labels=FAST, verdict="OK", sig=True):
    return vcc.profile_entry(_row("x.wav", verdict, labels, sig=sig))


@pytest.mark.parametrize("label,row,base,result,kind", [
    ("same route, full signature", _row("x", labels=FAST), _prof(), "pass", None),
    ("same route, legacy hash", _row("x", labels=FAST, sig=False), _prof(sig=False), "pass", None),
    ("fast clip now answered by the brain", _row("x", labels=BRAIN), _prof(), "fail", "route"),
    ("legacy: fast clip now brain", _row("x", labels=BRAIN, sig=False), _prof(sig=False), "fail", "route"),
    ("router tool changed, lane unchanged", _row("x", labels=dict(FAST, router_tool="show_list")), _prof(),
     "fail", "route"),
    ("newly EMPTY", _row("x", "EMPTY"), _prof(), "fail", "stt-empty"),
    ("CANT_DO", _row("x", "CANT_DO", labels=FAST), _prof(), "fail", "verdict"),
    ("not OK in the baseline: nothing to hold", _row("x", labels=BRAIN), _prof(verdict="CANT_DO"), "unchecked", None),
])
def test_check_clip(label, row, base, result, kind):
    r = vcc.check_clip("abc", row, base, None)
    assert r["result"] == result, (label, r)
    assert r.get("kind") == kind


def test_a_signature_baseline_compares_the_signature_and_names_the_changed_fields():
    r = vcc.check_clip("abc", _row("x", labels=dict(FAST, tier0="list_add")), _prof(), None)
    assert (r["result"], r["compared"], r["changed_fields"]) == ("fail", "route_sig", ["tier0"])


def test_owner_labels_override_the_baseline_field_by_field():
    owner = {"lane": "fast", "domain": "lists"}
    assert vcc.check_clip("abc", _row("x", labels=FAST), _prof(BRAIN), owner)["result"] == "pass"
    bad = vcc.check_clip("abc", _row("x", labels=BRAIN), _prof(BRAIN), owner)
    assert (bad["result"], bad["kind"], bad["expected_from"]) == ("fail", "label", "owner")
    assert "lane" in bad["why"] and "domain" in bad["why"]
    # an owner label is checked even when the baseline had the clip failing
    assert vcc.check_clip("abc", _row("x", labels=BRAIN), _prof(verdict="CANT_DO"), owner)["result"] == "fail"
    # ...and it needs labels to check against (a pre-T-3 replay records none)
    assert vcc.check_clip("abc", _row("x", labels=FAST, sig=False), None, owner)["kind"] == "labels-missing"


def test_a_pinned_clip_that_was_not_replayed_fails():
    assert vcc.check_clip("abc", None, _prof(), None)["kind"] == "missing"


def test_load_expectations(tmp_path):
    p = tmp_path / "e.json"
    assert vcc.load_expectations(p) == ({}, None)                       # absent: no labels, no problem
    p.write_text("{nope")
    assert vcc.load_expectations(p)[1] and "unreadable" in vcc.load_expectations(p)[1]
    p.write_text(json.dumps({"clips": {"a" * 12: {"lane": "fast", "colour": "red"}}}))
    assert "unknown field" in vcc.load_expectations(p)[1]
    p.write_text(json.dumps({"clips": {"a" * 12: {"note": "only a note"}}}))
    assert "labels nothing" in vcc.load_expectations(p)[1]
    p.write_text(json.dumps({"version": 1, "clips": {"a" * 12: {"lane": "brain", "label_source": "owner"}}}))
    assert vcc.load_expectations(p) == ({"a" * 12: {"lane": "brain", "label_source": "owner"}}, None)


# ── through main(): the run, the artifact, the gate ──────────────────────────
def test_clean_run_passes_and_the_artifact_shows_every_clip(tmp_path, monkeypatch, world):
    d, names, base = world
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_good(names)])
    assert (rc, art["status"]) == (0, "pass"), art["reason"]
    assert art["clip_checks"] is True and art["self_test"] is False
    assert art["clip_summary"]["checked"] == 6 and art["clip_summary"]["failed"] == 0
    assert art["clip_summary"]["compared"] == ["route_sig"]
    assert {r["clip_id"] for r in art["clip_results"]} == {_cid(d, n) for n in names}
    assert not any(n in json.dumps(art) for n in names)                # public repo: never a clip name
    ok, why = vgc.evaluate(art, now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert ok and "6/6 clips held their expected outcome" in why


def test_a_confident_wrong_action_now_fails_the_gate(tmp_path, monkeypatch, world):
    """THE finding: clip 0 (a list add) is answered by the brain - OK verdict, no refusal - and used to pass."""
    d, names, base = world
    rows = _good(names)
    rows[0] = _row(names[0], labels=BRAIN)
    assert rows[0]["verdict"] == "OK"
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [rows, [rows[0]]])
    # retried once on the same code (a router flip is transient); the same wrong route again = 2 of 2: counted
    assert (rc, art["status"]) == (1, "fail") and [c[0] for c in calls] == [names, [names[0]]]
    assert art["flaky_clips"][0]["runs"] == ["ROUTE-CHANGED", "ROUTE-CHANGED"]
    cid = _cid(d, names[0])
    assert f"FUNCTION: clip {cid} route" in art["said_vs_did_regressions"]
    assert art["summary"]["ok_rate"] == 1.0                            # the old bar would have said all OK
    bad = [r for r in art["clip_results"] if r["result"] == "fail"]
    assert [r["clip_id"] for r in bad] == [cid] and bad[0]["changed_fields"]
    assert "lists" not in art["reason"] and "add_item" not in art["reason"]   # labels stay out of the public reason
    ok, why = vgc.evaluate(dict(art, status="pass"), now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert not ok and "1 of 6 pinned clip(s) did not hold" in why      # the checker re-derives, never trusts the label


def test_a_transient_route_flip_is_tolerated_like_brain_wording(tmp_path, monkeypatch, world):
    """Measured: one pinned clip came back OK on another route in 2 of 44 runs (2026-10-15..18), isolated."""
    d, names, base = world
    rows = _good(names)
    rows[0] = _row(names[0], labels=BRAIN)
    back = [_good(names)[0]]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [rows, back, back])
    assert (rc, art["status"]) == (0, "pass"), art["reason"]
    c = art["flaky_clips"][0]
    assert c["runs"] == ["ROUTE-CHANGED", "OK", "OK"] and "route flip" in c["note"] and c["regressed"] is False
    assert {r["clip_id"]: r["result"] for r in art["clip_results"]}[_cid(d, names[0])] == "pass"


def test_negative_control_without_the_per_clip_checks_the_wrong_action_passes(tmp_path, monkeypatch, world):
    """Prove the test above tests the new code: neutralise the per-clip evaluation and the same run is green."""
    d, names, base = world
    rows = _good(names)
    rows[0] = _row(names[0], labels=BRAIN)
    real = vcc.evaluate
    monkeypatch.setattr(vcc, "evaluate", lambda *a, **k: {**real(*a, **k), "regressions": []})
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [rows])
    assert (rc, art["status"]) == (0, "pass")


def test_a_legacy_route_hash_baseline_already_catches_a_fast_path_change(tmp_path, monkeypatch):
    """The live baseline (2026-10-15) predates T-3 labels: its route hash still catches a lane / intent change."""
    d = _corpus(tmp_path, 6)
    names = _names(d)
    base = _baseline(d, names, sig=False)
    rows = [_row(n, labels=lab, sig=False) for n, lab in zip(names, _labels_for(names))]
    rows[1] = _row(names[1], labels=dict(FAST, intent="show_list"), sig=False)
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [rows])
    assert (rc, art["status"]) == (1, "fail")
    assert art["clip_summary"]["compared"] == ["route_hash"] and art["clip_summary"]["failed"] == 1


def test_newly_empty_clip_is_retried_and_tolerated_when_transient(tmp_path, monkeypatch, world):
    d, names, base = world
    rows = _good(names)
    rows[3] = _row(names[3], "EMPTY")
    again = [_good(names)[3]]
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [rows, again, again])
    assert (rc, art["status"]) == (0, "pass"), art["reason"]
    assert [c[0] for c in calls] == [names, [names[3]], [names[3]]]    # ONLY that clip, twice more
    c = art["flaky_clips"][0]
    assert c["runs"] == ["EMPTY", "OK", "OK"] and c["regressed"] is False and "EMPTY (transient STT)" in c["note"]
    # the clip is judged on the run that PASSED, so its route matches again
    assert {r["clip_id"]: r["result"] for r in art["clip_results"]}[_cid(d, names[3])] == "pass"


def test_newly_empty_clip_that_stays_empty_fails(tmp_path, monkeypatch, world):
    d, names, base = world
    rows = _good(names)
    rows[3] = _row(names[3], "EMPTY")
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [rows, [_row(names[3], "EMPTY")]])
    assert (rc, art["status"]) == (1, "fail")
    assert f"FUNCTION: clip {_cid(d, names[3])} stt-empty" in art["said_vs_did_regressions"]


def test_owner_label_file_is_read_and_a_malformed_one_is_an_error(tmp_path, monkeypatch, world):
    d, names, base = world
    exp = tmp_path / "exp.json"
    exp.write_text(json.dumps({"clips": {_cid(d, names[4]): {"lane": "fast", "domain": "timers"}}}))
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_good(names)], extra=("--expectations", str(exp)))
    assert (rc, art["status"]) == (1, "fail") and art["clip_summary"]["owner_labelled"] == 1
    exp.write_text("{broken")
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_good(names)], extra=("--expectations", str(exp)))
    assert (rc, art["status"]) == (2, "error") and "clip expectations" in art["reason"]


def test_a_rebaseline_run_is_not_evidence(tmp_path, monkeypatch, world):
    d, names, base = world
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_good(names)], extra=("--update-baseline",))
    assert art["status"] == "pass"                                     # it wrote the new bar...
    ok, why = vgc.evaluate(art, now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert ok, why                                                     # (same clip ids: judged against the old profile)
    (tmp_path / "other").mkdir()
    fresh = _corpus(tmp_path / "other", 3)
    rc, art2, _ = _drive(tmp_path / "other", monkeypatch, fresh, {}, [_good(_names(fresh))])
    assert art2["status"] == "pass" and art2["clip_summary"] is None    # a first-ever run judges nothing...
    ok, why = vgc.evaluate(art2, now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert not ok and "well-formed" in why                             # ...so it never clears the deploy gate


def test_pin_from_repins_to_exactly_the_proposed_clips(tmp_path, monkeypatch, world):
    d, names, base = world
    chosen = [_cid(d, names[i]) for i in (5, 1, 3)]
    pin = tmp_path / "pins.json"
    pin.write_text(json.dumps({"clip_ids": chosen}))
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [_good([names[5], names[1], names[3]])],
                            extra=("--update-baseline", "--pin-from", str(pin)))
    assert calls[0][0] == [names[5], names[1], names[3]]
    saved = json.loads((tmp_path / "baseline.json").read_text())["summary"]
    assert saved["clip_ids"] == chosen and art["speed_comparison"].startswith("skipped")
    pin.write_text(json.dumps({"clip_ids": ["f" * 12]}))
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_good(names)], extra=("--update-baseline", "--pin-from", str(pin)))
    assert (rc, art["status"]) == (2, "error") and "--pin-from" in art["reason"]


# ── the self-test (the negative control) ─────────────────────────────────────
def _faulty_replay(names, *, apply=True, misroute_breaks_route=True):
    def run(fault):
        assert fault in vrp.SELF_TEST_FAULTS, fault
        if fault == "blank_stt":
            rows = [_row(n, "EMPTY") for n in names]
        else:
            rows = [_row(n, labels=dict(lab, router_domain="weather", router_tool=None)) if misroute_breaks_route
                    else _row(n, labels=lab) for n, lab in zip(names, _labels_for(names))]
        return _report(rows, fault=fault if apply else None)
    return run


def test_self_test_passes_only_when_every_fault_goes_red(tmp_path, monkeypatch, world):
    d, names, base = world
    shared = tmp_path / "last.json"
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, _faulty_replay(names), extra=("--self-test",))
    assert rc == 0
    assert art is None and not shared.exists()                         # the shared artifact is never written
    st = json.loads((tmp_path / "selftest.json").read_text())
    assert st["self_test"] is True and st["self_test_status"] == "pass" and st["status"] == "self-test-pass"
    assert [c[1] for c in calls] == list(vrp.SELF_TEST_FAULTS)          # one replay per fault, fault set per replay
    assert os.environ.get(vrp.FAULT_ENV) is None                       # and cleared after
    assert st["faults"]["blank_stt"]["failed_kinds"] == {"stt-empty": 6}
    assert st["faults"]["misroute"]["clip_summary"]["failed"] == 6
    assert not (tmp_path / "t.jsonl").exists()                         # nor the shared trend
    ok, why = vgc.evaluate(st, now_epoch=time.time(), max_age_s=3600, baseline=None)
    assert not ok and "SELF-TEST" in why                               # a self-test artifact is never evidence


def test_self_test_fails_when_a_fault_stays_green(tmp_path, monkeypatch, world):
    """The control of the control: a misroute the checks cannot see means the instrument is broken."""
    d, names, base = world
    rc, _, _ = _drive(tmp_path, monkeypatch, d, base, _faulty_replay(names, misroute_breaks_route=False),
                      extra=("--self-test",))
    st = json.loads((tmp_path / "selftest.json").read_text())
    assert rc == 1 and st["self_test_status"] == "fail" and "misroute" in st["reason"]
    assert st["faults"]["misroute"]["caught"] is False and st["faults"]["blank_stt"]["caught"] is True


def test_self_test_fails_when_the_empty_rule_is_disabled(tmp_path, monkeypatch, world):
    """Break the fix (EMPTY excluded again, the pre-T-3 behaviour): the blank-STT fault must stay green -> fail."""
    d, names, base = world
    real = vcc.check_clip

    def old_rule(cid, row, base_e, owner):
        if row is not None and row.get("verdict") == "EMPTY":
            return {"clip_id": cid, "result": "unchecked", "why": "EMPTY excluded"}
        return real(cid, row, base_e, owner)
    monkeypatch.setattr(vcc, "check_clip", old_rule)
    rc, _, _ = _drive(tmp_path, monkeypatch, d, base, _faulty_replay(names), extra=("--self-test",))
    st = json.loads((tmp_path / "selftest.json").read_text())
    assert rc == 1 and st["faults"]["blank_stt"]["caught"] is False


def test_self_test_errors_when_the_replay_ignores_the_fault(tmp_path, monkeypatch, world):
    """A replay that predates fault injection reports no fault: that is 'could not run', never a pass."""
    d, names, base = world
    rc, _, _ = _drive(tmp_path, monkeypatch, d, base, _faulty_replay(names, apply=False), extra=("--self-test",))
    st = json.loads((tmp_path / "selftest.json").read_text())
    assert rc == 2 and st["self_test_status"] == "error" and "did not apply the fault" in st["reason"]


def test_self_test_needs_expectations(tmp_path, monkeypatch):
    d = _corpus(tmp_path, 3)
    names = _names(d)
    base = _baseline(d, names, rows=[_row(n, "CANT_DO") for n in names])   # nothing was OK: nothing to violate
    rc, _, calls = _drive(tmp_path, monkeypatch, d, base, _faulty_replay(names), extra=("--self-test",))
    st = json.loads((tmp_path / "selftest.json").read_text())
    assert rc == 2 and calls == [] and "nothing for a fault to violate" in st["reason"]


def test_self_test_refuses_the_shared_artifact_path(tmp_path, monkeypatch, world):
    d, names, base = world
    monkeypatch.setattr(sys, "argv", ["probe", "--self-test", "--self-test-results", str(vrp.DEFAULT_RESULTS)])
    with pytest.raises(SystemExit) as ei:
        vrp.main()
    assert ei.value.code == 2


def test_self_test_refuses_a_results_path_given_on_the_same_command_line(tmp_path, monkeypatch):
    shared = tmp_path / "some-shared.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--self-test", "--results", str(shared),
                                      "--self-test-results", str(shared)])
    with pytest.raises(SystemExit) as ei:
        vrp.main()
    assert ei.value.code == 2 and not shared.exists()


def test_a_normal_run_strips_an_inherited_fault_and_refuses_a_faulted_report(tmp_path, monkeypatch, world):
    d, names, base = world
    rc, art, calls = _drive(tmp_path, monkeypatch, d, base, [_good(names)], env_fault="blank_stt")
    assert calls[0][1] is None and (rc, art["status"]) == (0, "pass")  # the env var never reached the replay
    rc, art, _ = _drive(tmp_path, monkeypatch, d, base, [_report(_good(names), fault="misroute")])
    assert (rc, art["status"]) == (2, "error") and "injected fault" in art["reason"]
    assert json.loads((tmp_path / "baseline.json").read_text()) == base   # and never becomes a baseline


def test_self_test_faults_match_the_replay_harness():
    """The probe's fault list and replay_samples.REPLAY_FAULTS must agree (AST read: the replay imports zoe-data)."""
    tree = ast.parse((REPO / "services/zoe-data/tests/replay_samples.py").read_text())
    faults = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", None) == "REPLAY_FAULTS" for t in n.targets))
    assert tuple(faults) == vrp.SELF_TEST_FAULTS


def test_fault_caught_rules():
    res = lambda kinds: {"results": [{"clip_id": str(i), "result": "fail" if k else "pass", "kind": k}  # noqa: E731
                                     for i, k in enumerate(kinds)]}
    assert vcc.fault_caught("blank_stt", res(["stt-empty"] * 3), 3)[0] is True
    assert vcc.fault_caught("blank_stt", res(["stt-empty", "stt-empty", None]), 3)[0] is False   # one slipped through
    assert vcc.fault_caught("misroute", res(["route", None, None]), 3)[0] is True
    assert vcc.fault_caught("misroute", res([None, None, None]), 3)[0] is False
    assert vcc.fault_caught("misroute", {"results": []}, 0)[0] is False


# ── measure_voice: the labels reach the probe, content-free ──────────────────
def test_route_sig_is_order_independent_and_none_without_labels():
    a = dict(FAST)
    b = dict(reversed(list(FAST.items())))
    assert mv._sig(a) == mv._sig(b) and len(mv._sig(a)) == 10
    assert mv._sig(dict(FAST, intent="other")) != mv._sig(a)
    assert mv._sig(None) is None


def test_measure_voice_passes_route_labels_and_the_fault_through(tmp_path, monkeypatch):
    replay = {"counts": {"OK": 1}, "fault": "misroute", "memory_recall": "ok",
              "rows": [{"file": "a.wav", "verdict": "OK", "outcome": "tier1:lists:add_item", "transcript": "x",
                        "stt_ms": 1, "route": dict(FAST)}]}
    rj = tmp_path / "replay.json"
    rj.write_text(json.dumps(replay))
    out = tmp_path / "out.json"

    class _P:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(mv.subprocess, "run", lambda *a, **k: _P())
    args = type("A", (), {"timeout": 5, "json": str(out)})()
    assert mv._run_and_report(["x"], str(tmp_path), str(rj), args) == 0
    rep = json.loads(out.read_text())
    assert rep["fault"] == "misroute"
    assert rep["rows"][0]["route"] == FAST and rep["rows"][0]["route_sig"] == mv._sig(FAST)
    assert "x" not in json.dumps(rep["rows"][0]["route"])              # labels, never the transcript
