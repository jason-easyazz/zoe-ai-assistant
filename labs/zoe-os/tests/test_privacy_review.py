"""Socket-free review regressions; all planted words are invented."""
import io
import json
from types import SimpleNamespace

import pytest

from spine_helpers import AllowList
from zoe_os.brain import serve
from zoe_os.brain.bridge import Outcome
from zoe_os.brain.drivers import Alarms, Keeper, Lists, Reminders
from zoe_os.events import Event, Kind
from zoe_os.replay import Recording, build_report, diff
from zoe_os.spine import Spine

PLANT = 'quartz-moth-velvet-8041'


def test_report_paths_never_display_payload_fields(capsys, monkeypatch):
    from zoe_os.twin.rehearse import _privacy
    import real_turn

    fields = ('text', 'arguments', 'say', 'reason', 'who', 'attempted', 'skill',
              'action', 'outcome', 'finding', 'channel', 'response', 'offer_id')
    payload = dict.fromkeys(fields, PLANT)
    kinds = (Kind.OFFER, Kind.ACTION_RECEIPT, Kind.REFUSAL, Kind.CLAIM,
             Kind.AUDIT_FINDING, Kind.ATTENTION, Kind.OFFER_RESPONSE)
    facts = [(2, 0, Event(kind, 'probe', 'member-b', payload=payload,
                         seq=n, caused_by=(1,))) for n, kind in enumerate(kinds, 1)]
    rec = Recording([], [])
    skill = SimpleNamespace(manifest={'skill': PLANT})
    report = build_report(rec, [], facts, '')
    print(report.text)
    print(diff(build_report(rec, [], [], ''), report))
    import importlib
    replay_module = importlib.import_module('zoe_os.replay')
    monkeypatch.setattr(replay_module.Recording, 'load', lambda _: rec)
    monkeypatch.setattr(replay_module, '_load_skill', lambda _: None)
    monkeypatch.setattr(replay_module, 'replay', lambda *a, **kw: report)
    assert replay_module.main(['unused', 'unused']) == 0
    assert replay_module.main(['unused', 'unused', '--against', 'unused']) == 0
    from zoe_os.twin.rehearse import rehearse
    print(rehearse([], [], recorded=[Recording([], [], meta={
        'day': PLANT, 'weekday': PLANT, 'awkward': PLANT})]).text)
    kernel = SimpleNamespace(enforcer=SimpleNamespace(is_origin=lambda _: False),
                             spine=SimpleNamespace(_get=lambda _: None))
    print(_privacy(kernel, facts, [skill]))
    frames = ['__TOOL__:' + json.dumps({'phase': 'args', 'name': PLANT,
                                     'args': {PLANT: PLANT}, 'result': PLANT}), PLANT]
    print(real_turn.reduce_frames(frames))
    print(real_turn.diagnose(frames, [{'status': 'invalid', 'reason': PLANT,
                                     'shape': {PLANT: PLANT}}], None))
    real_turn.report([dict.fromkeys(('n', 'member', 'expect', 'right_tool', 'args_valid',
                                    'model_ms', 'hop_ms', 'error', 'diag'), PLANT)
                      | {'called': [PLANT], 'kernel': [PLANT]}], {PLANT: PLANT},
                     {PLANT: PLANT}, {PLANT: PLANT}, {PLANT: PLANT},
                     SimpleNamespace(dry_run=True), PLANT)
    # The bridge's spoken reply to the member who asked is authorised display, not a report path: it is not checked here.
    captured = capsys.readouterr()
    assert PLANT not in captured.out + captured.err


@pytest.fixture
def http_face(monkeypatch):
    """Capture the actual handler without binding or starting any socket/thread."""
    class HTTP:
        server_address = ('127.0.0.1', 1)

        def __init__(self, address, handler):
            self.handler = handler

        def serve_forever(self):
            pass

    monkeypatch.setattr(serve, 'ThreadingHTTPServer', HTTP)
    bridge = SimpleNamespace(tools=lambda: [], propose=lambda *a: Outcome(
        'done', PLANT, 17, PLANT, {PLANT: [PLANT]}))
    server = serve.BridgeServer(bridge, 'synthetic-token')
    turn_id = server.open_turn('member-b', 11)

    def request(method, path, token=None, body=None):
        h = object.__new__(server.httpd.handler)
        h.path = path
        data = json.dumps(body or {}).encode()
        h.headers = {'Content-Length': str(len(data)), 'X-Turn': turn_id}
        if token is not None:
            h.headers['X-Runner'] = token
        h.rfile, h.wfile = io.BytesIO(data), io.BytesIO()
        reply = []
        h._send = lambda code, obj: reply.append((code, obj))
        getattr(h, 'do_' + method)()
        return reply[0]

    return server, request


@pytest.mark.parametrize('method,path', [('GET', '/log'), ('GET', '/tools'),
                                       ('POST', '/turn'), ('POST', '/propose')])
@pytest.mark.parametrize('token', [None, '', 'wrong-token'])
def test_every_bridge_endpoint_requires_token(http_face, method, path, token):
    _, request = http_face
    assert request(method, path, token)[0] == 403


def test_authenticated_log_keeps_only_ids_codes_counts(http_face):
    server, request = http_face
    assert request('POST', '/propose', 'synthetic-token',
                   {'tool': PLANT, 'arguments': {PLANT: PLANT}})[0] == 200
    code, log = request('GET', '/log', 'synthetic-token')
    assert code == 200 and log and log[0]['receipt'] == 17
    assert PLANT not in json.dumps(log)
    assert not {'arguments', 'say', 'reason', 'shape'} & server.log[0].keys()


def test_forwarder_token_cannot_set_identity_or_read_log(http_face):
    server, request = http_face
    assert request('GET', '/tools', server.app_token)[0] == 200
    assert request('POST', '/propose', server.app_token)[0] == 200
    assert request('GET', '/log', server.app_token)[0] == 403
    assert request('POST', '/turn', server.app_token)[0] == 403


@pytest.mark.parametrize('scope', ['person', 'seqs', 'entity', 'topic'])
@pytest.mark.parametrize('store', [Lists, lambda: Reminders(lambda: 0), Alarms],
                         ids=['lists', 'reminders', 'alarms'])
def test_erased_request_cleanup_crosses_every_owner(store, scope):
    store = store()
    if isinstance(store, Alarms):
        store.at = {'member-b': {'asked': 11}, 'member-c': {'asked': 22}}
    else:
        store.rows = {'member-b': [{'asked': 11}, {'asked': 22}],
                      'member-c': [{'asked': 22}]}
    notice = Event(Kind.FORGET, 'kernel', visibility='kernel', payload={
        'subject': 'member-a', 'scope': scope, 'seqs': [11], 'upto': 22})
    Keeper(store).on_event(notice, None)
    if isinstance(store, Alarms):
        assert store.at == {'member-c': {'asked': 22}}
    else:
        assert store.rows == {'member-b': [{'asked': 22}], 'member-c': [{'asked': 22}]}


def test_suspended_replay_refreshes_forgotten_prefetch(tmp_path):
    auth = AllowList(origins={'voice'}, readers={'reader': {'*'}})
    with_spine = Spine(str(tmp_path / 'events.db'), auth)
    try:
        first = with_spine.publish(Event(Kind.UTTERANCE, 'voice', 'member-a', payload={'text': PLANT}))
        second = with_spine.publish(Event(Kind.UTTERANCE, 'voice', 'member-a', payload={'text': PLANT}))
        kept = with_spine.publish(Event(Kind.UTTERANCE, 'voice', 'member-b', payload={'text': 'copper fern'}))
        pending = with_spine.replay('reader', batch=100)
        assert next(pending).seq == first.seq
        with_spine.forget('member-a', actor='member-a')
        rest = list(pending)
        assert second.seq not in [e.seq for e in rest]
        assert kept.seq in [e.seq for e in rest]
        assert PLANT not in str([e.payload for e in rest])
    finally:
        with_spine.close()


def test_suspended_replay_refreshes_scrubbed_snapshot(tmp_path):
    from test_forget_receipts_notices import boot, add, PERSON, SECRET, KEEP
    k = boot(tmp_path / 'snapshots.db')
    try:
        store = Lists().register(k)
        first, receipt = add(k, store, SECRET)
        _, surviving = add(k, store, KEEP)
        k.grants.install({'skill': 'reader', 'reads': {'action_receipt': 'private'}})
        k.grants.grant(PERSON, 'reader')
        pending = k.spine.replay('reader', kinds={Kind.ACTION_RECEIPT})
        assert next(pending).seq == receipt.seq
        k.spine.forget(PERSON, actor=PERSON, seqs=[first.seq])
        refreshed = next(e for e in pending if e.seq == surviving.seq)
        assert SECRET not in str(refreshed.payload)
        assert refreshed.payload == k.spine._get(surviving.seq).payload
    finally:
        k.close()


def test_skill_request_caches_purge_other_owners():
    from zoe_os.skills.leaving_catch import LeavingCatch
    from zoe_os.skills.calendar_headsup import CalendarHeadsUp
    from zoe_os.skills.reminders import Reminders as SkillReminders
    from zoe_os.twin.baseline import Twin

    leaving, calendar, reminders = LeavingCatch(), CalendarHeadsUp(), SkillReminders()
    twin = Twin()
    twin.days = {'member-b': {'2032-06-01': {PLANT: (True, 11), 'kept': (True, 22)}}}
    leaving._undone = {'member-b': {PLANT: 11, 'kept': 22}}
    calendar._open = {'headsup-11': ('member-b', PLANT), 'headsup-22': ('member-b', '07:00')}
    reminders._items = {'r11': {'person': 'member-b', 'asked': 11, 'receipt': 12},
                        'r22': {'person': 'member-b', 'asked': 22, 'receipt': 23}}
    calls = []
    ctx = SimpleNamespace(act=lambda *a, **kw: calls.append((a, kw)),
                          purge=lambda *a: calls.append((a, {})))
    notice = Event(Kind.FORGET, 'kernel', visibility='kernel', seq=30, payload={
        'subject': 'member-a', 'scope': 'seqs', 'seqs': [11, 12], 'upto': 23})
    for skill in (leaving, calendar, reminders, twin):
        skill.on_event(notice, ctx)
    assert leaving._undone == {'member-b': {'kept': 22}}
    assert calendar._open == {'headsup-22': ('member-b', '07:00')}
    assert set(reminders._items) == {'r22'}
    assert any(a[:2] == ('reminder.delete', {'id': 'r11'}) for a, _ in calls)
    assert twin.days == {'member-b': {'2032-06-01': {'kept': (True, 22)}}}


@pytest.mark.parametrize('store_type', ['lists', 'reminders', 'alarms', 'skill-reminders'])
def test_mentioned_member_forget_removes_effect_but_keeps_unrelated(tmp_path, store_type):
    from zoe_os.capability import Person
    from zoe_os.kernel import Kernel
    from zoe_os.brain.drivers import BRAIN
    from zoe_os.skills.reminders import Reminders as SkillReminders
    from reminder_store import ReminderStore

    k = Kernel(str(tmp_path / 'kernel.db'), [Person(p) for p in ('member-a', 'member-b', 'member-c')])
    try:
        k.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
        k.grants.install(BRAIN)
        k.grants.grant('member-b', 'brain')
        k.grants.grant('member-c', 'brain')
        if store_type == 'skill-reminders':
            store = ReminderStore().register(k)
            k.install(SkillReminders())
            k.grants.grant('member-b', 'reminders')
        else:
            store = {'lists': Lists(), 'reminders': Reminders(lambda: 0), 'alarms': Alarms()}[store_type].register(k)
            k.install(Keeper(store))
        hit = k.spine.publish(Event(Kind.UTTERANCE, 'voice', 'member-b', mentions=('member-a',),
            payload={'text': PLANT, 'intent': {'name': 'reminder.create', 'at': 100, 'note': PLANT}}))
        owner = 'member-c' if store_type == 'alarms' else 'member-b'
        kept = k.spine.publish(Event(Kind.UTTERANCE, 'voice', owner,
            payload={'text': 'copper fern', 'intent': {'name': 'reminder.create', 'at': 200, 'note': 'copper fern'}}))
        if store_type == 'skill-reminders':
            k.pump()
        else:
            action, args = {'lists': ('list.add', {'item': PLANT}),
                            'reminders': ('remind.set', {'note': PLANT, 'in_minutes': 5}),
                            'alarms': ('alarm.set', {'time': '06:00'})}[store_type]
            for ev, values in ((hit, args), (kept, args | ({'item': 'copper fern'} if store_type == 'lists'
                else {'note': 'copper fern'} if store_type == 'reminders' else {'time': '07:00'}))):
                assert k.actions.act('brain', action, ev.subject, values | {'asked': ev.seq},
                                     caused_by=(ev.seq,)).verified
        k.spine.forget('member-a', actor='member-a')
        k.pump()
        if store_type == 'skill-reminders':
            assert set(store.rows) == {f'r{kept.seq}'}
        elif store_type == 'alarms':
            assert set(store.at) == {'member-c'}
        else:
            assert [r['asked'] for r in store.rows['member-b']] == [kept.seq]
        assert k.spine._get(hit.seq) is None and k.spine._get(kept.seq) is not None
    finally:
        k.close()


def test_purge_cannot_delete_an_id_outside_live_notice(tmp_path):
    from test_forget_receipts_notices import boot, PERSON
    from zoe_os.skills.reminders import Reminders as SkillReminders
    from reminder_store import ReminderStore
    from zoe_os.spine import Refused

    k = boot(tmp_path / 'purge.db')
    try:
        store = ReminderStore().register(k)
        k.install(SkillReminders())
        request = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': PLANT}))
        k.spine.forget(PERSON, actor=PERSON)
        notice = next(e for e in k.spine._fetch(0, None, None, 100) if e.kind is Kind.FORGET)
        store.rows = {f'r{request.seq}': {'note': PLANT}, 'r99999': {'note': 'copper fern'}}
        with pytest.raises(Refused, match='^invalid purge id$'):
            k.actions.purge('reminders', 'reminder.delete', {'id': 'r99999'}, notice)
        with pytest.raises(Refused, match='^invalid purge$'):
            k.actions.purge('brain', 'reminder.delete', {'id': f'r{request.seq}'}, notice)
        with pytest.raises(Refused, match='^invalid purge$'):
            k.actions.purge('reminders', 'reminder.create', {'id': f'r{request.seq}'}, notice)
        # No member grant exists: erasure remains a duty, while unrelated rows stay.
        k.actions.purge('reminders', 'reminder.delete', {'id': f'r{request.seq}'}, notice)
        assert store.rows == {'r99999': {'note': 'copper fern'}}
    finally:
        k.close()


def test_a_purge_whose_person_cannot_be_resolved_is_refused_and_runs_no_driver(tmp_path, monkeypatch):
    """An anonymous whole-person notice names its subject only by a keyed hash. If that hash matches no known person, the
    purge is not confirmed and the erasure driver is never run for nobody; once it resolves, the same purge completes."""
    from test_forget_receipts_notices import boot, PERSON
    from zoe_os.skills.reminders import Reminders as SkillReminders
    from reminder_store import ReminderStore
    from zoe_os.spine import Refused

    k = boot(tmp_path / 'purge.db')
    try:
        store = ReminderStore().register(k)
        k.install(SkillReminders())
        request = k.spine.publish(Event(Kind.UTTERANCE, 'voice', PERSON, payload={'text': PLANT}))
        k.spine.forget(PERSON, actor=PERSON)
        notice = next(e for e in k.spine._fetch(0, None, None, 100) if e.kind is Kind.FORGET)
        assert not notice.payload.get('subject') and notice.payload.get('subject_hash')      # the anonymous form
        rid = f'r{request.seq}'
        store.rows = {rid: {'note': PLANT, 'owner': PERSON}}
        ran = []
        real = store.purge_one
        k.actions._purge_do['reminder.delete'] = lambda subject, args: (ran.append(subject), real(subject, args))[1]
        with monkeypatch.context() as m:
            m.setattr(k.spine, '_owner', lambda person: b'\x00' * 8)                          # the hash matches nobody
            with pytest.raises(Refused, match='^purge unresolved$'):
                k.actions.purge('reminders', 'reminder.delete', {'id': rid}, notice)
        assert ran == [] and rid in store.rows                                                # no driver, nothing confirmed
        k.actions.purge('reminders', 'reminder.delete', {'id': rid}, notice)                  # resolvable again: completes
        assert ran == [PERSON] and store.rows == {}
    finally:
        k.close()
