"""A forget reaches a store row whose request has already expired to its retention skeleton.

The brain drivers key their rows by ``asked`` (the request's seq). A skeleton has no readable
body, but its seq is all a purge needs, so it is handed to the purge AND to the read-back.
Controls put back the former filter (readable bodies only).
"""
import pytest

from test_history_cost import vm_budget
from zoe_os.brain.drivers import Lists
from zoe_os.capability import Person
from zoe_os.events import Event, Kind
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused, Skeleton, Spine

A, B = 'member-a', 'member-b'


@pytest.fixture
def k(tmp_path):
    kernel = Kernel(str(tmp_path / 'skeleton.db'), [Person(A), Person(B)])
    kernel.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
    kernel.grants.install({'skill': 'brain', 'reads': {'utterance': 'private'}, 'acts': ['list.add']})
    kernel.lists = Lists().register(kernel)
    yield kernel
    kernel.close()


def added(k, person, item='synthetic oat'):
    k.grants.grant(person, 'brain')
    ask = k.spine.publish(Event(Kind.UTTERANCE, 'voice', person, payload={'text': 'synthetic ask'}))
    assert k.actions.act('brain', 'list.add', person, {'item': item, 'asked': ask.seq},
                         caused_by=(ask.seq,)).verified
    return ask


def old_filter(monkeypatch):
    """The former input: only events whose body is still readable."""
    monkeypatch.setattr(Spine, '_erased_events',
                        lambda self, seqs: tuple(e for s in seqs if (e := self._get(s))))


def test_scoped_forget_of_an_expired_request_removes_its_row(k):
    ask = added(k, A)
    k.spine._expire([ask.seq])
    assert k.spine._get(ask.seq) is None and k.lists.rows[A]
    k.spine.forget(A, actor=A, seqs=[ask.seq])
    assert A not in k.lists.rows


def test_control_old_filter_reports_success_and_the_row_stays(k, monkeypatch):
    old_filter(monkeypatch)
    ask = added(k, A)
    k.spine._expire([ask.seq])
    assert k.spine.forget(A, actor=A, seqs=[ask.seq]).count >= 1   # "success"
    assert k.lists.rows[A] and k.lists.rows[A][0]['asked'] == ask.seq


def test_whole_forget_reaches_another_owners_row_keyed_by_an_expired_request(k):
    ask = added(k, A)
    mine = added(k, B, 'synthetic rye')
    k.lists.rows[B].append({'list': 'shopping', 'item': 'synthetic copy', 'asked': ask.seq})
    k.spine._expire([ask.seq])
    k.spine.forget(A, actor=A)
    assert A not in k.lists.rows
    assert [r['asked'] for r in k.lists.rows[B]] == [mine.seq]       # B's own row stays


def test_control_whole_forget_old_filter_leaves_the_cross_owner_row(k, monkeypatch):
    old_filter(monkeypatch)
    ask = added(k, A)
    k.lists.rows.setdefault(B, []).append({'list': 'shopping', 'item': 'synthetic copy', 'asked': ask.seq})
    k.spine._expire([ask.seq])
    k.spine.forget(A, actor=A)
    assert [r['asked'] for r in k.lists.rows[B]] == [ask.seq]


def test_read_back_sees_the_skeleton_so_a_purge_that_skips_it_aborts_the_forget(k):
    ask = added(k, A)
    k.spine._expire([ask.seq])
    store = {'rows': {ask.seq}}
    k.register_store_purge(
        'synthetic-live-only',
        lambda p, events: store['rows'].difference_update(
            {e.seq for e in events or () if not isinstance(e, Skeleton)}),
        lambda p, events: not (store['rows'] & {e.seq for e in events or ()}))
    with pytest.raises(Refused, match='external store purge unverified'):
        k.spine.forget(A, actor=A, seqs=[ask.seq])
    # The log rolled back (external deletes already done may remain; retry completes them).
    assert k.spine._db.execute('SELECT tombstoned FROM events WHERE seq=?', (ask.seq,)).fetchone() == (2,)


@pytest.mark.parametrize('size', [200, 5000])
def test_reading_the_erased_set_is_bounded_by_the_set_not_the_history(k, size):
    ask = added(k, A)
    with k.spine._lock:
        k.spine._append([Event(Kind.UTTERANCE, 'voice', B, payload={'text': 'synthetic'})
                         for _ in range(size)])
    k.spine._expire([ask.seq])
    with vm_budget(k.spine._db, maximum=400):
        got = k.spine._erased_events([ask.seq])
    assert got == (Skeleton(ask.seq, Kind.UTTERANCE),)
