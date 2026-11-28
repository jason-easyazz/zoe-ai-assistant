"""The shadow client against the REAL Zoe OS lab kernel: the wire cannot drift silently.

Runs ``labs/zoe-os/shadow_daemon.py`` as a subprocess (the production entry point, on a temp state dir) and drives ``os_shadow`` against
its socket: a list add matches, a forget removes the kernel's record, a member outside the roster is never proposed. Also pins the
client's constants and every op / argument it sends against ``zoe_os.wire``.

A forget is checked on the BYTES: every file in the state directory (the kernel's database, its WAL and shared-memory files, the daemon's
own files and the client's park file) is scanned for planted, invented markers, and the same scan before the forget is the control.

SKIPPED, with a named reason, when ``labs/zoe-os`` is not in this tree (the lab stack is unmerged draft PRs): the zoe-data side can land
without it, and this test starts proving the contract the day both are present. Test code may read ``labs/``; zoe-data never imports it.
Lab only: hand-run, one short kernel process, SQLite in a temp dir.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import pytest

os_shadow = pytest.importorskip("os_shadow", reason="the zoe-data shadow client (os_shadow.py) is not in this tree: nothing to hold to the wire")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "labs" / "zoe-os"
DAEMON = LAB / "shadow_daemon.py"
pytestmark = [pytestmark, pytest.mark.skipif(
    not DAEMON.is_file(),
    reason="the lab Zoe OS kernel is not in this tree (labs/zoe-os/shadow_daemon.py): the shadow wire contract is unproven here")]

# invented, distinctive markers: each is searched for in raw bytes, so none may be a substring of anything else the kernel writes
MEMBER = "member-qv7a"
OTHER = "member-b"
ITEM = "zqx-plum-anchovy-item"
ITEM_AGAIN = "zqx-quince-kelp-item"
LIST = "zqx-larder-list"
#: the room AF_UNIX gives a socket path (108 bytes with the NUL on Linux), with a margin
SOCK_MAX = 100


@pytest.fixture
def state(tmp_path):
    """The daemon's state directory: ``tmp_path`` when ``k.sock`` inside it fits a Unix socket path, else a short temp dir that is
    removed afterwards (a long ``--basetemp`` must not turn into a bind error)."""
    if len(str(tmp_path / "k.sock")) <= SOCK_MAX:
        yield tmp_path
        return
    short = Path(tempfile.mkdtemp(prefix="osc"))
    try:
        yield short
    finally:
        shutil.rmtree(short, ignore_errors=True)


def start(state: Path, people: list, monkeypatch) -> subprocess.Popen:
    if os_shadow.read_marker(str(state)) is None:      # as the install tool leaves it, a minute later (no real wait)
        wall, mono, boot = os_shadow.clock()
        os_shadow.write_marker(str(state), wall - 60, mono - 60, boot, os_shadow.SETTLE_S)
    (state / "people.json").write_text(json.dumps({"people": people}))
    proc = subprocess.Popen([sys.executable, "-B", str(DAEMON), "--dir", str(state)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().startswith("zoe-os shadow kernel up")
    except BaseException:
        stop(proc)
        raise
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    os_shadow._reset_for_tests()
    getattr(os_shadow, "revive", lambda: True)()                      # what zoe-data's start-up does (a turn never starts the worker)
    return proc


def stop(proc: subprocess.Popen) -> None:
    os_shadow._reset_for_tests()
    proc.terminate()
    proc.wait(timeout=10)
    proc.stdout.close()


@pytest.fixture
def real_kernel(state, monkeypatch):
    proc = start(state, [MEMBER, OTHER], monkeypatch)
    try:
        yield state
    finally:
        stop(proc)


def settle(total, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sum(os_shadow.counters().values()) >= total:
            return
        time.sleep(0.02)
    raise AssertionError(f"counters never reached {total}: {os_shadow.counters()}")


needs_forget = pytest.mark.skipif(
    not hasattr(os_shadow, "forget_user"),
    reason="this os_shadow has no forget_user (it arrives with the shadow train, #2631): the forget half of the contract runs again "
           "on its own once that is on main")


def forget(member: str) -> int:
    return asyncio.run(os_shadow.forget_user(member))


def holds(state: Path, marker: str, *, kernel: bool | None = None) -> list:
    """The files under ``state`` whose raw bytes contain ``marker`` (relative names, sorted). ``kernel``: True = only the kernel's
    database files, False = only the daemon's and the client's own files, None = every regular file (the socket is not a file)."""
    found = []
    for root, _dirs, names in os.walk(state):
        for name in names:
            path = Path(root) / name
            rel = path.relative_to(state).as_posix()
            if kernel is not None and rel.startswith("kernel/") != kernel:
                continue
            if stat.S_ISREG(path.lstat().st_mode) and marker.encode() in path.read_bytes():
                found.append(rel)
    return sorted(found)


def kernel_db(state: Path) -> sqlite3.Connection:
    """A read-only look at the kernel's record (test inspection only; closed before the next kernel call)."""
    return sqlite3.connect(f"file:{state / 'kernel' / 'zoe-os.db'}?mode=ro", uri=True)


def known(state: Path) -> list:
    return json.loads((state / "known-people.json").read_text())["people"]


def what_still_names(state: Path, member: str) -> dict:
    """Every place in the kernel's record that names ``member``, by table (and event kind)."""
    with closing(kernel_db(state)) as db:
        return {
            "event_subject": db.execute("SELECT COUNT(*) FROM events WHERE subject = ?", (member,)).fetchone()[0],
            "people_links": db.execute("SELECT COUNT(*) FROM people WHERE person = ?", (member,)).fetchone()[0],
            "intents": db.execute("SELECT COUNT(*) FROM intents WHERE person = ?", (member,)).fetchone()[0],
            "event_payload_kinds": sorted(r[0] for r in db.execute(
                "SELECT DISTINCT kind FROM events WHERE instr(payload, ?) > 0", (json.dumps(member),))),
        }


#: what is left naming a forgotten member: NOTHING. The kernel's whole-person forget writes the notice and the ledger under a keyed
#: hash of the id (``subject_hash``, ``forget_ledger.subject``) and tombstones the member's grant records; the shadow's record-only grant
#: stays in memory (no new event names them until a restart re-grants a member still on the roster).
NOTHING_NAMES = {"event_subject": 0, "people_links": 0, "intents": 0, "event_payload_kinds": []}


def test_the_client_constants_and_ops_agree_with_the_kernels_wire_module():
    sys.path.insert(0, str(LAB))
    try:
        from zoe_os import wire
    finally:
        sys.path.remove(str(LAB))
    assert os_shadow.WIRE_VERSION == wire.VERSION
    assert os_shadow.MAX_REPLY <= wire.MAX_REPLY
    sent = {"publish": {"kind", "subject", "payload"}, "act": {"action", "on_behalf_of", "args", "caused_by"},
            "forget": {"subject", "actor", "entity", "topic", "stamp"}}
    for op, names in sent.items():
        assert op in wire.OPS and names <= set(wire.OPS[op]), op
        assert wire.check_args(op, {k: v for k, v in {"kind": "utterance", "subject": "s", "payload": {}, "action": "a", "on_behalf_of": "p",
                                                      "args": {}, "caused_by": [1], "actor": "p", "stamp": os_shadow.erase_stamp("p")}.items()
                                         if k in wire.OPS[op]}) == ""


@needs_forget
def test_a_real_list_add_matches_and_the_forget_leaves_none_of_it_readable(real_kernel):
    state = real_kernel
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name=LIST, item=ITEM)
    settle(1)
    assert os_shadow.counters() == {"match": 1}                       # event + verified receipt + read-back, over the real socket
    with closing(kernel_db(state)) as db:
        receipts = [r[0] for r in db.execute("SELECT seq FROM events WHERE kind = 'action_receipt' AND subject = ?", (MEMBER,))]
    assert len(receipts) == 1
    for marker in (ITEM, LIST):                                         # control: the scan finds what is there
        assert holds(state, marker, kernel=True), marker
    assert what_still_names(state, MEMBER)["people_links"] >= 2

    assert forget(MEMBER) >= 2                                          # the utterance and its receipt (and what derives from them)

    for marker in (ITEM, LIST):                                         # the item and the receipt's arguments: in NO file, kernel or not
        assert holds(state, marker) == [], marker
    with closing(kernel_db(state)) as db:
        assert db.execute("SELECT tombstoned, payload, subject, mentions, caused_by FROM events WHERE seq = ?",
                          (receipts[0],)).fetchone() == (1, None, None, None, None)
    # nothing the member did is left, and nothing in the kernel's record names them in plaintext: no event, no link, no action
    # bookkeeping, no notice or ledger row (hashed), no grant record (tombstoned). Still on the roster: people.json names them.
    assert what_still_names(state, MEMBER) == NOTHING_NAMES
    assert holds(state, MEMBER, kernel=True) == []
    assert MEMBER in known(state)

    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="rice")
    settle(2)
    assert os_shadow.counters() == {"match": 2}                       # the kernel is still serving after a forget
    # The kernel's own rule, pinned so nobody reads it as a client fault: it shields, for this member, the free text of what it
    # forgot ("forgotten means forever"), so the SAME item again is a counted refusal; anything else of theirs is recorded afresh.
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item=ITEM)
    settle(3)
    assert os_shadow.counters() == {"match": 2, "kernel_refused_live_did": 1}
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="oats")
    settle(4)
    assert os_shadow.counters() == {"match": 3, "kernel_refused_live_did": 1}


def test_the_real_kernel_refuses_a_member_it_has_no_grant_from_and_the_client_names_it(real_kernel):
    # a roster member the daemon granted works; one NOT in people.json is stopped by the client before the socket
    os_shadow.note_add("member-z", source="spoken", list_type="shopping", list_name="Shopping", item="x")
    settle(1)
    assert os_shadow.counters() == {"member_not_in_roster": 1}


def test_an_oversized_item_is_a_named_refusal_not_a_crash(real_kernel):
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="x" * 600)
    settle(1)
    assert os_shadow.counters() == {"kernel_refused_live_did": 1}       # the kernel's own argument check (wrong argument type)


# ---- the widened verbs, against the real kernel (os_shadow.note_act and the daemon's four acts) -------------------------------------
needs_verbs = pytest.mark.skipif(not hasattr(os_shadow, "note_act"),
                                 reason="this os_shadow predates the widened verbs (note_act): only list add is in the contract here")


@needs_verbs
@needs_forget
def test_a_reminder_an_alarm_and_a_remove_match_against_the_real_kernel(real_kernel, monkeypatch):
    import proactive.triggers.reminder_scan as scan
    from datetime import timedelta

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "list_add,list_remove,reminder_create,alarm_create")
    when = scan.zoe_now() + timedelta(minutes=45)
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="oat milk")
    os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="oat milk")
    os_shadow.note_reminder_create(MEMBER, source="service", note="call the plumber", due_date=when.date().isoformat(),
                                   due_time=when.strftime("%H:%M"), recurring=False)
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(4)
    assert os_shadow.counters_by_verb() == {v: {"match": 1} for v in ("list_add", "list_remove", "reminder_create", "alarm_create")}
    assert forget(MEMBER) >= 8                                          # four utterances and four receipts, all the member's


@needs_verbs
@needs_forget
def test_the_real_kernel_refuses_what_its_schema_cannot_take_and_the_client_names_it(real_kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="7:5", repeat="once")      # never sent: the client stops it
    # a repeating alarm is something the kernel's one-time alarm.set cannot say: the client counts it and never sends it either
    # (os_shadow.kernel_args; this line used to expect a match, from before the client stopped sending them)
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="weekdays")
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")    # what the verb CAN say: sent, matches
    settle(3)
    assert os_shadow.counters_by_verb() == {"alarm_create": {"not_expressible": 2, "match": 1}}


def removed_and_forgotten(state: Path, monkeypatch) -> subprocess.Popen:
    """MEMBER adds an item, is taken off the roster, and the next daemon (returned, still running) forgets them."""
    proc = start(state, [MEMBER, OTHER], monkeypatch)
    try:
        os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name=LIST, item=ITEM)
        settle(1)
        assert os_shadow.counters() == {"match": 1}
    finally:
        stop(proc)
    proc = start(state, [OTHER], monkeypatch)                           # MEMBER was taken off the roster
    try:
        assert known(state) == sorted([MEMBER, OTHER])                  # still forgettable: the kernel holds their record
        assert holds(state, MEMBER, kernel=False) == ["known-people.json"]      # control: the daemon's copy of the id is there
        assert forget(MEMBER) >= 2                                      # their old record is reached and erased
    except BaseException:
        stop(proc)
        raise
    return proc


@needs_forget
def test_a_member_taken_off_the_roster_is_forgotten_and_the_daemon_keeps_no_copy_of_their_id(state, monkeypatch):
    proc = removed_and_forgotten(state, monkeypatch)
    try:
        assert known(state) == [OTHER]                                  # rewritten once the kernel's forget had succeeded
        assert stat.S_IMODE((state / "known-people.json").stat().st_mode) == 0o600
        assert holds(state, MEMBER, kernel=False) == []                 # no daemon or client file holds the id, temp files included
        assert holds(state, ITEM) == [] and holds(state, LIST) == []
        assert what_still_names(state, MEMBER) == NOTHING_NAMES
        assert forget(MEMBER) == 0                                      # nothing is left to reach: the kernel no longer knows them
        assert forget("member-never") == 0                              # a stranger: the kernel never knew them
    finally:
        stop(proc)
    proc = start(state, [OTHER], monkeypatch)
    try:
        assert known(state) == [OTHER]                                  # a restart does not bring the id back
    finally:
        stop(proc)


@needs_forget
def test_a_forgotten_member_who_acts_again_is_recorded_afresh_and_stays_forgettable_off_the_roster(state, monkeypatch):
    """A whole-person forget erases what the member did and shields their words; it does not shield the PERSON: a member still on the
    roster who acts again is recorded afresh, and that new record is what keeps them forgettable once they leave the roster."""
    proc = start(state, [MEMBER, OTHER], monkeypatch)
    try:
        os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name=LIST, item=ITEM)
        settle(1)
        assert forget(MEMBER) >= 2
        os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item=ITEM_AGAIN)
        settle(2)
        assert os_shadow.counters() == {"match": 2}
    finally:
        stop(proc)
    proc = start(state, [OTHER], monkeypatch)
    try:
        assert known(state) == sorted([MEMBER, OTHER])                  # their NEW record keeps them forgettable
        assert forget(MEMBER) >= 2
        assert known(state) == [OTHER]
        assert holds(state, ITEM_AGAIN) == [] and holds(state, MEMBER, kernel=False) == []
    finally:
        stop(proc)


@needs_forget
def test_after_a_removed_member_is_forgotten_no_kernel_file_holds_their_id(state, monkeypatch):
    proc = removed_and_forgotten(state, monkeypatch)
    try:
        assert holds(state, MEMBER, kernel=True) == []
    finally:
        stop(proc)


# ---- the lists door (ZOE_OS_LISTS=kernel) against the real kernel -------------------------------------------------------------------------
def test_the_lists_door_client_and_the_real_kernel_agree_on_verbs_codes_and_the_decision(state, monkeypatch):
    """``list_service`` asks the REAL daemon: an adult's household delete is allowed and closed ``done``; a child's is refused with the
    kernel's own code and nothing is written; a stranger is refused; the panel may add. The kernel's file holds ids only."""
    import list_service

    sys.path.insert(0, str(LAB))
    try:
        from zoe_os import lists as kernel_lists, wire
    finally:
        sys.path.remove(str(LAB))
    assert tuple(os_shadow.DOOR_VERBS) == tuple(kernel_lists.VERBS)
    assert {kernel_lists.UNKNOWN_PERSON, kernel_lists.NOT_OWNER, kernel_lists.ADULTS_ONLY, kernel_lists.NOT_IN_MANIFEST} \
        <= set(os_shadow.DOOR_REFUSALS)
    assert {"verb", "person", "via", "list", "items", "owner", "visibility", "turn", "stamps"} == set(wire.OPS["list_ask"])
    assert {"ask", "outcome", "stamps"} == set(wire.OPS["list_report"])
    assert os_shadow.KERNEL_ERASED == kernel_lists.ERASED and kernel_lists.ERASED not in os_shadow.DOOR_REFUSALS

    class DB:
        def __init__(self):
            self.writes = []

        async def fetch(self, sql, *args):
            return [{"id": "list-zqx", "user_id": MEMBER, "visibility": "family"}]

        async def execute(self, sql, *params):
            self.writes.append(sql.split()[0])

    kid = "member-kid"
    proc = start(state, [{"id": MEMBER, "role": "adult"}, {"id": OTHER, "role": "adult"},
                         {"id": kid, "role": "child", "guardians": [MEMBER]}], monkeypatch)
    try:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
        monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "2000")       # a cold subprocess on a busy box; the deadline has its own test

        async def run():
            ok, panel, refused = DB(), DB(), DB()
            await list_service.delete_list_record(ok, user_id=OTHER, list_id="list-zqx")
            await list_service.create_item_record(panel, list_id="list-zqx", text=ITEM, user_id="guest-kiosk")
            out = []
            for who in (kid, "member-nobody"):
                with pytest.raises(list_service.ListRefused) as exc:
                    await list_service.delete_list_record(refused, user_id=who, list_id="list-zqx")
                out.append(exc.value.reason)
            return ok.writes, panel.writes, refused.writes, out

        assert asyncio.run(run()) == (["UPDATE"], ["INSERT"], [], ["adults_only", "unknown_person"])
        end = time.monotonic() + 10
        rows: list = []
        while time.monotonic() < end and len(rows) < 2:
            with closing(kernel_db(state)) as con:
                rows = con.execute("SELECT payload FROM events WHERE kind = 'action_receipt' AND payload LIKE '%\"done\"%'").fetchall()
            time.sleep(0.05)
        assert len(rows) == 2                                    # both allowed changes were reported and closed
        with closing(kernel_db(state)) as con:
            assert con.execute("SELECT COUNT(*) FROM intents WHERE receipt_seq IS NULL").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM snapshot_dependencies").fetchone()[0] == 0
        assert holds(state, ITEM) == [] and holds(state, "guest-kiosk") == [] and holds(state, "member-nobody") == []
    finally:
        stop(proc)


# ---- the reminders door (ZOE_OS_REMINDERS=kernel) against the real kernel -----------------------------------------------------------------
def test_the_reminders_door_client_and_the_real_kernel_agree_on_verbs_codes_and_the_decision(state, monkeypatch):
    """``reminder_service`` asks the REAL daemon on the area-neutral ops: an adult's edit of a child's reminder is allowed and
    closed ``done``; a child's delete of an adult's household reminder is refused with the kernel's own code and nothing is
    written; someone the roster does not name is refused, as actor and as the person a reminder is for. The kernel's file holds
    ids only: never the title. Control run: with ``reminders`` left out of the kernel's areas (``Kernel.__init__``) every ask is
    ``not_checked reason=protocol``, nothing is refused and this fails at the first ``ReminderRefused``."""
    import reminder_service as rs

    sys.path.insert(0, str(LAB))
    try:
        from zoe_os import door as kernel_door_mod, remind_door, wire
    finally:
        sys.path.remove(str(LAB))
    area = os_shadow.AREAS["reminders"]
    assert tuple(area.verbs) == tuple(remind_door.VERBS) and remind_door.AREA.name == "reminders"
    assert (area.ask_op, area.report_op, area.target, area.parts, area.named) == ("door_ask", "door_report", "target", None, True)
    assert {"area", "verb", "person", "via", "target", "parts", "owner", "visibility", "turn", "stamps"} == set(wire.OPS["door_ask"])
    assert {"ask", "outcome", "stamps"} == set(wire.OPS["door_report"])
    assert {kernel_door_mod.UNKNOWN_PERSON, kernel_door_mod.NOT_OWNER, kernel_door_mod.ADULTS_ONLY, kernel_door_mod.NOT_IN_MANIFEST} \
        <= set(os_shadow.DOOR_REFUSALS) and set(rs.ReminderRefused.lines) <= set(os_shadow.DOOR_REFUSALS)
    assert os_shadow.KERNEL_ERASED == kernel_door_mod.ERASED

    kid, rid, word = "member-kid", "rem_bar_0001", "womble"

    class _Cur:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class DB:
        def __init__(self, owner, visibility):
            self.writes = []
            self.row = {"id": rid, "user_id": owner, "title": word, "visibility": visibility, "due_date": "2099-06-15", "due_time": "08:00",
                        "recurring_pattern": None, "is_active": 1, "acknowledged": 0, "deleted": 0, "snoozed_until": None}

        async def execute(self, sql, params=()):
            if sql.lstrip().upper().startswith("SELECT"):
                return _Cur(dict(self.row))
            self.writes.append(sql.split()[0])
            return _Cur(None)

        async def commit(self):
            pass

    async def _noop(*a, **k):
        return None

    for name in ("require_feature_access", "_cancel_reminder_jobs_safe", "_reschedule_reminder_due_safe"):
        monkeypatch.setattr(rs, name, _noop)
    monkeypatch.setattr(rs.broadcaster, "broadcast", _noop)
    proc = start(state, [{"id": MEMBER, "role": "adult"}, {"id": OTHER, "role": "adult"},
                         {"id": kid, "role": "child", "guardians": [MEMBER]}], monkeypatch)
    try:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_REMINDERS", "kernel")
        monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 2.0)      # a cold subprocess on a busy box; the deadline has its own test

        def actor(who):
            return {"user_id": who, "role": "user"}

        async def run():
            ok, own, refused = DB(kid, "family"), DB(kid, "personal"), DB(MEMBER, "family")
            await rs.update_reminder_record(ok, actor(OTHER), rid, {"title": word + " two"})       # an adult, for a child
            await rs.acknowledge_reminder_record(own, actor(kid), rid)                              # their own
            out = []
            for who, db in ((kid, refused), ("member-nobody", refused), (OTHER, DB("member-nobody", "family"))):
                with pytest.raises(rs.ReminderRefused) as exc:
                    await rs.delete_reminder_record(db, actor(who), rid)
                out.append((exc.value.reason, db.writes))
            return ok.writes, own.writes, out

        assert asyncio.run(run()) == (["UPDATE"], ["UPDATE", "INSERT"], [("adults_only", []), ("unknown_person", []), ("unknown_person", [])])
        end = time.monotonic() + 10
        rows: list = []
        while time.monotonic() < end and len(rows) < 2:
            with closing(kernel_db(state)) as con:
                rows = con.execute("SELECT payload FROM events WHERE kind = 'action_receipt' AND payload LIKE '%\"done\"%'").fetchall()
            time.sleep(0.05)
        assert len(rows) == 2                                    # both allowed changes were reported and closed
        with closing(kernel_db(state)) as con:
            assert con.execute("SELECT COUNT(*) FROM intents WHERE receipt_seq IS NULL").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM snapshot_dependencies").fetchone()[0] == 0
            kept = [json.loads(p) for (p,) in con.execute("SELECT payload FROM events WHERE kind = 'action_receipt' AND payload LIKE '%allowed%'")]
        assert sorted(p["action"] for p in kept) == ["reminder.done", "reminder.edit"] and all(p["reminder"] == rid for p in kept)
        assert holds(state, word) == [] and holds(state, "member-nobody") == []
    finally:
        stop(proc)


@needs_forget
def test_the_real_kernel_orders_the_clients_asks_and_reports_against_its_forget(state, monkeypatch):
    """The three races of a door that has its own connection, against the REAL daemon, with the client's own stamps and forget:
    (1) an ask stamped before the member's forget reaches the kernel after it; (2) the report of an ask allowed before the forget
    arrives after it; (3) nothing on this side has to compensate (no parked forget exists to fail). Each ends with NOTHING in the
    kernel's record naming the member, and the member acting again is recorded afresh. Control run: with ``stamp`` left off the
    client's forget (``_forget_call``) the late ask is allowed and recorded and this fails at "erased"."""
    proc = start(state, [{"id": MEMBER, "role": "adult"}, {"id": OTHER, "role": "adult"}], monkeypatch)
    try:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
        monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "2000")

        async def ask(who):
            return await os_shadow.ask_list("item.add", who, list_id="list-zqx", item_ids=("item-zq1",), owner=MEMBER, household=True)

        def late(stamps):               # the same ask as the door thread sends it, stamped earlier, arriving now
            return os_shadow._door_call(os_shadow._Kernel(), "item.add", OTHER, {"list": "list-zqx", "owner": MEMBER,
                                        "visibility": "household", "stamps": stamps}, time.monotonic() + 2)

        decision, _, first, stamps = asyncio.run(ask(OTHER))        # another adult, on MEMBER's household list
        assert decision == "allow" and set(stamps) == {MEMBER, OTHER}
        assert "action_receipt" in what_still_names(state, MEMBER)["event_payload_kinds"]       # the control: the owner is named
        assert forget(MEMBER) >= 1                                  # the owner is forgotten, on the forget worker's connection
        assert what_still_names(state, MEMBER) == NOTHING_NAMES
        assert late(stamps) == ("not_checked", "erased", None)      # race 1
        os_shadow.report_list(first, "done", stamps)                # race 2: the queued report of the earlier ask
        again = asyncio.run(ask(OTHER))                             # same door thread, after the report: it has been answered
        assert what_still_names(state, MEMBER)["intents"] == 0
        with closing(kernel_db(state)) as con:
            assert con.execute("SELECT COUNT(*) FROM events WHERE kind = 'action_receipt' AND payload LIKE '%\"done\"%'").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM intents WHERE receipt_seq IS NULL").fetchone()[0] == 1      # only the new ask
        assert again[0] == "allow" and again[3][MEMBER] > stamps[MEMBER]            # acting again: recorded afresh
        assert what_still_names(state, MEMBER)["event_payload_kinds"] == ["action_receipt"]     # the new ask's, and only that
        assert not (state / "pending-forgets.json").exists()        # race 3: nothing was parked, nothing had to be
    finally:
        stop(proc)


def test_a_member_added_after_the_kernel_started_is_decided_by_the_real_kernel_in_the_same_ask(state, monkeypatch):
    """Both halves of the roster sync together, this client against the REAL daemon, no restart: the kernel was started knowing
    one adult; a child is added to Zoe's accounts afterwards. Their first list change has the roster rewritten from the accounts
    by the service (``os_shadow._roster_sync``), the kernel reads the new file at that very ask and allows it, and it decides by
    the role the ACCOUNT states (a child may change a household list's items, not delete the list). Before this the answer was
    ``unknown_person`` until the roster was rewritten by hand and the kernel restarted.

    Control run: with ``self.sync_roster()`` removed from the daemon's ``_op_list_ask`` the file is rewritten but the first
    answer is ("refuse", "unknown_person"); with the ``_roster_wanted`` call removed from ``ask_list`` it is the same answer and
    the file is never rewritten."""
    import db_compat
    import test_os_shadow_account_roster as accounts_fake

    proc = start(state, [{"id": MEMBER, "role": "adult"}], monkeypatch)
    try:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
        monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "2000")
        monkeypatch.setattr(accounts_fake, "ACCOUNTS", {MEMBER: ("admin", None), OTHER: ("child", None)})
        monkeypatch.setattr(accounts_fake, "MIRROR", [MEMBER, OTHER])
        monkeypatch.setattr(db_compat, "get_compat_db", accounts_fake._DB)

        async def run():
            add = await os_shadow.ask_list("item.add", OTHER, list_id="list-zqx", item_ids=("item-zq1",), owner=MEMBER, household=True)
            delete = await os_shadow.ask_list("list.delete", OTHER, list_id="list-zqx", owner=MEMBER, household=True)
            own = await os_shadow.ask_list("list.create", OTHER, list_id="list-zqy", owner=OTHER, household=False)
            theirs = await os_shadow.ask_list("item.add", MEMBER, list_id="list-zqy", owner=OTHER, household=True)
            return add[:2], delete[:2], own[:2], theirs[:2]

        assert asyncio.run(run()) == (("allow", ""), ("refuse", "adults_only"), ("allow", ""), ("allow", ""))
        assert json.loads((state / "people.json").read_text()) == {"people": [{"id": OTHER, "role": "child"},
                                                                              {"id": MEMBER, "role": "adult"}]}
        assert os_shadow.roster_counters() == {"written": 1} and proc.poll() is None
        assert json.loads((state / "known-people.json").read_text())["people"] == sorted([MEMBER, OTHER])
    finally:
        stop(proc)
