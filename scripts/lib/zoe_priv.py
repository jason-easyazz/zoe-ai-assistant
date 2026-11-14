"""The ONE routing of privileged calls for scripts (P0-1, docs/architecture/p0-deroot.md sections 4.3 and 5).

A caller that needs root asks for a FIXED helper with FIXED arguments. When the owner has installed the helper
(``/usr/local/libexec/zoe-priv/<name>``, root-owned, phase P0-0) the call is ``sudo -n <helper> <args>``, which
``sudoers.d/60-zoe-deroot`` allows as exact argv after P0-3 removes the broad grant. When it is not installed the
caller keeps its old command (the LEGACY path, which only the broad grant allows). Same code before and after the
grant goes; nothing here widens what runs.

Every route is logged by the caller as ONE fixed line, ``priv-route <helper>=<path>`` with ``path`` from the enum
below and never an argument, so the P0-1 watch can count which path ran without reading anything else.

Availability is probed the same way: ``sudo -n -l <helper> <args>`` (asks the policy, runs nothing) when the
helper is installed, else the old ``sudo -n true``. After P0-3 ``sudo -n true`` FAILS even though the helper is
allowed, so probing it would make the night window refuse a fragmented-RAM night it could compact.

Stdlib only; imported via the sibling-import convention (scripts/AGENTS.md ``lib/``).
"""
from __future__ import annotations

import os
from typing import Callable, Sequence

PRIV_DIR = "/usr/local/libexec/zoe-priv"

#: helper -> the argument tuples sudoers allows (mirrors sudoers.d/60-zoe-deroot; pinned by
#: tests/unit/test_p0_route_callers.py). A request outside this set is a bug in the caller: ValueError.
ALLOWED: "dict[str, frozenset[tuple[str, ...]]]" = {
    "zoe-mem-compact": frozenset({("0",), ("1",), ("3",)}),
    "zoe-nvmap-read": frozenset({("free",), ("clients",)}),
    "zoe-ha-config-export": frozenset({("storage",)}),
    "zoe-restore-drill-pg": frozenset(
        {("up",), ("down",), ("inspect", "list"), ("inspect", "schema")}
        | {("restore", db, s) for db in ("zoe", "multica") for s in ("pre-data", "data", "post-data")}
        | {("psql", db) for db in ("zoe", "multica", "drill")}),
    "zoe-db-exec": frozenset({("isready",), ("dumpall-globals",), ("restore-list",), ("restore-schema",),
                              ("restore-data",)}
                             | {(v, db) for v in ("dump", "psql", "psql-at") for db in ("zoe", "multica")}),
    "zoe-compose-deploy": frozenset({("zoe-auth",), ("zoe-auth", "--build"), ("zoe-ui",), ("modules",),
                                     ("logs", "zoe-auth"), ("logs", "zoe-ui")}),
}

# The route enum (the only words a route line may carry).
HELPER = "helper"        # sudo -n <installed helper> <fixed args>
LEGACY = "legacy"        # the pre-P0 command (needs the broad grant)
NONE = "none"            # neither is possible; the caller degrades as it always did
OVERRIDE = "override"    # a lab / test stand-in named by the caller's own override


def helper_path(name: str) -> str:
    if name not in ALLOWED:
        raise ValueError(f"unknown helper {name!r}")
    return os.path.join(PRIV_DIR, name)


def installed(name: str) -> bool:
    """The owner's copy is present: a regular, executable file (a symlink is not an install)."""
    p = helper_path(name)
    return os.path.isfile(p) and not os.path.islink(p) and os.access(p, os.X_OK)


def helper_argv(name: str, *args: str) -> "list[str]":
    if tuple(args) not in ALLOWED.get(name, frozenset()):
        raise ValueError(f"{name} does not take {args!r}")
    return ["sudo", "-n", helper_path(name), *args]


def route(name: str, args: Sequence[str], legacy: "Sequence[str] | None") -> "tuple[str, list[str] | None]":
    """``(path, argv)``: the helper when installed, else ``legacy`` (None = no legacy command: path NONE)."""
    if installed(name):
        return HELPER, helper_argv(name, *args)
    if legacy is None:
        return NONE, None
    return LEGACY, list(legacy)


def route_line(name: str, path: str) -> str:
    if name not in ALLOWED or path not in (HELPER, LEGACY, NONE, OVERRIDE):
        raise ValueError("route lines carry fixed words only")
    return f"priv-route {name}={path}"


def can_run(rc_of: Callable[["list[str]"], int], name: str, args: Sequence[str]) -> bool:
    """Would the routed call be allowed? ``rc_of(argv)`` runs a READ-ONLY probe and returns its exit status."""
    if installed(name):
        return rc_of(["sudo", "-n", "-l", helper_path(name), *helper_argv(name, *args)[3:]]) == 0
    return rc_of(["sudo", "-n", "true"]) == 0
