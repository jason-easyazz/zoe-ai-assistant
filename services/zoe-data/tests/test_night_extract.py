"""The SHADOW night pre-pass (``night_extract.py``, ``scripts/maintenance/zoe-night-extract.py``): GLiNEREL proposes facts, nothing is written to memory.

Every rule has a negative control beside it. The model is NEVER loaded here (CI has neither the venv nor the weights): ``extract`` is a double that returns the
raw shape the worker would, so the REAL post-processing, comparison, ledger walls and file handling run. The one test that needs the model is skipped cleanly
when the venv / weights are absent. Invented household only (the repo is public).
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import memory_forgotten as mf
import night_extract as ne
from forgotten_support import FRIEND, USER, ledger_env  # noqa: F401 - autouse ledger fixture

pytestmark = pytest.mark.ci_safe

KEEP = "Leo"
NIGHT = "2026-11-04"
REPO = Path(__file__).resolve().parents[3]


def run(coro):
    return asyncio.run(coro)


def ent(label, text, score=0.97):
    return {"label": ne.ENT_LABELS[label], "text": text, "score": score, "start": 0, "end": len(text)}


def rel(label, head, tail, score=0.9):
    return {"label": ne.REL_LABELS[label], "head": {"text": head}, "tail": {"text": tail}, "score": score}


# ── a synthetic day: (text, raw model result) ────────────────────────────────────────────────────────────────────────────────────────────────
DAY = [
    ("hi zoe, im Dana and I live in Hobart", {"entities": [ent("person", "Dana"), ent("place", "Hobart")], "relations": [rel("lives in", "SPEAKER", "Hobart")]}),
    ("my son Leo is 16", {"entities": [ent("person", "Leo"), ent("age", "16")], "relations": [rel("age", "Leo", "16")]}),
    ("my daughter Mia is fourteen now", {"entities": [ent("person", "Mia")], "relations": [rel("age", "Mia", "fourteen")]}),
    ("we have a cat called Mittens", {"entities": [], "relations": []}),
    ("Biscuit is our dog", {"entities": [], "relations": []}),
    ("my mum Ruth lives in Albany", {"entities": [ent("person", "Ruth"), ent("place", "Albany")], "relations": [rel("lives in", "Ruth", "Albany")]}),
    ("forget that Kez rang", {"entities": [ent("person", "Kez")], "relations": []}),
    ("never mind, forget I said Tom", {"entities": [ent("person", "Tom")], "relations": []}),
    ("the client wants the quote by Friday", {"entities": [ent("person", "client")], "relations": []}),
    ("zoe set a timer", {"entities": [ent("person", "Zoe")], "relations": []}),
    ("what is the weather", {"entities": [], "relations": []}),
    ("Priya does not eat beef", {"entities": [ent("person", "Priya"), ent("food", "beef")], "relations": [rel("dislikes", "Priya", "beef")]}),
    ("my wife is 40", {"entities": [ent("age", "40")], "relations": [rel("age", "SPEAKER", "40", 0.3)]}),
    ("low confidence name", {"entities": [ent("person", "Jax", 0.5)], "relations": []}),
    ("I work at the depot", {"entities": [ent("workplace", "depot")], "relations": [rel("works at", "SPEAKER", "depot")]}),
    ("Tom is 70 years old", {"entities": [ent("person", "Tom"), ent("age", "70")], "relations": [rel("age", "Tom", "70 years old")]}),
    ("my partner Sam moved to Perth", {"entities": [ent("person", "Sam"), ent("place", "Perth")], "relations": [rel("lives in", "Sam", "Perth")]}),
    ("play some music", {"entities": [], "relations": []}),
    ("the dog needs a walk", {"entities": [], "relations": []}),
    ("my age is 41", {"entities": [ent("age", "41")], "relations": [rel("age", "SPEAKER", "41")]}),
]


def make_extract(day=DAY):
    table = {ne.prepare(t): r for t, r in day if ne.prepare(t) is not None}
    calls = []

    def extract(texts):
        calls.append(list(texts))
        return {"results": [table.get(t, {"entities": [], "relations": []}) for t in texts], "load_s": 1.0, "infer_s": 2.0, "peak_rss_mb": 1100.0, "threads": 4}
    extract.calls = calls
    return extract


def turns_of(day=DAY):
    return [(f"m{i}", t, 1.0e9 + i) for i, (t, _r) in enumerate(day)]


def drive(tmp_path, *, day=DAY, stored=(), extract=None, mode="shadow", dry=False, load_turns=None):
    extract = extract or make_extract(day)

    async def lt(_uid):
        return turns_of(day)

    async def ls(_uid):
        return list(stored)

    s = run(ne.run_night(night=NIGHT, members=[USER], load_turns=load_turns or lt, load_stored=ls, extract=extract, dry_run=dry, d=tmp_path / "out", run_mode=mode))
    return s, extract


def facts_on_disk(tmp_path):
    p = ne.facts_path(NIGHT, tmp_path / "out")
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


# ── prepare / postprocess ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_prepare_skips_forget_turns_and_rewrites_the_speaker():
    assert ne.prepare("forget that Kez rang") is None and ne.prepare("never mind") is None and ne.prepare("   ") is None
    assert ne.prepare("my son is 16 and I live in Hobart") == "SPEAKER's son is 16 and SPEAKER live in Hobart"
    assert ne.prepare("I'm Dana, my dog Rex") == "SPEAKER is Dana, SPEAKER's dog Rex"
    assert "forgetful" not in (ne.SKIP_RE.pattern or "") and ne.prepare("I am not forgetful") is not None      # whole-word: "forgetful" is no forget request


def test_postprocess_trusts_only_person_age_place_lives_in_at_the_global_thresholds():
    raw = dict(DAY)["my son Leo is 16"]
    kinds = {(f["kind"], f["subject"], f["value"]) for f in ne.postprocess(raw)}
    assert kinds == {("person", None, "leo"), ("age", "leo", "16")}
    day = dict(DAY)
    assert {(f["kind"], f["subject"], f["value"]) for f in ne.postprocess(day["hi zoe, im Dana and I live in Hobart"])} == {
        ("person", None, "dana"), ("place", None, "hobart"), ("lives_in", "@speaker", "hobart")}
    assert ne.postprocess(day["my daughter Mia is fourteen now"])[-1] == {"kind": "age", "subject": "mia", "value": "14", "src": "model", "score": 0.9}
    assert ne.postprocess(day["Priya does not eat beef"]) == [{"kind": "person", "subject": None, "value": "priya", "src": "model", "score": 0.97}]      # dislikes is NOT trusted
    assert ne.postprocess(day["I work at the depot"]) == []                                                                                           # works-at / workplace neither
    assert ne.postprocess(day["low confidence name"]) == []                                                                                           # 0.5 < 0.9 entity threshold
    assert ne.postprocess(day["my wife is 40"]) == []                                                                                                 # relation 0.3 < 0.5
    assert ne.postprocess(day["the client wants the quote by Friday"]) == [] and ne.postprocess(day["zoe set a timer"]) == []               # the non-name stop-list
    assert [f["value"] for f in ne.postprocess(day["Tom is 70 years old"]) if f["kind"] == "age"] == ["70"]


# ── the lexicon rules ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_lexicon_rules_find_pets_and_relation_words_the_model_cannot():
    pets = {(f["subject"], f["value"]) for t in ("we have a cat called Mittens", "Biscuit is our dog", "my dog Rex is the best", "our dog is called Pip") for f in ne.lexicon_facts(t) if f["kind"] == "pet"}
    assert pets == {("mittens", "cat"), ("biscuit", "dog"), ("rex", "dog"), ("pip", "dog")}
    kin = {(f["subject"], f["value"], f["word"]) for t in ("my son Leo is 16", "Ruth is my mum", "my partner is Sam", "Kamla, my mother, rang") for f in ne.lexicon_facts(t) if f["kind"] == "kin"}
    assert kin == {("leo", "son", "son"), ("ruth", "mother", "mum"), ("sam", "partner", "partner"), ("kamla", "mother", "mother")}


def test_lexicon_rules_refuse_what_is_not_a_name_or_not_a_pet():
    for t in ("the dog needs a walk", "my dog is sick", "my son is tall", "my mum is coming", "I like my cat", "the cat sat on the mat"):
        assert [f for f in ne.lexicon_facts(t) if f["kind"] == "pet" or f["subject"]] == [], t          # (an unnamed "my son" mention is a fact; a NAME is not invented)
    assert [f for f in ne.lexicon_facts("my cat Lola is 3") if f["kind"] == "kin"] == []
    mention = {(f["value"], f["of"]) for f in ne.lexicon_facts("my sis is preggo and tom's mum is called sandra and my boss wants it") if f["kind"] == "kin" and not f["subject"]}
    assert mention == {("sister", "@speaker"), ("mother", "tom"), ("boss", "@speaker")}          # an unnamed relative: subject empty, `of` says whose                      # a pet word is never a kin word


# ── the comparison with the 4B ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_compare_agree_extractor_only_and_the_contradictions():
    facts = [{"kind": "age", "subject": "leo", "value": "16"}, {"kind": "person", "subject": None, "value": "ruth"}, {"kind": "place", "subject": None, "value": "albany"},
             {"kind": "age", "subject": "mia", "value": "14"}, {"kind": "age", "subject": "anaya", "value": "7"}, {"kind": "pet", "subject": "biscuit", "value": "dog"},
             {"kind": "kin", "subject": "ruth", "value": "mother", "word": "mum"}, {"kind": "lives_in", "subject": "ruth", "value": "albany"}]
    stored = ["Leo is 16 years old", "The user's mum Ruth lives in Albany", "Mia is 15 years old", "The user is 7 years old",
              "Biscuit is the user's son"]
    c = ne.compare(facts, stored)
    by = {(f["kind"], f["subject"]): (f["cmp"], f["reason"]) for f in facts}
    assert by[("age", "leo")] == ("agree", "") and by[("person", None)] == ("agree", "") and by[("place", None)] == ("agree", "")
    assert by[("age", "mia")] == ("contradicts", "different_value")            # the 4B stored 15, the extractor read 14
    assert by[("age", "anaya")] == ("contradicts", "wrong_person")             # the 4B filed Anaya's age under the user
    assert by[("pet", "biscuit")] == ("contradicts", "pet_stored_as_child")
    assert by[("kin", "ruth")] == ("agree", "") and by[("lives_in", "ruth")] == ("agree", "")
    assert c == {"agree": 5, "extractor_only": 0, "contradicts": 3}
    solo = [{"kind": "person", "subject": None, "value": "kez"}]
    assert ne.compare(solo, ["Leo is 16"]) == {"agree": 0, "extractor_only": 1, "contradicts": 0} and solo[0]["cmp"] == "extractor_only"


# ── the model process: capped, offline, never raises ─────────────────────────────────────────────────────────────────────────────────────
def test_the_worker_command_is_a_capped_nice_scope_with_no_swap():
    argv = ne.worker_argv(Path("/v/bin/python"), Path("/r/w.py"), "1600M")
    assert argv[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=1600M" in argv and "MemorySwapMax=0" in argv
    assert argv[argv.index("nice") : argv.index("nice") + 3] == ["nice", "-n", "10"] and argv[-2:] == ["/v/bin/python", "/r/w.py"]


class _P:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def test_run_worker_reports_instead_of_raising(tmp_path, monkeypatch):
    py = tmp_path / "bin" / "python"
    py.parent.mkdir()
    py.write_text("#!/bin/sh\n")
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT_VENV", str(tmp_path))
    seen = {}

    def ok(argv, **kw):
        seen.update(argv=argv, env=kw["env"], req=json.loads(kw["input"]))
        return _P(0, 'noise\n{"results": [{"entities": [], "relations": []}], "load_s": 1.0}\n')
    out = ne.run_worker(["x"], threads=2, batch_size=4, runner=ok)
    assert out["results"] and seen["env"]["OMP_NUM_THREADS"] == "2" and seen["env"]["HF_HUB_OFFLINE"] == "1" and seen["req"]["batch_size"] == 4
    assert seen["req"]["entity_threshold"] == ne.LOW_E and seen["req"]["relation_threshold"] == ne.LOW_R and "day of week" in seen["req"]["entities"] and "is aged" in seen["req"]["relations"]
    assert ne.run_worker(["x"], runner=lambda *a, **k: _P(3, '{"error": "model_missing"}'))["error"] == "model_missing"
    assert ne.run_worker(["x"], runner=lambda *a, **k: _P(137, "")).get("error") == "failed"                  # killed by the cap
    assert ne.run_worker(["x"], runner=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))["error"] == "timeout"
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT_VENV", str(tmp_path / "nope"))
    assert ne.run_worker(["x"], runner=ok) == {"error": "model_missing", "kind": "no_venv"}


# ── the night ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_shadow_night_writes_the_proposals_privately_and_compares(tmp_path):
    stored = ["Leo is 16 years old", "The user's daughter Mia is 15", "The user lives in Hobart"]
    s, extract = drive(tmp_path, stored=stored)
    assert s["status"] == "ok" and s["turns"] == 18 and s["skipped_forget_turns"] == 2 and len(extract.calls) == 1            # the model is called ONCE for the whole day
    rows = facts_on_disk(tmp_path)
    assert s["facts"] == len(rows) > 10 and s["agree"] + s["extractor_only"] + s["contradicts"] == s["facts"]
    assert {r["kind"] for r in rows} <= {"person", "age", "place", "lives_in", "pet", "kin"}
    assert {"person", "age", "place", "lives_in", "pet", "kin"} == {r["kind"] for r in rows}
    cmp = {(r["kind"], r["subject"], r["value"]): (r["cmp"], r["reason"]) for r in rows}
    assert cmp[("age", "leo", "16")][0] == "agree" and cmp[("age", "mia", "14")] == ("contradicts", "different_value")
    assert cmp[("person", None, "ruth")][0] == "extractor_only" and cmp[("pet", "mittens", "cat")][0] == "extractor_only"
    assert {r["src"] for r in rows} == {"model", "rule"}
    assert all(r["user_id"] == USER and r["night"] == NIGHT and r["turn_id"].startswith("m") for r in rows)
    text = ne.facts_path(NIGHT, tmp_path / "out").read_text()
    assert "kez" not in {r["value"] for r in rows} and not any(r["turn_id"] in ("m6", "m7") for r in rows)                      # forget / never-mind turns produce nothing
    assert "client" not in {r["value"] for r in rows} and "zoe" not in {r["value"] for r in rows}
    d = tmp_path / "out"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700 and stat.S_IMODE(ne.facts_path(NIGHT, d).stat().st_mode) == 0o600 and stat.S_IMODE(ne.summary_path(NIGHT, d).stat().st_mode) == 0o600
    summ = json.loads(ne.summary_path(NIGHT, d).read_text())
    assert summ["perf"]["peak_rss_mb"] == 1100.0 and summ["members"][USER]["facts"] == len(rows)
    assert not any(w in ne.summary_path(NIGHT, d).read_text().lower() for w in ("leo", "mia", "hobart"))                      # the summary is counts only


def test_off_writes_nothing_and_never_calls_the_model(tmp_path):
    s, extract = drive(tmp_path, mode="off")
    assert s == {"status": "flag_off", "mode": "off"} and not extract.calls and not (tmp_path / "out").exists()


def test_the_flag_registry_default_is_off_and_on_is_accepted_but_unbuilt(monkeypatch):
    monkeypatch.delenv("ZOE_NIGHT_EXTRACT", raising=False)
    assert ne.mode() == "off"
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT", "shadow")
    assert ne.mode() == "shadow"
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT", "bogus")
    assert ne.mode() == "off"


def test_dry_run_writes_nothing(tmp_path):
    s, _ = drive(tmp_path, dry=True)
    assert s["status"] == "ok" and s["facts"] > 0 and not (tmp_path / "out").exists()


def test_shadow_never_touches_memory_or_the_people_graph():
    src = (REPO / "services" / "zoe-data" / "night_extract.py").read_text() + (REPO / "scripts" / "maintenance" / "zoe-night-extract.py").read_text()
    for banned in ("people_graph", "person_extractor", "get_memory_service().add", "svc.add", ".add_memory", "INSERT INTO", "UPDATE ", "memory_authority", "write_fact"):
        assert banned not in src, banned
    assert "load_recent_for_prompt" in src                                                                                  # reads the 4B's rows, never writes them


def test_a_member_whose_day_cannot_be_read_is_withheld_not_guessed(tmp_path):
    async def boom(_uid):
        raise mf.LedgerUnavailable("down")
    s, extract = drive(tmp_path, load_turns=boom)
    assert s["status"] == "nothing_to_do" and s["withheld_members"] == 1 and not extract.calls and not (tmp_path / "out").exists()


def test_a_missing_model_is_a_status_not_a_crash(tmp_path):
    s, _ = drive(tmp_path, extract=lambda texts: {"error": "model_missing", "kind": "no_venv"})
    assert s["status"] == "model_missing" and not (tmp_path / "out").exists()
    s2, _ = drive(tmp_path, extract=lambda texts: {"results": []})
    assert s2["status"] == "failed"                                                                                        # a short answer is never zipped against the wrong turns


def test_guests_and_synthetic_ids_are_never_members():
    assert ne.real_members(["guest", "guest-abc123", "anonymous", "voice-guest", "", "demo_bar_00000001", "test_x_0000ab", "member_owner", "member_kid"]) == ["member_kid", "member_owner"]


def test_the_forgotten_ledger_is_a_second_wall_on_the_output(tmp_path):
    day = DAY + [(f"{FRIEND} said hi", {"entities": [ent("person", FRIEND)], "relations": []})]
    run(mf.add(USER, FRIEND, actor=USER))
    s, _ = drive(tmp_path, day=day)
    rows = facts_on_disk(tmp_path)
    assert not [r for r in rows if FRIEND.lower() in fact_words(r)] and rows                                              # Dana is in the day AND in the model's output; neither reaches the file


def fact_words(r):
    return ne.fact_text(r).lower()


def test_a_ledger_outage_withholds_the_whole_member(tmp_path, monkeypatch):
    async def down(*_a, **_k):
        raise mf.LedgerUnavailable("down")
    monkeypatch.setattr(mf, "keep_unforgotten", down)
    s, _ = drive(tmp_path)
    assert s["status"] == "ok" and facts_on_disk(tmp_path) == []


# ── forget ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_forget_x_erases_the_proposals_naming_it_and_keeps_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT_DIR", str(tmp_path / "out"))
    day = [("my son Leo is 16", dict(DAY)["my son Leo is 16"]), ("my friend Dana is 30", {"entities": [ent("person", FRIEND), ent("age", "30")], "relations": [rel("age", FRIEND, "30")]})]
    drive(tmp_path, day=day)
    before = facts_on_disk(tmp_path)
    assert any("dana" in fact_words(r) for r in before) and any("leo" in fact_words(r) for r in before)
    assert run(ne.erase_entity(USER, mf.name_pattern(FRIEND), strict=True)) >= 2
    after = facts_on_disk(tmp_path)
    assert after and not any("dana" in fact_words(r) for r in after) and any("leo" in fact_words(r) for r in after)
    assert run(ne.erase_entity("someone_else", mf.name_pattern("Leo"), strict=True)) == 0                                  # another member's erase touches nothing here
    assert any("leo" in fact_words(r) for r in facts_on_disk(tmp_path))


def test_a_member_erase_removes_every_fact_and_the_summary_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT_DIR", str(tmp_path / "out"))
    drive(tmp_path)
    assert facts_on_disk(tmp_path) and USER in json.loads(ne.summary_path(NIGHT, tmp_path / "out").read_text())["members"]
    assert run(ne.delete_user(USER)) > 0
    assert facts_on_disk(tmp_path) == [] and USER not in json.loads(ne.summary_path(NIGHT, tmp_path / "out").read_text())["members"]
    assert run(ne.delete_user("")) == 0


def test_the_reader_withholds_a_forgotten_name_even_when_the_erase_did_not_land(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_NIGHT_EXTRACT_DIR", str(tmp_path / "out"))
    day = [("my friend Dana is 30", {"entities": [ent("person", FRIEND), ent("age", "30")], "relations": [rel("age", FRIEND, "30")]}),
           ("my son Leo is 16", dict(DAY)["my son Leo is 16"])]
    drive(tmp_path, day=day)
    run(mf.add(USER, FRIEND, actor=USER))
    served = run(ne.load_facts(NIGHT))
    assert served and not any("dana" in fact_words(r) for r in served) and any("leo" in fact_words(r) for r in served)


def test_the_cascade_and_the_member_erase_are_wired():
    cascade = (REPO / "services" / "zoe-data" / "memory_forget_cascade.py").read_text()
    assert "night_extract.erase_entity" in cascade and '"night_extract"' in cascade
    assert "night_extract.delete_user" in (REPO / "services" / "zoe-data" / "struggle_detector.py").read_text()      # MemoryService.delete_user -> struggle_detector.delete_user


# ── the real model: skipped cleanly where the venv / weights are absent (CI) ──────────────────────────────────────────────────────────────
HAVE_MODEL = ne.venv_python().exists() and (Path.home() / ".zoe" / "models" / "glinerel" / "hub").exists() and os.environ.get("ZOE_NIGHT_EXTRACT_REAL_MODEL") == "1"


@pytest.mark.skipif(not HAVE_MODEL, reason="needs ~/.zoe/venvs/glinerel + the weights and ZOE_NIGHT_EXTRACT_REAL_MODEL=1 (a ~1.2 GB run); never in CI")
def test_the_real_model_finds_the_obvious_facts_under_the_cap():
    out = ne.run_worker([ne.prepare("my son Leo is 16 and my mum Ruth lives in Albany")], threads=4, batch_size=1)
    assert not out.get("error"), out
    facts = {(f["kind"], f["subject"], f["value"]) for f in ne.postprocess(out["results"][0])}
    assert ("age", "leo", "16") in facts and out["peak_rss_mb"] < 1600


# ── the CLI ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("zoe_night_extract_cli", REPO / "scripts" / "maintenance" / "zoe-night-extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_cli_is_a_no_op_while_the_flag_is_off_and_runs_synthetic_personas_when_forced(tmp_path, monkeypatch):
    cli = _cli()
    persona = tmp_path / "round9" / "a-transcript.jsonl"
    persona.parent.mkdir()
    persona.write_text("\n".join(json.dumps({"in": t}) for t, _r in DAY) + "\n")
    monkeypatch.delenv("ZOE_NIGHT_EXTRACT", raising=False)
    monkeypatch.setattr(ne, "run_worker", lambda texts, **kw: make_extract()(texts))
    code, s = run(cli.amain(["--jsonl", str(persona), "--out-dir", str(tmp_path / "o"), "--night", NIGHT]))
    assert (code, s["status"]) == (0, "flag_off") and not (tmp_path / "o").exists()
    code, s = run(cli.amain(["--jsonl", str(persona), "--out-dir", str(tmp_path / "o"), "--night", NIGHT, "--force-mode", "shadow", "--budget-s", "300",
                             "--model-url", "ignored", "--ctx-tokens", "8192"]))
    assert code == 0 and s["status"] == "ok" and s["turns"] == 18 and list(s["members"]) == ["a-transcript:round9"]
    assert (tmp_path / "o" / f"{NIGHT}.night-extract.jsonl").exists()
    code, s = run(cli.amain(["--jsonl", str(persona), "--force-mode", "shadow", "--budget-s", "60"]))
    assert s["status"] == "no_time"                                                  # the window handed it too little time: not started


def test_the_cli_never_fails_the_night(monkeypatch, capsys):
    cli = _cli()

    async def boom(_argv=None):
        raise RuntimeError("x")
    monkeypatch.setattr(cli, "amain", boom)
    assert cli.main([]) == 0
    assert "status=error" in capsys.readouterr().err
