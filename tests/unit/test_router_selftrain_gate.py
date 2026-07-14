"""Promotion-ratchet tests for the router self-training loop.

The ratchet is the entire safety story of scripts/maintenance/router_selftrain.py:
it is what stops an autonomous retrain loop from quietly serving a worse model.
These tests pin its behaviour — a regression, a chat false positive, a blown
latency budget, or a replay gate that did not actually run must all REJECT.

Pure-logic only (stdlib), so this runs in the fast `ci_safe` lane.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "router_selftrain", REPO / "scripts" / "maintenance" / "router_selftrain.py")
rs = importlib.util.module_from_spec(_SPEC)
# register before exec: @dataclass resolves annotations via sys.modules[__module__]
sys.modules["router_selftrain"] = rs
_SPEC.loader.exec_module(rs)


# The live incumbent: 91.4% overall / 0% chat-FP / 386ms p50 (prod-path.json).
INCUMBENT = rs.EvalScore(overall_pct=91.4, chat_fp_pct=0.0, p50_ms=386.0, n=81)
PASSED_REPLAY = rs.ReplayVerdict(ran=True, passed=True, detail="n=20")


def candidate(overall=93.8, chat_fp=0.0, p50=420.0, n=81):
    return rs.EvalScore(overall_pct=overall, chat_fp_pct=chat_fp, p50_ms=p50, n=n)


# --- promote ---------------------------------------------------------------
def test_all_gates_pass_promotes():
    d = rs.decide_promotion(INCUMBENT, candidate(), PASSED_REPLAY)
    assert d.promote is True
    assert all(d.checks.values())
    assert d.reasons == []


def test_equal_accuracy_promotes():
    """A tie promotes: same measured quality, strictly more training signal."""
    d = rs.decide_promotion(INCUMBENT, candidate(overall=91.4), PASSED_REPLAY)
    assert d.promote is True


# --- reject ----------------------------------------------------------------
def test_accuracy_regression_rejects():
    d = rs.decide_promotion(INCUMBENT, candidate(overall=91.3), PASSED_REPLAY)
    assert d.promote is False
    assert d.checks["no_accuracy_regression"] is False
    assert "regression" in " ".join(d.reasons)


def test_chat_false_positive_rejects_even_when_more_accurate():
    """The 92.6% ungated model was MORE accurate and still unshippable: it
    called a tool on 12.5% of chat turns. Accuracy never buys off a chat-FP."""
    d = rs.decide_promotion(INCUMBENT, candidate(overall=92.6, chat_fp=12.5),
                            PASSED_REPLAY)
    assert d.promote is False
    assert d.checks["chat_fp_zero"] is False


def test_latency_over_budget_rejects():
    d = rs.decide_promotion(INCUMBENT, candidate(p50=600.0), PASSED_REPLAY)
    assert d.promote is False
    assert d.checks["p50_under_budget"] is False


def test_failed_replay_gate_rejects():
    replay = rs.ReplayVerdict(ran=True, passed=False, detail="said-vs-did regressed")
    d = rs.decide_promotion(INCUMBENT, candidate(), replay)
    assert d.promote is False
    assert d.checks["replay_gate_passed"] is False


def test_skipped_replay_gate_is_not_a_pass():
    """voice_regression_probe.py exits 0 when it SKIPS (box too tight). A skip
    must never be read as a pass — the model would go live unreplayed."""
    replay = rs.ReplayVerdict(ran=False, passed=False, detail="probe skipped")
    d = rs.decide_promotion(INCUMBENT, candidate(), replay)
    assert d.promote is False
    assert d.checks["replay_gate_passed"] is False
    assert "did not run" in " ".join(d.reasons)


def test_truncated_corpus_rejects():
    """A candidate scored on fewer cases is not comparable — 100% of 5 cases
    must not beat 91.4% of 81."""
    d = rs.decide_promotion(INCUMBENT, candidate(overall=100.0, n=5), PASSED_REPLAY)
    assert d.promote is False
    assert d.checks["corpus_intact"] is False


def test_every_gate_is_required():
    """No single passing gate can carry a promotion."""
    for bad in (candidate(overall=80.0), candidate(chat_fp=1.0),
                candidate(p50=900.0), candidate(n=0)):
        assert rs.decide_promotion(INCUMBENT, bad, PASSED_REPLAY).promote is False


# --- post-deploy rollback --------------------------------------------------
# Post-deploy is judged against the incumbent's own LIVE score (the model that
# was just replaced, measured on the same live rig) — not against the
# candidate's scratch-rig number, which is not comparable.
def test_post_deploy_healthy_keeps_deploy():
    post = rs.EvalScore(92.0, 0.0, 430.0, 81)
    assert rs.decide_rollback(post, INCUMBENT).promote is True


def test_post_deploy_accuracy_collapse_rolls_back():
    post = rs.EvalScore(70.0, 0.0, 430.0, 81)
    d = rs.decide_rollback(post, INCUMBENT)
    assert d.promote is False
    assert d.checks["accuracy_holds"] is False


def test_post_deploy_chat_fp_rolls_back():
    post = rs.EvalScore(93.8, 2.5, 430.0, 81)
    assert rs.decide_rollback(post, INCUMBENT).promote is False


def test_post_deploy_latency_blowout_rolls_back():
    post = rs.EvalScore(93.8, 0.0, 700.0, 81)
    d = rs.decide_rollback(post, INCUMBENT)
    assert d.promote is False
    assert d.checks["p50_under_budget"] is False


def test_post_deploy_small_measurement_wobble_is_tolerated():
    """The live re-measure runs on a differently-loaded box; a ~1pt wobble is
    noise, not a regression."""
    post = rs.EvalScore(90.5, 0.0, 430.0, 81)  # incumbent live was 91.4
    assert rs.decide_rollback(post, INCUMBENT).promote is True


# --- measurement validity (the rig noise is bigger than the signal) --------
def test_contended_rig_aborts_inconclusive():
    """MEASURED on this box: the same GGUF scored 86.4% live and 71.6% on a
    contended scratch sidecar. A ~15pt swing from load alone dwarfs any real
    retrain delta — the loop must refuse to rule rather than reject a good
    candidate (or promote a bad one) on noise."""
    live = rs.EvalScore(86.4, 0.0, 488.0, 81)
    scratch = rs.EvalScore(71.6, 0.0, 796.0, 81)
    ok, why = rs.check_measurement_validity(live, scratch)
    assert ok is False
    assert "too contended" in why


def test_quiet_rig_is_valid():
    """Same GGUF, quiet box: 86.4% live vs 87.7% scratch — within tolerance."""
    live = rs.EvalScore(86.4, 0.0, 488.0, 81)
    scratch = rs.EvalScore(87.7, 0.0, 589.5, 81)
    ok, why = rs.check_measurement_validity(live, scratch)
    assert ok is True
    assert why == ""


# --- held-out guard (never train on the frozen eval corpus) ----------------
# The REAL shape written by the miner, labs/router-selftrain/mine_candidates.py:
#   "held_out_guard": {"result": "pass", "corpus": ..., "entries": N, "collisions": 0}
MINER_META = {"held_out_guard": {"result": "pass", "corpus": "labs/needle-benchmark/corpus.jsonl",
                                 "entries": 81, "collisions": 0}}


def test_heldout_guard_accepts_the_real_miner_meta():
    """Pins the lane-A contract: the orchestrator must accept what the miner
    actually writes (`held_out_guard.result == 'pass'`)."""
    ok, reasons = rs.check_heldout_guard(
        MINER_META, ["put milk on the shopping list"], {"what time is it"})
    assert ok is True, reasons


def test_heldout_guard_missing_meta_rejects():
    ok, reasons = rs.check_heldout_guard({}, ["turn the kitchen lights off"], set())
    assert ok is False
    assert "held-out guard" in " ".join(reasons)


def test_heldout_guard_requires_a_positive_pass_not_just_presence():
    """A present-but-not-passing guard block must not sneak through."""
    ok, _ = rs.check_heldout_guard(
        {"held_out_guard": {"result": "fail"}}, ["x"], set())
    assert ok is False


def test_heldout_guard_rejects_miner_reported_collisions():
    meta = {"held_out_guard": {"result": "pass", "collisions": 3}}
    ok, reasons = rs.check_heldout_guard(meta, ["x"], set())
    assert ok is False
    assert "collision" in " ".join(reasons)


def test_heldout_guard_detects_frozen_corpus_leak():
    """We do not merely trust the miner's meta — we recompute the overlap."""
    frozen = {"what time is it"}
    ok, reasons = rs.check_heldout_guard(MINER_META, ["What time is it?"], frozen)
    assert ok is False
    assert "FROZEN eval corpus" in " ".join(reasons)


def test_heldout_guard_clean_candidate_passes():
    frozen = {"what time is it"}
    ok, reasons = rs.check_heldout_guard(
        MINER_META, ["put milk on the shopping list"], frozen)
    assert ok is True
    assert reasons == []


def test_frozen_corpus_texts_parses_real_corpus():
    texts = rs.frozen_corpus_texts(rs.FROZEN_CORPUS)
    assert len(texts) >= 50  # the frozen corpus is 81 cases
    assert "" not in texts


# --- never leave the brain stopped -----------------------------------------
def test_sigterm_unwinds_so_the_brain_is_restored():
    """Python's DEFAULT SIGTERM disposition exits WITHOUT running `finally`.

    The scheduled job SIGTERMs this script on timeout, and a timeout can land
    inside a training window with llama-server stopped — so the default behaviour
    would leave the brain DOWN. The handler must raise SystemExit so train()'s
    `finally` restores it.
    """
    import signal
    rs._install_signal_handlers()
    handler = signal.getsignal(signal.SIGTERM)
    assert handler not in (signal.SIG_DFL, signal.SIG_IGN)
    with pytest.raises(SystemExit):
        handler(signal.SIGTERM, None)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)  # restore, don't poison the suite


def _fake_gguf(path: Path, tag: bytes) -> Path:
    """A file that passes validate_gguf: GGUF magic + plausible size."""
    path.write_bytes(b"GGUF" + tag + b"\0" * rs.MIN_GGUF_BYTES)
    return path


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate EVERY live path the deploy/recover code touches.

    This fixture exists because an earlier version of these tests patched
    SERVED_GGUF but NOT DEPLOY_MARKER — so `deploy()` wrote a marker to the REAL
    data/router_selftrain/, pointing at pytest temp files. A later `--recover` on
    the box read that marker and copied a 9-byte test file over the live 292 MB
    router GGUF, taking the sidecar down. Patch the whole set, always.
    """
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setattr(rs, "SERVED_GGUF", tmp_path / "served.gguf")
    monkeypatch.setattr(rs, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(rs, "DEPLOY_MARKER", tmp_path / "deploy_in_progress.json")
    monkeypatch.setattr(rs, "PROVENANCE", tmp_path / "provenance.json")
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(rs, "systemctl", lambda *a, **k: None)
    monkeypatch.setattr(rs, "prune_archive", lambda: None)
    return tmp_path


def test_interrupt_after_model_swap_rolls_back(sandbox, monkeypatch):
    """Once the served GGUF is swapped, ANY exit must roll back.

    The scheduler SIGTERMs the script on timeout and our handler turns that into
    SystemExit. If that lands during restart/verify or the post-deploy eval, an
    `except Exception` would not catch it and the sidecar would be left serving a
    model that never passed its live checks — the one thing deploy must never do.
    """
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    cand = _fake_gguf(sandbox / "cand.gguf", b"CANDIDATE")

    def _boom(*_a, **_k):
        raise SystemExit("SIGTERM")           # the interrupt, after the swap
    monkeypatch.setattr(rs, "wait_for", _boom)

    with pytest.raises(SystemExit):
        rs.deploy(cand, "teststamp", INCUMBENT, {})

    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUF" + b"INCUMBENT", (
        "served model was left as the unverified candidate after an interrupt")


# --- never write junk onto the live model path ------------------------------
def test_refuses_to_restore_a_truncated_file(sandbox):
    """The 9-byte-file incident, as a test. A stray/truncated 'model' must never
    reach the served path — the sidecar cannot load it and goes down."""
    _fake_gguf(rs.SERVED_GGUF, b"GOOD")
    junk = rs.ARCHIVE_DIR / "lkg_junk.gguf"
    junk.write_bytes(b"INCUMBENT")            # 9 bytes, exactly the incident
    assert rs.restore_lkg(junk) is False
    assert rs.SERVED_GGUF.read_bytes()[:8] == b"GGUFGOOD", "live model was overwritten"


def test_refuses_to_restore_from_outside_the_archive(sandbox):
    """The recovery marker is untrusted input read off disk — it must not be able
    to point the restore at an arbitrary path."""
    _fake_gguf(rs.SERVED_GGUF, b"GOOD")
    stray = _fake_gguf(sandbox / "stray.gguf", b"STRAY")   # valid GGUF, wrong place
    ok, why = rs.validate_lkg(stray)
    assert ok is False
    assert "not inside the archive" in why
    assert rs.restore_lkg(stray) is False
    assert rs.SERVED_GGUF.read_bytes()[:8] == b"GGUFGOOD"


def test_recover_rejects_a_marker_for_a_different_served_path(sandbox):
    """A stale marker from another checkout must not drive a live restore."""
    _fake_gguf(rs.SERVED_GGUF, b"GOOD")
    lkg = _fake_gguf(rs.ARCHIVE_DIR / "lkg_x.gguf", b"OLD")
    rs.DEPLOY_MARKER.write_text(json.dumps({
        "stamp": "x", "last_known_good": str(lkg),
        "served": "/some/other/checkout/model.gguf"}))
    assert rs.recover() == 1
    assert rs.SERVED_GGUF.read_bytes()[:8] == b"GGUFGOOD"


def test_deploy_refuses_a_junk_candidate(sandbox):
    _fake_gguf(rs.SERVED_GGUF, b"GOOD")
    junk = sandbox / "junk.gguf"
    junk.write_bytes(b"not a model")
    with pytest.raises(SystemExit):
        rs.deploy(junk, "s", INCUMBENT, {})
    assert rs.SERVED_GGUF.read_bytes()[:8] == b"GGUFGOOD"


def test_sigkilled_mid_deploy_is_recovered_from_disk(sandbox, monkeypatch):
    """The scheduler's timeout kills the run with SIGKILL — no handler survives it.

    If that lands after the model file was swapped but before the live checks
    passed, the sidecar is left serving an UNVERIFIED model and nothing in the
    dead process can fix it. The on-disk deploy marker is what survives, and
    --recover must use it to put the last-known-good back.
    """
    _fake_gguf(rs.SERVED_GGUF, b"CANDIDATE")                    # swap happened
    lkg = _fake_gguf(rs.ARCHIVE_DIR / "lkg_x_served.gguf", b"INCUMBENT")
    rs.DEPLOY_MARKER.write_text(json.dumps({
        "stamp": "x", "last_known_good": str(lkg), "served": str(rs.SERVED_GGUF)}))

    monkeypatch.setattr(rs, "http_ok", lambda *a, **k: True)     # brain healthy
    monkeypatch.setattr(rs, "restart_and_verify_sidecar", lambda expect: True)

    assert rs.recover() == 0
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUF" + b"INCUMBENT", (
        "unverified candidate is still the served model")
    assert not rs.DEPLOY_MARKER.exists(), "deploy marker not cleared after recovery"


def test_recover_is_a_noop_when_nothing_was_in_flight(sandbox, monkeypatch):
    """--recover must be safe to run at any time, including when all is well."""
    monkeypatch.setattr(rs, "http_ok", lambda *a, **k: True)
    assert rs.recover() == 0


def test_recover_refuses_when_lkg_is_missing(sandbox, monkeypatch):
    """Never silently 'recover' into a model we cannot verify."""
    rs.DEPLOY_MARKER.write_text(json.dumps({
        "stamp": "x", "last_known_good": str(rs.ARCHIVE_DIR / "gone.gguf"),
        "served": str(rs.SERVED_GGUF)}))
    monkeypatch.setattr(rs, "http_ok", lambda *a, **k: True)
    assert rs.recover() == 1


def test_systemctl_sets_user_bus_env():
    """`systemctl --user` from a scheduled subprocess has no login session; without
    XDG_RUNTIME_DIR the brain-restore silently fails (scripts/AGENTS.md)."""
    import inspect
    src = inspect.getsource(rs.systemctl)
    assert "XDG_RUNTIME_DIR" in src


# --- no bypass exists ------------------------------------------------------
def test_no_force_promote_symbol_exists():
    """If someone ever adds a bypass, this test is the tripwire."""
    src = (REPO / "scripts" / "maintenance" / "router_selftrain.py").read_text()
    assert "force_promote" not in src.replace("--force-promote is deliberately", "")
    for name in dir(rs):
        assert "force" not in name.lower() or name.startswith("__")


def test_decide_promotion_takes_no_override_kwarg():
    import inspect
    params = set(inspect.signature(rs.decide_promotion).parameters)
    assert params == {"incumbent", "candidate", "replay"}
