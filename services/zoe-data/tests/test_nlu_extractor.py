import datetime

import pytest

import nlu_extractor

pytestmark = pytest.mark.ci_safe


class _FixedDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 5, 8)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-5-8", "2026-05-08"),
        ("2026-05-8", "2026-05-08"),
        ("2026-5-08", "2026-05-08"),
        ("2026-05-08", "2026-05-08"),
        ("2026-5", "2026-5"),
        ("2026/5/8", "2026/5/8"),
        ("", ""),
    ],
)
def test_normalize_date_handles_padding_and_non_iso_inputs(raw, expected):
    assert nlu_extractor._normalize_date(raw) == expected


@pytest.mark.unit
def test_today_prefix_formats_friendly_date_and_default_hint(monkeypatch):
    monkeypatch.setattr(nlu_extractor.datetime, "date", _FixedDate)

    assert nlu_extractor._today_prefix() == (
        "Today is Friday, May 8, 2026. If the date is not stated, default to today."
    )