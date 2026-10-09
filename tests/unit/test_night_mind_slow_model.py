"""A slow model is a slow model, not a dead one: the night mind's per-call HTTP budget (2026-10-09, the first real 12B run).

The 12B measured prefill 136 tok/s and decode 3.62 tok/s; the K cells were built with the 4B's constants (prefill 650, decode 8.0 - ``--decode-tok-s`` never
reached the lab arm) and 8 of 10 cells came back ERROR on ReadTimeout, with ``totals.calls == 0``. These tests run a loopback server that holds each answer for as
long as a slow model would, and prove: one formula (prompt/prefill + max/decode + margin, floored), both rates reach every call (the pass, the cells arm, K12), a
timeout names its budget, the counters survive an abandoned night and are aggregated in ``--cells``.

RED-BEFORE-GREEN: with the prefill rate ignored (the old formula) the slow-prefill call times out; with the rates not forwarded to the arm the budget is the 4B's.
Times are scaled down (a 'slow model' here answers in ~2 s, not 100 s) by shrinking the margin and floor, never by changing the formula.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import night_mind as nm  # noqa: E402
from zmb.arms.z0 import Z0Arm  # noqa: E402
from zmb.night_brain import FakeNightBrain, FakeNightServer  # noqa: E402

#: the first real 12B run, 2026-10-09
PREFILL_12B, DECODE_12B = 136.0, 3.62


def run(coro):
    return asyncio.run(coro)


# ── the formula ───────────────────────────────────────────────────────────────

def old_formula(prompt_tokens: int, max_tokens: int, decode_tok_s: float) -> float:
    """What shipped: the prefill rate a constant 650, no floor."""
    return round(prompt_tokens / 650.0 + max_tokens / max(decode_tok_s, 0.5) + 20.0, 1)


def test_the_budget_is_prompt_over_prefill_plus_output_over_decode_plus_margin_floored():
    assert nm.timeout_for(5000, 640, DECODE_12B, PREFILL_12B) == pytest.approx(5000 / 136 + 640 / 3.62 + 20, abs=0.1)
    assert nm.timeout_for(100, 10, 60.0, 650.0) == nm.TIMEOUT_FLOOR_S                    # a fast model on a tiny call still gets the floor
    assert nm.timeout_for(2800, 450, 8.0) == nm.timeout_for(2800, 450, 8.0, nm.PREFILL_TOK_S)             # the 4B's old default is unchanged
    assert nm.timeout_for(4000, 640, 3.62, 136.0) > nm.timeout_for(4000, 640, 3.62, 650.0) + 20           # a slower prefill LENGTHENS the budget


def test_a_12b_answer_the_old_formula_would_cut_off_gets_its_full_budget():
    """The member pass had the right decode rate (3.62) and still lost calls: a 5,000-token prompt at 136 tok/s spends 37 s on prefill, 7 s were budgeted."""
    prompt, cap = 5000, nm.MOMENT_MAX_TOKENS
    takes = prompt / PREFILL_12B + cap / DECODE_12B                                       # the slowest honest answer: a full-cap reply
    assert old_formula(prompt, cap, DECODE_12B) < takes < nm.timeout_for(prompt, cap, DECODE_12B, PREFILL_12B)


def test_the_cells_default_rates_cut_a_12b_answer_off_and_the_measured_ones_do_not():
    """The K cells never received ``--decode-tok-s``: the arm's Config used 8.0, so a 400-token answer that takes 110 s at 3.62 got a ~100 s budget."""
    prompt, cap = 3000, nm.MOMENT_MAX_TOKENS
    takes = prompt / PREFILL_12B + 400 / DECODE_12B
    assert 100 < takes < 140
    assert nm.timeout_for(prompt, cap, 8.0) < takes                                       # the 4B's rates: ERROR on ReadTimeout
    assert nm.timeout_for(prompt, cap, DECODE_12B, PREFILL_12B) > takes                   # the measured ones: the answer lands


def test_the_rates_come_from_the_cli_or_the_env_and_the_cli_wins(monkeypatch):
    for k in (nm.DECODE_ENV, nm.PREFILL_ENV):
        monkeypatch.delenv(k, raising=False)
    cfg = nm.config_from_env(url="http://127.0.0.1:1")
    assert (cfg.decode_tok_s, cfg.prefill_tok_s) == (nm.DEFAULT_DECODE_TOK_S, nm.PREFILL_TOK_S)
    monkeypatch.setenv(nm.DECODE_ENV, "3.62")
    monkeypatch.setenv(nm.PREFILL_ENV, "136")
    cfg = nm.config_from_env(url="http://127.0.0.1:1")
    assert (cfg.decode_tok_s, cfg.prefill_tok_s) == (3.62, 136.0) and cfg.timeout_for(5000, 640) == nm.timeout_for(5000, 640, 3.62, 136.0)
    cfg = nm.config_from_env(url="http://127.0.0.1:1", decode_tok_s=5.0, prefill_tok_s=200.0)
    assert (cfg.decode_tok_s, cfg.prefill_tok_s) == (5.0, 200.0)


def test_the_decode_safety_net_takes_the_configured_prefill_off_not_650():
    cfg = nm.Config(decode_tok_s=8.0, prefill_tok_s=136.0)
    nm.observe_decode_rate(cfg, 3000, 300, 3000 / 136.0 + 100.0)                          # 300 tokens in 100 s of pure decode = 3 tok/s
    assert cfg.decode_tok_s == pytest.approx(2.7, abs=0.05)
    nm.observe_decode_rate(cfg, 3000, 300, 1.0)                                           # a fast call afterwards never shortens it
    assert cfg.decode_tok_s == pytest.approx(2.7, abs=0.05)


# ── the HTTP path against a server that is slow the way the 12B is ────────────

@pytest.fixture
def scaled(monkeypatch):
    """Shrink the margin and the floor, nothing else: the same formula at a test-sized scale."""
    monkeypatch.setattr(nm, "TIMEOUT_MARGIN_S", 0.3)
    monkeypatch.setattr(nm, "TIMEOUT_FLOOR_S", 0.0)


#: the 'model': 200 tok/s prefill, 200 tok/s decode, holds the answer for what that costs
TRUE_PREFILL, TRUE_DECODE = 200.0, 200.0
MESSAGES = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": "w" * 1300}]


def slow_server() -> FakeNightServer:
    srv = FakeNightServer(FakeNightBrain())
    srv.delay_s = lambda body: (sum(len(str(m.get("content", ""))) for m in body["messages"]) // 4) / TRUE_PREFILL + int(body["max_tokens"]) / TRUE_DECODE
    return srv


def call(cfg: nm.Config, max_tokens: int = 64) -> str:
    return run(nm._complete(MESSAGES, max_tokens, cfg, {"prompt_tokens": 0, "completion_tokens": 0}))


def test_a_slow_answer_succeeds_with_the_measured_rates_and_the_old_formula_times_out(scaled, caplog):
    srv = slow_server()
    try:
        cfg = nm.Config(url=srv.url[:-3], model="12B", decode_tok_s=TRUE_DECODE, prefill_tok_s=TRUE_PREFILL)
        assert call(cfg) is not None                                                      # ~2.1 s answer inside a ~2.7 s budget
        old = nm.Config(url=srv.url[:-3], model="12B", decode_tok_s=TRUE_DECODE, prefill_tok_s=nm.PREFILL_TOK_S)       # the prefill constant of the old formula
        with pytest.raises(nm.ModelTimeout) as ei:
            call(old)                                                                     # the same answer in a ~1.3 s budget
        assert ei.value.budget_s == old.timeout_for(nm.est_tokens(MESSAGES[0]["content"]) + nm.est_tokens(MESSAGES[1]["content"]), 64)
        assert "budget_s=" in str(ei.value) and "650 tok/s" in str(ei.value) and "200 tok/s" in str(ei.value)
    finally:
        srv.close()


def test_a_timed_out_night_logs_llm_timeout_with_the_budget_and_still_reports_its_counters(scaled, caplog, monkeypatch):
    srv = slow_server()
    try:
        cfg = nm.Config(url=srv.url[:-3], model="12B", ctx_tokens=8192, decode_tok_s=TRUE_DECODE, prefill_tok_s=nm.PREFILL_TOK_S)      # a prefill the model does not have
        turns = [{"id": f"t{i}", "text": "Tamsin got the offer from Pinecrest Mills and I am so glad for her, I cried a little. " * 6, "at": "2026-10-08T01:00:00+00:00"} for i in range(40)]
        monkeypatch.setattr(nm, "ModelTimeout", nm.ModelTimeout)
        import memory_digest
        tr = memory_digest.Transcript("\n".join(t["text"] for t in turns), [(t["id"], t["text"]) for t in turns], [t["at"] for t in turns])
        with caplog.at_level(logging.WARNING, logger=nm.logger.name):
            res = run(nm.run_for_user("demo_bar_00000001", tr, None, force_mode="shadow", cfg=cfg))
        assert res["status"] == "llm_unreachable" and res["llm_timeout"] is True and res["timeout_budget_s"] > 0
        assert res["calls"] >= 1 and res["prompt_tokens"] >= 0                            # the failed call counts: never the 0 the cells reported
        line = next(r.getMessage() for r in caplog.records if "status=llm_timeout" in r.getMessage())
        assert f"budget_s={res['timeout_budget_s']}" in line and "decode_tok_s=200" in line and "prefill_tok_s=650" in line
    finally:
        srv.close()


# ── the lab arm (the K cells) gets the same rates, in the pass and in K12 ─────

def test_the_cells_arm_builds_every_config_from_the_measured_rates():
    arm = Z0Arm(name="Z0n", night=True, night_url="http://127.0.0.1:1", night_model="12B", night_ctx=8192,
                night_decode_tok_s=DECODE_12B, night_prefill_tok_s=PREFILL_12B)
    try:
        cfg = arm._night_cfg()
        assert (cfg.decode_tok_s, cfg.prefill_tok_s) == (DECODE_12B, PREFILL_12B)
        assert arm._night_cfg(chunk_tokens=400, max_calls=7).timeout_for(3000, 640) == nm.timeout_for(3000, 640, DECODE_12B, PREFILL_12B)
        bare = Z0Arm(name="Z0n", night=True, night_url="http://127.0.0.1:1")
        try:
            assert bare._night_cfg().prefill_tok_s == nm.PREFILL_TOK_S                    # no rates given: the env / module default, as before
        finally:
            bare.close()
    finally:
        arm.close()


def _k1(arm):
    from zmb import cells as cellmod, spec, world
    w = world.make_world("zmb-v1")
    cell = next(c for c in spec.load_cells() if c.axis == "reflection" and c.id.startswith("K1."))
    return cellmod.run_cell(cell.rendered(w), w, arm)


def test_a_k_cell_on_a_slow_model_is_an_error_naming_the_budget_and_passes_once_the_rates_are_measured(scaled):
    """The cell-level proof: the same slow server, the arm built without the measured prefill (ERROR + reason), then with it (the cell runs to a verdict)."""
    srv = slow_server()
    try:
        bad = Z0Arm(name="Z0n", night=True, night_url=srv.url[:-3], night_model="12B", night_ctx=8192, night_chunk_tokens=400,
                    night_decode_tok_s=TRUE_DECODE)                                       # prefill left at the 4B's 650
        try:
            o = _k1(bad)
            assert o.verdict == "ERROR" and "llm_timeout" in o.reason and "budget_s=" in o.reason and "650 tok/s" in o.reason
            assert bad.night_totals["calls"] >= 1                                         # the calls it spent are counted even though the night was abandoned
        finally:
            bad.close()
        good = Z0Arm(name="Z0n", night=True, night_url=srv.url[:-3], night_model="12B", night_ctx=8192, night_chunk_tokens=400,
                     night_decode_tok_s=TRUE_DECODE, night_prefill_tok_s=TRUE_PREFILL)
        try:
            o = _k1(good)
            assert o.verdict != "ERROR", o.reason
            assert good.night_totals["calls"] >= 2 and good.night_totals["completion_tokens"] > 0
        finally:
            good.close()
    finally:
        srv.close()


def _run_py(args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "PYTHONPATH", "TZ", "LANG")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, timeout=240, cwd=str(REPO))


def test_cells_mode_aggregates_the_model_counters_and_names_the_reason_for_an_error():
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--decode-tok-s", "50", "--prefill-tok-s", "900"])
        assert r.returncode == 0, r.stderr[-800:]
        assert len(r.stdout.strip().splitlines()) == 1, "stdout must be ONE compact line: the 12B window reads the cells object from it (PR #1946 review)"
        d = json.loads(r.stdout)
        t = d["totals"]
        assert t["calls"] > 0 and t["prompt_tokens"] > 0 and t["completion_tokens"] > 0 and t["members"] == 0       # was calls == 0
        assert t["calls"] == d["cells"]["model_totals"]["calls"] and srv.requests >= t["calls"]
        assert isinstance(d["cells"]["reasons"], dict)
    finally:
        srv.close()


def test_cells_mode_logs_one_progress_line_per_cell_so_a_killed_run_keeps_its_verdicts():
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--decode-tok-s", "50", "--prefill-tok-s", "900"])
        assert r.returncode == 0, r.stderr[-800:]
        import re
        lines = re.findall(r"^NIGHT_CELL id=(K\d+f?) verdict=(\w+) wall_s=", r.stderr, re.M)
        cells = json.loads(r.stdout)["cells"]
        assert [k for k, _v in lines] == ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K9f", "K10", "K11", "K12"]
        assert all(cells[k] == v for k, v in lines) and cells["skipped_budget"] == []
    finally:
        srv.close()


def test_cell_budget_stops_starting_cells_that_would_overrun_and_still_prints_the_one_json_line():
    """The 2026-10-09 failure was a kill with NO output. With a budget the CLI skips (and names) the cells it cannot finish, and prints what it has."""
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--decode-tok-s", "3", "--prefill-tok-s", "100", "--cell-budget", "30"])
        assert r.returncode == 0, r.stderr[-800:]
        assert len(r.stdout.strip().splitlines()) == 1
        c = json.loads(r.stdout)["cells"]
        assert c["skipped_budget"] == ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K9f", "K10", "K11", "K12"] and c["skip"] == 13 and c["pass"] == 0
        assert "cell_budget" in c["reasons"]["K1"] and srv.requests <= 2                      # no cell was started: only the model probe touched the server
        ok = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--decode-tok-s", "50", "--prefill-tok-s", "900", "--cell-budget", "3000"])
        assert json.loads(ok.stdout)["cells"]["skipped_budget"] == [] and json.loads(ok.stdout)["cells"]["pass"] > 0
    finally:
        srv.close()


def test_a_cell_that_cannot_reach_its_model_is_an_error_that_says_why(tmp_path):
    srv = FakeNightServer(FakeNightBrain())
    srv.up = False
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--cells"])
        assert r.returncode == 2 and json.loads(r.stdout)["status"] == "llm_unreachable" and json.loads(r.stdout)["reason"]
    finally:
        srv.close()


def test_pretty_is_the_only_way_to_get_a_multi_line_stdout():
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--cells", "--pretty"])
        assert r.returncode == 0, r.stderr[-800:]
        assert len(r.stdout.strip().splitlines()) > 5 and "cells" in json.loads(r.stdout)
    finally:
        srv.close()


def _cli_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("zoe_night_mind_cli", REPO / "scripts" / "maintenance" / "zoe-night-mind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exit_2_after_an_earlier_member_committed_says_how_far_it_got(monkeypatch, capsys):
    cli = _cli_module()
    members = [{"user_id": "a", "status": "ran", "written": 3, "observations_written": 3}, {"user_id": "b", "status": "ran", "written": 0, "observations_written": 0},
               {"user_id": "c", "status": "llm_unreachable", "written": 0}]
    assert cli.written_tally(members) == (1, 3)

    async def fake_amain(argv=None):
        return 2, {"status": "llm_unreachable", "members": members, "members_written": 1, "members_total": 3, "totals": {}}
    monkeypatch.setattr(cli, "amain", fake_amain)
    assert cli.main(["--all-members"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out)["members_written"] == 1 and "after 1 of 3 members had committed" in out.err and "per member" in out.err
    assert "PER MEMBER" in cli.__doc__                                      # the contract text states the guarantee per member, not run-wide


def test_a_pin_or_a_card_said_in_a_turn_is_never_kept_as_a_night_quote():
    for secret in ("my pin is 4826 and I always forget it", "my card number is 4111 1111 1111 1111 and I am nervous about it"):
        assert nm._unstorable(secret) is True, secret                       # the scrubbed text differs from the words: the quote is rejected, never rewritten
    assert nm._unstorable("I felt really proud of the way the rehearsal went on Sunday") is False
