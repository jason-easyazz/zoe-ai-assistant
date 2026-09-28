"""The two-stage router's low-confidence floor (ZOE_ROUTER_HEAD_MIN_CONF).

Samantha bar S1 (2026-09-28): "Who is flying in on Thursday, and where from?"
scored head top = people @ 0.5371, passed the 0.5 chat gate, and the decoder
picked show_calendar from the people/calendar/reminders shortlist — a
deterministic calendar reply in 488 ms; the brain and the recall packet never
saw the turn. A non-chat head top below the measured floor (0.70, table in
docs/knowledge/two-stage-router-rollout.md → "Low-confidence floor") must now
fall through to chat, logged ``gated: true, reason: low_conf``.

The sidecar is always faked. The real-head tests use the SHIPPED numpy MLP head
and committed bge-small vectors of the synthetic harness asks, so no fastembed,
no sklearn, no network. Slim-dep-green (ci_safe).
"""
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")
router_heads_numpy = pytest.importorskip("router_heads_numpy")

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
PROBES = SVC / "tests" / "fixtures" / "router_samantha_probe_vectors.json"
S1_TEXT = "Who is flying in on Thursday, and where from?"
CALENDAR_CALL = "call:show_calendar{qualifier:<escape>thursday<escape>}"
VEC = np.ones(4, dtype=np.float32)
CLASSES = ("calendar", "chat", "people", "reminders")
# The live S1 head output: people 0.5371 (top), calendar second.
S1_PROBS = [0.4340, 0.0, 0.5371, 0.0289]


class _FakeHead:
    def __init__(self, probs, classes=CLASSES):
        self.classes_ = np.asarray(classes)
        self._probs = np.asarray(probs, dtype=np.float64)

    def predict_proba(self, X):
        return self._probs.reshape(1, -1)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ZOE_ROUTER_HEAD_MIN_CONF", "ZOE_ROUTER_TWO_STAGE_GATE"):
        monkeypatch.delenv(k, raising=False)


def _set_head(monkeypatch, head):
    monkeypatch.setattr(router_two_stage, "_HEAD", head)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)


def _fake_sidecar(monkeypatch, raw=CALENDAR_CALL):
    calls = []

    def _post(text, grammar):
        calls.append(text)
        return raw

    monkeypatch.setattr(router_two_stage, "_post_sidecar", _post)
    return calls


# --------------------------------------------------------------------------- #
# decide(): the floor                                                          #
# --------------------------------------------------------------------------- #
def test_s1_low_confidence_calendar_misroute_falls_through_to_chat(monkeypatch):
    _set_head(monkeypatch, _FakeHead(S1_PROBS))
    calls = _fake_sidecar(monkeypatch)
    d = router_two_stage.decide(S1_TEXT, VEC)
    assert d["gated"] is True and d["reason"] == "low_conf"
    assert d["tool"] is None and d["domain"] == "chat"
    assert d["head_top"] == "people" and d["head_conf"] == 0.5371
    assert calls == []  # the floor abstains BEFORE paying the sidecar call


def test_confident_calendar_ask_still_routes_to_calendar(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.93, 0.02, 0.03, 0.02]))
    _fake_sidecar(monkeypatch)
    d = router_two_stage.decide("what have I got on thursday", VEC)
    assert d["gated"] is False and "reason" not in d
    assert d["tool"] == "show_calendar" and d["domain"] == "calendar"


def test_decision_exactly_at_the_floor_routes(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.70, 0.10, 0.15, 0.05]))
    _fake_sidecar(monkeypatch)
    assert router_two_stage.MIN_CONF_DEFAULT == 0.70
    d = router_two_stage.decide("what have I got on thursday", VEC)
    assert d["gated"] is False and d["tool"] == "show_calendar"


def test_floor_zero_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD_MIN_CONF", "0")
    _set_head(monkeypatch, _FakeHead(S1_PROBS))
    calls = _fake_sidecar(monkeypatch)
    d = router_two_stage.decide(S1_TEXT, VEC)
    # exactly the live 2026-09-28 16:29 decision
    assert d["gated"] is False and d["tool"] == "show_calendar"
    assert d["domain"] == "calendar"
    assert d["shortlist"] == ["people", "calendar", "reminders"]
    assert calls == [S1_TEXT]


def test_gate_reasons_are_distinct(monkeypatch):
    _set_head(monkeypatch, _FakeHead([0.1, 0.8, 0.05, 0.05]))
    assert router_two_stage.decide("how are you", VEC)["reason"] == "chat_top"
    _set_head(monkeypatch, _FakeHead([0.4, 0.3, 0.2, 0.1]))
    assert router_two_stage.decide("whats on", VEC)["reason"] == "below_gate"


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "  ", "1.70", "-0.2", "70"])
def test_bad_or_out_of_range_floor_keeps_the_measured_default(monkeypatch, caplog, raw):
    """1.70 would silently abstain EVERY tool decision; -0.2 is a typo."""
    monkeypatch.setenv("ZOE_ROUTER_HEAD_MIN_CONF", raw)
    with caplog.at_level("WARNING", logger="router_two_stage"):
        assert router_two_stage.min_conf() == router_two_stage.MIN_CONF_DEFAULT
    if raw.strip():
        assert "ZOE_ROUTER_HEAD_MIN_CONF" in caplog.text and repr(raw) in caplog.text


@pytest.mark.parametrize("raw,want", [("0", 0.0), ("0.0", 0.0), ("1", 1.0),
                                      ("0.65", 0.65)])
def test_in_range_floor_is_honoured_including_the_edges(monkeypatch, raw, want):
    monkeypatch.setenv("ZOE_ROUTER_HEAD_MIN_CONF", raw)
    assert router_two_stage.min_conf() == want


def test_out_of_range_floor_does_not_disable_tools(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD_MIN_CONF", "1.70")
    _set_head(monkeypatch, _FakeHead([0.93, 0.02, 0.03, 0.02]))
    _fake_sidecar(monkeypatch)
    d = router_two_stage.decide("what have I got on thursday", VEC)
    assert d["gated"] is False and d["tool"] == "show_calendar"


def test_floor_is_in_the_flag_inventory():
    inv = json.loads((SVC.parents[1] / "docs" / "knowledge" /
                      "flag-inventory.json").read_text())
    assert "ZOE_ROUTER_HEAD_MIN_CONF" in json.dumps(inv)
    assert router_two_stage.min_conf() == router_two_stage.MIN_CONF_DEFAULT == 0.70


# --------------------------------------------------------------------------- #
# semantic_router.route() in active mode: the route + the log line             #
# --------------------------------------------------------------------------- #
def _fake_router(monkeypatch):
    class _FakeModel:
        def embed(self, texts):
            for _ in texts:
                yield np.ones(4, dtype=np.float32)

    labels = np.asarray(["calendar", "people", "chat", "chat"])
    monkeypatch.setattr(semantic_router, "ROUTES",
                        {"calendar": [], "people": [], "chat": []})
    monkeypatch.setattr(semantic_router, "_MODEL", _FakeModel())
    monkeypatch.setattr(semantic_router, "_MATRIX", np.eye(4, dtype=np.float32))
    monkeypatch.setattr(semantic_router, "_LABELS", labels)
    monkeypatch.setattr(
        semantic_router, "_DOM_IDX",
        {d: np.where(labels == d)[0] for d in ("calendar", "people", "chat")})


def test_active_route_sends_s1_to_chat_and_logs_low_conf(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    _set_head(monkeypatch, _FakeHead(S1_PROBS))
    _fake_sidecar(monkeypatch)
    rr = semantic_router.route(S1_TEXT)
    assert rr["routed"] == "chat" and rr["domain"] == "chat"
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["gated"] is True and rec["reason"] == "low_conf"
    assert rec["actual_routed"] == "chat" and rec["two_stage_tool"] is None
    assert rec["head_conf"] == 0.5371
    assert semantic_router.route_two_stage(S1_TEXT).source == "gate_abstain"


# --------------------------------------------------------------------------- #
# The SHIPPED head on the real S1 embedding (and head parity)                  #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_head():
    return router_heads_numpy.load_head(
        str(SVC / "models" / "router_head_mlp.joblib"))


@pytest.fixture(scope="module")
def probes():
    return json.loads(PROBES.read_text())


def _vec(probes, name):
    case = next(c for c in probes["cases"] if c["name"] == name)
    return case, np.asarray(case["vector"], dtype=np.float32)


def test_shipped_head_outputs_are_unchanged(real_head, probes):
    """This fix sits AROUND stage 1; it must not change the head. The served
    numpy head reproduces the probabilities measured when the floor was set."""
    assert [str(c) for c in real_head.classes_] == probes["classes"]
    for case in probes["cases"]:
        got = real_head.predict_proba(
            np.asarray(case["vector"], dtype=np.float32).reshape(1, -1))[0]
        assert np.max(np.abs(got - np.asarray(case["proba"]))) <= 1e-6, case["name"]


def test_real_s1_embedding_through_the_real_head_is_gated(monkeypatch,
                                                          real_head, probes):
    case, v = _vec(probes, "ASK_SISTER")
    assert case["text"] == S1_TEXT
    _set_head(monkeypatch, real_head)
    calls = _fake_sidecar(monkeypatch)
    d = router_two_stage.decide(S1_TEXT, v)
    assert d["head_top"] == "people" and 0.5 < d["head_conf"] < 0.70
    assert d["shortlist"] == ["people", "calendar", "reminders"]
    assert d["gated"] is True and d["reason"] == "low_conf" and calls == []

    monkeypatch.setenv("ZOE_ROUTER_HEAD_MIN_CONF", "0")  # the old behaviour
    d = router_two_stage.decide(S1_TEXT, v)
    assert d["gated"] is False and d["tool"] == "show_calendar"


# --------------------------------------------------------------------------- #
# The miss is fed back to both stages' training corpora (not trained here)     #
# --------------------------------------------------------------------------- #
def test_s1_miss_is_in_both_training_corpora_and_not_in_the_frozen_eval():
    repo = SVC.parents[1]
    frozen = {json.loads(l)["text"].strip().lower() for l in
              (repo / "labs/needle-benchmark/corpus.jsonl").read_text().splitlines()
              if l.strip()}
    stage1 = repo / "labs/setfit-router/data/misses.jsonl"
    stage2 = repo / "labs/functiongemma-finetune/data/train_misses.jsonl"
    for path, key, want in ((stage1, "label", "memory"),
                            (stage2, "tool", "recall_memory")):
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        assert any(r["text"] == S1_TEXT and r[key] == want for r in rows), path
        assert not {r["text"].strip().lower() for r in rows} & frozen, path
    # both trainers actually read them (a builder regenerates the main files)
    assert "data/misses.jsonl" in (repo / "labs/setfit-router/train.py").read_text()
    assert '"train_misses.jsonl"' in (
        repo / "scripts/maintenance/router_selftrain.py").read_text()
