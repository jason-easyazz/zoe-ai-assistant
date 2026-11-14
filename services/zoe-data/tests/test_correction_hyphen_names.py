"""A correction naming one person never rewrites a different person's value: a hyphenated part is not a name ("tova" is not
"Tova-Rae"). Invented names only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import time

import pytest

import value_correction as vc

pytestmark = pytest.mark.ci_safe

UID = "demo_hyphen_00000001"
HYPHEN, PLAIN = "Tova-Rae is 6", "Tova is 6"


def run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": UID}


class _Svc:
    def __init__(self, *texts):
        self.rows = [_Row(f"r{i}", t) for i, t in enumerate(texts)]
        self.edits = []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, actor=None, **_k):
        self.edits.append((rid, edits))
        return object()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    async def nobody(_name, _uid):
        return "error"                  # the people graph is never asked to decide here: no shortening on its say-so

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    monkeypatch.setattr(vc, "person_status", nobody)
    vc.reset_state()
    yield
    vc.reset_state()


@pytest.mark.parametrize("said", ["tova is 7 not 6", "rae is 7 not 6"])
def test_a_hyphenated_part_is_never_the_corrected_person(said):
    svc = _Svc(HYPHEN)
    assert run(vc.apply(said, UID, None, svc=svc)) == 0
    assert svc.edits == [] and svc.rows[0].text == HYPHEN                    # untouched; the correction stays as said
    assert vc.rewrite(HYPHEN, vc.statement(said)) is None
    assert vc.superseded_by_correction(UID, HYPHEN, said_at=time.time() - 60) is False


def test_with_both_people_stored_only_the_named_one_is_rewritten():
    svc = _Svc(PLAIN, HYPHEN)
    assert run(vc.apply("tova is 7 not 6", UID, None, svc=svc)) == 1
    assert svc.edits == [("r0", "Tova is 7")]


@pytest.mark.parametrize("said", ["tova-rae is 7 not 6", "TOVA-RAE is 7 not 6"])
def test_the_full_hyphenated_name_corrects_its_own_row(said):
    svc = _Svc(PLAIN, HYPHEN)
    assert run(vc.apply(said, UID, None, svc=svc)) == 1
    assert svc.edits == [("r1", "Tova-Rae is 7")]


def test_the_apostrophe_and_lazy_possessive_paths_are_unchanged():
    row = "Tova's swimming is on Thursday"
    assert vc.rewrite(row, vc.statement("actually tova's swimming is on wednesday not thursday")) == "Tova's swimming is on Wednesday"
    assert vc.rewrite(row, vc.statement("actually tovas swimming is on wednesday not thursday"), lazy_s=True) == \
        "Tova's swimming is on Wednesday"
    assert vc.rewrite("Kai is 22", vc.statement("kais 21 not 22"), lazy_s=True) == "Kai is 21"
