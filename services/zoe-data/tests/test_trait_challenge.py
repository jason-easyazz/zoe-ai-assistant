"""trait_challenge + the card's tentative marking (pure / fake store; invented people only).

Negative controls: drop the article requirement (a bare state "I'm not sure I'm ready" would flag rows);
drop the negation check in ``matching_rows`` (a row that already denies the trait is flagged again);
drop the ``speaker_verified`` guard (a stranger's hedge parks the owner's row); drop ``is_tentative``
(an inferred or unconfirmed religion row is stated flat on the card).
"""
import asyncio
import datetime as dt

import pytest

import trait_challenge as tc
import user_model_card as card
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe


@pytest.mark.parametrize("turn,trait", [
    ("Well, I'm not sure I'm a mountain person, but, um... at least she knows me.", "mountain person"),
    ("I’m not really a morning person", "morning person"),
    ("I don't think I'm a big spender.", "big spender"),
    ("Honestly I wouldn't say I'm an early riser", "early riser"),
    ("I am not sure that I am a chess player and that's fine", "chess player"),
])
def test_hedge_or_denial_is_read(turn, trait):
    assert tc.challenged_trait(turn) == trait


@pytest.mark.parametrize("turn", [
    "I'm not sure I'm ready for this",          # a state, no article
    "I'm a mountain person",                    # an affirmation
    "She's not a morning person",               # someone else
    "I'm not sure what a mountain person is",
    "",
])
def test_non_challenges_are_ignored(turn):
    assert tc.challenged_trait(turn) is None


def row(i, text, status="approved", **meta):
    return MemoryRef(id=i, text=text, metadata={"status": status, "user_id": "ottilie", **meta})


def test_matching_rows_needs_every_content_word_and_skips_negations():
    rows = [row("a", "User is a mountain person."), row("b", "User's job/role: mountain person"),
            row("c", "User is not a mountain person."), row("d", "User loves the mountains."),
            row("e", "User is a person who likes tea."), row("f", "User is a mountain person.", status="pending")]
    assert [r.id for r in tc.matching_rows("mountain person", rows)] == ["a", "b"]


class FakeSvc:
    def __init__(self, rows):
        self.rows, self.flagged = rows, []

    async def list_by_status(self, *, user_id, status, limit):
        return [r for r in self.rows if r.metadata["status"] == status]

    async def flag_challenged(self, mem_id, *, user_id, utterance):
        self.flagged.append((mem_id, user_id))
        return True


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_apply_flags_only_the_matching_rows():
    svc = FakeSvc([row("a", "User is a mountain person."), row("d", "User plays the cello.")])
    out = run(tc.apply_challenge("ottilie", "Well, I'm not sure I'm a mountain person, but...", svc=svc))
    assert out == ["a"] and svc.flagged == [("a", "ottilie")]


def test_apply_ignores_an_unverified_speaker_and_a_plain_turn():
    svc = FakeSvc([row("a", "User is a mountain person.")])
    assert run(tc.apply_challenge("ottilie", "I'm not sure I'm a mountain person", svc=svc,
                                  speaker_verified=False)) == []
    assert run(tc.apply_challenge("ottilie", "I love the mountains a lot", svc=svc)) == []
    assert svc.flagged == []


# ---- the card never states an inferred / unconfirmed special-category trait flat ----------------

NOW = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc)


def ref(i, text, typ="profile", **meta):
    md = {"memory_type": typ, "status": "approved", "source": "turn_digest",
          "added_at": "2026-09-29T10:00:00Z", **meta}
    return MemoryRef(id=i, text=text, metadata=md)


def render(rows):
    return card.render_card(card.build_card("Ottilie", rows, None, now=NOW))


def test_inferred_row_is_marked_unconfirmed():
    out = render([ref("1", "User plays the cello in an orchestra.", "habit",
                      authority="inferred", authority_class="model_from_transcript")])
    assert "plays the cello in an orchestra (unconfirmed)" in out


def test_stated_row_is_not_marked():
    out = render([ref("1", "User plays the cello in an orchestra.", "habit",
                      authority="user_stated", authority_class="user_stated")])
    assert "unconfirmed" not in out


def test_religion_trait_is_marked_unless_confirmed():
    said = ref("1", "User is a person of deep faith.", authority="user_stated", authority_class="user_stated",
               concept_tags="religion,identity")
    assert "(unconfirmed)" in render([said])
    confirmed = ref("1", "User is a person of deep faith.", authority="user_confirmed",
                    authority_class="user_confirmed", concept_tags="religion,identity")
    assert "unconfirmed" not in render([confirmed])


def test_legacy_row_without_stamped_provenance_is_unchanged():
    assert "unconfirmed" not in render([ref("1", "User plays the cello in an orchestra.", "habit")])
