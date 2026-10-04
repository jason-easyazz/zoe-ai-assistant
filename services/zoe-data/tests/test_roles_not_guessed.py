"""Class 3 — roles are STATED, never guessed from a first name.

A pasted list of names with no stated roles must not be assigned roles by first name. Rule:
gender and family role are never inferred from a name; unstated roles stay unknown and ONE
short question is asked. All names here are synthetic.

Negative controls: when the user DOES tie a name to a role on the same line, the fact is kept
(the guard is not a blanket role ban); a flag-off roster is not intercepted.
"""
import json

import pytest

pytestmark = pytest.mark.ci_safe

import people_roles as pr

LIST_NO_ROLES = (
    "Here are my friend's family details, along with a partner and two children.\n"
    "\n"
    "Jordan Smith - 26/10/1985\n"
    "Casey Smith - 16/12/1994\n"
    "Riley Smith- 30/05/2014\n"
    "Morgan Smith - 22/08/2017"
)
LIST_WITH_ROLES = (
    "My friend Jordan Smith, he has a wife Casey and 2 kids, here are their details\n"
    "\n"
    "Jordan Smith - 26/10/1985\n"
    "Casey Smith - 16/12/1994\n"
    "Riley Smith- 30/05/2014\n"
    "Morgan Smith - 22/08/2017"
)


# ── the source: the guard every extractor shares ────────────────────────────

@pytest.mark.parametrize("fact", [
    "Casey is the wife", "Casey Smith is the wife of Jordan Smith", "Riley is a girl",
    "Casey: wife of Jordan Smith", "Jordan is the husband", "Morgan (daughter)",
])
def test_a_list_of_names_yields_no_gendered_role(fact):
    assert pr.role_claims(fact), fact  # the shape IS a name-holds-role claim ...
    assert pr.named_role_claim_unsupported(fact, LIST_NO_ROLES) is True  # ... and it is refused


def test_extractor_value_with_a_guessed_role_is_refused():
    for name, value in (("Casey Smith", "wife of Jordan Smith"), ("Riley Smith", "girl"),
                        ("Morgan Smith", "daughter of Jordan Smith"), ("Jordan Smith", "husband")):
        assert pr.value_role_unsupported(name, value, LIST_NO_ROLES) is True, (name, value)


def test_the_same_roles_stated_by_the_user_are_kept():
    said = "No Jordan is my male friend, Casey is the wife and Riley and Morgan are the girls"
    assert pr.named_role_claim_unsupported("Casey is the wife", said) is False
    assert pr.value_role_unsupported("Casey Smith", "wife of Jordan Smith", said) is False
    # a role stated for ANOTHER name does not carry over
    assert pr.named_role_claim_unsupported("Jordan is the wife", said) is True


def test_role_in_the_intro_line_does_not_attach_to_a_name_on_another_line():
    assert pr.role_assignment_supported("Casey", "wife", LIST_NO_ROLES) is False
    assert pr.role_assignment_supported("Casey", "wife", LIST_WITH_ROLES) is True


def test_names_alone_carry_no_gender_signal():
    # the name never decides: same roles, names swapped, same verdict
    swapped = LIST_NO_ROLES.replace("Casey", "Tomas").replace("Jordan", "Alexa")
    assert pr.named_role_claim_unsupported("Tomas is the wife", swapped) is True
    assert pr.named_role_claim_unsupported("Alexa is the wife", swapped) is True


# ── the live extractors ──────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._p)}}]}


class _Client:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return _Resp(self._p)


async def test_person_llm_drops_roles_it_guessed_from_names(monkeypatch):
    import person_extractor
    import person_extractor_llm as pel

    items = [
        {"name": "Casey Smith", "fact_type": "preference", "value": "wife of Jordan Smith"},
        {"name": "Riley Smith", "fact_type": "preference", "value": "daughter of Jordan Smith"},
        {"name": "Casey Smith", "fact_type": "birthday", "value": "16 December 1994"},
    ]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append((name, fact_type, value))
        return True

    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)
    written = await pel.process_text_llm(LIST_NO_ROLES, user_id="demo-roles-list")
    # the date survives; both guessed roles are gone
    assert applied == [("Casey Smith", "birthday", "16 December 1994")]
    assert written == 1


async def test_person_llm_control_keeps_a_stated_role(monkeypatch):
    import person_extractor
    import person_extractor_llm as pel

    items = [{"name": "Casey", "fact_type": "preference", "value": "wife of Jordan Smith"}]
    monkeypatch.setattr(pel.httpx, "AsyncClient", lambda **k: _Client(items))
    applied = []

    async def fake_apply(name, fact_type, value, **kw):
        applied.append(value)
        return True

    monkeypatch.setattr(person_extractor, "apply_person_fact", fake_apply)
    await pel.process_text_llm(LIST_WITH_ROLES, user_id="demo-roles-list")
    assert applied == ["wife of Jordan Smith"]


async def test_turn_digest_drops_a_guessed_role_and_keeps_a_stated_one(monkeypatch):
    import memory_digest as md

    stored = []

    class Svc:
        async def ingest(self, text, **kw):
            stored.append(text)
            return type("R", (), {"id": f"x{len(stored)}", "text": text})()

        async def list_by_status(self, **kw):
            return []

    class _R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([
                {"type": "relationship", "fact": "Casey Smith is the wife of Jordan Smith"},
                {"type": "profile", "fact": "Riley Smith was born on 30 May 2014"},
            ])}}]}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _R()

    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())
    monkeypatch.setattr(md.httpx, "AsyncClient", lambda **k: _C())

    async def no_facts(*a, **k):
        return ""

    import zoe_agent
    monkeypatch.setattr(zoe_agent, "_mempalace_load_user_facts", no_facts, raising=False)
    await md.run_turn_digest("demo-roles-list", LIST_NO_ROLES, "", session_id="s")
    assert not any("wife" in t for t in stored), stored
    assert any("30 May 2014" in t for t in stored), stored


async def test_contact_offer_drops_a_relationship_the_user_never_stated(monkeypatch):
    import latent_intent_detector as lid

    async def fake_complete(prompt):
        assert "Never infer the relationship from the name" in prompt
        return json.dumps([{"action_type": "person_create", "offer_phrase": "Add Casey?",
                            "pre_filled_slots": {"name": "Casey Smith", "relationship": "wife"}}])

    async def not_a_contact(name, user_id):
        return False

    monkeypatch.setattr(lid, "_complete", fake_complete)
    monkeypatch.setattr(lid, "_already_a_contact", not_a_contact)
    monkeypatch.setattr(lid, "_person_enabled", lambda: True)
    out = await lid.detect(LIST_NO_ROLES, user_id="demo-roles-list", session_id="s")
    assert out and out[0]["pre_filled_slots"] == {"name": "Casey Smith"}  # relationship left unknown


def test_every_extraction_prompt_carries_the_rule():
    import latent_intent_detector as lid
    import memory_digest as md
    import person_extractor_llm as pel

    rendered = (
        md._TURN_EXTRACTION_PROMPT.format(user_message="x"),
        md._EXTRACTION_PROMPT.format(chat_text="x"),
        pel._EXTRACTION_PROMPT.format(text="x", rules=pel._EXTRACTION_RULES),
        pel._EXTRACTION_PROMPT_CONF.format(text="x", rules=pel._EXTRACTION_RULES),
    )
    for prompt in rendered:
        assert "Never infer a person's gender" in prompt
        assert "A pet (dog, cat...) is never a child" in prompt
    assert "Never infer the relationship from the name" in lid._PERSON_HINT


# ── the neutral restatement + the one question ───────────────────────────────

def test_roster_without_roles_is_recognised_and_restated_neutrally():
    assert pr.is_unlabelled_roster(LIST_NO_ROLES) is True
    reply = pr.roster_reply(LIST_NO_ROLES)
    assert "Jordan Smith (26 October 1985)" in reply  # dates are day-first in the restatement
    assert "which one is your friend?" in reply
    for guessed in ("wife", "husband", "daughter", "girls", "son", "mother", "father"):
        assert guessed not in reply.lower()
    assert reply.count("?") == 1  # ONE short question


def test_roster_with_stated_roles_is_not_intercepted():
    assert pr.is_unlabelled_roster(LIST_WITH_ROLES) is False
    assert pr.is_unlabelled_roster("Jordan - 26/10/1985\nCasey - 16/12/1994") is False  # < 3 lines
    assert pr.is_unlabelled_roster("what's the weather") is False


async def test_fast_tier_roster_flag_dark(monkeypatch):
    import fast_tiers

    monkeypatch.delenv("ZOE_ROSTER_NEUTRAL_ASK", raising=False)
    off = await fast_tiers.resolve(LIST_NO_ROLES, "demo-roles-list", "s", channel="chat",
                                   run_tier0=False)
    assert off is None or off.tier != "roster"
    monkeypatch.setenv("ZOE_ROSTER_NEUTRAL_ASK", "1")
    on = await fast_tiers.resolve(LIST_NO_ROLES, "demo-roles-list", "s", channel="chat",
                                  run_tier0=False)
    assert on is not None and on.tier == "roster" and "which one is your friend?" in on.reply
    # a stated-role list still goes to the brain
    again = await fast_tiers.resolve(LIST_WITH_ROLES, "demo-roles-list", "s", channel="chat",
                                     run_tier0=False)
    assert again is None or again.tier != "roster"
