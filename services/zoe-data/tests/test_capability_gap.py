"""Capability-gap clustering (``capability_gap.py`` + ``scripts/maintenance/capability_gap_backlog.py``).

Every utterance, member id and log line here is invented for the test (public repo). Each rule has a negative control.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions + tmp files; no DB, no network, no model

import capability_gap as cg

NOW = datetime(2026, 11, 5, 12, 0, 0).timestamp()
DAY = 86400.0


def L(ts: float, msg: str) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z") + " INFO     " + msg


def ev(source=cg.SRC_INTENT_MISS, *, age_days=1.0, text=None, member=None, area=None, note="", utt=None):
    return cg.Event(NOW - age_days * DAY, source, member, text=text, utt=utt or (cg.utterance_hash(text) if text else None),
                    area=area, note=note)


# ── lexicon ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,area", [
    ("set a timer for ten minutes", "timers"), ("remind me to water the plants", "reminders"),
    ("add bananas to the shopping list", "lists"), ("what is on my calendar tomorrow", "calendar"),
    ("will it rain this weekend", "weather"), ("turn the lamp off", "smart_home"), ("turn it on", "smart_home"),
    ("play some jazz", "music"), ("what time is it", "time"), ("send a message to the neighbour", "communications"),
    ("convert ten miles to kilometres", "knowledge_tools"), ("learn a new skill", "capability_request"),
    ("how are you feeling", "conversation"), ("thanks a lot", "conversation"),
])
def test_classify_area(text, area):
    assert cg.classify(text)[0] == area


def test_classify_negative_controls():
    assert cg.classify("purple monkey dishwasher")[0] == "uncategorised"
    assert cg.classify("")[0] == "uncategorised"
    # "meaning of life" is conversation, not the definitions tool; "meaning of" a word still is
    assert cg.classify("what is the meaning of life")[0] == "conversation"
    assert cg.classify("what is the meaning of serendipity")[0] == "knowledge_tools"
    # a timer outranks the list rule: first match wins by priority
    assert cg.classify("add a timer to my list")[0] == "timers"


def test_classify_action():
    assert cg.classify("cancel the meeting")[1] == "remove"
    assert cg.classify("add milk to the list")[1] == "add"
    assert cg.classify("pause the music")[1] == "playback"
    assert cg.classify("zzz qqq")[1] == "do"


def test_hash_matches_service_tag():
    from log_redaction import utterance_tag
    t = "an invented sentence"
    assert utterance_tag(t).endswith("utt=" + cg.utterance_hash(t))


# ── log parsing ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_parse_struggle_sources():
    base = "struggle_detector: STRUGGLE signal=%s conf=0.85 fix=%s domain=%s path=brain channel=voice user=member-a mode=shadow"
    e = cg.parse_log_line(L(NOW, base % ("CANT_DO", "capability_gap", "smart_home")))
    assert (e.source, e.member, e.area) == (cg.SRC_STRUGGLE_CANT_DO, "member-a", "smart_home")
    assert cg.parse_log_line(L(NOW, base % ("FALLTHROUGH", "router_training", "-"))).source == cg.SRC_STRUGGLE_FALLTHROUGH
    assert cg.parse_log_line(L(NOW, base % ("REPHRASE", "capability_gap", "lists"))).source == cg.SRC_STRUGGLE_REPEAT
    assert cg.parse_log_line(L(NOW, base % ("ABANDON", "answer_quality", "-"))).source == cg.SRC_STRUGGLE_QUALITY


@pytest.mark.parametrize("signal,fix", [("MISHEAR", "stt"), ("VERBOSE", "verbosity"), ("FALLTHROUGH", "router_latency")])
def test_non_capability_struggles_are_ignored(signal, fix):
    line = L(NOW, f"struggle_detector: STRUGGLE signal={signal} conf=0.8 fix={fix} domain=- path=brain channel=voice user=member-a mode=shadow")
    assert cg.parse_log_line(line) is None


def test_parse_other_sources():
    w = cg.parse_log_line(L(NOW, "write_claim_guard: WRITE_CLAIM_BLOCKED action=replaced lane=brain user=member-b lang=en kinds=list reason=no_receipt"))
    assert (w.source, w.member, w.area) == (cg.SRC_WRITE_CLAIM, "member-b", "lists")
    assert cg.parse_log_line(L(NOW, "expert_dispatch: EXPERT_MISS domain=timers score=0.80 (no plan) -> brain")).source == cg.SRC_EXPERT_MISS
    d = cg.parse_log_line(L(NOW, "expert_dispatch: EXPERT_DEFER_WRITE domain=lists intent=list_add (write_ok=False) -> brain"))
    assert (d.source, d.area) == (cg.SRC_EXPERT_DEFER, "lists")
    n = cg.parse_log_line(L(NOW, "nlu_extractor: no extractor for intent extend_capability"))
    assert (n.source, n.area, n.note) == (cg.SRC_NLU_NO_EXTRACTOR, "capability_request", "intent=extend_capability")


def test_harness_and_noise_lines_are_ignored():
    assert cg.parse_log_line(L(NOW, "expert_dispatch: EXPERT_MISS domain=timers score=0.80 (no plan) -> brain traffic=harness")) is None
    assert cg.parse_log_line(L(NOW, "nlu_extractor: no extractor for intent extend_capability traffic=harness")) is None
    assert cg.parse_log_line(L(NOW, "middleware.logging: GET /health 200")) is None
    assert cg.parse_log_line("not a log line at all") is None
    assert cg.parse_log_line("") is None


def test_intent_miss_words_and_hash_forms():
    w = cg.parse_log_line(L(NOW, "intent_router: intent_miss: Open the pod bay doors"))
    assert w.text == "Open the pod bay doors" and w.utt == cg.utterance_hash("Open the pod bay doors")
    h = cg.parse_log_line(L(NOW, "intent_router: intent_miss: len=22 utt=0123456789ab"))
    assert h.text is None and h.utt == "0123456789ab"


def test_shadow_records():
    ok = cg.parse_shadow_record({"actual_routed": "chat", "reason": "low_conf", "ts": NOW, "utt": "aaaaaaaaaaaa", "head_top": "weather"})
    assert ok.source == cg.SRC_ROUTER_UNSURE and ok.area == "weather"
    for bad in ({"actual_routed": "weather", "reason": None}, {"actual_routed": "chat", "reason": "chat_top"},
                {"actual_routed": "chat", "reason": "low_conf", "traffic": "harness"}):
        assert cg.parse_shadow_record({**bad, "ts": NOW, "utt": "aaaaaaaaaaaa"}) is None
    assert cg.parse_shadow_record({"actual_routed": "chat", "reason": "low_conf"}) is None     # no ts/utt


def test_resolver_reads_hash_back():
    r = cg.build_resolver(["Dim the lights please.", "", None])
    assert r[cg.utterance_hash("Dim the lights please.")] == "Dim the lights please."
    assert r[cg.utterance_hash("Dim the lights please")] == "Dim the lights please."
    assert cg.utterance_hash("something else") not in r


# ── de-duplication, windows ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_same_utterance_same_day_counts_once_other_day_twice():
    a = ev(text="Dim the lights", age_days=1.0)
    b = ev(text="Dim the lights", age_days=1.0 - 0.01)
    c = ev(text="Dim the lights", age_days=3.0)
    assert len(cg.dedupe([a, b])) == 1
    assert len(cg.dedupe([a, b, c])) == 2                    # negative control: a different day is a new request
    assert len(cg.dedupe([a, ev(text="Brighten the lights", age_days=1.0)])) == 2


def test_dedupe_keeps_the_version_with_words():
    h = ev(cg.SRC_ROUTER_UNSURE, utt="abcabcabcabc", age_days=1.0)
    t = cg.Event(h.ts + 5, cg.SRC_INTENT_MISS, None, text="x y z", utt="abcabcabcabc")
    kept = cg.dedupe([h, t])
    assert len(kept) == 1 and kept[0].text == "x y z"


def test_burst_collapses_but_spaced_events_do_not():
    mk = lambda off: cg.Event(NOW - DAY + off, cg.SRC_EXPERT_MISS, None, area="timers", note="n")
    assert len(cg.dedupe([mk(0), mk(30), mk(60)])) == 1
    assert len(cg.dedupe([mk(0), mk(cg.BURST_S + 600)])) == 2
    other = cg.Event(NOW - DAY + 10, cg.SRC_EXPERT_MISS, None, area="lists", note="m")
    assert len(cg.dedupe([mk(0), other])) == 2             # different detail: not the same incident


def test_window_excludes_old_events():
    ranked = cg.cluster_events([ev(text="set a timer", age_days=2), ev(text="set a timer please", age_days=40)], now=NOW, days=14)
    assert sum(c.count for c in ranked) == 1


# ── clustering and ranking ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_texts_and_domain_events_cluster_by_area_action():
    events = [ev(text="turn the lamp off"), ev(text="turn the fan off"), ev(text="switch the heater on", age_days=2),
              ev(cg.SRC_STRUGGLE_CANT_DO, area="smart_home", member="m1", note="CANT_DO/smart_home")]
    ranked = cg.cluster_events(events, now=NOW)
    labels = {c.label: c.count for c in ranked}
    assert labels["Smart home control - switch and control"] == 3
    assert labels["Smart home control - Zoe said she could not"] == 1


def test_leftover_words_group_by_overlap_not_by_nothing():
    events = [ev(text="reorganise the garden shed"), ev(text="reorganise garden shed now", age_days=2),
              ev(text="quantum pancake diplomacy")]
    ranked = cg.cluster_events(events, now=NOW)
    assert sorted(c.count for c in ranked) == [1, 2]
    assert all(c.area == "uncategorised" for c in ranked)


def test_unresolved_hash_is_kept_as_a_hash_only_row():
    ranked = cg.cluster_events([ev(utt="0123456789ab")], now=NOW)
    assert ranked[0].area == "uncategorised" and ranked[0].examples == []
    resolved = cg.cluster_events([ev(utt=cg.utterance_hash("set a timer for tea"))], now=NOW,
                                 resolver=cg.build_resolver(["set a timer for tea"]))
    assert resolved[0].area == "timers" and resolved[0].examples == ["set a timer for tea"]


def test_rank_frequency_recency_members():
    many = [ev(text=f"add item {i} to the list", age_days=1 + i * 0.1) for i in range(5)]
    few = [ev(text="set a timer", age_days=1.0)]
    assert cg.cluster_events(many + few, now=NOW)[0].area == "lists"                    # frequency
    old = [ev(text="play some jazz", age_days=12), ev(text="play more jazz", age_days=12.5)]
    new = [ev(text="set a timer", age_days=0.5), ev(text="set a timer now", age_days=0.6)]
    assert cg.cluster_events(old + new, now=NOW)[0].area == "timers"                    # recency
    assert cg.cluster_events(new + old, now=NOW)[0].area == "timers"                    # order-independent
    one = [ev(cg.SRC_STRUGGLE_CANT_DO, area="weather", member="m1", note="a"), ev(cg.SRC_STRUGGLE_CANT_DO, area="weather", member="m1", age_days=2, note="b")]
    two = [ev(cg.SRC_STRUGGLE_CANT_DO, area="time", member="m1", note="a"), ev(cg.SRC_STRUGGLE_CANT_DO, area="time", member="m2", age_days=2, note="b")]
    ranked = cg.cluster_events(one + two, now=NOW)
    assert ranked[0].area == "time" and ranked[0].score > ranked[1].score               # distinct members
    assert ranked[0].members == {"m1", "m2"}


def test_conversation_counts_for_less_than_a_real_gap():
    chat = [ev(text="how are you today", age_days=1)]
    gap = [ev(cg.SRC_STRUGGLE_CANT_DO, area="lists", member="m1", note="x")]
    assert cg.cluster_events(chat + gap, now=NOW)[0].area == "lists"


# ── private / public output ──────────────────────────────────────────────────────────────────────────────────────────────────

SECRET_WORDS = ["Zanzibar pancakes", "member-secret-7"]


def _ranked():
    events = [ev(text=f"add Zanzibar pancakes to the list {i}", age_days=i * 0.2 + 0.1) for i in range(4)]
    events += [ev(cg.SRC_STRUGGLE_CANT_DO, area="smart_home", member="member-secret-7", note=f"n{i}", age_days=i + 0.5) for i in range(3)]
    events += [ev(text="set a timer", age_days=1)]                             # a count-1 cluster
    events += [ev(text="reorganise the garden shed"), ev(text="reorganise garden shed now", age_days=2), ev(text="reorganise a garden shed", age_days=3)]
    return cg.cluster_events(events, now=NOW)


def test_private_report_has_examples_and_ids():
    r = cg.private_report(_ranked(), now=NOW, days=14)
    assert "Zanzibar pancakes" in r and "member-secret-7" in r and "PRIVATE" in r


def test_public_summary_is_aggregate_only():
    s = cg.public_summary(_ranked(), now=NOW, days=14)
    for w in SECRET_WORDS + ["garden", "shed", "timer for"]:
        assert w not in s
    assert "Lists - add" in s and "Smart home control - Zoe said she could not" in s


def test_public_summary_drops_small_rows_and_puts_residual_last():
    s = cg.public_summary(_ranked(), now=NOW, days=14)
    assert "Timers" not in s                                    # one event: under PUBLIC_MIN_COUNT
    rows = [l for l in s.splitlines() if l.startswith("| ") and not l.startswith("| #") and "---" not in l]
    assert rows[-1].split("|")[2].strip() == "Uncategorised requests"
    two = cg.cluster_events([ev(text="set a timer", age_days=1), ev(text="set a timer now", age_days=2)], now=NOW)
    assert "Timers" not in cg.public_summary(two, now=NOW, days=14)           # 2 < 3
    three = cg.cluster_events([ev(text="set a timer", age_days=1), ev(text="set a timer now", age_days=2), ev(text="start a timer", age_days=3)], now=NOW)
    assert "Timers" in cg.public_summary(three, now=NOW, days=14)             # 3 passes


# ── the script ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "capability_gap_backlog.py"
    spec = importlib.util.spec_from_file_location("capability_gap_backlog", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_end_to_end(tmp_path):
    sc = _script()
    now = datetime.now().timestamp()
    app = tmp_path / "app.log"
    app.write_text("\n".join([
        L(now - 600, "intent_router: intent_miss: Add Zanzibar pancakes to the list"),
        L(now - 400, "intent_router: intent_miss: Add Zanzibar pancakes to the list"),
        L(now - 300, "intent_router: intent_miss: len=18 utt=" + cg.utterance_hash("set a timer please")),
        L(now - 200, "nlu_extractor: no extractor for intent extend_capability"),
        L(now - 100, "middleware.logging: noise"),
    ]) + "\n")
    stt = tmp_path / "stt.jsonl"
    stt.write_text(json.dumps({"transcript": "set a timer please"}) + "\nnot json\n")
    out, pub = tmp_path / "private.md", tmp_path / "public.md"
    rc = sc.main(["--app-log", str(app), "--shadow-log", str(tmp_path / "none.jsonl"), "--misses", str(tmp_path / "none2.jsonl"),
                  "--stt-log", str(stt), "--out", str(out), "--public-out", str(pub), "--days", "7"])
    assert rc == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    priv = out.read_text()
    assert "Zanzibar pancakes" in priv and "set a timer please" in priv              # hash read back privately
    assert "Zanzibar" not in pub.read_text() and "timer please" not in pub.read_text()


def test_script_exit_2_when_nothing_readable(tmp_path):
    sc = _script()
    missing = tmp_path / "missing"
    assert sc.main(["--app-log", str(missing), "--shadow-log", str(missing), "--misses", str(missing), "--stt-log", str(missing),
                    "--out", str(tmp_path / "o.md")]) == 2
    assert not (tmp_path / "o.md").exists()
