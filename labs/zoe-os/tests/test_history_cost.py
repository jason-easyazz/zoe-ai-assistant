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


def test_whole_forget_of_absent_member_does_not_scan_unrelated_history(history):
    spine, _, _ = history
    with vm_budget(spine._db):
        assert spine.forget('member-absent', actor='member-absent').count == 0


def test_negative_control_detects_one_statement_full_scan(history):
    spine, _, _ = history
    with pytest.raises(AssertionError, match='unbounded SQL work'):
        with vm_budget(spine._db, maximum=100):
            spine._db.execute('SELECT seq FROM people NOT INDEXED WHERE person=?',
                              (PERSON,)).fetchall()


@pytest.mark.parametrize('dependants', [1, 50])
def test_snapshot_lookup_cost_depends_on_hits(history, dependants):
    spine, _, _ = history
    victim = spine.publish(Event(Kind.UTTERANCE, 'voice', subject=PERSON,
                                 payload={'text': 'synthetic snapshot source'}))
    payload = {'undo': {'args': {}, 'before': {'provenance': 1,
               'rows': [{'asked': victim.seq, 'item': 'synthetic snapshot source'}]}}}
    with spine._lock:
        receipts = spine._append([Event(Kind.ACTION_RECEIPT, 'kernel', subject=PERSON,
                                       payload=payload) for _ in range(dependants)])
    # The unchanged 4000-step budget covers even 50 index hits, amidst 5000 receipts.
    with vm_budget(spine._db):
        hits = spine._db.execute('SELECT receipt_seq FROM snapshot_dependencies '
                                 'WHERE source_seq IN (?)', (victim.seq,)).fetchall()
    assert {r[0] for r in hits} == {r.seq for r in receipts}
    # Fixed overhead remains 4000; allow 150 steps per actual payload rewrite.
    # In particular, the 160k-step old scan cannot hide inside the 50-hit allowance.
    real = spine._index_snapshot
    visited = []
    def measured(seq, person, p):
        real(seq, person, p)
        visited.append(seq)
    spine._index_snapshot = measured
    with vm_budget(spine._db, maximum=4000 + 150 * dependants):
        spine.forget(PERSON, actor=PERSON, seqs=[victim.seq])
    assert set(visited) == {r.seq for r in receipts}
    assert not spine._db.execute('SELECT * FROM snapshot_dependencies').fetchall()
    assert all(spine._get(r.seq).payload['undo']['before']['rows'] == [] for r in receipts)


def test_negative_control_detects_snapshot_history_scan(history, monkeypatch):
    spine, _, rows = history
    if len(rows) < 5000:
        return
    real = spine._scrub_snapshots
    def scan(dead):
        spine._db.execute("SELECT seq, subject, payload FROM events "
                          "WHERE kind = 'action_receipt' AND tombstoned = 0").fetchall()
        real(dead)
    monkeypatch.setattr(spine, '_scrub_snapshots', scan)
    victim = spine.publish(Event(Kind.UTTERANCE, 'voice', subject=PERSON,
                                 payload={'text': 'synthetic scan control'}))
    with pytest.raises(AssertionError, match='unbounded SQL work'):
        with vm_budget(spine._db):
            spine.forget(PERSON, actor=PERSON, seqs=[victim.seq])


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


def test_new_schema_keeps_oversized_body_off_disk(tmp_path):
    from world import household

    # Match the socket launcher, including its initial audit grant. An empty
    # DenyAll spine understated the live WAL pages in the failing wire scenario.
    kernel = household(tmp_path)
    spine = kernel.spine
    try:
        for _ in range(2):
            with pytest.raises(Refused, match='frame too large'):
                spine._refuse('unverified-peer', 'send a frame', 'frame too large')
        # Same body and oracle as test_boundary_attacks; schema size is not the
        # guarantee. Include WAL and SHM while the store is open.
        body = b'x' * 200_000
        on_disk = b''.join(p.read_bytes() for p in tmp_path.glob('zoe-os.db*'))
        assert body[:4096] not in on_disk
        assert len(on_disk) < 2 * len(body)
    finally:
        kernel.close()


def test_negative_control_detects_oversized_body_on_disk(tmp_path, monkeypatch):
    real = Spine._refuse

    def leaking_refusal(self, *args, **kwargs):
        # Simulate a boundary that retained the body before rejecting it. This
        # must fail the content oracle regardless of schema/WAL overhead.
        self._db.execute('UPDATE events SET payload=? WHERE seq=(SELECT MIN(seq) FROM events)',
                         ('{"text":"' + 'x' * 200_000 + '"}',))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Spine, '_refuse', leaking_refusal)
    with pytest.raises(AssertionError):
        test_new_schema_keeps_oversized_body_off_disk(tmp_path)


def test_snapshot_source_lookup_chunks_and_expired_receipt_cleanup(tmp_path):
    auth = AllowList(origins={'voice'}, readers={'all': {'*'}})
    spine = Spine(str(tmp_path / 'chunks.db'), auth)
    try:
        sources = spine.publish_many([Event(Kind.UTTERANCE, 'voice', subject=PERSON,
                                            payload={'text': 'synthetic source'}) for _ in range(550)])
        payload = {'undo': {'before': {'provenance': 1, 'rows': [
            {'asked': e.seq, 'item': 'synthetic source'} for e in sources]}}}
        with spine._lock:
            receipt = spine._append([Event(Kind.ACTION_RECEIPT, 'kernel', subject=PERSON,
                                           payload=payload)])[0]
        spine.forget(PERSON, actor=PERSON, seqs=[e.seq for e in sources[:-1]])
        assert spine._db.execute('SELECT * FROM snapshot_dependencies').fetchall() == [
            (sources[-1].seq, receipt.seq)]
        spine._expire([receipt.seq])
        assert not spine._db.execute('SELECT * FROM snapshot_dependencies').fetchall()
        spine.forget(PERSON, actor=PERSON, seqs=[sources[-1].seq])
    finally:
        spine.close()
