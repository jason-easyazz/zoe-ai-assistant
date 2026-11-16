"""An action for a person must cite an event about that person (confused-deputy guard).

Kernel change for the brain bridge. A skill granted by both people could otherwise act for
SAM citing ALEX's utterance, because the kernel checked that the skill may READ the cause and
that the person granted the skill, but never that the cause was about the person.
"""
from __future__ import annotations

import pytest
from world import ALEX, SAM, emit, household, of_kind, refusal_reasons

from zoe_os.events import Kind
from zoe_os.receipts import Dispatcher
from zoe_os.spine import Refused


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.grants.install({"skill": "helper", "reads": {"utterance": "private"}, "acts": ["thing.do"]})
    kernel.did = []
    kernel.actions.register("thing.do", lambda p, a: kernel.did.append(p), lambda p, a, r: {"ok": p},
                            needs_accept=False)
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "helper")
    kernel.alex = emit(kernel, Kind.UTTERANCE, "voice", ALEX, text="alex speaking").seq
    kernel.sam = emit(kernel, Kind.UTTERANCE, "voice", SAM, text="sam speaking").seq
    yield kernel
    kernel.close()


def test_acting_for_a_person_on_their_own_event_works(k):
    assert k.actions.act("helper", "thing.do", SAM, {}, caused_by=(k.sam,)).verified and k.did == [SAM]


def test_acting_for_a_person_on_someone_elses_event_is_refused_though_both_granted(k):
    with pytest.raises(Refused, match="not about that person"):
        k.actions.act("helper", "thing.do", SAM, {}, caused_by=(k.alex,))
    assert k.did == [] and refusal_reasons(k) == ["the cited event is not about that person"]
    assert of_kind(k, Kind.ACTION_RECEIPT) == []


def test_control_without_the_rule_the_deputy_acts_for_sam_on_alexs_word(k, monkeypatch):
    monkeypatch.setattr(Dispatcher, "_concerns", lambda self, person, caused_by: True)
    assert k.actions.act("helper", "thing.do", SAM, {}, caused_by=(k.alex,)).verified and k.did == [SAM]
