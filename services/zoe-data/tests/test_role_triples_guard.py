"""The role guard's evidence step reads ONLY id triples (ZOE_STRUCTURAL_CLAIMS; ``role_triples``).

The deferred review findings of #1912 (ledger 2026-10-07/08), each a test against the triple floor in ``enforce``:

  1. an OWNERLESS row ("Anika Reyes is a mother") must not license "your mother"          -> a triple always has an owner
  2. a NEGATED packet clause ("Anika Reyes is not my mother", "was never my wife") is not evidence -> a denial is the absence of a triple
  3. a LOWERCASE namesake ("anika patel is my mother; who is Anika Reyes?") must not license her -> full-name ids, case irrelevant
  4. a NESTED owner: "Mary Smith's friend's wife" is not "Mary Smith's brother's wife"          -> the owner chain is resolved over triples
  5. a stated "your mother's friend" must not be rewritten as if it claimed "your mother"       -> the reader no longer reads a possessive role as a claim
  plus: the owner's turn and the packet's prose are not parsed at all in enforce, an ambiguous given name names nobody, a gender-neutral edge
  cannot license a gendered claim, a failed read fails closed, ``shadow`` keeps the lexical decision and logs the disagreement.

Synthetic names only.
"""
from __future__ import annotations

import json
import logging

import pytest

import person_recall_floor as prf
import role_guess_guard as rg
import role_triples as rt
import structural_claims as sc
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

ASK = "Who is Anika Reyes?"
NAMES = ["Anika Reyes"]
IDS = {"Anika Reyes": "p-anika"}
ROLELESS = "## What I know about you\n- Anika Reyes: 2 November 1985 [mem:aaaa1111]\n"


def ts(people, triples):
    s = rt.TripleSet(names=dict(people))
    s.triples = [rt.Triple(*t) for t in triples]
    return s


PEOPLE = {"p-anika": "Anika Reyes", "p-callum": "Callum Reyes"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(rg.ENV, raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    monkeypatch.setenv(sc.ENV, "enforce")


def fixed(reply, triples, packet=ROLELESS, user_text=ASK, people=PEOPLE, ids=IDS):
    return rg.neutralise(reply, NAMES, packet, user_text, triples=ts(people, triples), ids=ids)


# -- the supported / unsupported basics ------------------------------------------------------------

def test_a_claim_is_allowed_iff_the_triple_is_in_the_set():
    triple = [("p-anika", "mother", "user")]
    assert fixed("Anika Reyes is your mother.", triple)[1] == []
    assert fixed("Anika Reyes is your mum.", triple)[1] == []                  # mum / mother: one kin code
    assert fixed("Anika Reyes is your wife.", triple)[1] == ["anika~wife"]
    assert fixed("Anika Reyes is your mother.", [])[1] == ["anika~mother"]
    # break the fix: with the triple floor off the same set is never consulted - the lexical packet decides
    monkey = pytest.MonkeyPatch()
    monkey.setenv(sc.ENV, "off")
    try:
        out, guessed = rg.neutralise("Anika Reyes is your mother.", NAMES, ROLELESS + "- User's mum is Anika Reyes\n", ASK,
                                     triples=ts(PEOPLE, []), ids=IDS)
    finally:
        monkey.undo()
    assert guessed == []


def test_enforce_does_not_parse_the_packet_or_the_owners_turn():
    packet = ROLELESS + "- User's mum is Anika Reyes [mem:cccc3333]\n"
    # lexical world: the packet states it, so it is allowed; triple world: no triple, so it is not
    assert rg.neutralise("Anika Reyes is your mother.", NAMES, packet, ASK)[1] == []
    assert fixed("Anika Reyes is your mother.", [], packet=packet)[1] == ["anika~mother"]
    assert fixed("Anika Reyes is your mother.", [], user_text="Anika Reyes is my mother, who is she again?")[1] == ["anika~mother"]


def test_a_gender_neutral_edge_cannot_license_a_gendered_claim_but_a_specific_one_licenses_the_general():
    spouse = [("p-anika", "spouse", "user")]
    assert fixed("Anika Reyes is your spouse.", spouse)[1] == []
    assert fixed("Anika Reyes is your wife.", spouse)[1] == ["anika~wife"]
    wife = [("p-anika", "wife", "user")]
    assert fixed("Anika Reyes is your partner.", wife)[1] == [] and fixed("Anika Reyes is your spouse.", wife)[1] == []
    assert fixed("Anika Reyes is your husband.", wife)[1] == ["anika~husband"]


def test_a_failed_read_fails_closed():
    broken = rt.TripleSet(complete=False)
    out, guessed = rg.neutralise("Anika Reyes is your mother.", NAMES, ROLELESS, ASK, triples=broken, ids=IDS)
    assert guessed == ["anika~mother"]


async def test_fetch_failure_is_an_incomplete_empty_set(monkeypatch):
    async def boom(uid):
        raise RuntimeError("db down")
    monkeypatch.setattr(rt, "_read", boom)
    got = await rt.fetch("demo-owner")
    assert got.complete is False and got.triples == [] and got.supports("p-anika", "mother", "user") is False


# -- the deferred findings of #1912 ------------------------------------------------------------------

def test_deferred_1_an_ownerless_packet_row_licenses_nothing():
    packet = ROLELESS + "- Anika Reyes is a mother [mem:dddd4444]\n"
    # the lexical guard still has the wildcard (that is the finding): it ALLOWS the claim
    assert rg.neutralise("Anika Reyes is your mother.", NAMES, packet, ASK)[1] == []
    # the triple floor has no row without an owner
    assert fixed("Anika Reyes is your mother.", [], packet=packet)[1] == ["anika~mother"]


@pytest.mark.parametrize("row", ["Anika Reyes is not my mother", "Anika Reyes was never my wife"])
def test_deferred_2_a_negated_packet_clause_is_not_evidence(row):
    packet = ROLELESS + f"- {row} [mem:eeee5555]\n"
    role = "wife" if "wife" in row else "mother"
    assert fixed(f"Anika Reyes is your {role}.", [], packet=packet)[1] == [f"anika~{role}"]
    # a stored denial is a closed / absent edge: even a reply that REVERSES it is held
    assert fixed(f"Anika Reyes is your {role}.", [("p-callum", role, "user")], packet=packet)[1] == [f"anika~{role}"]


def test_deferred_3_a_lowercase_namesake_licenses_nobody_else():
    people = {**PEOPLE, "p-patel": "Anika Patel"}
    triples = [("p-patel", "mother", "user")]
    out = fixed("Anika Reyes is your mother.", triples, people=people, user_text="anika patel is my mother; who is Anika Reyes?")
    assert out[1] == ["anika~mother"]
    patel = rg.neutralise("Anika Patel is your mother.", ["Anika Patel"], "", "who is anika patel?",
                          triples=ts(people, triples), ids={"Anika Patel": "p-patel"})
    assert patel[1] == []


def test_deferred_3b_an_ambiguous_given_name_names_nobody():
    people = {"p-a1": "Anika Reyes", "p-a2": "Anika Patel"}
    s = ts(people, [("p-a2", "mother", "user")])
    assert s.ids_for(("anika",)) == set()                      # two Anikas: the bare name is no handle
    assert s.ids_for(("anika", "patel")) == {"p-a2"} and s.ids_for(("ANIKA".casefold(), "REYES".casefold())) == {"p-a1"}


def test_deferred_4_nested_owners_are_resolved_over_triples():
    people = {**PEOPLE, "p-friend": "Priya Nair", "p-brother": "Dev Marsh"}
    triples = [("p-friend", "friend", "user"), ("p-anika", "wife", "p-friend")]
    assert fixed("Anika Reyes is your friend's wife.", triples, people=people)[1] == []
    assert fixed("Anika Reyes is your brother's wife.", triples, people=people)[1] == ["anika~wife"]
    assert fixed("Anika Reyes is your wife.", triples, people=people)[1] == ["anika~wife"]            # not the USER's wife
    assert fixed("Anika Reyes is the wife of your friend.", triples, people=people)[1] == []
    # Mary Smith's friend's wife is not Mary Smith's brother's wife (the nested swap of the review thread)
    people2 = {"p-anika": "Anika Reyes", "p-mary": "Mary Smith", "p-pat": "Pat Doyle", "p-bob": "Bob Doyle"}
    t2 = [("p-pat", "friend", "p-mary"), ("p-anika", "wife", "p-pat"), ("p-bob", "brother", "p-mary")]
    s2 = ts(people2, t2)
    assert s2.supports("p-anika", "wife", ("rel", ("friend",))) is False        # owner chain starts at the USER, who has no friend Pat
    assert s2.supports("p-anika", "wife", ("name", ("pat", "doyle"))) is True


def test_deferred_5_a_possessive_role_is_not_read_as_the_claim():
    # the reader used to take "your mother's friend" as the claim "Anika is your mother" and rewrote a correct reply
    for reply in ("Anika Reyes is your mother's friend.", "Anika Reyes is your brother's colleague."):
        assert rg.neutralise(reply, NAMES, ROLELESS, ASK)[0] == reply
        assert fixed(reply, [])[0] == reply
    # ... while a policed role behind a possessive is still a claim, with its owner chain
    assert fixed("Anika Reyes is your mother's sister.", [])[1] == ["anika~sister"]
    triples = [("p-callum", "mother", "user"), ("p-anika", "sister", "p-callum")]
    assert fixed("Anika Reyes is your mother's sister.", triples)[1] == []


def test_the_owner_pronoun_and_name_forms():
    triples = [("p-anika", "wife", "p-callum")]
    assert fixed("Anika Reyes is Callum's wife.", triples)[1] == []
    assert fixed("Anika Reyes is your wife.", triples)[1] == ["anika~wife"]
    assert fixed("Anika Reyes is the wife of Callum Reyes.", triples)[1] == []
    assert fixed("Anika Reyes is probably Callum's mum.", triples)[1] == ["anika~mum"]


# -- shadow keeps the lexical decision and logs the disagreement ---------------------------------------

def test_shadow_keeps_the_lexical_guard_and_logs_where_the_triple_floor_would_differ(monkeypatch, caplog):
    monkeypatch.setenv(sc.ENV, "shadow")
    packet = ROLELESS + "- User's mum is Anika Reyes [mem:cccc3333]\n"
    with caplog.at_level(logging.INFO):
        out, guessed = rg.neutralise("Anika Reyes is your mother.", NAMES, packet, ASK, triples=ts(PEOPLE, []), ids=IDS)
    assert guessed == [] and out == "Anika Reyes is your mother."                    # lexical: stated, so untouched
    assert "STRUCTURAL_FLOOR floor=role lane=reply mode=shadow lang=en lexical=allow structural=deny agree=0 applied=0" in caplog.text
    # off: no triple read, no log
    monkeypatch.setenv(sc.ENV, "off")
    caplog.clear()
    with caplog.at_level(logging.INFO):
        rg.neutralise("Anika Reyes is your mother.", NAMES, packet, ASK, triples=ts(PEOPLE, []), ids=IDS)
    assert "STRUCTURAL_FLOOR" not in caplog.text


def test_the_marker_line_reads_triples_in_enforce():
    triples = ts(PEOPLE, [("p-anika", "mother", "user")])
    assert rg.rule_line(NAMES, ROLELESS, triples=triples, ids=IDS) == ""                  # a triple states it: no marker
    assert "Relationship not stated for: Anika Reyes" in rg.rule_line(NAMES, ROLELESS, triples=ts(PEOPLE, []), ids=IDS)
    packet = ROLELESS + "- User's mum is Anika Reyes [mem:cccc3333]\n"
    assert "Relationship not stated for: Anika Reyes" in rg.rule_line(NAMES, packet, triples=ts(PEOPLE, []), ids=IDS)   # prose is not read
    assert rg.rule_line(NAMES, packet) == ""                                                                          # the lexical guard reads it


# -- the triples themselves -----------------------------------------------------------------------------

def test_triples_from_the_people_graph_rows():
    s = rt.triples_from_rows(
        [("p-1", "Anika Reyes", "Mother"), ("p-2", "Callum Reyes", ""), ("p-3", "Rex", "family")],
        [("p-2", "p-1", "spouse", "user_stated"), ("p-9", "p-1", "friend", "inferred"), ("p-2", "p-3", "pet", "")])
    kinds = {(t.person_id, t.kin, t.owner) for t in s.triples}
    assert ("p-1", "mother", "user") in kinds                                 # people.relationship: the account owner's edge
    assert ("p-1", "spouse", "p-2") in kinds and ("p-2", "spouse", "p-1") in kinds            # both directions of an edge
    assert not any(t.owner == "p-9" or t.person_id == "p-9" for t in s.triples)              # an inferred edge counts for nothing
    assert not any(t.person_id == "p-3" for t in s.triples)                                  # "family" / a pet: no kin code to be had


# -- end to end through the recall block and the reply stream ------------------------------------------

class _Resp:
    def __init__(self, text):
        self._t = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": self._t}}


class _Client:
    captured: dict = {}
    reply = "ok"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _Resp(type(self).reply)


async def _ask(monkeypatch, brain_reply, triples):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    monkeypatch.setattr(_Client, "reply", brain_reply)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def fake_floor(msg, user_id):
        return [prf.NamedPerson("Anika Reyes", "p-anika", "people")]

    async def fake_fetch(user_id, msg, focus=None):
        return ROLELESS

    async def no_continuity(*a, **k):
        return ""

    async def fake_triples(user_id, **k):
        return triples

    monkeypatch.setattr(zc, "_named_person_floor", fake_floor)
    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    monkeypatch.setattr(zc, "_continuity_context_block", no_continuity)
    monkeypatch.setattr(rt, "fetch", fake_triples)
    out = [c async for c in zc.run_flue_brain_streaming(ASK, "s22", "demo-owner")]
    return "".join(out), json.loads(_Client.captured["content"])["message"]


async def test_end_to_end_enforce_a_triple_licenses_the_reply_and_its_absence_rewrites_it(monkeypatch):
    reply, sent = await _ask(monkeypatch, "Anika Reyes is your mother.", ts(PEOPLE, [("p-anika", "mother", "user")]))
    assert reply == "Anika Reyes is your mother." and "Relationship not stated" not in sent
    reply, sent = await _ask(monkeypatch, "Anika Reyes is your mother.", ts(PEOPLE, []))
    assert "mother" not in reply and "someone you've told me about" in reply and "Relationship not stated for: Anika Reyes" in sent
    # break the fix: the triple floor off -> the (role-less) packet alone decides, exactly as before this change
    monkeypatch.setenv(sc.ENV, "off")
    reply, sent = await _ask(monkeypatch, "Anika Reyes is your mother.", ts(PEOPLE, [("p-anika", "mother", "user")]))
    assert "mother" not in reply and "Relationship not stated for: Anika Reyes" in sent


async def test_end_to_end_a_db_outage_fails_closed_in_enforce_and_is_invisible_in_shadow(monkeypatch):
    broken = rt.TripleSet(complete=False)
    reply, _ = await _ask(monkeypatch, "Anika Reyes is your mother.", broken)
    assert "mother" not in reply
    monkeypatch.setenv(sc.ENV, "shadow")
    reply, _ = await _ask(monkeypatch, "Anika Reyes is your mother.", broken)
    assert "mother" not in reply          # role-less packet: the LEXICAL guard rewrites it, as it always did


async def test_shadow_fetches_the_triples_off_the_critical_path_and_logs_after_the_reply(monkeypatch, caplog):
    """Shadow adds nothing before the brain is called: the read is a background task the reply filter collects once the reply is whole."""
    monkeypatch.setenv(sc.ENV, "shadow")
    order: list = []

    async def slow_triples(user_id, **k):
        order.append("fetch-start")
        await __import__("asyncio").sleep(0.05)
        order.append("fetch-done")
        return ts(PEOPLE, [])

    monkeypatch.setattr(rt, "fetch", slow_triples)
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    monkeypatch.setattr(_Client, "reply", "Anika Reyes is your mother.")
    import httpx

    class Brain(_Client):
        async def post(self, url, content=None, headers=None):
            order.append("brain-called")
            return await super().post(url, content=content, headers=headers)

    monkeypatch.setattr(httpx, "AsyncClient", Brain)

    async def fake_floor(msg, user_id):
        return [prf.NamedPerson("Anika Reyes", "p-anika", "people")]

    async def fake_fetch(user_id, msg, focus=None):
        return ROLELESS

    async def no_continuity(*a, **k):
        return ""

    monkeypatch.setattr(zc, "_named_person_floor", fake_floor)
    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    monkeypatch.setattr(zc, "_continuity_context_block", no_continuity)
    with caplog.at_level(logging.INFO):
        out = "".join([c async for c in zc.run_flue_brain_streaming(ASK, "s22", "demo-owner")])
    assert order.index("brain-called") < order.index("fetch-done")          # the brain did not wait for the read
    assert "floor=role lane=reply mode=shadow" in caplog.text and "mother" not in out    # collected, logged; the role-less packet still rewrites
