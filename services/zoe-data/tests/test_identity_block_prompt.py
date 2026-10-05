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
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import identity_facts as idf
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

LAB = Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x"
LINE = "You are talking to Zed, a member of this household in Hobart, Tasmania, Australia."
BLOCK = f"[Today — household context]\n{LINE}\n[END Today]"
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

    async def _res(uid, db=None, budget_s=None):
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
    assert sent("Hello there") == f" zoe-uid:jason\n{BLOCK}\nHello there"


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
    assert out.startswith(" zoe-uid:jason\n" + BLOCK + "\n[MEMORY CONTEXT]")


def test_a_failing_identity_lookup_never_breaks_the_turn(sent, monkeypatch):
    async def boom(uid, db=None, budget_s=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(idf, "resolve_identity", boom)
    assert sent("Hello there") == " zoe-uid:jason\nHello there"


def test_the_line_names_no_pii_beyond_name_and_household_place():
    assert idf.identity_line(IDENT) == LINE
    no_place = idf.build_identity("u", username="zed", sysloc={})
    assert idf.identity_line(no_place) == "You are talking to Zed, a member of this household."


def test_the_block_rides_an_existing_pinned_family_so_no_sidecar_change_is_needed():
    open_prefix, close = next((o, c) for o, c in zc._FLUE_CONTEXT_BLOCKS if idf.BLOCK_OPEN.startswith(o + " "))
    assert idf.BLOCK_OPEN.endswith("]") and idf.BLOCK_CLOSE == close == "[END Today]"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_real_sidecar_elides_stale_copies_and_keeps_the_newest():
    """Runs the sidecar's own ``elideStaleBlocks`` (context-blocks.ts) over user messages as
    the seam builds them: older turns lose the identity block (and a day-brief block beside
    it), the newest keeps it - so history costs ~0 tokens per old turn."""
    brief = "[Today 2026-10-05]\nbrief\n[END Today]"
    msgs = [{"role": "user", "content": f"{BLOCK}\nfirst question\n{brief}"},
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": f"{BLOCK}\nsecond question"},
            {"role": "assistant", "content": "another answer"},
            {"role": "user", "content": f"{BLOCK}\nthird question"}]
    script = ("const m=JSON.parse(await new Promise(r=>{let d='';process.stdin.on('data',c=>d+=c)"
              ".on('end',()=>r(d))}));const {elideStaleBlocks}=await import(%r);"
              "console.log(JSON.stringify(elideStaleBlocks(m)))" % (LAB / "src/context-blocks.ts").as_uri())
    out = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                         input=json.dumps(msgs), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-400:]
    got = [m["content"] for m in json.loads(out.stdout) if m["role"] == "user"]
    assert got == ["first question", "second question", f"{BLOCK}\nthird question"]


@pytest.mark.parametrize("city,kept", [("Cold Lake", False), ("Hot Springs", False), ("Rainier", True),
                                       ("Windsor", True), ("Hobart", True), ("Music City", False)])
def test_a_place_that_would_arm_a_tool_group_is_left_out_but_the_name_stays(city, kept):
    """tool-groups.ts keyword-matches the whole user message (blocks included) every turn;
    a city called "Cold Lake" would arm the weather group on every turn of the session."""
    ident = idf.build_identity("u", username="zed", sysloc={"city": city, "country": "CA"})
    line = idf.identity_line(ident)
    assert line.startswith("You are talking to Zed, a member of this household")
    assert (city in line) is kept


def test_trigger_guard_covers_every_weather_word_the_sidecar_matches():
    """Drift pin: the plain-word weather alternation parsed out of tool-groups.ts."""
    src = (LAB / "src/tools/tool-groups.ts").read_text()
    words = re.search(r"weather:\s*/\\b\(([^)]*)\)\\b/i", src).group(1).split("|")
    assert len(words) >= 10
    for w in words:
        assert idf._TRIGGER_RE.search(f"{w.title()} Falls"), w


def test_a_cold_cache_never_stalls_the_first_token(sent, monkeypatch):
    import time

    async def slow(uid, db=None, budget_s=None):
        assert budget_s is not None and budget_s <= 0.5  # the prompt path bounds its wait
        return None

    monkeypatch.setattr(idf, "resolve_identity", slow)
    t0 = time.monotonic()
    assert sent("Hello there") == " zoe-uid:jason\nHello there"
    assert time.monotonic() - t0 < 1.0
