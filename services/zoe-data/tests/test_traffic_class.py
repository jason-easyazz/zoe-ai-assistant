"""Traffic class (P-2, deep dive 2, 2026-10-19): every turn is household | harness | probe | ci, derived
SERVER-SIDE from the principal, never from a client claim alone, and the turn records carry it.

Negative controls are the point of this file: a harness turn is never tagged household, and no client can
claim household (or, without the internal token, anything at all). Every id is invented; no DB, no network.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

pytestmark = pytest.mark.ci_safe

import traffic_class as tc

TOKEN = "unit-internal-token-0123456789"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ZOE_TRAFFIC_CLASS", raising=False)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("ZOE_PANEL_KV_MISS_TOKENS", raising=False)
    tok = tc.bind(tc.HOUSEHOLD)
    yield
    tc.reset(tok)


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("uid,expected", [
    ("demo_bar_0a1b2c3d", "harness"),
    ("bench_person_01", "harness"),
    ("zoe-hammer-1700000000", "harness"),
    ("zoe-mempalace-baseline", "harness"),
    ("probe_route_1", "probe"),
    ("ci_suite_7", "ci"),
    ("test-sec-b-9", "ci"),
    ("e2e_flow", "ci"),
])
def test_harness_ids_are_never_household(uid, expected):
    assert tc.classify(uid) == expected
    # NEGATIVE CONTROL: not even the service secret plus an explicit "household" claim can promote one
    assert tc.classify(uid, internal_token_valid=True, claimed="household") == expected
    assert tc.classify(uid, default="household") == expected


@pytest.mark.parametrize("uid", ["jason-member", "Christine", "Benchley", "zoe-touch-pi", "guest", "voice-guest",
                                 "guest-panel-1", ""])
def test_people_and_guests_are_household(uid):
    """Guest sentinels are synthetic for MEMORY passes, but a guest at the real panel is household traffic."""
    assert tc.classify(uid) == "household"


def test_a_client_cannot_claim_a_class_without_the_internal_token():
    # NEGATIVE CONTROL: the header alone (a browser, the panel, a loopback caller) is never read
    assert tc.classify("jason-member", claimed="harness") == "household"
    assert tc.classify_headers({tc.HEADER: "probe", "X-Zoe-User-Id": "demo_bar_0a1b2c3d"}) == "household"
    assert tc.classify_headers({tc.HEADER: "ci", "X-Internal-Token": "wrong"}) == "household"


def test_the_internal_token_may_name_only_a_non_household_class():
    assert tc.classify_headers({"X-Internal-Token": TOKEN, tc.HEADER: "probe"}) == "probe"
    assert tc.classify_headers({"X-Internal-Token": TOKEN, tc.HEADER: "household"}) == "household"
    assert tc.classify_headers({"X-Internal-Token": TOKEN, tc.HEADER: "nonsense"}) == "household"
    # the acting user the token authorises is classified by the pattern, and wins over any claim
    assert tc.classify_headers({"X-Internal-Token": TOKEN, "X-Zoe-User-Id": "demo_bar_0a1b2c3d",
                                tc.HEADER: "household"}) == "harness"
    # a real member forwarded by the Telegram bridge stays household
    assert tc.classify_headers({"X-Internal-Token": TOKEN, "X-Zoe-User-Id": "jason-member"}) == "household"


def test_an_unset_internal_token_never_validates(monkeypatch):
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "")
    assert tc.classify_headers({"X-Internal-Token": "", tc.HEADER: "probe"}) == "household"


def test_device_token_panel_is_resolved_server_side(monkeypatch):
    import routers.panel_auth as pa

    panels = {"tok-harness": {"panel_id": "replay-harness"}, "tok-real": {"panel_id": "zoe-touch-pi"}}
    monkeypatch.setattr(pa, "lookup_device_token", lambda raw: panels.get(raw))
    assert tc.classify_headers({"X-Device-Token": "tok-harness"}) == "probe"
    assert tc.classify_headers({"X-Device-Token": "tok-real"}) == "household"
    assert tc.classify_headers({"X-Device-Token": "unknown"}) == "household"


def test_process_default_is_for_harness_processes_only(monkeypatch):
    monkeypatch.setenv("ZOE_TRAFFIC_CLASS", "probe")
    assert tc.process_default() == "probe"
    assert tc.classify("jason-member") == "probe"   # the in-process replay runs as a real id
    monkeypatch.setenv("ZOE_TRAFFIC_CLASS", "household")
    assert tc.process_default() == "household"
    monkeypatch.setenv("ZOE_TRAFFIC_CLASS", "garbage")
    assert tc.process_default() == "household"


def test_refine_only_ever_moves_away_from_household():
    tc.bind(tc.HOUSEHOLD)
    assert tc.refine_for_user("demo_bar_0a1b2c3d") == "harness"
    tc.bind(tc.PROBE)
    assert tc.refine_for_user("jason-member") == "probe"      # never promoted back
    assert tc.refine_for_user("ci_x") == "probe"              # an established non-household class is kept


# ── the request path: middleware binds, auth refines ─────────────────────────────────────────────────────


def _app():
    from fastapi import Depends, FastAPI

    from auth import resolve_acting_user
    from middleware.logging import StructuredLoggingMiddleware

    app = FastAPI()
    app.add_middleware(StructuredLoggingMiddleware)

    @app.get("/probe-class")
    async def probe_class():
        return {"cls": tc.current()}

    @app.get("/acting")
    async def acting(user: dict = Depends(resolve_acting_user)):
        return {"cls": tc.current(), "user": user["user_id"]}

    return app


def test_middleware_binds_the_class_for_the_request(monkeypatch):
    import auth

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", TOKEN)
    from fastapi.testclient import TestClient

    c = TestClient(_app())
    assert c.get("/probe-class").json()["cls"] == "household"
    assert c.get("/probe-class", headers={"X-Internal-Token": TOKEN, tc.HEADER: "ci"}).json()["cls"] == "ci"
    assert c.get("/probe-class", headers={tc.HEADER: "ci"}).json()["cls"] == "household"   # no token: ignored
    r = c.get("/acting", headers={"X-Internal-Token": TOKEN, "X-Zoe-User-Id": "demo_bar_0a1b2c3d"}).json()
    assert r == {"cls": "harness", "user": "demo_bar_0a1b2c3d"}
    r = c.get("/acting", headers={"X-Internal-Token": TOKEN, "X-Zoe-User-Id": "jason-member"}).json()
    assert r["cls"] == "household"
    # the binding is reset after the request: the test's own context is untouched
    assert tc.current() == "household"


# ── the records carry it ─────────────────────────────────────────────────────────────────────────────────


def test_app_log_suffix_marks_non_household_lines_only():
    from logging_setup import TrafficClassFormatter

    fmt = TrafficClassFormatter("%(name)s: %(message)s")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", (), None)
    assert fmt.format(rec) == "x: hello"                  # household: byte-identical to before
    tc.bind(tc.HARNESS)
    assert fmt.format(rec) == "x: hello traffic=harness"
    rec2 = logging.LogRecord("x", logging.INFO, __file__, 1, "BRAIN_LANE a=b traffic=harness", (), None)
    assert fmt.format(rec2).count("traffic=") == 1        # never suffixed twice


def test_brain_lane_line_names_the_class(caplog):
    import brain_dispatch

    tc.bind(tc.PROBE)
    before = tc.brain_turn_counts()["probe"]
    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        brain_dispatch._log_lane(attempted="flue", served="flue", outcome="ok", session_id="replay-abc")
    assert any("BRAIN_LANE" in r.getMessage() and r.getMessage().endswith("traffic=probe") for r in caplog.records)
    assert tc.brain_turn_counts()["probe"] == before + 1


def test_router_shadow_record_names_the_class():
    import semantic_router as sr

    tc.bind(tc.HARNESS)
    rec = sr._two_stage_rec("what time is it", {"domain": "time"}, "active", "time")
    assert rec["traffic"] == "harness"
    recs = [rec, {"x": 1}, {"traffic": "household"}]
    assert sr.filter_shadow_traffic(recs, "untagged") == [{"x": 1}]
    assert sr.filter_shadow_traffic(recs, "household") == [{"traffic": "household"}]
    assert len(sr.filter_shadow_traffic(recs, "all")) == 3
    assert sr.shadow_traffic_breakdown(recs) == "traffic: harness=1 household=1 untagged=1"


def test_struggle_capture_skips_non_household_even_under_a_real_id(monkeypatch):
    import struggle_detector as sd

    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "shadow")
    sd.reset()
    try:
        async def go():
            tc.bind(tc.PROBE)
            skipped = sd.observe("voice", "turn it up", "Done.", "jason-member", "replay-1")
            tc.bind(tc.HOUSEHOLD)
            queued = sd.observe("voice", "turn it up", "Done.", "jason-member", "voice-panel-hall-0a0b0c0d")
            return skipped, queued

        skipped, queued = asyncio.run(go())
        assert skipped is False and queued is True
        assert sd.stats()["skipped_traffic"] == 1
    finally:
        sd.reset()


# ── P-3: the panel-only first_prompt_n alarm ─────────────────────────────────────────────────────────────


def test_panel_kv_alarm_warm_vs_first(caplog):
    import zoe_flue_client as zf

    zf._panel_kv_prev.clear()
    sid = "voice-panel-zoe-touch-pi-0a0b0c0d"
    with caplog.at_level(logging.WARNING, logger="zoe_flue_client"):
        assert zf._panel_kv_alarm(sid, 3400, 0, "household", now=1000.0) == "first"
        assert zf._panel_kv_alarm(sid, 40, 3300, "household", now=1030.0) is None      # a hit: no alarm
        tc.note_brain_turn("harness")
        zf.brain_slot_guard.STATS["released_quiet"] = zf.brain_slot_guard.STATS.get("released_quiet", 0) + 2
        assert zf._panel_kv_alarm(sid, 3500, 100, "household", now=1090.0) == "warm"
        assert zf._panel_kv_alarm(sid, 3500, 0, "household", now=2000.0) == "first"    # > 5 min later
    lines = [r.getMessage() for r in caplog.records if "PANEL_KV_MISS" in r.getMessage()]
    assert len(lines) == 3
    assert "kind=warm" in lines[1] and "non_household_turns_between=1" in lines[1] and "since_prev_s=60" in lines[1]
    assert "background_calls_between=2" in lines[1]


def test_panel_kv_alarm_ignores_the_harness_and_non_panel_sessions(caplog, monkeypatch):
    import zoe_flue_client as zf

    zf._panel_kv_prev.clear()
    with caplog.at_level(logging.WARNING, logger="zoe_flue_client"):
        # NEGATIVE CONTROLS: a probe turn on a panel-shaped session, a Telegram turn, threshold off
        assert zf._panel_kv_alarm("voice-panel-replay-0a0b0c0d", 4000, 0, "probe", now=1.0) is None
        assert zf._panel_kv_alarm("telegram-123", 4000, 0, "household", now=1.0) is None
        monkeypatch.setenv("ZOE_PANEL_KV_MISS_TOKENS", "0")
        assert zf._panel_kv_alarm("voice-panel-zoe-touch-pi-1", 4000, 0, "household", now=1.0) is None
    assert not [r for r in caplog.records if "PANEL_KV_MISS" in r.getMessage()]


def test_prompt_cache_line_names_the_class(caplog):
    import zoe_flue_client as zf

    zf._panel_kv_prev.clear()
    tc.bind(tc.HARNESS)
    with caplog.at_level(logging.INFO, logger="zoe_flue_client"):
        zf._log_prompt_cache("bar-pf-1", {"prompt_cache": [{"prompt_n": 3000, "cache_n": 0}]})
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("FLUE_PROMPT_CACHE session=bar-pf-1 ") and m.endswith("traffic=harness") for m in msgs)
    assert not any("PANEL_KV_MISS" in m for m in msgs)
