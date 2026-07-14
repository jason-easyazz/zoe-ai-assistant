"""Unit tests for the SetFit router head SHADOW integration (semantic_router).

Pure logic — the embedding model and the joblib head are faked, so no
fastembed download, no sklearn requirement. Slim-dep-green (ci_safe).
"""
import json

import pytest

np = pytest.importorskip("numpy")
semantic_router = pytest.importorskip("semantic_router")

pytestmark = pytest.mark.ci_safe


class _FakeHead:
    """Stands in for the sklearn LogisticRegression artifact."""

    def __init__(self, probs, classes=("calendar", "chat", "lists")):
        self.classes_ = np.asarray(classes)
        self._probs = np.asarray(probs, dtype=np.float64)

    def predict_proba(self, X):
        return self._probs.reshape(1, -1)


def _fake_router(monkeypatch):
    """Wire a deterministic fake embedding model so route() runs offline."""
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


def _set_head(monkeypatch, head):
    monkeypatch.setattr(semantic_router, "_HEAD", head)
    monkeypatch.setattr(semantic_router, "_HEAD_FAILED", False)
    # keep lazy loading from replacing the fake
    monkeypatch.setattr(semantic_router, "_ensure_head_loaded", lambda: None)


# --------------------------------------------------------------------------- #
# mode parsing                                                                #
# --------------------------------------------------------------------------- #
def test_head_mode_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_ROUTER_HEAD", raising=False)
    assert semantic_router.head_mode() == "off"


def test_head_mode_shadow(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    assert semantic_router.head_mode() == "shadow"


def test_head_mode_active(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    assert semantic_router.head_mode() == "active"


def test_head_mode_shadow2(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow2")
    assert semantic_router.head_mode() == "shadow2"


def test_head_mode_unknown_is_off(monkeypatch):
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "bogus")
    assert semantic_router.head_mode() == "off"


# --------------------------------------------------------------------------- #
# shadow never changes routing; off never touches the head                    #
# --------------------------------------------------------------------------- #
def test_route_unchanged_in_shadow(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "shadow.jsonl"))
    # head disagrees hard (says lists) — routing must not care
    _set_head(monkeypatch, _FakeHead([0.05, 0.05, 0.90]))
    monkeypatch.setenv("ZOE_ROUTER_THRESHOLD", "0.0")

    baseline_routed = semantic_router.route("anything")["routed"]

    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")
    off_routed = semantic_router.route("anything")["routed"]
    assert baseline_routed == off_routed  # shadow changed nothing


def test_off_mode_never_calls_head(monkeypatch):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "off")

    class _Boom:
        classes_ = np.asarray(["chat"])

        def predict_proba(self, X):
            raise AssertionError("head must not run when ZOE_ROUTER_HEAD=off")

    _set_head(monkeypatch, _Boom())
    semantic_router.route("hello there")  # must not raise


# --------------------------------------------------------------------------- #
# shadow log record shape (hash only, agreement bool)                         #
# --------------------------------------------------------------------------- #
def test_shadow_log_record(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setenv("ZOE_ROUTER_HEAD_THRESHOLD", "0.4")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    _set_head(monkeypatch, _FakeHead([0.9, 0.05, 0.05]))  # calendar @0.9

    utterance = "add a dentist appointment"
    semantic_router.route(utterance)

    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["head_pred"] == "calendar"
    assert rec["head_routed"] == "calendar"  # above 0.4 gate, non-chat
    assert 0.89 <= rec["head_conf"] <= 0.91
    assert rec["actual_routed"] in ("calendar", "lists", "chat")
    assert rec["agree"] == (rec["head_routed"] == rec["actual_routed"])
    # privacy: raw utterance never appears, only a short hash
    assert utterance not in log.read_text()
    assert len(rec["utt"]) == 12


def test_shadow_gate_abstains_to_chat(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setenv("ZOE_ROUTER_HEAD_THRESHOLD", "0.4")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    _set_head(monkeypatch, _FakeHead([0.35, 0.33, 0.32]))  # low confidence

    semantic_router.route("hmm not sure")
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["head_routed"] == "chat"  # below gate -> abstain


def test_shadow_head_error_is_nonfatal(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH",
                        str(tmp_path / "shadow.jsonl"))

    class _Broken:
        classes_ = np.asarray(["chat"])

        def predict_proba(self, X):
            raise RuntimeError("boom")

    _set_head(monkeypatch, _Broken())
    out = semantic_router.route("still works")  # must not raise
    assert "routed" in out


# --------------------------------------------------------------------------- #
# report helper                                                               #
# --------------------------------------------------------------------------- #
def test_shadow_report_aggregates(tmp_path):
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "router_shadow_report",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "..", "scripts", "maintenance",
                     "router_shadow_report.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    log = tmp_path / "shadow.jsonl"
    rows = [
        {"utt": "a" * 12, "head_pred": "calendar", "head_conf": 0.9,
         "head_routed": "calendar", "actual_routed": "calendar",
         "agree": True, "head_ms": 0.2},
        {"utt": "b" * 12, "head_pred": "lists", "head_conf": 0.5,
         "head_routed": "lists", "actual_routed": "chat",
         "agree": False, "head_ms": 0.3},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    recs = mod.load(str(log))
    assert len(recs) == 2
    text = mod.report(recs)
    assert "agreement" in text and "1/2" in text
    assert "chat -> lists: 1" in text


# --------------------------------------------------------------------------- #
# ZOE_ROUTER_SHADOW_TEXT — opt-in raw-text capture (default OFF)               #
# --------------------------------------------------------------------------- #
def test_shadow_text_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_ROUTER_SHADOW_TEXT", raising=False)
    assert semantic_router.shadow_text_enabled() is False


@pytest.mark.parametrize("val,want", [
    ("1", True), ("true", True), ("on", True), ("YES", True),
    ("0", False), ("false", False), ("", False), ("bogus", False),
])
def test_shadow_text_flag_parsing(monkeypatch, val, want):
    monkeypatch.setenv("ZOE_ROUTER_SHADOW_TEXT", val)
    assert semantic_router.shadow_text_enabled() is want


def test_shadow_log_has_no_raw_text_by_default(monkeypatch, tmp_path):
    """The privacy default: the log is keyed by hash and NEVER stores the words."""
    _fake_router(monkeypatch)
    log = tmp_path / "shadow.jsonl"
    monkeypatch.delenv("ZOE_ROUTER_SHADOW_TEXT", raising=False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    _set_head(monkeypatch, _FakeHead([0.9, 0.05, 0.05]))

    utterance = "my private family utterance"
    semantic_router.route(utterance)

    raw = log.read_text()
    assert utterance not in raw
    assert "utt_text" not in json.loads(raw.strip().splitlines()[-1])


def test_shadow_log_captures_raw_text_when_opted_in(monkeypatch, tmp_path):
    _fake_router(monkeypatch)
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_SHADOW_TEXT", "1")
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))
    _set_head(monkeypatch, _FakeHead([0.9, 0.05, 0.05]))

    utterance = "add a dentist appointment"
    semantic_router.route(utterance)

    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["utt_text"] == utterance
    assert len(rec["utt"]) == 12          # the hash is kept either way


def test_two_stage_rec_text_is_opt_in(monkeypatch):
    monkeypatch.delenv("ZOE_ROUTER_SHADOW_TEXT", raising=False)
    off = semantic_router._two_stage_rec("hello", {"tool": "get_time"}, "active", "time")
    assert "utt_text" not in off

    monkeypatch.setenv("ZOE_ROUTER_SHADOW_TEXT", "1")
    on = semantic_router._two_stage_rec("hello", {"tool": "get_time"}, "active", "time")
    assert on["utt_text"] == "hello"
    assert on["utt"] == off["utt"]        # same hash, extra field


def test_two_stage_info_line_never_carries_raw_text(monkeypatch, tmp_path, caplog):
    """Opting in adds text to the FILE only — journald/log shipping stays hash-only."""
    monkeypatch.setenv("ZOE_ROUTER_SHADOW_TEXT", "1")
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(semantic_router, "_HEAD_LOG_PATH", str(log))

    rec = semantic_router._two_stage_rec("secret words", {"tool": "get_time"},
                                         "active", "time")
    with caplog.at_level("INFO", logger="zoe.router_head_shadow"):
        semantic_router._log_two_stage(rec)

    assert "secret words" not in caplog.text
    assert json.loads(log.read_text().strip())["utt_text"] == "secret words"
