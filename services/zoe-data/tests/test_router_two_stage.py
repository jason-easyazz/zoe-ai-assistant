"""Unit tests for the two-stage active router (router_two_stage +
semantic_router shadow2/active integration).

Pure logic — the embedding model, the MLP head, and the FunctionGemma
sidecar are all faked, so no fastembed download, no sklearn, no network.
Slim-dep-green (ci_safe).
"""
import json
import time

import pytest

np = pytest.importorskip("numpy")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")

pytestmark = pytest.mark.ci_safe


class _FakeHead:
    """Stands in for the sklearn MLPClassifier artifact."""

    def __init__(self, probs, classes=("calendar", "chat", "lists", "reminders")):
        self.classes_ = np.asarray(classes)
        self._probs = np.asarray(probs, dtype=np.float64)

    def predict_proba(self, X):
        return self._probs.reshape(1, -1)


def _set_head(monkeypatch, head):
    monkeypatch.setattr(router_two_stage, "_HEAD", head)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)


def _fake_sidecar(monkeypatch, raw):
    calls = []

    def _post(text, grammar):
        calls.append({"text": text, "grammar": grammar})
        if isinstance(raw, Exception):
            raise raw
        return raw

    monkeypatch.setattr(router_two_stage, "_post_sidecar", _post)
    return calls


def _wait_until_truthy(predicate, *, timeout_s=2.5, interval_s=0.05):
    """Poll predicate() until it returns a TRUTHY value; None on timeout.

    Truthiness IS the ready signal — a falsy result means "not yet". Both callers
    wait for a value to appear (a dict entry, a non-empty list), so that reads
    naturally; a predicate whose valid answer is falsy (0/False/"") would need a
    different helper. Named for the contract so that isn't a surprise.

    Budget matches the original 50 x 0.05s waits.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        val = predicate()
        if val:
            return val
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval_s)


def _wait_for_log_lines(path, *, timeout_s=2.5):
    """Wait for the shadow log to have CONTENT — not merely to exist.

    The shadow log is written by a BACKGROUND thread that creates the file and
    only THEN writes to it. Polling `exists()` alone races that gap: the path is
    already there while the file is still empty, so `splitlines()[0]` raises
    IndexError (a CI flake — the line was emitted, just not flushed yet). Poll
    for non-empty content instead, and fail loudly if it never lands so a real
    regression can't hide behind a timeout.
    """
    def _lines():
        try:
            text = path.read_text()
        except OSError:  # not created yet
            return None
        return text.splitlines() if text.strip() else None

    lines = _wait_until_truthy(_lines, timeout_s=timeout_s)
    assert lines, (
        f"shadow log {path} received no line within {timeout_s}s — the "
        "background writer never flushed one"
    )
    return lines


VEC = np.ones(4, dtype=np.float32)


# --------------------------------------------------------------------------- #
# parse / validate / grammar                                                   #
# --------------------------------------------------------------------------- #
def test_parse_call_escaped_string_args():
    name, args = router_two_stage.parse_call(
        "<start_function_call>call:add_reminder{title:<escape>take out the "
        "bins<escape>,date:<escape>next tuesday<escape>,time:}")
    assert name == "add_reminder"
    assert args["title"] == "take out the bins"
    assert args["date"] == "next tuesday"


def test_parse_call_literals():
    name, args = router_two_stage.parse_call(
        "call:set_timer{minutes:10,label:<escape>pasta<escape>}")
    assert name == "set_timer"
    assert args == {"minutes": 10, "label": "pasta"}
    name, args = router_two_stage.parse_call(
        "call:get_weather{forecast:false}")
    assert args == {"forecast": False}


def test_parse_call_chat_escape_and_junk():
    # <unused20> is a special token — never rendered into content
    assert router_two_stage.parse_call("") == (None, {})
    assert router_two_stage.parse_call("random prose no call") == (None, {})


def test_validate_call_filters_illegal_names_and_args():
    name, args = router_two_stage.validate_call(
        "set_timer", {"minutes": 5, "bogus": 1}, ["set_timer"])
    assert (name, args) == ("set_timer", {"minutes": 5})
    # not in the shortlist's legal set → no call
    assert router_two_stage.validate_call(
        "set_timer", {}, ["get_time"]) == (None, {})
    # hallucinated name not in the schema → no call
    assert router_two_stage.validate_call(
        "made_up_tool", {}, ["made_up_tool"]) == (None, {})


def test_build_grammar_contains_shortlist_and_chat_escape():
    g = router_two_stage.build_grammar(["get_time", "set_timer"])
    assert '"get_time" | "set_timer"' in g
    assert '"<unused20>"' in g
    assert "<start_function_call>" in g


# --------------------------------------------------------------------------- #
# decide(): gate / tool / chat-escape / failure paths                          #
# --------------------------------------------------------------------------- #
def test_decide_gate_abstains_below_threshold(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.4, 0.3, 0.2, 0.1]))
    monkeypatch.setenv("ZOE_ROUTER_TWO_STAGE_GATE", "0.5")
    calls = _fake_sidecar(monkeypatch, "call:show_calendar{}")
    d = router_two_stage.decide("whats on", VEC)
    assert d["gated"] is True and d["tool"] is None and d["domain"] == "chat"
    assert calls == []  # gate abstain never pays the sidecar call


def test_decide_gate_abstains_on_chat_top(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.1, 0.8, 0.05, 0.05]))
    calls = _fake_sidecar(monkeypatch, "call:show_calendar{}")
    d = router_two_stage.decide("how are you", VEC)
    assert d["gated"] is True and d["domain"] == "chat"
    assert calls == []


def test_decide_routes_tool_and_maps_domain(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    calls = _fake_sidecar(
        monkeypatch,
        "call:add_calendar_event{title:<escape>dentist<escape>,"
        "date:<escape>tuesday<escape>}")
    d = router_two_stage.decide("book the dentist tuesday", VEC)
    assert d["tool"] == "add_calendar_event"
    assert d["domain"] == "calendar"
    assert d["args"]["title"] == "dentist"
    assert d["gated"] is False
    assert d["shortlist"][0] == "calendar" and "chat" not in d["shortlist"]
    # grammar was restricted to the shortlist's tools
    assert '"add_calendar_event"' in calls[0]["grammar"]


def test_decide_chat_escape_from_decoder(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch, "")  # <unused20> renders as empty content
    d = router_two_stage.decide("lovely day isn't it", VEC)
    assert d["tool"] is None and d["domain"] == "chat" and d["gated"] is False


def test_decide_sidecar_failure_returns_none(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch, TimeoutError("sidecar timeout"))
    assert router_two_stage.decide("set a timer", VEC) is None


def test_decide_malformed_output_is_chat_not_error(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch, "call:not_a_real_tool{x:1}")
    d = router_two_stage.decide("set a timer", VEC)
    assert d is not None and d["tool"] is None and d["domain"] == "chat"


def test_decide_head_load_failure_returns_none(monkeypatch):
    monkeypatch.setattr(router_two_stage, "_HEAD", None)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", True)
    assert router_two_stage.decide("set a timer", VEC) is None


# --------------------------------------------------------------------------- #
# semantic_router integration: active routes, shadow2 does not                 #
# --------------------------------------------------------------------------- #
def _fake_router(monkeypatch):
    class _FakeModel:
        def embed(self, texts):
            for _ in texts:
                yield np.ones(4, dtype=np.float32)

    monkeypatch.setattr(semantic_router, "ROUTES",
                        {"calendar": [], "lists": [], "chat": []})
    monkeypatch.setattr(semantic_router, "_MODEL", _FakeModel())
    monkeypatch.setattr(semantic_router, "_MATRIX",
                        np.eye(4, dtype=np.float32))
    labels = np.asarray(["calendar", "lists", "chat", "chat"])
    monkeypatch.setattr(semantic_router, "_LABELS", labels)
    monkeypatch.setattr(
        semantic_router, "_DOM_IDX",
        {d: np.where(labels == d)[0] for d in ("calendar", "lists", "chat")},
    )


def test_active_two_stage_decision_overrides_similarity(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(
        semantic_router, "_two_stage_active",
        lambda text, v: {"tool": "add_reminder", "domain": "reminders",
                         "args": {}, "shortlist": ["reminders"],
                         "head_top": "reminders", "head_conf": 0.9,
                         "gated": False, "ms": 5.0})
    rr = semantic_router.route("remind me to call mum")
    assert rr["routed"] == "reminders" and rr["domain"] == "reminders"
    assert rr["two_stage"]["tool"] == "add_reminder"
    assert "similarity_routed" in rr


def test_active_failure_falls_back_to_similarity(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    baseline = semantic_router.route("anything")
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setattr(semantic_router, "_two_stage_active",
                        lambda text, v: None)  # sidecar down
    rr = semantic_router.route("anything")
    assert rr["routed"] == baseline["routed"]
    assert "two_stage" not in rr


def test_active_chat_decision_routes_chat(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setenv("ZOE_ROUTER_THRESHOLD", "0.0")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(
        semantic_router, "_two_stage_active",
        lambda text, v: {"tool": None, "domain": "chat", "args": {},
                         "shortlist": ["calendar"], "head_top": "chat",
                         "head_conf": 0.9, "gated": True, "ms": 1.0})
    rr = semantic_router.route("how are you")
    assert rr["routed"] == "chat"


def test_shadow2_never_changes_routing(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    baseline = semantic_router.route("anything")

    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow2")
    seen = {}

    def _fake_decide(text, v):
        seen["text"] = text
        return {"tool": "show_list", "domain": "lists", "args": {},
                "shortlist": ["lists"], "head_top": "lists",
                "head_conf": 0.99, "gated": False, "ms": 5.0}

    monkeypatch.setattr(router_two_stage, "decide", _fake_decide)
    rr = semantic_router.route("anything")
    assert rr["routed"] == baseline["routed"]  # shadow2 never routes
    assert "two_stage" not in rr
    # Both waits below poll for CONTENT, never for mere file existence — the
    # shadow work happens on a background thread (see _wait_for_log_lines).
    assert _wait_until_truthy(lambda: seen.get("text")) == "anything", (
        "shadow2 background thread never called router_two_stage.decide() "
        "with the utterance within 2.5s"
    )
    rec = json.loads(_wait_for_log_lines(tmp_path / "log.jsonl")[0])
    assert rec["mode"] == "shadow2" and rec["two_stage_tool"] == "show_list"
    assert "anything" not in json.dumps(rec)  # hash only, never raw text


# --------------------------------------------------------------------------- #
# route_two_stage() public contract (RouterDecision)                           #
# --------------------------------------------------------------------------- #
def test_route_two_stage_contract_tool(monkeypatch):
    _fake_router(monkeypatch)
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch,
                  "call:add_calendar_event{title:<escape>dentist<escape>}")
    d = semantic_router.route_two_stage("book the dentist")
    assert isinstance(d, semantic_router.RouterDecision)
    assert d.tool == "add_calendar_event" and d.source == "two_stage"
    assert d.args == {"title": "dentist"}
    assert d.confidence == pytest.approx(0.7)
    assert d.latency_ms >= 0


def test_route_two_stage_contract_gate_abstain(monkeypatch):
    _fake_router(monkeypatch)
    _set_head(monkeypatch, _FakeHead([0.1, 0.8, 0.05, 0.05]))
    _fake_sidecar(monkeypatch, "never called")
    d = semantic_router.route_two_stage("how are you")
    assert d.tool is None and d.source == "gate_abstain"


def test_route_two_stage_contract_shortlist_miss(monkeypatch):
    _fake_router(monkeypatch)
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch, "")  # decoder chat escape
    d = semantic_router.route_two_stage("lovely day")
    assert d.tool is None and d.source == "shortlist_miss"


def test_route_two_stage_contract_error_fallback(monkeypatch):
    _fake_router(monkeypatch)
    _set_head(monkeypatch, _FakeHead([0.7, 0.1, 0.15, 0.05]))
    _fake_sidecar(monkeypatch, TimeoutError("down"))
    d = semantic_router.route_two_stage("set a timer")
    assert d.tool is None and d.source == "error_fallback"
    assert d.confidence == 0.0


def test_active_unscored_domain_gets_zero_score(monkeypatch, tmp_path):
    """A two-stage domain with no similarity examples must not borrow
    another domain's similarity score (Greptile #1322 P1)."""
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(
        semantic_router, "_two_stage_active",
        lambda text, v: {"tool": "create_note", "domain": "notes",
                         "args": {}, "shortlist": ["notes"],
                         "head_top": "notes", "head_conf": 0.9,
                         "gated": False, "ms": 5.0})
    rr = semantic_router.route("jot this down")
    assert rr["domain"] == "notes"
    assert rr["score"] == 0.0  # never a borrowed score
