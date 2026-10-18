"""The owner's harness-user purge (``scripts/maintenance/purge_harness_users.sql``, P-2) uses THE synthetic rule
and is safe by default. Pins: its POSIX pattern selects exactly the ids ``user_filters.SYNTHETIC_USER_RE`` does
(minus nothing - guests never match either), and the file is dry-run / read-only unless both -v purge=1 and
-v commit=1 are given. The SQL itself was exercised against a throwaway pg17 (dry run, rolled-back rehearsal,
FK multi-pass, keep_ids, commit) when it was written; this test keeps the static contract from drifting."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "scripts" / "maintenance" / "purge_harness_users.sql").read_text()
sys.path.insert(0, str(ROOT / "services" / "zoe-data"))
from user_filters import SYNTHETIC_USER_RE  # noqa: E402

IDS = ["demo_bar_0a1b2c3d", "DEMO-x", "bench_1", "test-sec-b-9", "probe_route", "ci_suite", "e2e_flow",
       "zoe-hammer-1700000000", "zoe_tok_17", "zoe-hammer-jane", "zoe-mempalace-baseline",
       "zoe-prompt-packet-measure", "zoe-prompt-packet-measure-2", "zoe-touch-pi", "jason", "Christine",
       "Benchley", "Testa", "cindy", "guest", "voice-guest", "", "democrat", "ci", "test"]


def _sql_pattern() -> str:
    m = re.search(r"^\\set synthetic_re '([^']+)'$", SQL, re.M)
    assert m, "purge_harness_users.sql lost its \\set synthetic_re line"
    return m.group(1)


def test_sql_pattern_selects_exactly_the_synthetic_ids():
    sql = re.compile(_sql_pattern(), re.IGNORECASE)   # ~* = case-insensitive; this ERE is also valid Python re
    for uid in IDS:
        assert bool(sql.match(uid)) == bool(SYNTHETIC_USER_RE.match(uid)), uid


def test_default_is_a_read_only_dry_run():
    assert "SET TRANSACTION READ ONLY;" in SQL
    assert "\\set purge 0" in SQL and "\\set commit 0" in SQL
    # the only COMMIT sits under BOTH flags
    assert SQL.count("COMMIT;") == 1
    commit_block = SQL[SQL.index("\\if :commit"):]
    assert commit_block.index("\\if :purge") < commit_block.index("COMMIT;")


def test_keep_ids_are_excluded_everywhere_a_row_is_selected():
    hits = [m.start() for m in re.finditer(r"(?<!!)~\*", SQL)]
    assert len(hits) >= 6, "selection sites not found"
    for at in hits:
        window = SQL[at:at + 160]
        assert "keep" in window or "$2" in window, f"a selection ignores keep_ids: {SQL[at - 60:at + 60]!r}"


def test_a_row_a_real_user_also_owns_is_never_selected():
    """Cross-review finding: `owner_user_id = demo_*, user_id = jason` must survive. Both the dry run and the purge
    build their predicate from EVERY owner column of the table (exercised on pg17: that row was kept)."""
    assert SQL.count("c2.column_name <> ") == 2
    assert SQL.count("coalesce(' AND ' || others, '')") == 2
