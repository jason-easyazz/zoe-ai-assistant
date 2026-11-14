"""The deterministic claim validator (memory redesign step 4, ``memory_claims_validate``): rules D.1-D.20 each with a
passing and a failing case, the chain order, the hyphen / nickname / lazy-possessive names, the no-regex AST rule, a
negative control per rule family, and the golden set: every golden case this validator can decide runs and must pass; the
rest are skipped BY NAME from fixtures/memory_claims_golden_validator_skips.json, and those skips are a ratchet - an
entry may only be removed, never added without a reason in review. Synthetic names, fixed dates. ``ci_safe``."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import medical_safety
import memory_claims as mc
import memory_claims_validate as vm
import people_graph
import structural_claims as sc

pytestmark = pytest.mark.ci_safe

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "memory_claims_validator_cases.json"
GOLDEN = HERE / "fixtures" / "memory_claims_golden.json"
GOLDEN_ORACLE = HERE / "fixtures" / "memory_claims_recorded" / "oracle.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in DATA["cases"]}


# -- the case runner (design note G schema; the extensions are documented in the fixture's ``_doc``) -----------------

def _ts(value: str) -> datetime:
    return people_graph.parse_ts(value)


class _PrefixGraph(vm.Graph):
    """A resolver that guesses by prefix (``resolve_person``'s default): the validator must refuse its answer (D.3)."""

    def resolve(self, name):
        names = [p.name for p in self._people]
        tier, idx = people_graph.match_tier(name, names, allow_prefix=True)
        if not idx:
            return people_graph.Resolution("none", handle=name)
        found = tuple(people_graph.PersonMatch(self._people[i].id, self._people[i].name) for i in idx)
        return people_graph.Resolution("unique" if len(found) == 1 else "ambiguous", tier, name, found)


class _FailingGraph(vm.Graph):
    """A roster read that failed: the strict reader raises (D.5)."""

    def resolve(self, name):
        raise OSError("roster read failed")

    def related(self, kin):
        raise OSError("roster read failed")


def build_context(case: dict) -> vm.Context:
    people = [vm.Person(p["id"], p["name"], p.get("relationship") or "") for p in case.get("graph") or []]
    edges = [(e[0], e[2]) for p in case.get("graph") or [] for e in p.get("edges") or [] if e[1] == "member"]
    opts = dict(case.get("context") or {})
    graph_cls = {"allow_prefix": _PrefixGraph, "fails": _FailingGraph}.get(opts.pop("resolver", ""), vm.Graph)
    member = case.get("member") or "demo_mc"
    rows = [vm.Row(r["id"], r["text"], r.get("status") or "approved", r.get("entity_id"),
                   tuple(vm.Attr(a["subject_ref"], a["attribute"], a["value_norm"], a.get("unit") or "", a.get("span"))
                         for a in (r.get("attrs") or [])))
            for r in case.get("rows_before") or []]
    turns = [vm.Turn(t["t"], _ts(t["at"]), t["text"], people_graph.content_turn_id(member, t["text"]) or "",
                     t.get("own", True)) for t in case.get("turns") or []]
    registry = [vm.Registered(e["subject_ref"], e["attribute"], e["role"], e["value_norm"], _ts(e["said_at"]))
                for e in case.get("registry_before") or []]
    return vm.Context(member, turns, rows, graph_cls(people, edges), registry, detector=medical_safety.detect, **opts)


def run_case(case: dict, output) -> tuple:
    return vm.validate(output, build_context(case))


def _value_in(attribute: str, want: dict, got: dict) -> bool:
    return mc.same_value(attribute, want, got) if attribute in mc.ATTRIBUTES else want == got


def disagreements(case: dict, verdicts: tuple) -> list:
    """What the verdicts get wrong against ``case["expect"]`` (an empty list = the case passes)."""
    exp, out = case.get("expect") or {}, []
    got = [(v.verdict, v.rule) for v in verdicts]
    if "verdicts" in exp and got != [(w["verdict"], w["rule"]) for w in exp["verdicts"]]:
        out.append(f"verdicts {got}")
    # "left as mined" (design note B.4, the G schema): the stored rows are untouched - no edit, no event - and no candidate
    # was refused or held. A registry-only apply (C10: a restatement with nothing stored) leaves the rows as mined.
    untouched = not any(v.edits or v.event for v in verdicts) and not any(v.verdict in ("refuse", "hold") for v in verdicts)
    if "left_as_mined" in exp and exp["left_as_mined"] != untouched:
        out.append("left_as_mined")
    if exp.get("invalid_rule") is not None and exp["invalid_rule"] not in [v.rule for v in verdicts]:
        out.append(f"invalid_rule {[v.rule for v in verdicts]}")
    applied = [v for v in verdicts if v.verdict == "apply"]
    edits = [e for v in applied for e in v.edits]
    before = {r["id"] for r in case.get("rows_before") or []}
    if "rows_after" in exp:
        want_sup = {r["id"] for r in exp["rows_after"] if r.get("status") == "superseded"}
        if want_sup != {e.row_id for e in edits if e.row_id in before}:
            out.append(f"superseded {sorted(e.row_id for e in edits)}")
        for r in exp["rows_after"]:
            if "new_of" not in r:
                continue
            for a in r.get("attrs") or []:
                if not any(e.row_id == r["new_of"] and any(x.attribute == a["attribute"] and _value_in(
                        a["attribute"], a["value_norm"], x.value_norm) and vm.same_ref(x.subject_ref, a["subject_ref"])
                        for x in e.attrs) for e in edits):
                    out.append(f"new_of {r['new_of']} {a['attribute']}")
    planned = [e for v in applied for e in v.registry]
    for w in exp.get("registry") or []:
        if not any(e.role == w["role"] and e.attribute == w["attribute"]
                   and _value_in(w["attribute"], w["value_norm"], e.value_norm) for e in planned):
            out.append(f"registry {w['role']} {w['attribute']}")
    if "events" in exp:
        events = [v.event for v in applied if v.event is not None]
        if len(events) != len(exp["events"]) or not all(
                any(vm.same_ref(e.subject_ref, w["subject_ref"]) and e.event == w["event"] for e in events)
                for w in exp["events"]):
            out.append("events")
    return out


# -- every fixture case --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cid", sorted(CASES))
def test_fixture_case(cid):
    case = CASES[cid]
    assert disagreements(case, run_case(case, DATA["oracle"][cid])) == []


def test_the_fixture_is_large_enough_and_synthetic():
    assert len(CASES) >= 25
    assert all(c["member"].startswith("demo_") for c in CASES.values())
    assert all(c["reason_if_changed"] is None for c in CASES.values())


# -- one test per rule: a passing case and a failing case ----------------------------------------------------------

#: rule -> (a case the rule PASSES in, a case the rule FAILS in). D.7 and D.10 accept rather than refuse: D.7's failing
#: case falls through to D.6 (the possessive is declined), D.10's "failing" case is a claim it does not stop (an apply).
RULE_CASES = {
    1: ("C01", "G01"), 2: ("C01", "C31"), 3: ("C01", "G07"), 4: ("K07", "C25"), 5: ("C28", "C27"), 6: ("C23", "C23b"),
    7: ("C26", "C26b"), 8: ("C22", "C40"), 9: ("C03", "C42"), 10: ("C21", "C01"), 11: ("C13", "C14"),
    12: ("C09", "C10b"), 13: ("C04", "C04b"), 14: ("K05", "K02"), 15: ("E01", "E06"), 16: ("C01", "G02"),
    17: ("C01", "G03"), 18: ("C01", "G04"), 19: ("C01", "G05"), 20: ("C01", "G06"),
}
_FAIL_RULE = {7: 6, 10: None}


@pytest.mark.parametrize("rule", sorted(RULE_CASES))
def test_rule_passes_and_fails(rule):
    ok_id, bad_id = RULE_CASES[rule]
    ok_v = run_case(CASES[ok_id], DATA["oracle"][ok_id])
    bad_v = run_case(CASES[bad_id], DATA["oracle"][bad_id])
    assert disagreements(CASES[ok_id], ok_v) == [] and disagreements(CASES[bad_id], bad_v) == []
    if rule == 10:
        assert (ok_v[0].verdict, ok_v[0].rule) == ("agree", 10) and bad_v[0].verdict == "apply"
        return
    assert all(v.rule != rule or v.verdict == "agree" for v in ok_v)
    assert ok_v[0].verdict in ("apply", "agree") or (rule == 4 and ok_v[0].verdict == "refuse")
    assert bad_v[0].rule == _FAIL_RULE.get(rule, rule) and bad_v[0].verdict in ("abstain", "refuse")


def test_every_rule_has_a_case_in_both_directions_in_the_fixture():
    fails = {c["expect"]["invalid_rule"] for c in CASES.values()}
    passes = {n for c in CASES.values() for n in c["expect"]["exercises"]}
    assert set(range(1, 21)) - {7, 10} <= fails
    assert set(range(1, 21)) <= passes


# -- the rule functions directly (inputs the fixture cannot express) -----------------------------------------------

UID = "demo_mc_unit"
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


def _ctx(people=(), edges=(), rows=(), texts=("juno is 8 not 7",), **kw):
    turns = [vm.Turn(i, T0.replace(minute=5 * i), t, f"t{i}") for i, t in enumerate(texts)]
    return vm.Context(UID, turns, list(rows), vm.Graph([vm.Person(*p) for p in people], edges), **kw)


def _fail_rule(fn, *a, **k):
    with pytest.raises(vm._Fail) as e:
        fn(*a, **k)
    return e.value.rule


def test_rule_3_refuses_a_prefix_tier_and_rule_5_an_ambiguous_one():
    assert _fail_rule(vm.rule_3_tier, people_graph.Resolution("unique", "prefix", "jun", ())) == 3
    vm.rule_3_tier(people_graph.Resolution("unique", "token", "jun", ()))
    ctx = _ctx(people=[("p1", "Sam"), ("p2", "Sam")], texts=("sam is 8 not 7",))
    assert _fail_rule(vm.resolve_subject, "Sam", 0, ctx, allow_label=True) == 5


def test_rule_4_hyphen_parts_never_match_but_whole_parts_do():
    ctx = _ctx(people=[("p1", "Ana-Lise"), ("p2", "Ana Moss")], texts=("ana is 7 not 6",))
    ref = vm.resolve_subject("ana", 0, ctx, allow_label=True)
    assert ref == {"kind": "person", "id": "p2"}               # Ana-Lise filtered out: Ana Moss is the one Ana
    ctx = _ctx(people=[("p1", "Ana-Lise")], texts=("ana lise is 7",))
    assert vm.resolve_subject("ana lise", 0, ctx, allow_label=True) == {"kind": "person", "id": "p1"}


def test_rule_6_a_name_carried_over_a_pronoun_must_have_been_said_earlier_not_later():
    ctx = _ctx(people=[("p1", "Juno")], texts=("she is 7 not 6", "my daughter juno started school"))
    assert _fail_rule(vm.resolve_subject, "Juno", 0, ctx, allow_label=True) == 6
    assert vm.resolve_subject("Juno", 1, ctx, allow_label=True) == {"kind": "person", "id": "p1"}


def test_rule_6_label_and_member_subjects():
    ctx = _ctx(texts=("the boat fund is 13.5k",))
    assert vm.resolve_subject("the boat fund", 0, ctx, allow_label=True) == {"kind": "label", "key": "boat fund"}
    assert vm.resolve_subject("I", 0, ctx, allow_label=True) == {"kind": "member", "id": UID}
    assert _fail_rule(vm.resolve_subject, "it", 0, ctx, allow_label=True) == 6
    assert _fail_rule(vm.resolve_subject, "boat fund", 0, ctx, allow_label=False, none_rule=15) == 15


def test_rule_7_the_lazy_possessive_both_ways_and_never_over_an_exact_name():
    ctx = _ctx(people=[("p1", "Kai")], texts=("kais 21 not 22",))
    assert vm.resolve_subject("Kai", 0, ctx, allow_label=True)["id"] == "p1"     # the model gave Y
    assert vm.resolve_subject("kais", 0, ctx, allow_label=True)["id"] == "p1"    # the model gave the typed X
    ctx = _ctx(people=[("p1", "Kai"), ("p2", "Kais")], texts=("kais 21 not 22",))
    assert vm.resolve_subject("kais", 0, ctx, allow_label=True)["id"] == "p2"    # an exact X always wins


def test_a_nickname_is_not_resolved():
    ctx = _ctx(people=[("p1", "Beatrix Cole")], texts=("bea is 8 not 7",))
    assert vm.resolve_subject("bea", 0, ctx, allow_label=True) == {"kind": "label", "key": "bea"}


def test_rule_14_a_pet_with_an_empty_relationship_is_a_person_and_the_member_is_never_an_animal():
    ctx = _ctx(people=[("p1", "Brindle", "")])
    assert vm.rule_14_kind("Brindle", "animal", ctx, []) == ("refuse", 14)
    ctx = _ctx(people=[("p1", "Brindle", "pet")])
    assert vm.rule_14_kind("Brindle", "animal", ctx, []) == ("agree", None)
    assert vm.rule_14_kind("Brindle", "person", ctx, []) == ("refuse", 14)
    assert vm.rule_14_kind("I", "animal", ctx, []) == ("refuse", 14)


def test_rule_15_a_missing_or_failing_detector_files_nothing():
    claim = mc.EmergencyClaim(0, "i fell and cant get up", "I", "fall", "current")
    ref = {"kind": "member", "id": UID}
    assert _fail_rule(vm.rule_15_emergency, claim, ref, _ctx(texts=("i fell and cant get up",))) == 15

    def broken(_text):
        raise RuntimeError("x")

    assert _fail_rule(vm.rule_15_emergency, claim, ref, _ctx(detector=broken)) == 15
    vm.rule_15_emergency(claim, ref, _ctx(detector=medical_safety.detect))


def test_rule_18_a_forgotten_subject_or_an_unreadable_ledger_blocks():
    claim = mc.CorrectionClaim(0, "juno is 8 not 7", "Juno", "age", "8", "current", "7")
    assert _fail_rule(vm.rule_18_not_forgotten, claim, _ctx(forgotten=lambda s: "juno" in s.lower())) == 18

    def broken(_s):
        raise OSError("x")

    assert _fail_rule(vm.rule_18_not_forgotten, claim, _ctx(forgotten=broken)) == 18
    vm.rule_18_not_forgotten(claim, _ctx(forgotten=lambda s: False))


def test_a_malformed_output_or_claim_is_an_abstain_never_an_exception():
    ctx = _ctx()
    assert [(v.verdict, v.rule) for v in vm.validate("{not json", ctx)] == [("abstain", 2)]
    assert [(v.verdict, v.rule) for v in vm.validate({"claims": [{"type": "correction"}]}, ctx)] == [("abstain", 2)]
    assert vm.validate({"claims": [], "abstain": "unsure"}, ctx) == ()


def test_an_unexpected_error_inside_is_an_abstain(monkeypatch):
    def boom(*_a, **_k):
        raise ZeroDivisionError

    monkeypatch.setattr(vm, "rule_8_values", boom)
    case = CASES["C01"]
    assert [(v.verdict, v.rule) for v in run_case(case, DATA["oracle"]["C01"])] == [("abstain", 0)]


def test_a_day_that_the_stored_month_does_not_have_abstains_on_rule_8():
    text = "Book club is on the 12th of February"
    rows = [vm.Row("r0", text, attrs=(vm.Attr({"kind": "label", "key": "book club"}, "event_date", {"m": 2, "d": 12},
                                              "date", f"{text.index('12th')}:{len(text)}"),))]
    ctx = _ctx(rows=rows, texts=("book club moved to the 31st",))
    claim = {"type": "correction", "turn": 0, "quote": "book club moved to the 31st", "subject": "book club",
             "attribute": "event_date", "new": "the 31st", "time": "current"}
    assert [(v.verdict, v.rule) for v in vm.validate({"claims": [claim]}, ctx)] == [("abstain", 8)]
    ok = dict(claim, quote="book club moved to the 28th", new="the 28th")
    ctx = _ctx(rows=rows, texts=("book club moved to the 28th",))
    assert vm.validate({"claims": [ok]}, ctx)[0].edits[0].new == {"m": 2, "d": 28}


# -- chains: an older claim never overwrites a newer one ----------------------------------------------------------

def _book(d: int, minute: int):
    return {"type": "correction", "turn": minute // 5, "quote": f"book club moved to the {d}th", "subject": "book club",
            "attribute": "event_date", "new": f"the {d}th", "time": "current"}


def test_a_chain_inside_one_session_is_ordered_by_when_it_was_said():
    text = "Book club is on the 5th of December"
    rows = [vm.Row("r0", text, attrs=(vm.Attr({"kind": "label", "key": "book club"}, "event_date", {"m": 12, "d": 5},
                                              "date", f"{text.index('5th')}:{len(text)}"),))]
    texts = ("book club moved to the 12th", "book club moved to the 19th", "book club moved to the 5th")
    ctx = _ctx(rows=rows, texts=texts)
    out = vm.validate({"claims": [_book(5, 10), _book(19, 5), _book(12, 0)]}, ctx)       # emitted out of order
    assert [v.verdict for v in out] == ["apply"] * 3
    by_turn = {v.anchor[0]: v for v in out}
    assert [by_turn[t].edits[0].new for t in (0, 1, 2)] == [{"m": 12, "d": 12}, {"m": 12, "d": 19}, {"m": 12, "d": 5}]
    final = [e for v in out for e in v.registry if e.role == "asserted"]
    assert max(final, key=lambda e: e.said_at).value_norm == {"m": 12, "d": 5}            # and back to the 5th


def test_is_stale_drops_an_old_value_and_keeps_one_reasserted_later():
    ref = {"kind": "label", "key": "boat fund"}
    t = [T0.replace(hour=h) for h in (8, 9, 10, 11, 12)]
    v12, v135 = {"v": "12000", "cur": "AUD"}, {"v": "13500", "cur": "AUD"}
    reg = [vm.Registered(ref, "amount", "superseded", v12, t[1]), vm.Registered(ref, "amount", "asserted", v135, t[1])]
    assert vm.is_stale(reg, ref, "amount", v12, t[0])            # mined from before the correction: stale
    assert not vm.is_stale(reg, ref, "amount", v12, t[2])        # said after it: current
    reg += [vm.Registered(ref, "amount", "superseded", v135, t[3]), vm.Registered(ref, "amount", "asserted", v12, t[3])]
    assert not vm.is_stale(reg, ref, "amount", {"v": "12000.00", "cur": "AUD"}, t[0])   # 12k -> 13.5k -> 12k: current
    assert vm.is_stale(reg, ref, "amount", v135, t[2])
    assert not vm.is_stale(reg, {"kind": "label", "key": "savings"}, "amount", v12, t[0])


# -- negative controls: each rule family goes red without its rule ------------------------------------------------

def _verdicts(cid):
    return [(v.verdict, v.rule) for v in run_case(CASES[cid], DATA["oracle"][cid])]


@pytest.mark.parametrize("cid, target, attr, stub, want", [
    ("G02", vm, "rule_16_speaker", lambda *a, **k: None, "apply"),                         # gates
    ("G01", sc, "quote_basis", lambda *a, **k: "raw", "apply"),                            # the quote wall
    ("C25", vm, "rule_4_whole_parts", lambda name, res: tuple(res.matches), "apply"),      # Ana-Lise rewritten
    ("C40", mc, "age_at", lambda *a, **k: None, "apply"),                                  # values
    ("C14", vm, "rule_11_year", lambda a, new, stored, s: mc.merge_date(new, stored)[0], "apply"),   # rows
    ("C04b", vm, "rule_13_order", lambda *a, **k: None, "apply"),                         # the chain
    ("K05", mc, "is_pet_relationship", lambda rel: False, "refuse"),                       # kind from the graph
    ("E06b", vm, "rule_15_emergency", lambda *a, **k: None, "apply"),                      # the helper misfiled
])
def test_negative_control(monkeypatch, cid, target, attr, stub, want):
    before = _verdicts(cid)
    monkeypatch.setattr(target, attr, stub)
    after = _verdicts(cid)
    assert after != before and after[0][0] == want


# -- the hard rule: no regex, no phrase list in the validator -------------------------------------------------------

def _docstring_nodes(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def test_the_validator_imports_no_regex_and_holds_only_vocabulary_strings():
    tree = ast.parse(Path(vm.__file__).read_text(encoding="utf-8"))
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imports & {"re", "regex", "fnmatch", "lexicons"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)], "no f-strings: they build phrasing"
    docs = _docstring_nodes(tree)
    allowed = mc.VALIDATOR_VOCABULARY | set(mc.LOG_CODES) | {""}      # an empty default carries no phrasing
    strays = sorted({n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and id(n) not in docs and n.value not in allowed})
    assert strays == [], f"string constants outside the vocabulary: {strays}"


def test_the_ast_check_catches_a_phrase_list_and_a_regex():
    bad = ast.parse('import re\nWORDS = ("cat", "dog")\n')
    assert any(isinstance(n, ast.Import) and n.names[0].name == "re" for n in ast.walk(bad))
    assert {n.value for n in ast.walk(bad) if isinstance(n, ast.Constant)} - mc.VALIDATOR_VOCABULARY == {"cat", "dog"}


def test_the_vocabulary_is_tied_to_the_code_it_names():
    import correction_apply

    assert mc.WRITER_CLASS == correction_apply.SOURCE
    assert set(mc.EVENTS) == set(medical_safety._EVENT_WHAT)
    for spec in mc.ATTRIBUTES.values():
        assert spec.predicate in sc.PREDICATES or spec.predicate.startswith("kin:")


# -- the golden set (design note G, PR 2467): every case this validator can decide runs; the rest skip BY NAME ----------
#
# THE SKIPS ARE A RATCHET. fixtures/memory_claims_golden_validator_skips.json names every golden case this validator-only
# runner cannot decide yet, with its cause and the disagreement it reports today. An entry may only be REMOVED (the steps
# 6-7 builder turns a group green and deletes its entries), never added without a reason in review:
# ``test_the_golden_skips_only_shrink`` pins the ceiling and ``test_a_skipped_golden_case_still_fails`` goes red the
# moment a skipped case starts passing, so a stale skip cannot linger. Forget (F01-F04) and log (K10) cases are the
# registry erasers' and the writer's (design note steps 5-6), not this validator's.

GOLDEN_CLASSES = ("correction", "kind", "emergency")
GOLDEN_SKIPS_PATH = HERE / "fixtures" / "memory_claims_golden_validator_skips.json"
#: the number of skipped golden cases when the list was written (2026-11-14): it may only go DOWN
GOLDEN_SKIP_CEILING = 77


def _golden() -> tuple:
    if not GOLDEN.exists() or not GOLDEN_ORACLE.exists():
        return [], {}
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    oracle = json.loads(GOLDEN_ORACLE.read_text(encoding="utf-8"))
    cases = golden.get("cases", golden) if isinstance(golden, dict) else golden
    for key in ("oracle", "cases"):                  # the golden oracle file is {"version", "oracle": {id: output}}
        if isinstance(oracle, dict) and isinstance(oracle.get(key), dict):
            oracle = oracle[key]
            break
    return [c for c in cases if c.get("class") in GOLDEN_CLASSES], oracle


GOLDEN_CASES, GOLDEN_ORACLE_OUT = _golden()
GOLDEN_SKIPS = json.loads(GOLDEN_SKIPS_PATH.read_text(encoding="utf-8"))
SKIPPED = GOLDEN_SKIPS["cases"]


def _golden_params() -> list:
    out = []
    for case in GOLDEN_CASES:
        entry = SKIPPED.get(case["id"])
        marks = [pytest.mark.skip(reason=GOLDEN_SKIPS["reasons"][entry["cause"]])] if entry else []
        out.append(pytest.param(case, id=case["id"], marks=marks))
    return out or [pytest.param(None, id="golden-absent")]


def _golden_problems(case) -> list:
    if case["id"] not in GOLDEN_ORACLE_OUT:
        return ["no oracle output"]
    try:
        return disagreements(case, run_case(case, GOLDEN_ORACLE_OUT[case["id"]]))
    except Exception as exc:  # noqa: BLE001 - a case the runner cannot read is a reported failure
        return [f"runner error {type(exc).__name__}"]


@pytest.mark.parametrize("case", _golden_params())
def test_golden_case(case):
    if case is None:
        pytest.skip("golden set not on this branch: tests/fixtures/memory_claims_golden.json + "
                    "memory_claims_recorded/oracle.json land with design-note step 2")
    assert _golden_problems(case) == []


def test_the_golden_skips_only_shrink():
    assert len(SKIPPED) <= GOLDEN_SKIP_CEILING, "a golden skip was ADDED: that needs a reason in review"
    assert set(GOLDEN_SKIPS["reasons"]) >= {e["cause"] for e in SKIPPED.values()}
    for cause in ("attrs", "edges", "end_to_end"):
        assert "redesign steps 6-7" in GOLDEN_SKIPS["reasons"][cause]
    if GOLDEN_CASES:
        assert set(SKIPPED) <= {c["id"] for c in GOLDEN_CASES}, "a skip names a case the golden set no longer has"


def test_a_skipped_golden_case_still_fails():
    if not GOLDEN_CASES:
        pytest.skip("golden set not on this branch")
    green = [c["id"] for c in GOLDEN_CASES if c["id"] in SKIPPED and not _golden_problems(c)]
    assert green == [], f"these skipped golden cases pass now: remove their entries from {GOLDEN_SKIPS_PATH.name}"
