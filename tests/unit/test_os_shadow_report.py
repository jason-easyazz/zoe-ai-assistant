"""The week-of-shadow reader turns OS_SHADOW_LIST_ADD lines into the comparison table (scripts/maintenance/os_shadow_report.py)."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("os_shadow_report", ROOT / "scripts" / "maintenance" / "os_shadow_report.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)


def line(result="match", kernel="verified", reason="-", hop=1.0, src="spoken", cls="shopping"):
    return (f"2030-01-01 10:00:00 INFO os_shadow: OS_SHADOW_LIST_ADD src={src} cls={cls} live=added kernel={kernel} "
            f"result={result} reason={reason} hop_ms={hop:.1f} q_ms=0.1")


def test_the_table_for_a_synthetic_week():
    lines = [line(hop=h) for h in (1, 2, 3, 4, 5, 6, 7, 8, 9, 40)]
    lines += [line("kernel_refused_live_did", "refused", "no_grant", 2.0, src="rest", cls="todo"),
              line("kernel_unavailable", "none", "circuit", 0.0), line("timeout", "none", "timeout", 500.0),
              line("member_not_in_roster", "none", "-", 0.0), "unrelated INFO line", "OS_SHADOW worker error=KeyError"]
    s = rep.summarize(rep.parse(lines))
    assert s["adds_logged"] == 14 and s["compared"] == 13 and s["match"] == 10 and s["match_rate"] == round(10 / 13, 4)
    assert s["mismatch"] == {"kernel_refused_live_did": 1, "kernel_unavailable": 1, "timeout": 1}
    assert s["refused_by_reason"] == {"no_grant": 1} and s["not_compared"] == {"member_not_in_roster": 1}
    assert s["kernel_unavailable"] == 1 and s["timeouts"] == 1 and s["hop_answered"] == 11
    assert s["hop_ms_p50"] == 5.0 and s["hop_ms_p95"] == 40.0       # the unavailable / timed-out adds are not in the hop
    assert s["by_src"] == {"spoken": 13, "rest": 1} and s["by_cls"] == {"shopping": 13, "todo": 1}
    text = rep.render(s)
    assert "match: 10 (76.9%)" in text and "kernel unavailable: 1" in text


def test_an_empty_week_does_not_divide_by_zero(tmp_path, capsys):
    f = tmp_path / "log"
    f.write_text("nothing here\n")
    assert rep.main([str(f)]) == 0 and "match: 0 (n/a)" in capsys.readouterr().out
    assert rep.main(["--json", str(f)]) == 0


PLANT = "Quillon-tangerines"


@pytest.mark.parametrize("field", ["src", "cls", "live", "kernel", "result", "reason"])
def test_text_planted_in_any_rendered_field_never_reaches_the_output(tmp_path, capsys, field):
    good = line(result="kernel_refused_live_did", kernel="refused", reason="no_grant")
    planted = re.sub(rf"\b{field}=\S+", f"{field}={PLANT}", good)
    assert PLANT in planted
    f = tmp_path / "log"
    f.write_text(planted + "\n" + good + f" item={PLANT}\n")
    for args in (["--json", str(f)], [str(f)]):
        assert rep.main(args) == 0
        out = capsys.readouterr().out
        assert PLANT not in out and "quillon" not in out.lower()
    s = rep.summarize(rep.parse([planted]))
    assert s["adds_logged"] == 1 and PLANT not in json.dumps(s)


def test_a_duplicate_key_rejects_the_line_rather_than_letting_the_last_copy_win(capsys):
    dup = line() + f" reason={PLANT}"
    assert rep.parse([dup]) == []
    assert rep.parse([line() + " src=rest"]) == []                 # even a duplicate with an allowed value: the writer never repeats a key
    assert len(rep.parse([line()])) == 1


def _client_literal(name):
    """A literal from os_shadow.py, read with ast (this lane never imports zoe-data)."""
    import ast
    tree = ast.parse((ROOT / "services" / "zoe-data" / "os_shadow.py").read_text("utf-8"))
    node = next(n.value for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                and name in [getattr(t, "id", None) for t in (n.targets if isinstance(n, ast.Assign) else [n.target])])
    return ast.literal_eval(node)


def test_the_allowlists_are_the_codes_the_client_writes():
    assert set(_client_literal("RESULTS")) == rep.ALLOWED["result"]                  # exactly: every code the client can write, no more
    assert {"hook", "worker", "code", "pending_forget"} <= rep.ALLOWED["reason"] and "noop" in rep.ALLOWED["live"]     # shadow_error reasons, live_noop
    assert set(_client_literal("LIST_CLASSES")) | {"other", "once", "repeat", "-"} == rep.ALLOWED["cls"]   # reminder/alarm: once|repeat
    assert {code for _, code in _client_literal("_REASONS")} | {"other"} <= rep.ALLOWED["reason"]
    src = (ROOT / "services" / "zoe-data").rglob("*.py")
    sources = {m for p in src if "tests" not in p.parts for m in re.findall(r'note_\w+\(.{0,240}?source="(\w+)"', p.read_text("utf-8"), re.S)}
    assert sources and sources <= rep.ALLOWED["src"], sources - rep.ALLOWED["src"]      # every call site's src is a known code


def act(verb, result="match", kernel="verified", reason="-", hop=1.0, cls="-"):
    return (f"2030-01-01 10:00:00 INFO os_shadow: OS_SHADOW_ACT verb={verb} src=service cls={cls} live=added kernel={kernel} "
            f"result={result} reason={reason} hop_ms={hop:.1f} q_ms=0.1")


def test_the_per_verb_table_buckets_every_line_exactly_once():
    lines = [act("reminder_create", hop=h) for h in range(1, 11)]                       # 10 agree, hops 1..10
    lines += [act("reminder_create", "not_expressible", "none", "recurring", 0.0, cls="repeat"),
              act("reminder_create", "item_differs", "verified", "-", 3.0),
              act("reminder_create", "timeout", "none", "timeout", 1500.0),
              act("reminder_create", "dropped_erased", "none", "-", 0.0)]
    lines += [act("alarm_create", "kernel_refused_live_did", "refused", "no_grant", 2.0), act("alarm_create", hop=4.0)]
    lines += [line(hop=2.0)]                                                              # the older marker reads as list_add
    s = rep.summarize(rep.parse(lines))
    r = s["verbs"]["reminder_create"]
    assert (r["seen"], r["agree"], r["disagree"], r["unavailable"], r["not_compared"]) == (14, 10, 2, 1, 1)
    assert r["agree"] + r["disagree"] + r["unavailable"] + r["not_compared"] == r["seen"]
    assert r["agree_rate"] == round(10 / 12, 4)                                           # unavailable is never in the rate
    assert r["disagree_by_reason"] == {"item_differs:-": 1, "not_expressible:recurring": 1}
    assert r["hop_answered"] == 11 and r["hop_ms_p50"] == 5.0 and r["hop_ms_p90"] == 9.0   # the 1500 ms timeout is not a hop
    a = s["verbs"]["alarm_create"]
    assert (a["agree"], a["disagree"], a["agree_rate"]) == (1, 1, 0.5)
    assert list(s["verbs"]) == ["list_add", "reminder_create", "alarm_create"] and s["verbs"]["list_add"]["agree"] == 1
    text = rep.render(s)
    assert "| reminder_create | 14 | 10 | 2 | 1 | 1 | 0 | 83.3% | 7.7% | 5.0 | 9.0 |" in text


def test_a_result_code_the_reader_does_not_know_counts_as_disagree():
    s = rep.summarize(rep.parse([act("list_remove", "something_new", "verified")]))
    assert s["verbs"]["list_remove"]["disagree"] == 1 and s["verbs"]["list_remove"]["agree_rate"] == 0.0


@pytest.mark.parametrize("field", ["verb", "src", "cls", "live", "kernel", "result", "reason"])
def test_text_planted_in_any_field_of_a_verb_line_never_reaches_the_per_verb_table(tmp_path, capsys, field):
    good = act("reminder_create", "not_expressible", "none", "recurring", 0.0, cls="repeat")
    planted = re.sub(rf"\b{field}=\S+", f"{field}={PLANT}", good)
    assert PLANT in planted
    f = tmp_path / "log"
    f.write_text(planted + "\n" + good + "\n")
    for args in (["--json", str(f)], [str(f)]):
        assert rep.main(args) == 0
        assert PLANT not in capsys.readouterr().out
    assert rep.parse([good + f" verb={PLANT}"]) == [] and rep.parse([line() + " verb=alarm_create"]) == []   # no smuggled verb


def test_the_verb_and_not_expressible_allowlists_are_the_clients():
    assert set(_client_literal("VERBS")) == rep.ALLOWED["verb"] == set(rep.VERB_ORDER)
    import ast
    src = (ROOT / "services" / "zoe-data" / "os_shadow.py").read_text("utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "kernel_args")
    why = {e.elts[1].value for e in ast.walk(fn) if isinstance(e, ast.Tuple) and len(e.elts) == 2
           and isinstance(e.elts[0], ast.Constant) and e.elts[0].value is None}
    assert why == {"recurring", "no_time", "range", "time"} and why | {"parse"} <= rep.ALLOWED["reason"]


def test_nothing_is_dropped_unknown_codes_and_unreadable_lines_are_counted(tmp_path, capsys):
    lines = [line(), line("something_new", "verified"), line("live_noop", "none").replace("live=added", "live=noop"),
             line("shadow_error", "none", "worker", 0.0), line("no_input", "none"), line(src="brand_new_src"),
             act("alarm_create", "another_new_code"), act("alarm_create", "shadow_error", "none", "hook", 0.0),
             line() + " src=rest",                                   # a repeated key: rejected, and counted
             "OS_SHADOW_ACT verb=list_add src=spoken hop_ms=1.0"]    # no result: rejected, and counted
    recs, rejected = rep.parse_counted(lines)
    s = rep.summarize(recs, rejected)
    assert s["lines_logged"] == 8 and s["rejected"] == 2 and s["lines_logged"] + s["rejected"] == len(lines)
    assert s["adds_logged"] == 6                                       # the list-add totals count list-add lines only
    assert s["unknown"] == 2 and s["unknown_fields"] == 1 and s["shadow_errors"] == 2
    la, al = s["verbs"]["list_add"], s["verbs"]["alarm_create"]
    assert (la["seen"], la["agree"], la["disagree"], la["not_compared"], la["unknown"]) == (6, 2, 1, 3, 1)
    assert (al["seen"], al["disagree"], al["not_compared"], al["unknown"], al["shadow_errors"]) == (2, 1, 1, 1, 1)
    for v in s["verbs"].values():
        assert v["agree"] + v["disagree"] + v["unavailable"] + v["not_compared"] == v["seen"]
    assert la["disagree_by_result"] == {"unknown": 1}                # shown by its own name, never folded away
    f = tmp_path / "log"
    f.write_text("\n".join(lines) + "\n")
    assert rep.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "rejected lines 2" in out and "unknown result 2" in out and "something_new" not in out and "another_new" not in out


def test_every_literal_reason_the_client_writes_is_known_to_the_reader():
    """Static: every string the client can put in ``reason=`` (an outcome dict, a ``reason=`` keyword, an assignment to ``reason``)
    is in the reader's allowlist, so a new reason is never shown as ``unknown`` by accident."""
    import ast

    def consts(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return {n.value}
        if isinstance(n, ast.IfExp):
            return consts(n.body) | consts(n.orelse)
        return set()

    tree = ast.parse((ROOT / "services" / "zoe-data" / "os_shadow.py").read_text("utf-8"))
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            found |= {c for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "reason" for c in consts(v)}
        if isinstance(n, ast.Call):
            found |= {c for kw in n.keywords if kw.arg == "reason" for c in consts(kw.value)}
        if isinstance(n, ast.Assign):
            for t in n.targets:
                names = [t] if isinstance(t, ast.Name) else (t.elts if isinstance(t, ast.Tuple) else [])
                vals = [n.value] if isinstance(t, ast.Name) else (n.value.elts if isinstance(n.value, ast.Tuple) else [])
                found |= {c for tn, v in zip(names, vals) if getattr(tn, "id", "") == "reason" for c in consts(v)}
    found.discard("")
    assert found and found <= rep.ALLOWED["reason"], found - rep.ALLOWED["reason"]



# ---- the list-add totals are list adds; every verb is judged on its own, against the owner's five numbers -------------------------
def test_other_verbs_in_the_log_never_change_the_list_add_numbers():
    adds = [line(hop=h) for h in (1, 2, 3)] + [line("kernel_refused_live_did", "refused", "no_grant", 2.0), line("timeout", "none", "timeout", 0)]
    others = ([act("reminder_create", "schedule_differs", hop=50.0)] * 7 + [act("alarm_create", "kernel_unavailable", "none", "circuit", 0.0)] * 4
              + [act("list_remove", hop=99.0)] * 5)
    alone, mixed = rep.summarize(rep.parse(adds)), rep.summarize(rep.parse(adds + others))
    keys = ("adds_logged", "compared", "match", "match_rate", "mismatch", "refused_by_reason", "not_compared", "kernel_unavailable",
            "timeouts", "hop_ms_p50", "hop_ms_p95", "hop_answered", "by_src", "by_cls")
    assert {k: mixed[k] for k in keys} == {k: alone[k] for k in keys}
    assert mixed["verbs"]["list_add"] == alone["verbs"]["list_add"]
    assert (mixed["verbs"]["reminder_create"]["disagree_by_result"], mixed["verbs"]["alarm_create"]["unavailable_rate"]) == (
        {"schedule_differs": 7}, 1.0)
    assert mixed["lines_logged"] == len(adds) + len(others)


def test_an_unknown_verb_and_an_unknown_code_are_their_own_counted_rows(capsys, tmp_path):
    lines = [act("list_add"), act("list_add", "brand_new_code"), act("list_add").replace("verb=list_add", "verb=teleport")]
    s = rep.summarize(rep.parse(lines))
    assert s["verbs"]["unknown"]["seen"] == 1 and s["verbs"]["list_add"]["unknown"] == 1
    f = tmp_path / "log"
    f.write_text("\n".join(lines) + "\n")
    rep.main([str(f)])
    out = capsys.readouterr().out
    assert "| unknown | 1 |" in out and "teleport" not in out and "brand_new_code" not in out


def _week(agree, disagree=0, unavailable=0, hop=1.0, slow=0, reason="-"):
    """``agree`` matches at ``hop`` ms (the slowest ``slow`` of them at 50 ms), ``disagree`` item_differs, ``unavailable`` timeouts."""
    out = [act("reminder_create", hop=(50.0 if i < slow else hop)) for i in range(agree)]
    out += [act("reminder_create", "item_differs", reason=reason, hop=hop)] * disagree
    out += [act("reminder_create", "timeout", "none", "timeout", 0.0)] * unavailable
    return rep.summarize(rep.parse(out))["verbs"]["reminder_create"]["verdict"]


def test_the_pass_line_at_each_boundary():
    assert _week(200)["pass"] and not _week(199)["pass"]                                         # 200 compared actions
    assert _week(199)["fails"] == ["compared 199 < 200"]
    assert _week(995, 5)["pass"]                                                                  # exactly 99.5%
    assert _week(994, 6)["fails"] == ["agreement 99.40% < 99.5%"]
    assert _week(1000, 0, 10)["pass"]                                                             # 0.99%: under 1%
    assert _week(990, 0, 10)["fails"] == ["unavailable 1.00% >= 1%"]                               # 1.0% is not UNDER 1%
    assert _week(991, 0, 9)["pass"]                                                                # 0.9%
    assert _week(300, hop=9.9)["pass"] and _week(300, hop=10.0)["fails"] == ["hop p90 10.00 ms >= 10 ms"]
    assert not _week(300, slow=40)["pass"] and _week(300, slow=20)["pass"]                         # the p90, not the max
    assert _week(995, 5, reason="other")["fails"] == ["5 disagreement(s) with no known code or reason"]


def test_the_pass_line_is_rendered_per_verb(capsys, tmp_path):
    f = tmp_path / "log"
    f.write_text("\n".join([act("alarm_create", hop=2.0)] * 200 + [act("list_add")]) + "\n")
    rep.main([str(f)])
    out = capsys.readouterr().out
    assert "alarm_create: PASS" in out and "list_add: NOT YET (compared 1 < 200)" in out
