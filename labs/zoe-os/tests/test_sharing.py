"""Sharing is the person's choice: off by default, one way, revocable, and only theirs to make."""
from __future__ import annotations

import pytest
from test_decisions import a_sick_child_day

from zoe_os.capability import ConsentError
from zoe_os.events import Kind
from zoe_os.twin.unusual import Sharing, shown_to


@pytest.fixture
def scene(tmp_path):
    """The generated sick-child day: there are lines about SAM and about KID, none about ALEX."""
    k, result, people = a_sick_child_day(tmp_path)
    yield k, result, people, Sharing(k.spine, people)
    k.close()


def about(result, viewer, people, sharing) -> set[str]:
    return {d.person for d in shown_to(result, viewer, people, sharing)}


def test_nothing_is_shared_until_the_person_chooses_and_then_only_with_whom_they_chose(scene):
    k, result, people, sharing = scene
    assert about(result, "alex", people, sharing) == {"kid"}               # the default: not SAM's
    sharing.share("sam", "alex", by="sam")
    assert about(result, "alex", people, sharing) == {"kid", "sam"}
    assert about(result, "other", people, sharing) == set() and about(result, "visitor", people, sharing) == set()
    assert sharing.shared_with("sam") == {"alex"} and sharing.shared_with("alex") == set()     # one way


def test_stopping_takes_effect_at_once(scene):
    k, result, people, sharing = scene
    sharing.share("sam", "alex", by="sam")
    sharing.stop("sam", "alex", by="sam")
    assert about(result, "alex", people, sharing) == {"kid"} and sharing.shared_with("sam") == set()


def test_only_the_person_themself_can_choose_and_a_childs_lines_go_no_further(scene):
    k, result, people, sharing = scene
    for person, other, by in (("sam", "alex", "alex"),       # a partner cannot switch it on for you
                              ("sam", "alex", "twin"),       # nor a skill
                              ("kid", "other", "alex"),      # nor a guardian for a child, beyond the guardians
                              ("kid", "other", "kid")):      # nor the child
        with pytest.raises(ConsentError, match="only the person themself"):
            sharing.share(person, other, by=by)
    for other in ("visitor", "twin", "no-such-person", "sam"):          # a guest, a skill, nobody, yourself
        with pytest.raises(ConsentError, match="another member of the household"):
            sharing.share("sam", other, by="sam")
    assert about(result, "alex", people, sharing) == {"kid"} and about(result, "other", people, sharing) == set()


def test_negative_control_without_the_ownership_rule_someone_else_shares_your_lines(scene, monkeypatch):
    k, result, people, sharing = scene
    monkeypatch.setattr(Sharing, "_may_set", lambda self, by, person: True)
    sharing.share("sam", "other", by="other")                # an outsider switches it on for themself
    assert about(result, "other", people, sharing) == {"sam"}


def test_each_choice_is_recorded_with_names_and_no_content(scene):
    k, result, people, sharing = scene
    sharing.share("sam", "alex", by="sam")
    sharing.stop("sam", "alex", by="sam")
    changes = [e.payload for e in k.spine._fetch(0, frozenset({Kind.GRANT_CHANGE}), None, 1000)
               if "with" in e.payload]
    assert changes == [{"change": "shared", "person": "sam", "with": "alex"},
                       {"change": "unshared", "person": "sam", "with": "alex"}]
    assert k.explainer.why(k.spine.last_seq, person="sam")["kind"] == "grant_change"      # SAM can ask why
