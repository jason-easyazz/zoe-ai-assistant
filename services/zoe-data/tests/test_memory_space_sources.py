"""The owner's Memories page: each row's STAGE, WHERE IT CAME FROM, its mute mark, and the "Let go" list (GET /api/memories/mine).

Read-only fields over columns the store already holds, shown to the row's OWNER only. One group per promise, each with the
negative control that was run (the wall broken by hand -> the test went red -> restored):

  stage      the four labels come from the stored authority class only; an unknown class is the weakest stage
             (control: ``stage_of`` returning "told" for every class -> test_stage_is_the_stored_class_only went red)
  came_from  the owner's own quoted words, the channel and the day; a source the own-words wall will not quote (another
             speaker, a curator, a transcript pass) gives day + channel only; no source = ``kept`` False
             (control: ``came_from`` using the raw ``source_excerpt`` instead of ``owner_quote`` ->
             test_another_speakers_words_never_ship went red)
  owner      another member, a guest, or no session never gets these fields (the identity wall of GET /mine; control: the
             ``_owns`` check in ``read_let_go`` removed -> test_let_go_is_own_rows_only went red)
  let go     superseded / archived rows with a closed-list reason; a forgotten row and a private-class row are never listed
             (control: ``pa.sensitive_classes`` skip removed -> test_let_go_never_lists_a_private_row went red;
             "rejected" added to LET_GO_STATUSES -> test_a_forgotten_row_is_never_let_go went red)
  mute       ``muted`` only when ZOE_RESTRAINT enforces; in shadow the key is absent (a recorded mute that does not bite is not
             "muted"); control: the mode check in ``_enforced_mutes`` removed -> test_shadow_mode_says_nothing_about_mutes went red

Synthetic data only (``demo_bar_`` ids and invented words), fake Chroma (``ci_safe``).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import memory_authority as auth
import memory_space
from test_memory_space import GUEST, H, ME, ME_USER, OTHER, as_member, put, row_id, run, svc, _env  # noqa: F401

pytestmark = pytest.mark.ci_safe

LIKE = "I like dark roast coffee with oat milk."
LIKE_SAID = "honestly I like dark roast coffee with oat milk"
SHED = "I keep my bike in the shed behind the garage."
OTHERS_WORDS = "Quillfeather said the ferry to Wexmoor leaves at nine"


def _items(data):
    return [it for g in data.get("groups", []) for it in g.get("items", [])]


def _item(data, needle):
    return next(it for it in _items(data) if needle in it["text"])


def _set_meta(svc, rid, **kw):
    text, meta = svc._col.rows[rid]
    meta.update(kw)
    svc._col.rows[rid] = (text, meta)


# ── stage ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls,origin,want", [
    (auth.CURATED, "curator", "added"),
    (auth.OPERATOR, "admin", "added"),
    (auth.USER_CONFIRMED, "review_ui", "confirmed"),
    (auth.USER_STATED_DERIVED, "omi_review", "confirmed"),     # approved from the day review
    (auth.USER_STATED, "chat_regex", "told"),
    (auth.USER_STATED_DERIVED, "turn_digest", "noticed"),
    (auth.USER_UNVERIFIED, "voice", "noticed"),
    (auth.MODEL_FROM_TURN, "brain_tool", "noticed"),
    (auth.EXTERNAL_SOURCE, "external_source", "noticed"),
    (auth.MODEL_FROM_TRANSCRIPT, "digest", "noticed"),
    ("", "", "noticed"),                                         # unread class: never promoted
    ("made_up_class", "chat", "noticed"),
])
def test_stage_is_the_stored_class_only(cls, origin, want):
    it = SimpleNamespace(cls=cls, origin=origin)
    assert memory_space.stage_of(it) == want
    assert memory_space.STAGE_LABELS[want]
    assert len(set(memory_space.STAGE_LABELS.values())) == 4


def test_a_chat_row_is_you_told_me_with_your_words(svc):
    put(svc, LIKE, source_excerpt=LIKE_SAID)
    data = as_member().get("/api/memories/mine", headers=H).json()
    it = _item(data, "coffee")
    assert it["stage"] == "told" and it["stage_label"] == "You told me"
    src = it["came_from"]
    assert src["kept"] is True and src["channel"] == "chat" and src["channel_label"] == "In chat"
    assert "dark roast" in src["quote"] and src["day"]


def test_a_row_with_no_source_says_so(svc):
    put(svc, SHED)
    rid = row_id(svc, ME, "bike")
    _set_meta(svc, rid, source_excerpt="", user_turn_id="")
    src = _item(as_member().get("/api/memories/mine", headers=H).json(), "bike")["came_from"]
    assert src["kept"] is False and src["quote"] == ""


def test_another_speakers_words_never_ship(svc):
    """A row of MINE whose stored excerpt is a pendant recording (another speaker's words): day + channel, never the words."""
    put(svc, SHED)
    rid = row_id(svc, ME, "bike")
    _set_meta(svc, rid, source="omi_review", origin="omi_review", source_excerpt=OTHERS_WORDS)
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.status_code == 200
    assert "Quillfeather" not in r.text and "Wexmoor" not in r.text
    src = _item(r.json(), "bike")["came_from"]
    assert src["kept"] is True and src["quote"] == "" and src["channel"] == "pendant" and src["day"]


def test_a_curators_note_is_not_quoted(svc):
    put(svc, SHED)
    rid = row_id(svc, ME, "bike")
    _set_meta(svc, rid, source="curator", origin="curator", source_excerpt=OTHERS_WORDS)
    r = as_member().get("/api/memories/mine", headers=H)
    assert "Quillfeather" not in r.text
    assert _item(r.json(), "bike")["stage"] == "added"


def test_voice_channel(svc):
    put(svc, SHED)
    _set_meta(svc, row_id(svc, ME, "bike"), source="voice_regex")
    assert _item(as_member().get("/api/memories/mine", headers=H).json(), "bike")["came_from"]["channel"] == "voice"


# ── owner only ─────────────────────────────────────────────────────────────────────────────────

def test_another_member_never_sees_my_sources(svc):
    put(svc, LIKE, source_excerpt=LIKE_SAID)
    r = as_member({"user_id": OTHER, "role": "user"}).get("/api/memories/mine", headers=H)
    assert r.status_code == 200
    assert "dark roast" not in r.text and "honestly" not in r.text


@pytest.mark.parametrize("user", [GUEST, {"user_id": ME, "role": "guest"}])
def test_a_guest_gets_no_stage_or_source(svc, user):
    put(svc, LIKE, source_excerpt=LIKE_SAID)
    r = as_member(user).get("/api/memories/mine", headers=H)
    assert r.status_code in (401, 403)
    for needle in ("came_from", "stage", "let_go", "honestly", "coffee"):
        assert needle not in r.text


def test_no_session_header_gets_nothing(svc):
    put(svc, LIKE, source_excerpt=LIKE_SAID)
    r = as_member().get("/api/memories/mine")
    assert r.status_code == 401 and "let_go" not in r.text


# ── let go ─────────────────────────────────────────────────────────────────────────────────────

def _retire(svc, needle, user=ME, **meta):
    rid = row_id(svc, user, needle)
    _set_meta(svc, rid, **meta)
    return rid


def test_a_replaced_row_is_let_go_with_its_reason(svc):
    put(svc, SHED)
    _retire(svc, "bike", status="superseded", superseded_by_id="row-new")
    data = as_member().get("/api/memories/mine", headers=H).json()
    assert not any("bike" in it["text"] for it in _items(data)), "a retired row is not a current one"
    lg = data["let_go"]
    assert len(lg) == 1 and "bike" in lg[0]["text"]
    assert lg[0]["reason"] == "replaced" and lg[0]["reason_label"] == "Replaced"


def test_let_go_reasons_are_a_closed_list():
    now = 2_000_000_000.0
    f = memory_space.let_go_reason
    assert f({"status": "superseded"}, ME, now=now) == "replaced"
    assert f({"status": "archived", "duplicate_of": "x"}, ME, now=now) == "duplicate"
    assert f({"status": "archived", "expires_at": "2020-01-01T00:00:00Z"}, ME, now=now) == "expired"
    assert f({"status": "archived", "expires_at": now + 99}, ME, now=now) == ""
    assert f({"status": "archived", "reviewed_by": ME}, ME, now=now) == "by_you"
    assert f({"status": "archived", "reviewed_by": OTHER}, ME, now=now) == ""
    assert f({"status": "archived"}, ME, now=now) == ""          # no reason in the data = no reason shown
    assert f({"status": "rejected"}, ME, now=now) == ""
    assert set(memory_space.LET_GO_REASONS) == {"replaced", "duplicate", "expired", "by_you"}


def test_a_forgotten_row_is_never_let_go(svc):
    put(svc, SHED)
    _retire(svc, "bike", status="rejected", review_note="forget_memory_space")
    data = as_member().get("/api/memories/mine", headers=H).json()
    assert data["let_go"] == [] and "bike" not in str(data)


def test_let_go_never_lists_a_private_row(svc):
    put(svc, "I have migraines most weeks and my doctor wants a scan.")
    _retire(svc, "migraines", status="superseded")
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.json()["let_go"] == [] and "migraine" not in r.text.lower()


def test_let_go_never_lists_an_unanchored_guess(svc):
    """Control run: the unanchored skip in ``read_let_go`` removed -> this test went red."""
    put(svc, SHED)
    _retire(svc, "bike", status="archived", authority_basis="unanchored", source="digest", authority_class=auth.MODEL_FROM_TRANSCRIPT)
    assert as_member().get("/api/memories/mine", headers=H).json()["let_go"] == []


def test_let_go_is_own_rows_only(svc):
    put(svc, "My neighbour keeps a parrot called Biscuit.", user=OTHER)
    _retire(svc, "Biscuit", user=OTHER, status="superseded")
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.json()["let_go"] == [] and "Biscuit" not in r.text


def test_a_failed_let_go_read_leaves_the_list_out_not_the_view(svc, monkeypatch):
    put(svc, SHED)

    async def boom(*_a, **_k):
        raise RuntimeError("store down")

    monkeypatch.setattr(memory_space, "read_let_go", boom)
    r = as_member().get("/api/memories/mine", headers=H)
    assert r.status_code == 200 and "let_go" not in r.json() and "bike" in r.text


# ── mute ───────────────────────────────────────────────────────────────────────────────────────

def _mutes(monkeypatch, mode, mutes):
    import restraint

    monkeypatch.setattr(restraint, "mode", lambda: mode)

    async def listed(_uid):
        return mutes

    monkeypatch.setattr(restraint, "list_mutes_strict", listed)


def test_an_enforced_mute_marks_the_row(svc, monkeypatch):
    import restraint

    put(svc, SHED)
    put(svc, LIKE)
    _mutes(monkeypatch, "enforce", [restraint.Mute("m1", restraint.stems("bike shed"))])
    data = as_member().get("/api/memories/mine", headers=H).json()
    assert _item(data, "bike")["muted"] is True
    assert _item(data, "coffee")["muted"] is False


def test_shadow_mode_says_nothing_about_mutes(svc, monkeypatch):
    import restraint

    put(svc, SHED)
    _mutes(monkeypatch, "shadow", [restraint.Mute("m1", restraint.stems("bike shed"))])
    assert "muted" not in _item(as_member().get("/api/memories/mine", headers=H).json(), "bike")


def test_a_failed_mute_read_is_unknown_not_unmuted(svc, monkeypatch):
    import restraint

    put(svc, SHED)
    monkeypatch.setattr(restraint, "mode", lambda: "enforce")

    async def boom(_uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(restraint, "list_mutes_strict", boom)
    assert "muted" not in _item(as_member().get("/api/memories/mine", headers=H).json(), "bike")
