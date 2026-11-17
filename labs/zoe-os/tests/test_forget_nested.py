"""A whole-person forget reaches an event about someone else whose payload names the member.

The member id may sit anywhere in an ordinary payload: as a value, as a dict key, nested in
lists. Only whole strings equal to a known member id count; an id inside free text is the
name shield's job. Each guarantee has a control that removes the nested index.
"""
import glob
import sqlite3

import pytest

from spine_helpers import AllowList
from test_history_cost import vm_budget
from zoe_os import spine as spine_mod
from zoe_os.capability import ADULT, Person
from zoe_os.events import Event, Kind
from zoe_os.kernel import Kernel
from zoe_os.spine import Spine

A, B, C = 'member-a', 'member-b', 'member-c'
SECRET = 'zx-nested-sentinel-cobalt-4417'
SHAPES = {
    'value': {'offer_id': 'o', 'text': SECRET, 'for': A},
    'key': {'offer_id': 'o', 'text': SECRET, A: True},
    'list': {'offer_id': 'o', 'text': SECRET, 'deep': [{'who': [A]}]},
}


def auth():
    return AllowList(origins={'voice'}, skills={'skill'},
                     readers={'skill': {'*'}, A: {A}, B: {B}})


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / 'nested.db')


@pytest.fixture
def spine(path):
    s = Spine(path, auth(), members={A, B})
    yield s
    s.close()


def on_disk(path) -> bytes:
    return b''.join(open(p, 'rb').read() for p in glob.glob(path + '*'))


def b_event(s, payload):
    cause = s.publish(Event(Kind.UTTERANCE, 'voice', B, payload={'text': 'synthetic b cause'}))
    return cause, s.publish(Event(Kind.OFFER, 'skill', B, payload=payload, caused_by=(cause.seq,)))


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_whole_forget_selects_and_erases_a_payload_naming_the_member(spine, path, shape):
    cause, naming = b_event(spine, SHAPES[shape])
    kept = spine.publish(Event(Kind.UTTERANCE, 'voice', B, payload={'text': 'synthetic b only'}))
    assert SECRET.encode() in on_disk(path)                          # planted: it was there
    assert spine.forget(A, actor=A).count == 1                       # the naming event, nothing else
    survivors = {e.seq for e in spine.replay(B)}
    assert naming.seq not in survivors and {cause.seq, kept.seq} <= survivors
    assert SECRET not in str(spine.export('skill'))
    assert SECRET.encode() not in on_disk(path)                      # database and WAL


def test_negative_control_without_the_nested_index_the_event_survives(spine, path, monkeypatch):
    monkeypatch.setattr(spine_mod, '_nested_ids', lambda *args: set())
    _, naming = b_event(spine, SHAPES['value'])
    assert spine.forget(A, actor=A).count == 0
    assert naming.seq in {e.seq for e in spine.replay(B)}
    assert SECRET.encode() in on_disk(path)


def test_an_id_inside_free_text_is_not_an_identity(spine):
    _, ev = b_event(spine, {'offer_id': 'o', 'text': f'synthetic note about {A} today'})
    assert spine.forget(A, actor=A).count == 0
    assert ev.seq in {e.seq for e in spine.replay(B)}


def test_scoped_forget_is_unaffected_by_the_nested_index(spine):
    _, naming = b_event(spine, SHAPES['value'])
    own = spine.publish(Event(Kind.UTTERANCE, 'voice', A, payload={'text': 'synthetic a words'}))
    assert spine.forget(A, actor=A, seqs=[own.seq, naming.seq]).count == 1   # not A's event to name
    assert naming.seq in {e.seq for e in spine.replay(B)}


def test_rows_written_before_the_index_are_backfilled_at_open(path):
    s = Spine(path, auth())                                          # no members: nothing indexed
    _, naming = b_event(s, SHAPES['list'])
    s.close()
    db = sqlite3.connect(path)
    assert db.execute('SELECT COUNT(*) FROM nested_people').fetchone()[0] == 0
    db.close()
    again = Spine(path, auth(), members={A, B})
    try:
        assert again.forget(A, actor=A).count == 1
        assert naming.seq not in {e.seq for e in again.replay(B)}
        assert SECRET.encode() not in on_disk(path)
    finally:
        again.close()


def test_negative_control_without_the_backfill_an_old_row_survives(path, monkeypatch):
    s = Spine(path, auth())
    _, naming = b_event(s, SHAPES['list'])
    s.close()
    monkeypatch.setattr(Spine, '_backfill_nested', lambda self: None)
    again = Spine(path, auth(), members={A, B})
    try:
        assert again.forget(A, actor=A).count == 0
        assert naming.seq in {e.seq for e in again.replay(B)}
    finally:
        again.close()


def test_backfill_runs_once_and_the_marker_names_nobody(path):
    s = Spine(path, auth(), members={A, B})
    for _ in range(300):        # a re-scan of this history would cost far more than the budget
        b_event(s, SHAPES['value'])
    s.close()
    again = Spine(path, auth(), members={A, B})
    try:
        with vm_budget(again._db, maximum=200):
            again._backfill_nested()                                 # already marked: no scan
        for (owner,) in again._db.execute('SELECT owner FROM nested_members'):
            assert A.encode() not in owner and B.encode() not in owner
    finally:
        again.close()


def test_kernel_members_come_from_the_grant_store(tmp_path):
    k = Kernel(str(tmp_path / 'kernel.db'), [Person(A, ADULT), Person(B, ADULT)])
    try:
        k.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
        ev = k.spine.publish(Event(Kind.UTTERANCE, 'voice', B,
                                   payload={'text': SECRET, 'with': {A: [A]}}))
        assert k.spine.forget(A, actor=A).count == 1
        assert k.spine._get(ev.seq) is None
    finally:
        k.close()


@pytest.mark.parametrize('size', [200, 5000])
def test_whole_forget_cost_follows_the_member_not_the_history(path, size):
    s = Spine(path, auth(), members={A, B, C})
    try:
        cause = s.publish(Event(Kind.UTTERANCE, 'voice', B, payload={'text': 'synthetic b cause'}))
        with s._lock:     # a growing nested index that names another member, never A
            s._append([Event(Kind.OFFER, 'skill', B, caused_by=(cause.seq,),
                             payload={'offer_id': 'o', 'text': 'synthetic', 'for': C})
                       for _ in range(size)])
        assert s._db.execute('SELECT COUNT(*) FROM nested_people').fetchone()[0] == size
        _, naming = b_event(s, SHAPES['value'])
        with vm_budget(s._db):
            assert s.forget(A, actor=A).count == 1
        assert s._get(naming.seq) is None
    finally:
        s.close()


def test_publish_cost_with_nested_ids_stays_within_the_publish_budget(spine):
    cause = spine.publish(Event(Kind.UTTERANCE, 'voice', B, payload={'text': 'synthetic b cause'}))
    payload = {'offer_id': 'o', 'text': 'synthetic', 'deep': [{A: [A, B]} for _ in range(50)]}
    with vm_budget(spine._db):
        spine.publish(Event(Kind.OFFER, 'skill', B, payload=payload, caused_by=(cause.seq,)))
