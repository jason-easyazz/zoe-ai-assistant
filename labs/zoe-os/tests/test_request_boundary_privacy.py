"""Untrusted request metadata must never become durable content; synthetic only."""
import json
from types import SimpleNamespace

import pytest

from test_forget_receipts_notices import PERSON, boot, disk
from zoe_os.brain.drivers import Alarms, Keeper, Lists, Reminders
from zoe_os.capability import ConsentError
from zoe_os.events import Event, Kind
from zoe_os.server import KernelServer
from zoe_os.spine import Refused

SECRET = 'violet kestrel envelope'


def answer(k, op, args, name='brain'):
    # Exercise the real socket dispatch without binding a socket or starting threads.
    server = object.__new__(KernelServer)
    server.kernel, server._surfaces = k, {'voice'}
    conn = SimpleNamespace(name=name, subs={}, next_sub=1)
    return server._answer(conn, {'v': 1, 'id': 1, 'op': op, 'args': args})


def say(k, text):
    return k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': text}))


@pytest.mark.parametrize('scope', ['seqs', 'person'])
@pytest.mark.parametrize('action', ['list.add', 'remind.set', 'alarm.set'])
def test_socket_act_binds_asked_and_forget_reaches_rows_and_snapshots(tmp_path, scope, action):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        stores = [Lists().register(k), Reminders(lambda: 0).register(k), Alarms().register(k)]
        k.install(Keeper(*stores))
        cause = say(k, SECRET)
        values = {'list.add': {'item': SECRET},
                  'remind.set': {'note': SECRET, 'in_minutes': 2},
                  'alarm.set': {'time': '06:00'}}[action]
        args = values | {'asked': 999999}
        reply = answer(k, 'act', {'action': action, 'on_behalf_of': PERSON,
                                 'args': args, 'caused_by': [cause.seq]})
        assert reply['ok'] and reply['result']['verified']
        receipt = k.spine._get(reply['result']['seq'])
        assert reply['result']['asked'] == receipt.payload['asked'] == cause.seq
        assert receipt.payload['undo']['args']['asked'] == cause.seq
        assert args['asked'] == 999999  # the dispatcher owns a copy
        later = say(k, 'copper fern parcel')
        kept = {'list.add': {'item': 'copper fern parcel'},
                'remind.set': {'note': 'copper fern parcel', 'in_minutes': 3},
                'alarm.set': {'time': '07:00'}}[action]
        assert answer(k, 'act', {'action': action, 'on_behalf_of': PERSON,
                                'args': kept | {'asked': 999999}, 'caused_by': [later.seq]})['ok']
        k.spine.forget(PERSON, actor=PERSON, **({'seqs': [cause.seq]} if scope == 'seqs' else {}))
        k.pump()
        for store in stores:
            rows = [store.at[PERSON]] if isinstance(store, Alarms) and PERSON in store.at else \
                store.rows.get(PERSON, []) if not isinstance(store, Alarms) else []
            assert all(r['asked'] != cause.seq and r['asked'] != 999999 for r in rows)
            if scope == 'person':
                assert not rows
        live = k.spine._fetch(0, None, None, 100)
        assert all(r.get('asked') != cause.seq for ev in live
                   for r in ev.payload.get('undo', {}).get('before', {}).get('rows', []))
        assert all(ev.payload.get('undo', {}).get('before', {}).get('alarm', {}).get('asked') != cause.seq
                   for ev in live if ev.payload.get('undo', {}).get('before', {}).get('alarm'))
        assert SECRET.encode() not in disk(path)
        if scope == 'seqs':
            k.actions.undo(PERSON, actor=PERSON)
            for store in stores:
                assert not (store.at if isinstance(store, Alarms) else store.rows).get(PERSON)
    finally:
        k.close()


def test_multiple_request_causes_are_refused_before_driver(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    try:
        store = Lists().register(k)
        a, b = say(k, 'copper fern'), say(k, 'silver acorn')
        with pytest.raises(Refused, match='ambiguous_request_provenance'):
            k.actions.act('brain', 'list.add', PERSON, {'item': 'parcel', 'asked': a.seq},
                          caused_by=(a.seq, b.seq))
        assert not store.rows
    finally:
        k.close()


@pytest.mark.parametrize('channel', ['action', 'skill', 'kind', 'key', 'actor', 'grant_skill', 'op'])
def test_forgotten_request_metadata_never_reaches_disk_or_errors(tmp_path, channel):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        Lists().register(k)
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        fresh = say(k, 'copper fern parcel')
        op, name = 'act', 'brain'
        args = {'action': 'list.add', 'on_behalf_of': PERSON,
                'args': {'item': 'silver acorn', 'asked': fresh.seq}, 'caused_by': [fresh.seq]}
        if channel == 'action':
            args['action'] = SECRET
        elif channel == 'skill':
            name = SECRET
        elif channel == 'kind':
            op, args = 'publish', {'kind': SECRET, 'payload': {}}
        elif channel == 'key':
            args['args'][SECRET] = 'unused'
        elif channel == 'actor':
            op, name, args = 'forget', 'voice', {'subject': PERSON, 'actor': SECRET}
        elif channel == 'grant_skill':
            op, name, args = 'grant', 'voice', {'person': PERSON, 'skill': SECRET}
        else:
            op = SECRET
        reply = answer(k, op, args, name)
        assert not reply['ok']
        assert SECRET not in json.dumps(reply)
        diagnostic = [e for e in k.spine._fetch(0, None, None, 100)
                      if e.kind in {Kind.REFUSAL, Kind.AUDIT_FINDING}]
        assert diagnostic and all(SECRET not in json.dumps(e.payload) for e in diagnostic)
        assert all(e.payload.get('reason') and e.payload.get('attempted') for e in diagnostic)
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


def test_failed_handshake_never_records_claimed_skill(tmp_path, monkeypatch):
    from zoe_os import wire

    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        # Syntactically valid but unverified: the old handshake stored it verbatim.
        plant = 'violet-kestrel-envelope'
        old = say(k, plant)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        server = object.__new__(KernelServer)
        server.kernel, server.frame_deadline = k, 1
        server._nonce = lambda: b'nonce'
        server._read = lambda *a, **kw: {'v': 1, 'skill': plant, 'proof': '00'}
        server._verify = lambda *a: False
        replies = []
        monkeypatch.setattr(wire, 'write_frame', lambda sock, obj, **kw: replies.append(obj))
        assert not server._handshake(SimpleNamespace(sock=None, name='unverified-peer'))
        assert plant not in json.dumps(replies)
        refusal = k.spine._get(k.spine.last_seq)
        assert refusal.payload == {'who': 'unverified-peer', 'attempted': 'connect',
                                   'reason': 'bad credential'}
        assert plant.encode() not in disk(path)
    finally:
        k.close()


def test_claim_audit_uses_sequence_not_caller_action_and_forgets_with_claim(tmp_path):
    k = boot(tmp_path / 'kernel.db')
    try:
        source = say(k, SECRET)
        # A legacy invalid claim is the auditor's input (public publish rejects it).
        claim = k.spine._kernel_append(Event(Kind.CLAIM, 'brain', PERSON,
            payload={'action': SECRET}, caused_by=(source.seq,)))
        k.auditor.scan()
        finding = k.spine._get(k.spine.last_seq)
        assert finding.kind is Kind.AUDIT_FINDING
        assert finding.payload == {'finding': 'claim_without_receipt', 'who': 'brain',
                                   'claim_seq': claim.seq}
        assert finding.subject == PERSON and finding.caused_by == (claim.seq,)
        k.spine.forget(PERSON, actor=PERSON, seqs=[source.seq])
        assert k.spine._get(finding.seq) is None
    finally:
        k.close()


def test_schema_free_argument_key_and_kernel_readback_meet_shields(tmp_path):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        fresh = say(k, 'copper fern parcel')
        seen = []
        k.actions.register('list.add', lambda p, a: seen.append(a),
                           lambda *a: {SECRET: True}, needs_accept=False)
        with pytest.raises(Refused, match='forgotten_action_arguments'):
            k.actions.act('brain', 'list.add', PERSON, {SECRET: 1}, caused_by=(fresh.seq,))
        assert not seen
        with pytest.raises(Refused, match='forgotten_kernel_content'):
            k.actions.act('brain', 'list.add', PERSON, {}, caused_by=(fresh.seq,))
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


def test_attention_uses_live_offer_not_caller_subject(tmp_path):
    from dataclasses import replace

    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        k.grants.install({'skill': 'offers', 'origin': True, 'emits': ['offer']})
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        live = k.spine.publish(Event(Kind.OFFER, 'offers', PERSON,
                                    payload={'offer_id': 'o1', 'text': 'copper fern'}))
        k.attention.route(replace(live, subject=SECRET), hour=12, day=1)
        assert k.spine._get(k.spine.last_seq).subject == PERSON
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


def test_adult_grant_discards_unvalidated_guardian(tmp_path):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        grant = k.grants.grant(PERSON, 'brain', guardian=SECRET)
        assert grant.guardian is None
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


@pytest.mark.parametrize('asked', [None, 'caller-controlled', 999999])
def test_kernel_fills_or_overwrites_declared_provenance(tmp_path, asked):
    k = boot(tmp_path / 'kernel.db')
    try:
        store = Lists().register(k)
        cause = say(k, 'copper fern parcel')
        args = {'item': 'silver acorn'} | ({} if asked is None else {'asked': asked})
        receipt = k.actions.act('brain', 'list.add', PERSON, args, caused_by=(cause.seq,))
        assert receipt.asked == cause.seq == store.rows[PERSON][0]['asked']
    finally:
        k.close()


def test_bridge_shape_never_echoes_unknown_argument_key():
    from zoe_os.brain.bridge import shape

    assert SECRET not in json.dumps(shape({'item': {'type': 'string'}}, {SECRET: 'unused'}))


@pytest.mark.parametrize('op', ['publish', 'subscribe', 'poll', 'forget', 'grant', 'revoke',
                              'act', 'undo', 'describe', 'explain'])
@pytest.mark.parametrize('error', [Refused(SECRET), ConsentError(SECRET), ValueError(SECRET),
                                 RuntimeError(SECRET), type(SECRET, (Exception,), {})(SECRET)])
def test_wire_exception_paths_do_not_echo_input(tmp_path, op, error):
    k = boot(tmp_path / 'kernel.db')
    try:
        server = object.__new__(KernelServer)
        server.kernel = k
        server._validate = lambda _: ''  # exercise each operation's exception handler
        def fail(*args):
            raise error
        setattr(server, '_op_' + op, fail)
        reply = server._answer(SimpleNamespace(name='brain'), {'v': 1, 'op': op, 'args': {}})
        assert not reply['ok'] and SECRET not in json.dumps(reply)
    finally:
        k.close()


@pytest.mark.parametrize('scope', ['seqs', 'person'])
def test_socket_reminder_skill_provenance_and_opaque_snapshot_forget(tmp_path, scope):
    from reminder_store import ReminderStore
    from zoe_os.skills.reminders import Reminders as ReminderSkill

    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        store = ReminderStore().register(k)
        k.install(ReminderSkill())
        k.grants.grant(PERSON, 'reminders')
        first = say(k, SECRET)
        args = {'id': f'r{first.seq}', 'at': 100, 'note': SECRET, 'asked': 999999}
        reply = answer(k, 'act', {'action': 'reminder.create', 'on_behalf_of': PERSON,
                                 'args': args, 'caused_by': [first.seq]}, name='reminders')
        assert reply['ok'] and reply['result']['asked'] == first.seq
        later = say(k, 'copper fern parcel')
        # Same row: the second receipt snapshots the first request's content.
        assert answer(k, 'act', {'action': 'reminder.create', 'on_behalf_of': PERSON,
                                'args': args | {'note': 'copper fern parcel'},
                                'caused_by': [later.seq]}, name='reminders')['ok']
        k.spine.forget(PERSON, actor=PERSON, **({'seqs': [first.seq]} if scope == 'seqs' else {}))
        k.pump()
        assert not store.rows
        assert SECRET.encode() not in disk(path)
        if scope == 'seqs':
            with pytest.raises(Refused):
                k.actions.undo(PERSON, actor=PERSON)
    finally:
        k.close()


@pytest.mark.parametrize('field', ['subject', 'mentions'])
def test_wire_publish_person_fields_are_registered_ids(tmp_path, field):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        args = {'kind': 'utterance', 'subject': PERSON, 'payload': {'text': 'copper fern'}}
        args[field] = [SECRET] if field == 'mentions' else SECRET
        assert not answer(k, 'publish', args, name='voice')['ok']
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


def test_driver_exception_name_is_a_fixed_receipt_code(tmp_path):
    path = tmp_path / 'kernel.db'
    k = boot(path)
    try:
        old = say(k, SECRET)
        k.spine.forget(PERSON, actor=PERSON, seqs=[old.seq])
        fresh = say(k, 'copper fern parcel')
        def fail(*args):
            raise type(SECRET, (Exception,), {})(SECRET)
        k.actions.register('list.add', fail, lambda *args: None, needs_accept=False)
        receipt = k.actions.act('brain', 'list.add', PERSON, {}, caused_by=(fresh.seq,))
        assert not receipt.verified
        assert k.spine._get(receipt.seq).payload['error'] == 'driver_failed'
        assert SECRET.encode() not in disk(path)
    finally:
        k.close()


@pytest.mark.parametrize('raw', [{'skill': 'probe', SECRET: 1},
                               {'skill': 'probe', 'reads': {SECRET: 'private'}}])
def test_manifest_errors_never_echo_keys_or_kinds(raw):
    from zoe_os.capability import Manifest

    with pytest.raises(ConsentError) as exc:
        Manifest.from_dict(raw)
    assert SECRET not in str(exc.value)
