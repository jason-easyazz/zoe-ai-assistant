"""An action that declares its parameters is checked before its driver runs, and can be described.

Kernel change for the brain bridge: ``Dispatcher.register(params=...)``, ``check_params``,
``Dispatcher.catalog``. The negative control removes the check and shows the driver run on
arguments it was never meant to get.
"""
from __future__ import annotations

import pytest
from world import ALEX, emit, household, of_kind, refusal_reasons

from zoe_os import receipts
from zoe_os.events import Kind
from zoe_os.spine import Refused

PARAMS = {"item": {"type": "string", "required": True, "description": "what"},
          "count": {"type": "integer", "required": False, "description": "how many"}}


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.seen = []
    kernel.grants.install({"skill": "helper", "reads": {"utterance": "private"}, "acts": ["thing.do", "thing.old"]})
    kernel.actions.register("thing.do", lambda p, a: kernel.seen.append(a), lambda p, a, r: {"ok": 1},
                            needs_accept=False, doc="Do a thing", params=PARAMS)
    kernel.actions.register("thing.old", lambda p, a: kernel.seen.append(a), lambda p, a, r: {"ok": 1},
                            needs_accept=False)             # an earlier-style action: no params
    kernel.grants.grant(ALEX, "helper")
    kernel.cause = emit(kernel, Kind.UTTERANCE, "voice", ALEX, text="hello").seq
    yield kernel
    kernel.close()


def act(k, action="thing.do", **args):
    return k.actions.act("helper", action, ALEX, args, caused_by=(k.cause,))


def test_exact_arguments_run(k):
    assert act(k, item="milk", count=2).verified and k.seen == [{"item": "milk", "count": 2}]


@pytest.mark.parametrize("args,reason", [
    ({}, "missing argument"), ({"item": "x", "extra": 1}, "unknown argument"),
    ({"item": 5}, "wrong argument type"), ({"item": "x", "count": True}, "wrong argument type"),
    ({"item": "x", "count": "2"}, "wrong argument type"), ({"item": "   "}, "wrong argument type"),
    ({"item": "y" * 501}, "wrong argument type"), ({"item": None}, "wrong argument type")])
def test_a_malformed_proposal_is_refused_recorded_and_runs_nothing(k, args, reason):
    with pytest.raises(Refused, match=reason):
        act(k, **args)
    assert k.seen == [] and refusal_reasons(k) == [reason] and of_kind(k, Kind.ACTION_RECEIPT) == []
    assert k.spine._db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0   # not even an intent


def test_control_without_the_check_the_driver_runs_on_malformed_arguments(k, monkeypatch):
    monkeypatch.setattr(receipts, "check_params", lambda params, args: "")
    act(k, item=5, extra=1)
    assert k.seen == [{"item": 5, "extra": 1}]


def test_an_action_without_params_is_unchecked_and_not_described(k):
    assert act(k, "thing.old", anything=1).verified
    names = [d["action"] for d in k.actions.catalog(["thing.do", "thing.old", "nope"])]
    assert names == ["thing.do"]


def test_the_catalog_is_data_only(k):
    (entry,) = k.actions.catalog(["thing.do"])
    assert entry == {"action": "thing.do", "doc": "Do a thing", "params": PARAMS,
                     "needs_accept": False, "reversible": False}


def test_pattern_minimum_and_maximum_refuse_what_the_type_alone_would_let_through():
    p = {"time": {"type": "string", "required": True, "max": 5, "pattern": r"([01]\d|2[0-3]):[0-5]\d"},
         "n": {"type": "integer", "required": True, "minimum": 1, "maximum": 10}}
    ok = {"time": "07:30", "n": 5}
    assert receipts.check_params(p, ok) == ""
    for bad in ({"time": "seven", "n": 5}, {"time": "6:00 AM", "n": 5}, {"time": "25:00", "n": 5},
                {"time": "07:30", "n": 0}, {"time": "07:30", "n": 11}):
        assert receipts.check_params(p, bad) == "wrong argument type", bad
