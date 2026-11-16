"""In process, a whole-person forget reaches skill state whose source retention had already
retired (it is in no notice list), through ``provenance.erased``: the context resolves the
anonymous notice's subject, the resolved member's state goes up to the watermark, and the
other member's state, retired sources included, is untouched (scoping is exact in process).
"""
from __future__ import annotations

import pytest
from reminder_store import ReminderStore
from world import ALEX, NOW, SAM, TOMORROW_0630, emit, household, presence, step

from zoe_os.events import Kind
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.skills.reminders import Reminders
from zoe_os.twin.baseline import Twin

HOUR = 3600


def retire(k, *events):
    seqs = [e.seq for e in events]
    k.spine._expire(seqs)
    k.spine._retire(seqs)
    assert all(k.spine._db.execute('SELECT 1 FROM events WHERE seq=?', (s,)).fetchone() is None for s in seqs)


def kernel_with(tmp_path, skill, name):
    k = household(tmp_path)
    k.install(skill)
    for person in (ALEX, SAM):
        k.grants.grant(person, name)
    return k


def forget_alex(k):
    k.spine.forget(ALEX, actor=ALEX)
    (notice,) = [e for e in k.spine._newest(frozenset({Kind.FORGET}), None)]
    assert 'subject' not in notice.payload           # anonymous by design
    k.pump()


def test_twin(tmp_path):
    twin = Twin()
    k = kernel_with(tmp_path, twin, 'twin')
    try:
        a, b = presence(k, ALEX, 'up'), presence(k, SAM, 'up')
        k.pump()
        retire(k, a, b)
        before = dict(twin.days[SAM])
        forget_alex(k)
        assert ALEX not in twin.days and twin.days[SAM] == before
    finally:
        k.close()


def test_leaving_catch(tmp_path):
    catch = LeavingCatch()
    k = kernel_with(tmp_path, catch, 'leaving-catch')
    try:
        a, b = step(k, ALEX, 'synthetic umbrella'), step(k, SAM, 'synthetic keys')
        k.pump()
        retire(k, a, b)
        forget_alex(k)
        assert catch._undone == {SAM: {'synthetic keys': b.seq}}
    finally:
        k.close()


def test_calendar_headsup(tmp_path):
    heads = CalendarHeadsUp()
    k = kernel_with(tmp_path, heads, 'calendar-heads-up')
    try:
        made = [emit(k, Kind.CALENDAR_CHANGE, 'calendar', p, event_id=f'e{i}', change='created',
                     title='synthetic early thing', starts_at=TOMORROW_0630)
                for i, p in enumerate((ALEX, SAM))]
        k.pump()
        assert {v[0] for v in heads._open.values()} == {ALEX, SAM}
        retire(k, *made)
        forget_alex(k)
        assert {v[0] for v in heads._open.values()} == {SAM}
    finally:
        k.close()


def test_reminders(tmp_path):
    skill = Reminders()
    k = household(tmp_path)
    try:
        k.now = [NOW]
        k.clock = lambda: k.now[0]
        store = ReminderStore().register(k)
        k.install(skill)
        for person in (ALEX, SAM):
            k.grants.grant(person, 'reminders')
        for person in (ALEX, SAM):
            emit(k, Kind.UTTERANCE, 'voice', person, text='synthetic ask',
                 intent={'name': 'reminder.create', 'at': NOW + HOUR, 'note': 'synthetic note'})
        k.pump()
        assert {i['person'] for i in skill._items.values()} == {ALEX, SAM}
        sources = [e for e in k.spine._newest(frozenset({Kind.UTTERANCE, Kind.ACTION_RECEIPT}), None)]
        retire(k, *sources)
        before = {r: dict(i) for r, i in skill._items.items() if i['person'] == SAM}
        forget_alex(k)
        assert {r: i for r, i in skill._items.items()} == before
        assert all(row['owner'] != ALEX for row in store.rows.values())
    finally:
        k.close()
