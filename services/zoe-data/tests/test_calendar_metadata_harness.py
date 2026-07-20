"""CI wrapper: desktop calendar metadata preservation + HTML escaping.

Two defects in services/zoe-ui/dist/calendar.html.

DATA LOSS. PUT /calendar/events/{id} REPLACES the metadata column wholesale --
routers/calendar.py builds `metadata = ?` from the payload with no merge against
the stored row. Four client call sites (rescheduleEvent, linkTasksToEvent,
saveEventDuration, saveEventLinkedTasks) sent `metadata: {linked_tasks: ...}`
and nothing else, so dragging or resizing an event silently destroyed
description, prep_items, get_ready_time, travel_time, attendees and reminders.
The read half lived in openEventPanel(), which loaded attendees and reminders
from GET .../attendees and .../reminders -- routes that do not exist on
zoe-data. Both 404'd, reset the lists to empty, and the next save persisted the
emptied lists.

XSS. calendar.html had no escape helper at all and interpolated task text,
reminder title/category/description, event titles, linked task text, person
name/email and notification messages into innerHTML, plus reminder message and
prep item text into value="..." attributes.

Validated against the real pre-fix file from origin/main: the node harness fails
18 of its 22 checks there, including the behavioural ones ("description must
survive a drag-reschedule", "the verbatim payload must not survive"), so this is
a real regression gate and not a synthetic guard.

test_server_still_replaces_metadata_wholesale is the counterpart pin: if zoe-data
ever starts MERGING metadata server-side, the client-side spread becomes
redundant and someone should learn that deliberately rather than leave it as
cargo cult.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "zoe-ui" / "dist"
HARNESS = DIST / "test_calendar_metadata_and_xss.js"
ROUTER = ROOT / "zoe-data" / "routers" / "calendar.py"


def _strip_py_comments(src: str) -> str:
    """Drop docstrings and # comments so prose cannot satisfy an assertion."""
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )


def test_calendar_metadata_and_xss_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        # A silent skip on CI means the data-loss and XSS guards quietly stop
        # running while the build still goes green. Skip is fine on a dev box;
        # on CI it is a failure.
        if os.environ.get("CI"):
            pytest.fail("node is required on CI to run the calendar harness")
        pytest.skip("Node.js is not installed on this host")
    assert HARNESS.is_file(), f"harness missing: {HARNESS}"
    try:
        # The harness executes extracted renderers in a vm. Bound it so a
        # runaway loop fails here with a useful message rather than burning the
        # CI job against the runner-level timeout. It normally runs well under a
        # second.
        proc = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"calendar harness did not finish within 60s: {HARNESS}")
    assert proc.returncode == 0, f"harness failed:\n{proc.stdout}\n{proc.stderr}"
    assert "checks passed" in proc.stdout


def test_server_still_replaces_metadata_wholesale():
    """Pin the server behaviour the client-side spread exists to compensate for.

    If this goes red because zoe-data now merges metadata, the four client
    spreads are no longer load-bearing -- re-examine them rather than deleting
    this test.
    """
    py = _strip_py_comments(ROUTER.read_text(encoding="utf-8"))
    assert 'if key == "metadata":' in py, (
        "the metadata branch in the update loop moved -- re-verify the wipe"
    )
    branch = py[py.index('if key == "metadata":'):]
    body = branch[: branch.index("elif")]
    assert 'updates.append("metadata = ?")' in body, (
        "server must still assign the metadata column outright"
    )
    assert "json.loads" not in body, (
        "server appears to MERGE metadata now -- the client-side spread in "
        "calendar.html may be redundant; re-examine it deliberately"
    )


def test_all_put_sites_preserve_existing_metadata():
    """The data-loss guard, asserted independently of node."""
    src = DIST / "calendar.html"
    code = "\n".join(
        line
        for line in src.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    # Only the UPDATE paths need the spread. createEventFromTasks POSTs a brand
    # new event, which has no prior metadata to preserve, so it is excluded on
    # purpose rather than by accident.
    put_sites = [
        "rescheduleEvent",       # drag to a new slot
        "linkTasksToEvent",      # drop a task onto an event
        "saveEventDuration",     # resize handle
        "saveEventLinkedTasks",  # tick a linked task
    ]
    missing = []
    for name in put_sites:
        start = code.index(f"function {name}(")
        body = code[start : start + 1200]
        meta = body[body.index("metadata: {") :]
        literal = meta[: meta.index("}")]
        if "...(event.metadata" not in literal:
            missing.append(name)
    assert not missing, (
        f"PUT site(s) without the existing-metadata spread: {missing}; "
        "the server replaces the column, so these wipe description/prep_items/"
        "get_ready_time/travel_time/attendees/reminders"
    )


def test_dead_attendee_and_reminder_routes_are_not_fetched():
    """openEventPanel must not call sub-routes that do not exist.

    They 404'd and reset attendees/reminders to empty, which the next save then
    persisted -- the read half of the same wipe.
    """
    code = "\n".join(
        line
        for line in (DIST / "calendar.html").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    assert "${event.id}/attendees" not in code, (
        "GET /calendar/events/{id}/attendees does not exist on zoe-data"
    )
    assert "${event.id}/reminders" not in code, (
        "GET /calendar/events/{id}/reminders does not exist on zoe-data"
    )

    py = _strip_py_comments(ROUTER.read_text(encoding="utf-8"))
    assert "attendees" not in py, (
        "routers/calendar.py gained an attendees route -- the client now reads "
        "attendees from event.metadata, so revisit that decision"
    )
