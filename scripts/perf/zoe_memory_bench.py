#!/usr/bin/env python3
"""zoe_memory_bench.py - the Zoe Memory Bench (ZMB) entry point.

Thin wrapper: everything lives in the ``zmb`` package next to it (scripts/perf/zmb/). See
docs/knowledge/zoe-memory-bench.md for what each axis proves, the negative-control rule, the artifact
format and the bake-off decision rule.

    python3 scripts/perf/zoe_memory_bench.py --dry-run                 # the plan; nothing runs
    python3 scripts/perf/zoe_memory_bench.py --list                    # every cell id
    python3 scripts/perf/zoe_memory_bench.py --tier store              # controls first, then the measurement
    python3 scripts/perf/zoe_memory_bench.py --control off             # ONLY the negative controls (exit 2 if one stays green)
    python3 scripts/perf/zoe_memory_bench.py --axis authority --seed fresh
    python3 scripts/perf/zoe_memory_bench.py --record-baseline         # alone, deliberately; baseline seed, full run, Z0

The lab driver is in-process (the real MemoryService over an in-memory store): no network, no brain, no
Postgres, no live store, no ZOE_PERF needed. It never talks to the running zoe-data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zmb.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
