"""The kernel's roster is kept current by the running service, and a told list owner is stamped when the caller learned of them.

Two gaps closed behind ``ZOE_OS_LISTS=kernel`` (the lists door is live):

1. The roster (``people.json``) was only as current as its last hand-written copy: a member added to Zoe afterwards was refused
   ``unknown_person`` for every list change until someone ran the command and restarted the kernel. Now the door, on meeting a
   member the roster does not name, has the roster rewritten from the accounts in a task of its own (``os_shadow._roster_wanted``
   / ``_roster_sync``), waits for it only inside the ask's own budget, and the kernel (which reads the file again when it changes)
   decides that very ask.
2. A caller that TELLS the door a list's owner (only ``create_list_record``: the list has no row yet) hands over its own stamp
   snapshot, taken before anything it awaited (``list_service.stamps_now``), so an owner forgotten in the caller's own gap keeps
   the old stamp.

The kernel here is a fake on a real Unix socket that reads ``people.json`` at every ask, as the real one does since this change
(``labs/zoe-os/tests/test_roster_sync.py`` proves the real one). Invented ids only. Every test names the negative control that was
run against it (the code broken, the test seen to fail, the code restored).
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

import db_compat
import list_service
import os_shadow
import test_os_shadow_account_roster as accounts_fake
import user_erase_gate
from test_lists_kernel_door import DB, DOORS, DoorKernel

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]
OWNER, KID, NEW, TYPO = "member-ova", "member-kip", "member-nyx", "member-zzz"
DEMO = "demo_bar_0001"


class RosterKernel(DoorKernel):
    """Refuses ``unknown_person`` for an actor or an owner who is not in ``people.json`` NOW (read at every ask)."""

    def _answer(self, conn, skill, req):
        if req["op"] == "list_ask":
            a = req["args"]
            people = json.loads((self.state / "people.json").read_text())["people"]
            ids = {p["id"] if isinstance(p, dict) else p for p in people}
            named = [a.get("owner")] + ([] if a.get("via") == "panel" else [a["person"]])
            if any(m and m not in ids for m in named):
                return self._refuse(conn, req, "unknown_person")
        return super()._answer(conn, skill, req)


class Clock:
    def __init__(self):
        self.at = time.monotonic()

    def __call__(self):
        return self.at


@pytest.fixture
def house(monkeypatch):
    """Lists on, a kernel whose roster names OWNER and KID, and an account store that ALSO knows NEW (an adult added since)."""
    k = RosterKernel()
    state = Path(tempfile.mkdtemp(prefix="osr"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, KID]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LISTS", "kernel")
    monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "2000")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(accounts_fake, "ACCOUNTS", {OWNER: ("admin", None), KID: ("child", None), NEW: ("user", None)})
    monkeypatch.setattr(accounts_fake, "MIRROR", [OWNER, KID, NEW, DEMO])
    monkeypatch.setattr(db_compat, "get_compat_db", accounts_fake._DB)
    import db_pool

    k.pool = []

    async def opened():
        k.pool.append("open")

    async def closed():
        k.pool.append("close")

    monkeypatch.setattr(db_pool, "init_pool", opened)
    monkeypatch.setattr(db_pool, "close_pool", closed)
    k.clock = Clock()
    monkeypatch.setattr(os_shadow, "_mono", k.clock)
    k.reads = []
    real = os_shadow._roster_entries

    async def counted(db, demo=None):
        k.reads.append(dict(demo or {}))
        return await real(db, demo)

    monkeypatch.setattr(os_shadow, "_roster_entries", counted)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.state = state
    yield k
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def people(k) -> dict:
    return {(e["id"] if isinstance(e, dict) else e): (e["role"] if isinstance(e, dict) else None)
            for e in json.loads((k.state / "people.json").read_text())["people"]}


async def settle():
    task = os_shadow._roster_task
    if task is not None:
        await task


def remove(who, owner=OWNER):
    return DOORS["remove_item_record"][1](DB(owner=owner), who)


# ---- gap 1: the roster follows the accounts --------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_member_the_roster_does_not_name_gets_it_rewritten_and_the_same_change_is_decided(house, caplog):
    """A member added to Zoe after the roster was written makes a list change: the roster is rewritten from the accounts (their
    role with it), the kernel is asked ONCE and allows that very change. The service's pool is never opened or closed by it,
    the file stays 0600, and the one log line carries a closed code and no id.

    Negative control: the ``_roster_wanted`` call removed from ``ask_list`` -> the kernel refuses ``unknown_person`` and the
    change raises ``ListRefused`` (this test fails at its first line of work)."""
    caplog.set_level(logging.DEBUG)
    await remove(NEW)
    assert len(house.ops("list_ask")) == 1                                  # decided in the same ask: no refusal first
    assert people(house) == {OWNER: "adult", KID: "child", NEW: "adult", DEMO: None}
    assert (house.state / "people.json").stat().st_mode & 0o777 == 0o600
    assert house.pool == [] and os_shadow.roster_counters() == {"written": 1}
    assert "ZOE_OS_ROSTER result=written" in caplog.text
    assert not any(uid in caplog.text for uid in (OWNER, KID, NEW, DEMO))
    assert NEW in os_shadow.roster()                                        # ... and this process knows it without reading again
    await remove(NEW)
    assert len(house.reads) == 1                                            # known now: nothing more is read or written


@pytest.mark.asyncio
async def test_a_list_whose_owner_the_roster_does_not_name_is_healed_the_same_way(house):
    """The kernel refuses a list whose OWNER it does not know, whoever asks. A known member changing a new member's list
    triggers the same rewrite.

    Negative control: ``_roster_wanted`` given the actor only (``tuple(stamps)[:1]``) -> refused ``unknown_person``."""
    await remove(OWNER, owner=NEW)
    assert people(house)[NEW] == "adult" and len(house.ops("list_ask")) == 1


@pytest.mark.asyncio
async def test_an_id_that_is_not_a_member_cannot_cause_a_rewrite_per_request(house):
    """A typo, a deleted member, a sentinel: refused every time (the kernel's answer, final), and the accounts are read ONCE for
    it however often it asks; it may ask again only after ``ROSTER_MISS_S``. Other unknown ids inside ``ROSTER_SYNC_MIN_S`` of
    the last rewrite do not get one either.

    Negative control: both rate limits removed from ``_roster_wanted`` (the ``if not fresh and ...: return None`` and the
    ``ROSTER_SYNC_MIN_S`` return) -> 30 asks read the accounts 30 times."""
    for _ in range(30):
        with pytest.raises(list_service.ListRefused):
            await remove(TYPO)
    assert len(house.reads) == 1 and os_shadow.roster_counters() == {"written": 1}   # (the accounts had a new member too)
    for n in range(5):                                                      # other strangers, inside the floor
        with pytest.raises(list_service.ListRefused):
            await remove(f"member-q{n}")
    assert len(house.reads) == 1
    house.clock.at += os_shadow.ROSTER_SYNC_MIN_S + 1                       # past the floor, not past the per-id wait
    with pytest.raises(list_service.ListRefused):
        await remove(TYPO)
    assert len(house.reads) == 1
    house.clock.at += os_shadow.ROSTER_MISS_S
    with pytest.raises(list_service.ListRefused):
        await remove(TYPO)
    assert len(house.reads) == 2


@pytest.mark.asyncio
async def test_a_slow_rewrite_never_holds_a_list_change_past_its_budget_and_never_runs_on_the_loop(house, monkeypatch):
    """The rewrite is a task of its own. An ask waits for it for a share of its OWN budget and no longer: a slow one means this
    change is put to the kernel as it is (refused once), the next attempt works, and the loop keeps turning throughout. Every
    file touch of the rewrite is in a thread.

    Negative control: the wait made unbounded (``await syncing`` in ``ask_list``) -> the first change takes the whole 0.6 s;
    and ``_roster_store`` called directly instead of through ``asyncio.to_thread`` -> the thread assert fails."""
    monkeypatch.setenv("ZOE_OS_LISTS_ASK_MS", "100")
    main, where = threading.get_ident(), []
    for name in ("_roster_home", "_demo_roles_read", "_roster_store"):
        real = getattr(os_shadow, name)
        monkeypatch.setattr(os_shadow, name, lambda *a, _real=real, _name=name: (where.append((_name, threading.get_ident())),
                                                                              _real(*a))[1])
    real_entries = os_shadow._roster_entries

    async def slow(db, demo=None):
        await asyncio.sleep(0.6)
        return await real_entries(db, demo)

    monkeypatch.setattr(os_shadow, "_roster_entries", slow)
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    ticker = asyncio.create_task(tick())
    started = time.perf_counter()
    with pytest.raises(list_service.ListRefused) as refused:
        await remove(NEW)
    took = time.perf_counter() - started
    assert refused.value.reason == "unknown_person" and took < 0.3 and ticks >= 5
    await settle()
    ticker.cancel()
    await remove(NEW)                                                       # the next attempt: the roster has them
    assert [n for n, _ in where] == ["_roster_home", "_demo_roles_read", "_roster_store"]
    assert all(ident != main for _, ident in where)


@pytest.mark.asyncio
async def test_hand_stated_demo_roles_survive_the_services_rewrite_and_never_state_a_role_for_a_person(house):
    """The demo roles given to the command are kept beside the roster (0600) and read by every rewrite: the command's own with
    no arguments, and the service's. A kept pair that names a real id (the file edited by hand) is dropped: a person's role
    comes from their account.

    Negative control: ``_roster_sync`` reading no demo roles (``demo = {}``) -> the demo id falls back to least access after
    the service's rewrite; and ``_demo_roles_read`` taking the pairs as written (no ``_demo_roles`` check) -> the child is
    written as an adult."""
    await asyncio.to_thread(os_shadow.write_roster_from_accounts, house.state / "people.json", {DEMO: "adult"})
    assert house.pool == ["open", "close"]                                  # the COMMAND opens the database itself, and closes it
    kept = house.state / os_shadow.DEMO_ROLES_FILE
    assert json.loads(kept.read_text()) == {"roles": {DEMO: "adult"}} and kept.stat().st_mode & 0o777 == 0o600
    assert people(house)[DEMO] == "adult"
    await asyncio.to_thread(os_shadow.write_roster_from_accounts, house.state / "people.json")
    assert people(house)[DEMO] == "adult"                                   # run again with none stated: the kept ones hold
    (house.state / "people.json").write_text(json.dumps({"people": [OWNER, KID]}))      # an older roster, NEW not on it
    kept.write_text(json.dumps({"roles": {DEMO: "adult", KID: "adult", OWNER: "owner"}}))
    house.pool.clear()
    os_shadow._reset_for_tests()
    await remove(NEW)
    assert people(house) == {OWNER: "adult", KID: "child", NEW: "adult", DEMO: "adult"}
    assert house.reads[-1] == {DEMO: "adult"} and house.pool == []          # the service's rewrite: the pool is left alone


@pytest.mark.asyncio
async def test_one_failed_account_lookup_abandons_the_rewrite_so_nobody_is_downgraded(house, monkeypatch, caplog):
    """An adult whose lookup fails during the service's rewrite must stay an adult: the rewrite is abandoned (``failed``) and
    the roster in force stays, never a plain id for them (least access until a later good rewrite).

    Negative control: the plain-id fallback back in ``_roster_entries`` -> the roster is rewritten with OWNER as a plain id
    and the first assert fails."""
    import enrollment_state

    real = enrollment_state._account_facts

    async def flaky(uid, db=None):
        if uid == OWNER:
            raise RuntimeError("lookup failed")
        return await real(uid, db=db)

    monkeypatch.setattr(enrollment_state, "_account_facts", flaky)
    caplog.set_level("INFO", logger="os_shadow")
    with pytest.raises(list_service.ListRefused):                           # NEW is still unknown: refused, as before the rewrite
        await remove(NEW)
    await settle()
    assert people(house) == {OWNER: "adult", KID: None}                     # exactly the roster that was in force
    assert any("ZOE_OS_ROSTER result=failed" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o606])
@pytest.mark.asyncio
async def test_a_demo_roles_file_others_could_write_or_read_states_no_role(house, mode, caplog):
    """The kept demo roles are read only from a file that is this user's and 0600; anything looser is ignored (a closed code is
    logged), so a file another account could write never supplies a role.

    Negative control: ``_demo_roles_read`` without the mode check -> the demo id is written as an adult."""
    kept = house.state / os_shadow.DEMO_ROLES_FILE
    kept.write_text(json.dumps({"roles": {DEMO: "adult"}}))
    kept.chmod(mode)
    caplog.set_level("WARNING", logger="os_shadow")
    await remove(NEW)
    await settle()
    assert people(house)[DEMO] is None and people(house)[NEW] == "adult"
    assert any("demo_roles=ignored_mode" in rec.getMessage() for rec in caplog.records)
    kept.chmod(0o600)
    assert os_shadow._demo_roles_read(house.state) == {DEMO: "adult"}       # the same file, tightened: read


@pytest.mark.parametrize("value", [None, "off", "shadow"])
def test_with_lists_off_a_caller_that_starts_a_list_copies_nothing(monkeypatch, value):
    """``stamps_now`` is one flag read with lists off: no copy of the stamp map. Control: the unconditional copy fails this."""
    if value is None:
        monkeypatch.delenv("ZOE_OS_LISTS", raising=False)
    else:
        monkeypatch.setenv("ZOE_OS_LISTS", value)

    def never():
        raise AssertionError("the stamp map was copied with lists off")

    monkeypatch.setattr(os_shadow, "stamp_snapshot", never)
    assert list_service.stamps_now() is None


@pytest.mark.asyncio
async def test_the_service_never_makes_a_roster_or_a_state_directory_and_never_writes_an_empty_one(house, monkeypatch):
    """No roster installed = no kernel to keep current: nothing is created. And a read of the accounts that comes back with
    nobody never empties the roster (that is the owner's to state, with the command).

    Negative control: ``_roster_home`` returning the state directory whether or not people.json is there -> a roster appears;
    and the ``if not entries`` return removed from ``_roster_store`` -> the roster is written empty."""
    before = (house.state / "people.json").read_bytes()
    monkeypatch.setattr(accounts_fake, "ACCOUNTS", {})
    monkeypatch.setattr(accounts_fake, "MIRROR", [])
    with pytest.raises(list_service.ListRefused):
        await remove(NEW)
    assert (house.state / "people.json").read_bytes() == before and os_shadow.roster_counters() == {"empty": 1}
    (house.state / "people.json").unlink()
    os_shadow._reset_for_tests()
    monkeypatch.setattr(accounts_fake, "ACCOUNTS", {NEW: ("user", None)})
    await remove(NEW)                                    # the fake kernel cannot read a roster either: the change proceeds
    await settle()
    assert not (house.state / "people.json").exists() and os_shadow.roster_counters() == {"no_roster": 1}
    assert not {p.name for p in house.state.iterdir()} & {"people.json", os_shadow.DEMO_ROLES_FILE}


@pytest.mark.asyncio
async def test_a_role_stated_since_arrives_with_the_periodic_rewrite_which_no_change_waits_for(house):
    """Everyone is on the roster, so nothing is missing; but KID's account now says child where the roster has a plain id. While
    list changes are being made the roster is rewritten every ``ROSTER_REFRESH_S``, in the background (the ask that begins it
    is given nothing to wait for).

    Negative control: the refresh clause removed from ``_roster_wanted`` (return None whenever nobody is missing) -> KID stays
    a plain id."""
    await remove(OWNER)
    assert house.reads == [] and people(house)[KID] is None                 # nobody missing, not due: nothing is read
    house.clock.at += os_shadow.ROSTER_REFRESH_S + 1
    assert os_shadow._roster_wanted((OWNER,)) is None                       # begun, and nothing to wait for
    await settle()
    assert people(house)[KID] == "child" and len(house.reads) == 1
    await remove(OWNER)
    assert len(house.reads) == 1                                            # and not again until the next one is due


@pytest.mark.parametrize("held_back_by", ["a stale unknown id", "a full miss memory"])
@pytest.mark.asyncio
async def test_an_overdue_refresh_runs_whatever_the_miss_rules_say(house, held_back_by):
    """The periodic refresh carries role changes and removals, on its own clock. An id that keeps being unknown (already
    asked for, so not fresh) or a full miss memory gates only the EXTRA on-demand rewrite; neither may hold the refresh back.

    Negative control: the old rule (return None whenever someone is missing and nobody is fresh) -> with a stale unknown id
    in the ask the overdue refresh never begins and KID stays a plain id; with the cap returning None when the memory is
    full the second case fails the same way."""
    stranger = "member-nobody"
    await remove(OWNER)
    assert house.reads == [] and people(house)[KID] is None
    house.clock.at += os_shadow.ROSTER_REFRESH_S + 1
    if held_back_by == "a stale unknown id":
        os_shadow._roster_asked[stranger] = house.clock.at                  # asked for a moment ago: not fresh
    else:
        for n in range(os_shadow.ROSTER_MISS_MAX):
            os_shadow._roster_asked[f"member-x{n}"] = house.clock.at        # the memory is full of recent misses
    assert os_shadow._roster_wanted((OWNER, stranger)) is None              # begun in the background; nothing to wait for
    await settle()
    assert len(house.reads) == 1 and people(house)[KID] == "child"


@pytest.mark.asyncio
async def test_a_rewrite_that_fails_changes_nothing_and_whoever_asked_may_ask_again(house, monkeypatch):
    """The accounts cannot be read: the roster file is untouched, the change gets the kernel's answer, one closed code is
    counted, and the same member may trigger the next rewrite once the floor has passed (they are not held off for
    ``ROSTER_MISS_S`` by a fault that was not theirs).

    Negative control: ``_roster_asked.clear()`` removed from the failed branch of ``_roster_sync`` -> the second attempt reads
    nothing and is refused again."""
    class Broken(accounts_fake._DB):
        async def execute(self, query, params=()):
            raise RuntimeError("database fault")

    before = (house.state / "people.json").read_bytes()
    monkeypatch.setattr(db_compat, "get_compat_db", Broken)
    with pytest.raises(list_service.ListRefused):
        await remove(NEW)
    assert (house.state / "people.json").read_bytes() == before and os_shadow.roster_counters() == {"failed": 1}
    monkeypatch.setattr(db_compat, "get_compat_db", accounts_fake._DB)
    house.clock.at += os_shadow.ROSTER_SYNC_MIN_S + 1
    await remove(NEW)
    assert people(house)[NEW] == "adult"


@pytest.mark.parametrize("value", [None, "off", "shadow"])
@pytest.mark.asyncio
async def test_with_lists_off_nothing_of_the_roster_is_looked_at_or_written(house, monkeypatch, value):
    """Flag off is exactly as before: no rewrite, no task, no read of the accounts, no kernel call, the file untouched.

    Negative control: ``_roster_wanted`` called from ``list_service._gate`` ahead of its flag read -> ``never`` raises."""
    def never(*a, **k):
        raise AssertionError("the roster was looked at with lists off")

    if value is None:
        monkeypatch.delenv("ZOE_OS_LISTS")
    else:
        monkeypatch.setenv("ZOE_OS_LISTS", value)
    for name in ("_roster_wanted", "_roster_sync", "_roster_home", "_roster_store", "_roster_seen", "roster"):
        monkeypatch.setattr(os_shadow, name, never)
    before = (house.state / "people.json").read_bytes()
    for name in sorted(DOORS):
        await DOORS[name][1](DB(), NEW)
    assert (house.state / "people.json").read_bytes() == before
    assert house.reads == [] and house.ops("list_ask") == [] and os_shadow._roster_task is None


def test_the_roster_is_replaced_in_one_step_by_every_writer(house):
    """The kernel reads the file whenever it changes, so no writer may leave half a file where it can be read: each writes a new
    file and renames it (the inode moves, which is also how the kernel notices).

    Negative control: ``write_roster`` writing in place (``open(path, "w")``) -> the inode does not move."""
    path = house.state / "people.json"
    first = path.stat().st_ino
    os_shadow.write_roster([OWNER, KID, NEW], dest=path)
    second = path.stat().st_ino
    assert second != first and path.stat().st_mode & 0o777 == 0o600
    os_shadow._roster_store(house.state, [OWNER])
    assert path.stat().st_ino != second
    assert not [p.name for p in house.state.iterdir() if p.name.endswith(".tmp")]              # no temp file left behind


def test_the_in_service_read_takes_an_open_handle_and_only_the_command_touches_the_pool():
    """Structural: ``_roster_entries`` (what the service calls) never names the pool; ``_roster_from_accounts`` (the command)
    opens and closes it; and the service's ``_roster_sync`` never calls the command's reader.

    Negative control: ``_roster_sync`` calling ``_roster_from_accounts`` -> the last assert fails (and it would close the live
    pool)."""
    tree = ast.parse((ROOT / "os_shadow.py").read_text("utf-8"))
    fns = {n.name: ast.dump(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "init_pool" not in fns["_roster_entries"] and "close_pool" not in fns["_roster_entries"]
    assert "init_pool" in fns["_roster_from_accounts"] and "close_pool" in fns["_roster_from_accounts"]
    for name in ("_roster_sync", "_roster_wanted", "_roster_store", "ask_list"):
        assert "_roster_from_accounts" not in fns[name] and "close_pool" not in fns[name] and "ensure_state_dir" not in fns[name]


# ---- gap 2: a told owner is stamped when the caller learned of them ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_owner_forgotten_in_the_callers_own_gap_keeps_their_old_stamp(house):
    """Starting a list is the one door whose owner is TOLD (the list has no row to read). The caller takes its stamp snapshot
    first; the owner's forget is then confirmed while the caller is still on its way to the door; the ask carries the stamp from
    BEFORE it (the kernel then records nothing: not strictly newer than the forget's mark).

    Negative control: ``_gate`` ignoring the caller's snapshot (always ``os_shadow.stamp_snapshot()``) -> the ask carries the
    moved-on stamp and the equality fails."""
    before = os_shadow.erase_stamp(OWNER)
    at = list_service.stamps_now()                                          # the caller, before anything it awaits
    assert await os_shadow.forget_for_erase(OWNER) == 3                     # forgotten AND confirmed in the caller's gap
    await list_service.create_list_record(DB(), user_id=OWNER, name="Vellum", list_type="personal", visibility="personal",
                                          stamps_at=at)
    (forget,) = house.ops("forget")
    asked = house.ops("list_ask")[-1]["stamps"]
    assert asked[OWNER] == before == forget["stamp"] and os_shadow.erase_stamp(OWNER) > before


@pytest.mark.asyncio
async def test_a_spoken_add_that_starts_a_list_stamps_its_owner_from_before_its_own_lookups(house):
    """A real caller: ``add_item_to_list`` looks for the list (awaits) and, finding none, starts one. The owner is forgotten
    during that lookup; the ``list.create`` ask still carries the old stamp.

    Negative control: the ``stamps_at=stamps_at`` argument removed from the ``create_list_record`` call inside
    ``add_item_to_list`` is a TypeError (the argument is required); with ``stamps_at=stamps_now()`` written AT that call instead
    -> the ask carries the moved-on stamp and this fails."""
    class NoListYet(DB):
        forgot = False

        async def execute(self, sql, *params):
            if sql.lstrip().upper().startswith("SELECT") and not self.forgot:
                self.forgot = True
                assert await os_shadow.forget_for_erase(OWNER) == 3
            await super().execute(sql, *params)
            return type("Cur", (), {"fetchone": staticmethod(_none), "fetchall": staticmethod(_empty)})()

    async def _none():
        return None

    async def _empty():
        return []

    before = os_shadow.erase_stamp(OWNER)
    await list_service.add_item_to_list(NoListYet(), user_id=OWNER, list_type="personal", list_name="Vellum", text="womble",
                                        new_list_visibility="personal")
    created = [a for a in house.ops("list_ask") if a["verb"] == "list.create"]
    assert len(created) == 1 and created[0]["stamps"][OWNER] == before
    assert os_shadow.erase_stamp(OWNER) > before


_PARENTS: dict = {}


def _takes_first(fn) -> bool:
    """``stamps_at`` is assigned EXACTLY ONCE in the function, from ``stamps_now()``, before its first ``await`` (a second
    assignment after an await would hand the door a later snapshot under the same name)."""
    first_await = min([n.lineno for n in ast.walk(fn) if isinstance(n, ast.Await)] or [10 ** 9])
    binds = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "stamps_at" and isinstance(n.ctx, ast.Store)]
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "stamps_at" for t in n.targets)]
    return (len(binds) == 1 and len(assigns) == 1 and "stamps_now" in ast.dump(assigns[0].value)
            and assigns[0].lineno < first_await)


def _scan(src: str) -> list:
    """The functions that call ``create_list_record`` without ``stamps_at=stamps_at``, or whose ``stamps_at`` was not taken
    first (a nested helper is judged by its outermost enclosing function)."""
    tree = ast.parse(src)
    _PARENTS.clear()
    for outer in ast.walk(tree):
        if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(outer):
                if inner is not outer and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _PARENTS.setdefault(id(inner), outer)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == "create_list_record":
            continue
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", "")) == "create_list_record"]
        calls = [c for c in calls if not any(isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef)) and g is not fn
                                             and any(n is c for n in ast.walk(g)) for g in ast.walk(fn))]
        if not calls:
            continue
        told = all(any(k.arg == "stamps_at" and getattr(k.value, "id", "") == "stamps_at" for k in c.keywords) for c in calls)
        if not told or not _takes_first(_PARENTS.get(id(fn), fn)):
            out.append(fn.name)
    return out


def _modules() -> list:
    """Every module of the service (tests, migrations and caches aside): a caller in a NEW file is scanned too."""
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return sorted(p for p in ROOT.rglob("*.py") if not skip.intersection(p.relative_to(ROOT).parts))


def test_every_caller_that_starts_a_list_takes_its_stamps_before_anything_it_awaits():
    """Structural, over every module that can start a list: each function that calls ``create_list_record`` hands it
    ``stamps_at=stamps_at``, where ``stamps_at`` was taken from ``stamps_now()`` before that function's first ``await`` (for a
    nested helper: its enclosing function's). And no door but ``list.create`` is ever told an owner.

    Every module of the service is scanned, so a caller in a new file is held to it too.

    Negative control (planted below): a caller that awaits first, one that passes nothing, one that passes a fresh snapshot
    and one that reassigns ``stamps_at`` after an await are all reported."""
    modules = _modules()
    assert len(modules) > 100 and ROOT / "routers" / "lists.py" in modules
    for path in modules:
        assert _scan(path.read_text("utf-8")) == [], path.name
    planted = ("async def late(db):\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await create_list_record(db, user_id='u', stamps_at=stamps_at)\n"
               "async def silent(db):\n    stamps_at = stamps_now()\n    await create_list_record(db, user_id='u')\n"
               "async def fresh(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n"
               "    await create_list_record(db, user_id='u', stamps_at=stamps_now())\n"
               "async def again(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n    stamps_at = stamps_now()\n"
               "    await create_list_record(db, user_id='u', stamps_at=stamps_at)\n"
               "async def good(db):\n    stamps_at = stamps_now()\n    await db.fetch('x')\n"
               "    await create_list_record(db, user_id='u', stamps_at=stamps_at)\n")
    assert _scan(planted) == ["late", "silent", "fresh", "again"]
    gates = [n for n in ast.walk(ast.parse((ROOT / "list_service.py").read_text("utf-8"))) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_gate" and any(k.arg in ("owner", "visibility") for k in n.keywords)]
    assert len(gates) == 1 and gates[0].args[1].value == "list.create"
    others = [p.name for p in modules if p.name not in ("list_service.py", "os_shadow.py") and
              ("ask_kernel(" in p.read_text("utf-8") or "ask_list(" in p.read_text("utf-8"))]
    assert others == []                                  # nobody outside list_service puts an ask to the kernel itself
