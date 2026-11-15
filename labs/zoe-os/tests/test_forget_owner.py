"""Socket-free owner decisions, with controls for the former behaviour."""
import pytest

from spine_helpers import AllowList
from test_history_cost import vm_budget
from zoe_os.capability import Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.kernel import Kernel
from zoe_os.brain.bridge import BrainBridge, Turn
from zoe_os.spine import Refused, Spine
from zoe_os.twin.unusual import Sharing

A, B = 'member-a', 'member-b'
TEXT = 'synthetic cobalt token'
NAME = 'Synthetic Quill'


@pytest.fixture
def spine(tmp_path):
    auth = AllowList(origins={'voice'}, skills={'skill'},
                     readers={'skill': {'*'}, A: {A}, B: {B}})
    s = Spine(str(tmp_path / 'owner.db'), auth)
    yield s
    s.close()


def say(s, person, text):
    return s.publish(Event(Kind.UTTERANCE, 'voice', person, payload={'text': text}))


def proposal(person, cause, text=TEXT, **kwargs):
    return Event(Kind.OFFER, 'skill', person, payload={'offer_id': 'o', 'text': text},
                 caused_by=(cause,), **kwargs)


def nothing_names_person(db, person):
    """Sweep every column of every table, including bookkeeping and tombstones."""
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [r[1] for r in db.execute(f'PRAGMA table_info({quoted})')]
        for row in db.execute(f'SELECT * FROM {quoted}'):
            for column, value in zip(columns, row):
                raw = value if isinstance(value, bytes) else str(value).encode()
                assert person.encode() not in raw, (table, column)


def prepare(s):
    first = say(s, A, 'synthetic first hand')
    s.publish(proposal(A, first.seq))  # TEXT exists only in a derived event
    live = say(s, B, 'synthetic live cause')
    s.forget(A, actor=A, seqs=[first.seq])
    return live.seq


def assert_b_can_receive(s, cause):
    event = s.publish(proposal(B, cause))
    assert event.seq in [e.seq for e in s.replay(B)]
    assert event.seq not in [e.seq for e in s.replay(A)]


def test_b_can_publish_and_receive_identical_derived_text(spine):
    cause = prepare(spine)
    assert_b_can_receive(spine, cause)
    assert say(spine, B, TEXT).seq


def test_negative_control_old_global_shield_fails_b_assertion(spine):
    cause = prepare(spine)
    # The former _tombstone put every derived text fingerprint in this
    # household-wide set; the same B assertion now fails by refusal.
    spine._shield.add(spine._fp(TEXT))
    with pytest.raises(Refused, match='forgotten content'):
        assert_b_can_receive(spine, cause)


@pytest.mark.parametrize('route', ['subject', 'mention', 'payload-name', 'payload-key', 'tuple', 'household',
                                   'kernel-visibility', 'origin', 'kernel-service', 'batch'])
def test_a_cannot_receive_forgotten_derived_text_via_any_publish_route(spine, route):
    cause = prepare(spine)
    ev = proposal(A if route in {'subject', 'tuple', 'batch', 'kernel-service'} else B, cause)
    if route == 'mention':
        ev = proposal(B, cause, mentions=(A,))
    elif route == 'payload-name':
        ev = Event(Kind.OFFER, 'skill', B, payload={
            'offer_id': 'o', 'text': TEXT, 'nested': ({'who': A},)}, caused_by=(cause,))
    elif route == 'tuple':
        ev = Event(Kind.OFFER, 'skill', A, payload={
            'offer_id': 'o', 'text': 'synthetic wrapper', 'nested': ({'value': (TEXT,)},)},
            caused_by=(cause,))
    elif route == 'payload-key':
        ev = Event(Kind.OFFER, 'skill', B, payload={'offer_id': 'o', 'text': TEXT, A: True},
                   caused_by=(cause,))
    elif route in {'household', 'kernel-visibility'}:
        ev = proposal(B, cause, visibility=Visibility.HOUSEHOLD if route == 'household'
                      else Visibility.KERNEL)
    elif route == 'origin':
        ev = Event(Kind.UTTERANCE, 'voice', A, payload={'text': TEXT})
    with pytest.raises(Refused, match='forgotten content'):
        if route == 'kernel-service':
            spine._kernel_append(ev)
        elif route == 'batch':
            spine.publish_many([proposal(B, cause, 'synthetic permitted'), ev])
        else:
            spine.publish(ev)


def test_entity_shield_is_scoped_but_household_and_names_of_a_are_checked(spine):
    say(spine, A, NAME + ' called')
    cause = say(spine, B, 'synthetic live').seq
    spine.forget(A, actor=A, entity=NAME)
    assert spine.publish(proposal(B, cause, NAME)).seq
    for subject, mentions, visibility in [(A, (), Visibility.PRIVATE),
                                           (B, (A,), Visibility.PRIVATE),
                                           (B, (), Visibility.HOUSEHOLD)]:
        with pytest.raises(Refused, match='forgotten entity'):
            spine.publish(proposal(subject, cause, NAME, mentions=mentions, visibility=visibility))


def test_refusal_bookkeeping_cannot_republish_forgotten_text(spine):
    prepare(spine)
    with pytest.raises(Refused):
        spine._refuse(A, 'synthetic attempt', TEXT)
    assert spine._get(spine.last_seq).payload == {}


def test_scoped_shields_survive_restart(spine, tmp_path):
    cause = prepare(spine)
    again = Spine(str(tmp_path / 'owner.db'), spine.authorizer)
    try:
        assert_b_can_receive(again, cause)
        with pytest.raises(Refused, match='forgotten content'):
            again.publish(proposal(A, cause))
    finally:
        again.close()


def test_legacy_global_rows_stay_global_on_upgrade(tmp_path):
    path = str(tmp_path / 'legacy.db')
    s = Spine(path, AllowList(origins={'voice'}))
    s._db.execute('INSERT INTO shield VALUES (?)', (s._fp(TEXT),))
    s._db.execute('INSERT INTO entity_shield VALUES (?,?)', (A, s._efp(A, 'synthetic quill')))
    s.close()
    again = Spine(path, AllowList(origins={'voice'}))
    try:
        for person in (A, B):
            with pytest.raises(Refused, match='forgotten content'):
                say(again, person, TEXT)
            with pytest.raises(Refused, match='forgotten entity'):
                say(again, person, NAME)
        again.forget(A, actor=A)
        nothing_names_person(again._db, A)
        with pytest.raises(Refused, match='forgotten entity'):
            say(again, B, NAME)
    finally:
        again.close()


@pytest.fixture
def kernel(tmp_path):
    k = Kernel(str(tmp_path / 'kernel.db'), [Person(A), Person(B)])
    k.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
    k.grants.install({'skill': 'skill', 'reads': {'utterance': 'private'},
                      'acts': ['synthetic.act']})
    k.actions.register('synthetic.act', lambda p, a: None, lambda p, a, r: {'ok': True},
                       needs_accept=False)
    yield k
    k.close()


@pytest.mark.parametrize('person', [A, B])
def test_whole_forget_sweeps_all_tables_and_erased_consent_refuses_action(kernel, person):
    k = kernel
    grant = k.grants.grant(person, 'skill')
    k.spine._db.execute('INSERT INTO cursors VALUES (?,0)', (person,))
    cause = say(k.spine, person, 'synthetic allowed cause')
    assert k.actions.act('skill', 'synthetic.act', person, {}, caused_by=(cause.seq,)).verified
    with pytest.raises(Refused):
        k.spine._refuse(person, 'synthetic attempt', 'synthetic reason')
    with pytest.raises(AssertionError):
        nothing_names_person(k.spine._db, person)  # planted positive control
    k.spine.forget(person, actor=person)
    nothing_names_person(k.spine._db, person)
    assert k.explainer._grant(grant.id, person) is None
    assert k.grants.of('skill', person) is None and k.grants.all_for('skill') == []
    fresh = say(k.spine, person, 'synthetic new cause')
    with pytest.raises(Refused, match='no grant from that person'):
        k.actions.act('skill', 'synthetic.act', person, {}, caused_by=(fresh.seq,))


def test_negative_control_unindexed_grant_and_refusal_survive_old_forget(kernel, monkeypatch):
    k = kernel
    monkeypatch.setattr(k.spine, '_index_record', lambda *args: None)
    k.grants.grant(A, 'skill')
    with pytest.raises(Refused):
        k.spine._refuse(A, 'synthetic attempt', 'synthetic reason')
    k.spine.forget(A, actor=A)
    with pytest.raises(AssertionError, match='grant_change|events'):
        nothing_names_person(k.spine._db, A)
    live = [e for e in k.spine._newest(frozenset({Kind.GRANT_CHANGE, Kind.REFUSAL}), None)]
    assert {e.kind for e in live} == {Kind.GRANT_CHANGE, Kind.REFUSAL}
    assert k.enforcer.may_act('skill', 'synthetic.act', A).allowed


def test_event_forget_only_erases_selected_bookkeeping_and_unrelated_grants_stay(kernel):
    k = kernel
    ga = k.grants.grant(A, 'skill')
    gb = k.grants.grant(B, 'skill')
    text = say(k.spine, A, 'synthetic isolated')
    k.spine.forget(A, actor=A, seqs=[text.seq])
    assert k.grants.of('skill', A) is ga
    k.spine.forget(A, actor=A, seqs=[ga.record_seq])
    assert k.grants.of('skill', A) is None and k.grants.of('skill', B) is gb


def test_event_forget_can_select_a_refusal_without_erasing_grants(kernel):
    k = kernel
    grant = k.grants.grant(A, 'skill')
    with pytest.raises(Refused):
        k.spine._refuse(A, 'synthetic attempt', 'synthetic reason')
    refusal_seq = k.spine.last_seq
    k.spine.forget(A, actor=A, seqs=[refusal_seq])
    assert k.spine._get(refusal_seq) is None and k.grants.of('skill', A) is grant


def test_cached_brain_tool_cannot_use_an_erased_grant_without_a_socket(kernel):
    k = kernel
    grant = k.grants.grant(A, 'skill')
    cause = say(k.spine, A, 'synthetic cause').seq

    class LocalClient:
        def act(self, action, person, args, *, caused_by):
            return k.actions.act('skill', action, person, args, caused_by=tuple(caused_by))

    bridge = BrainBridge('unused', b'synthetic', skill='skill')
    bridge._client = LocalClient()
    bridge._catalog = {'synthetic_act': {'action': 'synthetic.act', 'doc': 'Synthetic action',
                                        'params': {}, 'needs_accept': False}}
    assert bridge.propose(Turn(A, cause), 'synthetic_act', {}).status == 'done'
    grant_seq = next(e.seq for e in k.spine._newest(frozenset({Kind.GRANT_CHANGE}), None)
                     if e.payload.get('grant_id') == grant.id)
    k.spine.forget(A, actor=A, seqs=[grant_seq])
    result = bridge.propose(Turn(A, cause), 'synthetic_act', {})
    assert result.status == 'refused' and result.reason == 'no grant from that person'


def test_whole_forget_rollback_preserves_consent(kernel, monkeypatch):
    k = kernel
    grant = k.grants.grant(A, 'skill')

    def fail(dead):
        raise RuntimeError('synthetic rollback')

    monkeypatch.setattr(k.spine, '_scrub_intents', fail)
    with pytest.raises(RuntimeError, match='synthetic rollback'):
        k.spine.forget(A, actor=A)
    assert k.grants.of('skill', A) is grant
    assert k.spine._get(grant.record_seq) is not None


def test_forget_reaches_sharing_grants_as_owner_and_recipient(kernel):
    k = kernel
    sharing = Sharing(k.spine, k.grants.people)
    sharing.share(A, B, by=A)
    sharing.share(B, A, by=B)
    assert sharing.shared_with(A) == {B} and sharing.shared_with(B) == {A}
    k.spine.forget(A, actor=A)
    nothing_names_person(k.spine._db, A)
    assert sharing.shared_with(A) == sharing.shared_with(B) == frozenset()


def test_upgrade_backfills_old_grant_and_refusal_references(spine, tmp_path):
    for kind, payload in [('grant_change', '{"person":"member-a","change":"granted"}'),
                          ('refusal', '{"who":"member-a"}')]:
        spine._db.execute("INSERT INTO events(ts,kind,source,visibility,payload) VALUES(0,?,'kernel','kernel',?)",
                           (kind, payload))
    spine._db.execute("DELETE FROM meta WHERE key='record_people_v1'")
    again = Spine(str(tmp_path / 'owner.db'), spine.authorizer)
    try:
        again.forget(A, actor=A)
        nothing_names_person(again._db, A)
    finally:
        again.close()


@pytest.mark.parametrize('size', [200, 5000])
def test_shield_lookup_budget_is_independent_of_shield_history(spine, size):
    cause = say(spine, B, 'synthetic live').seq
    spine._db.executemany('INSERT INTO scoped_shield VALUES (?,?)',
                          [(spine._owner(A), spine._fp(f'synthetic text {i}')) for i in range(size)])
    spine._db.executemany('INSERT INTO scoped_entities VALUES (?,?)',
                          [(spine._owner(A), spine._fp(f'synthetic entity {i}')) for i in range(size)])
    with vm_budget(spine._db):
        assert spine.publish(proposal(B, cause, 'synthetic text 1')).seq
    with vm_budget(spine._db):
        with pytest.raises(Refused, match='forgotten content'):
            spine.publish(proposal(A, cause, 'synthetic text 1'))


@pytest.mark.parametrize('driver', ['lists', 'reminders', 'alarms'])
def test_registered_brain_purge_is_synchronous_scoped_and_cross_owner(kernel, driver):
    from zoe_os.brain.drivers import Lists, Reminders, Alarms

    k = kernel
    store = {'lists': Lists, 'reminders': lambda: Reminders(lambda: 0),
             'alarms': Alarms}[driver]().register(k)
    grant = k.grants.grant(A, 'skill')
    hit = say(k.spine, A, 'synthetic selected request')
    kept = say(k.spine, A, 'synthetic surviving request')
    if driver == 'alarms':
        store.at = {A: {'asked': kept.seq}, B: {'asked': hit.seq}}
    else:
        store.rows = {A: [{'asked': kept.seq}], B: [{'asked': hit.seq}]}
    k.spine.forget(A, actor=A, seqs=[])
    assert B in (store.at if driver == 'alarms' else store.rows)
    k.spine.forget(A, actor=A, seqs=[hit.seq])
    rows = store.at if driver == 'alarms' else store.rows
    assert set(rows) == {A}  # before pump, across owners, without widening the scope
    assert k.grants.of('skill', A) is grant


def test_store_readback_failure_preserves_log_consent_and_notices(kernel):
    k = kernel
    grant = k.grants.grant(A, 'skill')
    hit = say(k.spine, A, 'synthetic selected request')
    k.register_store_purge('synthetic-failing-store', lambda *_: None, lambda *_: False)
    with pytest.raises(Refused, match='external store purge unverified'):
        k.spine.forget(A, actor=A)
    assert k.spine._get(hit.seq) is not None
    assert k.grants.of('skill', A) is grant
    assert not list(k.spine._newest(frozenset({Kind.FORGET}), None))
    assert not k.spine._db.execute('SELECT * FROM scoped_shield').fetchall()
