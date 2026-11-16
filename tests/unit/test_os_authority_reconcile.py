"""The kernel-vs-production list reconcile (scripts/maintenance/os_authority_reconcile.py): identity per (member, list, item) with
duplicate counts, keyed digests, codes and counts only, and a run with a missing input says INCOMPLETE. Members and items invented."""
import importlib.util
from pathlib import Path

import pytest

import os_authority
import os_speaker

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("os_authority_reconcile", ROOT / "scripts" / "maintenance" / "os_authority_reconcile.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)

KEY = bytes(range(32))
A, B = "member-a", "member-b"
ROSTER = (A, B)


def digest(member, lst, item):
    return os_speaker.keyed_digest(KEY, member, lst, item)


def kernel(*rows):
    return [digest(*r) for r in rows]


def personal(owner, lst, item, recent=True):
    return rec.Row(owner, lst, item, False, recent)


def shared(owner, lst, item, recent=True):
    return rec.Row(owner, lst, item, True, recent)


def run(k, rows):
    return rec.compare(k, rows, digest, ROSTER)


def test_a_clean_store_has_no_drift():
    out = run(kernel((A, "Shopping", "oat milk")), [personal(A, "Shopping", "oat milk")])
    assert out == {"kernel_rows": 1, "production_rows": 1, "kernel_only": 0, "matched": 1, "production_only_recent": 0}


def test_the_same_text_on_another_members_list_does_not_mask_a_missing_row():
    """The old global text digest matched these: member A's kernel row against member B's production row."""
    out = run(kernel((A, "Shopping", "oat milk")), [personal(B, "Shopping", "oat milk")])
    assert out["kernel_only"] == 1 and out["production_only_recent"] == 1


def test_the_same_text_on_another_list_does_not_mask_a_missing_row():
    out = run(kernel((A, "Todo", "oat milk")), [personal(A, "Shopping", "oat milk")])
    assert out["kernel_only"] == 1 and out["production_only_recent"] == 1


def test_duplicates_count_one_for_one():
    two = kernel((A, "Shopping", "milk"), (A, "Shopping", "milk"))
    assert run(two, [personal(A, "Shopping", "milk")])["kernel_only"] == 1                     # one row short
    assert run(two, [personal(A, "Shopping", "milk")] * 2)["kernel_only"] == 0
    out = run(kernel((A, "Shopping", "milk")), [personal(A, "Shopping", "milk")] * 2)
    assert out["kernel_only"] == 0 and out["production_only_recent"] == 1                        # an extra production row


def test_a_shared_list_row_matches_any_members_add_but_only_once():
    rows = [shared(A, "Family", "bread")]
    assert run(kernel((B, "Family", "bread")), rows)["kernel_only"] == 0                          # B added to A's family list
    assert run(kernel((B, "Family", "bread"), (A, "Family", "bread")), rows)["kernel_only"] == 1   # two adds, one row
    assert run(kernel(("member-z", "Family", "bread")), rows)["kernel_only"] == 1                # not on the roster
    assert run(kernel((B, "Family", "bread")), [personal(A, "Family", "bread")])["kernel_only"] == 1   # personal: owner only


def test_a_personal_row_is_used_before_a_shared_one_so_nothing_is_double_counted():
    rows = [shared(A, "Shopping", "tea"), personal(A, "Shopping", "tea")]
    out = run(kernel((A, "Shopping", "tea"), (B, "Shopping", "tea")), rows)
    assert out["kernel_only"] == 0 and out["matched"] == 2


def test_production_rows_take_the_member_and_list_exactly_as_the_add_sends_them():
    records = [{"text": "oat milk", "recent": True, "owner": A, "name": "Shopping", "list_type": "shopping", "visibility": "personal"},
               {"text": "rice", "recent": False, "owner": "family-admin", "name": "", "list_type": "shopping", "visibility": "personal"},
               {"text": "jam", "recent": True, "owner": B, "name": "Pantry", "list_type": "shopping", "visibility": "family"}]
    rows = rec.production_rows(records, os_authority.kernel_list_name, "family-admin")
    assert rows == [personal(A, "Shopping", "oat milk"), shared("family-admin", "shopping", "rice", recent=False),
                    shared(B, "Pantry", "jam")]
    assert os_authority.kernel_list_name("shopping", "") == "shopping" == os_authority.kernel_list_name(None, None)


def test_a_log_that_cannot_be_read_makes_the_run_incomplete(tmp_path):
    good = tmp_path / "zoe-data.log"
    good.write_text("x OS_AUTHORITY verb=list_add result=kernel code=verified ms=1.0\n")
    lines, skipped = rec.read_logs([str(good), str(tmp_path / "gone.log"), str(tmp_path)])
    assert len(lines) == 1 and skipped == 2
    out = rec.summarise(run([], []), ["log"])
    assert out["status"] == "INCOMPLETE" and out["skipped"] == ["log"] and rec.exit_code(out) == 3
    clean = rec.summarise(run([], []), [])
    assert clean["status"] == "OK" and rec.exit_code(clean) == 0
    drift = rec.summarise(run(kernel((A, "Shopping", "x")), []), ["log"])
    assert drift["status"] == "INCOMPLETE" and rec.exit_code(drift) == 1                          # drift still exits 1


def test_decisions_read_codes_only_and_never_echo_a_planted_value():
    lines = ["x OS_AUTHORITY verb=list_add result=kernel code=verified ms=1.2",
             "x OS_AUTHORITY verb=list_add result=fallback code=timeout ms=150.1",
             "x OS_AUTHORITY verb=list_add result=fallback code=oat milk ms=1.0",      # not a code: never read
             "x OS_AUTHORITY verb=list_add result=fallback code=planted_word ms=1.0",   # not a known code: unknown
             "x OS_AUTHORITY verb=list_add result=refused code=forgotten ms=1.0",
             "x OS_AUTHORITY verb=list_add result=fallback code=late ms=150.2",      # a grant verified after the turn left
             "x OS_AUTHORITY verb=list_add result=rollback code=incomplete n=2 left=1",
             "unrelated line"]
    assert rec.decisions(lines) == {"fallback:late": 1, "fallback:timeout": 1, "fallback:unknown": 1, "kernel:verified": 1, "refused:forgotten": 1,
                                    "rollback:incomplete": 1}


def test_the_tool_never_prints_a_member_a_list_or_the_key():
    src = (ROOT / "scripts" / "maintenance" / "os_authority_reconcile.py").read_text("utf-8")
    prints = [ln for ln in src.splitlines() if "print(" in ln]
    assert all("exc).__name__" in ln or "json.dumps(out" in ln for ln in prints)
    assert "digest_key" not in src and "._key" not in src and "reconciler_creds" not in src    # the key never leaves KernelRows
