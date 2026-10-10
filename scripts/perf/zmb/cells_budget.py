"""What the night-mind CELLS (K1-K12, ``zoe-night-mind.py --cells``) cost on a model of a MEASURED speed, and how many of them fit in the time a window has left.

One place for the arithmetic, used by BOTH sides of the same run: the 12B night window (``scripts/night/night_window.py``: the watchdog it puts around the CLI, which cells it
asks for) and the CLI (``--cell-budget``: it stops STARTING cells that would overrun, and prints the verdicts it has instead of being killed with none).

Same formula family as the member pass (PR #1946, ``night_mind.timeout_for``): a call costs ``prompt_tokens / prefill_tok_s + output_tokens / decode_tok_s``. Two figures are built from it:

* EXPECTED seconds of a call: the output is what a model really writes, ``OUT_FILL`` x the call's ``max_tokens`` (2026-10-09, the 12B at decode 6.24 / prefill 158.7: a pass of 3
  calls took 150-200 s, i.e. 50-67 s per call: 0.6 x 640 / 6.24 = 62 s). This sizes the PLAN (which cells fit) and the CLI's per-cell go / no-go.
* WORST-CASE seconds of a call: ``night_mind.timeout_for`` itself (output at the cap, plus the 20 s margin): the longest the CLI will wait for ONE call before it logs ``llm_timeout``.
  The sum over the cells is the longest an honest run can take, so it is the watchdog's ceiling.

Which calls a cell makes is MEASURED, not guessed: the lab's fake brain (``zmb.night_brain``, seed ``zmb-v1``, ctx 8192) records every request. K2-K6 read the pass K1 played
(``play_group``): they make no calls of their own and cost nothing once K1 has run. Pure; stdlib only (``night_mind`` is imported lazily, for ``timeout_for`` alone).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parents[3]

#: the order ``zoe-night-mind.py --cells`` runs the reflection cells in (``spec.load_cells()``): K9.change_and_quiet = K9, K9.flat_week = K9f
CELL_ORDER = ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K9f", "K10", "K11", "K12")
#: (MOMENTS calls, THREADS calls) per cell, MEASURED: the largest count over 12 runs of the live 4B (``zoe-night-mind.py --cells --runs 3`` four times, 2026-10-10, the last with the 12-line call; the CLI prints
#: every run's own in ``cells.cell_calls``). The lab household is 24 turns in 400-token chunks (3 MOMENTS calls for K1 / K7 / K8); K9 / K9f / K10 add the tail ask for the lines a
#: reply never reached (the 2026-10-09 table said 1 + 1 for them: a third too cheap). A cell not in the table is priced as one MOMENTS + one THREADS call.
CELL_CALLS = {"K1": (3, 1), "K2": (0, 0), "K3": (0, 0), "K4": (0, 0), "K5": (0, 0), "K6": (0, 0), "K7": (4, 1), "K8": (3, 1),
              "K9": (2, 1), "K9f": (2, 1), "K10": (3, 1), "K11": (1, 1), "K12": (1, 0)}
#: prompt tokens of the two call kinds (the largest the lab household produced: 577 and 461) and their output caps (``night_mind.MOMENT_MAX_TOKENS`` / ``THREAD_MAX_TOKENS``)
MOMENTS_PROMPT_TOK, THREADS_PROMPT_TOK = 700, 600
MOMENTS_MAX_TOK, THREADS_MAX_TOK = 640, 450
OUT_FILL = 0.6          # the share of a call's ``max_tokens`` a model really writes (measured on the 12B, 2026-10-09)
SLACK = 1.25            # headroom over the expected time that a plan demands before it promises a cell
STARTUP_S = 45.0        # the CLI's imports, the model probe and the lab world, before the first call
GRACE_S = 60.0          # between the CLI's own budget and the window's kill: time to print the verdicts it has
TIMEOUT_MARGIN_S = 20.0


def _timeout_for(prompt_tokens: int, max_tokens: int, decode_tok_s: float, prefill_tok_s: float) -> float:
    sys.path.insert(0, str(REPO / "services" / "zoe-data"))
    import night_mind as nm                      # lazily: the single per-call formula lives there
    return nm.timeout_for(prompt_tokens, max_tokens, decode_tok_s, prefill_tok_s)


def default_rates() -> "tuple[float, float]":
    """(decode, prefill) tok/s the CLI assumes when it is given none: the 4B's (``night_mind.DEFAULT_DECODE_TOK_S`` / ``PREFILL_TOK_S``)."""
    sys.path.insert(0, str(REPO / "services" / "zoe-data"))
    import night_mind as nm
    return nm.DEFAULT_DECODE_TOK_S, nm.PREFILL_TOK_S


def _kinds():
    return ((MOMENTS_PROMPT_TOK, MOMENTS_MAX_TOK), (THREADS_PROMPT_TOK, THREADS_MAX_TOK))


def call_expected_s(prompt_tokens: int, max_tokens: int, decode_tok_s: float, prefill_tok_s: float) -> float:
    return prompt_tokens / max(prefill_tok_s, 5.0) + OUT_FILL * max_tokens / max(decode_tok_s, 0.5)


def cell_expected_s(key: str, decode_tok_s: float, prefill_tok_s: float) -> float:
    """Expected seconds of one cell's own model calls (0 for a cell that reads a pass an earlier cell played)."""
    return sum(n * call_expected_s(p, m, decode_tok_s, prefill_tok_s) for n, (p, m) in zip(CELL_CALLS.get(key, (1, 1)), _kinds()))


def cell_worst_s(key: str, decode_tok_s: float, prefill_tok_s: float) -> float:
    """The longest the cell can take if every call runs to its HTTP budget (``night_mind.timeout_for``)."""
    return sum(n * _timeout_for(p, m, decode_tok_s, prefill_tok_s) for n, (p, m) in zip(CELL_CALLS.get(key, (1, 1)), _kinds()))


def plan(decode_tok_s: float, prefill_tok_s: float, room_s: float, keys: "Sequence[str]" = CELL_ORDER, runs: int = 1) -> dict:
    """The cells that fit in ``room_s`` seconds (what the cap leaves after the restore reserve), and the two clocks for the CLI run.

    ``runs`` = how many times the CLI runs the whole set (``--runs``): every cell costs ``runs`` times its expected / worst seconds.
    ``selected`` = the longest PREFIX of ``keys`` (K2-K6 cannot run without K1's pass) whose ``STARTUP_S + SLACK x runs x expected`` fits ``room_s - GRACE_S``.
    ``cell_budget_s`` = what the CLI gets (``--cell-budget``): that room, but never more than the worst case of the selected cells.
    ``watchdog_s`` = the window's kill: the CLI's budget plus ``GRACE_S``, never past ``room_s``."""
    usable = max(0.0, room_s - GRACE_S)
    runs = max(1, int(runs))
    selected: "list[str]" = []
    expected = worst = 0.0
    for k in keys:
        e, w = runs * cell_expected_s(k, decode_tok_s, prefill_tok_s), runs * cell_worst_s(k, decode_tok_s, prefill_tok_s)
        if STARTUP_S + SLACK * (expected + e) > usable:
            break
        selected.append(k)
        expected, worst = expected + e, worst + w
    cell_budget = min(usable, STARTUP_S + worst) if selected else 0.0
    return {"selected": selected, "dropped": [k for k in keys if k not in selected], "expected_s": round(expected, 1), "worst_s": round(worst, 1),
            "needed_s": round(STARTUP_S + SLACK * expected, 1), "room_s": round(room_s, 1), "cell_budget_s": round(cell_budget, 1),
            "watchdog_s": round(min(room_s, cell_budget + GRACE_S), 1) if selected else 0.0, "fits_all": not [k for k in keys if k not in selected],
            "decode_tok_s": decode_tok_s, "prefill_tok_s": prefill_tok_s, "runs": runs}


def fits_next(elapsed_s: float, budget_s: Optional[float], key: str, decode_tok_s: float, prefill_tok_s: float) -> bool:
    """The CLI's go / no-go before it starts cell ``key``: the time already spent plus the cell's expected time (with the slack) must be inside ``--cell-budget``. No budget = always."""
    if not budget_s or budget_s <= 0:
        return True
    return elapsed_s + SLACK * cell_expected_s(key, decode_tok_s, prefill_tok_s) <= budget_s


def describe(p: dict) -> str:
    """One log line for a plan (the window logs it before it starts the CLI)."""
    return (f"night-mind cells budget: decode {p['decode_tok_s']:.2f} / prefill {p['prefill_tok_s']:.1f} tok/s -> expected {p['expected_s']:.0f} s "
            f"(x{SLACK} + {STARTUP_S:.0f} s startup = {p['needed_s']:.0f} s), worst case {p['worst_s']:.0f} s; room {p['room_s']:.0f} s -> CLI budget {p['cell_budget_s']:.0f} s, "
            f"watchdog {p['watchdog_s']:.0f} s; cells {','.join(p['selected']) or 'NONE'}" + (f" x{p['runs']} runs" if p.get("runs", 1) > 1 else "") + ("" if p["fits_all"] else f"; DROPPED (cannot fit): {','.join(p['dropped'])}"))
