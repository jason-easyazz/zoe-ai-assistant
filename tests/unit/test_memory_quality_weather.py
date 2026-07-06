"""is_storable_fact must reject ephemeral weather reports (a background extractor
scraping an assistant weather reply pollutes recall) WITHOUT rejecting real
personal facts that merely mention a temperature.

Run: pytest tests/unit/test_memory_quality_weather.py -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "zoe-data"))

import memory_quality as mq  # noqa: E402

import pytest
# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe



WEATHER_REPORTS = [
    "It's 17 point 6 degrees and mainly clear in Geraldton, and it feels like 10 degrees.",
    "in Perth like\nIt's 17 point 6 degrees and mainly clear in Geraldton, and it feels like 10 degrees.",
    "it feels like 10 degrees.",
    "It's 30 degrees and sunny outside.",
    "Currently 12 degrees, overcast with a chance of showers.",
    # extra condition words
    "Currently 8 degrees and foggy in Perth.",
    "It's 15 degrees with a light frost.",
    "About 5 degrees and freezing out there.",
]

# Real facts / preferences that mention temperature or "feels like" but are NOT
# weather reports — these must stay storable.
REAL_FACTS = [
    "My mums name is Janice, she was born on the 17/11/1947",
    "User's father's name is Niel.",
    "One of user's sisters is Karen, born on 1/1/1970.",
    "User's name is Jason",
    "I like the house at 20 degrees",
    "My thermostat is set to 21 degrees",
    "I feel like going for a walk",
    "Remember it's 22 degrees and sunny for the wedding",  # explicit command wins
    # first/second-person owned facts that hit the weather pattern but are REAL —
    # the personal-subject guard must let these through.
    "My baby feels like she has 38 degrees fever",
    "My room feels like it's 15 degrees colder than the hall",
]


def test_weather_reports_rejected():
    for text in WEATHER_REPORTS:
        storable, reason = mq.is_storable_fact(text)
        assert not storable, f"weather report should be rejected: {text!r}"
        assert reason == "weather_report", f"unexpected reason {reason!r} for {text!r}"


def test_real_facts_and_temperature_preferences_kept():
    for text in REAL_FACTS:
        storable, reason = mq.is_storable_fact(text)
        assert storable, f"real fact wrongly rejected ({reason}): {text!r}"
