"""ZOE_IDENTITY_BLOCK (default ON) — the brain is told who it is talking to.

Live 2026-10-05: "who is the prime minister" was answered correctly, then "How is your time
in Australia going?" — a visitor assumption, because nothing in the brain's context said
this is a household member at home. The seam now adds ONE line, built from the ACCOUNT, right
behind the identity envelope. These tests pin the wire bytes: the line and its position,
flag-off bytes identical to a build without the feature, and no line for ids that are not
registered accounts (guests, harness users) — so the replay gate and bar harnesses are
unchanged by construction. Synthetic names/places only (ci_safe)."""
from __future__ import annotations

import asyncio
import json

import pytest

import identity_facts as idf
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

LINE = "You are talking to Zed, a member of this household in Hobart, Tasmania, Australia."
IDENT = idf.build_identity("jason", username="zed", users_name="zed",
                           sysloc={"city": "Hobart", "country": "AU", "timezone": "Australia/Hobart"})


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": "ok"}}


def _client(captured):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return _Resp()
    return _C


@pytest.fixture
def sent(monkeypatch):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    for k in ("ZOE_STRIP_NARRATION", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE",
              "ZOE_TRIVIA_HEDGE", "ZOE_IDENTITY_BLOCK"):
        monkeypatch.delenv(k, raising=False)
    idf.clear_cache()

    async def _res(uid, db=None):
        return IDENT if uid == "jason" else None

    monkeypatch.setattr(idf, "resolve_identity", _res)

    def turn(message, uid="jason"):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _client(cap))

        async def go():
            return [c async for c in zc.run_flue_brain_streaming(message, "s1", uid)]
        asyncio.run(go())
        return json.loads(cap["content"])["message"]
    return turn


def test_default_on_one_line_right_after_the_identity_envelope(sent):
    assert sent("Hello there") == f" zoe-uid:jason\n{LINE}\nHello there"


def test_flag_off_is_byte_identical_to_a_build_without_the_feature(sent, monkeypatch):
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    assert sent("Hello there") == " zoe-uid:jason\nHello there"


@pytest.mark.parametrize("uid", ["guest", "voice-guest", "bar-demo-user", ""])
def test_not_a_registered_account_means_no_line(sent, uid):
    out = sent("Hello there", uid=uid)
    assert "You are talking to" not in out
    assert out == (f" zoe-uid:{uid}\nHello there" if uid else "Hello there")


def test_deterministic_block_order_identity_then_recall_then_words(sent, monkeypatch):
    async def recall(message, uid):
        return "[MEMORY CONTEXT]\n- a fact\n[END MEMORY CONTEXT]"

    monkeypatch.setattr(zc, "_recall_context_block", recall)
    out = sent("Where do I keep the keys")
    assert out.index(LINE) < out.index("[MEMORY CONTEXT]") < out.index("Where do I keep the keys")
    assert out.startswith(" zoe-uid:jason\n" + LINE + "\n[MEMORY CONTEXT]")


def test_a_failing_identity_lookup_never_breaks_the_turn(sent, monkeypatch):
    async def boom(uid, db=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(idf, "resolve_identity", boom)
    assert sent("Hello there") == " zoe-uid:jason\nHello there"


def test_the_line_names_no_pii_beyond_name_and_household_place():
    assert idf.identity_line(IDENT) == LINE
    no_place = idf.build_identity("u", username="zed", sysloc={})
    assert idf.identity_line(no_place) == "You are talking to Zed, a member of this household."


def test_the_line_is_not_a_registered_context_block():
    """Deliberately a plain line, not a bracketed block: _FLUE_CONTEXT_BLOCKS is pinned equal
    to the sidecar's context-blocks.ts, and a new bracket type would need a sidecar change."""
    assert not any(LINE.startswith(open_) for open_, _ in zc._FLUE_CONTEXT_BLOCKS)
