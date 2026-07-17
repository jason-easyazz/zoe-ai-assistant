"""Invariants for the WS-origin + panel-subscribe guards (2026-07-03 incident).

Two production failures shipped together in the CSWSH/panel-guard batch:
1. the origin allowlist omitted the host's own LAN origin, 403-ing the kiosk's
   voice+push websockets (panel dead);
2. the new guards returned from inside ``async for db in get_db()``, leaking a
   pooled connection per call (pool max=10 drained -> every DB endpoint hung).
These tests pin both at the source level (main.py's import graph is too heavy
for a unit import here; the leak pattern is a structural property of the code).
"""
import pytest
import os
import re

pytestmark = pytest.mark.ci_safe

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(DATA, "main.py")


def _main_src() -> str:
    with open(MAIN, encoding="utf-8") as fh:
        return fh.read()


def test_allowlist_includes_self_lan_origins():
    src = _main_src()
    assert "_SELF_LAN_ORIGINS" in src, "self-LAN origin set missing"
    # Capture ONLY the return line ([^\n]+, not .+ under DOTALL which greedily
    # matches to end-of-file and made this assertion vacuously true).
    m = re.search(r"def _allowed_browser_origins.*?\n\s*return ([^\n]+)", src, re.DOTALL)
    assert m and "_SELF_LAN_ORIGINS" in m.group(1), (
        "_allowed_browser_origins() must include the host's own LAN origin in "
        "its return expression — the kiosk connects with "
        "Origin: https://<this-host-ip> and was 403'd when the CSWSH guard "
        "shipped without it (panel outage 2026-07-03)"
    )


def test_no_return_inside_get_db_generator_in_main():
    """`return`/`break` inside ``async for db in get_db()`` leaks the pooled
    connection (#953). The 2026-07-03 outage drained the pool via guards that
    ran on every kiosk reconnect. main.py must stay clean of the pattern —
    use `async with get_db_ctx() as db:` in non-route helpers instead."""
    src = _main_src().splitlines()
    inside_indent = None
    offenders = []
    for i, line in enumerate(src, 1):
        if "async for db in get_db()" in line:
            inside_indent = len(line) - len(line.lstrip())
            continue
        if inside_indent is not None:
            cur = len(line) - len(line.lstrip())
            if line.strip() and cur <= inside_indent:
                inside_indent = None
                continue
            if re.match(r"\s*(return\b|break\b)", line):
                offenders.append(i)
    assert not offenders, (
        f"main.py lines {offenders}: return/break inside `async for db in "
        f"get_db()` leaks the pooled connection — use get_db_ctx()"
    )
