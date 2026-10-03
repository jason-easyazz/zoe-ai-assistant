"""Day-sim gaps 6/6n/9 + the explicit-date booking (live 2026-10-03, demo user).

- "Am I still doing the half-marathon?" / "Do I still get migraines?" got no
  recall-floor packet (present-state shape) → "not sure I have that saved".
- "What time is my dentist appointment on Friday?" → head time @ 0.9971 →
  "It's 10:41 PM." (event-time question routed to the clock).
- "Saturday 3 October at 5pm", asked on Saturday 3 October, was booked on the
  10th (the weekday heuristic beat the explicit date).

Head + sidecar are faked as in test_evidence_question_routing — no fastembed,
sklearn, DB or network (ci_safe)."""
import asyncio
import contextlib
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

np = pytest.importorskip("numpy")
memory_gate = pytest.importorskip("memory_gate")
semantic_router = pytest.importorskip("semantic_router")
router_two_stage = pytest.importorskip("router_two_stage")
fast_tiers = pytest.importorskip("fast_tiers")
zc = pytest.importorskip("zoe_flue_client")
intent_router = pytest.importorskip("intent_router")

pytestmark = pytest.mark.ci_safe

PERTH = ZoneInfo("Australia/Perth")
DENTIST = "What time is my dentist appointment on Friday?"
RACE, MIGRAINE, MUM = "Am I still doing the half-marathon?", "Do I still get migraines?", "How's my mum doing?"
CLASSES = ("calendar", "chat", "memory", "reminders", "time")
TIME_CALL = "call:get_time{}"

PRESENT_POS = [RACE, MIGRAINE, MUM, "how is my dog", "Is my mum still in Bendigo?",
               "Are we still on for dinner Friday?", "Which team am I on?", "Which gym do I go to?",
               "Have I still got the dentist on Friday?", "How are my parents?"]
PRESENT_NEG = ["How's the weather?", "Am I still connected?", "am I still on the call",
               "Do I still need an umbrella?", "Is my timer still running?", "How's my internet?",
               "How are you?", "How's it going?", "is my phone still charging", "Are you still there?",
               "am I still muted"]
EVENT_POS = [DENTIST, "when's my flight", "What time does my train leave?", "what time do I have the dentist",
             "what day is our wedding", "what time am I seeing the physio", "when do I fly out",
             "what time am I on tomorrow", "which day is my exam", "what time's my haircut"]
EVENT_NEG = ["What time is it?", "what time is it in London", "What's the time?", "when is Easter",
             "what time does Woolworths close", "What time is the game on Friday?",
             "what time is my alarm set for", "When is my birthday?", "what time should I leave",
             "when do I need to leave", "what time is sunset"]


@pytest.mark.parametrize("text", PRESENT_POS)
def test_present_state_positive(text):
    assert memory_gate.present_state_question_kind(text)


@pytest.mark.parametrize("text", PRESENT_NEG)
def test_present_state_negative(text):
    assert memory_gate.present_state_question_kind(text) == ""


@pytest.mark.parametrize("text", EVENT_POS)
def test_event_time_positive(text):
    assert memory_gate.is_event_time_question(text)


@pytest.mark.parametrize("text", EVENT_NEG)
def test_event_time_negative(text):
    assert not memory_gate.is_event_time_question(text)


# ── Flue recall floor ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    (RACE, "present"), (MIGRAINE, "present"), (MUM, "present"), (DENTIST, "event_time"),
    ("what's my mum's name", "personal"),  # the existing shape still wins first
    ("How's the weather?", ""), ("Am I still connected?", ""), ("What time is it?", ""),
])
def test_floor_claims_present_state_and_event_time(monkeypatch, text, want):
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_RECALL_PRESENT_STATE_SHAPES", "1")
    assert zc._recall_floor_shape(text) == want


@pytest.mark.parametrize("text", [RACE, MIGRAINE, MUM, DENTIST])
def test_floor_flag_off_is_todays_behaviour(monkeypatch, text):
    """Negative control: flag-dark → the sim misses reproduce (no packet)."""
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.delenv("ZOE_RECALL_PRESENT_STATE_SHAPES", raising=False)
    assert zc._recall_floor_shape(text) == ""


# ── Router precedence ────────────────────────────────────────────────────────

class _Head:
    def __init__(self, top, conf):
        self._p = np.full(len(CLASSES), (1.0 - conf) / (len(CLASSES) - 1))
        self._p[CLASSES.index(top)] = conf
        self.classes_ = np.asarray(CLASSES)

    def predict_proba(self, X):
        return self._p.reshape(1, -1)


@pytest.fixture
def head(monkeypatch, tmp_path):
    labels = np.asarray(CLASSES)
    for k, v in {"ROUTES": {d: [] for d in CLASSES}, "_MODEL": object(), "_LABELS": labels,
                 "_MATRIX": np.eye(len(CLASSES), dtype=np.float32), "_HEAD_LOG_PATH": str(tmp_path / "s.jsonl"),
                 "_DOM_IDX": {d: np.where(labels == d)[0] for d in CLASSES},
                 "embed": lambda text: np.ones(len(CLASSES), dtype=np.float32)}.items():
        monkeypatch.setattr(semantic_router, k, v)
    monkeypatch.setattr(router_two_stage, "_HEAD_FAILED", False)
    for k in ("ZOE_ROUTER_HEAD_MIN_CONF", "ZOE_ROUTER_TWO_STAGE_GATE", "ZOE_INTENT_ROUTER_GATE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "active")
    monkeypatch.setenv("ZOE_ROUTER_ENABLED", "1")
    monkeypatch.setenv("ZOE_ROUTER_EVENT_TIME_PRECEDENCE", "1")

    def _set(top, conf=0.9971, raw=TIME_CALL):
        monkeypatch.setattr(router_two_stage, "_HEAD", _Head(top, conf))
        monkeypatch.setattr(router_two_stage, "_post_sidecar", lambda text, grammar: raw)
    return _set


def test_exact_sim_ask_leaves_the_clock_despite_confident_head(head, caplog):
    head("time")
    with caplog.at_level(logging.INFO, logger="semantic_router"):
        rr = semantic_router.route(DENTIST)
    assert rr["domain"] == rr["routed"] == rr["two_stage"]["domain"] == "memory"
    assert rr["two_stage"]["reason"] == "event_time_question" and rr["two_stage"]["head_top"] == "time"
    assert rr["score"] == rr["scores"]["memory"]
    assert "INTENT_GATE event_time_question=1 head=time conf=0.9971 routed=memory" in caplog.text


@pytest.mark.parametrize("text,top,want", [
    ("What time is it?", "time", "time"),  # the clock keeps the clock question
    ("what time is it in London", "time", "time"),
    (DENTIST, "calendar", "calendar"),  # a calendar claim is kept
    (DENTIST, "reminders", "memory"),
    ("when's my flight", "time", "memory"),
    ("When did I tell you about the dentist?", "time", "memory"),  # evidence rule first
])
def test_route(head, text, top, want):
    head(top, 0.99, {"time": TIME_CALL, "calendar": "call:show_calendar{}"}.get(top, "call:list_reminders{}"))
    assert semantic_router.route(text)["routed"] == want


def test_negative_control_flag_off_the_clock_answers(head, monkeypatch):
    head("time")
    monkeypatch.delenv("ZOE_ROUTER_EVENT_TIME_PRECEDENCE")
    assert semantic_router.route(DENTIST)["routed"] == "time"


@pytest.mark.parametrize("intent,text,allowed", [
    ("time_query", DENTIST, False),
    ("time_query", "What time is it?", True),
    ("calendar_show", DENTIST, True),
    ("memory_forget_entity", DENTIST, True),
])
def test_keyword_gate(head, intent, text, allowed):
    head("time")
    assert fast_tiers.intent_gate(intent, text, lane="voice") is allowed


@pytest.mark.parametrize("intent,want", [
    ("time_query", ("veto", "event_time_question")),
    ("date_query", ("veto", "event_time_question")),
    ("calendar_show", ("allow", "event_time_recall_intent")),
    ("memory_remember", ("allow", "event_time_recall_intent")),
])
def test_gate_decision_table(monkeypatch, intent, want):
    monkeypatch.delenv("ZOE_INTENT_ROUTER_GATE", raising=False)
    verdict = {"domain": "memory", "head_top": "time", "reason": "event_time_question", "gated": False}
    assert fast_tiers.intent_gate_decision(intent, verdict) == want


# ── Explicit dates win ───────────────────────────────────────────────────────

SAT = date(2026, 10, 3)  # a Saturday


@pytest.mark.parametrize("raw,want", [
    ("Saturday 3 October", "2026-10-03"),
    ("saturday, october 3", "2026-10-03"),
    ("Saturday, October 3, 2026", "2026-10-03"),
    ("sat the 3rd of october", "2026-10-03"),
    ("on Saturday 3rd October", "2026-10-03"),
    ("Saturday 2026-10-03", "2026-10-03"),
    ("the 3rd of october", "2026-10-03"),
    ("Friday 9 October", "2026-10-09"),
    ("3 October", "2026-10-03"),
    ("saturday", "2026-10-10"),  # a bare weekday keeps the next-week heuristic
    ("friday", "2026-10-09"),
    ("tomorrow", "2026-10-04"),
])
def test_parse_date_explicit_wins(raw, want):
    assert intent_router._parse_date(raw, today=SAT) == want


def test_negative_control_weekday_first_books_next_week(monkeypatch):
    monkeypatch.setattr(intent_router, "_parse_explicit_date", lambda raw, today: None)
    assert intent_router._parse_date("Saturday 3 October", today=SAT) == "2026-10-10"


class _DB:
    def __init__(self):
        self.params = None

    async def execute(self, sql, params=()):
        if "INSERT INTO events" in sql:
            self.params = tuple(params)
        return self

    async def fetchone(self):
        return None


# Perth wall clock (UTC instant) → booked date + what the reply must say.
@pytest.mark.parametrize("utc,note", [
    (datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc), ""),  # 00:30 Sat Perth (UTC still Fri)
    (datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc), ""),  # 16:00 Perth, 5pm still ahead
    (datetime(2026, 10, 3, 14, 38, tzinfo=timezone.utc), "That time has already passed today."),  # 22:38
    (datetime(2026, 10, 3, 15, 59, tzinfo=timezone.utc), "That time has already passed today."),  # 23:59
    (datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc), "That date is already in the past."),  # Sun 09:00
])
def test_calendar_create_books_the_named_date(monkeypatch, utc, note):
    db = _DB()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    async def _noop(*a):
        return None

    now = utc.astimezone(PERTH)
    monkeypatch.setattr("database.get_db_ctx", ctx)
    monkeypatch.setattr(intent_router, "_notify_ui_channel", _noop)
    monkeypatch.setattr(intent_router, "_zoe_now", lambda: now)
    monkeypatch.setattr(intent_router, "today_for_zoe_tz", lambda: now.date())
    reply = asyncio.run(intent_router._execute_calendar_create_direct(intent_router.Intent(
        "calendar_create", {"title": "pick up the Kestrel proofs",
                            "date": "Saturday 3 October", "time": "5pm"}), "demo"))
    assert "2026-10-03" in db.params and "17:00" in db.params
    assert reply.endswith(note or "at 5 PM.")
