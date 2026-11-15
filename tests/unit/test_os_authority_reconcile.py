"""The kernel-vs-production list reconcile (scripts/maintenance/os_authority_reconcile.py): codes and counts only."""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("os_authority_reconcile", ROOT / "scripts" / "maintenance" / "os_authority_reconcile.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)


def test_kernel_only_is_the_drift_and_production_only_is_counted_not_blamed():
    out = rec.compare(kernel=["a", "b", "z"], production_all=["a", "b", "c", "old"], production_recent=["a", "b", "c"])
    assert out == {"kernel_rows": 3, "production_rows": 4, "kernel_only": 1, "production_only_recent": 1, "matched": 2}


def test_a_clean_store_has_no_drift():
    assert rec.compare(["a"], ["a"], ["a"])["kernel_only"] == 0


def test_decisions_read_codes_only_and_never_echo_a_planted_value():
    lines = ["x OS_AUTHORITY verb=list_add result=kernel code=verified ms=1.2",
             "x OS_AUTHORITY verb=list_add result=fallback code=timeout ms=150.1",
             "x OS_AUTHORITY verb=list_add result=fallback code=oat milk ms=1.0",      # not a code: never read
             "x OS_AUTHORITY verb=list_add result=fallback code=planted_word ms=1.0",   # not a known code: unknown
             "unrelated line"]
    assert rec.decisions(lines) == {"fallback:timeout": 1, "fallback:unknown": 1, "kernel:verified": 1}
