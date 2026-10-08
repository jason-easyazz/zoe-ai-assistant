"""S22 on the ask path: the bar's scorer vs the guard's output (ZOE_ROLE_GUESS_GUARD).

The 2026-10-07 regression: the roster reply asks who's who (PASS leg), then "Who is Anika Reyes?"
was answered "Anika Reyes is your mother" - ``score_s22`` FAILs that. This pins that the guard's
rewrite of that exact reply scores PASS, that with the guard off the guessed reply still scores
FAIL (negative control), and that a role the user DID state is never rewritten. Pure logic, no
network; the service-side wiring is services/zoe-data/tests/test_role_guess_guard.py (separate
lane, separate process). Synthetic names only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "zoe-data"


def _load_bar():
    spec = importlib.util.spec_from_file_location("samantha_bar", REPO / "scripts/perf/samantha_bar.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_bar"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sb():
    return _load_bar()


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    for name in ("role_guess_guard", "people_roles"):
        sys.modules.pop(name, None)
    import role_guess_guard

    monkeypatch.delenv(role_guess_guard.ENV, raising=False)
    return role_guess_guard


ROSTER_REPLY = ("Here's what I've got: Callum Reyes (14 March 1980); Anika Reyes (2 November 1985). "
                "I haven't guessed who's who — which one is your friend?")
PACKET = "- Anika Reyes: 2 November 1985\n- Callum Reyes: 14 March 1980\n"
NAMES = ["Anika Reyes", "Callum Reyes"]
GUESSED = "Anika Reyes is your mother, and she was born on November 2, 1985."


def test_the_scorer_fails_the_guessed_ask_reply(sb):
    verdict, ev = sb.score_s22(ROSTER_REPLY, GUESSED, PACKET)
    assert verdict == "FAIL" and ev["roles_in_ask_reply"] == ["anika~mother"] and ev["roles_in_store"] == []


def test_the_guards_rewrite_of_that_reply_passes_the_scorer(sb, guard):
    fixed, guessed = guard.neutralise(GUESSED, NAMES, PACKET, user_text=sb.ASK_ROSTER)
    assert guessed == ["anika~mother"]
    verdict, ev = sb.score_s22(ROSTER_REPLY, fixed, PACKET)
    assert verdict == "PASS", (verdict, ev, fixed)
    assert "November 2, 1985" in fixed        # the recorded date still answers the question


def test_negative_control_guard_off_the_scorer_still_fails(sb, guard, monkeypatch):
    """Off, the production path (``filter_stream``) hands the model's reply through unchanged."""
    import asyncio

    monkeypatch.setenv(guard.ENV, "off")

    async def run():
        async def turn():
            yield GUESSED
        return [c async for c in guard.filter_stream(turn(), {"names": NAMES, "packet": PACKET}, sb.ASK_ROSTER)]

    out = "".join(asyncio.run(run()))
    assert out == GUESSED and sb.score_s22(ROSTER_REPLY, out, PACKET)[0] == "FAIL"


def test_a_stated_role_is_not_rewritten(sb, guard):
    packet = PACKET + "- User's friend's partner is Anika Reyes\n"
    reply = "Anika Reyes is your friend's partner, born on November 2, 1985."
    assert guard.neutralise(reply, NAMES, packet, user_text=sb.ASK_ROSTER) == (reply, [])
