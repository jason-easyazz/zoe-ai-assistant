# Flue-brain quality gates

A committed, reusable testing system for the live Flue Zoe-brain (the production
brain via `ZOE_BRAIN_BACKEND=flue`). It grew out of the ad-hoc 2026-07-07
quality-to-100% campaign; this is that campaign's harness, promoted from
scratchpad into the repo so it stops being throwaway and the mechanics stop
being re-copied per gate.

## Run it

```bash
# from the live checkout (/home/zoe/assistant):
python3 labs/flue-zoe-brain/parity/run_gates.py            # all gates, fresh user, full guards
python3 labs/flue-zoe-brain/parity/run_gates.py --gates hard,corpus
python3 labs/flue-zoe-brain/parity/run_gates.py --list     # show discovered gates
python3 labs/flue-zoe-brain/parity/run_gates.py --skip-guards   # dev: ignore memory/quiet-window
```

The runner provisions ONE fresh empty-store test user (`test-gate-<nonce>`),
logs it in, preflights one real turn, runs the selected gates, and writes a JSON
+ markdown report to `gate-results/` (gitignored). Exit code is non-zero if any
row is FAIL/ERROR or if zoe-data restarted mid-run.

## Pieces

| File | Role |
|---|---|
| `gatelib.py` | Shared harness: provisioning, login, nonce'd sessions, `chat`/`api_get`, DB-truth verification, the `Recorder`/verdict logic, and the operational guards. **Every gate imports this — don't re-implement its mechanics.** |
| `run_gates.py` | Unified runner. Discovers gates, enforces guards, provisions the user, aggregates one report. |
| `corpus_gate.py` | 46-prompt conversation-quality corpus (recall/identity/research auto-checked, rest JUDGE). |
| `hard_gate.py` | Adversarial: identity under pressure, research traps, corrections/negation, cross-session recall, DB-verified writes, two-user isolation, tool honesty. |
| `recall_reliability.py`, `tool_reliability.py`, `parity_check.py` | Older standalone benchmarks (recall N-trial reliability; legacy flue-vs-core A/B). Kept; may be folded onto `gatelib` later. |

## Writing a new gate

Drop a `*_gate.py` in this directory exposing a module-level `GATE`:

```python
from gatelib import GateContext, GateSpec, list_item_present

def run(ctx: GateContext) -> None:
    s = ctx.session("mycheck")                       # nonce'd, ownership-safe
    ctx.expect("category", "a question", s, must=["expected"], must_not=["forbidden"])
    # DB-truth for writes — never trust the reply:
    present, detail = list_item_present("thing")
    ctx.recorder.check("category", "[DB] thing present?", present, detail, "ok", "NOT in DB")

GATE = GateSpec(name="mygate", description="what it checks", run=run)
```

`run_gates.py --gates mygate` (or `all`) picks it up automatically — no
registration.

## Lessons this system encodes (each was a real failure)

- **Per-run nonce'd session ids** (`ctx.session()`). Sessions are ownership-bound
  (a foreign session id 403s) and durable — a reused long-lived session's prompt
  eventually exceeds the 8192-token context and every turn 500s. A hardcoded
  `"replay"` session wedged the whole voice replay-gate this way.
- **DB-truth, not reply-trust.** "I've added it" is not evidence; the row in
  Postgres is. All writes are verified via `gatelib.db_rows` / `list_item_present`.
- **Brain-fallback text = FAIL.** The degraded "trouble reaching my brain" reply
  silently counted as OK once, weakening gate evidence; `expect()` now fails it.
- **Guards before measuring.** Refuse when memory is tight (OOM risk) or when
  `main` moved in the last few minutes (deploy.yml restarts zoe-data on every
  push and corrupts a run). The report stamps zoe-data's start time before and
  after; a mid-run restart marks the run INVALID.
- **Gate writes land on FAMILY-shared surfaces — always hard-purge.** "Add X to my shopping list" writes to the household's shared list (`visibility='family'`), NOT the test user's own store, so it's visible to the real family and — if only *soft*-deleted — accumulates as invisible clutter on real data. Every gate hard-deletes its writes: `run_gates.py` calls `gatelib.purge_artifacts(nonce)` in a `finally` (survives crash/kill), the standalone gates hard-delete their own nonce'd rows, and write payloads are nonce-tagged so the purge is surgical. `GATE_WRITE_MARKERS` lists the eval-only phrases; add to it when a gate introduces a new write payload.
- **Fresh empty-store user per run.** Avoids cross-run memory contamination — the
  confound that muddied the original 2026-07-03 parity numbers.

## Scope

LAB ONLY. Nothing here is imported by `services/zoe-data` or run in CI; these are
hand-started checks against the live host. Known coverage gaps (candidates for
new `*_gate.py`): real-audio E2E through Moonshine/Kokoro, streaming/barge-in,
long-horizon session soak, and full concurrency.
