"""Pin scripts/perf/self_improver.py - self-evolution Phase 1 (the nightly manner.md improver, lane B E1).

Fakes only: a fake Anthropic client, the synthetic evaluator, a fake overlay root. NO brain, NO network, NO key, NO live
state (every test gets its own tmp home). Each guard has a negative control: undo the guard and the test goes red.

* candidate generation: two candidates from the reflection, each through the overlay's own rules;
* the hard per-night cost cap: checked BEFORE every call, worst case, shared across a re-run of the night, unknown model refused;
* the key: read by NAME, never in a row, a file or a problem string;
* the fourteen-night stop rule;
* SHADOW-ONLY: a candidate can never reach the live overlay (or the repo) - home placement, planted symlinks, a full winning night.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


si = _load("self_improver", "scripts/perf/self_improver.py")

SEED = "Talk like a friend. Ask at most one question. Never flatter."
SENTINEL = "sk-test-SENTINEL-0123456789"
NIGHT = dt.datetime(2026, 11, 6, 4, 0, 0)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A fake live overlay root and flue env per test; the key env var unset unless a test sets it."""
    monkeypatch.setattr(si, "LIVE_OVERLAY_ROOT", tmp_path / "live-overlay")
    monkeypatch.setattr(si, "FLUE_ENV", tmp_path / "flue.env")
    monkeypatch.delenv(si.KEY_ENV, raising=False)
    (tmp_path / "live-overlay" / "current").mkdir(parents=True)
    (tmp_path / "live-overlay" / "current" / "manner.md").write_text("LIVE TEXT", encoding="utf-8")


def _cfg(**kw) -> "si.Config":
    return si.Config(**kw)


class FakeClient:
    """messages.create -> a canned answer per call; records every call."""

    def __init__(self, answers, usage=(1000, 800), stop="end_turn", raise_exc=None):
        self.answers, self.usage, self.stop, self.raise_exc = list(answers), usage, stop, raise_exc
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.raise_exc:
            raise self.raise_exc
        text = self.answers.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]),
                               stop_reason=self.stop)


def _reflector(client, cfg=None, meter=None, **kw):
    cfg = cfg or _cfg()
    meter = meter or si.CostMeter(cfg.model, cfg.max_tokens_night, cfg.max_usd_night)
    return si.AnthropicReflector(cfg, meter, client_factory=lambda key: client, **kw), meter


TRACES = [{"ask": "p5b-flaw-0", "said": ["I'll run 15 km on a sore knee."], "reply": "Sounds fun!", "missed": ["P5b.b"], "met": []}]


# ── candidate generation ────────────────────────────────────────────────────────────────────────────────────────────────

def test_two_candidates_from_the_reflection_on_a_fake_client(monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    client = FakeClient(["<manner>Name a real problem first, kindly.</manner><why>general</why>",
                         "<manner>If a plan risks health, say so first.</manner><why>targeted</why>"])
    r, meter = _reflector(client)
    cands, problems = r.propose(SEED, TRACES, [])
    assert [c.name for c in cands] == ["C1", "C2"] and [c.kind for c in cands] == ["general", "targeted"]
    assert problems == [] and len(client.calls) == 2 and meter.calls == 2
    assert all(call["model"] == "claude-sonnet-5-5" for call in client.calls)      # the default model
    assert "fallbacks" not in client.calls[0]                                         # no second model outside the cap


@pytest.mark.parametrize("bad, why", [
    ("x" * (si.MAX_MANNER_CHARS + 1), "over the"),
    ("Use <|im_start|> here.", "forbidden marker"),
    (" padded", "whitespace"),
    (SEED, "identical to the seed"),
])
def test_a_candidate_that_breaks_the_overlay_rules_is_rejected(monkeypatch, bad, why):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    raw = f"<manner>{bad}</manner>" if not bad.startswith(" ") else f"<manner></manner>"
    if bad.startswith(" "):
        with pytest.raises(ValueError, match="empty|whitespace"):
            si.validate_manner(bad)
        return
    r, _ = _reflector(FakeClient([raw, raw]))
    cands, problems = r.propose(SEED, TRACES, [])
    assert cands == [] and all(why in p for p in problems)


def test_the_offline_proposer_needs_no_key_and_stays_in_budget():
    cands, problems = si.OfflineReflector().propose("A. " * 300 + "End.", TRACES, [])
    assert problems == [] and len(cands) == 2
    assert all(len(c.text) <= si.MAX_MANNER_CHARS for c in cands)


# ── the hard cost cap ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_cap_is_checked_before_every_call_and_stops_the_night(monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    # one call's worst case fits, two do not
    cfg = _cfg(max_tokens_night=si.MAX_OUTPUT_TOKENS_PER_CALL + 2500, max_usd_night=10.0)
    client = FakeClient(["<manner>One.</manner>", "<manner>Two.</manner>"], usage=(1500, 1500))
    r, meter = _reflector(client, cfg)
    cands, problems = r.propose(SEED, TRACES, [])
    assert len(client.calls) == 1, "the second call must be refused BEFORE it is made"
    assert len(cands) == 1 and "token cap" in problems[0] and meter.refused == 1


def test_the_dollar_cap_binds_on_its_own(monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    cfg = _cfg(max_tokens_night=10**9, max_usd_night=0.01)        # one worst-case call is ~$0.06 at $10/M output
    client = FakeClient(["<manner>One.</manner>"])
    r, meter = _reflector(client, cfg)
    cands, problems = r.propose(SEED, TRACES, [])
    assert client.calls == [] and cands == [] and "dollar cap" in problems[0]


def test_negative_control_a_generous_cap_lets_both_calls_through(monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    client = FakeClient(["<manner>One.</manner>", "<manner>Two.</manner>"])
    r, _ = _reflector(client, _cfg(max_tokens_night=10**6, max_usd_night=5.0))
    assert len(r.propose(SEED, TRACES, [])[0]) == 2 and len(client.calls) == 2


def test_an_unpriced_model_is_refused_not_guessed():
    m = si.CostMeter("claude-unknown-9", 10**6, 10.0)
    with pytest.raises(si.CapExceeded, match="no price"):
        m.reserve(10, 10)


def test_a_rerun_of_the_same_night_shares_one_cap(tmp_path):
    cfg = _cfg()
    m = si.CostMeter.for_night(tmp_path / "home", "2026-11-06", cfg)
    m.record(5000, 30000)
    m.save(tmp_path / "home", "2026-11-06")
    again = si.CostMeter.for_night(tmp_path / "home", "2026-11-06", cfg)
    assert again.tokens == 35000
    with pytest.raises(si.CapExceeded):
        again.reserve(2000, si.MAX_OUTPUT_TOKENS_PER_CALL)          # 35000 + 8000 > 40000


# ── the key ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_key_never_reaches_a_problem_string_a_row_or_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)

    class Boom(Exception):
        status_code = 401

    client = FakeClient([], raise_exc=Boom(f"invalid x-api-key {SENTINEL}"))
    r, meter = _reflector(client)
    monkeypatch.setattr(si.AnthropicReflector, "key_present", staticmethod(lambda: True))
    code, row = si.run_night(_cfg(), home=tmp_path / "home", mode="live", evaluator=si.FakeEvaluator(), reflector=r,
                             meter=meter, now=NIGHT, gate=lambda c, n: [], seed=(SEED, "test"), lock_path=None, window_lock_path=None, log=lambda *_: None)
    assert code == si.EXIT_UNMEASURED and "Boom 401" in json.dumps(row)
    for p in (tmp_path / "home").rglob("*"):
        if p.is_file():
            assert SENTINEL not in p.read_text(encoding="utf-8"), p
    assert SENTINEL not in json.dumps(row)


def test_a_live_night_without_the_key_is_refused_before_any_gate(tmp_path):
    called = []
    r, meter = _reflector(FakeClient([]))
    code, row = si.run_night(_cfg(), home=tmp_path / "home", mode="live", evaluator=si.FakeEvaluator(), reflector=r,
                             meter=meter, now=NIGHT, gate=lambda c, n: called.append(1) or [], seed=(SEED, "t"),
                             lock_path=None, window_lock_path=None, log=lambda *_: None)
    assert code == si.EXIT_REFUSED and si.KEY_ENV in row["reason"] and called == []


# ── traces + privacy ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_only_dev_rows_reach_the_reflection():
    plan = si.FakeEvaluator().plan()
    dev = [{"phase": "dev", "ask": "p5b-flaw-0", "sample": 0, "replies": ["ok"], "scored": {"P5b.b": [False, {"why": "x"}]}}]
    t = si.build_traces(dev, plan, ["flaw"])
    assert t and t[0]["missed"] == ["P5b.b"] and "why" not in json.dumps(t)      # the scorer's evidence never travels
    with pytest.raises(si.PrivacyRefusal):
        si.build_traces([dict(dev[0], phase="heldout")], plan, ["flaw"])


@pytest.mark.parametrize("payload", ["mail me at someone@example.com", "ask p5b-flawH-3 again", "about Bartholomew today"])
def test_the_privacy_guard_refuses_before_anything_leaves(payload):
    with pytest.raises(si.PrivacyRefusal):
        si.privacy_guard(payload, ["bartholomew"])
    si.privacy_guard("a synthetic bench sentence", ["bartholomew"])                # negative control: clean text passes


# ── the verdict ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _rows(arm_rates: dict[str, float], n: int = 40, half: str = "P5b.b", phase: str = "heldout", seed: int = 0):
    import random

    rng = random.Random(seed)
    rows = []
    for arm, p in arm_rates.items():
        for i in range(n):
            rows.append({"arm": f"on:{arm}", "phase": phase, "ask": f"a{i}", "sample": 0, "scored": {half: [rng.random() < p, {}]}})
    return rows


def _plan(n: int = 40):
    return {"violation_halves": [], "regression": ["P7.a"], "plans": {"heldout": {"flaw": [[f"a{i}", 0] for i in range(n)]}}}


def test_a_clear_lift_beats_the_sampling_control():
    v = si.judge_heldout(_rows({"R0": 0.3, "C1": 0.95, "C2": 0.3, "S1": 0.3, "S2": 0.3}), _plan(), ["flaw"], ["C1", "C2"], ["S1", "S2"])
    assert v["measured"] and v["best_candidate"] == "C1" and v["heldout_win"]


def test_negative_control_candidates_that_are_just_more_draws_do_not_win():
    same = {"R0": 0.4, "C1": 0.4, "C2": 0.4, "S1": 0.4, "S2": 0.4}
    rows = []
    for s, arm in enumerate(same):          # every arm an independent draw of the SAME rate
        rows += _rows({arm: same[arm]}, seed=s + 1)
    v = si.judge_heldout(rows, _plan(), ["flaw"], ["C1", "C2"], ["S1", "S2"])
    assert v["measured"]
    best_c = max(v["scores"][c]["rate"] for c in ("C1", "C2"))
    best_s = max(v["scores"][a]["rate"] for a in ("R0", "S1", "S2"))
    assert v["heldout_win"] == (best_c > best_s and v["candidates"][v["best_candidate"]]["admissible"])


def test_an_incomplete_arm_is_unmeasured_never_a_loss_or_a_win():
    rows = [r for r in _rows({"R0": 0.3, "C1": 0.95, "C2": 0.3, "S1": 0.3, "S2": 0.3}) if not (r["arm"] == "on:S2" and r["ask"] == "a7")]
    v = si.judge_heldout(rows, _plan(), ["flaw"], ["C1", "C2"], ["S1", "S2"])
    assert not v["measured"] and not v["heldout_win"] and "S2" in v["problems"][0]


def test_collateral_above_the_noise_floor_is_inadmissible():
    # C1 wins overall but flips many asks the reference passed; the sampling arms are identical to R0 (noise floor 0)
    base = _rows({"R0": 0.5}, seed=3)
    rows = list(base)
    for arm in ("S1", "S2", "C2"):
        rows += [dict(r, arm=f"on:{arm}") for r in base]
    flipped = []
    for r in base:
        v = r["scored"]["P5b.b"][0]
        flipped.append(dict(r, arm="on:C1", scored={"P5b.b": [not v if r["ask"] < "a2" else True, {}]}))
    rows += flipped
    v = si.judge_heldout(rows, _plan(), ["flaw"], ["C1", "C2"], ["S1", "S2"])
    c1 = v["candidates"]["C1"]
    assert v["noise_floor"] == 0 and c1["regressions"] > 0 and not c1["admissible"] and not v["heldout_win"]


def test_the_regression_veto_uses_separation_not_a_point_drop():
    rows = _rows({"R0": 0.95, "C1": 0.2}, n=40, half="P7.a", phase="regress")
    assert si.regression_veto(rows, "R0", "C1", ["P7.a"], [])
    rows = _rows({"R0": 0.9, "C1": 0.85}, n=40, half="P7.a", phase="regress", seed=5)
    assert si.regression_veto(rows, "R0", "C1", ["P7.a"], []) == []


# ── the stop rule ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _n(win, mode="live", status="measured"):
    return {"mode": mode, "status": status, "win": win}


def test_ten_of_fourteen_passes():
    st = si.stop_state([_n(True)] * 10 + [_n(False)] * 4)
    assert st["status"] == "passed" and st["complete"]


def test_a_fifth_loss_stops_early():
    st = si.stop_state([_n(True)] * 3 + [_n(False)] * 5)
    assert st["status"] == "stopped" and st["complete"] and st["measured_nights"] == 8


def test_four_losses_keep_running():
    st = si.stop_state([_n(False)] * 4)
    assert st["status"] == "running" and not st["complete"]


def test_dry_run_skipped_and_unmeasured_nights_never_count():
    st = si.stop_state([_n(False, mode="dry-run")] * 9 + [_n(None, status="skipped")] * 9 + [_n(None, status="unmeasured")] * 9)
    assert st["measured_nights"] == 0 and st["status"] == "running"


def test_only_the_first_fourteen_measured_nights_decide():
    st = si.stop_state([_n(True)] * 9 + [_n(False)] * 4 + [_n(False)] + [_n(True)] * 5)
    assert st["measured_nights"] == 14 and st["wins"] == 9 and st["status"] == "stopped"


def test_a_decided_rule_refuses_every_later_live_night(tmp_path, monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    home = tmp_path / "home"
    home.mkdir()
    (home / "nights.jsonl").write_text("".join(json.dumps(_n(False)) + "\n" for _ in range(5)), encoding="utf-8")
    r, meter = _reflector(FakeClient([]))
    code, row = si.run_night(_cfg(), home=home, mode="live", evaluator=si.FakeEvaluator(), reflector=r, meter=meter, now=NIGHT,
                             gate=lambda c, n: [], seed=(SEED, "t"), lock_path=None, window_lock_path=None, log=lambda *_: None)
    assert code == si.EXIT_REFUSED and "stopped" in row["reason"]


# ── SHADOW ONLY: a candidate can never reach the live overlay ───────────────────────────────────────────────────────────

def test_a_home_inside_the_live_overlay_or_the_repo_is_refused(tmp_path):
    for home in (si.LIVE_OVERLAY_ROOT, si.LIVE_OVERLAY_ROOT / "current", REPO / "tmp-improver", REPO.parent):
        with pytest.raises(si.ShadowViolation):
            si.check_home(home)
    si.check_home(tmp_path / "ok-home")                                   # negative control: a separate dir is fine


def test_a_configured_overlay_dir_is_forbidden_too(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere" / "overlay" / "current"
    other.mkdir(parents=True)
    si.FLUE_ENV.write_text(f"ZOE_BRAIN_HARNESS_DIR={other}\n", encoding="utf-8")
    with pytest.raises(si.ShadowViolation):
        si.check_home(other.parent / "improver")


def test_a_planted_symlink_cannot_redirect_a_shadow_write(tmp_path):
    home = tmp_path / "home"
    (home / "nights" / "n1").mkdir(parents=True)
    (home / "nights" / "n1" / "shadow").symlink_to(si.LIVE_OVERLAY_ROOT / "current")
    with pytest.raises(si.ShadowViolation):
        si.safe_write(home, "nights/n1/shadow/manner.md", "CANDIDATE")
    with pytest.raises(si.ShadowViolation):
        si.safe_write(home, "../escape.md", "CANDIDATE")
    with pytest.raises(si.ShadowViolation):
        si.safe_write(home, str(si.LIVE_OVERLAY_ROOT / "current" / "manner.md"), "CANDIDATE")
    assert (si.LIVE_OVERLAY_ROOT / "current" / "manner.md").read_text() == "LIVE TEXT"


def test_negative_control_without_the_guard_the_symlink_write_would_land_live(tmp_path, monkeypatch):
    """Proves the test above is red-on-revert: with the guard disabled the same write reaches the live overlay."""
    home = tmp_path / "home"
    (home / "nights" / "n1").mkdir(parents=True)
    (home / "nights" / "n1" / "shadow").symlink_to(si.LIVE_OVERLAY_ROOT / "current")
    target = home / "nights" / "n1" / "shadow" / "manner.md"
    target.write_text("CANDIDATE", encoding="utf-8")                      # what an unguarded writer does
    assert (si.LIVE_OVERLAY_ROOT / "current" / "manner.md").read_text() == "CANDIDATE"


def test_a_winning_night_writes_only_its_shadow_and_never_the_live_overlay(tmp_path):
    home = tmp_path / "home"
    before = {p: p.read_bytes() for p in si.LIVE_OVERLAY_ROOT.rglob("*") if p.is_file()}
    code, row = si.run_night(_cfg(), home=home, mode="dry-run", evaluator=si.FakeEvaluator(), reflector=si.OfflineReflector(),
                             now=NIGHT, gate=lambda c, n: [], seed=(SEED, "t"), lock_path=None, window_lock_path=None, log=lambda *_: None)
    assert code == si.EXIT_MEASURED and row["status"] == "measured"
    after = {p: p.read_bytes() for p in si.LIVE_OVERLAY_ROOT.rglob("*") if p.is_file()}
    assert after == before
    ledger = si.read_jsonl(home / "shadow-ledger.jsonl")
    if row.get("win"):
        assert ledger and all(x["applied"] is False and x["would_apply"] for x in ledger)
        assert (home / "nights" / row["night"] / "shadow" / "manner.md").is_file()
    written = [p for p in tmp_path.rglob("*") if p.is_file() and not str(p).startswith(str(home)) and "live-overlay" not in str(p)]
    assert written == [], written


def test_the_module_has_one_write_path_and_no_promotion_code():
    src = (REPO / "scripts" / "perf" / "self_improver.py").read_text(encoding="utf-8")
    assert src.count(".write_text(") == 1 and "def safe_write" in src                 # only safe_write writes
    assert "open(" not in src.replace("os.open(", "")                                # no other file handle
    for forbidden in ("harness-overlay.ts", "reloadHarness", "SIGHUP", "symlink_to", "os.symlink"):
        assert forbidden not in src


# ── the whole dry-run night + the gate ────────────────────────────────────────────────────────────────────────────────

def test_a_dry_run_night_rehearses_everything_and_counts_for_nothing(tmp_path):
    home = tmp_path / "home"
    code, row = si.run_night(_cfg(), home=home, mode="dry-run", evaluator=si.FakeEvaluator(), reflector=si.OfflineReflector(),
                             now=NIGHT, gate=lambda c, n: ["informational"], seed=(SEED, "t"), lock_path=None, window_lock_path=None, log=lambda *_: None)
    assert code == si.EXIT_MEASURED and row["mode"] == "dry-run" and row["gate_now"] == ["informational"]
    assert set(row["heldout"]["scores"]) == {"R0", "C1", "C2", "S1", "S2"}
    assert si.stop_state(si.read_jsonl(home / "nights.jsonl"))["measured_nights"] == 0
    assert (home / "nights" / row["night"] / "candidates" / "C1" / "manner.md").is_file()


def test_a_measured_live_night_runs_once(tmp_path, monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    answers = ["<manner>Name the real problem in your first sentence, kindly.</manner>", "<manner>Say a fine plan sounds good.</manner>"]
    home = tmp_path / "home"
    r, meter = _reflector(FakeClient(answers))
    kw = dict(home=home, mode="live", evaluator=si.FakeEvaluator(), now=NIGHT, gate=lambda c, n: [], seed=(SEED, "t"),
              lock_path=None, window_lock_path=None, log=lambda *_: None)
    code, row = si.run_night(_cfg(), reflector=r, meter=meter, **kw)
    assert code == si.EXIT_MEASURED and row["reflection_cost"]["calls"] == 2
    r2, m2 = _reflector(FakeClient(list(answers)))
    code2, row2 = si.run_night(_cfg(), reflector=r2, meter=m2, **kw)
    assert code2 == si.EXIT_REFUSED and "one measurement per night" in row2["reason"]


class _HQ:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def check(self, name, **kw):
        return SimpleNamespace(allowed=self.allowed, reason="quiet hours")


def _runner(nb_rc=1, vr="inactive", lander=1):
    def run(cmd, **kw):
        if cmd[0] == "bash":
            return SimpleNamespace(returncode=nb_rc, stdout="")
        if cmd[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout=vr + "\n")
        if cmd[0] == "pgrep":
            return SimpleNamespace(returncode=0 if lander == 0 else 1, stdout="")
        raise AssertionError(cmd)
    return run


@pytest.fixture
def _clear_box(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "_held", lambda path, shared=False: False)
    monkeypatch.setattr(si, "mem_available_mb", lambda: 4000)
    nb = tmp_path / "night_busy.sh"
    nb.write_text("exit 1\n")
    monkeypatch.setenv("ZOE_NIGHT_BUSY", str(nb))


def test_the_gate_clears_a_quiet_box_in_the_slot(_clear_box):
    assert si.live_gate(_cfg(), NIGHT, runner=_runner(), hq=_HQ()) == []


@pytest.mark.parametrize("when, runner, hq, needle", [
    (dt.datetime(2026, 11, 6, 14, 0), _runner(), _HQ(), "outside the improver slot"),
    (dt.datetime(2026, 11, 6, 4, 20), _runner(), _HQ(), "outside the improver slot"),        # < 10 min to the deadline
    (NIGHT, _runner(nb_rc=0), _HQ(), "night_busy.sh test says pause"),
    (NIGHT, _runner(nb_rc=7), _HQ(), "fail closed"),
    (NIGHT, _runner(vr="active"), _HQ(), "zoe-voice-regression is active"),
    (NIGHT, _runner(lander=0), _HQ(), "land_voice_pr.sh"),
    (NIGHT, _runner(), _HQ(allowed=False), "quiet hours"),
])
def test_each_gate_refuses_on_its_own(_clear_box, when, runner, hq, needle):
    why = si.live_gate(_cfg(), when, runner=runner, hq=hq)
    assert any(needle in w for w in why), why


def test_a_skipped_night_never_probes(tmp_path, monkeypatch):
    monkeypatch.setenv(si.KEY_ENV, SENTINEL)

    class Exploding(si.FakeEvaluator):
        def run(self, *a, **k):
            raise AssertionError("probed on a skipped night")

    r, meter = _reflector(FakeClient([]))
    code, row = si.run_night(_cfg(), home=tmp_path / "home", mode="live", evaluator=Exploding(), reflector=r, meter=meter,
                             now=NIGHT, gate=lambda c, n: ["night window running"], seed=(SEED, "t"), lock_path=None, window_lock_path=None,
                             log=lambda *_: None)
    assert code == si.EXIT_SKIPPED and row["status"] == "skipped"


def test_a_held_brain_window_lock_skips_the_night_and_a_free_one_is_taken_shared(tmp_path, monkeypatch):
    """Window-first: an exclusive holder (a 12B window) skips the night before any probe; free, the improver holds it SHARED
    (a second shared holder - the rig's own preflight - still gets in) and releases it afterwards."""
    import fcntl

    monkeypatch.setenv(si.KEY_ENV, SENTINEL)
    win, harness = str(tmp_path / "window.lock"), str(tmp_path / "harness.lock")
    seen = []

    class Probe(si.FakeEvaluator):
        def run(self, *a, **k):
            fd = os.open(win, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)        # shared: compatible
                seen.append("shared-ok")
                with pytest.raises(BlockingIOError):
                    fd2 = os.open(win, os.O_RDWR)
                    try:
                        fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a 12B window could not start now
                    finally:
                        os.close(fd2)
            finally:
                os.close(fd)
            return super().run(*a, **k)

    holder = os.open(win, os.O_RDWR | os.O_CREAT)
    fcntl.flock(holder, fcntl.LOCK_EX)
    r, meter = _reflector(FakeClient([]))
    kw = dict(home=tmp_path / "home", mode="live", now=NIGHT, gate=lambda c, n: [], seed=(SEED, "t"),
              lock_path=harness, window_lock_path=win, log=lambda *_: None)
    code, row = si.run_night(_cfg(), evaluator=Probe(), reflector=r, meter=meter, **kw)
    assert code == si.EXIT_SKIPPED and "brain-window" in row["reason"] and seen == []
    os.close(holder)
    r, meter = _reflector(FakeClient(["<manner>Name the real problem first.</manner>", "<manner>Fine plans: say so.</manner>"]))
    code, row = si.run_night(_cfg(), evaluator=Probe(), reflector=r, meter=meter, **kw)
    assert code == si.EXIT_MEASURED and seen and set(seen) == {"shared-ok"}
    fd = os.open(win, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                         # released after the night
    os.close(fd)


def test_the_shipped_seed_passes_the_overlay_rules():
    text, src = si.current_manner(flue_env=Path("/nonexistent"), lexicon=REPO / "services" / "zoe-data" / "lexicons_data" / "en.json")
    assert si.validate_manner(text) == text and src.startswith("lexicon:")
