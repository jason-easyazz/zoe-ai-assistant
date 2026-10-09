#!/usr/bin/env python3
"""The 03:00 nightly digest as a STANDALONE process, for the 12B night window (scripts/night/night_window.py).

Why it exists: the digest does not run from a timer. It is a loop INSIDE zoe-data (``routers/system.py``
``_memory_digest_loop``: sleep until 03:00, ``run_nightly_digest_pass``, then the evolution NOTICE and MEASURE
phases). The night window stops zoe-data, so the loop is not alive at 03:00 and, once zoe-data starts again, it sleeps until the
NEXT 03:00: a window that did not run this body itself would silently skip the digest for the night. This runs the same
body, in the same order, against whatever model ``GEMMA_SERVER_URL`` names (the window sets it to the 12B).

Output is COUNTS ONLY (never fact text): one ``NIGHT_JOB digest ...`` line the window copies into its report.

    night_digest.py            run the pass
    night_digest.py --check    import everything, open the database, run NO model call and change nothing

Exit: 0 the pass ran and did real work or had nothing to do; 1 an exception; 3 the pass ran but the model could not serve it
(``extractor_errors`` / ``processing_errors`` / ``no_eligible_users_despite_input``): the window then runs the job again on the
live 4B after it restores.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
ZOE_DATA = Path(os.environ.get("NIGHT_ZOE_DATA_DIR", str(REPO / "services" / "zoe-data")))
sys.path.insert(0, str(ZOE_DATA))

#: verdicts (memory_metrics) that mean "the pass ran but did not do its work": exit 3
DEGRADED_VERDICTS = frozenset({"extractor_errors", "processing_errors", "no_eligible_users_despite_input"})


def scalars(d: dict) -> dict:
    """Numbers, booleans and None only: a result dict can carry text (a fact, a title); the report never does."""
    return {k: v for k, v in (d or {}).items() if isinstance(v, (int, float, bool)) or v is None}


def surface_count_logs() -> None:
    """Print the counts-only OPEN_LOOPS line (same handler the dreaming runner attaches); never the root level."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    counts = logging.getLogger("memory_digest.open_loops")
    counts.setLevel(logging.INFO)
    counts.addHandler(handler)
    counts.propagate = False


def model_endpoint() -> str:
    """host:port of the model this process will call (never the whole URL)."""
    parts = urlsplit(os.environ.get("GEMMA_SERVER_URL", "") or "http://127.0.0.1:11434")
    return f"{parts.hostname}:{parts.port}"


async def run_digest_body(*, run_pass, record, evolution, measure) -> tuple[int, dict]:
    """The loop body of ``_memory_digest_loop``, with its collaborators injected (the tests pass doubles). Returns (exit code, summary)."""
    passed = await run_pass()
    results, input_seen = passed["results"], passed["input_seen"]
    summary = record(results, input_seen=input_seen) or {}
    out = {"users": summary.get("users"), "effect_count": summary.get("effect_count"), "verdict": summary.get("verdict"),
           "attempted": summary.get("attempted"), "skipped": summary.get("skipped"), "errors": summary.get("errors"),
           "input_seen": input_seen}
    for name, fn in (("evolution_notice", evolution), ("evolution_measure", measure)):
        try:                                                  # the loop treats both as non-fatal; so do we
            out[name] = scalars(await fn())
        except Exception as exc:  # noqa: BLE001
            out[name] = {"error": type(exc).__name__}
    return (3 if out["verdict"] in DEGRADED_VERDICTS else 0), out


async def main(check: bool) -> int:
    from db_pool import close_pool, get_db_ctx, init_pool

    surface_count_logs()
    try:
        await init_pool()
    except Exception as exc:  # noqa: BLE001
        print(f"Database pool initialisation failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    try:
        import memory_digest
        import memory_metrics
        from evolution_notice import run_evolution_notice, run_measure_phase

        if check:
            async with get_db_ctx() as db:
                await db.execute("SELECT 1")
            print(f"NIGHT_JOB digest check ok model={model_endpoint()} "
                  f"timeout_scale={os.environ.get('ZOE_DIGEST_LLM_TIMEOUT_SCALE', '1')}")
            return 0
        rc, out = await run_digest_body(run_pass=memory_digest.run_nightly_digest_pass, record=memory_metrics.record_digest_run,
                                        evolution=run_evolution_notice, measure=run_measure_phase)
        print("NIGHT_JOB digest " + json.dumps(out, sort_keys=True, default=str) + f" model={model_endpoint()}")
        return rc
    except Exception as exc:  # noqa: BLE001
        print(f"NIGHT_JOB digest failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        await close_pool()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="import, open the database, no model call, no change")
    raise SystemExit(asyncio.run(main(ap.parse_args().check)))
