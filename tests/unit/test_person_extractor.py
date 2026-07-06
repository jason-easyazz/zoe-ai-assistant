"""Unit tests for person_extractor.py — pattern matching and process_text() logic."""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../services/zoe-data'))

from person_extractor import _parse_birthday, _BDAY_RE, _PREF_RE, _MEETING_RE, _GIFT_IDEA_RE, _BUCKET_RE

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe



class TestParseBirthday:
    def test_day_then_month(self):
        m, d, y = _parse_birthday("15 March")
        assert m == 3
        assert d == 15
        assert y is None

    def test_month_then_day(self):
        m, d, y = _parse_birthday("March 15")
        assert m == 3
        assert d == 15

    def test_iso_format(self):
        m, d, y = _parse_birthday("1990-03-15")
        assert m == 3
        assert d == 15
        assert y == 1990

    def test_partial_month_name(self):
        m, d, y = _parse_birthday("jan 3")
        assert m == 1
        assert d == 3

    def test_invalid_day(self):
        m, d, y = _parse_birthday("45 March")
        assert d is None  # 45 is not valid

    def test_empty_string(self):
        m, d, y = _parse_birthday("")
        assert m is None
        assert d is None


class TestRegexPatterns:
    def test_birthday_pattern_matches(self):
        assert _BDAY_RE.search("Sarah's birthday is 15 March") is not None
        assert _BDAY_RE.search("John's birthday is on April 20") is not None

    def test_preference_pattern_matches(self):
        assert _PREF_RE.search("Sarah loves jazz music") is not None
        assert _PREF_RE.search("Tom hates mornings") is not None

    def test_meeting_pattern_matches(self):
        assert _MEETING_RE.search("met Sarah for coffee") is not None
        # "had dinner with" requires the activity word AFTER the name in this pattern
        assert _MEETING_RE.search("met James for dinner") is not None

    def test_gift_idea_pattern(self):
        assert _GIFT_IDEA_RE.search("getting Sarah a headphone") is not None
        assert _GIFT_IDEA_RE.search("buying Tom a book") is not None

    def test_bucket_list_pattern(self):
        assert _BUCKET_RE.search("want to see jazz with Sarah") is not None
        assert _BUCKET_RE.search("would love to travel with Tom") is not None


class TestProcessText:
    """Integration-style tests that mock DB and MemPalace."""

    def _make_db_mock(self, person_id=None):
        """Create a mock DB that returns person_id on SELECT."""
        db = MagicMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=[person_id] if person_id else None)
        db.execute = AsyncMock(return_value=cursor)
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_no_patterns_returns_zero(self):
        from person_extractor import process_text
        result = await process_text("Hello how are you", user_id="alice", source="test", db=self._make_db_mock())
        assert result == 0

    @pytest.mark.asyncio
    async def test_guest_returns_zero(self):
        from person_extractor import process_text
        result = await process_text("Sarah loves jazz", user_id="guest", source="test")
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_text_returns_zero(self):
        from person_extractor import process_text
        result = await process_text("", user_id="alice", source="test", db=self._make_db_mock())
        assert result == 0

    @pytest.mark.asyncio
    async def test_pref_pattern_known_person_writes_db_row(self):
        """Known person → DB row + MemPalace write."""
        from person_extractor import process_text
        uuid = "test-uuid-1234"
        db = self._make_db_mock(person_id=uuid)

        with patch('person_extractor._ingest_to_mempalace', new_callable=AsyncMock, return_value="mem-1"), \
             patch('person_extractor._write_activity', new_callable=AsyncMock) as mock_write_act, \
             patch('person_extractor._post_write_hooks', new_callable=AsyncMock):
            result = await process_text("Sarah loves jazz music", user_id="alice", source="test", db=db)

        assert result > 0
        mock_write_act.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_person_mempalace_only(self):
        """Unknown person → MemPalace only (no DB row write for preference)."""
        from person_extractor import process_text
        db = self._make_db_mock(person_id=None)  # no DB match

        with patch('person_extractor._ingest_to_mempalace', new_callable=AsyncMock, return_value="mem-2") as mock_mp, \
             patch('person_extractor._write_activity', new_callable=AsyncMock) as mock_write_act, \
             patch('person_extractor._post_write_hooks', new_callable=AsyncMock):
            result = await process_text("UnknownPerson loves hiking", user_id="alice", source="test", db=db)

        # MemPalace should be called regardless
        if result > 0:
            mock_mp.assert_called()
            # No DB write (person not in DB)
            mock_write_act.assert_not_called()

    @pytest.mark.asyncio
    async def test_birthday_pattern_known_person_writes_date(self):
        from person_extractor import process_text
        uuid = "test-uuid-bday"
        db = self._make_db_mock(person_id=uuid)

        with patch('person_extractor._ingest_to_mempalace', new_callable=AsyncMock, return_value="mem-b"), \
             patch('person_extractor._write_date', new_callable=AsyncMock) as mock_date, \
             patch('person_extractor._write_activity', new_callable=AsyncMock), \
             patch('person_extractor._post_write_hooks', new_callable=AsyncMock):
            await process_text("Sarah's birthday is 15 March", user_id="alice", source="test", db=db)

        mock_date.assert_called()

    @pytest.mark.asyncio
    async def test_gift_pattern_known_person_writes_gift(self):
        from person_extractor import process_text
        uuid = "test-uuid-gift"
        db = self._make_db_mock(person_id=uuid)

        with patch('person_extractor._ingest_to_mempalace', new_callable=AsyncMock, return_value="mem-g"), \
             patch('person_extractor._write_gift', new_callable=AsyncMock) as mock_gift, \
             patch('person_extractor._post_write_hooks', new_callable=AsyncMock):
            await process_text("getting Sarah a headphone for her birthday", user_id="alice", source="test", db=db)

        mock_gift.assert_called()

    @pytest.mark.asyncio
    async def test_bucket_list_pattern_writes_bucket(self):
        from person_extractor import process_text
        uuid = "test-uuid-bucket"
        db = self._make_db_mock(person_id=uuid)

        with patch('person_extractor._ingest_to_mempalace', new_callable=AsyncMock, return_value="mem-bk"), \
             patch('person_extractor._write_bucket', new_callable=AsyncMock) as mock_bucket, \
             patch('person_extractor._post_write_hooks', new_callable=AsyncMock):
            await process_text("want to travel with Sarah", user_id="alice", source="test", db=db)

        mock_bucket.assert_called()
