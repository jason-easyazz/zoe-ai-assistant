"""The SQL placeholder rewrite is memoised, and its output is byte-identical (speed audit 2026-11-14 A5).

``db_pool._adapt_params`` walks every query's SQL one character at a time with regex work at
each position. Its rewritten text depends ONLY on the SQL text (``params`` is just copied to a
list), so the text step is cached per SQL string (``_adapt_sql``; the walk itself stays as
``_adapt_sql_uncached``). These tests prove the cached path returns exactly what the walk
returns, cold and warm, over:

* every SQL string literal (and f-string, its holes filled) passed to an execute/fetch call
  anywhere in services/zoe-data, harvested statically: about 1,600 distinct statements;
* a synthetic set that reaches every branch of the walk.

and that the parameters are handled exactly as before (copied to a fresh list, never cached).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import db_pool

pytestmark = pytest.mark.ci_safe

_ZOE_DATA = pathlib.Path(__file__).resolve().parents[1]
_SQL_METHODS = {"execute", "executemany", "fetch", "fetchrow", "fetchval", "fetchall", "fetchone", "execute_fetchall"}


def _harvest() -> list[str]:
    out: list[str] = []
    for path in sorted(_ZOE_DATA.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _SQL_METHODS and node.args):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                out.append("".join(v.value if isinstance(v, ast.Constant) else "x" for v in arg.values))
    return list(dict.fromkeys(out))


_HARVESTED = _harvest()

_SYNTHETIC = [
    "",
    "SELECT 1",                                                   # no placeholder
    "SELECT * FROM t WHERE a = ?",                                # trailing ?
    "?",
    "??",
    "SELECT * FROM t WHERE id IN (?, ?, ?) AND u = ?",            # IN (?, ?, ?)
    "SELECT '?' AS q, x FROM t WHERE y = ?",                      # ? inside a quoted literal
    "SELECT 'it''s ? here' FROM t WHERE y = ?",                   # escaped (doubled) quote
    "SELECT \"col?\" FROM \"we\"\"ird\" WHERE z = ?",             # double-quoted identifiers
    "SELECT 'unterminated ? literal",                             # quote never closed
    "SELECT data->>'k' FROM t WHERE data ? 'key' AND id = ?",     # JSON ? operator
    "SELECT 1 FROM t WHERE tags ?| array['a','b'] AND tags ?& array['c'] AND id = ?",  # ?| and ?&
    "SELECT ?::text, created_at::timestamptz FROM t",             # casts with ::
    "SELECT 1 -- a comment with ? and NOW()\nFROM t WHERE a = ?",  # line comment
    "SELECT 1 /* block ? comment NOW() */ FROM t WHERE a = ?",    # block comment
    "SELECT $$ dollar ? quoted $$, $tag$ NOW() $tag$ FROM t WHERE a = ?",  # dollar quoting
    "INSERT INTO t (a, ts) VALUES (?, NOW())",                    # NOW() as a value -> ::text
    "UPDATE t SET ts = now() WHERE id = ?",                       # lower-case now()
    "SELECT NOW()::timestamptz",                                  # already cast
    "SELECT * FROM t WHERE ts > NOW() - interval '10 seconds'",   # NOW() operand (after)
    "SELECT * FROM t WHERE ts < interval '1 day' + NOW()",        # NOW() operand (before)
    "SELECT * FROM t WHERE ts < (NOW()) - interval '1 day'",      # closing parens then operator
    "SELECT NOW() AT TIME ZONE 'UTC'",                            # AT TIME ZONE
    "SELECT 1 -- x -\nWHERE a = NOW()",                           # operator inside a -- comment
    "SELECT a - -NOW()",                                          # (?<!-) guard
    "SELECT 'NOW()' AS label, NOW() AS ts",                       # NOW() inside a literal
    "SELECT * FROM t WHERE created_at > datetime('now', '-7 days') AND u = ?",   # SQLite offset
    "SELECT * FROM t WHERE created_at < DATETIME( 'now' , '+1 day' )",           # spacing, case
    "SELECT datetime('now') AS ts, datetime('now', ?) AS bad",    # datetime('now') and the unhandled ? form
    "SELECT 'datetime(''now'')' AS lit",                          # inside a literal
    "SELECT x FROM t WHERE a = ? AND b = '?' AND c = \"?\" AND d = ? -- ?\n",
    "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",                  # double-digit $10+
    "SELECT ’?’ FROM t WHERE a = ?",                              # non-ASCII quotes are not quotes
    "SELECT 1\r\nFROM t\tWHERE a = ?\n",
]


def _cold_then_warm(sql: str) -> tuple[str, str]:
    db_pool._adapt_sql_memo.cache_clear()
    cold = db_pool._adapt_params(sql, (1, "a", None))[0]
    warm = db_pool._adapt_params(sql, ())[0]
    return cold, warm


def test_the_harvest_is_large_enough_to_mean_something():
    assert len(_HARVESTED) >= 500, len(_HARVESTED)


@pytest.mark.parametrize("corpus", ["harvested", "synthetic"])
def test_cached_rewrite_is_byte_identical_to_the_walk(corpus):
    sqls = _HARVESTED if corpus == "harvested" else _SYNTHETIC
    mismatches = []
    for sql in sqls:
        want = db_pool._adapt_sql_uncached(sql)
        cold, warm = _cold_then_warm(sql)
        if not (cold == want and warm == want and type(cold) is str and type(warm) is str):
            mismatches.append(sql[:80])
    assert mismatches == []


def test_a_full_cache_returns_the_same_bytes_as_an_empty_one():
    """Warm every statement first (the steady state in production), then compare again."""
    db_pool._adapt_sql_memo.cache_clear()
    for sql in _HARVESTED + _SYNTHETIC:
        db_pool._adapt_params(sql, ())
    for sql in _HARVESTED + _SYNTHETIC:
        assert db_pool._adapt_params(sql, ())[0] == db_pool._adapt_sql_uncached(sql)
    assert db_pool._adapt_sql_memo.cache_info().hits >= len(set(_HARVESTED + _SYNTHETIC))


def test_synthetic_set_reaches_every_branch():
    """Pin that the synthetic corpus actually exercises what it claims (not a vacuous pass)."""
    out = {sql: db_pool._adapt_sql_uncached(sql) for sql in _SYNTHETIC}
    assert out["SELECT * FROM t WHERE id IN (?, ?, ?) AND u = ?"] == "SELECT * FROM t WHERE id IN ($1, $2, $3) AND u = $4"
    assert out["SELECT '?' AS q, x FROM t WHERE y = ?"] == "SELECT '?' AS q, x FROM t WHERE y = $1"
    assert out["INSERT INTO t (a, ts) VALUES (?, NOW())"] == "INSERT INTO t (a, ts) VALUES ($1, NOW()::text)"
    assert out["SELECT * FROM t WHERE ts > NOW() - interval '10 seconds'"].count("::text") == 0
    assert "(CURRENT_TIMESTAMP - INTERVAL '7 days')::text" in out[
        "SELECT * FROM t WHERE created_at > datetime('now', '-7 days') AND u = ?"]
    assert out["SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"].endswith("$11, $12")
    # JSON operators are NOT special to the walk (today's behaviour, kept byte for byte)
    assert "data $1 'key'" in out["SELECT data->>'k' FROM t WHERE data ? 'key' AND id = ?"]


@pytest.mark.parametrize("params", [None, (), [], (1, 2, 3), [1, "x", None], ("only",)])
def test_parameters_are_handled_exactly_as_before(params):
    sql = "SELECT * FROM t WHERE a = ? AND b = ? AND c = ?"
    for _ in range(2):                      # cold, then warm
        got_sql, got_args = db_pool._adapt_params(sql, params)
        assert got_sql == db_pool._adapt_sql_uncached(sql)
        assert got_args == (list(params) if params is not None else [])
        assert type(got_args) is list
        if isinstance(params, list):
            assert got_args is not params   # always a fresh copy, never a cached or shared list


def test_the_same_sql_with_different_params_never_shares_args():
    sql = "UPDATE t SET a = ? WHERE id = ?"
    _, a1 = db_pool._adapt_params(sql, ("x", 1))
    _, a2 = db_pool._adapt_params(sql, ("y", 2))
    assert a1 == ["x", 1] and a2 == ["y", 2]


def test_the_cache_is_bounded():
    assert db_pool._adapt_sql_memo.cache_info().maxsize == 2048


def test_swapping_a_rewrite_rule_is_not_served_from_the_cache(monkeypatch):
    """A test that swaps a rule (the NOW()-operand negative control does) sees the swapped rule's
    rewrite, and the real rule's entry is not overwritten by it."""
    import re

    sql = "SELECT * FROM t WHERE ts > NOW() - interval '10 seconds'"
    real = db_pool._adapt_params(sql, ())[0]
    assert "NOW()::text" not in real
    never = re.compile(r"(?!)")
    with monkeypatch.context() as m:
        m.setattr(db_pool, "_NOW_ARITH_AFTER_RE", never)
        m.setattr(db_pool, "_NOW_ARITH_BEFORE_RE", never)
        assert "NOW()::text" in db_pool._adapt_params(sql, ())[0]
    assert db_pool._adapt_params(sql, ())[0] == real


def test_non_str_sql_takes_the_uncached_walk():
    class _S(str):
        pass

    sql = _S("SELECT ? , NOW()")
    db_pool._adapt_sql_memo.cache_clear()
    got, args = db_pool._adapt_params(sql, (1,))
    assert got == "SELECT $1 , NOW()::text" and args == [1]
    assert db_pool._adapt_sql_memo.cache_info().currsize == 0


def test_a_dropped_rule_cannot_be_impersonated_by_a_new_one_at_the_same_address():
    """Review of PR 2469: with the key built from id(rule), a rule that is swapped out and freed gives its address
    back; a NEW rule allocated there would match the old cache entry and serve the OLD rewrite. The key holds the rule
    objects, so a rule a cache entry was built under is never freed.

    Proved directly, not by hoping for an address reuse: after the caller drops its last reference (and ``re``'s own
    compile cache is purged), the swapped-in rule must still be ALIVE, held by the memo. The earlier shape of this test
    swapped rules 300 times through ``monkeypatch.setattr`` with two ``gc.collect()`` per swap. ``monkeypatch`` keeps
    every replaced value for its undo, so no rule was ever freed and the test passed on the id()-keyed code too; and each
    full collection walks the whole heap, which late in the lane cost ~0.6 s: 357 s for this one test (2026-11-14)."""
    import re
    import weakref

    sql = "SELECT NOW() FROM t WHERE a = ?"
    real_rule = db_pool._NOW_RE
    real = db_pool._adapt_sql(sql)
    assert "NOW()::text" in real
    try:
        for i in range(20):
            # rule A: never matches -> NOW() is left alone
            rule = re.compile(f"this_never_matches_anything_xyz{i}")
            alive = weakref.ref(rule)
            db_pool._NOW_RE = rule
            assert db_pool._adapt_sql(sql) == "SELECT NOW() FROM t WHERE a = $1", i
            # drop A everywhere the test can reach: the module global, the local, re's compile cache
            db_pool._NOW_RE = real_rule
            del rule
            re.purge()
            assert alive() is not None, "the memo must keep a rule it has an entry for alive (its address is never reused)"
            # a DIFFERENT rule, freshly allocated: it gets its own rewrite, never A's cached one
            db_pool._NOW_RE = re.compile(r"\bNOW\(\)(?!::)", re.IGNORECASE)
            assert db_pool._adapt_sql(sql) == real, i
    finally:
        db_pool._NOW_RE = real_rule
        db_pool._adapt_sql_memo.cache_clear()     # the 20 throwaway rules are not left pinned for the rest of the lane
    assert db_pool._adapt_sql(sql) == real


def test_the_cache_key_holds_the_rule_objects_not_their_addresses():
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(db_pool._adapt_sql)))
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "id" not in calls, "the memo key must hold the rule objects, never id(rule)"
