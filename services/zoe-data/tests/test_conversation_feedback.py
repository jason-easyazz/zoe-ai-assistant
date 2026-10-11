"""conversation_feedback: "that was wrong" / "good answer" leave the thumb the chat buttons leave, on every lane.

Break-the-fix (done once, recorded in the PR): make ``handle`` return None before the insert -> ``test_*_writes_*`` go red; make
``classify`` accept "perfect" -> ``test_phrases_that_are_not_feedback`` goes red; drop the guest wall -> ``test_walls_write_nothing``.
"""
import pytest

pytestmark = pytest.mark.ci_safe

import conversation_feedback as cf
from lane_parity_rig import FakeDB

import contextlib

USER = "demo_feedback_user"
SESSION = "demo-feedback-session"


@pytest.fixture
def db(monkeypatch):
    cf._RECENT.clear()
    d = FakeDB()
    d.add_message(SESSION, "user", "Who won the 1987 Australian Open?", USER)
    aid = d.add_message(SESSION, "assistant", "Stefan Edberg won it.", USER)
    d.assistant_id = aid

    @contextlib.asynccontextmanager
    async def ctx(*_a, **_k):
        yield d

    monkeypatch.setattr("db_pool.get_db_ctx", ctx)
    monkeypatch.delenv(cf.ENV, raising=False)
    return d


@pytest.mark.parametrize("text,kind,value", [
    ("That was wrong.", "thumbs_down", ""),
    ("that's not right", "thumbs_down", ""),
    ("Wrong answer", "thumbs_down", ""),
    ("No, that's not what I meant", "thumbs_down", ""),
    ("you got that wrong", "thumbs_down", ""),
    ("That's wrong, it's Thursday", "correction", "Thursday"),
    ("no, that is not right it was Friday", "correction", "Friday"),
    ("Good answer.", "thumbs_up", ""),
    ("Great answer, Zoe", "thumbs_up", ""),
    ("That was really helpful, thanks", "thumbs_up", ""),
    ("That's exactly what I needed", "thumbs_up", ""),
])
def test_classify(text, kind, value):
    assert cf.classify(text) == (kind, value)


@pytest.mark.parametrize("text", [
    "Good morning", "Perfect", "good", "I was wrong about that", "What was wrong with that?", "Is that right?",
    "That's wrong because the tax rules changed in July and also the date on the form is different",
    "Add my brother Percival", "Thanks", "Are you sure?", "",
])
def test_phrases_that_are_not_feedback(text):
    assert cf.classify(text) is None


async def test_wrong_writes_thumbs_down_for_the_last_answer_and_lets_the_turn_go_on(db):
    assert await cf.handle("That was wrong.", USER, SESSION) is None          # no reply: the turn still gets its answer
    [row] = db.feedback
    assert row["params"][1:] == (db.assistant_id, USER, "thumbs_down", None) or row["params"][1:4] == (db.assistant_id, USER, "thumbs_down")


async def test_correction_carries_the_value(db):
    assert await cf.handle("That's wrong, it's Thursday", USER, SESSION) is None
    [row] = db.feedback
    assert row["params"][3] == "correction" and row["params"][4] == "Thursday"


async def test_kind_verdict_writes_thumbs_up_and_answers_in_four_words(db):
    assert await cf.handle("Good answer.", USER, SESSION) == cf.UP_REPLY
    assert [f["params"][3] for f in db.feedback] == ["thumbs_up"]


@pytest.mark.parametrize("who,verified,allow", [
    ("guest", None, True), ("voice-guest", None, True), ("", None, True), ("voice-daemon", None, True),
    (USER, False, True),          # a voice the speaker gate rejected cannot leave a thumb under the member's name
    (USER, None, False),          # a dry replay writes nothing
])
async def test_walls_write_nothing(db, who, verified, allow):
    await cf.handle("That was wrong.", who, SESSION, speaker_verified=verified, allow_writes=allow)
    await cf.handle("Good answer.", who, SESSION, speaker_verified=verified, allow_writes=allow)
    assert db.feedback == []


async def test_nothing_to_rate_nothing_written(db):
    assert await cf.handle("Good answer.", USER, "a-session-with-no-reply") is None
    assert db.feedback == []


async def test_flag_off_is_inert(db, monkeypatch):
    monkeypatch.setenv(cf.ENV, "0")
    assert await cf.handle("Good answer.", USER, SESSION) is None
    assert db.feedback == []


async def test_a_store_failure_never_breaks_the_turn(monkeypatch):
    @contextlib.asynccontextmanager
    async def boom(*_a, **_k):
        raise RuntimeError("store down")
        yield  # pragma: no cover

    monkeypatch.setattr("db_pool.get_db_ctx", boom)
    assert await cf.handle("Good answer.", USER, SESSION) is None


def test_the_thumbs_endpoint_and_the_tier_share_one_writer():
    """``routers/chat.py`` must not carry a second INSERT INTO chat_feedback (the single-writer rule of the register's G15)."""
    import pathlib

    chat_src = (pathlib.Path(__file__).resolve().parents[1] / "routers" / "chat.py").read_text()
    assert "INSERT INTO chat_feedback" not in chat_src
    assert "record_feedback" in chat_src


async def test_one_verdict_on_one_reply_is_one_row_however_many_stages_call_it(db):
    """The voice lane calls handle() at the top of the turn and again in the conversation phase."""
    await cf.handle("That was wrong.", USER, SESSION)
    await cf.handle("That was wrong.", USER, SESSION)
    assert len(db.feedback) == 1
    assert await cf.handle("Good answer.", USER, SESSION) == cf.UP_REPLY      # a different verdict is a different row
    assert await cf.handle("Good answer.", USER, SESSION) == cf.UP_REPLY      # ... and a repeat still gets its reply
    assert [f["params"][3] for f in db.feedback] == ["thumbs_down", "thumbs_up"]
