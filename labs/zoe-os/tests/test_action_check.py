"""An action's own rule (``Dispatcher.register(check=...)``): refused and recorded before the intent and the driver, on the
arguments the kernel bound, and dropped when the action is registered again without one."""
from __future__ import annotations

import pytest
from world import ALEX, emit, household, of_kind, refusal_reasons

from zoe_os.events import Kind
from zoe_os.spine import Refused

PARAMS = {"item": {"type": "string", "required": True, "description": "what"},
          "asked": {"type": "integer", "required": False, "description": "the single authorised request (set by the kernel)"}}


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.seen, kernel.checked = [], []
    kernel.grants.install({"skill": "helper", "reads": {"utterance": "private"}, "acts": ["thing.do"]})

    def rule(person, args, caused_by):
        kernel.checked.append((person, dict(args), caused_by))
        return "not today" if args["item"] == "no" else ""

    kernel.rule = rule
    kernel.actions.register("thing.do", lambda p, a: kernel.seen.append(a), lambda p, a, r: {"ok": 1},
                            needs_accept=False, doc="Do a thing", params=PARAMS, check=rule)
    kernel.grants.grant(ALEX, "helper")
    kernel.cause = emit(kernel, Kind.UTTERANCE, "voice", ALEX, text="hello").seq
    yield kernel
    kernel.close()


def act(k, **args):
    return k.actions.act("helper", "thing.do", ALEX, args, caused_by=(k.cause,))


def test_the_rule_refuses_before_the_intent_and_the_driver_and_is_recorded(k):
    with pytest.raises(Refused, match="not today"):
        act(k, item="no")
    assert k.seen == [] and refusal_reasons(k) == ["not today"] and of_kind(k, Kind.ACTION_RECEIPT) == []
    assert k.spine._db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0


def test_the_rule_sees_the_arguments_the_kernel_bound_not_the_callers(k):
    assert act(k, item="yes", asked=999999).verified                 # a caller-chosen provenance is overwritten first
    person, args, caused_by = k.checked[-1]
    assert person == ALEX and args["asked"] == k.cause and caused_by == (k.cause,)
    assert k.seen[-1]["asked"] == k.cause


def test_registering_again_without_a_rule_drops_the_old_one(k):
    k.actions.register("thing.do", lambda p, a: k.seen.append(a), lambda p, a, r: {"ok": 1},
                       needs_accept=False, doc="Do a thing", params=PARAMS)
    assert act(k, item="no").verified and k.seen[-1]["item"] == "no"


def test_control_a_stale_rule_would_still_refuse(k):
    """What the line above protects against: with the old rule left in place, the re-registered action is still refused."""
    k.actions.register("thing.do", lambda p, a: k.seen.append(a), lambda p, a, r: {"ok": 1},
                       needs_accept=False, doc="Do a thing", params=PARAMS)
    k.actions._checks["thing.do"] = k.rule                           # the pre-fix state
    with pytest.raises(Refused, match="not today"):
        act(k, item="no")
