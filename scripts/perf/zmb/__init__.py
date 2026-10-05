"""Zoe Memory Bench (ZMB): a benchmark for the properties no public memory benchmark scores.

Authority (who may change what the user said), extraction fidelity, forgetting, identity, abstention and
the rest, each with generated gold, a deterministic scorer and a NEGATIVE CONTROL: a switch that disables
the feature under test and must turn the cell red. A run whose control stays green is refused.

    spec.py       the scenario spec format (JSON cells, matrix expansion, validation)
    world.py      the seeded synthetic household that generates every name, date and gold fact
    scorers.py    pure deterministic scorers + Wilson intervals
    cells.py      the arm-agnostic cell script (events in, probes out)
    arms/         the adapter interface (base) + Z0 (implemented), Hindsight and Graphiti (stubs)
    lab_driver.py the in-process lab: the real MemoryService over an in-memory store, with the controls
    artifact.py   the results artifact, per-axis Wilson intervals, baseline compare
    runner.py     the orchestration + CLI (scripts/perf/zoe_memory_bench.py)

Docs: docs/knowledge/zoe-memory-bench.md.
"""
HARNESS_VERSION = "0.1"
