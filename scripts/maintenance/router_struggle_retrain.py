#!/usr/bin/env python3
"""Struggle-fed router RETRAIN-AND-ADOPT loop (Phase 3 of the self-correction loop).

Owner idea (2026-10-18): keep the voice turns that FAILED, retrain the router on them, test the new router against those
failures, and adopt it only if it fixes them without breaking anything. Contract: docs/architecture/struggle-detector.md
-> *Phase 3*; the ratchet it extends: docs/knowledge/router-selftrain-loop.md.

    struggle_events --(Phase 2: failed->success labels)--> router_example proposals --(seam: queued_router_examples)--> COLLECT
      COLLECT   strong labels only (label_source=followup; a router-belief label only when a PERSON accepted it), forget
                tombstones out, frozen-corpus texts out, one example per normalised wording, conflicting labels dropped;
                TIME split: the newest 20% of wordings are the held-out "known failures", never trained on
      ORACLE    the local brain labels every example blind (full tool menu, no hint); kept only where it AGREES with the label
      TRAIN     warm-start continuation of the SERVED generation's checkpoint (the lineage; ZOE_ROUTER_WARM_START before the
                first adoption) on the train split + a replay sample of the committed corpus (against forgetting)
      GATE      deterministic, no model-judged score; candidate and incumbent on the SAME scratch rig, back to back:
                (a) the held-out failures: >= MIN_HOLDOUT of them, and fixed - broken >= max(3, 25% of the incumbent's misses)
                (b) the frozen 81-case corpus: no case the incumbent answers may break, aggregate >=, chat-FP 0, p50 < budget
                (c) the voice replay corpus, replayed through the CANDIDATE (router_selftrain.replay_gate): ran AND passed
                (d) no example the run used was forgotten while it ran
      ADOPT     `on` only: router_selftrain.deploy (atomic swap, LKG archive, post-deploy live eval, auto-rollback); the
                checkpoint becomes a GENERATION (never deleted), the ledger records its data (ids / hashes) and gate numbers,
                the proposals it learned from move to `applied`. `shadow` builds and gates but NEVER swaps.

Flag ZOE_ROUTER_STRUGGLE_FEED = off (default) | shadow | on, read per run. `--shadow` runs a shadow pass whatever the flag
says; NOTHING on the command line can make a run adopt (there is no force flag, by design: fix the model, not the gate).

    router_struggle_retrain.py --collect-only     # how many labelled failures exist, the split, what is missing (no writes)
    router_struggle_retrain.py --shadow           # the whole loop, never swaps
    router_struggle_retrain.py                    # per the flag (the timer template runs this)
    router_struggle_retrain.py --status           # the ledger, counts only
    router_struggle_retrain.py --rollback         # put the PREVIOUS generation back (instant; the LKG archive)
    router_struggle_retrain.py --recover          # after a kill: restore a brain THIS loop stopped + an interrupted deploy

Household words: never in git, never in a log line, never in the ledger. They exist only for one run in
$ZOE_ROUTER_RETRAIN_DIR/work/<stamp>/*.texts.jsonl (0600) and are deleted when the run ends; a forget deletes them sooner.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "zoe-data"
for _p in (str(SERVICE), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import router_retrain_ledger as ledger  # noqa: E402 - stdlib, zoe-data
import router_selftrain as rs  # noqa: E402 - the existing ratchet / deploy / rollback machinery
from router_two_stage import DOMAIN_TOOLS, TOOL_DOMAIN  # noqa: E402 - stdlib static policy

FLAG = "ZOE_ROUTER_STRUGGLE_FEED"
MODES = ("off", "shadow", "on")
POLICY_DECIDER = "policy:struggle-feed"
FAST_LABELS = frozenset({"weather", "time", "timers", "lists", "calendar", "smart_home", "music", "reminders"})


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


# ── the gate's constants (pinned by tests/unit/test_router_struggle_retrain.py) ──
HOLDOUT_FRAC = _env_float("ZOE_ROUTER_RETRAIN_HOLDOUT_FRAC", 0.2, 0.1, 0.5)
MIN_HOLDOUT = 10              # fewer held-out failures = "not enough evidence": no training, no adoption
MIN_TRAIN = 10                # fewer trainable failures = nothing worth an hour of CPU
FIX_MIN_ABS = 3               # (a) fixed - broken must reach max(FIX_MIN_ABS, FIX_MIN_FRAC x the incumbent's misses)
FIX_MIN_FRAC = 0.25
GOLDEN_MAX_BROKEN = 0         # (b) the tolerance: no frozen-corpus case the incumbent answers may break (1 case = 1.2 points)

# ── training ──
REPLAY_SAMPLE = int(_env_float("ZOE_ROUTER_RETRAIN_REPLAY", 300, 0, 2000))
DELTA_REPEAT = int(_env_float("ZOE_ROUTER_RETRAIN_DELTA_REPEAT", 2, 1, 5))
EPOCHS = _env_float("ZOE_ROUTER_RETRAIN_EPOCHS", 1.0, 0.25, 4.0)
LR = _env_float("ZOE_ROUTER_RETRAIN_LR", 1e-4, 1e-6, 5e-4)

# ── where it may run ──
MEM_FLOOR_MB = 700            # never let MemAvailable go below this with the voice stack up (the watchdog kills the trainer)
DEFAULT_EST_PEAK_MB = 4000    # trainer peak RSS until a run has MEASURED it (then the measured peak x 1.15)
DEFAULT_EST_MINUTES = 120     # whole run until measured
PANEL_IDLE_MIN = 30           # the brain may be stopped only if no voice turn for this long
#: (start hh, mm, end hh, mm) local: the 12B night window and the nightly voice regression - a run must not overlap them
FORBIDDEN = ((2, 45, 3, 15), (4, 0, 4, 55))
NIGHT_UNIT = "zoe-night-window.service"


# ===========================================================================
# Pure logic (stdlib, no I/O) — unit-tested
# ===========================================================================
def feed_mode(env: Optional[dict] = None) -> str:
    v = str((env if env is not None else os.environ).get(FLAG, "off")).strip().lower()
    if v in ("on", "1", "true", "yes", "enforce"):
        return "on"
    if v in ("shadow", "dry", "dry-run"):
        return "shadow"
    return "off"


def effective_mode(flag: str, cli_shadow: bool) -> str:
    """The command line can only make a run SAFER: ``--shadow`` forces shadow; nothing forces ``on``."""
    return "shadow" if cli_shadow else flag


@dataclass
class Example:
    event_id: str
    user_key: str
    text_key: str
    label: str
    label_source: str
    ts: float
    text: str
    proposal_id: str = ""
    status: str = ""
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)

    def ledger_row(self, split: str) -> dict:
        return {"e": self.event_id, "u": self.user_key, "k": self.text_key, "s": split}


def strong_label(row: dict) -> bool:
    """A follow-up label (the user's next try WORKED on that fast path) is evidence. A router-belief label is evidence only when a
    PERSON accepted it (an owner correction); the loop's own policy accept never upgrades a weak label."""
    if row.get("label_source") == "followup":
        return True
    who = str(row.get("decided_by") or "")
    return (row.get("label_source") == "router" and row.get("status") in ("accepted", "applied")
            and bool(who) and not who.startswith("policy:"))


def select_examples(rows: Iterable[dict], *, tombstones: dict, frozen: set, normalize) -> tuple[list[Example], Counter]:
    """Seam rows -> Examples, each drop counted by reason. ``frozen`` is the normalised frozen-corpus text set."""
    ev = set(tombstones.get("event_ids") or [])
    tk = set(tombstones.get("text_keys") or [])
    uk = set(tombstones.get("user_keys") or [])
    out: list[Example] = []
    dropped: Counter = Counter()
    for r in rows:
        text = str(r.get("text") or "").strip()
        label = str(r.get("label") or "")
        if not text:
            dropped["no_text"] += 1
            continue
        if label not in FAST_LABELS or label not in DOMAIN_TOOLS:
            dropped["bad_label"] += 1
            continue
        if not strong_label(r):
            dropped["weak_label"] += 1
            continue
        ex = Example(event_id=str(r.get("event_id")), user_key=ledger.user_key(str(r.get("user_id") or "")),
                     text_key=ledger.text_key(text), label=label, label_source=str(r.get("label_source")),
                     ts=float(r.get("ts") or 0.0), text=text, proposal_id=str(r.get("proposal_id") or ""),
                     status=str(r.get("status") or ""))
        if ex.event_id in ev or ex.text_key in tk or ex.user_key in uk:
            dropped["forgotten"] += 1
            continue
        if normalize(text) in frozen:
            dropped["frozen_corpus"] += 1          # the gate's own corpus is never a training (or holdout) input
            continue
        out.append(ex)
    return out, dropped


def group_and_split(examples: list[Example], *, trained_keys: set, frac: float = HOLDOUT_FRAC) -> tuple[list[Example], list[Example], dict]:
    """One example per normalised wording (the newest), then a TIME split with no leakage:

    * a wording with two different labels is dropped (the follow-ups disagree, so neither is proof);
    * the newest ``ceil(frac x wordings)`` wordings that no SERVED generation has trained on are the holdout ("known failures");
    * everything older - plus any wording a served generation already learned, however new - is train.
    So a held-out wording never appears in the training set (one row per wording), every held-out failure happened after
    every trained one that is new to the lineage, and a wording the incumbent was trained on can never be used to "prove" it."""
    groups: dict[str, list[Example]] = {}
    for ex in examples:
        groups.setdefault(ex.text_key, []).append(ex)
    info: Counter = Counter()
    reps: list[tuple[float, Example]] = []
    for key, exs in groups.items():
        if len({e.label for e in exs}) > 1:
            info["conflicting_label"] += len(exs)
            continue
        rep = max(exs, key=lambda e: e.ts)
        reps.append((min(e.ts for e in exs), rep))
        info["duplicates_folded"] += len(exs) - 1
    reps.sort(key=lambda t: (t[0], t[1].text_key))
    fresh = [(t, e) for t, e in reps if e.text_key not in trained_keys]
    n_hold = min(len(fresh), math.ceil(frac * len(reps))) if reps else 0
    hold = fresh[len(fresh) - n_hold:] if n_hold else []
    hold_keys = {e.text_key for _, e in hold}
    train = [e for _, e in reps if e.text_key not in hold_keys]
    holdout = [e for _, e in hold]
    cutoff = min((t for t, _ in hold), default=None)
    return train, holdout, {**info, "wordings": len(reps), "holdout": len(holdout), "train": len(train),
                            "already_trained": len(reps) - len(fresh), "holdout_cutoff_ts": cutoff}


def evidence_shortfall(n_train: int, n_holdout: int) -> Optional[str]:
    """None when there is enough to train and judge; else the reason (counts only)."""
    if n_holdout < MIN_HOLDOUT:
        return (f"not enough evidence: {n_holdout} held-out failure(s) < {MIN_HOLDOUT} "
                f"(the newest {int(HOLDOUT_FRAC * 100)}% of labelled wordings; ~{math.ceil(MIN_HOLDOUT / HOLDOUT_FRAC)} needed)")
    if n_train < MIN_TRAIN:
        return f"not enough evidence: {n_train} trainable failure(s) < {MIN_TRAIN}"
    return None


def holdout_cases(holdout: list[Example]) -> list[dict]:
    """The held-out failures in prod_path_eval's case shape (scored by DOMAIN: any tool of the label's domain is right). The id is
    the wording's hash, never words."""
    return [{"id": f"h-{e.text_key}", "style": "struggle", "text": e.text, "expected": list(DOMAIN_TOOLS[e.label]),
             "domain": e.label} for e in holdout]


def per_case(payload: dict) -> dict[str, bool]:
    return {str(c["id"]): bool(c["ok"]) for c in (payload.get("cases") or [])}


@dataclass
class AdoptionDecision:
    adopt: bool
    checks: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    numbers: dict = field(default_factory=dict)


def decide_adoption(*, holdout_incumbent: dict, holdout_candidate: dict, golden_incumbent: dict, golden_candidate: dict,
                    incumbent: "rs.EvalScore", candidate: "rs.EvalScore", replay: "rs.ReplayVerdict",
                    forget_clean: bool) -> AdoptionDecision:
    """THE ADOPT RULE. Every check must hold; there is no override parameter and callers must not add one.

    (a) real improvement on the held-out failures, scored like-for-like:
        fixed  = incumbent wrong, candidate right;  broken = incumbent right, candidate wrong
        needs >= MIN_HOLDOUT cases (same ids both sides), the incumbent getting at least one wrong, and
        fixed - broken >= max(FIX_MIN_ABS, ceil(FIX_MIN_FRAC x incumbent misses)) - memorising the train split cannot pass this.
    (b) no regression anywhere else, per CASE on the frozen corpus (<= GOLDEN_MAX_BROKEN cases the incumbent answered may break,
        same case ids) and in aggregate (>=), chat false positives 0, p50 under budget, same case count;
    (c) the voice replay of the candidate RAN and passed (a skip is not a pass);
    (d) nothing the run trained or judged on was forgotten while it ran."""
    ids = sorted(holdout_incumbent)
    same_holdout = set(ids) == set(holdout_candidate)
    inc_wrong = [i for i in ids if not holdout_incumbent[i]]
    fixed = [i for i in ids if not holdout_incumbent[i] and holdout_candidate.get(i)]
    broken = [i for i in ids if holdout_incumbent[i] and not holdout_candidate.get(i)]
    need = max(FIX_MIN_ABS, math.ceil(FIX_MIN_FRAC * len(inc_wrong)))
    gids = sorted(golden_incumbent)
    same_golden = set(gids) == set(golden_candidate) and bool(gids)
    g_broken = [i for i in gids if golden_incumbent[i] and not golden_candidate.get(i)]
    g_fixed = [i for i in gids if not golden_incumbent[i] and golden_candidate.get(i)]
    checks = {
        "holdout_enough": len(ids) >= MIN_HOLDOUT and same_holdout,
        "fixes_known_failures": bool(inc_wrong) and same_holdout and (len(fixed) - len(broken)) >= need,
        "golden_no_case_regression": same_golden and len(g_broken) <= GOLDEN_MAX_BROKEN,
        "golden_aggregate_holds": candidate.overall_pct >= incumbent.overall_pct,
        "chat_fp_zero": candidate.chat_fp_pct <= rs.CHAT_FP_MAX_PCT,
        "p50_under_budget": candidate.p50_ms < rs.P50_MAX_MS,
        "corpus_intact": candidate.n == incumbent.n and candidate.n > 0,
        "replay_gate_passed": bool(replay.ran and replay.passed),
        "forget_clean": bool(forget_clean),
    }
    reasons = []
    if not checks["holdout_enough"]:
        reasons.append(f"held-out failures: {len(ids)} < {MIN_HOLDOUT} or the two sides scored different cases")
    if not checks["fixes_known_failures"]:
        reasons.append(f"does not fix the known failures: fixed {len(fixed)} - broken {len(broken)} < {need} "
                       f"(incumbent missed {len(inc_wrong)} of {len(ids)})")
    if not checks["golden_no_case_regression"]:
        reasons.append(f"frozen corpus: {len(g_broken)} case(s) the incumbent answers now fail (tolerance {GOLDEN_MAX_BROKEN})"
                       if same_golden else "frozen corpus: the two sides scored different cases")
    if not checks["golden_aggregate_holds"]:
        reasons.append(f"frozen corpus regression: {candidate.overall_pct}% < incumbent {incumbent.overall_pct}%")
    if not checks["chat_fp_zero"]:
        reasons.append(f"chat false positives {candidate.chat_fp_pct}% > 0")
    if not checks["p50_under_budget"]:
        reasons.append(f"latency p50 {candidate.p50_ms}ms >= {rs.P50_MAX_MS}ms")
    if not checks["corpus_intact"]:
        reasons.append(f"corpus mismatch {candidate.n} vs {incumbent.n}")
    if not checks["replay_gate_passed"]:
        reasons.append("voice replay " + ("did not run (a skip is NOT a pass)" if not replay.ran else "failed") + f": {replay.detail}")
    if not checks["forget_clean"]:
        reasons.append("an example this run used was forgotten while it ran: retrain without it")
    numbers = {"holdout_n": len(ids), "incumbent_holdout_wrong": len(inc_wrong), "fixed": len(fixed), "broken": len(broken),
               "needed": need, "fixed_ids": fixed, "broken_ids": broken,
               "golden_n": len(gids), "golden_broken": len(g_broken), "golden_fixed": len(g_fixed), "golden_broken_ids": g_broken,
               "incumbent_overall": incumbent.overall_pct, "candidate_overall": candidate.overall_pct,
               "candidate_chat_fp": candidate.chat_fp_pct, "candidate_p50_ms": candidate.p50_ms,
               "replay_ran": replay.ran, "replay_passed": replay.passed}
    return AdoptionDecision(adopt=all(checks.values()), checks=checks, reasons=reasons, numbers=numbers)


def window_conflict(start: datetime, minutes: float) -> Optional[str]:
    """None when [start, start + minutes] (local) overlaps none of the FORBIDDEN windows (checked over two days)."""
    end_ts = start.timestamp() + max(0.0, minutes) * 60.0
    for day in (0, 1):
        base = datetime(start.year, start.month, start.day).timestamp() + day * 86400.0
        for h1, m1, h2, m2 in FORBIDDEN:
            a = base + (h1 * 60 + m1) * 60.0
            b = base + (h2 * 60 + m2) * 60.0
            if start.timestamp() < b and end_ts > a:
                return f"the run ({start:%H:%M} + {minutes:.0f} min) would overlap the {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d} window"
    return None


def memory_plan(avail_mb: int, est_peak_mb: float, *, stop_brain_allowed: bool, floor_mb: int = MEM_FLOOR_MB) -> tuple[bool, bool, str]:
    """(may_train, must_stop_brain, why). Training must leave >= floor_mb available with everything else that is running."""
    if avail_mb - est_peak_mb >= floor_mb:
        return True, False, f"MemAvailable {avail_mb} MB - est. peak {est_peak_mb:.0f} MB >= {floor_mb} MB floor"
    if stop_brain_allowed:
        return True, True, (f"MemAvailable {avail_mb} MB - est. peak {est_peak_mb:.0f} MB < {floor_mb} MB floor: the brain is stopped "
                            "for the training window (re-checked after the stop)")
    return False, False, (f"MemAvailable {avail_mb} MB - est. peak {est_peak_mb:.0f} MB < {floor_mb} MB floor with the voice stack up, "
                          "and ZOE_ROUTER_RETRAIN_STOP_BRAIN is not set: not training now")


def replay_sample(rows: list[dict], n: int, seed: str) -> list[dict]:
    if n <= 0 or not rows:
        return []
    rng = random.Random(f"router-retrain|{seed}")
    return rng.sample(rows, min(n, len(rows)))


def train_rows(delta: list[Example], replay: list[dict], repeat: int = DELTA_REPEAT) -> list[dict]:
    rows = [{"text": e.text, "tool": e.tool, "args": e.args, "source": "struggle-followup"} for e in delta for _ in range(max(1, repeat))]
    return rows + replay


# ===========================================================================
# I/O
# ===========================================================================
log = rs.log


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def _jsonl(rows: Iterable[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


def _load_service_env() -> None:
    """POSTGRES_URL (and nothing else) from the live service .env when this process was not started with it. Never printed."""
    if not os.environ.get("POSTGRES_URL"):
        url = rs.read_env_var(rs.LIVE_SERVICE_DIR / ".env", "POSTGRES_URL")
        if url:
            os.environ["POSTGRES_URL"] = url


async def _collect_rows() -> list[dict]:
    import struggle_proposals as sp
    from db_pool import close_pool, init_pool

    await init_pool()
    try:
        return await sp.queued_router_examples(limit=500, sources=(sp.SOURCE_FOLLOWUP, sp.SOURCE_ROUTER), statuses=sp.SEAM_STATUSES)
    finally:
        await close_pool()


def trained_keys(data: dict) -> set:
    """Wordings a served (or previous) generation was trained on."""
    keep = {data.get("served"), data.get("previous")} - {None}
    return {x.get("k") for r in data.get("runs") or [] if r.get("gen") in keep for x in r.get("examples") or [] if x.get("s") == "train"}


def collect(data: dict) -> tuple[list[Example], list[Example], dict]:
    _load_service_env()
    rows = asyncio.run(_collect_rows())
    frozen = rs.frozen_corpus_texts(rs.FROZEN_CORPUS)
    examples, dropped = select_examples(rows, tombstones=data["tombstones"], frozen=frozen, normalize=rs._normalize)
    train, holdout, info = group_and_split(examples, trained_keys=trained_keys(data))
    by_source = Counter(r.get("label_source") for r in rows)
    by_status = Counter(r.get("status") for r in rows)
    report = {"seam_rows": len(rows), "by_label_source": dict(by_source), "by_status": dict(by_status),
              "dropped": dict(dropped), "split": {k: v for k, v in info.items()},
              "labels": dict(Counter(e.label for e in train + holdout))}
    return train, holdout, report


def oracle_filter(examples: list[Example]) -> tuple[list[Example], int]:
    """The independent second opinion (the miner's grammar-constrained brain oracle, told nothing): kept only on agreement."""
    sys.path.insert(0, str(REPO / "labs" / "router-selftrain"))
    import mine_candidates as mc

    cands = [mc.Candidate(text=e.text, reason="struggle-followup", domain=e.label) for e in examples]
    agreed = mc.label(cands, mc.make_brain_labeler())
    by_text = {}
    for c in agreed:
        by_text.setdefault(c.text, c)
    kept = []
    for e in examples:
        c = by_text.get(e.text)
        if c is not None and c.tool and TOOL_DOMAIN.get(c.tool) == e.label:
            e.tool, e.args = c.tool, dict(c.args)
            kept.append(e)
    return kept, len(examples) - len(kept)


def committed_rows() -> list[dict]:
    rows: list[dict] = []
    for name in ("train.jsonl", "train_sibling.jsonl", "train_round2.jsonl", "train_misses.jsonl"):
        p = rs.FINETUNE / "data" / name
        if p.exists():
            rows += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    return rows


def trainer_ready() -> Optional[str]:
    """None when the trainer interpreter can import the training stack; else why not (checked BEFORE the oracle spends brain time)."""
    probe = "import torch, transformers, peft, datasets"
    try:
        r = subprocess.run([rs.TRAIN_PYTHON, "-c", probe], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"trainer interpreter {rs.TRAIN_PYTHON} unusable ({type(exc).__name__}); set ZOE_ROUTER_TRAIN_PYTHON"
    if r.returncode != 0:
        return (f"trainer interpreter {rs.TRAIN_PYTHON} cannot import the training stack "
                f"({(r.stderr or '').strip().splitlines()[-1:]}); set ZOE_ROUTER_TRAIN_PYTHON")
    return None


def unit_active(unit: str) -> bool:
    return rs.unit_active(unit)


def panel_idle_minutes() -> Optional[float]:
    """Minutes since the last voice turn: the newest saved utterance or router shadow-log write (None = no evidence either way)."""
    newest = 0.0
    sample_dir = Path(os.environ.get("ZOE_VOICE_SAMPLE_DIR") or (Path.home() / ".zoe-voice-samples"))
    try:
        for p in sample_dir.iterdir():
            if p.suffix == ".wav":
                newest = max(newest, p.stat().st_mtime)
    except OSError:
        pass
    log_path = Path(os.environ.get("ZOE_ROUTER_HEAD_LOG") or (rs.LIVE_SERVICE_DIR / "data" / "router_head_shadow.jsonl"))
    for p in [log_path] + [log_path.with_name(f"{log_path.name}.{i}") for i in (1,)]:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return None if newest <= 0 else (time.time() - newest) / 60.0


def served_record(data: dict) -> Optional[dict]:
    """The ledger's served generation, only if the sidecar file IS that model (another loop or a hand rollback may have swapped it)."""
    gen = data.get("served")
    rec = next((r for r in data.get("runs") or [] if gen and r.get("gen") == gen), None)
    if rec is None or not rs.SERVED_GGUF.exists():
        return None
    return rec if rs.sha256(rs.SERVED_GGUF) == rec.get("gguf_sha256") else None


def warm_start_for(data: dict) -> Path:
    rec = served_record(data)
    if rec and rec.get("merged") and Path(rec["merged"]).exists():
        return Path(rec["merged"])
    return rs.WARM_START


def estimates(data: dict) -> tuple[float, float]:
    peaks = [float(r["measure"]["peak_rss_mb"]) for r in data.get("runs") or [] if (r.get("measure") or {}).get("peak_rss_mb")]
    mins = [float(r["measure"]["total_minutes"]) for r in data.get("runs") or [] if (r.get("measure") or {}).get("total_minutes")]
    return (max(peaks) * 1.15 if peaks else DEFAULT_EST_PEAK_MB), (max(mins) * 1.2 if mins else DEFAULT_EST_MINUTES)


def brain_marker() -> Path:
    return ledger.root() / "brain_stopped_by_retrain.json"


def _stop_brain() -> None:
    brain_marker().parent.mkdir(parents=True, exist_ok=True)
    brain_marker().write_text(json.dumps({"at": time.time(), "unit": rs.BRAIN_UNIT}))
    rs.systemctl("stop", rs.BRAIN_UNIT)
    time.sleep(3)


def _restore_brain() -> bool:
    rs.systemctl("start", rs.BRAIN_UNIT, check=False)
    ok = rs.wait_for(lambda: rs.http_ok(rs.BRAIN_HEALTH), timeout=180)
    if ok:
        brain_marker().unlink(missing_ok=True)
    return ok


def train_candidate(train_file: Path, warm: Path, run_dir: Path, *, stop_brain: bool, journal: dict) -> Path:
    """One CPU continuation run, MEASURED: wall minutes, the trainer's own peak RSS (wait4), the lowest MemAvailable seen. A
    watchdog terminates the trainer if MemAvailable falls under MEM_FLOOR_MB. A brain this run stopped is ALWAYS restarted."""
    log("=== TRAIN (continuation) ===")
    stopped = False
    procs: list[subprocess.Popen] = []
    measure: dict[str, Any] = {"warm_start": str(warm), "mem_before_mb": rs.mem_available_mb()}
    try:
        if stop_brain and unit_active(rs.BRAIN_UNIT):
            log(f"stopping {rs.BRAIN_UNIT} for the training window")
            _stop_brain()
            stopped = True
        measure["mem_at_start_mb"] = rs.mem_available_mb()
        cmd = ["nice", "-n", "10", rs.TRAIN_PYTHON, str(rs.FINETUNE / "train_lora.py"), "--variant", "functok", "--cpu",
               "--model-dir", str(warm), "--data", str(train_file), "--out", str(run_dir),
               "--epochs", str(EPOCHS), "--lr", str(LR)]

        def _deprioritize() -> None:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("1000")

        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=str(REPO), preexec_fn=_deprioritize)
        procs.append(proc)
        low = {"min": rs.mem_available_mb(), "killed": False}
        done = threading.Event()

        def _watch() -> None:
            while not done.wait(5.0):
                m = rs.mem_available_mb()
                low["min"] = min(low["min"], m)
                if m < MEM_FLOOR_MB and proc.poll() is None:
                    low["killed"] = True
                    proc.terminate()

        threading.Thread(target=_watch, daemon=True).start()
        _, status, usage = os.wait4(proc.pid, 0)
        done.set()
        proc.returncode = os.waitstatus_to_exitcode(status)
        measure.update({"minutes": round((time.time() - t0) / 60.0, 1), "peak_rss_mb": round(usage.ru_maxrss / 1024.0),
                        "min_mem_available_mb": low["min"], "killed_low_memory": low["killed"], "stopped_brain": stopped,
                        "returncode": proc.returncode})
        journal["train"] = measure
        log(f"train: {measure['minutes']} min, peak RSS {measure['peak_rss_mb']} MB, lowest MemAvailable {low['min']} MB")
        if low["killed"]:
            raise SystemExit(f"ABORT: MemAvailable fell under {MEM_FLOOR_MB} MB - trainer terminated")
        if proc.returncode != 0:
            raise SystemExit(f"ABORT: training failed rc={proc.returncode}")
    finally:
        for p in procs:                       # a SIGTERM during wait4 unwinds here with the trainer still running: stop it first
            if p.returncode is None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    p.kill()
        if stopped:
            log(f"restoring {rs.BRAIN_UNIT}")
            if not _restore_brain():
                log(f"WARNING: {rs.BRAIN_UNIT} did not report healthy - CHECK THE BOX (--recover retries)")
    merged = run_dir / "merged"
    if not merged.exists():
        raise SystemExit(f"ABORT: no merged checkpoint at {merged}")
    return merged


def _eval(gguf: Path, port: int, out: Path, launch: bool, label: str, corpus: Optional[Path] = None) -> tuple["rs.EvalScore", dict]:
    score = rs.run_eval(gguf, port, out, launch=launch, label=label, corpus=corpus)
    payload = json.loads(out.read_text())
    if corpus is not None:
        # the cases came from household words (and the router's args are cut from them): keep an ALLOWLIST of id / verdict fields on
        # disk, so a future harness that echoes more (the text) cannot leak it into the persistent runs/ dir
        payload["cases"] = [{k: c.get(k) for k in _CASE_KEEP} for c in payload.get("cases") or []]
        _write_private(out, json.dumps(payload, indent=1))
    return score, payload


_CASE_KEEP = ("id", "style", "ok", "predicted", "source", "confidence", "latency_ms", "router_latency_ms")


def _new_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _strike_forgotten(d: dict, rec: dict) -> None:
    """A forget that landed while the run was in flight never reached this record (it was not in the ledger yet): drop those examples
    as the forget would have, so no record names a forgotten turn."""
    if rec.get("examples"):
        gone = ledger.tombstoned(rec["examples"], d)
        if gone:
            rec["examples"] = [x for x in rec["examples"] if x not in gone]
            rec["forgotten"] = int(rec.get("forgotten") or 0) + len(gone)


def _record(rec: dict) -> None:
    def _add(d: dict) -> None:
        _strike_forgotten(d, rec)
        d["runs"].append(rec)
    ledger.update(_add)


def _journal(stamp: str, journal: dict) -> Path:
    p = ledger.root() / "runs" / f"{stamp}.json"
    _write_private(p, json.dumps(journal, indent=1, default=str))
    return p


def _apply_proposals(gen: str, train: list[Example]) -> dict:
    """`on` only, after a verified adoption: the proposals the generation LEARNED from move to applied (a still-proposed one is accepted
    by the loop's policy first, so the record says who decided)."""
    import struggle_proposals as sp
    from db_pool import close_pool, init_pool

    async def _go() -> dict:
        res = Counter()
        await init_pool()
        try:
            for pid in sorted({e.proposal_id for e in train if e.proposal_id}):
                try:
                    p = await sp.get_proposal(pid, with_evidence=False)
                    if p.get("status") == sp.PROPOSED:
                        await sp.decide(pid, "accept", POLICY_DECIDER)
                    await sp.mark_applied(pid, gen)
                    res["applied"] += 1
                except Exception as exc:  # noqa: BLE001 - a proposal that moved meanwhile stays as it is; counted
                    res[type(exc).__name__] += 1
        finally:
            await close_pool()
        return dict(res)
    return asyncio.run(_go())


def run(mode: str, *, collect_only: bool = False) -> int:
    stamp = _new_stamp()
    journal: dict[str, Any] = {"stamp": stamp, "mode": mode, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    data = ledger.load()
    t_start = time.time()

    # ---- 1. COLLECT -------------------------------------------------------
    train, holdout, report = collect(data)
    journal["collect"] = report
    short = evidence_shortfall(len(train), len(holdout))
    log(f"COLLECT: {report['seam_rows']} seam rows -> {len(train)} train / {len(holdout)} held-out wordings; dropped {report['dropped']}")
    if collect_only:
        print(json.dumps({"collect": report, "verdict": short or "enough evidence to train"}, sort_keys=True, default=str))
        return 0
    rec: dict[str, Any] = {"stamp": stamp, "mode": mode, "collect": report}
    if short:
        log(f"NOT TRAINING: {short}")
        rec.update(status="insufficient", outcome=short)
        journal["outcome"] = short
        _record(rec)
        _journal(stamp, journal)
        print(f"ROUTER_RETRAIN mode={mode} outcome=insufficient train={len(train)} holdout={len(holdout)}")
        return 0

    # ---- where / when may it run? -------------------------------------------
    est_peak, est_min = estimates(data)
    if unit_active(NIGHT_UNIT):
        raise SystemExit("ABORT: the night window is running - the 12B owns the box")
    clash = window_conflict(datetime.now(), est_min)
    if clash:
        raise SystemExit(f"ABORT: {clash}")
    idle = panel_idle_minutes()
    if idle is not None and idle < PANEL_IDLE_MIN:
        why = f"a voice turn {idle:.0f} min ago (< {PANEL_IDLE_MIN} min): the panel is in use"
        rec.update(status="deferred", outcome=why)
        journal["outcome"] = why
        _record(rec)
        _journal(stamp, journal)
        print(f"ROUTER_RETRAIN mode={mode} outcome=deferred_panel_in_use train={len(train)} holdout={len(holdout)}")
        return 0
    stop_ok = (os.environ.get("ZOE_ROUTER_RETRAIN_STOP_BRAIN", "0").strip().lower() in ("1", "true", "yes", "on")
               and idle is not None and idle >= PANEL_IDLE_MIN)
    may, must_stop, why = memory_plan(rs.mem_available_mb(), est_peak, stop_brain_allowed=stop_ok)
    journal["plan"] = {"est_peak_mb": est_peak, "est_minutes": est_min, "panel_idle_min": idle, "stop_brain_allowed": stop_ok, "why": why}
    log(f"PLAN: {why}")
    if not may:
        rec.update(status="deferred", outcome=why)
        _record(rec)
        _journal(stamp, journal)
        print(f"ROUTER_RETRAIN mode={mode} outcome=deferred_memory train={len(train)} holdout={len(holdout)}")
        return 0

    trainer = trainer_ready()
    if trainer:
        raise SystemExit(f"ABORT: {trainer}")
    work = ledger.root() / ledger.WORK / stamp
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    texts_used = train + holdout
    _write_private(work / ledger.IDS_FILE, json.dumps({"event_ids": sorted({e.event_id for e in texts_used}),
                                                       "user_keys": sorted({e.user_key for e in texts_used})}))
    try:
        # ---- 2. ORACLE --------------------------------------------------------
        train, n_tr_drop = oracle_filter(train)
        holdout, n_ho_drop = oracle_filter(holdout)
        journal["oracle"] = {"train_kept": len(train), "train_dropped": n_tr_drop, "holdout_kept": len(holdout), "holdout_dropped": n_ho_drop}
        short = evidence_shortfall(len(train), len(holdout))
        if short:
            rec.update(status="insufficient", outcome="after the oracle: " + short, oracle=journal["oracle"])
            _record(rec)
            print(f"ROUTER_RETRAIN mode={mode} outcome=insufficient_after_oracle train={len(train)} holdout={len(holdout)}")
            return 0
        rec["examples"] = [e.ledger_row("train") for e in train] + [e.ledger_row("holdout") for e in holdout]

        # ---- 3. TRAIN -----------------------------------------------------------
        replay_rows = replay_sample(committed_rows(), REPLAY_SAMPLE, stamp)
        rows = train_rows(train, replay_rows)
        ok, reasons = rs.check_heldout_guard({"held_out_guard": {"result": "pass", "collisions": 0}},
                                             [r["text"] for r in rows], rs.frozen_corpus_texts(rs.FROZEN_CORPUS))
        if not ok:
            raise SystemExit("ABORT: held-out guard: " + "; ".join(reasons))
        train_file = work / f"train{ledger.TEXT_SUFFIX}"
        _write_private(train_file, _jsonl(rows))
        hold_file = work / f"holdout{ledger.TEXT_SUFFIX}"
        _write_private(hold_file, _jsonl(holdout_cases(holdout)))
        warm = warm_start_for(data)
        if not warm.exists():
            raise SystemExit(f"ABORT: warm-start checkpoint {warm} is missing (the lineage rule: never cold-start silently)")
        run_dir = work / "run"
        try:
            merged = train_candidate(train_file, warm, run_dir, stop_brain=must_stop, journal=journal)
        finally:
            if journal.get("train"):
                rec["measure"] = dict(journal["train"])     # the measurement survives a run that fails later (the next plan uses it)
        train_file.unlink(missing_ok=True)     # the words leave disk as soon as the trainer has read them
        cand_gguf = rs.export_gguf(merged, stamp, journal, out=work / f"candidate_{stamp}-Q8_0.gguf")

        # ---- 4. EVAL (same scratch rig, back to back) ---------------------------
        if rs.mem_available_mb() < rs.MIN_EVAL_MEM_MB:
            raise SystemExit(f"ABORT: MemAvailable {rs.mem_available_mb()} MB < {rs.MIN_EVAL_MEM_MB} MB for the scratch rig")
        rdir = ledger.root() / "runs"
        inc_live, _ = _eval(rs.SERVED_GGUF, rs.LIVE_PORT, rdir / f"{stamp}-incumbent-live.json", False, "incumbent-live")
        inc, inc_g = _eval(rs.SERVED_GGUF, rs.SCRATCH_PORT, rdir / f"{stamp}-incumbent.json", True, "incumbent-scratch")
        valid, why = rs.check_measurement_validity(inc_live, inc)
        journal["measurement_validity"] = {"ok": valid, "reason": why}
        if not valid:
            raise SystemExit(f"ABORT (inconclusive): {why}")
        cand, cand_g = _eval(cand_gguf, rs.SCRATCH_PORT, rdir / f"{stamp}-candidate.json", True, "candidate")
        _, inc_h = _eval(rs.SERVED_GGUF, rs.SCRATCH_PORT, rdir / f"{stamp}-incumbent-holdout.json", True, "incumbent-holdout", hold_file)
        _, cand_h = _eval(cand_gguf, rs.SCRATCH_PORT, rdir / f"{stamp}-candidate-holdout.json", True, "candidate-holdout", hold_file)
        hold_file.unlink(missing_ok=True)
        replay = rs.replay_gate(journal, candidate_gguf=cand_gguf,
                                results=rdir / f"{stamp}-replay.json", trend=ledger.root() / "replay_trend.jsonl")

        # ---- 5. GATE --------------------------------------------------------------
        now = ledger.load()
        forget_clean = not ledger.tombstoned(rec["examples"], now)
        decision = decide_adoption(holdout_incumbent=per_case(inc_h), holdout_candidate=per_case(cand_h),
                                   golden_incumbent=per_case(inc_g), golden_candidate=per_case(cand_g),
                                   incumbent=inc, candidate=cand, replay=replay, forget_clean=forget_clean)
        journal["gate"] = asdict(decision)
        for name, ok in decision.checks.items():
            log(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        for r in decision.reasons:
            log(f"  reason: {r}")
        n = decision.numbers
        rec.update(gate={"checks": decision.checks, "reasons": decision.reasons, "numbers": n},
                   gguf=str(cand_gguf), gguf_sha256=rs.sha256(cand_gguf), measure={**journal.get("train", {}),
                   "total_minutes": round((time.time() - t_start) / 60.0, 1)}, warm_start=str(warm),
                   parent=(served_record(data) or {}).get("gen"))

        # ---- 6. ADOPT (on only) ----------------------------------------------------
        act(mode, decision, rec, stamp=stamp, cand_gguf=cand_gguf, run_dir=run_dir, inc_live=inc_live, train=train, journal=journal)
        print(f"ROUTER_RETRAIN mode={mode} outcome={rec.get('status')} train={len(train)} "
              f"holdout={n['holdout_n']} incumbent_missed={n['incumbent_holdout_wrong']} fixed={n['fixed']} broken={n['broken']} "
              f"golden_broken={n['golden_broken']} replay={'pass' if n['replay_passed'] else ('fail' if n['replay_ran'] else 'skip')}")
        return 0
    except SystemExit as exc:
        if not rec.get("status"):
            rec.update(status="aborted", outcome=str(exc)[:300])
            _record(rec)
        journal["outcome"] = f"aborted: {str(exc)[:300]}"
        raise
    finally:
        for f in work.glob(f"*{ledger.TEXT_SUFFIX}"):
            f.unlink(missing_ok=True)                                 # no household words outlive the run
        shutil.rmtree(work / "run", ignore_errors=True)               # a candidate that did not become a generation is scratch
        if rec.get("status") != "shadow":
            for g in work.glob("candidate_*.gguf"):                   # a shadow candidate stays (newest only) for inspection
                g.unlink(missing_ok=True)
        _prune_work(keep=1)
        journal["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _journal(stamp, journal)


def act(mode: str, decision: AdoptionDecision, rec: dict, **kw) -> str:
    """The ONLY place a candidate can reach the live sidecar: mode ``on`` AND every gate passed. ``shadow`` records what it WOULD have
    done and never swaps; a failed gate records why. Returns the recorded status."""
    if mode == "on" and decision.adopt:
        adopt_generation(rec, **kw)
    else:
        rec.update(status="shadow" if mode != "on" else "rejected",
                   outcome=("shadow: " if mode != "on" else "") + ("WOULD ADOPT" if decision.adopt else "not adopted: " + "; ".join(decision.reasons)))
        log(f"{'SHADOW' if mode != 'on' else 'REJECTED'}: {rec['outcome']}")
        _record(rec)
    return str(rec.get("status"))


def adopt_generation(rec: dict, *, stamp: str, cand_gguf: Path, run_dir: Path, inc_live: "rs.EvalScore", train: list[Example],
                     journal: dict) -> None:
    """`on` + every gate passed: router_selftrain.deploy (LKG archive copied first, atomic swap, restart, identity check, post-deploy
    LIVE eval, auto-rollback on any failure). Only a verified deploy becomes a GENERATION: its checkpoint moves out of the scratch
    work dir (never deleted - the lineage rule), the ledger names it served and the old one previous (instant rollback), and the
    proposals it learned from move to applied."""
    gen = f"g{stamp}"
    if ledger.tombstoned(rec.get("examples") or []):
        # the gate's forget check is a snapshot; a forget that landed since must still stop the swap
        rec.update(status="rejected", outcome="not adopted: an example this run used was forgotten just before the swap")
        _record(rec)
        return
    rc = rs.deploy(cand_gguf, stamp, inc_live, journal)
    if rc != 0:
        rec.update(status="rolled_back_at_deploy", outcome="post-deploy check failed: auto-rolled back to the last-known-good")
        _record(rec)
        return
    gdir = ledger.root() / "generations" / gen
    gdir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(gdir))
    shutil.copy2(cand_gguf, gdir / cand_gguf.name)
    rec.update(status="adopted", gen=gen, merged=str(gdir / "merged"), gguf=str(gdir / cand_gguf.name),
               lkg=(journal.get("deploy") or {}).get("last_known_good"), outcome="adopted")

    def _serve(d: dict) -> None:
        _strike_forgotten(d, rec)
        d["runs"].append(rec)
        d["previous"], d["served"] = d.get("served"), gen
    ledger.update(_serve)
    try:
        applied = _apply_proposals(gen, train)
    except Exception as exc:  # noqa: BLE001 - the model is live and recorded; a proposal bookkeeping failure is reported, not fatal
        applied = {"error": type(exc).__name__}

    def _note(d: dict) -> None:
        for r in d["runs"]:
            if r.get("gen") == gen:
                r["applied"] = applied
    ledger.update(_note)
    rec["applied"] = applied


def _prune_work(keep: int = 1) -> None:
    """Old work dirs go (only the newest `keep` shadow candidate GGUF stays for inspection). Generations live elsewhere."""
    work = ledger.root() / ledger.WORK
    if not work.exists():
        return
    dirs = sorted((p for p in work.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in dirs[:-max(1, keep)]:
        shutil.rmtree(old, ignore_errors=True)


def rollback() -> int:
    """Instant rollback: the served generation's outgoing model (its LKG archive) goes back; the ledger names the previous
    generation as served again. Uses router_selftrain.restore_lkg (validated GGUF, archive-only source, restart + identity check)."""
    data = ledger.load()
    gen = data.get("served")
    rec = next((r for r in data.get("runs") or [] if gen and r.get("gen") == gen), None)
    if rec is None or not rec.get("lkg"):
        log("nothing to roll back: no generation of this loop is recorded as served")
        return 1
    if not rs.restore_lkg(Path(rec["lkg"])):
        log("ROLLBACK DID NOT VERIFY - the sidecar was not (or not provably) restored; see the log above")
        return 1

    def _back(d: dict) -> None:
        for r in d.get("runs") or []:
            if r.get("gen") == gen:
                r["status"] = "rolled_back"
                r["rolled_back_at"] = round(time.time(), 3)
        d["served"], d["previous"] = d.get("previous"), None
    ledger.update(_back)
    log(f"ROLLED BACK generation {gen}; served is now {ledger.load().get('served') or 'the pre-loop model'}")
    return 0


def recover() -> int:
    """After a kill: restart a brain THIS loop stopped (never one it did not - the night window owns the brain while it runs), then
    router_selftrain's interrupted-deploy recovery (its on-disk marker; idempotent)."""
    rc = 0
    if brain_marker().exists():
        if unit_active(NIGHT_UNIT):
            log("brain was stopped by this loop, but the night window is running now: leaving it to the window")
        elif _restore_brain():
            log("brain restored")
        else:
            log("OPERATOR ACTION REQUIRED: the brain did not come back healthy")
            rc = 1
    if rs.DEPLOY_MARKER.exists():
        rc = max(rc, rs.recover())
    return rc


def _install_signal_handlers() -> None:
    def _bail(signum, _frame):
        raise SystemExit(f"ABORT: received signal {signum} - unwinding so the brain and sidecar are restored")
    for sig_ in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig_, _bail)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collect-only", action="store_true", help="count the labelled failures and the split; write nothing")
    ap.add_argument("--shadow", action="store_true", help="run the whole loop in shadow (never swaps), whatever the flag says")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--recover", action="store_true")
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a.startswith("--force") for a in argv):
        raise SystemExit("There is no force/override flag. A candidate that cannot pass the gate is not safe to serve.")
    args = ap.parse_args(argv)
    _install_signal_handlers()
    if args.status:
        print(json.dumps(ledger.status(), sort_keys=True))
        return 0
    if args.recover:
        return recover()
    if args.rollback:
        return rollback()
    mode = effective_mode(feed_mode(), args.shadow)
    if args.collect_only:
        return run(mode if mode != "off" else "shadow", collect_only=True)
    if mode == "off":
        print(f"ROUTER_RETRAIN mode=off ({FLAG} is off; --shadow runs a shadow pass)")
        return 0
    import fcntl

    ledger.root().mkdir(parents=True, exist_ok=True)
    os.chmod(ledger.root(), 0o700)
    with open(ledger.root() / "run.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("ROUTER_RETRAIN another run holds the lock")
            return 0
        if brain_marker().exists() or rs.DEPLOY_MARKER.exists():
            log("a previous run was interrupted - recovering first")
            if recover() != 0:
                raise SystemExit("ABORT: could not recover from an interrupted run")
        return run(mode)


if __name__ == "__main__":
    sys.exit(main())
