#!/usr/bin/env python3
"""Router self-training loop — TRAIN → EVAL → RATCHET-PROMOTE → DEPLOY.

Zoe's two-stage router retrains itself on real-traffic mistakes (the candidate
dataset mined by the traffic-mining lane) and promotes the retrained model ONLY
if it is provably better than the model currently in the sidecar. The promotion
ratchet is what makes this safe rather than drift-prone: a candidate that
regresses accuracy, invents a tool call on a chat turn, blows the latency
budget, or fails the voice replay gate is DISCARDED and the incumbent stays.

There is deliberately NO override flag. Any "force promote" would defeat the
only thing standing between a self-training loop and silent quality collapse.

Stages (each recorded in the run journal, data/router_selftrain/runs/<stamp>.json):

  1. PRE-FLIGHT  candidate dataset + held-out guard, disk/mem, and a LIVE
                 re-measurement of the incumbent (never a stale results file —
                 the incumbent is whatever the sidecar is actually serving).
  2. TRAIN       warm-start from the production checkpoint lineage on
                 (existing training sets + the candidate), CPU-only, nice'd,
                 oom_score_adj=1000. Stops llama-server for the window if the
                 box is too tight, and ALWAYS restores it.
  3. EXPORT      merged checkpoint → GGUF, via the pristine-tokenizer recipe
                 (transformers' re-serialized tokenizer breaks the converter).
  4. EVAL        the frozen 81-case corpus through the real production router,
                 pointed at a SCRATCH sidecar on :11437. The live :11436
                 sidecar is never disturbed during eval.
  5. RATCHET     promote iff EVERY gate holds (see decide_promotion). Else the
                 candidate is discarded and the incumbent keeps serving.
  6. DEPLOY      archive the outgoing GGUF as last-known-good, swap the served
                 model file, restart the sidecar unit, verify health + identity,
                 then RE-RUN the eval against the LIVE sidecar to confirm the
                 promoted numbers in situ. Any post-deploy failure →
                 AUTO-ROLLBACK to last-known-good, restart, verify, exit loud.
  7. REPORT      append to the scoreboard so the loop's history is auditable.

The loop's ONLY production mutation is swapping the sidecar's model file and
restarting that one unit. It never edits the live routing code path.

Usage (repo root):
    # full loop (the scheduled job runs exactly this)
    python3 scripts/maintenance/router_selftrain.py

    # mine + train + eval, never promote — safe to run any time
    python3 scripts/maintenance/router_selftrain.py --dry-run

Flag: ZOE_ROUTER_SELFTRAIN (default off) gates the weekly scheduled job in
zoe-data. See docs/knowledge/router-selftrain-loop.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LABS = REPO / "labs"
FINETUNE = LABS / "functiongemma-finetune"
CAMPAIGN = LABS / "router-90-campaign"

# --- data / journal ---------------------------------------------------------
SELFTRAIN_DIR = REPO / "data" / "router_selftrain"
RUNS_DIR = SELFTRAIN_DIR / "runs"
SCOREBOARD = SELFTRAIN_DIR / "scoreboard.jsonl"
# Crash-safety intent record. Written to disk BEFORE the served model file is
# swapped and removed only once the new model is verified live (or rolled back).
# If this file exists, a deploy was interrupted and the sidecar may be serving an
# UNVERIFIED model — recover() undoes it. This cannot live in memory: the
# scheduler's timeout kills the child with SIGKILL, which no in-process handler
# can catch, so the only thing that survives is what is on disk.
DEPLOY_MARKER = SELFTRAIN_DIR / "deploy_in_progress.json"

# --- the frozen eval corpus (NEVER a training input) ------------------------
FROZEN_CORPUS = LABS / "needle-benchmark" / "corpus.jsonl"

# --- models -----------------------------------------------------------------
BASE_HF = Path(os.environ.get(
    "ZOE_ROUTER_BASE_HF", "/home/zoe/models/lab/functiongemma-270m-it-hf"))
# The checkpoint the CURRENT production model descends from. Warm-starting from
# the production lineage is what keeps each generation a small step rather than
# a fresh model with unknown behaviour.
WARM_START = Path(os.environ.get(
    "ZOE_ROUTER_WARM_START",
    str(FINETUNE / "runs" / "functok-r2" / "merged")))
SERVED_DIR = Path(os.environ.get(
    "ZOE_ROUTER_MODEL_DIR", "/home/zoe/models/functiongemma-router"))
# The systemd unit hardcodes this filename — swapping the FILE at this path (not
# editing the unit) is how a new model goes live.
SERVED_GGUF = SERVED_DIR / "functiongemma-270m-zoe-functok-r2-Q8_0.gguf"
ARCHIVE_DIR = SERVED_DIR / "archive"
PROVENANCE = SERVED_DIR / "provenance.json"
SIDECAR_UNIT = "functiongemma-router.service"
BRAIN_UNIT = os.environ.get("ZOE_BRAIN_UNIT", "llama-server.service")

LIVE_PORT = 11436
SCRATCH_PORT = int(os.environ.get("ZOE_ROUTER_SCRATCH_PORT", "11437"))
BRAIN_HEALTH = "http://127.0.0.1:11434/health"
# The replay probe must run against the LIVE service dir: it loads that .env for
# the voice path, and measure_voice silently takes a skip path (exit 0, no
# output) when the dir has no .env — which a git worktree never does. Pointing it
# at a worktree makes the gate quietly un-passable.
LIVE_SERVICE_DIR = Path(os.environ.get(
    "ZOE_LIVE_SERVICE_DIR", "/home/zoe/assistant/services/zoe-data"))

# --- ratchet gates ----------------------------------------------------------
CHAT_FP_MAX_PCT = 0.0
P50_MAX_MS = 600.0
# The incumbent is measured on BOTH the live sidecar and the scratch rig. If the
# two disagree by more than this, the box is too contended for the corpus to mean
# anything and the run is INCONCLUSIVE (see check_measurement_validity).
MAX_RIG_DRIFT_PCT = 10.0
# Post-deploy the live numbers are re-measured on a box that is no longer
# running a trainer; allow a small measurement band before calling it a
# regression, but never allow a hard gate (chat-FP / p50) to fail.
POST_DEPLOY_TOLERANCE_PCT = 2.0

MIN_FREE_DISK_MB = 4096
MIN_TRAIN_MEM_MB = 2048  # train_lora.py refuses to start below this
MIN_EVAL_MEM_MB = 1600   # prod_path_eval aborts below 1500; fail fast before that

# Nothing is ever copied onto the LIVE served model path without passing this.
# Learned the hard way: a stale marker pointing at a stray file let a 9-BYTE file
# be written over the live 292 MB router GGUF, taking the sidecar down. Any file
# heading for the served path must be a real GGUF of a plausible size, and any
# last-known-good must come from OUR archive dir — never an arbitrary path.
GGUF_MAGIC = b"GGUF"
MIN_GGUF_BYTES = 50 * 1024 * 1024  # the 270M Q8_0 is ~292 MB
ARCHIVE_KEEP = int(os.environ.get("ZOE_ROUTER_ARCHIVE_KEEP", "3"))


# ===========================================================================
# Pure ratchet logic — no I/O, no heavy imports. Unit-tested directly.
# ===========================================================================
@dataclass
class EvalScore:
    """A frozen-corpus measurement of one model through the production router."""
    overall_pct: float
    chat_fp_pct: float
    p50_ms: float
    n: int
    source: str = ""  # where the numbers came from (live sidecar / scratch)

    @classmethod
    def from_summary(cls, summary: dict, source: str = "") -> "EvalScore":
        return cls(
            overall_pct=float(summary["accuracy_overall_pct"] or 0.0),
            chat_fp_pct=float(summary["chat_false_positive_pct"] or 0.0),
            p50_ms=float(summary["latency_ms_p50"]),
            n=int(summary["n"]),
            source=source,
        )


@dataclass
class ReplayVerdict:
    """Outcome of the voice replay gate.

    `ran` is tracked separately from `passed` on purpose: the probe exits 0 when
    it SKIPS (e.g. the box is too tight to replay safely). A skip is not a pass —
    treating it as one would let a model through the gate having never been
    replayed. Only ran-and-passed counts.
    """
    ran: bool
    passed: bool
    detail: str = ""


@dataclass
class PromotionDecision:
    promote: bool
    checks: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def decide_promotion(incumbent: EvalScore,
                     candidate: EvalScore,
                     replay: ReplayVerdict) -> PromotionDecision:
    """The ratchet. Promote ONLY if every condition holds.

    - no accuracy regression vs the incumbent (>=, so a tie promotes: the
      candidate has strictly more training signal at equal measured quality)
    - zero chat false positives (a tool call on a chat turn is the worst
      failure mode this router has — it hijacks a conversational turn)
    - p50 under the latency budget
    - the voice replay gate actually ran and passed
    - the corpus was not truncated (same case count both sides)

    There is no bypass parameter, and callers must not add one.
    """
    checks = {
        "no_accuracy_regression": candidate.overall_pct >= incumbent.overall_pct,
        "chat_fp_zero": candidate.chat_fp_pct <= CHAT_FP_MAX_PCT,
        "p50_under_budget": candidate.p50_ms < P50_MAX_MS,
        "replay_gate_passed": bool(replay.ran and replay.passed),
        "corpus_intact": candidate.n == incumbent.n and candidate.n > 0,
    }
    reasons = []
    if not checks["no_accuracy_regression"]:
        reasons.append(
            f"accuracy regression: candidate {candidate.overall_pct}% < "
            f"incumbent {incumbent.overall_pct}%")
    if not checks["chat_fp_zero"]:
        reasons.append(
            f"chat false positives: {candidate.chat_fp_pct}% > 0 — the router "
            "would hijack conversational turns with tool calls")
    if not checks["p50_under_budget"]:
        reasons.append(
            f"latency: p50 {candidate.p50_ms}ms >= {P50_MAX_MS}ms budget")
    if not checks["replay_gate_passed"]:
        why = "did not run (a skip is NOT a pass)" if not replay.ran else "failed"
        reasons.append(f"voice replay gate {why}: {replay.detail}")
    if not checks["corpus_intact"]:
        reasons.append(
            f"corpus mismatch: candidate scored {candidate.n} cases vs "
            f"incumbent {incumbent.n} — refusing to compare different corpora")
    return PromotionDecision(promote=all(checks.values()),
                             checks=checks, reasons=reasons)


def decide_rollback(post: EvalScore, baseline_live: EvalScore) -> PromotionDecision:
    """Post-deploy confirmation, on the LIVE sidecar.

    The comparison is against the INCUMBENT'S OWN LIVE SCORE — the model this one
    just replaced, measured on the same rig. (Comparing a live number against the
    candidate's scratch-rig number would be apples-to-oranges; see
    check_measurement_validity.) The production question is simply: is the live
    system at least as good as it was before the swap?

    `promote` here means "the deploy is good". False ⇒ roll back.
    """
    checks = {
        "chat_fp_zero": post.chat_fp_pct <= CHAT_FP_MAX_PCT,
        "p50_under_budget": post.p50_ms < P50_MAX_MS,
        "accuracy_holds": post.overall_pct >= baseline_live.overall_pct - POST_DEPLOY_TOLERANCE_PCT,
        "corpus_intact": post.n == baseline_live.n and post.n > 0,
    }
    reasons = []
    if not checks["chat_fp_zero"]:
        reasons.append(f"post-deploy chat-FP {post.chat_fp_pct}% > 0")
    if not checks["p50_under_budget"]:
        reasons.append(f"post-deploy p50 {post.p50_ms}ms >= {P50_MAX_MS}ms")
    if not checks["accuracy_holds"]:
        reasons.append(
            f"post-deploy accuracy {post.overall_pct}% below the incumbent's own "
            f"live {baseline_live.overall_pct}% - {POST_DEPLOY_TOLERANCE_PCT}% tolerance")
    if not checks["corpus_intact"]:
        reasons.append(f"post-deploy corpus mismatch: {post.n} vs {baseline_live.n}")
    return PromotionDecision(promote=all(checks.values()),
                             checks=checks, reasons=reasons)


def check_measurement_validity(live: EvalScore, scratch: EvalScore,
                               max_drift_pct: float = MAX_RIG_DRIFT_PCT) -> tuple:
    """Is the box quiet enough right now for the numbers to mean anything?

    MEASURED on this Jetson: the SAME GGUF scores 86.4% / 488 ms on the live
    warm sidecar, 87.7% / 590 ms on a quiet scratch sidecar — and 71.6% / 796 ms
    on a CONTENDED one. Decode latency crosses the router's 1.5 s timeout and the
    turns become error_fallbacks, so frozen-corpus accuracy swings ~16 points
    with load alone.

    That noise is bigger than any improvement a retrain is likely to produce. So
    the incumbent is measured on BOTH rigs and the two are compared: if they
    disagree by more than max_drift_pct the box is too loaded to rule on, and the
    run aborts INCONCLUSIVE rather than promoting or rejecting on noise. The
    incumbent keeps serving either way.
    """
    drift = abs(live.overall_pct - scratch.overall_pct)
    if drift > max_drift_pct:
        return False, (
            f"measurement rig is unreliable right now: the SAME incumbent model "
            f"scored {live.overall_pct}% live but {scratch.overall_pct}% on the "
            f"scratch rig ({drift:.1f} pts > {max_drift_pct} pt tolerance). The box "
            "is too contended for the frozen corpus to mean anything — refusing to "
            "rule on noise. Re-run in a quiet window.")
    return True, ""


# --- dataset guard (pure) ---------------------------------------------------
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def frozen_corpus_texts(corpus_path: Path) -> set:
    out = set()
    for line in corpus_path.read_text().splitlines():
        if line.strip():
            out.add(_normalize(json.loads(line).get("text", "")))
    out.discard("")
    return out


def check_heldout_guard(meta: dict, candidate_texts: list, frozen: set) -> tuple:
    """Verify the candidate never trains on the frozen eval corpus.

    Two independent checks — we do NOT simply trust the miner's own meta:
      1. the miner must SAY its held-out guard ran (contract with lane A)
      2. we INDEPENDENTLY recompute the overlap against the frozen corpus

    Returns (ok, reasons).
    """
    reasons = []
    # The miner (labs/router-selftrain/mine_candidates.py) writes:
    #   "held_out_guard": {"result": "pass", "corpus": ..., "collisions": 0}
    # `heldout_guard: {ran: true}` is also accepted so the contract is not brittle
    # to that one key. The guard must POSITIVELY assert a pass — a merely-present
    # key, or a recorded collision, is not good enough.
    meta = meta or {}
    guard = meta.get("held_out_guard") or meta.get("heldout_guard") or {}
    passed = (str(guard.get("result", "")).lower() == "pass") or bool(guard.get("ran"))
    collisions = guard.get("collisions")
    if not passed:
        reasons.append(
            "candidate meta does not record a PASSING held-out guard "
            "(meta.held_out_guard.result == 'pass') — refusing to train on an "
            "unguarded set")
    elif collisions not in (None, 0):
        reasons.append(
            f"candidate meta records {collisions} held-out guard collision(s) — "
            "the miner itself says the eval corpus leaked into the training set")
    leaked = [t for t in candidate_texts if _normalize(t) in frozen]
    if leaked:
        reasons.append(
            f"{len(leaked)} candidate example(s) collide with the FROZEN eval "
            f"corpus (e.g. {leaked[0]!r}) — training on the eval set would make "
            "every downstream number a lie")
    return (not reasons), reasons


# ===========================================================================
# I/O helpers
# ===========================================================================
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def free_disk_mb(path: Path) -> int:
    st = os.statvfs(str(path))
    return (st.f_bavail * st.f_frsize) // (1024 * 1024)


def http_ok(url: str, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except OSError:
        return False


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_env_var(env_file: Path, key: str) -> str | None:
    """Read one KEY=value out of a .env file (no shell, no export semantics)."""
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    # This runs from a scheduled zoe-data subprocess with no login session, so the
    # user bus must be pointed at explicitly or `systemctl --user` fails — and the
    # brain-restore path would then only log a warning while leaving the brain
    # stopped. (scripts/AGENTS.md: "Scripts run by timers/CI have no login
    # session: prefix user-service systemctl calls with XDG_RUNTIME_DIR".)
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, check=check, env=env)


def unit_active(unit: str) -> bool:
    return systemctl("is-active", "--quiet", unit, check=False).returncode == 0


def wait_for(fn, timeout: int = 90, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


# ===========================================================================
# Stages
# ===========================================================================
def latest_candidate(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"ABORT: candidate dataset not found: {p}")
        return p
    cands = sorted(SELFTRAIN_DIR.glob("candidate_*.jsonl"))
    if not cands:
        raise SystemExit(
            f"ABORT: no candidate dataset in {SELFTRAIN_DIR} — the traffic-mining "
            "lane produces candidate_<UTCSTAMP>.jsonl. Nothing to train on.")
    return cands[-1]


def run_eval(gguf: Path, port: int, out: Path, launch: bool,
             label: str) -> EvalScore:
    """Run the production-path eval against a sidecar serving `gguf` on `port`.

    The real prod router (`semantic_router.route_two_stage`) is pointed at the
    sidecar via ZOE_ROUTER_SIDECAR_URL, so this measures the model exactly as
    production would use it — while leaving the live :11436 sidecar alone.
    """
    env = dict(os.environ)
    env["ZOE_ROUTER_SIDECAR_URL"] = f"http://127.0.0.1:{port}"
    env["ZOE_ROUTER_SIDECAR_PORT"] = str(port)
    env["GGUF_R2"] = str(gguf)
    cmd = [sys.executable, str(CAMPAIGN / "prod_path_eval.py"),
           "--out", str(out), "--no-assert"]
    if launch:
        cmd.append("--launch-sidecar")
    log(f"EVAL[{label}]: {gguf.name} on :{port}")
    proc = subprocess.run(cmd, cwd=str(REPO), env=env,
                          capture_output=True, text=True)
    if not out.exists():
        raise SystemExit(
            f"ABORT: eval [{label}] produced no results.\n"
            f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")
    summary = json.loads(out.read_text())["summary"]
    score = EvalScore.from_summary(summary, source=label)
    log(f"EVAL[{label}]: overall {score.overall_pct}%  chat-FP {score.chat_fp_pct}%  "
        f"p50 {score.p50_ms}ms  (n={score.n})")
    return score


def preflight(args, journal: dict, will_train: bool = True) -> tuple:
    log("=== 1. PRE-FLIGHT ===")
    candidate_path = latest_candidate(args.candidate)
    meta_path = candidate_path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if not meta_path.exists():
        raise SystemExit(
            f"ABORT: no {meta_path.name} beside the candidate — the held-out "
            "guard cannot be verified, so the candidate is not trainable.")

    rows = [json.loads(l) for l in candidate_path.read_text().splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"ABORT: candidate dataset {candidate_path} is empty")

    ok, reasons = check_heldout_guard(
        meta, [r.get("text", "") for r in rows], frozen_corpus_texts(FROZEN_CORPUS))
    if not ok:
        raise SystemExit("ABORT: held-out guard failed:\n  - " + "\n  - ".join(reasons))
    log(f"candidate: {candidate_path.name} ({len(rows)} examples) — "
        "held-out guard OK, no frozen-corpus leakage")

    if will_train and not WARM_START.exists():
        raise SystemExit(
            f"ABORT: warm-start checkpoint {WARM_START} is missing.\n"
            "The loop warm-starts from the PRODUCTION lineage; cold-starting "
            "from base would produce an unrelated model wearing the incumbent's "
            "name. It will NOT do that silently.\n"
            "NOTE: the merged HF checkpoint behind the live r2 GGUF was not "
            "preserved (a Q8_0 GGUF cannot be converted back into a trainable "
            "checkpoint). Re-establish the lineage once — retrain from "
            f"{BASE_HF} on the committed datasets, KEEP runs/<gen>/merged — then "
            "point ZOE_ROUTER_WARM_START at it. See "
            "docs/knowledge/router-selftrain-loop.md.")

    disk = free_disk_mb(SERVED_DIR if SERVED_DIR.exists() else REPO)
    if disk < MIN_FREE_DISK_MB:
        raise SystemExit(f"ABORT: only {disk} MB free disk (< {MIN_FREE_DISK_MB} MB)")

    # The eval harness enforces its own memory floor deep inside the run; check it
    # here so a tight box fails fast with an actionable message instead of dying
    # halfway through a stage.
    avail = mem_available_mb()
    if avail < MIN_EVAL_MEM_MB:
        raise SystemExit(
            f"ABORT: MemAvailable {avail} MB < {MIN_EVAL_MEM_MB} MB — the box is "
            "too tight to stand up a scratch sidecar and measure anything "
            "trustworthy. This loop is scheduled for a quiet window (default "
            "sat 01:00) for exactly this reason; re-run when the box is idle.")

    if not unit_active(SIDECAR_UNIT):
        raise SystemExit(
            f"ABORT: {SIDECAR_UNIT} is not active — there is no incumbent to "
            "measure, and this loop must never be the thing that starts one.")

    # Re-measure the incumbent LIVE. A stale results file may describe a model
    # that is no longer the one being served.
    incumbent = run_eval(SERVED_GGUF, LIVE_PORT,
                         RUNS_DIR / f"{journal['stamp']}-incumbent.json",
                         launch=False, label="incumbent")

    journal["preflight"] = {
        "candidate": str(candidate_path), "examples": len(rows),
        "meta": meta, "warm_start": str(WARM_START),
        "free_disk_mb": disk, "mem_available_mb": mem_available_mb(),
        "incumbent": asdict(incumbent),
    }
    return candidate_path, rows, incumbent


def build_training_set(candidate_path: Path, stamp: str) -> Path:
    """Existing training sets + the new candidate → one merged jsonl."""
    sources = [FINETUNE / "data" / "train.jsonl",
               FINETUNE / "data" / "train_sibling.jsonl",
               FINETUNE / "data" / "train_round2.jsonl"]
    merged = SELFTRAIN_DIR / "work" / f"train_{stamp}.jsonl"
    merged.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with merged.open("w") as out:
        for src in sources + [candidate_path]:
            if not src.exists():
                log(f"  (skip missing training set {src.name})")
                continue
            for line in src.read_text().splitlines():
                if line.strip():
                    out.write(line + "\n")
                    n += 1
            log(f"  + {src.name}")
    log(f"merged training set: {merged.name} ({n} examples)")
    return merged


def train(merged: Path, stamp: str, journal: dict) -> Path:
    log("=== 2. TRAIN ===")
    run_dir = SELFTRAIN_DIR / "work" / f"run_{stamp}"
    brain_was_up = unit_active(BRAIN_UNIT)
    stopped_brain = False
    try:
        if mem_available_mb() < MIN_TRAIN_MEM_MB and brain_was_up:
            log(f"MemAvailable {mem_available_mb()} MB < {MIN_TRAIN_MEM_MB} MB — "
                f"stopping {BRAIN_UNIT} for the training window")
            systemctl("stop", BRAIN_UNIT)
            stopped_brain = True
            time.sleep(3)
        avail = mem_available_mb()
        if avail < MIN_TRAIN_MEM_MB:
            raise SystemExit(
                f"ABORT: MemAvailable {avail} MB < {MIN_TRAIN_MEM_MB} MB even "
                "after freeing the brain — not starting a doomed training run")

        cmd = ["nice", "-n", "10", sys.executable, str(FINETUNE / "train_lora.py"),
               "--variant", "functok", "--cpu",
               "--model-dir", str(WARM_START),
               "--data", str(merged),
               "--out", str(run_dir),
               "--epochs", str(args_epochs())]
        log("train: " + " ".join(cmd))

        def _deprioritize() -> None:
            # first OOM-killed if the box gets tight — the live services matter
            # more than this training run, which can simply be re-run.
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("1000")

        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(REPO), preexec_fn=_deprioritize)
        mins = (time.time() - t0) / 60
        if proc.returncode != 0:
            raise SystemExit(f"ABORT: training failed rc={proc.returncode}")
        log(f"training done in {mins:.1f} min")
        journal["train"] = {"run_dir": str(run_dir), "minutes": round(mins, 1),
                            "stopped_brain": stopped_brain}
    finally:
        # NEVER leave the brain stopped.
        if stopped_brain:
            log(f"restoring {BRAIN_UNIT}")
            systemctl("start", BRAIN_UNIT, check=False)
            if wait_for(lambda: http_ok(BRAIN_HEALTH), timeout=180):
                log("brain healthy again")
            else:
                log(f"WARNING: {BRAIN_UNIT} did not report healthy — CHECK THE BOX")

    merged_ckpt = run_dir / "merged"
    if not merged_ckpt.exists():
        raise SystemExit(f"ABORT: no merged checkpoint at {merged_ckpt}")
    return merged_ckpt


_EPOCHS = 2.0


def args_epochs() -> float:
    return _EPOCHS


# The tokenizer files transformers re-serializes on save_pretrained break
# convert_hf_to_gguf (assert max token id < vocab_size). Copy the PRISTINE
# originals from the base model over the merged checkpoint before converting.
PRISTINE_TOKENIZER_FILES = (
    "tokenizer.json", "tokenizer.model", "tokenizer_config.json",
    "special_tokens_map.json", "added_tokens.json", "generation_config.json",
    "chat_template.jinja",
)


def export_gguf(merged_ckpt: Path, stamp: str, journal: dict) -> Path:
    log("=== 3. EXPORT ===")
    for name in PRISTINE_TOKENIZER_FILES:
        src = BASE_HF / name
        if src.exists():
            shutil.copy2(src, merged_ckpt / name)
    log(f"restored pristine tokenizer files from {BASE_HF}")

    out = SELFTRAIN_DIR / "work" / f"candidate_{stamp}-Q8_0.gguf"
    env = dict(os.environ)
    env.setdefault("LLAMA_CPP", "/home/zoe/llama.cpp")
    proc = subprocess.run(
        ["bash", str(FINETUNE / "export_gguf.sh"), str(merged_ckpt), str(out)],
        cwd=str(REPO), env=env, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        raise SystemExit(
            f"ABORT: GGUF export failed rc={proc.returncode}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    log(f"exported {out.name} ({out.stat().st_size // (1024*1024)} MB)")
    journal["export"] = {"gguf": str(out), "sha256": sha256(out)}
    return out


def replay_gate(journal: dict) -> ReplayVerdict:
    """The voice replay gate — mandatory for anything on the voice path.

    The probe writes its results JSON on a real run and exits 0 on a SKIP, so a
    zero exit code alone does not prove it ran. We require a FRESH results file
    with samples in it; anything else is 'did not run' → no promotion.
    """
    log("=== voice replay gate ===")
    if not http_ok(BRAIN_HEALTH):
        return ReplayVerdict(False, False,
                             "brain not healthy — replay cannot run")
    results = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
    before = results.stat().st_mtime if results.exists() else 0.0

    env = dict(os.environ)
    # The probe's artifact cleanup reads POSTGRES_URL from the environment; a
    # failed sweep is a warning-level non-zero exit, which would make this gate
    # permanently un-passable (⇒ nothing ever promotes). Load it from the live
    # service .env.
    env.setdefault("POSTGRES_URL", read_env_var(LIVE_SERVICE_DIR / ".env",
                                                "POSTGRES_URL") or "")
    cmd = ["flock", "/tmp/zoe-voice-harness.lock",
           sys.executable, str(REPO / "scripts" / "maintenance" / "voice_regression_probe.py"),
           "--service-dir", str(LIVE_SERVICE_DIR)]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                          text=True, timeout=3600)
    log((proc.stdout or "")[-2000:])
    if proc.returncode != 0 and proc.stderr:
        # never swallow the probe's stderr — a silent failure here reads as a
        # skip, and a skip must be diagnosable.
        log("probe stderr: " + proc.stderr[-1000:])

    fresh = results.exists() and results.stat().st_mtime > before
    if not fresh or "SKIP:" in (proc.stdout or ""):
        return ReplayVerdict(
            False, False,
            "probe skipped or failed to run (no fresh results file) — "
            "a skip is not a pass")
    payload = json.loads(results.read_text())
    summary = payload.get("summary", {})
    if not summary.get("n_samples"):
        return ReplayVerdict(False, False, "probe replayed 0 samples")
    passed = proc.returncode == 0
    journal["replay"] = {"returncode": proc.returncode, "summary": summary}
    return ReplayVerdict(
        True, passed,
        f"n={summary.get('n_samples')} ok_rate={summary.get('ok_rate')} rc={proc.returncode}")


def validate_gguf(path: Path) -> tuple:
    """Is this plausibly a real router model? (ok, reason)

    Cheap structural check, deliberately paranoid — it guards the one write this
    loop makes to a live production path.
    """
    if not path.exists():
        return False, f"{path} does not exist"
    size = path.stat().st_size
    if size < MIN_GGUF_BYTES:
        return False, (f"{path.name} is {size} bytes — far below the {MIN_GGUF_BYTES} "
                       "byte floor for this model. Refusing to serve a truncated or "
                       "placeholder file.")
    try:
        with path.open("rb") as f:
            magic = f.read(4)
    except OSError as e:
        return False, f"cannot read {path}: {e}"
    if magic != GGUF_MAGIC:
        return False, f"{path.name} is not a GGUF (magic {magic!r})"
    return True, ""


def validate_lkg(lkg: Path) -> tuple:
    """A last-known-good must be a real GGUF that WE archived. (ok, reason)

    The containment check matters: the recovery marker is read off disk and could
    be stale, hand-edited, or written by a different checkout. Restoring from an
    arbitrary path is how a stray file ends up being served as the router.
    """
    try:
        resolved = lkg.resolve()
        archive = ARCHIVE_DIR.resolve()
    except OSError as e:
        return False, f"cannot resolve {lkg}: {e}"
    if not str(resolved).startswith(str(archive) + os.sep):
        return False, (f"last-known-good {resolved} is not inside the archive dir "
                       f"{archive} — refusing to restore from an arbitrary path")
    return validate_gguf(resolved)


def restart_and_verify_sidecar(expect: Path) -> bool:
    """Restart the sidecar unit and prove it is serving exactly `expect`."""
    systemctl("restart", SIDECAR_UNIT, check=False)
    if not wait_for(lambda: http_ok(f"http://127.0.0.1:{LIVE_PORT}/health"),
                    timeout=120):
        log("sidecar did not come back healthy")
        return False
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{LIVE_PORT}/props", timeout=10) as r:
            served = json.load(r).get("model_path", "")
    except OSError as e:
        log(f"cannot read /props: {e}")
        return False
    if Path(served).resolve() != SERVED_GGUF.resolve():
        log(f"sidecar serves {served!r}, expected {SERVED_GGUF}")
        return False
    if sha256(SERVED_GGUF) != sha256(expect):
        log("served file is not byte-identical to the intended model")
        return False
    return True


def restore_lkg(lkg: Path) -> bool:
    """Put the last-known-good GGUF back and restart onto it.

    The FILE is restored before the restart, so even an interrupt during the
    restart leaves the good model on disk for the next start (or for --recover).
    """
    ok, why = validate_lkg(lkg)
    if not ok:
        log(f"REFUSING to restore: {why}")
        log("OPERATOR ACTION REQUIRED — the sidecar was NOT modified.")
        return False
    tmp = SERVED_GGUF.with_suffix(".rollback.tmp")
    shutil.copy2(lkg, tmp)
    os.replace(tmp, SERVED_GGUF)  # atomic
    ok = restart_and_verify_sidecar(lkg)
    DEPLOY_MARKER.unlink(missing_ok=True)  # the swap is undone
    return ok


def recover() -> int:
    """Idempotent crash recovery — safe to run at any time, even with nothing broken.

    The scheduler kills a timed-out run with SIGKILL, which no in-process handler
    can catch. If that lands after the served model file was swapped but before
    the new model passed its live checks, the sidecar is left serving an
    UNVERIFIED model. The on-disk DEPLOY_MARKER is what survives to say so.

    This: (1) makes sure the brain is up, and (2) if a deploy was in flight,
    restores the last-known-good GGUF and restarts the sidecar onto it.
    """
    log("=== RECOVER ===")
    rc = 0
    if not http_ok(BRAIN_HEALTH):
        log(f"brain not healthy — starting {BRAIN_UNIT}")
        systemctl("start", BRAIN_UNIT, check=False)
        if wait_for(lambda: http_ok(BRAIN_HEALTH), timeout=180):
            log("brain restored")
        else:
            log(f"OPERATOR ACTION REQUIRED: {BRAIN_UNIT} did not come back healthy")
            rc = 1

    if not DEPLOY_MARKER.exists():
        log("no deploy was in flight — sidecar model untouched, nothing to undo")
        return rc

    marker = json.loads(DEPLOY_MARKER.read_text())
    lkg = Path(marker["last_known_good"])
    log(f"INTERRUPTED DEPLOY detected (run {marker.get('stamp')}) — the sidecar may "
        f"be serving an UNVERIFIED model. Restoring {lkg.name}")

    # The marker is untrusted input: it is read off disk and may be stale, from a
    # different checkout, or point at a file that is no longer a model. Validate
    # BEFORE copying anything onto the live served path.
    marker_served = marker.get("served")
    if marker_served and Path(marker_served).resolve() != SERVED_GGUF.resolve():
        log(f"REFUSING to recover: marker targets {marker_served}, but this loop "
            f"serves {SERVED_GGUF}. Stale or foreign marker — the sidecar was NOT "
            "modified. Remove the marker by hand if it is junk.")
        return 1
    ok, why = validate_lkg(lkg)
    if not ok:
        log(f"OPERATOR ACTION REQUIRED: cannot auto-restore — {why}")
        return 1
    if restore_lkg(lkg):
        log("RECOVERED — last-known-good restored and verified live.")
        return rc
    log("OPERATOR ACTION REQUIRED: restore of last-known-good did not verify.")
    return 1


def deploy(candidate_gguf: Path, stamp: str, baseline_live: EvalScore,
           journal: dict) -> int:
    """Swap the served model file, restart, verify, re-eval live. Rollback on any failure."""
    log("=== 6. DEPLOY ===")
    # Never write anything but a real model onto the live served path.
    ok, why = validate_gguf(candidate_gguf)
    if not ok:
        raise SystemExit(f"ABORT: refusing to deploy — {why}")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    lkg = ARCHIVE_DIR / f"lkg_{stamp}_{SERVED_GGUF.name}"
    shutil.copy2(SERVED_GGUF, lkg)  # copy, never move — LKG must always exist
    ok, why = validate_gguf(lkg)
    if not ok:  # a bad archive is a rollback we could never perform
        raise SystemExit(f"ABORT: archived last-known-good is unusable — {why}")
    log(f"archived last-known-good → {lkg.name}")

    def _restart_and_verify(expect: Path) -> bool:
        return restart_and_verify_sidecar(expect)

    def _rollback(why: str) -> int:
        log(f"!!! POST-DEPLOY FAILURE: {why}")
        log("!!! AUTO-ROLLBACK to last-known-good")
        ok = restore_lkg(lkg)
        journal["deploy"] = {"promoted": False, "rolled_back": True,
                             "rollback_verified": ok, "reason": why,
                             "last_known_good": str(lkg)}
        if ok:
            log("ROLLBACK OK — incumbent restored and verified. Candidate DISCARDED.")
        else:
            log("ROLLBACK FAILED TO VERIFY — SIDECAR MAY BE DEGRADED. OPERATOR NEEDED.")
        return 1

    # Record the intent BEFORE touching the served file. If we are SIGKILLed
    # between here and a verified promotion, this marker is the only evidence the
    # sidecar may be serving an unverified model — `--recover` reads it and undoes
    # the swap. (The scheduler's timeout kill is a SIGKILL; no handler survives it.)
    DEPLOY_MARKER.write_text(json.dumps({
        "stamp": stamp, "last_known_good": str(lkg),
        "served": str(SERVED_GGUF), "candidate": str(candidate_gguf),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=1))

    tmp = SERVED_GGUF.with_suffix(".new.tmp")
    shutil.copy2(candidate_gguf, tmp)
    os.replace(tmp, SERVED_GGUF)  # atomic swap of the served file
    log(f"swapped served model ← {candidate_gguf.name}")

    # ---- CROSSED THE RUBICON ------------------------------------------------
    # The served file is now the candidate. From here EVERY exit path must end
    # with either a verified promotion or a rollback — including SIGTERM (the
    # scheduler's timeout kill, which our own handler turns into SystemExit) and
    # KeyboardInterrupt. Without this, an interrupt during restart/verify or the
    # post-deploy eval would unwind straight past _rollback() and leave the
    # sidecar serving a model that never passed its live checks. Hence
    # BaseException, not Exception.
    try:
        if not _restart_and_verify(candidate_gguf):
            return _rollback("sidecar health/identity check failed after restart")

        post = run_eval(SERVED_GGUF, LIVE_PORT,
                        RUNS_DIR / f"{stamp}-post-deploy.json",
                        launch=False, label="post-deploy-live")
        verdict = decide_rollback(post, baseline_live)
        if not verdict.promote:
            return _rollback("; ".join(verdict.reasons))
    except BaseException as exc:
        _rollback(f"interrupted or errored mid-deploy ({exc!r}) — the sidecar must "
                  "never be left serving an unverified model")
        raise

    PROVENANCE.write_text(json.dumps({
        "promoted_at": stamp, "gguf_sha256": sha256(SERVED_GGUF),
        "source_gguf": str(candidate_gguf),
        "scores": {"incumbent_live_before": asdict(baseline_live),
                   "post_deploy_live": asdict(post)},
        "last_known_good": str(lkg),
    }, indent=1))
    # verified live — the swap is complete, so the interrupted-deploy marker goes
    DEPLOY_MARKER.unlink(missing_ok=True)
    journal["deploy"] = {"promoted": True, "rolled_back": False,
                         "post_deploy": asdict(post),
                         "last_known_good": str(lkg)}
    log(f"PROMOTED — live sidecar confirmed at {post.overall_pct}% "
        f"(chat-FP {post.chat_fp_pct}%, p50 {post.p50_ms}ms)")
    prune_archive()
    return 0


def prune_archive() -> None:
    """Keep the most recent ARCHIVE_KEEP last-known-good GGUFs. Never zero."""
    lkgs = sorted(ARCHIVE_DIR.glob("lkg_*.gguf"), key=lambda p: p.stat().st_mtime)
    for old in lkgs[:-max(ARCHIVE_KEEP, 1)]:
        log(f"pruning old archive {old.name}")
        old.unlink()


def scoreboard_append(row: dict) -> None:
    SCOREBOARD.parent.mkdir(parents=True, exist_ok=True)
    with SCOREBOARD.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def write_journal(journal: dict) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    p = RUNS_DIR / f"{journal['stamp']}.json"
    p.write_text(json.dumps(journal, indent=1, default=str))
    return p


# ===========================================================================
def _install_signal_handlers() -> None:
    """Make SIGTERM unwind the stack so the brain-restore `finally` actually runs.

    Python's default SIGTERM disposition terminates the process WITHOUT running
    `finally` blocks. The scheduled job SIGTERMs this script on timeout — so
    without this, a timeout during a training window (when llama-server has been
    stopped) would leave the brain DOWN. Raising SystemExit instead lets train()'s
    `finally` restore it. (SIGKILL cannot be caught; the scheduler has its own
    belt-and-braces restore for that case.)
    """
    def _bail(signum, _frame):
        raise SystemExit(f"ABORT: received signal {signum} — unwinding so the "
                         "brain and sidecar are restored")
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _bail)


def main() -> int:
    global _EPOCHS
    _install_signal_handlers()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--candidate", help="candidate dataset (default: newest)")
    ap.add_argument("--dry-run", action="store_true",
                    help="train + eval, report the ratchet verdict, NEVER promote")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--skip-train", metavar="GGUF",
                    help="evaluate an already-exported candidate GGUF (debug)")
    ap.add_argument("--recover", action="store_true",
                    help="idempotent crash recovery: ensure the brain is up and, if a "
                         "deploy was interrupted, restore the last-known-good GGUF")
    args = ap.parse_args()
    _EPOCHS = args.epochs

    if args.recover:
        return recover()

    # --force-promote is deliberately NOT implemented. The ratchet is the entire
    # safety story of this loop; a bypass flag would quietly delete it.
    if any(a.startswith("--force") for a in sys.argv[1:]):
        raise SystemExit(
            "There is no force/override flag. A candidate that cannot pass the "
            "ratchet is not safe to serve — fix the model, not the gate.")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    journal = {"stamp": stamp, "dry_run": args.dry_run,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log(f"router self-train run {stamp}{' (DRY RUN — will not promote)' if args.dry_run else ''}")

    # A marker left behind means a previous run was killed mid-deploy and the
    # sidecar may be serving an unverified model. Heal that before measuring
    # anything — otherwise this run would "re-measure the incumbent" against a
    # model that was never promoted.
    if DEPLOY_MARKER.exists():
        log("stale deploy marker found — a previous run was interrupted mid-deploy")
        if recover() != 0:
            raise SystemExit(
                "ABORT: could not recover the sidecar from an interrupted deploy. "
                "Refusing to train or promote on top of an unverified model.")

    rc = 0
    try:
        candidate_path, rows, incumbent = preflight(
            args, journal, will_train=not args.skip_train)

        if args.skip_train:
            candidate_gguf = Path(args.skip_train)
            log(f"=== 2-3. TRAIN/EXPORT skipped — using {candidate_gguf}")
        else:
            merged = build_training_set(candidate_path, stamp)
            merged_ckpt = train(merged, stamp, journal)
            candidate_gguf = export_gguf(merged_ckpt, stamp, journal)

        log("=== 4. EVAL (scratch sidecar — live :11436 untouched) ===")
        # Both models are measured on the SAME scratch rig, back to back, one
        # sidecar at a time. Comparing a candidate measured on scratch against an
        # incumbent measured live would inject ~16 points of rig noise into a
        # ratchet whose job is to detect much smaller real differences.
        incumbent_scratch = run_eval(SERVED_GGUF, SCRATCH_PORT,
                                     RUNS_DIR / f"{stamp}-incumbent-scratch.json",
                                     launch=True, label="incumbent-scratch")
        ok, why = check_measurement_validity(incumbent, incumbent_scratch)
        journal["measurement_validity"] = {
            "ok": ok, "reason": why,
            "incumbent_live": asdict(incumbent),
            "incumbent_scratch": asdict(incumbent_scratch)}
        if not ok:
            raise SystemExit(f"ABORT (inconclusive): {why}")

        candidate = run_eval(candidate_gguf, SCRATCH_PORT,
                             RUNS_DIR / f"{stamp}-candidate.json",
                             launch=True, label="candidate")

        replay = replay_gate(journal)

        log("=== 5. RATCHET (candidate vs incumbent, same rig) ===")
        decision = decide_promotion(incumbent_scratch, candidate, replay)
        journal["ratchet"] = {"checks": decision.checks,
                              "reasons": decision.reasons,
                              "incumbent_live": asdict(incumbent),
                              "incumbent_scratch": asdict(incumbent_scratch),
                              "candidate": asdict(candidate),
                              "replay": asdict(replay)}
        for name, ok in decision.checks.items():
            log(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        for r in decision.reasons:
            log(f"  reason: {r}")

        if args.dry_run:
            log(f"DRY RUN — ratchet says {'PROMOTE' if decision.promote else 'REJECT'}; "
                "promoting nothing.")
            journal["outcome"] = "dry_run_promote" if decision.promote else "dry_run_reject"
        elif not decision.promote:
            log("REJECTED — incumbent keeps serving. Candidate discarded.")
            journal["outcome"] = "rejected"
        else:
            # post-deploy is judged against the incumbent's own LIVE score — the
            # model this one replaced, measured on the same (live) rig.
            rc = deploy(candidate_gguf, stamp, incumbent, journal)
            journal["outcome"] = "promoted" if rc == 0 else "rolled_back"

        scoreboard_append({
            "stamp": stamp, "outcome": journal["outcome"],
            "examples": len(rows), "candidate_dataset": candidate_path.name,
            "incumbent_live_overall": incumbent.overall_pct,
            "incumbent_scratch_overall": incumbent_scratch.overall_pct,
            "candidate_overall": candidate.overall_pct,
            "delta_overall": round(candidate.overall_pct - incumbent_scratch.overall_pct, 1),
            "candidate_chat_fp": candidate.chat_fp_pct,
            "candidate_p50_ms": candidate.p50_ms,
            "replay_ran": replay.ran, "replay_passed": replay.passed,
            "checks": decision.checks,
        })
    except SystemExit as e:
        journal["outcome"] = "aborted"
        journal["error"] = str(e)
        write_journal(journal)
        raise
    finally:
        journal["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        p = write_journal(journal)
        log(f"journal: {p}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
