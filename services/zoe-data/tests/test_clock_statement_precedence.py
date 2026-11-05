"""A remark that STATES a clock time never reaches the weather/clock tools.

Live 2026-11-05: a conversational reply carrying a clock time, answering Zoe's
own question, was head-routed to weather. ZOE_ROUTER_CLOCK_STATEMENT_PRECEDENCE
(default OFF) re-points a weather/time claim on such an utterance to chat.
Pure-function tests (no head/embedder/DB/network). All phrasings synthetic."""
import pytest

semantic_router = pytest.importorskip("semantic_router")

pytestmark = pytest.mark.ci_safe

FLAG = "ZOE_ROUTER_CLOCK_STATEMENT_PRECEDENCE"

STATEMENTS = [
    "um, it's about 9:15, so probably soon",
    "uh, let me check, 7:05, so not long",
    "it's 10:30 already, I think we're nearly done",
]
KEPT = [
    # real weather / time asks (with or without a clock time) keep their tool
    ("what's the weather", "weather"),
    ("what's the weather like at 6:30 tonight", "weather"),
    ("will it rain around 5:45", "weather"),
    ("what time is it", "time"),
    ("what's the time", "time"),
    ("what time is it in Tokyo, it's 9:15 here", "time"),
    # clock-time-carrying domains that are not weather/time are untouched
    ("set a timer for 10:30", "timers"),
    ("remind me at 7:05", "reminders"),
    ("add a meeting at 9:15", "calendar"),
]


@pytest.mark.parametrize("text", STATEMENTS)
@pytest.mark.parametrize("domain", ["weather", "time"])
def test_statement_repointed_to_chat_when_flag_on(monkeypatch, text, domain):
    monkeypatch.setenv(FLAG, "1")
    assert semantic_router.clock_statement_target(text, domain) == "chat"
    assert semantic_router.question_precedence(text, domain) == (
        "chat", semantic_router.CLOCK_STATEMENT_REASON)


@pytest.mark.parametrize("text", STATEMENTS)
def test_flag_off_is_inert(monkeypatch, text):
    monkeypatch.delenv(FLAG, raising=False)
    assert semantic_router.clock_statement_target(text, "weather") is None
    assert semantic_router.question_precedence(text, "weather") is None


@pytest.mark.parametrize("text,domain", KEPT)
def test_real_asks_keep_their_claim(monkeypatch, text, domain):
    monkeypatch.setenv(FLAG, "1")
    assert semantic_router.clock_statement_target(text, domain) is None


def test_no_clock_time_means_no_repoint(monkeypatch):
    # negative control: the same shape without a clock time is not this rule's
    monkeypatch.setenv(FLAG, "1")
    assert semantic_router.clock_statement_target("um, it's about soon", "weather") is None
