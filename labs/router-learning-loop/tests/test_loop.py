"""Local net for the router learning-loop lab (hand-run, not CI-wired; same convention as labs/self-heal):

    flock -x -w 14000 /tmp/zoe-test-lane.lock python3 -m pytest -p no:cacheprovider -q labs/router-learning-loop/tests

Every test uses a throwaway workdir under tmp_path and the committed synthetic data; nothing reads a live log, a database or a model.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import loop  # noqa: E402

SOURCE = loop.read_jsonl(HERE / "data" / "synthetic_source.jsonl")
FORGET = loop.load_forget(HERE / "data" / "forget.jsonl")
FROZEN = loop.load_frozen()
FZ = loop.frozen_norms(FROZEN)
NO_FORGET = {"ids": set(), "text_keys": set()}


def _src(rows):
    return lambda: list(rows)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── forget: honoured first, by id and by word-free key ──

def test_forgotten_rows_never_reach_training():
    examples, dropped = loop.harvest(_src(SOURCE), FORGET, FZ)
    ids = {e["id"] for e in examples}
    assert not ids & {"ctl-forget-1", "ctl-forget-2", "ctl-forget-key"}
    assert dropped["forgotten"] == 3


def test_negative_control_without_forget_list_they_would_train():
    examples, dropped = loop.harvest(_src(SOURCE), NO_FORGET, FZ)
    assert {"ctl-forget-1", "ctl-forget-2", "ctl-forget-key"} <= {e["id"] for e in examples}
    assert dropped["forgotten"] == 0


def test_forget_is_checked_before_every_other_rule():
    # a forgotten row that is ALSO a frozen case, a bad label and low confidence is counted only as forgotten
    row = {"id": "x-1", "text": "set a timer for 10 minutes", "label": "nope", "signal": "router_belief", "confidence": 0.0}
    _, dropped = loop.harvest(_src([row]), {"ids": {"x-1"}, "text_keys": set()}, FZ)
    assert dropped == Counter({"forgotten": 1})


def test_forgotten_wording_does_not_return_under_a_new_id():
    row = {"id": "fresh-id", "text": "Remind me to call the SCHOOL!", "label": "reminders", "signal": "owner_confirmed",
           "confidence": 1.0}
    examples, dropped = loop.harvest(_src([row]), FORGET, FZ)
    assert not examples and dropped["forgotten"] == 1


# ── the frozen set can never leak into training ──

def test_planted_frozen_case_is_dropped():
    examples, dropped = loop.harvest(_src(SOURCE), FORGET, FZ)
    assert dropped["frozen_overlap"] == 1
    assert not {e["norm"] for e in examples} & FZ


def test_every_frozen_case_planted_is_dropped():
    planted = [{"id": f"p{i}", "text": c["text"].upper() + " ", "label": "chat", "signal": "owner_confirmed", "confidence": 1.0}
               for i, c in enumerate(FROZEN)]
    examples, dropped = loop.harvest(_src(planted), NO_FORGET, FZ)
    assert examples == [] and dropped["frozen_overlap"] == len(FROZEN)


def test_negative_control_an_empty_frozen_guard_would_leak():
    planted = [{"id": "p", "text": FROZEN[0]["text"], "label": FROZEN[0]["expected"], "signal": "owner_confirmed",
                "confidence": 1.0}]
    examples, _ = loop.harvest(_src(planted), NO_FORGET, frozenset())
    assert len(examples) == 1


def test_incumbent_base_set_is_frozen_guarded_too():
    rows, dropped = loop.base_examples(FZ)
    assert dropped > 0
    assert not {loop.normalise(t) for t, _ in rows} & FZ


def test_weak_signals_and_conflicts_are_dropped():
    _, dropped = loop.harvest(_src(SOURCE), FORGET, FZ)
    assert dropped["low_confidence"] == 1      # the router's own belief is never a label
    assert dropped["unknown_signal"] == 1
    assert dropped["conflict"] == 2
    assert dropped["duplicate"] == 1


# ── the gate: each condition with a negative control ──

def _ev(per: dict) -> dict:
    """An evaluate()-shaped result from {intent: (correct, n)}."""
    c = sum(v[0] for v in per.values())
    n = sum(v[1] for v in per.values())
    return {"n": n, "correct": c, "accuracy_pct": round(100.0 * c / n, 1), "chat_fp_pct": 0.0,
            "per_intent": {k: {"correct": a, "n": b, "pct": round(100.0 * a / b, 1)} for k, (a, b) in per.items()}}


BASE = {"lists": (6, 12), "weather": (3, 5), "timers": (3, 4), "chat": (7, 8), "music": (3, 6)}


def test_gate_promotes_a_clean_improvement():
    after = dict(BASE, lists=(9, 12), music=(4, 6))
    v = loop.gate(_ev(BASE), _ev(after))
    assert v["promote"] and all(v["checks"].values())


def test_gate_overall_margin_required():
    v = loop.gate(_ev(BASE), _ev(BASE))        # no change: below the margin
    assert not v["promote"] and v["checks"] == {"overall_improves": False, "no_intent_regression": True, "safety_held": True}


def test_gate_refuses_an_intent_regression_beyond_tolerance():
    after = dict(BASE, lists=(12, 12), weather=(2, 5))   # big overall gain, weather -20 pts
    v = loop.gate(_ev(BASE), _ev(after))
    assert v["checks"]["overall_improves"] and not v["checks"]["no_intent_regression"] and not v["promote"]


def test_gate_tolerates_a_regression_within_tolerance():
    big = dict(BASE, lists=(6, 12))
    after = dict(BASE, lists=(5, 12), music=(6, 6))       # lists -8.3 pts (inside 15), overall up
    v = loop.gate(_ev(big), _ev(after))
    assert v["checks"]["no_intent_regression"] and v["promote"]


def test_gate_refuses_any_safety_loss_even_one_case():
    after = dict(BASE, lists=(12, 12), chat=(6, 8))       # chat -12.5 pts: inside the general tolerance, still refused
    v = loop.gate(_ev(BASE), _ev(after))
    assert v["checks"]["overall_improves"] and v["checks"]["no_intent_regression"]
    assert not v["checks"]["safety_held"] and not v["promote"]


def test_gate_safety_negative_control_same_loss_on_a_non_safety_intent_passes():
    after = dict(BASE, lists=(12, 12), music=(2, 6))      # music -16.7: a regression, but it is the general check that fires
    v = loop.gate(_ev(BASE), _ev(after))
    assert v["checks"]["safety_held"]


# ── the measured loop, the proposal and rollback ──

def test_clean_run_promotes_and_writes_a_text_free_proposal(tmp_path):
    j = loop.run(tmp_path, _src(SOURCE), FORGET, stamp="t-clean")
    assert j["promoted"], j["verdict"]["reasons"]
    prop_raw = (tmp_path / j["proposal"]).read_text()
    texts = {r["text"].lower() for r in SOURCE} | {c["text"].lower() for c in FROZEN}
    low = prop_raw.lower()
    assert not [t for t in texts if t in low]
    prop = json.loads(prop_raw)
    assert set(prop["evidence"]) <= {"signal", "before", "after", "controls", "diff", "probes", "gate", "canary", "ref"}


def test_proposal_matches_the_live_queue_contract(tmp_path):
    sys.path.insert(0, str(loop.REPO / "services" / "zoe-data"))
    import evolution_queue as q

    j = loop.run(tmp_path, _src(SOURCE), FORGET, stamp="t-q")
    prop = json.loads((tmp_path / j["proposal"]).read_text())
    assert prop["change_class"] in q.CLASSES and prop["status"] in q.OPEN and prop["risk_tier"] in q.RISK_TIERS
    assert len(prop["title"]) <= q.TITLE_MAX and len(prop["component"]) <= q.TAG_MAX
    assert q.normalize_evidence(prop["evidence"]) == json.loads(json.dumps(prop["evidence"], sort_keys=True))


def test_poisoned_run_is_refused_and_keeps_the_current_router(tmp_path):
    loop.run(tmp_path, _src([]), NO_FORGET, stamp="t-boot")          # bootstraps the incumbent, promotes nothing
    cur, prev = loop.artefacts(tmp_path)
    before = _sha(cur)
    j = loop.run(tmp_path, _src(loop.poison(SOURCE)), FORGET, stamp="t-poison")
    assert not j["promoted"] and j["verdict"]["reasons"]
    assert _sha(cur) == before and not prev.exists()
    assert not (tmp_path / "proposals").exists()
    assert json.loads((tmp_path / "runs" / "t-poison.json").read_text())["verdict"]["promote"] is False


def test_rollback_restores_the_previous_artefact_byte_for_byte(tmp_path):
    loop.run(tmp_path, _src([]), NO_FORGET, stamp="t-boot")
    cur, prev = loop.artefacts(tmp_path)
    original = cur.read_bytes()
    j = loop.run(tmp_path, _src(SOURCE), FORGET, stamp="t-up")
    assert j["promoted"] and cur.read_bytes() != original
    assert loop.rollback(tmp_path)
    assert cur.read_bytes() == original


def test_rollback_without_a_previous_router_changes_nothing(tmp_path):
    loop.run(tmp_path, _src([]), NO_FORGET, stamp="t-boot")
    cur, _ = loop.artefacts(tmp_path)
    before = cur.read_bytes()
    assert loop.rollback(tmp_path) is False and cur.read_bytes() == before


def test_artefact_is_byte_stable():
    rows, _ = loop.base_examples(FZ)
    assert loop.Model.fit(rows).to_bytes() == loop.Model.fit(list(rows)).to_bytes()
    m = loop.Model.fit(rows)
    assert loop.Model.from_bytes(m.to_bytes()).to_bytes() == m.to_bytes()


def test_poison_moves_every_label():
    assert all(p["label"] != r["label"] for p, r in zip(loop.poison(SOURCE), SOURCE))



def test_demo_totals_match_the_committed_summary(tmp_path):
    """The README's numbers: re-running the demo on the committed synthetic data reproduces results/summary.json exactly."""
    assert loop.demo_summary(tmp_path, SOURCE, FORGET) == json.loads(loop.SUMMARY.read_text())
