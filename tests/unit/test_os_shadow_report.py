"""The week-of-shadow reader turns OS_SHADOW_LIST_ADD lines into the comparison table (scripts/maintenance/os_shadow_report.py)."""
import importlib.util
import json
import math
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
    assert set(_client_literal("LIST_CLASSES")) | {"other"} == rep.ALLOWED["cls"]
    assert {code for _, code in _client_literal("_REASONS")} | {"other"} <= rep.ALLOWED["reason"]
    src = (ROOT / "services" / "zoe-data").rglob("*.py")
    sources = {m for p in src if "tests" not in p.parts for m in re.findall(r'note_add\(.{0,240}?source="(\w+)"', p.read_text("utf-8"), re.S)}
    assert sources and sources <= rep.ALLOWED["src"], sources - rep.ALLOWED["src"]      # every call site's src is a known code


def test_nothing_is_dropped_unknown_codes_and_unreadable_lines_are_counted(tmp_path, capsys):
    lines = [line(), line("something_new", "verified"), line("live_noop", "none").replace("live=added", "live=noop"),
             line("shadow_error", "none", "worker", 0.0), line("no_input", "none"), line(src="brand_new_src"),
             line() + " src=rest",                                   # a repeated key: rejected, and counted
             "OS_SHADOW_LIST_ADD src=spoken hop_ms=1.0"]             # no result: rejected, and counted
    recs, rejected = rep.parse_counted(lines)
    s = rep.summarize(recs, rejected)
    assert s["adds_logged"] == 6 and s["rejected"] == 2 and s["adds_logged"] + s["rejected"] == len(lines)
    assert s["unknown"] == 1 and s["unknown_fields"] == 1 and s["shadow_errors"] == 1
    assert s["not_compared"] == {"live_noop": 1, "no_input": 1, "shadow_error": 1}
    assert s["compared"] == 3 and s["match"] == 2 and s["match_rate"] == round(2 / 3, 4)       # the unknown one is not a match
    assert s["match"] + sum(s["mismatch"].values()) + s["unknown"] + sum(s["not_compared"].values()) == s["adds_logged"]
    f = tmp_path / "log"
    f.write_text("\n".join(lines) + "\n")
    assert rep.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "| unknown result (counted as not a match) | 1 |" in out and "rejected lines 2" in out and "something_new" not in out


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


def _client_tuple(name):
    """A tuple literal from os_shadow.py whose items may be names of string constants there (``CROSSED``) or ``A + (...)``."""
    import ast
    tree = ast.parse((ROOT / "services" / "zoe-data" / "os_shadow.py").read_text("utf-8"))
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
              for t in n.targets if isinstance(t, ast.Name)}

    def ev(node):
        if isinstance(node, ast.BinOp):
            return ev(node.left) + ev(node.right)
        if isinstance(node, ast.Name):
            return _client_tuple(node.id)
        return tuple(consts[e.id] if isinstance(e, ast.Name) else e.value for e in node.elts)

    node = next(n.value for n in tree.body if isinstance(n, ast.Assign) and name in [getattr(t, "id", None) for t in n.targets])
    return ev(node)


def test_every_closed_set_the_client_writes_is_the_readers():
    assert set(_client_tuple("SOURCES")) | {"other"} == rep.ALLOWED["src"]
    assert set(_client_tuple("KERNEL_STATES")) == rep.ALLOWED["kernel"]
    assert set(_client_tuple("LIVE")) == rep.ALLOWED["live"]
    assert set(_client_tuple("CLASSES")) <= rep.ALLOWED["cls"]
    assert set(_client_tuple("REASONS")) <= rep.ALLOWED["reason"], set(_client_tuple("REASONS")) - rep.ALLOWED["reason"]
    log = (ROOT / "services" / "zoe-data" / "os_shadow.py").read_text("utf-8")
    keys = re.findall(r"(\w+)=%", log[log.index("OS_SHADOW_LIST_ADD src="):].split('"', 1)[0])
    assert set(keys) <= set(rep.ALLOWED) | set(rep.NUMBERS), keys             # every field the client WRITES, the reader knows


@pytest.mark.parametrize("hop", ["nan", "inf", "-inf", "", "-1.0", "1e300", "abc", None])
def test_a_number_that_is_not_a_measurement_rejects_the_line(hop):
    good = line()
    bad = re.sub(r" hop_ms=\S+", "" if hop is None else f" hop_ms={hop}", good)
    recs, rejected = rep.parse_counted([good] * 200 + [bad])
    s = rep.summarize(recs, rejected)
    assert rejected == 1 and s["adds_logged"] == 200 and s["match"] == 200
    assert math.isfinite(s["hop_ms_p50"]) and math.isfinite(s["hop_ms_p95"]) and s["hop_ms_p95"] == 1.0
    bad_q = good.replace("q_ms=0.1", "q_ms=nan")
    assert rep.parse_counted([bad_q])[1] == 1


def test_an_unknown_key_is_counted_by_a_tag_never_by_its_name(tmp_path, capsys):
    planted = line() + " Zq7Secret=1"
    s = rep.summarize(*rep.parse_counted([planted, planted, line()]))
    tag = rep.key_tag("Zq7Secret")
    assert s["unknown_keys"] == {tag: 2} and s["adds_logged"] == 3
    f = tmp_path / "log"
    f.write_text(planted + "\n")
    rep.main([str(f)])
    out = capsys.readouterr().out
    assert f"{tag}=1" in out and "Zq7Secret" not in out


@pytest.mark.parametrize("cut", ["OS_SHADOW_LIST_ADD", "OS_SHADOW_LIST_ADD\n", "OS_SHADOW_LIST_ADD   ",
                                 "x INFO os_shadow: OS_SHADOW_LIST_ADD src=spoken",                     # the marker plus one field
                                 "x INFO os_shadow: OS_SHADOW_LIST_ADD src=spoken cls=shopping li",      # cut mid-key
                                 "x INFO os_shadow: OS_SHADOW_LIST_ADD src=spoken cls=shopping live=added kernel=verified result=ma"])  # mid-value
def test_a_marker_line_cut_short_anywhere_is_counted_as_rejected(cut, tmp_path, capsys):
    recs, rejected = rep.parse_counted([line(), cut])
    assert len(recs) == 1 and rejected == 1
    f = tmp_path / "log"
    f.write_text(line() + "\n" + cut)                                 # the cut line LAST in the file, newline or not
    assert rep.main([str(f)]) == 0 and "rejected lines 1" in capsys.readouterr().out
