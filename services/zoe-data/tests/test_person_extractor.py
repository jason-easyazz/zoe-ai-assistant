"""Focused tests for the pure/deterministic helpers in person_extractor.

The module mixes async DB/LLM-touching entry points (``process_text``,
``apply_person_fact``) with a small set of pure helpers used during the
regex-based extraction phase: a birthday parser, the compiled intent
regexes, and the role-to-relationship-type mapping table.

This test file pins the contract of those pure pieces only. The async
DB-bound functions are deliberately not exercised here so the suite can
run on any host (no PostgreSQL, no MemPalace, no model calls).
"""

from __future__ import annotations

import pytest

import person_extractor as person_extractor_module
from person_extractor import (
    _BDAY_RE,
    _BUCKET_RE,
    _GIFT_GIVEN_RE,
    _GIFT_IDEA_RE,
    _MEETING_RE,
    _MONTHS,
    _PREF_RE,
    _REL_RE,
    _ROLE_TO_TYPE,
    _WORK_RE,
    _parse_birthday,
)

pytestmark = pytest.mark.ci_safe


# ── __all__ public contract ──────────────────────────────────────────────────


def test_module_all_lists_public_entry_points():
    # ``__all__`` is the module's stable import surface; downstream callers
    # and tests rely on it. Locking it down prevents accidental removal
    # of the regex-orchestrating entry points when refactoring.
    assert person_extractor_module.__all__ == [
        "process_text",
        "apply_person_fact",
        "_resolve_person_uuid",
        "_parse_birthday",
    ]


def test_parse_birthday_is_exported_by_name():
    # The helper must be importable directly so callers (and other tests)
    # can reuse it without going through ``person_extractor._parse_birthday``.
    assert "_parse_birthday" in person_extractor_module.__all__


# ── _MONTHS lookup table ─────────────────────────────────────────────────────


def test_months_table_has_twelve_short_names():
    # Birthday parsing depends on every short month key resolving to an
    # int in [1, 12]. A missing key would silently drop date facts.
    expected_short = {"jan", "feb", "mar", "apr", "may", "jun",
                      "jul", "aug", "sep", "oct", "nov", "dec"}
    # Set-membership, not insertion order: reorganising _MONTHS must not break this.
    assert expected_short.issubset(_MONTHS.keys())
    assert all(1 <= _MONTHS[k] <= 12 for k in expected_short)


def test_months_short_and_long_names_agree():
    # The parser only inspects the first three letters of a month token,
    # so short and long forms must resolve to the same number. If a
    # contributor adds "may" → 5 but forgets "may" (only as long form),
    # both lookups must still match.
    pairs = [("jan", "january"), ("feb", "february"),
             ("mar", "march"), ("apr", "april"),
             ("may", "may"),     ("jun", "june"),
             ("jul", "july"),    ("aug", "august"),
             ("sep", "september"),("oct", "october"),
             ("nov", "november"),("dec", "december")]
    for short, long_ in pairs:
        assert _MONTHS[short] == _MONTHS[long_]
        assert 1 <= _MONTHS[short] <= 12


def test_months_lookup_is_case_insensitive_via_caller():
    # The parser lower-cases + truncates the token before lookup, so
    # ``_MONTHS`` itself only needs lowercase keys. Pin this so a future
    # refactor that case-folds the table doesn't double-normalize.
    assert all(k == k.lower() for k in _MONTHS.keys())


# ── _parse_birthday ──────────────────────────────────────────────────────────


def test_parse_birthday_day_first_format():
    # "15 March" — the most common casual form, used by the _BDAY_RE match.
    assert _parse_birthday("15 March") == (3, 15, None)


def test_parse_birthday_month_first_format():
    # "March 15" — also common; the parser must handle both orderings.
    assert _parse_birthday("March 15") == (3, 15, None)


def test_parse_birthday_iso_format():
    # YYYY-MM-DD is the unambiguous wire format; the m3 branch (re.match)
    # runs last and produces all three components when matched. Note: the
    # three date patterns are structurally mutually exclusive (ISO has no
    # alpha or spaces), so "precedence" is never actually contested.
    assert _parse_birthday("2024-03-15") == (3, 15, 2024)


def test_parse_birthday_iso_format_year_first_correctly_assigned():
    # Defensive: year is group 1, month is group 2, day is group 3 — make
    # sure the function does not silently swap month and day in YYYY-MM-DD.
    month, day, year = _parse_birthday("1990-12-25")
    assert (year, month, day) == (1990, 12, 25)


def test_parse_birthday_strips_surrounding_whitespace():
    # Real-world text often carries leading/trailing spaces. The helper
    # must strip them so regex anchors line up.
    assert _parse_birthday("  15 March  ") == (3, 15, None)


def test_parse_birthday_short_month_abbreviation():
    # The parser lower-cases + takes the first 3 letters, so "Mar" must
    # resolve to March just like the full word.
    assert _parse_birthday("15 Mar") == (3, 15, None)


def test_parse_birthday_long_month_full_name():
    assert _parse_birthday("December 25") == (12, 25, None)


def test_parse_birthday_day_first_with_year_after_is_not_iso():
    # "25 December 1990" is NOT YYYY-MM-DD — the helper must NOT promote
    # the trailing year into the year field. The m3 anchor requires
    # leading-4-digit-year + hyphen, so year stays None and the caller
    # can decide how to interpret the trailing token.
    month, day, year = _parse_birthday("25 December 1990")
    assert (month, day) == (12, 25)
    assert year is None


def test_parse_birthday_invalid_day_returns_none_day():
    # Out-of-range days must be rejected, not silently truncated.
    month, day, year = _parse_birthday("32 March")
    assert day is None
    # month is resolved to 3 by the day-first regex before the validation
    # step zeroes the bad day; pin whatever the helper actually returns
    # so a future regression that swaps the validation order is visible.
    assert month == 3
    assert year is None


def test_parse_birthday_empty_string_returns_all_none():
    # Empty input must not crash and must produce a fully-None triple
    # so callers can short-circuit on ``not any(result)``.
    assert _parse_birthday("") == (None, None, None)


def test_parse_birthday_only_digits_returns_all_none():
    # "15" alone has no month token, so the helper cannot resolve one.
    # Day should also be None because no regex matches a bare number.
    assert _parse_birthday("15") == (None, None, None)


def test_parse_birthday_only_month_returns_all_none():
    # "March" alone has no day token, so the parser must not invent one.
    # Both regexes require a digit token to fire.
    assert _parse_birthday("March") == (None, None, None)


def test_parse_birthday_unrecognized_month_returns_none_month():
    # "Foonuary 15" — unknown month name. The day-first regex captures
    # the number, but _MONTHS has no entry for "foo", so month becomes None.
    month, day, year = _parse_birthday("15 Foonuary")
    assert month is None
    assert day == 15
    assert year is None


def test_parse_birthday_returns_three_tuple_of_optional_ints():
    # The contract is ``tuple[Optional[int], Optional[int], Optional[int]]``
    # in (month, day, year) order. Any drift in shape or order breaks
    # downstream unpackers in process_text and apply_person_fact.
    result = _parse_birthday("15 March")
    assert isinstance(result, tuple)
    assert len(result) == 3
    for value in result:
        assert value is None or isinstance(value, int)


# ── Intent regex patterns ────────────────────────────────────────────────────


def test_pref_regex_matches_loves_form():
    # The preference regex is the workhorse for "Sarah loves pizza" /
    # "Mike hates broccoli" style facts. Group 1 must be the name, group
    # 2 must be the object of the preference.
    m = _PREF_RE.search("Sarah loves pizza")
    assert m is not None
    assert m.group(1) == "Sarah"
    assert "pizza" in m.group(2)


def test_pref_regex_matches_dislikes():
    # All six verbs (love, like, hate, prefer, enjoy, dislike) must work;
    # pin "dislikes" since it has the longest suffix.
    m = _PREF_RE.search("Mike dislikes rainy days")
    assert m is not None
    assert m.group(1) == "Mike"


def test_pref_regex_is_case_insensitive_due_to_ignorecase_flag():
    # The whole pattern is compiled with ``re.IGNORECASE``, which makes
    # the [A-Z] anchor in _NAME match lowercase too. The module docstring
    # claims names "start with a capital letter", but the IGNORECASE flag
    # defeats that guard. Pin the current behavior so a future refactor
    # that switches to case-sensitive name matching surfaces here.
    m = _PREF_RE.search("she loves pizza")
    assert m is not None
    assert m.group(1) == "she"


def test_bday_regex_matches_possessive_form():
    # "Sarah's birthday is March 15" — the most common phrasing.
    m = _BDAY_RE.search("Sarah's birthday is March 15")
    assert m is not None
    assert m.group(1) == "Sarah"
    assert "March 15" in m.group(2)


def test_bday_regex_matches_plain_form():
    # "John birthday is on June 1" — without possessive, with "on".
    m = _BDAY_RE.search("John birthday is on June 1")
    assert m is not None
    assert m.group(1) == "John"


def test_bday_regex_rejects_unrelated_sentence():
    # Pin that the regex doesn't match a sentence that merely contains
    # the word "birthday" without the structure.
    assert _BDAY_RE.search("Birthday parties are fun") is None


def test_work_regex_matches_works_at():
    m = _WORK_RE.search("Mike works at Google")
    assert m is not None
    assert m.group(1) == "Mike"
    assert "Google" in m.group(2)


def test_work_regex_matches_is_a_role_at():
    # The regex supports both "works at Google" and "is a doctor at Hospital".
    m = _WORK_RE.search("Anna is a doctor at Mercy Hospital")
    assert m is not None
    assert m.group(1) == "Anna"
    assert "Mercy Hospital" in m.group(2)


def test_meeting_regex_matches_with_venue():
    # "Met Sarah for coffee at Blue Bottle" — venue is captured in group 2.
    # Quirks to be aware of: with re.IGNORECASE, the optional second-name
    # part of _NAME is greedy enough to swallow the "for" connector, so
    # the name group comes back as "Sarah for" rather than "Sarah". Pin
    # the current contract so a future tightening surfaces here.
    m = _MEETING_RE.search("Met Sarah for coffee at Blue Bottle")
    assert m is not None
    assert m.group(1) == "Sarah for"
    assert m.group(2) == "Blue Bottle"


def test_meeting_regex_matches_without_venue():
    # "Met Mike for coffee" — the optional venue group is None.
    # Same "for" swallowing quirk applies to group 1.
    m = _MEETING_RE.search("Met Mike for coffee")
    assert m is not None
    assert m.group(1) == "Mike for"
    assert m.group(2) is None


def test_gift_idea_regex_matches_buying_form():
    m = _GIFT_IDEA_RE.search("Buying Sarah a book for her birthday")
    assert m is not None
    assert m.group(1) == "Sarah"


def test_gift_idea_regex_matches_thinking_about_getting():
    # "Thinking about getting" is the longest verb form; pin it.
    m = _GIFT_IDEA_RE.search("Thinking about getting Mike a watch")
    assert m is not None
    assert m.group(1) == "Mike"


def test_gift_given_regex_matches_past_tense_gift():
    m = _GIFT_GIVEN_RE.search("Gave Sarah a necklace for her birthday")
    assert m is not None
    assert m.group(1) == "Sarah"


def test_gift_given_regex_matches_bought_form():
    m = _GIFT_GIVEN_RE.search("Bought Mike a watch for his birthday")
    assert m is not None
    assert m.group(1) == "Mike"


def test_bucket_regex_matches_want_to_form():
    # "I want to travel with Sarah." — period anchors the match to
    # the end of the sentence; the bucket regex's trailing punctuation
    # class ([.!?]|$) is required.
    m = _BUCKET_RE.search("I want to travel with Sarah.")
    assert m is not None
    assert m.group(2) == "Sarah"


def test_bucket_regex_matches_would_love_to_form():
    # "Would love to hike with Mike." — same end-anchor requirement.
    m = _BUCKET_RE.search("Would love to hike with Mike.")
    assert m is not None
    assert m.group(2) == "Mike"


def test_bucket_regex_rejects_sentence_without_verb_phrase():
    # The verb alternation (want to / would love to / hope to / should)
    # is mandatory. A sentence with a name and "with" but no verb must
    # not be misclassified as a bucket-list item.
    assert _BUCKET_RE.search("Sarah with Mike") is None


def test_bucket_regex_swallows_trailing_word_when_capitalized_or_under_ignorecase():
    # Same quirk as the meeting regex: with re.IGNORECASE the optional
    # second-name slot in _NAME is greedy enough to swallow any trailing
    # word, so "with Mike tomorrow" parses as name="Mike tomorrow". Pin
    # the current behavior so a future tightening of the name group
    # (e.g. case-sensitive matching) surfaces here.
    m = _BUCKET_RE.search("Would love to hike with Mike tomorrow.")
    assert m is not None
    assert m.group(2) == "Mike tomorrow"


# ── Relationship regex (_REL_RE) ────────────────────────────────────────────


def test_rel_regex_matches_possessive_branch():
    # Branch 1: "Sarah is Mike's wife" — captures a, b, role1.
    m = _REL_RE.search("Sarah is Mike's wife")
    assert m is not None
    assert m.group("a") == "Sarah"
    assert m.group("b") == "Mike"
    assert m.group("role1") == "wife"
    # Branch 2 named groups stay None on this match.
    assert m.group("c") is None
    assert m.group("d") is None
    assert m.group("role2") is None


def test_rel_regex_matches_are_branch():
    # Branch 2: "Mike and Sarah are siblings" — captures c, d, role2.
    m = _REL_RE.search("Mike and Sarah are siblings")
    assert m is not None
    assert m.group("c") == "Mike"
    assert m.group("d") == "Sarah"
    assert m.group("role2") == "siblings"
    # Branch 1 named groups stay None.
    assert m.group("a") is None
    assert m.group("b") is None
    assert m.group("role1") is None


def test_rel_regex_rejects_unrelated_text():
    # The regex must not match a sentence that merely contains two names.
    assert _REL_RE.search("Sarah met Mike at the park") is None


def test_rel_regex_is_case_insensitive_due_to_ignorecase_flag():
    # Like _PREF_RE, the whole pattern is compiled with re.IGNORECASE,
    # so the [A-Z] anchors in the name groups match lowercase too. The
    # module-level intent of "Names must start with a capital letter"
    # is not actually enforced by this regex. Pin the behavior so a
    # future case-sensitive rewrite is visible in the diff.
    m = _REL_RE.search("sarah is mike's wife")
    assert m is not None
    assert m.group("a") == "sarah"
    assert m.group("b") == "mike"
    assert m.group("role1") == "wife"


# ── _ROLE_TO_TYPE mapping table ──────────────────────────────────────────────


_KNOWN_GROUPS = {"love", "family", "friend", "work"}


def test_role_to_type_keys_cover_all_relationship_strings():
    # The mapping is the bridge between the regex's role tokens and the
    # canonical RELATIONSHIP_TYPES keys in routers/people.py. Any new
    # role string added to the regex must appear here or the writer
    # will silently drop the relationship.
    expected = {"wife", "husband", "partner", "spouse", "spouses",
                "mother", "father", "sister", "brother",
                "siblings", "sibling", "twins",
                "daughter", "son",
                "aunt", "uncle", "cousin", "cousins",
                "niece", "nephew",
                "grandparent", "grandchild",
                "friend", "friends",
                "boss", "mentor", "colleague", "colleagues"}
    assert expected.issubset(set(_ROLE_TO_TYPE.keys()))


def test_role_to_type_values_are_well_formed():
    # Every entry must be a 2-tuple of (rel_type, rel_group) strings,
    # and rel_group must be one of the four known groups.
    for role, mapping in _ROLE_TO_TYPE.items():
        assert isinstance(mapping, tuple)
        assert len(mapping) == 2
        rel_type, rel_group = mapping
        assert isinstance(rel_type, str) and rel_type
        assert isinstance(rel_group, str) and rel_group in _KNOWN_GROUPS, (
            f"{role!r} maps to unknown group {rel_group!r}"
        )


def test_role_to_type_family_roles_share_family_group():
    # Family roles must all land in the "family" group so the work/
    # personal inference in _write_relationship picks the right context.
    family_roles = {"mother", "father", "sister", "brother", "siblings",
                    "sibling", "twins", "daughter", "son",
                    "aunt", "uncle", "cousin", "cousins",
                    "niece", "nephew", "grandparent", "grandchild"}
    for role in family_roles:
        assert role in _ROLE_TO_TYPE, f"{role!r} missing from _ROLE_TO_TYPE"
        assert _ROLE_TO_TYPE[role][1] == "family", role


def test_role_to_type_love_roles_share_love_group():
    love_roles = {"wife", "husband", "partner", "spouse", "spouses"}
    for role in love_roles:
        assert role in _ROLE_TO_TYPE, f"{role!r} missing from _ROLE_TO_TYPE"
        assert _ROLE_TO_TYPE[role][1] == "love", role


def test_role_to_type_work_roles_share_work_group():
    work_roles = {"boss", "mentor", "colleague", "colleagues"}
    for role in work_roles:
        assert role in _ROLE_TO_TYPE, f"{role!r} missing from _ROLE_TO_TYPE"
        assert _ROLE_TO_TYPE[role][1] == "work", role


def test_role_to_type_friend_role_in_friend_group():
    # The single "friend" group is the catch-all for non-kin, non-work
    # social connections. Both singular and plural forms must resolve.
    for role in ("friend", "friends"):
        assert role in _ROLE_TO_TYPE
        assert _ROLE_TO_TYPE[role][1] == "friend"
        assert _ROLE_TO_TYPE[role][0] == "friend"


def test_role_to_type_singular_and_plural_variants_collapse():
    # The call site in process_text does ``role.rstrip("s")`` for the
    # are-branch, so the dict must accept both the singular ("sibling")
    # and the regex's plural form ("siblings") and resolve to the same
    # canonical rel_type. Same goes for "friend" / "friends",
    # "colleague" / "colleagues", etc.
    pairs = [("sibling", "siblings"),
             ("friend", "friends"),
             ("colleague", "colleagues"),
             ("spouse", "spouses"),
             ("cousin", "cousins"),
             ("twin", "twins")]  # "twins".rstrip("s") == "twin" must resolve too
    for singular, plural in pairs:
        assert singular in _ROLE_TO_TYPE, f"{singular!r} missing"
        assert plural in _ROLE_TO_TYPE, f"{plural!r} missing"
        assert _ROLE_TO_TYPE[singular][0] == _ROLE_TO_TYPE[plural][0], (
            (singular, plural)
        )


def test_are_branch_role2_tokens_resolve_after_rstrip():
    # Regression for a live drop-bug: process_text resolves the are-branch
    # role via ``role.rstrip("s")`` before looking it up in _ROLE_TO_TYPE.
    # Every plural alternative the _REL_RE role2 group can capture must
    # therefore have its rstripped form present in the table, or the
    # relationship is silently dropped (this is exactly what happened for
    # "twins" -> "twin"). Enumerate the regex's role2 tokens and pin it.
    role2_tokens = [
        "siblings", "partners", "friends", "colleagues",
        "spouses", "twins", "cousins",
    ]
    for token in role2_tokens:
        looked_up = token.lower().rstrip("s")
        assert looked_up in _ROLE_TO_TYPE, (
            f"are-branch token {token!r} -> {looked_up!r} is not in _ROLE_TO_TYPE; "
            "the relationship would be silently dropped"
        )
