"""The struggles REPORT (``struggle_digest.py``) and the ``on``-mode pieces: clusters per user, example words only from
the viewer's own rows, the nightly ``STRUGGLE_SUMMARY`` line, the admin morning-brief line, the CLI, and the one-line
acknowledgement of a bare "that was wrong".

Every phrasing is invented (public repo). The store is the in-process backend; no DB, no network, no model.
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.ci_safe

import struggle_detector as sd
import struggle_digest as dg
import struggle_signals as sig

A, B = "member-a", "member-b"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for env in ("ZOE_STRUGGLE_DETECT", "ZOE_STRUGGLE_WINDOW_S", "ZOE_STRUGGLE_RETENTION_DAYS", "ZOE_STRUGGLE_QUEUE_MAX"):
        monkeypatch.delenv(env, raising=False)
    sd.reset()
    be = sd.MemoryBackend()
    sd.set_backend(be)
    yield be
    sd.reset()
    sd.set_backend(None)


def item(text, reply="Done.", *, user=A, session="chat-s1", channel="chat", ts=None, panel="", interrupted=False):
    return (time.time() if ts is None else ts, channel, text, reply, user, session, panel, interrupted)


async def feed(*turns):
    out = []
    for t in turns:
        out.extend(await sd.process(t))
    return out


def _script(name):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_one_members_words_never_reach_anothers_digest(fresh):
    await feed(item("what's the capital of chad", user=A, session="sa"), item("what's the capital of chad", user=A, session="sa"),
               item("who painted the night watch", user=B, session="sb"), item("who painted the night watch", user=B, session="sb"))
    mine = await dg.digest(viewer_id=A, user_ids=[A])
    assert mine["total"] == 1 and "chad" in " ".join(mine["clusters"][0]["examples"])
    everyone = await dg.digest(viewer_id=A, user_ids=None)
    assert everyone["total"] == 2
    examples = " ".join(x for c in everyone["clusters"] for x in c["examples"])
    assert "chad" in examples and "night watch" not in examples, "B's words leaked into A's report"
    theirs = await dg.digest(viewer_id=B, user_ids=[B])
    assert "chad" not in str(theirs)


async def test_the_digest_never_shows_guest_words_even_to_an_admin(fresh):
    await feed(item("what's the capital of chad", user="guest", session="g"), item("what's the capital of chad", user="guest", session="g"))
    rep = await dg.digest(viewer_id="guest", user_ids=None)
    assert rep["total"] == 1 and rep["clusters"][0]["examples"] == []


async def test_digest_clusters_by_signal_domain_and_shape(fresh):
    for i, s in enumerate(("sa", "sb", "sc")):
        text = "how warm is it going to get today"
        sd.note_route(text, {"domain": "weather", "score": 0.7}, two_stage_active=True, two_stage_failed=True)
        sd.note_brain(s, "flue", "ok")
        await feed(item(text, "About twenty.", session=s))
    rep = await dg.digest(viewer_id=A, user_ids=[A])
    c = rep["clusters"][0]
    assert (c["signal"], c["domain"], c["pattern"], c["count"]) == ("FALLTHROUGH", "weather", "weather|how:future:today", 3)
    assert c["suggested_fix"] == sig.FIX_ROUTER_LATENCY and c["fix_hint"] and c["users"] == 1
    assert c["first_seen"] <= c["last_seen"] and c["paths"] == {"brain:flue": 3}
    line = dg.summary_line(rep)
    assert line.startswith("STRUGGLE_SUMMARY ") and "FALLTHROUGH:3" in line and "warm" not in line


async def test_nightly_summary_line(caplog, fresh):
    import logging

    caplog.set_level(logging.INFO, logger="struggle_detector")
    await feed(item("what's the capital of chad"), item("what's the capital of chad"))
    line = await dg.log_nightly_summary()
    assert line and "RE_ASK:1" in line and "chad" not in line and line in caplog.text


async def test_brief_line_is_on_only_and_owner_scoped(monkeypatch, fresh):
    await feed(item("what's the capital of chad"), item("what's the capital of chad"),
               item("who painted that", user=B, session="sb"), item("who painted that", user=B, session="sb"))
    assert await dg.brief_line(A) is None                        # shadow: nothing user-facing
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "on")
    line = await dg.brief_line(A)
    assert line and "1 moment" in line and "chad" not in line


def test_ack_line_modes(monkeypatch):
    assert sd.ack_line("that was wrong") is None
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "on")
    assert sd.ack_line("that was wrong") == sd.ACK_LINE
    assert sd.ack_line("zoe, that's not right") == sd.ACK_LINE
    assert sd.ack_line("that's wrong, it's Thursday") is None     # a correction carries a value to act on
    assert sd.ack_line("good answer") is None
    assert sd.ack_line("what's the weather") is None


async def test_fast_tiers_acks_only_when_on_and_nothing_else_answered(monkeypatch):
    import fast_tiers

    async def none(*_a, **_k):
        return None

    for name in ("_distress_tier", "_feedback_tier", "_provenance_tier"):
        monkeypatch.setattr(fast_tiers, name, none)
    monkeypatch.setattr(fast_tiers, "_resolve_core", none)
    assert await fast_tiers.resolve("that was wrong", A, "s1", channel="voice") is None          # shadow
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "on")
    res = await fast_tiers.resolve("that was wrong", A, "s1", channel="voice")
    assert res is not None and res.reply == sd.ACK_LINE and res.tier == "struggle_ack"
    assert await fast_tiers.resolve("that was wrong", A, "s1", channel="voice", phase="domain") is None
    assert await fast_tiers.resolve("what's the weather", A, "s1", channel="voice") is None


async def test_morning_brief_carries_the_line_for_an_admin_only_when_on(monkeypatch):
    from proactive.triggers import morning_checkin as mc

    ctx = {"struggle_line": "Yesterday I noticed 2 moments where I struggled; the struggles report has details."}
    assert ctx["struggle_line"] in mc._compose_morning_message(ctx, "", "Monday")
    assert "struggled" not in mc._compose_morning_message({}, "", "Monday")


def test_report_cli_renders_counts_and_only_the_named_members_words(monkeypatch, fresh, capsys):
    import db_pool

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(db_pool, "init_pool", nothing)
    monkeypatch.setattr(db_pool, "close_pool", nothing)
    asyncio.run(feed(item("what's the capital of chad", user=A, session="sa"), item("what's the capital of chad", user=A, session="sa"),
                     item("who painted that", user=B, session="sb"), item("who painted that", user=B, session="sb")))
    cli = _script("struggle_report.py")
    assert cli.main(["--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "2 event(s)" in out and "RE_ASK" in out and "chad" not in out and "painted" not in out
    assert cli.main(["--days", "1", "--user", A]) == 0
    out = capsys.readouterr().out
    assert "1 event(s)" in out and "chad" in out and "painted" not in out


def test_report_cli_exit_2_without_a_database(monkeypatch):
    import db_pool

    async def down(*_a, **_k):
        raise ConnectionError("no db")

    monkeypatch.setattr(db_pool, "init_pool", down)
    assert _script("struggle_report.py").main([]) == 2


# Invented log lines in the app log's exact formats (no household words): a forecast question answered with the CURRENT
# reading by the fast path, a rephrase the two-stage router timed out on and the brain answered, then a misheard repeat.


def test_main_registers_the_report_router():
    """The endpoint is reachable in the real app: ``main.py`` includes the router (a source check - importing ``main`` boots
    the whole service)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "from routers.struggles import router as struggles_router" in src
    assert "app.include_router(struggles_router)" in src



async def test_a_report_that_hits_the_row_limit_says_so(monkeypatch, fresh):
    monkeypatch.setattr(dg, "ROW_LIMIT", 2)
    now = time.time()
    fresh.data = [{"id": str(i), "ts": now - i, "user_id": A, "signal": "RE_ASK", "summary": "", "is_guest": 0}
                  for i in range(5)]
    rep = await dg.digest(viewer_id=A, user_ids=[A])
    assert rep["total"] == 2 and rep["truncated"] is True
    assert "total=2+" in dg.summary_line(rep)
