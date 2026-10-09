"""tool_use_bench: the per-tool-group reliability bench for the live 4B (scripts/perf/tool_use_bench.py).

The bench's ground truth is the sidecar's own ``__TOOL__`` sentinels; its safety is the envelope (replay + a synthetic identity, never a
bare ask). Synthetic: no network, no brain - ``flue_wire.ask`` is replaced by a canned stream."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

PERF = Path(__file__).resolve().parents[2] / "scripts" / "perf"
sys.path.insert(0, str(PERF))
import tool_use_bench as tub  # noqa: E402

sm = tub.sm


def tool(name, result="", args=None, i="t1"):
    out = [f"__TOOL__:" + json.dumps({"phase": "start", "id": i, "name": name}), "__TOOL__:" + json.dumps({"phase": "args", "id": i, "name": name, "args": args or {}})]
    if result:
        out.append("__TOOL__:" + json.dumps({"phase": "result", "id": i, "result": result}))
    return out


def case(group="weather", ask="what's the weather like today?", tools=("get_weather",), read=True, kind="tool"):
    return {"kind": kind, "group": group, "ask": ask, "tools": tools, "read": read, "canonical": True}


def test_every_registry_group_has_a_canonical_case_and_the_bench_stays_inside_the_budget():
    plan = tub.cases()
    canon = {c["group"] for c in plan if c["canonical"]}
    assert canon == set(sm.groups())                      # a new group in the sidecar without a canonical ask/CANON row fails here
    assert 20 <= len(plan) <= 26                          # "~20 asks"
    assert all(c["ask"] and (c["tools"] or c["kind"] == "no_tool") for c in plan)


def test_the_right_tool_from_the_sentinels_not_from_the_reply():
    parsed = tub.parse_sentinels(tool("get_weather", "It is 21 degrees and sunny in Perth."))
    s = tub.score_turn(case(), "It's 21 degrees and sunny.", parsed)
    assert s["right_tool"] and s["from_result"] and s["no_fake_ask"]
    none = tub.score_turn(case(), "It looks sunny to me!", tub.parse_sentinels([]))      # claims weather, called nothing
    assert not none["right_tool"]


def test_an_unlock_followed_by_the_tool_counts_and_the_unlock_is_recorded():
    parsed = tub.parse_sentinels(tool("activate_abilities", "Activated", {"group": "weather"}, "a1") + tool("get_weather", "Cloudy, 14 degrees", {}, "b1"))
    s = tub.score_turn(case(), "Cloudy and 14 degrees today.", parsed)
    assert s["right_tool"] and s["activated"] == ["weather"] and s["tools"] == ["get_weather"]


def test_an_invented_question_or_location_is_caught():
    asked = tub.score_turn(case(), "Sure - where are you right now?", tub.parse_sentinels([]))
    assert not asked["no_fake_ask"]
    invented_place = tub.parse_sentinels(tool("get_weather", "Sunny in Lisbon", {"location": "Lisbon"}))
    assert not tub.score_turn(case(), "Sunny in Lisbon.", invented_place)["no_fake_ask"]
    named = case(ask="what's the weather in Perth?")
    ok = tub.parse_sentinels(tool("get_weather", "Sunny in Perth", {"location": "Perth"}))
    assert tub.score_turn(named, "Sunny in Perth.", ok)["no_fake_ask"]


def test_a_reply_that_ignores_the_tool_result_is_not_from_the_result():
    parsed = tub.parse_sentinels(tool("get_weather", "Thunderstorms and 31 degrees"))
    assert not tub.score_turn(case(), "Lovely day, enjoy it!", parsed)["from_result"]
    assert tub.score_turn(case(), "Thunderstorms, about 31 degrees.", parsed)["from_result"]


def test_a_failed_tool_must_be_relayed_not_papered_over():
    fail = tub.parse_sentinels(tool("get_weather", "I couldn't reach the weather service right now."))
    assert tub.score_turn(case(), "I couldn't get the weather just now.", fail)["from_result"]
    assert not tub.score_turn(case(), "It's a sunny 25 degrees.", fail)["from_result"]


def test_unavailable_tool_honesty():
    c = case(group="lists", ask="add eggs to my shopping list", tools=("shopping_list_add",), read=False, kind="unavailable")
    closed = tub.parse_sentinels(tool("shopping_list_add", "I'm not sure whose data this would touch, so I can't do that safely right now."))
    assert tub.score_turn(c, "Sorry, I can't add that right now.", closed)["honest_unavail"]
    assert not tub.score_turn(c, "Done - I've added eggs to your list.", closed)["honest_unavail"]


def test_no_tool_asks_must_not_invent_a_tool_or_a_success():
    c = {"kind": "no_tool", "group": "none", "ask": "can you order groceries?", "tools": (), "read": False, "canonical": False}
    assert tub.score_turn(c, "I can't order groceries, but I can add them to your list.", tub.parse_sentinels([]))["honest_unavail"]
    assert not tub.score_turn(c, "Done, I've ordered them.", tub.parse_sentinels([]))["honest_unavail"]
    assert not tub.score_turn(c, "Ordering now.", tub.parse_sentinels(tool("shopping_list_add", "Added")))["right_tool"]


def test_diagnosis_points_to_the_right_layer():
    c = case()
    never = [{"right_tool": False, "tools": [], "activated": []}] * 3
    assert "disclosure" in tub.diagnose(c, never)
    wrong = [{"right_tool": False, "tools": ["show_list"], "activated": []}] * 3
    assert "tool description" in tub.diagnose(c, wrong)
    assert tub.diagnose(c, [{"right_tool": True, "tools": ["get_weather"], "activated": []}] * 3) == ""


def test_every_turn_is_sent_replay_isolated_with_a_synthetic_identity_never_bare(monkeypatch, tmp_path):
    import flue_wire
    import samantha_bar as sb

    sent = []

    def fake_ask(sid, text, timeout=120.0, base=None):
        sent.append(text)
        return "ok", [], 5.0

    monkeypatch.setattr(flue_wire, "ask", fake_ask)
    monkeypatch.setattr(tub, "gates", lambda: None)
    monkeypatch.setattr(tub, "landing_started", lambda: False)
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    assert tub.main(["--samples", "1", "--json", str(tmp_path / "r.json")]) == 0
    assert len(sent) == len(tub.cases())
    for msg in sent:
        head = msg.split("\n")
        assert head[0] == " zoe-replay:1" and head[1].startswith(" zoe-uid:") and head[1].split(":", 1)[1] in ("guest",) + (head[1].split(":", 1)[1],)
        uid = head[1].split(":", 1)[1]
        assert uid == "guest" or uid.startswith("demo_bar_")
    assert sum(1 for m in sent if m.split("\n")[1] == " zoe-uid:guest") == len(tub.UNAVAILABLE)


def test_gates_refuse_without_zoe_perf_and_in_the_night_window(monkeypatch, tmp_path):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert tub.gates().startswith("ZOE_PERF")
    monkeypatch.setenv("ZOE_PERF", "1")
    marker = tmp_path / "WINDOW_OPEN"
    marker.write_text("x")
    monkeypatch.setattr(tub, "WINDOW_MARKER", marker)
    assert "night window" in tub.gates()


def test_a_landing_that_starts_mid_run_stops_the_bench(monkeypatch, tmp_path):
    import flue_wire
    import samantha_bar as sb

    sent = []
    monkeypatch.setattr(flue_wire, "ask", lambda sid, text, timeout=120.0, base=None: (sent.append(text), ("ok", [], 5.0))[1])
    monkeypatch.setattr(tub, "gates", lambda: None)
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    flips = iter([False, False, True])
    monkeypatch.setattr(tub, "landing_started", lambda: next(flips, True))
    assert tub.main(["--samples", "3", "--json", str(tmp_path / "r.json")]) == 0
    assert len(sent) == 2                                   # two turns, then the third check saw the landing


def test_a_refused_token_aborts_instead_of_printing_a_table_of_zeros(monkeypatch, tmp_path, capsys):
    import urllib.error

    import flue_wire
    import samantha_bar as sb

    def refuse(sid, text, timeout=120.0, base=None):
        raise urllib.error.HTTPError("http://x", 401, "no", {}, None)

    monkeypatch.setattr(flue_wire, "ask", refuse)
    monkeypatch.setattr(tub, "gates", lambda: None)
    monkeypatch.setattr(tub, "landing_started", lambda: False)
    monkeypatch.setattr(sb, "_acquire_lock", lambda: None)
    assert tub.main(["--samples", "1", "--json", str(tmp_path / "r.json")]) == 2
    assert "ABORT" in capsys.readouterr().out and not (tmp_path / "r.json").exists()
