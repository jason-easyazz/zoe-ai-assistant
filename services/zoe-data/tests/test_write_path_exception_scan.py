"""Write-path DB exception handlers must log at WARNING+ — no silent "Zoe forgot".

Silent failure with green health is this project's #1 recurring bug class: the
FK trilogy (#1282), the invisible DB-pool leak (#1258), and the W0 saga all
presented as "Zoe just didn't remember that" with every health check green,
because the handler that swallowed the write logged at ``debug`` (or not at
all) and nothing above it ever noticed.

``person_extractor._write_activity`` was the archetype::

    except Exception as exc:
        logger.debug("_write_activity failed: %s", exc)   # → the fact is gone

This scan makes that pattern impossible to re-add silently. It is a pure
source scan (stdlib ``ast``), sibling to ``test_get_db_leak_scan.py``.

Definition — a "write-path handler"
-----------------------------------
An ``except`` handler in a module listed in ``WRITE_PATH_MODULES`` that ALL of:

1. **guards a SQL write** — its ``try`` body contains a ``.execute(...)`` /
   ``.executemany(...)`` call whose SQL argument is a string literal matching
   ``INSERT`` / ``UPDATE`` / ``DELETE``;
2. **catches broadly** — ``except:``, ``except Exception``, or
   ``except BaseException``. A *typed* handler (``except ValueError``,
   ``except HTTPException``) is deliberate, precise control flow — the
   opposite of the bug class — and is out of scope;
3. **swallows** — it does not re-raise at its top level. A handler that
   propagates is visible by construction; and
4. **is terminal** — its own body does NOT itself perform a SQL write.

Rule 4 is what keeps the scan honest. This codebase talks to both Postgres and
SQLite, so nearly every write is a nested try/except that retries the ``?``
param style after the ``$1`` style raises::

    try:
        await db.execute("INSERT ... VALUES ($1,$2)", a, b)   # Postgres
    except Exception:                                          # ← NOT a failure
        await db.execute("INSERT ... VALUES (?,?)", (a, b))    # SQLite retry

That inner handler is dialect dispatch, not a swallowed write — it is exempt
by rule 4, automatically and without an allowlist entry. The *outer* handler,
which runs only when both dialects failed, is the one that must speak up.

A flagged handler must call ``logger.warning`` / ``.error`` / ``.exception`` /
``.critical`` (a bare ``pass`` or a ``.debug`` is a failure), or carry an
explicit entry in ``ALLOWLIST`` below.

Deliberately out of scope (keep this scan narrow enough to stay enabled)
-----------------------------------------------------------------------
* **Reads.** A swallowed ``SELECT`` degrades a recall; a swallowed write
  destroys data. Only writes are pinned.
* **Non-SQL writes** (MemPalace / Chroma ingest, HTTP calls). No literal SQL
  to key on; a future scan can extend ``_is_sql_write`` when those writers
  share a helper.
* **Branch granularity.** A handler needs ≥1 WARNING+ call; the scan does not
  prove every ``if``/``else`` path inside it logs. Whole-handler is the
  cheap, unambiguous rule. (A log in a *nested* handler does not count —
  that fires for the inner failure, not the write; see ``_iter_own_nodes``.)
* **Dynamic SQL** (f-strings/variables). Only literal SQL is classified, so a
  computed statement is invisible here. Every write in the scanned modules is
  a literal today; keep it that way.
* **The rest of the voice runtime** (``zoe_core_client.py``, ``fast_tiers.py``,
  ``kokoro_sidecar.py``) — no literal-SQL writes there today.
  ``routers/voice_tts.py`` IS listed: its 4 handlers were promoted (replay-gated)
  in the follow-up to #1373.

Modules are added to ``WRITE_PATH_MODULES`` as they are burned down, exactly
like ``CLEANED_FILES`` in ``test_get_db_leak_scan.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Slim-dep-green (pure stdlib source scan): runs in the ci_safe lane on
# GitHub-hosted CI (marker-based selection, no validate.yml enumeration —
# see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_SERVICE_DIR = Path(__file__).resolve().parents[1]

# Burned-down modules — append per sweep PR.
WRITE_PATH_MODULES = [
    "person_extractor.py",
    "pending_suggestions.py",
    "database.py",
    "background_runner.py",
    "greploop_guard.py",
    "mcp_server.py",
    "ui_layouts.py",
    "zoe_agent.py",
    "routers/chat.py",
    "routers/panel_auth.py",
    "routers/voice_tts.py",
    "routers/people.py",
    "routers/stubs.py",
]

# Logging levels that make a failed write visible in the journal.
_LOUD_LEVELS = frozenset({"warning", "error", "exception", "critical"})

# SQL verbs that mutate state. A handler guarding one of these is a write path.
_WRITE_VERBS = ("insert", "update", "delete")

_EXECUTE_METHODS = frozenset({"execute", "executemany"})

# ── Allowlist ───────────────────────────────────────────────────────────────
# (module, function, first line of the handler's try) → one-line reason.
# Every entry is a handler that genuinely must not shout. Keep this short; a
# growing allowlist means the rule is wrong, not that the code is fine.
ALLOWLIST: dict[tuple[str, str], str] = {
    # ui_layouts is a compose *hint* cache, not a store of user facts: stored
    # trees are never rendered, only fed to the compose prompt as a few-shot
    # (see services/zoe-data/AGENTS.md). A lost write costs one slightly worse
    # layout and self-heals on the next compose — nothing a human would act on,
    # so these stay at info by design rather than adding journal noise.
    ("ui_layouts.py", "save_layout"): "layout hint cache — loss self-heals next compose, nothing to action",
    ("ui_layouts.py", "touch"): "usage-counter bump on the hint cache — loss is cosmetic",
}


def _sql_arg_text(call: ast.Call) -> str:
    """Concatenated text of the call's string-literal SQL arguments.

    Implicit adjacent-literal concatenation ("INSERT ..." "VALUES ...") parses
    as a single Constant, but a BinOp `+` of literals does not — walk both.
    """
    parts: list[str] = []
    for arg in call.args:
        for node in ast.walk(arg):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parts.append(node.value)
    return " ".join(parts).lower()


def _is_sql_write(node: ast.AST) -> bool:
    """True if `node` is a `.execute(...)` call whose literal SQL mutates rows."""
    if not isinstance(node, ast.Call):
        return False
    if getattr(node.func, "attr", None) not in _EXECUTE_METHODS:
        return False
    sql = _sql_arg_text(node)
    return any(f"{verb} " in sql for verb in _WRITE_VERBS)


def _contains_sql_write(body: list[ast.stmt]) -> bool:
    return any(_is_sql_write(n) for stmt in body for n in ast.walk(stmt))


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for `except:` / `except Exception` / `except BaseException`.

    A typed handler is deliberate error handling, not the blanket-swallow bug
    class this scan exists for.
    """
    exc = handler.type
    if exc is None:  # bare `except:`
        return True
    names = [n.id for n in ast.walk(exc) if isinstance(n, ast.Name)]
    return any(n in ("Exception", "BaseException") for n in names)


def _reraises(handler: ast.ExceptHandler) -> bool:
    """True if the handler unconditionally re-raises (top-level `raise`).

    A propagating handler is visible by construction. A `raise` nested inside
    an `if` does not count — the other branch may still swallow.
    """
    return any(isinstance(stmt, ast.Raise) for stmt in handler.body)


def _iter_own_nodes(stmt: ast.AST):
    """Yield `stmt` and its descendants, pruning nested except-handler bodies.

    A log inside a *nested* handler fires for that inner failure, not the outer
    write failure, so it must not exempt the outer handler. The nested `try`'s
    own body IS still walked — a log there runs on the outer handler's own path.
    """
    if isinstance(stmt, ast.ExceptHandler):
        return
    yield stmt
    for child in ast.iter_child_nodes(stmt):
        yield from _iter_own_nodes(child)


def _logs_loudly(handler: ast.ExceptHandler) -> bool:
    """True if the handler itself calls a logger at WARNING or above."""
    for stmt in handler.body:
        for node in _iter_own_nodes(stmt):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in _LOUD_LEVELS:
                return True
    return False


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str:
    """Name of the innermost function containing `target` ('<module>' if none)."""
    best = "<module>"
    best_lineno = -1
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if node.lineno <= target.lineno <= end and node.lineno > best_lineno:
            best, best_lineno = node.name, node.lineno
    return best


def find_silent_write_handlers(source: str) -> list[tuple[int, str]]:
    """(lineno, function) for every terminal write-path handler that stays quiet.

    See the module docstring for the full definition of "write-path handler".
    """
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not _contains_sql_write(node.body):
            continue  # rule 1: the try must guard a SQL write
        for handler in node.handlers:
            if not _is_broad(handler):
                continue  # rule 2: typed handler = deliberate control flow
            if _reraises(handler):
                continue  # rule 3: propagates, so it is not silent
            if _contains_sql_write(handler.body):
                continue  # rule 4: dialect-fallback retry, not a swallow
            if _logs_loudly(handler):
                continue
            offenders.append((handler.lineno, _enclosing_function(tree, handler)))
    return sorted(set(offenders))


@pytest.mark.parametrize("relpath", WRITE_PATH_MODULES)
def test_write_path_handlers_log_loudly(relpath):
    source = (_SERVICE_DIR / relpath).read_text(encoding="utf-8")
    offenders = [
        (line, func)
        for line, func in find_silent_write_handlers(source)
        if (relpath, func) not in ALLOWLIST
    ]
    assert not offenders, (
        f"{relpath}: exception handler(s) guarding a SQL write neither log at "
        f"WARNING+ nor appear in ALLOWLIST — a failed write here is a silent "
        f"'Zoe forgot' with green health (#1282, #1258): "
        + ", ".join(f"line {line} in {func}()" for line, func in offenders)
    )


def test_allowlist_entries_are_live():
    """An allowlist entry for a handler that no longer exists is stale."""
    for (relpath, func), reason in ALLOWLIST.items():
        assert reason.strip(), f"{relpath}:{func} allowlist entry needs a reason"
        source = (_SERVICE_DIR / relpath).read_text(encoding="utf-8")
        funcs = {
            n.name
            for n in ast.walk(ast.parse(source))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert func in funcs, (
            f"ALLOWLIST references {relpath}:{func}(), which no longer exists — "
            "drop the stale entry"
        )


# ── negative cases: the scan itself must catch the patterns it exists for ────

_SILENT_DEBUG = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception as exc:
        logger.debug("_write_thing failed: %s", exc)
'''

_SILENT_PASS = '''
async def _write_thing(db):
    try:
        await db.execute("UPDATE t SET a=$1 WHERE id=$2", a, i)
    except Exception:
        pass
'''

_SILENT_DELETE = '''
async def _purge(db):
    try:
        await db.execute("DELETE FROM t WHERE id=$1", i)
    except Exception:
        return None
'''


@pytest.mark.parametrize("bad", [_SILENT_DEBUG, _SILENT_PASS, _SILENT_DELETE])
def test_scan_flags_silent_write_handlers(bad):
    assert find_silent_write_handlers(bad), "scan missed a silent write handler"


def test_scan_accepts_loud_handler():
    good = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception as exc:
        logger.warning("_write_thing failed for user=%s: %s", user_id, exc)
'''
    assert find_silent_write_handlers(good) == []


def test_scan_exempts_dialect_fallback_handler():
    """The Postgres→SQLite retry is dispatch, not a swallowed write."""
    fallback = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception:
        await db.execute("INSERT INTO t (a) VALUES (?)", (a,))
'''
    assert find_silent_write_handlers(fallback) == []


def test_scan_ignores_typed_handler():
    """A typed handler is deliberate control flow, not a blanket swallow."""
    typed = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
'''
    assert find_silent_write_handlers(typed) == []


def test_scan_ignores_reraising_handler():
    """A handler that propagates is visible by construction."""
    reraise = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception:
        raise
'''
    assert find_silent_write_handlers(reraise) == []


def test_scan_flags_conditional_raise_with_silent_else():
    """A `raise` nested in an `if` leaves the other branch swallowing."""
    partial = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception as exc:
        if "unique" in str(exc):
            raise HTTPException(status_code=409)
'''
    assert find_silent_write_handlers(partial), "conditional raise must not exempt"


def test_nested_handler_log_does_not_exempt_outer_write_handler():
    """A log in a nested handler fires for the INNER failure, not the write.

    Letting it exempt the outer handler would mask exactly the swallow this
    scan exists to catch (Greptile review, PR #1373).
    """
    masked = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception:
        try:
            _cleanup()
        except Exception as inner:
            logger.warning("cleanup failed: %s", inner)
'''
    assert find_silent_write_handlers(masked), (
        "nested handler's log must not exempt the outer write handler"
    )


def test_log_in_nested_try_body_does_exempt():
    """A log in a nested `try` BODY does run on the outer handler's own path."""
    ok = '''
async def _write_thing(db):
    try:
        await db.execute("INSERT INTO t (a) VALUES ($1)", a)
    except Exception as exc:
        try:
            logger.warning("_write_thing failed: %s", exc)
        except Exception:
            pass
'''
    assert find_silent_write_handlers(ok) == []


def test_scan_ignores_read_only_handler():
    """Reads are out of scope — a swallowed SELECT is not a lost write."""
    read = '''
async def _read_thing(db):
    try:
        cur = await db.execute("SELECT id FROM t WHERE user_id=$1", u)
    except Exception:
        pass
'''
    assert find_silent_write_handlers(read) == []


def test_scan_reports_enclosing_function_name():
    offenders = find_silent_write_handlers(_SILENT_DEBUG)
    assert offenders and offenders[0][1] == "_write_thing"
