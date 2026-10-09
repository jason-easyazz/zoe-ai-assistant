"""The off-path yes/no verifier (``structural_verifier``): bounded, post-turn, shadow-only, never on the voice path.

The 4B holds the brain's single slot for ~280 ms per call, so the caller is bounded and the verdict is only LOGGED: nothing here (and
nothing in the digest) lets a verdict change a row. The live endpoint is never reached: ``httpx.AsyncClient`` is stubbed.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import structural_claims as sc
import structural_verifier as sv

pytestmark = pytest.mark.ci_safe

ITEM = sv.Item(fact="User lives in Hobart", quote="I might move to Hobart", lexical="hold", structural="promote", lang="en",
               ambiguous=("modality_marker_vs_asserted",), said="I might move to Hobart.")


class Client:
    calls: list = []
    answer = "no"
    fail = False

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **k):
        type(self).calls.append((url, json))
        if type(self).fail:
            raise RuntimeError("brain busy")

        class R:
            def raise_for_status(self):
                pass

            def json(_self):
                return {"choices": [{"message": {"content": type(self).answer}}]}

        return R()


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(Client, "calls", [])
    monkeypatch.setattr(Client, "answer", "no")
    monkeypatch.setattr(Client, "fail", False)
    monkeypatch.setenv(sv.ENV, "shadow")
    monkeypatch.setenv(sc.ENV, "shadow")
    sv.reset()
    yield
    sv.reset()


def run(coro):
    return asyncio.run(coro)


def test_the_request_is_a_constrained_two_token_greedy_judge():
    got = run(sv.judge("User lives in Hobart", "I might move to Hobart.", url="http://brain.invalid"))
    assert got == "no"
    url, body = Client.calls[0]
    assert url == "http://brain.invalid/v1/chat/completions"
    assert body["grammar"] == 'root ::= "yes" | "no"' and body["max_tokens"] == 2 and body["temperature"] == 0 and body["stream"] is False
    assert "I might move to Hobart." in body["messages"][1]["content"]


def test_an_unparseable_answer_is_none_not_a_guess():
    Client.answer = "maybe"
    assert run(sv.judge("a fact", "a sentence", url="http://x")) is None


def test_post_turn_is_bounded_per_turn_and_logs_labels_only(caplog):
    items = [ITEM, ITEM, ITEM]
    with caplog.at_level(logging.INFO):
        out = run(sv.verify_post_turn(items, user_id="u"))
    assert len(out) == sv.MAX_PER_TURN == len(Client.calls)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("STRUCTURAL_VERIFY"))
    assert "verdict=no lexical=hold structural=promote agrees_structural=0 lang=en" in line
    assert "Hobart" not in line                                              # labels and counts only


def test_off_by_either_switch_makes_no_call(monkeypatch):
    monkeypatch.setenv(sv.ENV, "off")
    assert run(sv.verify_post_turn([ITEM])) == [] and not Client.calls
    monkeypatch.setenv(sv.ENV, "shadow")
    monkeypatch.setenv(sc.ENV, "off")
    assert run(sv.verify_post_turn([ITEM])) == [] and not Client.calls
    assert run(sv.verify_post_turn([])) == []


def test_the_hourly_budget_stops_the_calls(monkeypatch):
    monkeypatch.setattr(sv, "MAX_PER_HOUR", 3)
    for _ in range(4):
        run(sv.verify_post_turn([ITEM]))
    assert len(Client.calls) == 3 and sv.STATS["skipped_budget"] == 1


def test_a_failing_brain_opens_the_breaker_and_never_raises(caplog):
    Client.fail = True
    with caplog.at_level(logging.INFO):
        for _ in range(sv.BREAKER_AFTER):
            out = run(sv.verify_post_turn([ITEM]))
            assert out == [(ITEM, None)]
    assert sv.breaker_open()
    n = len(Client.calls)
    assert run(sv.verify_post_turn([ITEM])) == [] and len(Client.calls) == n         # open: not asked again
    assert "breaker open" in caplog.text and "verdict=error" in caplog.text


def test_the_verifier_is_not_reachable_from_the_voice_path():
    """Structural: the voice-path modules never import it (it is awaited only from the post-turn digest)."""
    root = Path(__file__).resolve().parent.parent
    for name in ("zoe_flue_client.py", "fast_tiers.py", "routers/voice_tts.py", "zoe_core_client.py", "brain_dispatch.py", "voice_speculation.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "structural_verifier" not in src, name
    digest = (root / "memory_digest.py").read_text(encoding="utf-8")
    assert digest.count("structural_verifier.verify_post_turn") == 1 and "async def _structural_post" in digest
