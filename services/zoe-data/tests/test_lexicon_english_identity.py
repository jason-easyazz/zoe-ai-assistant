"""The English word lists were MOVED out of the lexical floors into ``lexicons_data/en.json`` - not rewritten.

``memory_authority`` / ``role_guess_guard`` / ``people_roles`` now build their regexes and word sets from the lexicon; this file pins
every one of them BYTE FOR BYTE against the pre-move patterns (``fixtures/english_lexicon_goldens.json``, captured from
``origin/main`` @ 0e45a409 before the move). A word dropped from, or added to, ``en.json`` without a decision turns this red - the
English floors cannot drift by editing data. Break-the-fix control: the last test edits a list and shows the golden catches it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import lexicons
import memory_authority as ma
import people_roles as pr
import role_guess_guard as rg

pytestmark = pytest.mark.ci_safe

GOLD = json.loads((Path(__file__).parent / "fixtures" / "english_lexicon_goldens.json").read_text(encoding="utf-8"))


def _pat(r: "re.Pattern[str]") -> list:
    return [r.pattern, r.flags]


@pytest.mark.parametrize("name", [k for k, v in GOLD["ma"].items() if k.endswith("_RE")])
def test_memory_authority_regexes_are_the_pre_move_patterns(name):
    assert _pat(getattr(ma, name)) == GOLD["ma"][name]


@pytest.mark.parametrize("name", ["_FIRST_PERSON", "_PLAIN_FIRST_PERSON", "_NOT_A_CONTRAST", "_TEMPORAL", "_ANAPHORA", "_ARTICLES"])
def test_memory_authority_word_sets_are_the_pre_move_sets(name):
    assert sorted(getattr(ma, name)) == GOLD["ma"][name]


def test_memory_authority_strings_and_tuples():
    assert ma._RELATION == GOLD["ma"]["_RELATION"]
    assert ma._ENDED_BASE == GOLD["ma"]["_ENDED_BASE"]
    assert list(ma._HEDGE_VERBS) == GOLD["ma"]["_HEDGE_VERBS"]
    assert list(ma._HEDGE_WORDS) == GOLD["ma"]["_HEDGE_WORDS"]


def test_role_guard_and_people_roles_are_the_pre_move_patterns():
    assert list(rg.GUESS_ROLES) == GOLD["rg"]["GUESS_ROLES"]
    assert rg._ROLE == GOLD["rg"]["_ROLE"] and rg._ADV == GOLD["rg"]["_ADV"]
    for name in ("_HYPO_LEAD", "_HYPO_ANY", "_NEGATED"):
        assert _pat(getattr(rg, name)) == GOLD["rg"][name], name
    assert list(pr.PET_WORDS) == GOLD["pr"]["PET_WORDS"] and pr._ROLE_ALT == GOLD["pr"]["_ROLE_ALT"]
    assert _pat(pr._ROLE_RE) == GOLD["pr"]["_ROLE_RE"]
    assert [_pat(r) for r in pr._CLAIM_RES] == GOLD["pr"]["_CLAIM_RES"]


def test_every_guess_role_has_a_kin_code():
    """The role guard polices these words; the id-triple floor judges them by code - none may be unmappable."""
    missing = [w for w in rg.GUESS_ROLES if lexicons.kin_code(w, "en") is None]
    assert missing == []


def test_control_a_changed_list_is_caught():
    """Break-the-fix: with one word removed from the lexicon the rebuilt pattern no longer equals the golden."""
    en = lexicons.load("en")
    rebuilt = re.compile(r"\b(?:" + lexicons.alt(w for w in en["hypothetical"] if w != "wish") + r")\b", re.IGNORECASE)
    assert _pat(rebuilt) != GOLD["ma"]["_HYPOTHETICAL_RE"]


@pytest.mark.parametrize("lang", lexicons.LANGS)
def test_every_lexicon_file_is_well_formed(lang):
    lex = lexicons.load(lang)
    assert lex and lex["lang"] == lang and isinstance(lex["enabled"], bool) and isinstance(lex["reviewed"], bool)
    for key in ("negation_words", "kin_codes", "temporal", "anaphora", "contrast_connectors", "user_markers", "first_person"):
        assert lex.get(key), (lang, key)
    for code, forms in lex["kin_codes"].items():
        assert forms and all(isinstance(f, str) and f for f in forms), (lang, code)
    for key, value in lex.items():
        if isinstance(value, list):
            assert all(isinstance(v, str) for v in value), (lang, key)
            if key != "question_marks":      # the only list of LITERAL strings; every other fragment is a regex
                for frag in value:
                    re.compile(frag)
    if lang != "en":
        assert lex["reviewed"] is False       # author-written, not native-reviewed (research note E6) until a native reads it
