---
type: Reference
title: Test-Isolation Debugging Playbook
description: How to hunt a passes-alone-fails-in-full-run test failure to its exact poisoner — the mechanical bisect over ignored file sets, the os.environ/side-file trap for catching the precise writer, and the one bug class behind all five leaks found on 2026-07-06 (process-global state mutated without identity-restore) with its fix patterns.
tags: [testing, pytest, isolation, debugging, ci, playbook]
timestamp: 2026-07-07T00:00:00Z
---

# Test-Isolation Debugging Playbook

How to take a test that **passes alone but fails in the full run** from symptom to
exact root cause, mechanically. Five composition leaks were hunted this way on
2026-07-06 (#1061, #1073, #1074, #1076, plus one inside #1075) — every one was the
same bug class, and the technique converged in ≤ 30 minutes each.

## The bug class (check this FIRST)

All five leaks were **process-global state mutated without identity-restore**:

| Leak | Global mutated | Symptom at the victim |
|---|---|---|
| #1061 | `sys.modules["auth"]` del-and-reloaded (a NEW module object each time) | `app.dependency_overrides` keyed by the new `get_current_user` never match the routes' collection-time `Depends` → real auth runs → guest 403 |
| #1073 | `os.environ` — `mcp_server` import ran `bootstrap_runtime_env()`, injecting the production `.env` | `alembic/env.py` saw `POSTGRES_URL` → sqlite dialect renders became PostgresqlImpl |
| #1074 | `push.broadcaster._sequence` (cumulative singleton counter) | strict `sequence: 0` handshake assertion order-dependent |
| #1076 | `sys.modules["mempalace.*"]` replaced with test stubs at collection, never restored | lazy consumers (`memory_service`, `zoe_agent`) wrote into the test's fake collection → `ingest` returned None |
| in #1075 | `sys.modules` stubs (`openclaw_ws`, `database`, …) from a module-level install | later imports resolved the stubs → `ImportError`/wrong bindings |

Two structural facts make this class possible:

1. **pytest imports EVERY collected file before running ANY test** — deselection
   (`-m marker`) happens *after* collection, so a module-level side effect in an
   unmarked file still poisons a marked-only run. `--ignore` is the only thing
   that prevents the import.
2. **"Re-reload to restore" does NOT restore.** `del sys.modules[m]; import m;
   importlib.reload(m)` produces a *new module object*; everything that captured
   attributes of the old object at collection time (route `Depends`, module
   aliases) still points at the original. Only putting the **saved original
   object** back restores identity (see `test_telegram_link.py` for the
   canonical save/restore pattern).

## Step 1 — bisect to the poisoning FILE (mechanical, ~8 rounds)

Halve the candidate file set via `--ignore` flags; the failure follows the half
containing the poisoner. Keep the victim always collected; grep the sentinel
test name in the output to classify each round. Script pattern (adapt TARGET /
SENTINEL; full version in the #1073/#1076 PR bodies):

```python
def run(ignored):                      # True = victim still FAILS
    args = [sys.executable, "-m", "pytest", TESTS_DIR, "-q",
            "-p", "no:cacheprovider", "--override-ini", "asyncio_mode=auto"]
    for f in ignored: args += ["--ignore", str(f)]
    out = subprocess.run(args, capture_output=True, text=True).stdout
    return any(SENTINEL in l and "FAILED" in l for l in out.splitlines())

pool = sorted(all_test_files - {TARGET})
while len(pool) > 1:
    half = len(pool)//2
    if run(pool[half:]):   pool = pool[:half]      # poison in kept first half
    elif run(pool[:half]): pool = pool[half:]
    else: break                                     # multi-file interaction
```

Gotchas learned the hard way:
- **Run it in a worktree nothing else touches.** A PR-watcher rebasing the same
  worktree mid-bisect invalidates the rounds (happened once; restart on a
  stable tree).
- "Poisoner found" means *necessary under this composition*, not sufficient —
  the minimal pair may still pass (leak #2's pair did; the env was injected at
  collection by the import, which a two-file run doesn't reproduce).

## Step 2 — trap the exact writer (when the file isn't enough)

For env-var leaks, patch the setter and log to a **side file** — stdout gets
swallowed by `capsys` in passing tests, which hides earlier writers:

```python
# conftest-style plugin, load with -p; PYTHONPATH to its dir
_state = {"test": "<collection>"}   # mutable container — a bare module global
                                    # assigned inside the hook would need
                                    # `global` and is easy to get wrong
_orig = os.environ.__class__.__setitem__
def _spy(self, k, v):
    if k == "POSTGRES_URL":
        with open(LOG, "a") as f:
            f.write(f"SET during {_state['test']}\n"
                    + "".join(traceback.format_stack(limit=9)))
    return _orig(self, k, v)
os.environ.__class__.__setitem__ = _spy

def pytest_runtest_setup(item):
    _state["test"] = item.nodeid    # track current test
```

The same shape works for `sys.modules` (wrap `__setitem__` on a dict subclass)
or any singleton attribute.

## Fix patterns (in preference order)

1. **Identity-restore fixture** — snapshot the original object(s) before the
   test/module mutates them; put the SAME objects back in teardown
   (`sys.modules[k] = saved` / pop keys that were absent). Never "restore" by
   re-importing.
2. **Scope the mutation to the tests that need it** — module-level stub
   installs become install-in-fixture / restore-after (leak #4's fix), with a
   snapshot taken before any import-time install.
3. **Gate import-time side effects in production code** — a module that
   bootstraps process env for its spawned-script case must not do so on plain
   import (`if __name__ == "__main__":`, leak #2's fix), with a
   fresh-interpreter regression guard (`test_mcp_server_import_hygiene.py`).
4. **Reset singletons via monkeypatch in the asserting test** — when the
   assertion is strict against a cumulative counter, reset the counter rather
   than loosening the assertion (leak #3's fix).

## Related

- [Merge & deploy discipline](merge-and-deploy.md) — driving the fix PRs to merge.
- The full leak records live in the tech-debt plan
  (`../architecture/tech-debt-remediation-plan.md`, "Test-isolation leaks" entry)
  and the 2026-07-06 PR bodies (#1061, #1073, #1074, #1076).
