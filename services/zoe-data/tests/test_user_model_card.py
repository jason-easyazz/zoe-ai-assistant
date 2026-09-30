"""user_model_card: the deterministic card rules (pure; invented data, no store, no DB).

Pins the composition table: categories, newest wins, current facts only, caps, PII,
the age limit, byte-stability and "" for empty. Negative controls: drop the status check
in ``_eligible`` (the superseded half-marathon comes back); drop the people-first match
(the sister's diet lands on the user's Diet line); drop the ``sorted`` (shuffled rows give
different bytes).
"""
import datetime as dt
import random

import pytest

import user_model_card as c
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe
NOW = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc)
_n = iter(range(10_000))


def ref(text, typ="fact", days_ago=1, **meta):
    i = next(_n)
    added = (NOW - dt.timedelta(days=days_ago, seconds=i)).isoformat().replace("+00:00", "Z")
    md = {"memory_type": typ, "status": "approved", "source": "turn_digest", "added_at": added,
          **meta}
    return MemoryRef(id=f"m{i:04d}", text=text, metadata=md)


def card(rows, portrait=None, name="Ottilie"):
    return c.render_card(c.build_card(name, rows, portrait, now=NOW))


def ottilie():
    """The twin A/B's synthetic profile, as the turn digest stores it (third person).
    Same-day rows are one second apart, listed newest first."""
    return [
        ref("User has been vegetarian for about ten years.", "profile", 3),
        ref("User works night shifts as an ICU nurse, so sleeps during the day.", "habit", 3),
        ref("User plays the cello in a community orchestra on Tuesday evenings.", "habit", 3),
        ref("User's old greyhound Biscuit gets stiff on cold mornings.", "pet", 3),
        ref("User is training for their first half-marathon in March.", "event", 3,
            status="superseded"),
        ref("User dropped the half-marathon and is doing a 10k in May instead.", "event", 1),
        ref("User is learning Portuguese for a trip to Porto next spring.", "event", 3),
        ref("User is allergic to peanuts and carries an EpiPen.", "health", 3),
        ref("User prefers short, direct answers, not long lists.", "preference", 3),
        ref("User can't stand coriander, it tastes like soap to them.", "preference", 3),
        ref("User doesn't drink alcohol at all.", "preference", 3),
        ref("User is nervous about their cello audition next Friday.", "event", 2,
            candidate_affect="nervous"),
    ]


def test_the_card_for_the_ab_profile():
    text = card(ottilie(), "Ottilie is warm. She likes short, direct answers and gets to the point.")
    assert text == (
        "Name: Ottilie\n"
        "Prefers: short, direct answers, not long lists\n"
        "Diet: has been vegetarian for about ten years; allergic to peanuts and carries an EpiPen; "
        "can't stand coriander, it tastes like soap to them; doesn't drink alcohol at all\n"
        "Work & schedule: works night shifts as an ICU nurse, so sleeps during the day\n"
        "People & pets: old greyhound Biscuit gets stiff on cold mornings\n"
        "Current: dropped the half-marathon and is doing a 10k in May instead (noted 29 Sep); "
        "learning Portuguese for a trip to Porto next spring (noted 27 Sep)\n"
        "Enjoys: plays the cello in a community orchestra on Tuesday evenings\n"
        "How they talk (weekly portrait): She likes short, direct answers and gets to the point.")
    assert "training for" not in text, "superseded row served"
    assert "nervous" not in text, "a feeling is continuity's job, not the card's"
    assert len(text) <= c.CARD_MAX_CHARS


@pytest.mark.parametrize("meta", [
    {"status": "superseded"}, {"status": "pending"}, {"status": "archived"},
    {"status": "rejected"}, {"status": "disputed"},
    {"memory_type": "insight"}, {"memory_type": "note"}, {"memory_type": "journal"},
    {"memory_type": "emotional_moment"}, {"memory_type": "failure"},
    {"source": "synthesis"}, {"source": "profile-analysis"}, {"source": "note_created"},
    {"source": "journal_created"}, {"expires_at": "2026-10-02T00:00:00Z"},
    {"candidate_affect": "anxious"},
])
def test_only_current_approved_person_facts(meta):
    row = ref("User is vegetarian.")
    row.metadata.update(meta)
    assert card([row]) == ""
    assert card([ref("User is vegetarian.")]) == "Name: Ottilie\nDiet: vegetarian"


@pytest.mark.parametrize("text", [
    '{"observed_patterns": []}', "User's name is Ottilie.", "User's full name is Ottilie Grey.",
    "User's sister's phone is 0412 345 678.", "User's email is o@example.org.",
    "User's wifi password is hunter2.", "User's card is 4111 1111 1111 1111.",
])
def test_unusable_or_pii_items_are_dropped(text):
    assert card([ref(text, "relationship")]) == ""


def test_newest_wins_subsets_dedupe_and_caps():
    rows = [ref(f"User eats {food}.", days_ago=d)
            for d, food in enumerate(["lentils", "tofu", "tempeh", "rice", "beans", "kale"])]
    line = card(rows).split("\n")[1]
    assert line == "Diet: eats lentils; eats tofu; eats tempeh; eats rice", "4 newest, newest first"
    rows = [ref("User is vegetarian.", days_ago=5), ref("User is strictly vegetarian.", days_ago=1)]
    assert card(rows).split("\n")[1] == "Diet: strictly vegetarian"
    people = [ref(f"User's friend Pat{i} lives nearby.", "relationship", i) for i in range(8)]
    assert card(people).split("\n")[1].count(";") == 4, "People & pets holds 5"
    music = [ref(f"User enjoys band{i}.", "preference", i, source="music_digest") for i in range(4)]
    assert card(music).split("\n")[1] == "Enjoys: enjoys band0", "music taste: one row"


def test_people_first_then_table_order():
    assert card([ref("User's sister is vegan.")]).split("\n")[1] == "People & pets: sister is vegan"
    assert card([ref("Marisol loves hiking.", "person")]).startswith("Name: Ottilie\nPeople & pets:")
    assert card([ref("User's birthday is 3 March.")]).split("\n")[1] == "About: birthday is 3 March"
    assert card([ref("User is working on a garden shed.")]).split("\n")[1].startswith("Current:")
    assert card([ref("User lives in Fremantle.")]).split("\n")[1] == "Places: lives in Fremantle"
    assert card([ref("User wears odd socks.")]) == "", "no category, no type fallback"
    assert card([ref("User wears odd socks.", "habit")]).split("\n")[1].startswith("Enjoys:")


def test_current_is_dated_and_ages_out_other_lines_do_not():
    fresh = ref("User is planning a trip to Kyoto.", "event", c.CURRENT_MAX_AGE_DAYS - 1)
    stale = ref("User is planning a trip to Oslo.", "event", c.CURRENT_MAX_AGE_DAYS + 1)
    text = card([fresh, stale])
    assert "Kyoto (noted " in text and "Oslo" not in text
    assert card([ref("User is vegetarian.", days_ago=900)]).endswith("Diet: vegetarian")


def test_byte_stable_for_the_same_rows_in_any_order():
    rows = ottilie()
    first = c.build_card("Ottilie", rows, "She is direct.", now=NOW)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert c.build_card("Ottilie", shuffled, "She is direct.", now=NOW) == first
    text = c.render_card(first)
    assert c.card_version(text) == c.card_version(c.render_card(first)) and len(c.card_version(text)) == 16


def test_empty_and_name_only_are_empty():
    assert card([]) == "" and c.card_version("") == ""
    assert card([], "She is direct and brief.") == "", "a style line alone is not a card"


def test_budget_drops_from_the_end_of_the_render_order():
    long = " and".join(["x"] * 25)  # ≈ 100 chars, cut to ITEM_MAX_CHARS
    rows = [ref(f"User eats {i}{long}.", days_ago=1) for i in range(4)] + \
           [ref(f"User lives in town{i}{long}.", days_ago=1) for i in range(2)] + \
           [ref(f"User's birthday is {i}{long}.", days_ago=1) for i in range(2)] + \
           [ref(f"User enjoys chess{i}{long}.", "habit", 1) for i in range(3)] + \
           [ref(f"User works at firm{i}{long}.", days_ago=1) for i in range(3)] + \
           [ref(f"User's friend Al{i} {long}.", "relationship", 1) for i in range(5)]
    text = card(rows, "She is direct and brief. " + "y" * 150)
    assert len(text) <= c.CARD_MAX_CHARS
    assert "How they talk" not in text and "About:" not in text, "lowest priority goes first"
    assert "Diet:" in text and "Work & schedule:" in text


def test_render_keeps_only_live_items():
    built = c.build_card("Ottilie", ottilie(), None, now=NOW)
    ids = [it[2] for it in built["items"]]
    assert c.render_card(built, set(ids)) == c.render_card(built)
    dropped = next(it for it in built["items"] if "10k" in it[1])
    assert "10k" not in c.render_card(built, set(ids) - {dropped[2]})
    assert c.render_card(built, set()) == ""


def test_style_line_is_the_first_style_sentence_scrubbed():
    assert c.style_line("She runs. She prefers blunt feedback. She is direct.") == \
        "She prefers blunt feedback."
    assert c.style_line("She runs a lot.") == ""
    assert c.style_line("She is direct; her password is hunter2.") == ""
