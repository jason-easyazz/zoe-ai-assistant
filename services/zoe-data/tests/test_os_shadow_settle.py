"""The Zoe OS shadow's state directory SETTLES before it holds anything (owner decision: "redesign the setup step"), so a forget
with no state directory is answered from memory alone. Behaviour throughout: real directories under ``tmp_path``, an injected clock
and sleep (no test waits real seconds), both copies of the shared text (zoe-data's and the lab kernel's) run through the same cases."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import shutil
import threading
from pathlib import Path

import pytest

import os_shadow
from test_os_shadow import MEMBER, kernel  # noqa: F401  (kernel is a fixture: the fake kernel and an installed state directory)

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[3]
LAB_FILE = ROOT / "labs" / "zoe-os" / "zoe_os" / "shadow_state.py"
_spec = importlib.util.spec_from_file_location("lab_shadow_state", LAB_FILE)
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)
SIDES = {"zoe-data": os_shadow, "lab-kernel": lab}


def _shared(path: Path) -> str:
    text = path.read_text("utf-8")
    return text[text.index("# >>> settle"):text.index("# <<< settle")]


def test_the_two_copies_of_the_settle_rule_are_the_same_text_and_the_windows_nest():
    mine, theirs = _shared(ROOT / "services" / "zoe-data" / "os_shadow.py"), _shared(LAB_FILE)
    assert mine == theirs and "def settle(" in mine and "def marker_left(" in mine
    assert _shared(LAB_FILE).replace("SETTLE_S = 4", "SETTLE_S = 3") != mine          # the comparison is not vacuous
    for side in SIDES.values():
        assert side.SETTLE_S > side.TRUST_S > side.POLL_S > 0
    assert tuple(os_shadow.STATE_NAMES) == tuple(os_shadow.DATA_NAMES) == tuple(lab.DATA_NAMES)
    assert os_shadow.MARKER not in os_shadow.STATE_NAMES                               # the marker is never "data"


class Clock:
    """An injected clock: wall, monotonic and boot id; ``sleep`` advances it and runs ``during`` (what else is true meanwhile)."""

    def __init__(self, boot="boot-a"):
        self.wall, self.mono, self.boot, self.slept, self.during = 1_800_000_000.0, 5_000.0, boot, 0.0, None

    def now(self):
        return self.wall, self.mono, self.boot

    def sleep(self, s):
        if self.during:
            self.during()
        self.wall, self.mono, self.slept = self.wall + s, self.mono + s, self.slept + s


@pytest.mark.parametrize("side", sorted(SIDES))
def test_a_new_directory_is_made_empty_marked_and_holds_no_data_until_the_settle_time_has_passed(tmp_path, side):
    """ORDER: the directory and its marker exist first; at every moment of the wait there is no data file; the wait is longer
    than the time any process trusts a cached "no directory"; only then does the creator write data."""
    mod, d, c, seen = SIDES[side], tmp_path / "state", Clock(), []
    c.during = lambda: seen.append((d.is_dir(), (d / mod.MARKER).is_file(), mod.has_data(str(d)), sorted(p.name for p in d.iterdir())))
    waited = mod.settle(str(d), c.now, c.sleep)
    assert seen and all(s[:3] == (True, True, False) for s in seen) and all(s[3] == [mod.MARKER] for s in seen)
    assert waited == c.slept >= mod.SETTLE_S > mod.TRUST_S
    assert oct(d.stat().st_mode & 0o777) == "0o700" and oct((d / mod.MARKER).stat().st_mode & 0o777) == "0o600"
    marker = json.loads((d / mod.MARKER).read_text())
    assert set(marker) == {"v", "wall", "mono", "boot", "settle_s", "dev", "ino"}      # a time and this directory: no personal data
    (d / "people.json").write_text("{}")                                               # data, now that it has settled
    assert mod.settle(str(d), c.now, c.sleep) == 0.0                                   # a second call (another creator) waits nothing
    young = tmp_path / "young"
    c2 = Clock()
    c2.during = lambda: (young / "kernel").mkdir(exist_ok=True)                        # control: data INSIDE the window is caught
    with pytest.raises(mod.Unsettled, match="inside the settle window"):
        mod.settle(str(young), c2.now, c2.sleep)


def _raw_copy(tmp_path, how):
    """A state directory that was NOT made by the tool: copied by hand (``how`` says what marker came with it)."""
    made, c = tmp_path / "made", Clock()
    os_shadow.settle(str(made), c.now, c.sleep)
    (made / "kernel").mkdir()
    (made / "kernel" / "zoe-os.db").write_bytes(b"recorded")
    raw = tmp_path / "raw"
    if how == "no-marker":
        shutil.copytree(made, raw, ignore=shutil.ignore_patterns(os_shadow.MARKER))
    elif how == "copied-marker":
        shutil.copytree(made, raw)                                    # the old, settled marker rides along: another directory's
    elif how == "garbage-marker":
        shutil.copytree(made, raw)
        (raw / os_shadow.MARKER).write_text("{not json")
    elif how == "future-marker":
        shutil.copytree(made, raw)
        st = raw.stat()
        (raw / os_shadow.MARKER).write_text(json.dumps({"v": 1, "wall": c.wall + 9e6, "mono": c.mono + 9e6, "boot": c.boot,
                                                        "settle_s": 4.0, "dev": st.st_dev, "ino": st.st_ino}))
    elif how == "young-marker":
        raw.mkdir()
        os_shadow.write_marker(str(raw), *c.now(), os_shadow.SETTLE_S)                # made a moment ago ...
        shutil.copytree(made / "kernel", raw / "kernel")                              # ... and data copied straight in
    return raw, c


RAW = ["no-marker", "copied-marker", "garbage-marker", "future-marker", "young-marker"]


@pytest.mark.parametrize("how", RAW)
def test_the_kernel_side_refuses_to_serve_a_directory_copied_in_by_hand_and_says_why(tmp_path, how):
    raw, c = _raw_copy(tmp_path, how)
    with pytest.raises(SystemExit) as stop:
        lab.require_settled(raw, c.now, c.sleep)
    assert "refusing to start" in str(stop.value) and "os_shadow.py --restore" in str(stop.value)
    assert (raw / "kernel" / "zoe-os.db").read_bytes() == b"recorded"                  # it refuses; it does not delete
    made = tmp_path / "made"                                                           # control: the tool-made one is served
    assert lab.require_settled(made, c.now, c.sleep) == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("how", RAW)
async def test_the_forget_side_treats_a_hand_copied_directory_as_state_present_and_fails_closed(tmp_path, monkeypatch, how):
    """Data with no marker, a foreign, unreadable, future or too-young one: a forget that looks at the disk finds state PRESENT,
    goes to the kernel (nothing answers: the kernel refuses such a directory), is parked and RAISES - never "nothing to forget"."""
    raw, _c = _raw_copy(tmp_path, how)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(raw))
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    os_shadow._reset_for_tests()
    try:
        for forget in (lambda: os_shadow.forget_user(MEMBER), lambda: os_shadow.erase_entity(MEMBER, "Dana"),
                       lambda: os_shadow.erase_topic(MEMBER, "the garden"), lambda: os_shadow.forget_for_erase(MEMBER)):
            with pytest.raises(os_shadow.ShadowForgetUnavailable):
                await forget()
        assert json.loads((raw / "pending-forgets.json").read_text()) == [MEMBER]
    finally:
        os_shadow._reset_for_tests()


@pytest.mark.parametrize("case,left", [
    ("same-boot-young", 3.0), ("same-boot-settled", 0.0), ("same-boot-clock-behind", None), ("rebooted", 0.0),
    ("no-boot-id-young", 3.0), ("no-boot-id-future", None), ("no-boot-id-settled", 0.0), ("missing", None), ("longer-recorded", 9.0),
])
@pytest.mark.parametrize("side", sorted(SIDES))
def test_the_marker_age_is_monotonic_on_the_same_boot_and_fails_closed_on_a_future_or_missing_time(side, case, left):
    """Clock steps: on the same boot only the monotonic clock is read (a wall-clock jump of a year changes nothing); a marker
    'from the future' is not trusted; after a reboot every process has started since, so the marker is settled."""
    mod = SIDES[side]
    m = {"v": 1, "wall": 1000.0, "mono": 50.0, "boot": "a", "settle_s": 4.0}
    got = {
        "same-boot-young": lambda: mod.marker_left(m, 1000.0 + 3e7, 51.0, "a", 4.0),          # the wall clock jumped a year: ignored
        "same-boot-settled": lambda: mod.marker_left(m, 900.0, 54.0, "a", 4.0),               # ... or went backwards: ignored
        "same-boot-clock-behind": lambda: mod.marker_left(m, 2000.0, 49.0, "a", 4.0),
        "rebooted": lambda: mod.marker_left(m, 10.0, 1.0, "b", 4.0),
        "no-boot-id-young": lambda: mod.marker_left(m, 1001.0, 0.0, "", 4.0),
        "no-boot-id-future": lambda: mod.marker_left(m, 999.0, 9e9, "", 4.0),
        "no-boot-id-settled": lambda: mod.marker_left(m, 1004.0, 0.0, "", 4.0),
        "missing": lambda: mod.marker_left(None, 1e9, 1e9, "a", 4.0),
        "longer-recorded": lambda: mod.marker_left({**m, "settle_s": 10.0}, 0.0, 51.0, "a", 4.0),
    }[case]()
    assert got == left


@pytest.mark.parametrize("bad", [{"wall": float("nan")}, {"mono": float("inf")}, {"settle_s": -1}, {"settle_s": True}, {"v": 2},
                                 {"boot": 7}, {"ino": 1}, {"dev": "x"}])
@pytest.mark.parametrize("side", sorted(SIDES))
def test_a_marker_that_is_malformed_or_another_directorys_is_not_read_as_a_marker(tmp_path, side, bad):
    mod, d, c = SIDES[side], tmp_path / "state", Clock()
    mod.settle(str(d), c.now, c.sleep)
    good = json.loads((d / mod.MARKER).read_text())
    assert mod.read_marker(str(d)) == good
    (d / mod.MARKER).write_text(json.dumps({**good, **bad}))          # json.dumps writes NaN / Infinity, json.load reads them back
    assert mod.read_marker(str(d)) is None


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """zoe-data pointed at a state directory that does not exist, with the injected clock and sleep."""
    d, c = tmp_path / "os-shadow-new", Clock()
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setattr(os_shadow, "clock", c.now)
    monkeypatch.setattr(os_shadow, "_sleep", c.sleep)
    os_shadow._reset_for_tests()
    c.dir, c.seen = d, []
    c.during = lambda: c.seen.append(os_shadow.has_data(str(d)))
    yield c
    os_shadow._reset_for_tests()


CREATORS = {
    "park-forget": (lambda src: os_shadow._pending_add(MEMBER), "pending-forgets.json"),
    "park-erase": (lambda src: os_shadow.park_erase(MEMBER, rows=["r1"]), "pending-erases.json"),
    "roster": (lambda src: os_shadow.write_roster([MEMBER]), "people.json"),
    "restore": (lambda src: os_shadow.restore(src), "pending-forgets.json"),
    "cli-init": (lambda src: os_shadow.main(["--init"]), None),
}


@pytest.mark.parametrize("creator", sorted(CREATORS))
def test_every_zoe_data_writer_settles_the_directory_before_its_first_data_file(fresh, tmp_path, creator):
    """EVERY way zoe-data makes the state directory (a parked forget, a parked erase, the roster, the restore, --init): no data
    file exists at any moment of the wait, the wait is the settle time, the data file is there afterwards, and this process's own
    cached answer flipped before the directory was made."""
    c = fresh
    src = tmp_path / "backup"
    src.mkdir()
    (src / "pending-forgets.json").write_text(json.dumps([MEMBER]))
    call, data = CREATORS[creator]
    os_shadow._state_probe()
    assert os_shadow._no_state()
    c.during = lambda: c.seen.append((os_shadow.has_data(str(c.dir)), os_shadow._no_state()))
    call(src)
    assert c.seen and all(s == (False, False) for s in c.seen) and c.slept >= os_shadow.SETTLE_S > os_shadow.TRUST_S
    assert (c.dir / os_shadow.MARKER).is_file() and (data is None or (c.dir / data).is_file())
    slept = c.slept
    call(src)                                                          # the directory is settled (or holds data): no second wait
    assert c.slept == slept


def test_the_restore_brings_back_the_parks_and_the_roster_and_never_the_kernels_record(fresh, tmp_path):
    """The restore tool: parked forgets and erases are MERGED in (ids), the roster is written when there is none, and the kernel's
    record, its credentials and the backup's marker are not copied - a backup of the record can hold what was forgotten since."""
    c, src = fresh, tmp_path / "backup"
    (src / "kernel").mkdir(parents=True)
    (src / "kernel" / "zoe-os.db").write_bytes(b"old record")
    (src / "creds.json").write_text("{}")
    (src / os_shadow.MARKER).write_text("{}")
    (src / "people.json").write_text(json.dumps({"people": [{"id": MEMBER, "role": "adult"}, "member-b"]}))
    (src / "pending-forgets.json").write_text(json.dumps([MEMBER]))
    (src / "pending-erases.json").write_text(json.dumps({MEMBER: {"rows": ["r1"], "turns": ["t1"]}}))
    assert os_shadow.main(["--restore", str(src)]) == 0
    assert sorted(p.name for p in c.dir.iterdir() if not p.name.endswith(".lock")) == sorted(
        ["pending-erases.json", "pending-forgets.json", "people.json", os_shadow.MARKER])
    assert json.loads((c.dir / "pending-forgets.json").read_text()) == [MEMBER]
    assert json.loads((c.dir / "pending-erases.json").read_text()) == {MEMBER: {"rows": ["r1"], "turns": ["t1"]}}
    assert json.loads((c.dir / "people.json").read_text()) == {"people": [MEMBER, "member-b"]}
    assert os_shadow.read_marker(str(c.dir)) is not None and c.slept >= os_shadow.SETTLE_S


@pytest.mark.asyncio
async def test_the_settle_wait_never_runs_on_an_event_loop_and_a_forgets_park_takes_it_in_a_thread(fresh, monkeypatch):
    c = fresh
    with pytest.raises(RuntimeError, match="never on the event loop"):
        os_shadow.ensure_state_dir()                                   # here, on the loop, with a directory to make: refused
    assert not c.dir.exists()
    threads = []
    c.during = lambda: threads.append(threading.get_ident())
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")                 # the flag on and nothing installed: the forget is parked
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    with pytest.raises(os_shadow.ShadowForgetUnavailable, match="parked"):
        await os_shadow.forget_user(MEMBER)
    assert threads and threading.get_ident() not in threads and c.slept >= os_shadow.SETTLE_S
    assert json.loads((c.dir / "pending-forgets.json").read_text()) == [MEMBER]


@pytest.mark.asyncio
async def test_a_second_process_installing_during_forgets_is_either_seen_or_has_provably_written_nothing_yet(tmp_path, monkeypatch):
    """ONE clock for both: this process (its cached "no directory" answer, its poller dead - the worst case) and a second process
    that makes the directory through the lab kernel's own ``settle``. At every step of the second process's wait a forget here
    either answers from memory - and at that instant there is NO data in the directory - or it looks at the disk. When the wait
    ends (so data may appear) the cached answer is no longer trusted, and the forget after the data arrives is NOT confirmed."""
    d, c = tmp_path / "state", Clock()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(os_shadow, "_mono", lambda: c.mono)
    os_shadow._reset_for_tests()
    os_shadow._state_known = (str(d), False, c.mono)                   # looked just now: no directory. No poller will run.
    loop, steps = asyncio.get_running_loop(), []

    async def a_forget_here():
        from_memory = os_shadow._no_state()
        data = os_shadow.has_data(str(d))
        assert await os_shadow.forget_for_erase(MEMBER) == 0           # no data yet: nothing to forget, either way
        steps.append((from_memory, data))

    c.during = lambda: asyncio.run_coroutine_threadsafe(a_forget_here(), loop).result(5)
    try:
        waited = await asyncio.to_thread(lab.settle, str(d), c.now, c.sleep)       # the second process: mkdir, marker, wait
        assert waited >= lab.SETTLE_S and steps
        assert steps[0][0] is True and all(not data for _mem, data in steps)       # answered from memory ONLY while there was no data
        assert steps[-1][0] is False                                               # ... and memory stopped being trusted before the end
        assert not os_shadow._no_state()                                           # the wait is over: this process cannot skip any more
        (d / "kernel").mkdir()                                                     # the second process writes its data
        with pytest.raises(os_shadow.ShadowForgetUnavailable):
            await os_shadow.forget_for_erase(MEMBER)                               # seen: state present, not confirmed, parked
    finally:
        os_shadow._reset_for_tests()


def test_the_off_box_backup_never_carries_the_state_directory():
    """Nothing copies the directory raw: the off-box household tier walks ~/.zoe and leaves ``os-shadow`` out (syntax tree read)."""
    tree = ast.parse((ROOT / "scripts" / "maintenance" / "zoe_offbox.py").read_text("utf-8"))
    (excludes,) = [n.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ZOE_TREE_EXCLUDES"]
    assert "os-shadow" in ast.literal_eval(excludes)
