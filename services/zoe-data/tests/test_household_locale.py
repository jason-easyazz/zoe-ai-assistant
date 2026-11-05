"""household_locale - the household's conventions line (ZOE_LOCALE_HINT, default on): data-driven, never guessed.

Pinned: the country comes from ZOE_HOUSEHOLD_COUNTRY / ZOE_LOCATION_COUNTRY (code or name; UK -> GB), else an EXPLICIT
ZOE_TIMEZONE (never the code default), else nothing; every shipped country renders, within budget, with no tool-group trigger word
(the line rides in the user message) and no bracket (it sits inside a delimited block); the distress hand-off reads the same
explicit country; the Flue seam puts the line in the "[Today — household context]" block on every turn and leaves the bytes alone
when it is off or no country is set.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

pytestmark = pytest.mark.ci_safe  # env + a data file only

import household_locale as hl

_ENV = ("ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_TIMEZONE", "ZOE_LOCALE_HINT", "ZOE_LOCALE_FILE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    hl._load.cache_clear()
    yield
    hl._load.cache_clear()


def _countries() -> list[str]:
    return sorted(json.loads(hl.DATA_FILE.read_text(encoding="utf-8"))["countries"])


def test_nothing_set_means_no_line():
    assert hl.country() == "" and hl.prompt_line() == ""


@pytest.mark.parametrize("raw, want", [("AU", "AU"), ("au", "AU"), ("Australia", "AU"), ("UK", "GB"), ("United Kingdom", "GB")])
def test_explicit_country(monkeypatch, raw, want):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", raw)
    assert hl.explicit_country() == want == hl.country()


def test_location_country_is_the_fallback(monkeypatch):
    monkeypatch.setenv("ZOE_LOCATION_COUNTRY", "NZ")
    assert hl.country() == "NZ"
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "IE")
    assert hl.country() == "IE"


@pytest.mark.parametrize("zone, want", [("Australia/Perth", "AU"), ("Australia/Sydney", "AU"), ("America/Chicago", "US"),
                                        ("America/Indiana/Knox", "US"), ("Europe/London", "GB"), ("Pacific/Auckland", "NZ"),
                                        ("Europe/Berlin", ""), ("Asia/Tokyo", "")])
def test_explicit_timezone_is_the_last_resort(monkeypatch, zone, want):
    monkeypatch.setenv("ZOE_TIMEZONE", zone)
    assert hl.country() == want
    assert bool(hl.prompt_line()) == bool(want)       # a zone with no data entry gets no line, never another country's


def test_explicit_country_beats_the_timezone(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "US")
    assert hl.country() == "US"
    assert "Fahrenheit" in hl.prompt_line()


def test_australia_line_says_metric_celsius_and_australian_spelling(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    line = hl.prompt_line()
    assert line.startswith("This household is in Australia:")
    for want in ("metric", "Celsius", "Australian English", "flavour", "capsicum"):
        assert want in line
    assert "Fahrenheit" not in line


def test_us_line_negative_control(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "US")
    line = hl.prompt_line()
    assert "Fahrenheit" in line and "American English" in line and "Celsius" not in line


def test_unknown_country_gets_no_line(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "ZZ")
    assert hl.prompt_line() == ""


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    monkeypatch.setenv("ZOE_LOCALE_HINT", "0")
    assert hl.prompt_line() == ""


@pytest.mark.parametrize("code", _countries())
def test_every_shipped_country_renders_safely(code):
    from identity_facts import _TRIGGER_RE   # the mirror of the sidecar's tool-group triggers (pinned against tool-groups.ts)

    line = hl.render(code)
    assert line and len(line) <= hl.MAX_LINE_CHARS
    assert not _TRIGGER_RE.search(line), f"{code}: a tool-group trigger word would arm a group on every turn"
    # groups the identity_facts mirror does not carry (cross-review N1): alarms, the people / memory phrasings
    assert not re.search(r"\balarms?\b|\bwake (?:me|us)\b|\bwho\s+is\b|\bremember\b|\bremind", line, re.IGNORECASE), code
    assert "[" not in line and "]" not in line


def test_override_file_wins(monkeypatch, tmp_path):
    f = tmp_path / "locale.json"
    f.write_text(json.dumps({"countries": {"AU": {"name": "Testland", "units": "u", "heat": "h", "spelling": "s",
                                                  "spelling_examples": "e", "terms": "t"}}}), encoding="utf-8")
    monkeypatch.setenv("ZOE_LOCALE_FILE", str(f))
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    assert hl.prompt_line().startswith("This household is in Testland:")


def test_distress_reads_the_same_explicit_country_and_never_the_timezone(monkeypatch):
    import distress_handoff

    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    assert distress_handoff.config().country == ""        # no crisis number is guessed from a timezone
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "Australia")
    assert distress_handoff.config().country == "AU"


# -- the seam ----------------------------------------------------------------------------------------------

import zoe_flue_client as zc  # noqa: E402


def test_seam_adds_the_line_to_the_identity_block(monkeypatch):
    from identity_facts import BLOCK_CLOSE, BLOCK_OPEN

    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    ident = f"{BLOCK_OPEN}\nYou are talking to Alex, a member of this household in Townsville.\n{BLOCK_CLOSE}"
    out = zc._with_household_locale(ident)
    assert out == f"{BLOCK_OPEN}\nYou are talking to Alex, a member of this household in Townsville.\n{hl.prompt_line()}\n{BLOCK_CLOSE}"
    # no identity (a guest): the line gets a block of its own, same family (elided from history like the rest)
    assert zc._with_household_locale("") == f"{BLOCK_OPEN}\n{hl.prompt_line()}\n{BLOCK_CLOSE}"


def test_seam_bytes_unchanged_without_a_country_or_with_the_flag_off(monkeypatch):
    from identity_facts import BLOCK_CLOSE, BLOCK_OPEN

    ident = f"{BLOCK_OPEN}\nYou are talking to Alex.\n{BLOCK_CLOSE}"
    assert zc._with_household_locale(ident) == ident and zc._with_household_locale("") == ""
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    monkeypatch.setenv("ZOE_LOCALE_HINT", "off")
    assert zc._with_household_locale(ident) == ident and zc._with_household_locale("") == ""


def test_identity_context_block_carries_the_line(monkeypatch):
    import identity_facts

    async def no_identity(uid):
        return ""

    monkeypatch.setattr(identity_facts, "identity_block", no_identity)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    block = asyncio.run(zc._identity_context_block("guest"))
    assert "Celsius" in block and block.startswith("[Today")
