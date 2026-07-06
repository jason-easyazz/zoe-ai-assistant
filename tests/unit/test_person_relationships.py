"""Unit tests for person relationships feature.

Tests:
- _REL_RE regex pattern matching
- RELATIONSHIP_TYPES constant completeness
- ROLE_TO_TYPE mapping correctness
- Relationship endpoint validation helpers
- Context/tier derivation from rel_group
"""

import sys
import os
import re

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/zoe-data'))


class TestRelationshipRegex:
    """Test _REL_RE patterns in person_extractor.py."""

    def setup_method(self):
        from person_extractor import _REL_RE, _ROLE_TO_TYPE
        self._REL_RE = _REL_RE
        self._ROLE_TO_TYPE = _ROLE_TO_TYPE

    def test_wife_pattern(self):
        m = self._REL_RE.search("Sarah is Mike's wife")
        assert m is not None
        assert m.group("a").strip() == "Sarah"
        assert m.group("b").strip() == "Mike"
        assert m.group("role1").lower() == "wife"

    def test_husband_pattern(self):
        m = self._REL_RE.search("James is Anna's husband")
        assert m is not None
        assert m.group("role1").lower() == "husband"

    def test_siblings_pattern(self):
        m = self._REL_RE.search("Tom and Jerry are siblings")
        assert m is not None
        assert m.group("c").strip() == "Tom"
        assert m.group("d").strip() == "Jerry"
        assert "sibling" in m.group("role2").lower()

    def test_partners_pattern(self):
        m = self._REL_RE.search("Alice and Bob are partners")
        assert m is not None

    def test_friend_pattern(self):
        m = self._REL_RE.search("Chris and Dana are friends")
        assert m is not None

    def test_matches_case_insensitive(self):
        # re.IGNORECASE means lowercase also matches — this is intentional
        m = self._REL_RE.search("sarah is mike's wife")
        assert m is not None, "_REL_RE uses IGNORECASE so lowercase should match"

    def test_role_to_type_wife_maps_to_spouse(self):
        assert self._ROLE_TO_TYPE["wife"] == ("spouse", "love")

    def test_role_to_type_boss_maps_to_work(self):
        assert self._ROLE_TO_TYPE["boss"] == ("boss", "work")

    def test_role_to_type_cousin(self):
        assert self._ROLE_TO_TYPE["cousin"] == ("cousin", "family")


class TestRelationshipTypes:
    """Test RELATIONSHIP_TYPES constant from people.py."""

    def setup_method(self):
        from routers.people import RELATIONSHIP_TYPES, _PERSONAL_GROUPS, _WORK_GROUPS
        self.RELATIONSHIP_TYPES = RELATIONSHIP_TYPES
        self._PERSONAL_GROUPS = _PERSONAL_GROUPS
        self._WORK_GROUPS = _WORK_GROUPS

    def test_all_4_groups_present(self):
        for group in ['love', 'family', 'friend', 'work']:
            assert group in self.RELATIONSHIP_TYPES, f"Group '{group}' missing"

    def test_each_entry_has_3_fields(self):
        for group, entries in self.RELATIONSHIP_TYPES.items():
            for entry in entries:
                assert len(entry) == 3, f"Entry {entry} in {group} should be (key, lbl_a, lbl_b)"

    def test_spouse_in_love(self):
        keys = [e[0] for e in self.RELATIONSHIP_TYPES.get('love', [])]
        assert 'spouse' in keys

    def test_parent_in_family(self):
        keys = [e[0] for e in self.RELATIONSHIP_TYPES.get('family', [])]
        assert 'parent' in keys

    def test_colleague_in_work(self):
        keys = [e[0] for e in self.RELATIONSHIP_TYPES.get('work', [])]
        assert 'colleague' in keys

    def test_personal_groups_does_not_include_work(self):
        assert 'work' not in self._PERSONAL_GROUPS

    def test_work_groups_contains_work(self):
        assert 'work' in self._WORK_GROUPS

    def test_context_inference_personal(self):
        for group in self._PERSONAL_GROUPS:
            ctx = 'work' if group in self._WORK_GROUPS else 'personal'
            assert ctx == 'personal', f"Group '{group}' should infer personal context"

    def test_context_inference_work(self):
        for group in self._WORK_GROUPS:
            ctx = 'work' if group in self._WORK_GROUPS else 'personal'
            assert ctx == 'work', f"Group '{group}' should infer work context"


class TestRelLookup:
    """Test the _rel_lookup helper in people.py."""

    def setup_method(self):
        from routers.people import _rel_lookup
        self._rel_lookup = _rel_lookup

    def test_known_type_returns_tuple(self):
        result = self._rel_lookup("spouse")
        assert result is not None
        group, lbl_a, lbl_b = result
        assert group == "love"
        assert lbl_a == "Spouse"
        assert lbl_b == "Spouse"

    def test_parent_returns_asymmetric_labels(self):
        result = self._rel_lookup("parent")
        assert result is not None
        group, lbl_a, lbl_b = result
        assert lbl_a == "Parent"
        assert lbl_b == "Child"

    def test_unknown_type_returns_none(self):
        assert self._rel_lookup("nonexistent_type") is None

    def test_colleague_in_work_group(self):
        result = self._rel_lookup("colleague")
        assert result is not None
        group, _, _ = result
        assert group == "work"


class TestNormTier:
    """Tier normalisation used in both UIs and people.py validation."""

    def test_valid_tiers_pass_through(self):
        from routers.people import _VALID_CIRCLES
        assert "inner" in _VALID_CIRCLES
        assert "circle" in _VALID_CIRCLES
        assert "public" in _VALID_CIRCLES

    def test_old_values_not_in_valid_set(self):
        from routers.people import _VALID_CIRCLES
        for old in ["family", "friends", "work", "acquaintance"]:
            assert old not in _VALID_CIRCLES, f"Old circle value '{old}' should not be valid"

    def test_valid_contexts(self):
        from routers.people import _VALID_CONTEXTS
        assert "personal" in _VALID_CONTEXTS
        assert "work" in _VALID_CONTEXTS
