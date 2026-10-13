"""The MCP ``memory_forget`` tool is ROW-LEVEL by design and says so, so an agent cannot use it to "forget a person".

``memory_forget_entity`` (the spoken forget) archives the rows, erases the verbatim copies, redacts transcripts and runs the derived-store
cascade (portrait, card, loops, contacts, mutes, commitments, recordings), and answers "forgotten" only when every store was cleared.
The MCP tool rejects ONE row by id and runs none of that. It stays that way on purpose - an entity forget is physically irreversible
and must only ever come from the member's own words, never a model's call - but then it must not read as a forget:

  * its description says row-level only, and not a way to forget a person;
  * its answer says ``scope: row`` and ``cascade: not_run`` (nothing for a model to repeat as "forgotten");
  * a note that describes a person (``memory_type`` / ``entity_type`` = person) is refused, with the way to do it properly;
  * no MCP tool offers an entity forget.
"""
from __future__ import annotations

import types

import pytest

import mcp_server

pytestmark = pytest.mark.ci_safe

USER = "demo_mcp_00000001"


class _Svc:
    def __init__(self, md):
        self.md = md
        self.reviews = []

    async def get(self, mem_id):
        return None if mem_id == "gone" else types.SimpleNamespace(id=mem_id, metadata=self.md)

    async def review(self, mem_id, **kw):
        self.reviews.append((mem_id, kw))
        return types.SimpleNamespace(id=mem_id)


@pytest.fixture
def run(monkeypatch):
    async def actor(_db, _name, _args, _ctx):
        return dict(user_id=USER)
    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", actor)

    def go(md, mem_id="m1"):
        import memory_service
        svc = _Svc(md)
        monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
        import asyncio
        out = asyncio.run(mcp_server._execute_tool(None, "memory_forget", dict(memory_id=mem_id)))
        return out, svc
    return go


def _tool(name):
    return next(t for t in mcp_server.TOOLS if t["name"] == name)


def test_the_description_says_row_level_only_and_not_a_way_to_forget_a_person():
    desc = _tool("memory_forget")["description"].lower()
    assert "row-level only" in desc
    assert "does not forget a person" in desc
    assert "never use it to forget a person" in desc
    assert "never tell the member something is forgotten" in desc
    assert "forget dana" in desc                      # the way to do it properly is named


def test_no_mcp_tool_offers_an_entity_forget():
    forgets = [t["name"] for t in mcp_server.TOOLS if "forget" in t["name"]]
    assert forgets == ["memory_forget"]                # no memory_forget_entity: that stays the member's own words


def test_a_plain_row_is_rejected_and_the_answer_says_it_was_only_a_row(run, monkeypatch):
    import memory_forget_cascade

    async def boom(*a, **k):
        raise AssertionError("the row-level tool must not run the entity cascade")
    monkeypatch.setattr(memory_forget_cascade, "cascade_forget", boom)
    out, svc = run(dict(user_id=USER, memory_type="fact"))
    assert out["status"] == "rejected" and out["scope"] == "row" and out["cascade"] == "not_run"
    assert "did not forget any person or topic" in out["note"]
    assert len(svc.reviews) == 1 and svc.reviews[0][1]["decision"] == "reject" and svc.reviews[0][1]["origin"] == "mcp"


@pytest.mark.parametrize("md", [
    dict(user_id=USER, memory_type="person"),
    dict(user_id=USER, memory_type="fact", entity_type="person"),
    dict(user_id=USER, memory_type="fact", entity_type="Person"),
])
def test_a_note_that_describes_a_person_is_refused_not_hidden(run, md):
    out, svc = run(md)
    assert "refused" in out["error"] and "forget <name>" in out["error"]
    assert out["scope"] == "row" and out["cascade"] == "not_run"
    assert svc.reviews == []                           # nothing was hidden: the refusal is before any write


def test_a_non_person_entity_is_still_a_plain_row_the_control(run):
    out, svc = run(dict(user_id=USER, memory_type="fact", entity_type="place"))
    assert out["status"] == "rejected" and len(svc.reviews) == 1


def test_another_members_row_and_a_missing_row_behave_as_before(run):
    out, svc = run(dict(user_id="someone_else"))
    assert out["error"].startswith("forbidden") and svc.reviews == []
    out, svc = run(dict(user_id=USER), mem_id="gone")
    assert out == dict(status="not_found")
