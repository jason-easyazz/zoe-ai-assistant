"""Owner-scoped bookkeeping hashes the EXACT person id. Two ids that normalise alike as names
(a hyphen versus an underscore) are two people: one's forget never refuses the other's words,
and a whole-person notice for one never resolves to the other. Controls put back the former
hash over the normalised name.
"""
import pytest

from procs import Box
from spine_helpers import AllowList
from world import household

from zoe_os.capability import Person
from zoe_os.events import Event, Kind
from zoe_os.forget_match import entity_key
from zoe_os.kernel import Context, Kernel
from zoe_os.spine import Refused, Spine
from zoe_os.twin.baseline import Twin

A, B = 'member-x1', 'member_x1'          # distinct ids, the same name key
PHRASE = 'synthetic saffron lantern'
assert entity_key(A) == entity_key(B) and A != B


def normalised(monkeypatch):
    monkeypatch.setattr(Spine, '_owner', lambda self, person: self._fp('owner\0' + entity_key(person)))


@pytest.fixture
def spine(tmp_path):
    s = Spine(str(tmp_path / 'ids.db'), AllowList(origins={'voice'}, readers={A: {A}, B: {B}}),
              members={A, B})
    yield s
    s.close()


def say(s, person, text=PHRASE):
    return s.publish(Event(Kind.UTTERANCE, 'voice', person, payload={'text': text}))


def test_a_forgotten_phrase_is_refused_for_a_and_not_for_b(spine):
    spine.forget(A, actor=A, seqs=[say(spine, A).seq])
    assert say(spine, B).seq
    with pytest.raises(Refused, match='forgotten content'):
        say(spine, A)


def test_control_normalised_owner_refuses_b(spine, monkeypatch):
    normalised(monkeypatch)
    spine.forget(A, actor=A, seqs=[say(spine, A).seq])
    with pytest.raises(Refused, match='forgotten content'):
        say(spine, B)


@pytest.fixture
def k(tmp_path):
    kernel = Kernel(str(tmp_path / 'ids.db'), [Person(B), Person(A)])   # B first: a wrong match finds B
    kernel.grants.install({'skill': 'voice', 'origin': True, 'emits': ['utterance']})
    kernel.grants.install({'skill': 'skill', 'reads': {'utterance': 'private'}, 'acts': ['synthetic.del']})
    kernel.purged = []
    kernel.actions.register('synthetic.del', lambda p, a: kernel.purged.append(p), lambda p, a, r: True,
                            needs_accept=False, purge_key=('id', 'r'))
    yield kernel
    kernel.close()


def whole_notice(k):
    ask = say(k.spine, A)
    k.spine.forget(A, actor=A)
    (notice,) = [e for e in k.spine._newest(frozenset({Kind.FORGET}), None)]
    assert 'subject' not in notice.payload
    return ask, notice


def test_whole_notice_resolves_to_a_in_process_and_in_purge(k):
    ask, notice = whole_notice(k)
    assert Context(k, 'skill').forgotten_subject(notice) == A
    k.actions.purge('skill', 'synthetic.del', {'id': f'r{ask.seq}'}, notice)
    assert k.purged == [A]


def test_control_normalised_owner_resolves_to_b(k, monkeypatch):
    normalised(monkeypatch)
    ask, notice = whole_notice(k)
    assert Context(k, 'skill').forgotten_subject(notice) == B
    k.actions.purge('skill', 'synthetic.del', {'id': f'r{ask.seq}'}, notice)
    assert k.purged == [B]


def test_an_unmatched_hash_is_refused_by_purge(k, monkeypatch):
    ask, notice = whole_notice(k)
    monkeypatch.setattr(k.spine, '_owner', lambda person: b'\x00' * 32)
    with pytest.raises(Refused, match='purge unresolved'):
        k.actions.purge('skill', 'synthetic.del', {'id': f'r{ask.seq}'}, notice)
    assert k.purged == []


def test_in_process_twin_forgets_a_and_keeps_b(tmp_path):
    k = household(tmp_path, extra=(A, B))
    try:
        twin = Twin()
        k.install(twin)
        for person in (A, B):
            k.grants.grant(person, 'twin')
        from world import presence
        presence(k, A, 'up'), presence(k, B, 'up')
        k.pump()
        before = dict(twin.days[B])
        k.spine.forget(A, actor=A)
        k.pump()
        assert A not in twin.days and twin.days[B] == before
    finally:
        k.close()


# ---- through the remote Client, against a kernel in its own process ---------------------------
def remote_box(*controls):
    box = Box()
    box.cfg['install'].append(Twin.manifest)
    box.cfg['extra_people'] = [A, B]
    box.restart(*controls)
    return box


def remote_scene(box):
    panel, voice, sensor = box.client('panel'), box.client('voice'), box.client('presence')
    for person in (A, B):
        panel.grant(person, 'twin')
        sensor.publish('presence', subject=person, payload={'state': 'up', 'confidence': 0.95})
    twin, client = Twin(), box.client('twin')
    client.run(twin, from_seq=0)
    words = voice.publish('utterance', subject=A, payload={'text': PHRASE})
    panel.forget(A, actor=A, seqs=[words.seq])
    return panel, voice, twin, client, words


def test_remote_b_is_not_refused_and_a_whole_forget_leaves_b_twin_state():
    box = remote_box()
    try:
        panel, voice, twin, client, words = remote_scene(box)
        assert voice.publish('utterance', subject=B, payload={'text': PHRASE}).seq
        with pytest.raises(Refused):
            voice.publish('utterance', subject=A, payload={'text': PHRASE})
        before = dict(twin.days[B])
        panel.forget(A, actor=A)
        client.run(twin, from_seq=words.seq)
        assert A not in twin.days and twin.days[B] == before   # B's sources are live: kept
    finally:
        box.close()


def test_control_remote_normalised_owner_refuses_b():
    box = remote_box('normalised_owner')
    try:
        _, voice, *_ = remote_scene(box)
        with pytest.raises(Refused):
            voice.publish('utterance', subject=B, payload={'text': PHRASE})
    finally:
        box.close()
