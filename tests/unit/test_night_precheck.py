"""``scripts/night/jobs/night_precheck.py``: the read-only question the night window asks before it stops anything (is there anything new to think about?).

The SQL runs through the asyncpg positional-compat layer, which counts every literal question mark as a bind placeholder and needs the ``::text`` / ``::timestamptz`` casts;
the watermark must carry a UTC offset so a naive stamp can never be read in the wrong zone. The live query is exercised by the window's own tests with a stand-in precheck.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
_spec = importlib.util.spec_from_file_location("night_precheck", REPO / "scripts" / "night" / "jobs" / "night_precheck.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def test_the_sql_has_exactly_one_placeholder_and_the_required_casts():
    from user_filters import message_owner_expr

    owner = message_owner_expr()
    sql = pc.new_turn_members_sql(owner)
    assert sql.count("?") == owner.count("?") + 1                         # the watermark, and nothing the layer would miscount
    assert "(?::text)::timestamptz" in sql and "cm.created_at::timestamptz >" in sql
    assert "cm.role = 'user'" in sql                                       # a reply Zoe saved is not a member's new turn


def test_the_watermark_must_carry_a_utc_offset_and_is_normalised_to_utc():
    assert pc.parse_since("2026-10-17T18:51:58+00:00") == "2026-10-17T18:51:58+00:00"
    assert pc.parse_since("2026-10-18T02:51:58+08:00") == "2026-10-17T18:51:58+00:00"
    with pytest.raises(ValueError):
        pc.parse_since("2026-10-18T02:51:58")                              # naive: refused, never guessed


def test_a_failure_exits_one_with_no_household_text(capsys):
    assert pc.main(["--since", "not-a-date"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "NIGHT_PRECHECK failed: ValueError" in out.err
