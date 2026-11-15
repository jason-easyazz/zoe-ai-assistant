"""Socket-free complexity guards: SQLite VM steps count work inside single statements.

Statement counts alone miss a full-table scan disguised as one SELECT/DELETE.
These budgets cover fixed work, not elapsed time, fsync or checkpoint I/O.
"""
from contextlib import contextmanager

import pytest

from spine_helpers import AllowList
from zoe_os.events import Event, Kind
from zoe_os.receipts import Dispatcher
from zoe_os.spine import Refused, Spine

PERSON = 'member-a'


@contextmanager
def vm_budget(db, maximum=4000):
    steps = [0]

    def progress():
        steps[0] += 1
        return 0

    db.set_progress_handler(progress, 1)
    try:
        yield steps
    finally:
        db.set_progress_handler(None, 0)
    assert steps[0] <= maximum, f'unbounded SQL work: {steps[0]} > {maximum}'


@pytest.fixture(params=[200, 5000])
def history(tmp_path, request):
    auth = AllowList(origins={'voice'}, readers={'all': {'*'}})
    spine = Spine(str(tmp_path / 'history.db'), auth)
    dispatcher = Dispatcher(spine, auth)
    payload = {'action': 'synthetic.act', 'verified': True, 'outcome': 'verified',
               'reversible': True, 'undo': {'args': {}, 'before': {'value': 0}}}
    # Both link tables AND completed intents grow with the history.
    with spine._lock:
        rows = spine._append([Event(Kind.ACTION_RECEIPT, 'kernel', subject=PERSON,
                                   caused_by=(1,), payload=payload)
                              for _ in range(request.param)])
    spine._db.execute('BEGIN IMMEDIATE')
    spine._db.executemany(
        "INSERT INTO intents(ts,skill,action,person,receipt_seq,caused_by) VALUES(0,'s','a',?,?, '[]')",
        [(PERSON, r.seq) for r in rows])
    spine._db.execute('COMMIT')
    dispatcher.register('synthetic.act', lambda p, a: None, lambda p, a, r: {'ok': True},
                        snapshot=lambda p, a: {'value': 0}, undo=lambda p, a, b: None)
    try:
        yield spine, dispatcher, rows
    finally:
        spine.close()


def test_undo_has_bounded_sql_work(history):
    spine, dispatcher, rows = history
    with vm_budget(spine._db):
        receipt = dispatcher.undo(PERSON, actor=PERSON)
    assert receipt.verified
    assert spine._get(receipt.seq).payload['undoes'] == rows[-1].seq
    # Empty member lookup must also avoid traversing other people's history.
    with vm_budget(spine._db):
        assert dispatcher.last_action('member-absent') is None


def test_scoped_forget_has_bounded_sql_work(history):
    spine, dispatcher, rows = history
    # A single isolated event, matching the benchmark (no descendants).
    victim = spine.publish(Event(Kind.UTTERANCE, 'voice', subject=PERSON,
                                 payload={'text': 'synthetic isolated text'}))
    with vm_budget(spine._db):
        result = spine.forget(PERSON, actor=PERSON, seqs=[victim.seq])
    assert result.count == 1 and spine._get(victim.seq) is None
    # Scrubbing a completed receipt must be bounded as well, not just a no-hit query.
    with vm_budget(spine._db):
        assert spine.forget(PERSON, actor=PERSON, seqs=[rows[-1].seq]).count == 1


def test_negative_control_detects_one_statement_full_scan(history):
    spine, _, _ = history
    with pytest.raises(AssertionError, match='unbounded SQL work'):
        with vm_budget(spine._db, maximum=100):
            spine._db.execute('SELECT seq FROM people NOT INDEXED WHERE person=?',
                              (PERSON,)).fetchall()


def test_negative_control_detects_old_undo_lookup(history, monkeypatch):
    spine, dispatcher, _ = history
    monkeypatch.setattr(dispatcher, 'last_action', lambda person: next(
        (e for e in spine._newest(frozenset({Kind.ACTION_RECEIPT}), person)
         if e.subject == person), None))
    with pytest.raises(AssertionError, match='unbounded SQL work'):
        with vm_budget(spine._db):
            dispatcher.undo(PERSON, actor=PERSON)


def test_negative_control_detects_old_forget_scan(history, monkeypatch):
    spine, _, rows = history
    if len(rows) < 5000:
        return  # The large fixture distinguishes a history scan from fixed work.
    real = spine._scrub_intents

    def old_scan(dead):
        # The old OR query walked completed intents even for a no-hit forget.
        spine._db.execute(
            'SELECT id FROM intents NOT INDEXED WHERE receipt_seq=? OR accept_seq=? OR undoes=?',
            (-1, -1, -1)).fetchall()
        real(dead)

    monkeypatch.setattr(spine, '_scrub_intents', old_scan)
    victim = spine.publish(Event(Kind.UTTERANCE, 'voice', subject=PERSON,
                                 payload={'text': 'synthetic isolated text'}))
    with pytest.raises(AssertionError, match='unbounded SQL work'):
        with vm_budget(spine._db):
            spine.forget(PERSON, actor=PERSON, seqs=[victim.seq])


def test_forget_rollback_does_not_publish_or_change_shields(history, monkeypatch):
    spine, _, rows = history
    before = spine.last_seq
    real = spine._scrub_intents

    def fail(dead):
        real(dead)
        raise RuntimeError('synthetic failure')

    monkeypatch.setattr(spine, '_scrub_intents', fail)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        spine.forget(PERSON, actor=PERSON, seqs=[rows[-1].seq], entity='synthetic')
    assert spine.last_seq == before and spine._get(rows[-1].seq) is not None
    assert not spine._shield and not spine._entities
    assert spine._db.execute('SELECT count(*) FROM forget_ledger').fetchone()[0] == 0


def test_open_intent_cause_is_still_scrubbed(history):
    spine, dispatcher, rows = history
    intent = dispatcher._open_intent('skill', 'action', PERSON, None, None, (rows[-1].seq,))
    spine.forget(PERSON, actor=PERSON, seqs=[rows[-1].seq])
    assert spine._db.execute('SELECT id FROM intents WHERE id=?', (intent,)).fetchone() is None


def test_new_schema_stays_inside_oversized_frame_disk_ceiling(tmp_path):
    path = tmp_path / 'size.db'
    spine = Spine(str(path))
    try:
        for _ in range(2):
            with pytest.raises(Refused, match='frame too large'):
                spine._refuse('unverified-peer', 'send a frame', 'frame too large')
        # Include WAL and SHM while open, just like the wire test's oracle.
        assert sum(p.stat().st_size for p in tmp_path.glob('size.db*')) < 200_000
    finally:
        spine.close()
