"""Pin the persona drift probe battery (scripts/perf/persona_drift.py, Samantha initiative lane F E1).

Pure logic on synthetic transcripts: no live brain, network or service. Includes the negative control (a drifted transcript
is flagged, a stable one is not) and the wiring pins that keep the harness off the live brain by day and off the live DB."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "perf" / "persona_drift.py"


def _load():
    sys.path.insert(0, str(REPO / "scripts" / "perf"))
    spec = importlib.util.spec_from_file_location("persona_drift", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["persona_drift"] = mod
    spec.loader.exec_module(mod)
    return mod


pd = _load()


# ---- the battery -----------------------------------------------------------------------------------------------------

def test_battery_shape_and_probe_blocks():
    assert len(pd.BATTERY) == 60
    assert [t["turn"] for t in pd.BATTERY] == list(range(1, 61))
    for pos, turns in pd.PROBE_TURNS.items():
        assert [pd.BATTERY[n - 1]["kind"] for n in turns] == ["self", "feel", "fact"]
        assert [pd.BATTERY[n - 1]["user"] for n in turns] == [u for _, u in pd.PROBES], "the SAME probes at every position"
    assert sum(1 for t in pd.BATTERY if t["kind"] == "farewell") == 1
    assert len({t["user"] for t in pd.BATTERY if not t["probe"]}) == 51, "51 distinct chat turns"


def test_battery_holds_no_household_data():
    text = " ".join(t["user"] for t in pd.BATTERY)
    for needle in ("jason", "@", "http", "phone", "address", "password"):
        assert needle not in text.lower()


# ---- the rules, one by one (positive + the failure each was written for) ----------------------------------------------

@pytest.mark.parametrize("rule,kind,good,bad", [
    ("contractions", "chat", "That's a fair point, and I'd try the first one today.",
     "That is a fair point, and I would try the first one today."),
    ("no_exclaim", "chat", "That sounds fun.", "That sounds fun!"),
    ("no_retry", "chat", "I couldn't find that one.", "Something went wrong. Please try again."),
    ("one_question", "chat", "Which one? I'd pick the red.", "Which one? Why? Really?"),
    ("length_band", "farewell", "Anytime, take care.", " ".join(["word"] * 40)),
    ("no_markdown", "chat", "Boil it six minutes and cool it.", "- boil it\n- cool it"),
    ("no_hook", "chat", "Boil it six minutes.", "Boil it six minutes. Want me to time it?"),
    ("no_flattery", "chat", "It's mostly the salt, I'd say.", "Great question! It's mostly the salt."),
    ("no_opener", "chat", "Salt, mostly. It's from rivers.", "Certainly! It's from rivers."),
    ("no_assistant_speak", "chat", "I'd start with scales.", "As an AI language model I'd start with scales."),
    ("self_ref", "self", "I'm Zoe, glad you're back.", "I'm ChatGPT, a language model."),
    ("self_ref", "chat", "I'd pick the red.", "I'm Aria, and I'd pick the red."),
    ("acknowledges_first", "feel", "Ugh, that's a rough one. What's up?", "Here are five tips to feel better today."),
    ("voice_marker", "self", "I'm Zoe. Honestly glad you asked.", "I am the assistant."),
    ("farewell_clean", "farewell", "Anytime, take care.", "Anytime! Is there anything else?"),
])
def test_each_rule_passes_the_good_and_fails_the_bad(rule, kind, good, bad):
    assert pd.score_reply(good, kind)[rule] is True
    assert pd.score_reply(bad, kind)[rule] is False


def test_rules_are_not_applicable_where_they_do_not_apply():
    r = pd.score_reply("Anytime, take care.", "farewell")
    assert r["contractions"] is None and r["acknowledges_first"] is None and r["voice_marker"] is None
    assert pd.score_reply("Sunlight scatters.", "fact")["farewell_clean"] is None


def test_emphatic_stiff_form_at_a_clause_end_is_not_a_miss():
    assert pd.score_reply("Yes, I am. It's a good habit, honestly, and I'd keep it up.", "chat")["contractions"] is True


# ---- the scoring over a transcript ------------------------------------------------------------------------------------

def test_stable_transcript_is_green_with_no_drift():
    res = pd.cells_and_scores(pd.synthetic_transcript())
    assert not [c for c, v in res["cells"].items() if v == "FAIL"]
    assert res["drift"] == [] and res["errored_turns"] == []
    assert res["scores"]["T1"] == res["scores"]["T20"] == res["scores"]["T60"] == 1.0
    assert pd.status_of(res) == "ok"


@pytest.mark.parametrize("fault", sorted(pd.FAULTS))
def test_NEGATIVE_CONTROL_a_drifted_transcript_is_flagged(fault):
    """Gold turns 1-59, one fault at turn 58-60: the named cell fails at T60, it PASSED at T1, and the run says drift."""
    res = pd.cells_and_scores(pd.synthetic_transcript(fault))
    for cell in pd.FAULTS[fault][1]:
        assert res["cells"][cell] == "FAIL", cell
        assert res["cells"][cell.replace("@T60", "@T1")] == "PASS"
    assert res["drift"] and pd.status_of(res) == "drift"
    assert res["cells"]["no_exclaim@T20"] == "PASS", "a fault at T60 does not smear back"


def test_a_fault_at_T1_only_is_not_drift_the_reference_is_what_it_is():
    res = pd.cells_and_scores(pd.synthetic_transcript("exclaim", at=("T1",)))
    assert res["cells"]["no_exclaim@T1"] == "FAIL" and res["drift"] == []


def test_mid_conversation_noise_within_tolerance_is_not_drift():
    t = pd.synthetic_transcript()
    for turn in (10, 30):                                   # 2 of 51 chat turns get an exclamation mark
        t[turn - 1]["reply"] += "!"
    res = pd.cells_and_scores(t)
    assert res["cells"]["no_exclaim@mid"] == "PASS" and res["drift"] == []   # 49/51 >= 0.90
    for turn in range(5, 14):                               # now 11 of 51 are exclaiming
        t[turn - 1]["reply"] += "!"
    assert pd.cells_and_scores(t)["cells"]["no_exclaim@mid"] == "FAIL"


def test_score_fall_past_tolerance_is_drift_even_if_no_single_rule_was_clean_at_T1():
    t = pd.synthetic_transcript()
    for fault in ("formal", "exclaim", "retry", "assistant", "multiq"):       # a broad collapse at T60
        for s in t:
            if s["probe"] == "T60":
                s["reply"] = pd.FAULTS[fault][0](s["reply"], s["kind"])
    res = pd.cells_and_scores(t)
    assert res["scores"]["T1"] - res["scores"]["T60"] > pd.TOL
    assert any(d.startswith("score:") for d in res["drift"])


def test_a_dropped_probe_reply_is_error_never_pass():
    t = pd.synthetic_transcript()
    t[19]["reply"], t[19]["error"] = "", "empty reply"
    res = pd.cells_and_scores(t)
    assert res["errored_turns"] == [20] and res["cells"]["no_exclaim@T20"] == "ERROR"
    assert res["scores"]["T20"] is None and pd.status_of(res) == "error"
    assert res["cells"]["no_exclaim@mid"] == "PASS"


def test_a_missing_turn_is_an_error_too():
    res = pd.cells_and_scores(pd.synthetic_transcript()[:40])
    assert res["errored_turns"] and pd.status_of(res) == "error"


# ---- baseline comparison ---------------------------------------------------------------------------------------------

def _baseline(res):
    return {"cells": dict(res["cells"]), "scores": dict(res["scores"]), "commit": "abc"}


def test_compare_flags_a_cell_that_passed_in_the_baseline_and_fails_now():
    base = _baseline(pd.cells_and_scores(pd.synthetic_transcript()))
    now = pd.cells_and_scores(pd.synthetic_transcript("retry", at=("T1", "T20", "T60")))
    cmp = pd.compare(now, base)
    assert "no_retry@T1" in cmp["regressions"] and cmp["red"] and pd.status_of(now, cmp) == "regression"


def test_compare_is_green_on_an_identical_run_and_a_baseline_fail_cannot_regress():
    res = pd.cells_and_scores(pd.synthetic_transcript())
    assert not pd.compare(res, _baseline(res))["red"]
    base_res = pd.cells_and_scores(pd.synthetic_transcript("retry", at=("T1", "T20", "T60")))
    assert pd.compare(base_res, _baseline(base_res))["regressions"] == []


def test_compare_flags_a_score_fall_past_tolerance_across_runs():
    base = _baseline(pd.cells_and_scores(pd.synthetic_transcript()))
    now = pd.cells_and_scores(pd.synthetic_transcript())
    now["scores"] = {**now["scores"], "T60": 0.80}
    cmp = pd.compare(now, base)
    assert cmp["score_falls"] and cmp["red"]
    now["scores"]["T60"] = 0.95                                  # within tolerance
    assert not pd.compare(now, base)["red"]


def test_compare_refuses_a_baseline_missing_a_cell():
    res = pd.cells_and_scores(pd.synthetic_transcript())
    base = _baseline(res)
    del base["cells"]["no_exclaim@T60"]
    cmp = pd.compare(res, base)
    assert cmp["refused"] and cmp["red"]


def test_the_rules_hash_binds_battery_and_rules():
    assert len(pd.RULES_SHA256) == 64


# ---- the CLI (offline paths) -----------------------------------------------------------------------------------------

def test_controls_main_is_green(capsys):
    assert pd.main(["--controls"]) == 0
    assert "instrument proof: OK" in capsys.readouterr().out


def test_dry_run_and_live_skip_without_zoe_perf(monkeypatch, capsys):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert pd.main(["--dry-run"]) == 0 and "60 turns" in capsys.readouterr().out
    assert pd.main([]) == 0 and "ZOE_PERF=1" in capsys.readouterr().out


def test_rescore_reads_a_stored_run(tmp_path, capsys):
    import json
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"transcript": pd.synthetic_transcript("exclaim")}))
    assert pd.main(["--rescore", str(p)]) == 1                   # drift -> 1
    p.write_text(json.dumps({"transcript": pd.synthetic_transcript()}))
    assert pd.main(["--rescore", str(p)]) == 0
    p.write_text("{}")
    assert pd.main(["--rescore", str(p)]) == 2


# ---- wiring: never the live brain by day, never the live DB ---------------------------------------------------------

def _main_src():
    text = SRC.read_text()
    fn = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_main"][0]
    return text, ast.get_source_segment(text, fn)


def test_live_run_goes_through_the_scratch_stack_and_quiet_hours_gates():
    text, src = _main_src()
    assert src.count("scratch_stack_for(") == 1
    assert src.index("scratch_stack_for(") < src.index("sb.Live(stack.token"), "no live client before the scratch stack"
    assert "_preflight(args, live, log, service_dir, revision)" in src and src.index("sb.Live(stack.token") < src.index("_preflight(")
    assert "uma._gates(args, live, log)" in text, "nightly window + quiet hours + deploy + readiness + RAM (in _preflight)"
    assert "_acquire_lock()" in src and "teardown(" in src
    assert 'env_file_value(service_dir, "POSTGRES_URL")' not in src and 'env_file_value(service_dir, "ZOE_INTERNAL_TOKEN")' not in src
    assert "close_scratch(ctx)" in text and "return _main(argv, ctx)" in text


def test_harness_never_forces_or_lands_past_quiet_hours():
    code = "\n".join(l for l in SRC.read_text().splitlines() if not l.lstrip().startswith(("#", '"""')))
    assigned = [l for l in code.splitlines() if ("environ[" in l or "setenv" in l or "putenv" in l)]
    assert not any("ZOE_HARNESS_FORCE" in l or "ZOE_HARNESS_LANDING" in l for l in assigned)
    assert "--force" not in code and "--landing" not in code


def test_the_live_run_is_off_without_zoe_perf_and_never_runs_at_import():
    assert '_typed_env().env_str("ZOE_PERF") != "1"' in SRC.read_text()


# ---- review round 1 (cross-review findings) --------------------------------------------------------------------------

def test_a_probe_rule_cannot_pass_at_T1_and_silently_skip_later():
    """A short terse reply used to turn contractions PASS -> SKIP (no drift reported)."""
    t = pd.synthetic_transcript()
    for s in t:
        if s["probe"] == "T60":
            s["reply"] = "Zoe." if s["kind"] == "self" else "Rough." if s["kind"] == "feel" else "Scattering."
    res = pd.cells_and_scores(t)
    assert res["cells"]["contractions@T60"] != "SKIP"
    assert any("PASS@T1" in d for d in res["drift"]), "terse replies cannot hide a lost rule"


def test_drift_flags_treat_pass_then_skip_as_drift():
    cells = {"no_exclaim@T1": "PASS", "no_exclaim@T20": "PASS", "no_exclaim@T60": "SKIP"}
    assert pd.drift_flags(cells, {"T1": 1.0, "T60": 1.0})


@pytest.mark.parametrize("reply", [
    "It is caused by Rayleigh scattering, and blue light scatters far more than red does in the thin air up there overhead.",
    "There is more blue light scattered across the sky because its wavelength is short, which is what makes the daytime sky blue.",
])
def test_impersonal_prose_is_not_stiff_and_a_fact_needs_no_contraction(reply):
    assert pd.score_reply(reply, "fact")["contractions"] is True


def test_chat_replies_still_need_a_contraction_at_length():
    long_chat = "That is a fair thing to wonder about and the first option wins for me because of the long term view."
    assert pd.score_reply(long_chat, "chat")["contractions"] is False


@pytest.mark.parametrize("reply", ["That's an unusual statement.", "Day length changes throughout the year.",
                                   "Here's a thing about feelings."])
def test_acknowledges_first_is_not_vacuous(reply):
    assert pd.score_reply(reply, "feel")["acknowledges_first"] is False


def test_acknowledges_first_accepts_real_acknowledgements():
    for reply in ("Ugh, flat days happen.", "Oh, sorry to hear it.", "Yeah, one of those days."):
        assert pd.score_reply(reply, "feel")["acknowledges_first"] is True


def test_quoted_questions_are_counted():
    assert pd.n_questions('She asked "Why?" and then "How?" before leaving.') == 2
    assert pd.score_reply('You could ask "why?" or "how?" instead.', "chat")["one_question"] is False
    assert pd.n_questions("Really?! Yes?") == 2 and pd.n_questions("One thing? Fine.") == 1


@pytest.mark.parametrize("reply,ok", [
    ("I'm Zoe, not ChatGPT.", True), ("People often confuse me with ChatGPT, but I'm Zoe.", True),
    ("I'm not Claude, I'm Zoe.", True), ("I'm ChatGPT.", False), ("I was made by OpenAI.", False),
    ("I'm Zoe, powered by Gemma.", False)])
def test_self_ref_flags_a_claim_not_a_mention(reply, ok):
    assert pd.score_reply(reply, "self")["self_ref"] is ok


def test_sure_opener_and_bare_let_me_know_are_not_over_flagged():
    assert pd.score_reply("Sure, the red one works. Let me know how it goes.", "chat")["no_opener"] is True
    assert pd.score_reply("Let me know how it goes.", "chat")["no_hook"] is True
    assert pd.score_reply("Let me know if you want more.", "chat")["no_hook"] is False


def test_a_mention_of_language_models_is_not_assistant_speak_but_a_self_claim_is():
    assert pd.score_reply("Some people call them language models, but I'm just Zoe.", "chat")["no_assistant_speak"] is True
    assert pd.score_reply("I'm an AI language model.", "chat")["no_assistant_speak"] is False


def test_preflight_exception_becomes_a_refused_run_not_a_crash(monkeypatch, tmp_path):
    """The whole stretch between the scratch stack and the first turn is guarded."""
    text = SRC.read_text()
    assert "baseline, refusal = None, f\"preflight failed" in text
    assert text.index("_preflight(args, live, log, service_dir, revision)") < text.index("run_battery(live, user, log)")


def test_fingerprint_covers_scoring_definitions_not_just_rule_names(monkeypatch):
    base = pd._scoring_definitions()
    flipped = dict(pd.RULES, no_exclaim=lambda r, k: "!" in r)
    monkeypatch.setattr(pd, "RULES", flipped)
    assert pd._scoring_definitions() != base, "a reversed predicate under the same name must change the fingerprint input"
    monkeypatch.undo()
    assert pd._scoring_definitions()["patterns"], "module-level regexes are part of the fingerprint"
    assert len(pd.RULES_SHA256) == 64


def test_preflight_refuses_a_baseline_missing_a_cell(monkeypatch, tmp_path):
    import types
    cells = {c: "PASS" for c in pd.ALL_CELLS}
    cells.pop(next(iter(cells)))
    monkeypatch.setattr(pd.bench_baseline, "load", lambda path, verdicts: ({"cells": cells, "rules_sha256": pd.RULES_SHA256}, None))
    args = types.SimpleNamespace(compare_baseline=True, record_baseline=False, baseline=tmp_path / "b.json")
    baseline, why = pd._preflight(args, object(), lambda m: None, tmp_path, None)
    assert baseline is None and "lacks 1 scored cell" in why


def test_night_gate_refuses_during_the_pause_and_fails_closed(monkeypatch):
    import types
    hq = types.SimpleNamespace(night_pause_verdict=lambda name, **k: types.SimpleNamespace(allowed=False, reason="paused"))
    monkeypatch.setattr(pd, "_harness_quiet", lambda: hq)
    assert pd.night_gate() == "paused"
    hq.night_pause_verdict = lambda name, **k: types.SimpleNamespace(allowed=True, reason="")
    assert pd.night_gate() is None

    def boom(*a, **k):
        raise RuntimeError("x")

    hq.night_pause_verdict = boom
    assert "refusing" in pd.night_gate()


def test_battery_stops_between_turns_when_the_night_pause_begins(monkeypatch):
    calls = []
    monkeypatch.setattr(pd, "_TURN_GATE", lambda budget: "paused" if len(calls) >= 2 else None)

    class Live:
        def chat(self, user, sess, text):
            calls.append(text)
            return {"reply": "ok", "error": None, "ms": 1}

    with pytest.raises(RuntimeError, match="night pause"):
        pd.run_battery(Live(), "u", lambda m: None)
    assert len(calls) == 2
