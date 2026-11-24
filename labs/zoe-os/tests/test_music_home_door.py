"""The music and home-control areas of the door (``zoe_os/music_door.py``, ``zoe_os/home_door.py``): the kernel decides and
records a member's ACTION by ids and closed codes. Nothing is stored for either; no track, device, room or scene name reaches it.

One test per row of each table; a child's level (named, listed, defaulted, mis-stated); the levels file; the record; the rules
every area shares; and the same verbs over the daemon's wire. Each test names the control that was run against it (the code
broken, the test seen to fail, the code restored). Invented people and ids only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, home_door, music_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Decision, Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
ALLOW = Decision(True)
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]


def refused(reason):
    return (False, reason, None)


def music(verb, actor, **kw):
    return music_door.decide(PEOPLE, verb, actor, **kw)


def home(verb, actor, **kw):
    return home_door.decide(PEOPLE, verb, actor, **kw)


def test_neither_area_keeps_any_state_of_its_own():
    """The roster and the levels are each KERNEL's (``Door.roster``, ``Kernel.home``): neither area's module binds a name a
    ``build`` could assign and a second kernel would then read. Every lower-case name of either module is a function, a class
    of ``zoe_os`` or a module; ``roster``, ``on_roster``, ``levels`` and a stateful ``home_door.AREA`` are gone.
    Control run: ``levels = dict`` put back as a module attribute of ``home_door`` fails this."""
    import types

    for module in (music_door, home_door):
        for name, value in vars(module).items():
            if name.startswith("__") or not name[:1].islower() or name == "annotations":
                continue
            ours = isinstance(value, type) and value.__module__.startswith("zoe_os")
            assert isinstance(value, (types.FunctionType, types.ModuleType)) or ours, (module.__name__, name)
    assert not {"roster", "on_roster"} & set(vars(music_door)) and not {"levels", "AREA"} & set(vars(home_door))


# ---- music -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", music_door.VERBS)
@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (VISITOR, None), ("kiosk", "panel")])
def test_anyone_in_the_house_and_the_panel_may_control_the_music(verb, who, via):
    """Control run: with the table requiring an adult, a child's ``music.play`` is refused."""
    assert music(verb, who, via=via) == ALLOW


@pytest.mark.parametrize("verb", music_door.VERBS)
def test_someone_not_on_the_roster_may_not_control_the_music(verb):
    """Control run: with the roster check dropped a stranger is allowed."""
    assert music(verb, STRANGER) == refused(door.UNKNOWN_PERSON)
    assert set(music_door.QUIET_VERBS) == {"music.stop", "music.quieter"} <= set(music_door.VERBS)


# ---- home control: the table ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", home_door.VERBS)
@pytest.mark.parametrize("who", [OWNER, ADULT2])
def test_an_adult_may_control_everything_and_set_a_childs_level(verb, who):
    """Control run: with the adult rule dropped an adult's ``lock.open`` falls to the guest rule and is refused."""
    assert home(verb, who, owner=KID if verb == home_door.SET_LEVEL else None) == ALLOW


def test_a_child_with_nothing_stated_has_lights_and_media_only():
    """The default. Control run: with ``DEFAULT_LEVEL`` made ``most`` a child with nothing stated may set the heating."""
    assert home_door.DEFAULT_LEVEL == "lights"
    for act in home_door.ACTS:
        assert home(f"light.{act}", KID) == ALLOW and home(f"media.{act}", KID) == ALLOW
        for cls in ("switch", "climate", "cover", "lock", "scene", "other"):
            assert home(f"{cls}.{act}", KID) == refused(door.ADULTS_ONLY), (cls, act)


@pytest.mark.parametrize("level, may", [("none", set()), ("lights", {"light", "media"}), ("comfort", {"light", "media", "switch", "scene"}),
                                        ("most", {"light", "media", "switch", "scene", "climate"})])
def test_a_named_level_is_exactly_its_set_of_classes(level, may):
    """No named level holds a lock or a cover. Control run: with ``cover`` added to ``most`` this fails."""
    lv = lambda: {KID: level}  # noqa: E731
    for cls in home_door.CLASSES:
        for act in home_door.ACTS:
            assert home(f"{cls}.{act}", KID, levels=lv).allowed is (cls in may), (level, cls, act)
    assert home("light.on", KID2, levels=lv) == ALLOW and home("switch.on", KID2, levels=lv) == refused(door.ADULTS_ONLY)  # the default
    assert not ({"lock", "cover"} & set().union(*home_door.LEVELS.values()))


def test_a_listed_level_is_the_only_way_a_child_gets_a_cover_or_a_lock_and_a_bad_statement_is_the_default():
    """Control run: with ``allowed_classes`` answering every class for an unknown level name, the mis-stated child may unlock."""
    lv = lambda: {KID: ["light", "cover"], KID2: "everything"}  # noqa: E731
    assert home("cover.open", KID, levels=lv) == ALLOW and home("light.off", KID, levels=lv) == ALLOW
    assert home("media.on", KID, levels=lv) == refused(door.ADULTS_ONLY) and home("lock.open", KID, levels=lv) == refused(door.ADULTS_ONLY)
    assert home("light.on", KID2, levels=lv) == ALLOW and home("lock.open", KID2, levels=lv) == refused(door.ADULTS_ONLY)  # unknown: default
    for bad in (["light", "rocket"], 7, {"light": True}, None):
        assert home_door.allowed_classes(bad) == home_door.LEVELS["lights"]


@pytest.mark.parametrize("who, via", [("kiosk", "panel"), (VISITOR, None)])
def test_the_panel_and_a_guest_may_do_anything_but_open_the_house(who, via):
    """Today the panel drives the house; opening it needs a person the kernel knows as an adult. Control run: with ``opens``
    answering False the panel's ``lock.open`` is allowed."""
    for cls in home_door.CLASSES:
        for act in home_door.ACTS:
            want = refused(door.ADULTS_ONLY) if cls in ("lock", "cover") and act != "close" else ALLOW
            assert home(f"{cls}.{act}", who, via=via) == want, (cls, act)
    assert home_door.opens("cover.set") and home_door.opens("lock.on") and not home_door.opens("lock.close") and not home_door.opens("light.on")


@pytest.mark.parametrize("who, via", [(KID, None), (VISITOR, None), ("kiosk", "panel")])
def test_only_an_adult_sets_a_childs_level(who, via):
    """Control run: with the ``SET_LEVEL`` rule dropped a child with a level that... sets their own (the verb has no class)."""
    assert home(home_door.SET_LEVEL, who, via=via, owner=KID) == refused(door.ADULTS_ONLY)


@pytest.mark.parametrize("verb", ["light.on", "lock.open", home_door.SET_LEVEL])
def test_someone_not_on_the_roster_controls_nothing(verb):
    """Control run: with the roster check dropped a stranger falls to the guest rule and may turn a light on."""
    assert home(verb, STRANGER) == refused(door.UNKNOWN_PERSON)
    assert len(home_door.VERBS) == len(home_door.CLASSES) * len(home_door.ACTS) + 1


# ---- the levels file ---------------------------------------------------------------------------------------------------------------
def test_the_levels_file_is_followed_without_a_restart_and_states_nothing_unless_it_is_private_and_well_formed(tmp_path):
    """Control run: with the mode check dropped, a file others can write gives the child ``most``."""
    path = tmp_path / home_door.LEVELS_FILE
    read = home_door.file_levels(path)
    assert read() == {}                                         # no file: nothing stated
    path.write_text(json.dumps({"levels": {KID: "comfort", KID2: ["light"]}}))
    os.chmod(path, 0o600)
    assert read() == {KID: "comfort", KID2: ["light"]}
    path.write_text(json.dumps({"levels": {KID: "most"}}))      # rewritten: read again
    os.chmod(path, 0o600)
    assert read() == {KID: "most"}
    os.chmod(path, 0o644)
    path.write_text(json.dumps({"levels": {KID: "most", "x": 1}}))
    assert read() == {}                                         # others can read it: it states nothing
    os.chmod(path, 0o600)
    path.write_text("not json at all")
    assert read() == {}


def test_a_permission_change_alone_drops_the_levels_at_once(tmp_path):
    """``chmod`` changes neither the inode, the size nor the modification time: a valid private file that raised a child to
    ``most`` and is then opened to others (no rewrite) must state nothing at the very next read, and state it again once it is
    private again. Control run: with the mode left out of the cache's identity in ``file_levels`` the child keeps ``most``."""
    path = tmp_path / home_door.LEVELS_FILE
    path.write_text(json.dumps({"levels": {KID: "most", KID2: ["lock"]}}))
    os.chmod(path, 0o600)
    read = home_door.file_levels(path)
    assert read() == {KID: "most", KID2: ["lock"]}
    before = os.stat(path)
    os.chmod(path, 0o644)
    after = os.stat(path)
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
    assert read() == {}                                                     # opened to others: nothing stated, at once
    assert home("climate.set", KID, levels=read) == refused(door.ADULTS_ONLY) and home("lock.open", KID2, levels=read) == refused(door.ADULTS_ONLY)
    os.chmod(path, 0o600)
    assert read() == {KID: "most", KID2: ["lock"]}


def _off_the_roster(tmp_path, roster=frozenset(PEOPLE) - {ADULT2, VISITOR}):
    """A kernel that still knows ADULT2 and VISITOR (a record names them, so they can be forgotten) but whose CURRENT roster
    (``Door.roster``, the kernel's own) no longer holds them. ``roster=None``: the control, a kernel with no roster installed."""
    k = kernel(tmp_path)
    k.door.roster = None if roster is None else (lambda: roster)
    return k


def _answer(k, area, verb, person, **kw) -> str:
    try:
        k.door.ask("door", area, verb, person, visibility="household", **kw)
        return "ok"
    except Refused as exc:
        return exc.wire


@pytest.mark.parametrize("verb", ["music.play", "music.stop", "music.library"])
def test_a_member_taken_off_the_roster_may_not_control_the_music(tmp_path, verb):
    """The kernel keeps a member taken off the roster among its people (so their record can still be forgotten); ITS roster
    says who is on it now, and the door hands the music table only them (``Door._acting``). Named control, in the test: the same
    kernel with no roster installed allows the removed adult (so the refusal is the roster's, not the table's). Control run: with
    ``Door._acting`` returning ``people`` whatever the area says, the removed adult's ``music.play`` is allowed."""
    k = _off_the_roster(tmp_path)
    try:
        assert ADULT2 in k.grants.people and VISITOR in k.grants.people              # still people the kernel knows ...
        assert _answer(k, "music", verb, ADULT2, target="ply0001") == door.UNKNOWN_PERSON     # ... but off the roster: nothing
        assert _answer(k, "music", verb, VISITOR, target="ply0001") == door.UNKNOWN_PERSON
        assert _answer(k, "music", verb, OWNER, target="ply0001") == "ok"
        assert _answer(k, "music", verb, "kiosk", via="panel", target="ply0001") == "ok"
    finally:
        k.close()
    (tmp_path / "control").mkdir()
    k = _off_the_roster(tmp_path / "control", roster=None)
    try:
        assert _answer(k, "music", verb, ADULT2, target="ply0001") == "ok"           # the control: no roster, the people map
    finally:
        k.close()


@pytest.mark.parametrize("verb", ["light.on", "media.on", "scene.on", "lock.close", "level.set"])
def test_a_member_taken_off_the_roster_controls_nothing_in_the_house_and_is_not_a_guest(tmp_path, verb):
    """A removed member is kept as a least-access person, which is the GUEST role: read as a guest they could still turn a light
    on. They are not on the roster, so they control nothing. Named control, in the test: with no roster installed the removed
    guest's command falls to the guest rule (allowed, but ``level.set``). Control run: with ``Door._acting`` returning
    ``people`` whatever the area says, the removed adult's ``light.on`` is allowed."""
    owner = {"owner": KID} if verb == home_door.SET_LEVEL else {}
    k = _off_the_roster(tmp_path)
    try:
        assert _answer(k, "home", verb, ADULT2, target="light.hall_lamp", **owner) == door.UNKNOWN_PERSON
        assert _answer(k, "home", verb, VISITOR, target="light.hall_lamp", **owner) == door.UNKNOWN_PERSON
        assert _answer(k, "home", verb, OWNER, target="light.hall_lamp", **owner) == "ok"
    finally:
        k.close()
    (tmp_path / "control").mkdir()
    k = _off_the_roster(tmp_path / "control", roster=None)
    try:
        want = door.ADULTS_ONLY if verb == home_door.SET_LEVEL else "ok"
        assert _answer(k, "home", verb, VISITOR, target="light.hall_lamp", **owner) == want    # the control: a guest again
    finally:
        k.close()


# ---- the record ----------------------------------------------------------------------------------------------------------------
def kernel(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": [*music_door.VERBS, *home_door.VERBS]})
    return k


def naming(tmp_path, member) -> dict:
    con = sqlite3.connect(str(tmp_path / "k.db"))
    try:
        found = {}
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
            n = sum(1 for row in con.execute(f"SELECT * FROM {table}") if any(
                member in (c.decode("utf-8", "replace") if isinstance(c, bytes) else str(c)) for c in row))
            if n:
                found[table] = n
        return found
    finally:
        con.close()


def test_an_ask_records_ids_and_closed_codes_only_and_a_verb_belongs_to_its_own_area(tmp_path):
    """Control run: with the home area given ``target="player"`` the entity assertion fails."""
    k = kernel(tmp_path)
    m = k.door.ask("door", "music", "music.play", KID, target="ply0001", visibility="household")
    h = k.door.ask("door", "home", "light.on", KID, target="light.kitchen_lamp", visibility="household")
    assert k.spine._get(m["seq"]).payload["player"] == "ply0001" and k.spine._get(m["seq"]).payload["action"] == "music.play"
    kept = k.spine._get(h["seq"]).payload
    assert kept == {"action": "light.on", "skill": "door", "grant_id": None, "proof": None, "verified": False, "outcome": "allowed",
                    "reversible": False, "vis": "household", "entity": "light.kitchen_lamp"}
    assert k.door.report("door", h["ask"], "done")["outcome"] == "done"
    for area, verb in (("music", "light.on"), ("home", "music.play"), ("people", "light.on")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1")
    with pytest.raises(Refused) as exc:
        k.door.ask("door", "home", "lock.open", KID, target="lock.front_door", visibility="household")
    assert exc.value.wire == door.ADULTS_ONLY
    with pytest.raises(Refused) as exc:
        k.door.ask("door", "home", "light.on", STRANGER, target="light.kitchen_lamp", visibility="household")
    assert exc.value.wire == door.UNKNOWN_PERSON
    k.close()
    assert naming(tmp_path, STRANGER) == {}


@pytest.mark.parametrize("area, verb", [("music", "music.stop"), ("home", "light.off")])
def test_an_ask_made_before_a_forget_that_arrives_after_it_writes_nothing_that_names_anyone(tmp_path, area, verb):
    """Control run: with ``stale`` forced False in ``Door.ask`` both cases are recorded and this fails."""
    k = kernel(tmp_path)
    k.spine.forget(KID, actor=KID, stamp=AT)
    for stamp in (OLD, AT):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", area, verb, KID, target="x00000000001", visibility="household", stamps={KID: stamp})
        assert exc.value.wire == door.ERASED
    k.close()
    assert naming(tmp_path, KID) == {}
    k = kernel(tmp_path)
    assert k.door.ask("door", area, verb, KID, target="x00000000001", visibility="household", stamps={KID: NEW})["ask"]
    k.close()


# ---- the same verbs on the daemon's wire, with a level stated beside the roster ----------------------------------------------------
def test_the_daemon_serves_music_and_home_control_and_reads_the_levels_beside_its_roster(tmp_path, monkeypatch):
    """Control run: with the music and home verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``;
    with the levels reader not installed by ``build`` (``k.home.levels``) the child's switch is refused."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}, VISITOR]}))
    levels = state / home_door.LEVELS_FILE
    levels.write_text(json.dumps({"levels": {KID: "comfort"}}))
    os.chmod(levels, 0o600)
    d = Shadow(state)
    try:
        def call(area, verb, person, **kw):
            return d.brain.call("door_ask", area=area, verb=verb, person=person, visibility="household", **kw)

        out = call("music", "music.play", "kiosk-1", via="panel", target="ply0001")
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("home", "switch.on", KID, target="switch.kettle_plug")["ask"]             # the level stated beside the roster
        assert call("home", "lock.open", OWNER, target="lock.front_door")["ask"]
        assert call("home", "level.set", OWNER, owner=KID)["ask"]
        for area, verb, person, kw, reason in (
                ("home", "climate.set", KID, {"target": "climate.hall"}, door.ADULTS_ONLY),
                ("home", "lock.open", "kiosk-1", {"via": "panel", "target": "lock.front_door"}, door.ADULTS_ONLY),
                ("home", "level.set", KID, {"owner": KID}, door.ADULTS_ONLY),
                ("music", "music.play", STRANGER, {"target": "ply0001"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(area, verb, person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(wire.WireError):
            call("home", "light.on", OWNER, target="light.kitchen_lamp", name="Kitchen lamp")      # no field for a device's name
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()


def test_the_daemon_refuses_a_member_it_still_knows_once_they_are_off_the_roster(tmp_path):
    """Over the wire, the daemon's own roster: an adult and a guest act (so the kernel holds a record for both), are taken off
    people.json, and stay people the kernel knows (``known-people.json``: still forgettable). From the next ask on they control
    no music and nothing in the house; the members still on the roster are unaffected.
    Control run: with ``build`` not installing ``k.door.roster`` (the kernel's own; music and home have no roster of their own
    now), the removed adult's ``music.play`` and the removed guest's ``light.on`` are both allowed."""
    from test_shadow_daemon import Shadow, settled

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    roster = [{"id": OWNER, "role": "adult"}, {"id": ADULT2, "role": "adult"}, {"id": VISITOR, "role": "guest"}]
    (state / "people.json").write_text(json.dumps({"people": roster}))
    d = Shadow(state)
    try:
        def call(area, verb, person, **kw):
            return d.brain.call("door_ask", area=area, verb=verb, person=person, visibility="household", **kw)

        for who in (ADULT2, VISITOR):
            assert d.brain.call("door_report", ask=call("music", "music.play", who, target="ply0001")["ask"], outcome="done")
            assert call("home", "light.on", who, target="light.hall_lamp")["ask"]
        (state / "people.json").write_text(json.dumps({"people": roster[:1]}))
        assert call("music", "music.play", OWNER, target="ply0001")["ask"]                   # the re-read; the owner unaffected
        assert {ADULT2, VISITOR} <= set(json.loads((state / "known-people.json").read_text())["people"])   # still known
        for area, verb, who in (("music", "music.play", ADULT2), ("music", "music.library", VISITOR),
                                ("home", "light.on", ADULT2), ("home", "light.on", VISITOR), ("home", "scene.on", VISITOR)):
            with pytest.raises(Refused) as exc:
                call(area, verb, who, target="ply0001" if area == "music" else "light.hall_lamp")
            assert str(exc.value) == door.UNKNOWN_PERSON, (area, verb, who)
    finally:
        d.stop()


# ---- two households in one process: each kernel decides from its OWN levels and roster ------------------------------------------
def _household(people: list, levels: dict) -> Path:
    from test_shadow_daemon import settled

    state = Path(tempfile.mkdtemp(prefix="zh"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": people}))
    path = state / home_door.LEVELS_FILE
    path.write_text(json.dumps({"levels": levels}))
    os.chmod(path, 0o600)
    return state


def _over_the_wire(srv, area: str, verb: str, person: str, target: str) -> str:
    """The daemon's own op (the roster is re-read first, as on the wire): ``ok`` or the refusal code."""
    import types

    try:
        with srv.kernel.spine._lock:
            srv._op_door_ask(types.SimpleNamespace(name="brain"), {"area": area, "verb": verb, "person": person, "target": target,
                                                                   "visibility": "household"})
        return "ok"
    except Refused as exc:
        return exc.wire


def test_two_daemons_built_in_one_process_each_decide_from_their_own_levels_and_roster():
    """Two households' daemons built in ONE process, the same child (``member-kip``) and the same second adult in both. House A
    states level ``none`` for the child and then takes the adult off its roster (kept: a record names them); house B states a
    list holding ``lock`` for the child and keeps the adult. B is built AFTER A. Each decides from its own: in A the child may
    not turn a light on or unlock and the removed adult plays no music and switches nothing; in B the child may unlock and the
    adult may play music. Named control, in the test: A's child, ruled by A's file, is refused exactly what B's file allows.
    Control run (the old code): the levels reader installed by ``build`` as a module global (``home_door.levels``, read by
    ``decide``) -> A's child is allowed ``lock.open`` and ``light.on`` by B's file, and this fails at the first A line after B
    is built."""
    import shadow_daemon

    roster = [{"id": OWNER, "role": "adult"}, {"id": ADULT2, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]}]
    a_state = _household(roster, {KID: "none"})
    k1, a, _ = shadow_daemon.build(a_state)
    k2 = None
    try:
        assert _over_the_wire(a, "music", "music.play", ADULT2, "ply0001") == "ok"           # A's record names the adult
        (a_state / "people.json.new").write_text(json.dumps({"people": [roster[0], roster[2]]}))  # then A takes them off
        os.replace(a_state / "people.json.new", a_state / "people.json")
        assert _over_the_wire(a, "home", "light.on", OWNER, "light.hall_lamp") == "ok"        # (the re-read)
        k2, b, _ = shadow_daemon.build(_household(roster, {KID: ["light", "lock"]}))
        for area, verb, person, target, want_a, want_b in (
                ("home", "lock.open", KID, "lock.front_door", door.ADULTS_ONLY, "ok"),
                ("home", "light.on", KID, "light.hall_lamp", door.ADULTS_ONLY, "ok"),
                ("music", "music.play", ADULT2, "ply0001", door.UNKNOWN_PERSON, "ok"),
                ("home", "light.on", ADULT2, "light.hall_lamp", door.UNKNOWN_PERSON, "ok")):
            assert _over_the_wire(a, area, verb, person, target) == want_a, ("A", area, verb, person)
            assert _over_the_wire(b, area, verb, person, target) == want_b, ("B", area, verb, person)
        assert k1.home is not k2.home and k1.home.levels() == {KID: "none"} and k2.home.levels() == {KID: ["light", "lock"]}
        assert k1.door.areas["home"] is k1.home.area and k2.door.areas["home"] is k2.home.area
    finally:
        k1.close()
        if k2 is not None:
            k2.close()
