"""Property tests: forget over random derivation DAGs leaves zero residue (scratch sqlite only).

Run: flock -x -w 3600 /tmp/zoe-test-lane.lock python3 -m pytest -q labs/forget-first-memory  (no -n)
"""
import os
import random
import sqlite3
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from lineage_store import Forgotten, LineageRequired, LineageStore, concat, file_residue, random_dag  # noqa: E402

SEEDS = range(60)


def _build(seed, path=":memory:", **kw):
    rng = random.Random(seed)
    s = LineageStore(path, **kw)
    markers = random_dag(s, rng, n_sources=rng.randint(8, 40), depth=rng.randint(1, 4),
                         per_layer=rng.randint(5, 30))
    return s, rng, markers


def assert_forgotten(s, marker, payload):
    assert s.residue(marker) == [], f"residue of {marker}: {s.residue(marker)}"
    assert not [a for a, p in s.serve() if marker in p], "served forgotten-derived text"
    for variant in (payload, "  " + payload.upper() + "  "):
        with pytest.raises(Forgotten):
            s.ingest("reingest", variant)


@pytest.mark.parametrize("seed", SEEDS)
def test_property_forget_leaves_no_residue_anywhere(seed):
    s, rng, markers = _build(seed)
    victims = rng.sample(sorted(markers), rng.randint(1, 3))
    payloads = {v: s._payload(v) for v in victims}
    for v in victims:  # every victim has at least one derived artifact, so no closure below is empty
        s.write_derived(f"probe_{v}", "Lx", f"probe summary of {payloads[v]}", [v])
    untouched_before = dict(s.serve())
    closures = set()
    for v in victims:
        assert s.residue(markers[v]), "positive control: the instrument must see the planted marker"
        closure = s.forget_phase1(v)
        closures |= set(closure)
        # mid-rebuild: payloads of the closure still sit in the table, but nothing serves them
        assert not [a for a, p in s.serve() if markers[v] in p]
        assert closure, "the planted probe must be in the closure"
        with pytest.raises(Forgotten):  # a late write onto the forgotten source alone is refused
            s.write_derived(f"race_src_{v}", "Lx", "late summary", [v])
        for a in closure[:2]:  # and onto a quarantined artifact alone (no forgotten source among the parents)
            with pytest.raises(Forgotten):
                s.write_derived(f"race_{a}", "Lx", "late summary", [a])
    s.forget_phase2()
    for v in victims:
        assert_forgotten(s, markers[v], payloads[v])
    # no collateral: every artifact outside the closures is still served, byte for byte
    served = dict(s.serve())
    assert all(served.get(a) == p for a, p in untouched_before.items() if a not in closures)
    s.rebuild(concat)
    for v in victims:
        assert_forgotten(s, markers[v], payloads[v])
    survivors = set(markers) - set(victims)
    assert all(s.db.execute("SELECT 1 FROM sources WHERE id = ? AND payload = ?", (x, s._payload(x))).fetchone()
               and s._payload(x) is not None for x in survivors), "over-deletion: a surviving source row vanished"


@pytest.mark.parametrize("seed", range(20))
def test_serve_walls_a_missed_quarantine(seed):
    s, rng, markers = _build(seed)
    v = rng.choice(sorted(markers))
    s.write_derived("wall_probe", "Lx", f"probe summary of {s._payload(v)}", [v])
    assert [a for a, p in s.serve() if markers[v] in p], "positive control: the marker is served before forget"
    probe_text = f"probe summary of {s._payload(v)}"
    closure = s.forget_phase1(v)
    assert "wall_probe" in closure
    keep = next(x for x in sorted(markers) if x != v)
    with pytest.raises(Forgotten):  # the forgotten DERIVED text cannot be re-admitted as a new source ...
        s.ingest("derived_back", probe_text)
    with pytest.raises(Forgotten):  # ... nor as a derived write under an unrelated live parent
        s.write_derived("derived_back", "Lx", probe_text, [keep])
    s.db.executemany("UPDATE artifacts SET state = 'live' WHERE id = ?", [(a,) for a in closure])  # the bug
    assert not [a for a, p in s.serve() if markers[v] in p]


def test_no_lineage_no_write():
    s = LineageStore(":memory:")
    with pytest.raises(LineageRequired):
        s.write_derived("x", "digest", "a summary of nothing", [])


@pytest.mark.parametrize("seed", range(20))
def test_negative_control_legacy_writer_is_caught(seed):
    """A lineage-less write (today's per-store pattern) must make the SAME property check fail."""
    s, rng, markers = _build(seed)
    v = rng.choice(sorted(markers))
    payload = s._payload(v)
    s.write_legacy("legacy_note", "speaking_style", f"they like to say {payload}")
    s.forget(v)
    with pytest.raises(AssertionError):
        assert_forgotten(s, markers[v], payload)
    assert "artifacts.payload" in s.residue(markers[v])


@pytest.mark.parametrize("secure,compact,expect_clean", [(True, True, True), (False, False, False)])
def test_raw_file_residue(tmp_path, secure, compact, expect_clean):
    path = str(tmp_path / "scratch.db")
    s, rng, markers = _build(7, path, secure_delete=secure, fts_compact=compact)
    v = sorted(markers)[0]
    s.close()
    assert file_residue(path, markers[v]), "positive control: marker bytes are in the file before forget"
    s = LineageStore(path, secure_delete=secure, fts_compact=compact)
    s.forget(v)
    s.close()
    assert file_residue(path, markers[v]) is (not expect_clean)


@pytest.mark.parametrize("writer", ["ingest", "write_derived"])
def test_forget_committed_while_a_writer_waits_for_the_lock(tmp_path, writer):
    """Real interleave on two connections: the writer is already inside its call, blocked on the write lock, when the
    forget ledger row commits. It must re-check under the lock and refuse (a check made before BEGIN would let it through)."""
    path = str(tmp_path / "race.db")
    s = LineageStore(path)
    s.ingest("keep", "a harmless surviving note")
    secret = "the forgotten sentence zq91"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # the forgetter holds the write lock
    result = {}

    def late_writer():
        w = LineageStore(path)
        try:
            if writer == "ingest":
                w.ingest("late", secret)
            else:
                w.write_derived("late", "Lx", secret, ["keep"])
            result["outcome"] = "written"
        except Forgotten:
            result["outcome"] = "refused"
        finally:
            w.close()

    t = threading.Thread(target=late_writer)
    t.start()
    time.sleep(0.5)  # the writer is now waiting on BEGIN IMMEDIATE
    assert t.is_alive(), "positive control: the writer must be blocked behind the lock"
    holder.execute("INSERT INTO forget_ledger VALUES (?)", (s._h(secret),))
    holder.execute("COMMIT")
    t.join(10)
    assert result == {"outcome": "refused"}
    assert s.residue("zq91") == []
