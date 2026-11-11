"""Property tests: forget over random derivation DAGs leaves zero residue (scratch sqlite only).

Run: flock -x -w 3600 /tmp/zoe-test-lane.lock python3 -m pytest -q labs/forget-first-memory  (no -n)
"""
import os
import random
import sys

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
    untouched_before = {a for a, _ in s.serve()}
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
    assert untouched_before - closures <= {a for a, _ in s.serve()}
    s.rebuild(concat)
    for v in victims:
        assert_forgotten(s, markers[v], payloads[v])
    survivors = set(markers) - set(victims)
    assert all(s.residue(markers[x]) for x in survivors), "over-deletion: a surviving source vanished"


@pytest.mark.parametrize("seed", range(20))
def test_serve_walls_a_missed_quarantine(seed):
    s, rng, markers = _build(seed)
    v = rng.choice(sorted(markers))
    s.write_derived("wall_probe", "Lx", f"probe summary of {s._payload(v)}", [v])
    assert [a for a, p in s.serve() if markers[v] in p], "positive control: the marker is served before forget"
    closure = s.forget_phase1(v)
    assert "wall_probe" in closure
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
