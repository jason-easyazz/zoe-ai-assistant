"""Scheduled job callables must be serializable by APScheduler's job store.

Regression test for a silent outage. APScheduler's SQLAlchemyJobStore pickles
the callable, and a function nested inside ``lifespan()`` has no importable
reference, so ``add_job`` raises:

    "This Job cannot be serialized since the reference to its callable
     (<function lifespan.<locals>._run_music_discovery_batch ...>) could not be
     determined."

The surrounding try/except swallowed that as a logger.warning, so
``music_discovery_weekly`` NEVER registered while its flag was on, and
``router_selftrain_weekly`` carried the identical latent defect — guaranteed to
fail the same silent way the moment self-training was enabled.

Parsed with ast rather than imported: main.py has heavy import-time side
effects, and the property under test is purely structural.
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

pytestmark = pytest.mark.ci_safe

_MAIN = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "main.py"

#: Callables handed to APScheduler's add_job. Anything here MUST be module level.
SCHEDULED_CALLABLES = (
    "_run_music_discovery_batch",
    "_run_router_selftrain",
)


def _module_level_functions() -> set[str]:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("name", SCHEDULED_CALLABLES)
def test_scheduled_callable_is_module_level(name):
    """A nested callable cannot be pickled, and the failure is swallowed."""
    assert name in _module_level_functions(), (
        f"{name} is not module level — APScheduler cannot serialize it, add_job "
        f"will raise, and the surrounding try/except will swallow it as a "
        f"warning. The job then silently never exists."
    )


def test_no_add_job_target_is_defined_inside_lifespan():
    """Catches the general case, not just the two known names: any function
    passed to add_job must not be nested inside lifespan()."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))

    nested: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lifespan":
            for inner in ast.walk(node):
                if inner is node:
                    continue
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.add(inner.name)

    add_job_targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name):
                add_job_targets.add(first.id)

    assert add_job_targets, "found no add_job call sites — has the scheduler wiring moved?"
    offenders = sorted(add_job_targets & nested)
    assert not offenders, (
        f"these add_job targets are defined inside lifespan() and cannot be "
        f"serialized by the job store: {offenders}"
    )
