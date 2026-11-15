"""Socket-free action shield regressions; invented words and stores only."""
import hashlib
import json

import pytest

from test_forget_receipts_notices import PERSON, boot, disk
from zoe_os.brain.drivers import Keeper, Lists, Reminders
from zoe_os.events import Event, Kind
from zoe_os.receipts import Dispatcher
from zoe_os.spine import Refused, Spine

SECRET = 'mauve thistle parcel'
NAME = 'Velnora Mossquill'


def say(k, text):
    return k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': text}))


@pytest.mark.parametrize('action', ['list.add', 'remind.set'])
@pytest.mark.parametrize('scope', ['seqs', 'entity'])
def test_action_cannot_restore_forgotten_arguments(tmp_path, action, scope):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        store = (Lists() if action == 'list.add' else Reminders(lambda: 0)).register(k)
        k.install(Keeper(store))
        first = say(k, SECRET)
        args = {'item': SECRET} if action == 'list.add' else {'note': SECRET, 'in_minutes': 2}
        k.actions.act('brain', action, PERSON, args | {'asked': first.seq}, caused_by=(first.seq,))
        k.spine.forget(PERSON, actor=PERSON,
                       **({'seqs': [first.seq]} if scope == 'seqs' else {'entity': SECRET}))
        k.pump()
        assert not store.rows.get(PERSON)
        fresh = say(k, 'ordinary copper lantern')
        count = k.spine._db.execute('SELECT COUNT(*) FROM intents').fetchone()[0]
        with pytest.raises(Refused, match='forgotten_action_arguments'):
            k.actions.act('brain', action, PERSON, args | {'asked': fresh.seq}, caused_by=(fresh.seq,))
        assert not store.rows.get(PERSON)
        assert k.spine._db.execute('SELECT COUNT(*) FROM intents').fetchone()[0] == count
        refusal = k.spine._get(k.spine.last_seq)
        assert refusal.kind is Kind.REFUSAL
        assert refusal.payload == {'who': 'brain', 'attempted': f'act {action}',
                                   'reason': 'forgotten_action_arguments'}
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


@pytest.mark.parametrize('payload,entity', [
    ({'extra': ({'deep': [SECRET]},)}, False),
    ({'extra': ({'who': (f'visit {NAME} tomorrow',)},)}, True),
    ({'extra': ({NAME: 1},)}, True),
])
def test_action_shields_nested_tuple_values_and_entity_keys(tmp_path, payload, entity):
    k = boot(tmp_path / 'kernel.db')
    try:
        seen = []
        # No params schema: every registered action must meet the shields.
        k.actions.register('list.add', lambda person, args: seen.append(args),
                           lambda *_: {'ok': True}, needs_accept=False)
        old = say(k, f'{NAME} sent a parcel' if entity else SECRET)
        k.spine.forget(PERSON, actor=PERSON,
                       **({'entity': NAME} if entity else {'seqs': [old.seq]}))
        fresh = say(k, 'ordinary copper lantern')
        with pytest.raises(Refused, match='forgotten_action_arguments'):
            k.actions.act('brain', 'list.add', PERSON, payload, caused_by=(fresh.seq,))
        assert seen == []
    finally:
        k.close()


def test_allowed_action_receipt_and_proof_unchanged(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    try:
        store = Lists().register(k)
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        fresh = say(k, 'ordinary copper lantern')
        args = {'item': 'silver acorn', 'asked': fresh.seq}
        r = k.actions.act('brain', 'list.add', PERSON, args, caused_by=(fresh.seq,))
        assert r.verified and r.proof == {'item': 'silver acorn'}
        assert store.rows[PERSON] == [{'list': 'shopping', **args}]
        assert k.spine._get(r.seq).payload == {
            'action': 'list.add', 'skill': 'brain', 'grant_id': r.grant_id,
            'args_digest': hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16],
            'proof': r.proof, 'verified': True, 'outcome': 'verified', 'error': '',
            'reversible': True, 'asked': fresh.seq,
            'undo': {'args': args, 'before': {'provenance': 1, 'rows': []}}}
    finally:
        k.close()


@pytest.mark.parametrize('action', ['list.add', 'remind.set'])
def test_action_cannot_name_forgotten_entity_in_new_words(tmp_path, action):
    k = boot(tmp_path / 'kernel.db')
    try:
        store = (Lists() if action == 'list.add' else Reminders(lambda: 0)).register(k)
        say(k, f'{NAME} sent a parcel')
        k.spine.forget(PERSON, actor=PERSON, entity=NAME)
        fresh = say(k, 'ordinary copper lantern')
        text = f'bring a lantern to {NAME}'
        args = {'item': text} if action == 'list.add' else {'note': text, 'in_minutes': 2}
        # Isolate the name shield: these new words have no content-hash match.
        assert not k.spine._shielded(args)
        with pytest.raises(Refused, match='forgotten_action_arguments'):
            k.actions.act('brain', action, PERSON, args | {'asked': fresh.seq}, caused_by=(fresh.seq,))
        assert not store.rows
    finally:
        k.close()


def test_control_disabling_argument_check_restores_forgotten_text(tmp_path, monkeypatch):
    monkeypatch.setattr(Dispatcher, '_check_arguments', lambda *_: None)
    monkeypatch.setattr(Spine, '_payload_refusal', lambda *_: False)   # the kernel-write guard is the second line
    # The exact positive regression must fail when only the new guard is removed.
    with pytest.raises(pytest.fail.Exception, match='DID NOT RAISE'):
        test_action_cannot_restore_forgotten_arguments(tmp_path, 'list.add', 'seqs')


@pytest.mark.parametrize('part', ['args', 'before'])
def test_undo_checks_saved_arguments_and_snapshot_even_without_scrub(tmp_path, monkeypatch, part):
    k = boot(tmp_path / 'kernel.db')
    try:
        store = Lists().register(k)
        first = say(k, 'ordinary copper lantern')
        r = k.actions.act('brain', 'list.add', PERSON,
                          {'item': 'silver acorn', 'asked': first.seq}, caused_by=(first.seq,))
        # Simulate a legacy/stale saved copy not covered by row provenance.
        p = k.spine._get(r.seq).payload
        if part == 'args':
            p['undo']['args']['item'] = NAME
        else:
            p['undo']['before']['rows'] = [{'item': NAME, 'asked': first.seq}]
        k.spine._db.execute('UPDATE events SET payload = ? WHERE seq = ?', (json.dumps(p), r.seq))
        # An entity with no matching source rows still installs the entity shield.
        monkeypatch.setattr(k.spine, '_matching', lambda *_: set())
        k.spine.forget(PERSON, actor=PERSON, entity=NAME)
        count = k.spine._db.execute('SELECT COUNT(*) FROM intents').fetchone()[0]
        with pytest.raises(Refused, match='forgotten_action_arguments'):
            k.actions.undo(PERSON, actor=PERSON)
        assert store.rows[PERSON][0]['item'] == 'silver acorn'
        assert k.spine._db.execute('SELECT COUNT(*) FROM intents').fetchone()[0] == count
    finally:
        k.close()


def test_kernel_write_guard_still_refuses_when_the_argument_check_is_gone(tmp_path, monkeypatch):
    """Second line of defence: with only the dispatcher's check removed, the record itself refuses the write."""
    monkeypatch.setattr(Dispatcher, '_check_arguments', lambda *_: None)
    with pytest.raises(AssertionError, match='forgotten_kernel_content'):   # refused, by the other guard
        test_action_cannot_restore_forgotten_arguments(tmp_path, 'list.add', 'seqs')
