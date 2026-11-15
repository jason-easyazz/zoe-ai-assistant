"""Synthetic, socket-free regressions for the three forget review threads."""
import glob

import pytest

from zoe_os.brain.drivers import BRAIN, Alarms, Keeper, Lists, Reminders as DriverReminders
from zoe_os.capability import Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused, Spine
from zoe_os.skills.reminders import Reminders
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.twin.baseline import Twin
from reminder_store import ReminderStore

PERSON = 'synthetic-orbit'
SECRET, KEEP = 'velvet comet token', 'copper fern token'


def boot(path):
    k = Kernel(str(path), [Person(PERSON)])
    k.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
    k.grants.install(BRAIN)
    k.grants.grant(PERSON, 'brain')
    return k


def add(k, store, text):
    ev = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': text}))
    r = k.actions.act('brain', 'list.add', PERSON, {'item': text, 'asked': ev.seq},
                      caused_by=(ev.seq,))
    return ev, r


def disk(path):
    return b''.join(open(p, 'rb').read() for p in glob.glob(str(path) + '*'))


@pytest.mark.parametrize('scope', ['seqs', 'entity', 'person'])
@pytest.mark.parametrize('legacy', [False, True])
def test_snapshot_forget_bytes_and_undo_after_restart(tmp_path, scope, legacy):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists().register(k)
    k.install(Keeper(rows))
    first, _ = add(k, rows, SECRET)
    _, second = add(k, rows, KEEP)
    if legacy:
        p = k.spine._get(second.seq).payload
        p['undo']['before'].pop('provenance', None)
        import json
        k.spine._db.execute('UPDATE events SET payload = ? WHERE seq = ?',
                            (json.dumps(p), second.seq))
    k.grants.install({'skill': 'snapshot-reader', 'reads': {'action_receipt': 'private'}})
    k.grants.grant(PERSON, 'snapshot-reader')
    queued = k.spine.subscribe('snapshot-reader', kinds={Kind.ACTION_RECEIPT}, from_seq=0)
    queued._catch_up()
    assert SECRET.encode() in disk(path)
    args = {'seqs': [first.seq]} if scope == 'seqs' else {'entity': SECRET} if scope == 'entity' else {}
    k.spine.forget(PERSON, actor=PERSON, **args)
    k.pump()
    assert SECRET.encode() not in disk(path)
    assert SECRET not in str([e.payload for e in iter(queued.poll, None)])
    if scope != 'person':
        assert [r['item'] for r in rows.rows[PERSON]] == [KEEP]
    k.close()
    k = boot(path)
    rows.register(k)
    if scope == 'person' or legacy:
        with pytest.raises(Refused, match='nothing to undo|without provenance'):
            k.actions.undo(PERSON, actor=PERSON)
    else:
        assert k.actions.undo(PERSON, actor=PERSON).verified
        assert rows.rows[PERSON] == []
    assert SECRET.encode() not in disk(path)
    k.close()


def test_snapshot_scrub_keeps_nonforgotten_before_rows(tmp_path):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists().register(k)
    k.install(Keeper(rows))
    add(k, rows, KEEP)
    first, _ = add(k, rows, SECRET)
    add(k, rows, 'silver reed token')
    k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    k.pump()
    assert SECRET.encode() not in disk(path)
    assert not k.spine._shielded(Event(Kind.ACTION_RECEIPT, 'kernel', PERSON,
                                     payload={'proof': {'restored': {'item': KEEP}}}))
    assert k.actions.undo(PERSON, actor=PERSON).verified
    assert [r['item'] for r in rows.rows[PERSON]] == [KEEP]
    assert SECRET.encode() not in disk(path)
    assert SECRET not in str(k.spine._db.execute('SELECT payload FROM events').fetchall())
    k.close()


@pytest.mark.parametrize('opaque', [False, True])
def test_legacy_snapshot_index_backfills_once_before_forget(tmp_path, opaque):
    import json
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists().register(k)
    first, _ = add(k, rows, SECRET)
    _, second = add(k, rows, KEEP)
    if opaque:
        p = k.spine._get(second.seq).payload
        p['undo']['before'].pop('provenance')
        k.spine._db.execute('UPDATE events SET payload=? WHERE seq=?', (json.dumps(p), second.seq))
    # Emulate a store from before the index existed, then upgrade on restart.
    k.spine._db.execute('DROP TABLE snapshot_dependencies')
    k.spine._db.execute('DROP TABLE opaque_snapshots')
    k.spine._db.execute("DELETE FROM meta WHERE key='snapshot_index_v1'")
    k.close()
    k = boot(path)
    assert k.spine._db.execute("SELECT 1 FROM meta WHERE key='snapshot_index_v1'").fetchone()
    k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    assert SECRET.encode() not in disk(path)
    k.close()
    k = boot(path)
    rows.rows[PERSON] = [{'item': KEEP, 'asked': 999}]
    rows.register(k)
    if opaque:
        with pytest.raises(Refused, match='without provenance'):
            k.actions.undo(PERSON, actor=PERSON)
    else:
        assert k.actions.undo(PERSON, actor=PERSON).verified
        assert rows.rows[PERSON] == []
    k.close()


def test_snapshot_dependencies_survive_scrub_and_rollback_then_member_erase(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    rows = Lists().register(k)
    first, _ = add(k, rows, SECRET)
    survivor, _ = add(k, rows, KEEP)
    _, last = add(k, rows, 'synthetic third row')
    before = k.spine._db.execute('SELECT * FROM snapshot_dependencies ORDER BY 1,2').fetchall()
    k.spine._db.execute("CREATE TEMP TRIGGER stop_erase BEFORE INSERT ON forget_ledger "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    assert k.spine._db.execute('SELECT * FROM snapshot_dependencies ORDER BY 1,2').fetchall() == before
    k.spine._db.execute('DROP TRIGGER stop_erase')
    k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    assert k.spine._db.execute('SELECT * FROM snapshot_dependencies').fetchall() == [(survivor.seq, last.seq)]
    k.spine.forget(PERSON, actor=PERSON)
    assert not k.spine._db.execute('SELECT * FROM snapshot_dependencies').fetchall()
    assert not k.spine._db.execute('SELECT * FROM opaque_snapshots').fetchall()
    k.close()


def test_forget_scrubs_a_surviving_undo_readback_proof(tmp_path):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists().register(k)
    k.install(Keeper(rows))
    first, _ = add(k, rows, SECRET)
    add(k, rows, KEEP)
    back = k.actions.undo(PERSON, actor=PERSON)
    assert SECRET in str(k.spine._get(back.seq).payload['proof'])
    k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    k.pump()
    assert k.spine._get(back.seq).payload['proof']['restored']['rows'] == []
    assert SECRET.encode() not in disk(path)
    k.close()


@pytest.mark.parametrize('disable_shield', [False, True])
def test_control_without_snapshot_scrub_shield_refuses_dirty_receipt_or_row_resurrects(
        tmp_path, monkeypatch, disable_shield):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists().register(k)
    k.install(Keeper(rows))
    first, _ = add(k, rows, SECRET)
    add(k, rows, KEEP)
    monkeypatch.setattr(Spine, '_scrub_snapshots', lambda *_: None, raising=False)
    k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
    k.pump()
    assert SECRET.encode() in disk(path)
    if disable_shield:
        # Isolate the snapshot protection: the independent append shield would
        # otherwise refuse the dirty receipt AFTER the driver restored the row.
        monkeypatch.setattr(Spine, '_shielded', lambda *_: False)
        assert k.actions.undo(PERSON, actor=PERSON).verified
    else:
        with pytest.raises(Refused, match='kernel append: carries forgotten content'):
            k.actions.undo(PERSON, actor=PERSON)
    assert [r['item'] for r in rows.rows[PERSON]] == [SECRET]
    k.close()


@pytest.mark.parametrize('scope', ['seqs', 'empty', 'person', 'unknown', 'missing'])
def test_every_subscriber_obeys_notice_scope(tmp_path, scope):
    k = boot(tmp_path / 'kernel.db')
    lists, reminders, alarms = Lists(), DriverReminders(lambda: 0), Alarms()
    keeper = Keeper(lists, reminders, alarms)
    skill, store, twin = Reminders(), ReminderStore().register(k), Twin()
    k.install(skill)
    k.grants.grant(PERSON, 'reminders')
    leaving, calendar = LeavingCatch(), CalendarHeadsUp()
    for seq, text in [(11, SECRET), (22, KEEP)]:
        lists.rows.setdefault(PERSON, []).append({'item': text, 'asked': seq})
        reminders.rows.setdefault(PERSON, []).append({'note': text, 'asked': seq})
        rid = f'r{seq}'
        store.rows[rid] = {'owner': PERSON, 'note': text}
        skill._items[rid] = {'person': PERSON, 'asked': seq, 'receipt': seq + 100}
        twin.days.setdefault(PERSON, {}).setdefault('2032-06-01', {})[text] = (True, seq)
        leaving._undone.setdefault(PERSON, {})[text] = seq
        calendar._open[f'headsup-{seq}'] = (PERSON, text)
    alarms.at[PERSON] = {'time': KEEP, 'asked': 22}
    payload = {'subject': PERSON, 'seqs': [] if scope == 'empty' else [11]}
    if scope != 'missing':
        payload['scope'] = 'seqs' if scope in {'seqs', 'empty'} else scope
    notice = k.spine._kernel_append(Event(Kind.FORGET, 'kernel', visibility=Visibility.KERNEL,
                                         payload=payload))
    keeper.on_event(notice, None)
    ctx = k._skills[0][2]
    skill.on_event(notice, ctx)
    twin.on_event(notice, None)
    leaving.on_event(notice, None)
    calendar.on_event(notice, None)
    expected = [SECRET, KEEP] if scope == 'empty' else [KEEP] if scope == 'seqs' else []
    assert [r['item'] for r in lists.rows.get(PERSON, [])] == expected
    assert [r['note'] for r in reminders.rows.get(PERSON, [])] == expected
    assert [r['note'] for r in store.rows.values()] == expected
    assert list(twin.days.get(PERSON, {}).get('2032-06-01', {})) == expected
    assert list(leaving._undone.get(PERSON, {})) == expected
    assert [v[1] for v in calendar._open.values()] == expected
    assert bool(alarms.at) == bool(expected)
    k.close()


@pytest.mark.parametrize('n', [260, 1000])
@pytest.mark.parametrize('truncate', [False, True])
def test_all_chunks_replay_after_partial_delivery_restart(tmp_path, n, truncate, monkeypatch):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    rows = Lists()
    events = k.spine.publish_many([
        Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': f'{SECRET} {i}'})
        for i in range(n)])
    rows.rows[PERSON] = [{'item': SECRET, 'asked': e.seq} for e in events]
    k.install(Keeper(rows))
    skill, twin = Reminders(), Twin()
    store = ReminderStore().register(k)
    for e in events:
        rid = f'r{e.seq}'
        store.rows[rid] = {'owner': PERSON, 'note': SECRET}
        skill._items[rid] = {'person': PERSON, 'asked': e.seq, 'receipt': e.seq}
        twin.days.setdefault(PERSON, {}).setdefault('2032-06-01', {})[rid] = (SECRET, e.seq)
    k.install(skill)
    k.install(twin)
    k.grants.grant(PERSON, 'reminders')
    if truncate:
        real = Spine._append
        def old_notice(self, events, then=None, *, before=None):
            def prepare():
                if before is not None:
                    before()
                if events and events[0].kind is Kind.FORGET:
                    del events[1:]  # old single truncated notice: negative control
            return real(self, events, then, before=prepare)
        monkeypatch.setattr(Spine, '_append', old_notice)
    assert k.spine.forget(PERSON, actor=PERSON, entity=SECRET).count == n
    notices = k.spine._fetch(0, frozenset({Kind.FORGET}), None, n)
    assert all(len(e.payload['seqs']) <= 256 for e in notices)
    # A crash after the first handled chunk, with the external store surviving.
    k.pump(max_rounds=1)
    assert len(rows.rows.get(PERSON, [])) == n - 256
    # The registered store is purged synchronously before any notice is delivered.
    assert len(store.rows) == 0
    k.close()
    k = boot(path)
    k.install(Keeper(rows))
    store.register(k)
    k.install(Reminders())   # cache lost; the durable notice must still purge the store
    k.install(twin)          # stands in for reloading the reduced facts
    k.grants.grant(PERSON, 'reminders')
    k.pump()
    assert len(rows.rows.get(PERSON, [])) == (n - 256 if truncate else 0)
    assert len(store.rows) == 0
    assert len(twin.days.get(PERSON, {}).get('2032-06-01', {})) == (n - 256 if truncate else 0)
    if not truncate:
        assert [s for e in notices for s in e.payload['seqs']] == [e.seq for e in events]
        for e in notices:  # re-delivery is idempotent
            k._skills[0][0].on_event(e, None)
        assert PERSON not in rows.rows
    k.close()


def test_empty_selection_is_explicit_on_the_spine(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    e = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': KEEP}))
    assert k.spine.forget(PERSON, actor=PERSON, seqs=[]).count == 0
    notice = k.spine._fetch(e.seq, frozenset({Kind.FORGET}), None, 1)[0]
    assert notice.payload['scope'] == 'seqs' and notice.payload['seqs'] == []
    assert k.spine._get(e.seq) is not None
    k.close()


def test_oversized_entity_is_refused_without_truncation(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    with pytest.raises(Refused, match='too many words'):
        k.spine.forget(PERSON, actor=PERSON, entity='amber birch cobalt dune ember frost glade')
    k.close()


def test_forget_notice_and_erase_roll_back_together(tmp_path):
    import sqlite3
    k = boot(tmp_path / 'kernel.db')
    e = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': SECRET}))
    k.spine._db.execute("CREATE TEMP TRIGGER abort_forget BEFORE INSERT ON forget_ledger "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END")
    with pytest.raises(sqlite3.IntegrityError):
        k.spine.forget(PERSON, actor=PERSON, entity=SECRET)
    assert k.spine._get(e.seq) is not None
    assert not k.spine._fetch(0, frozenset({Kind.FORGET}), None, 100)
    assert not k.spine._shield and not k.spine._entities
    k.close()


@pytest.mark.parametrize('empty', [False, True])
def test_generated_notice_preserves_other_reminders_and_twin_facts(tmp_path, empty):
    k = boot(tmp_path / 'kernel.db')
    skill, twin = Reminders(), Twin()
    store = ReminderStore().register(k)
    k.install(skill)
    k.install(twin)
    k.grants.grant(PERSON, 'reminders')
    k.grants.grant(PERSON, 'twin')
    sources = []
    for text in (SECRET, KEEP):
        e = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON,
            payload={'text': text, 'intent': {'name': 'reminder.create', 'at': 9999999999,
                                             'note': text}}))
        sources.append(e.seq)
        twin.days.setdefault(PERSON, {}).setdefault('2032-06-01', {})[text] = (True, e.seq)
    k.pump()
    k.spine.forget(PERSON, actor=PERSON, seqs=[] if empty else sources[:1])
    k.pump()
    expected = [SECRET, KEEP] if empty else [KEEP]
    assert [r['note'] for r in store.rows.values()] == expected
    assert [r['note'] for r in skill._items.values()] == expected
    assert list(twin.days[PERSON]['2032-06-01']) == expected
    k.close()
