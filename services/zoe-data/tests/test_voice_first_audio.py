"""_extract_first_unit pulls the first speakable chunk out ASAP for fast first audio.

Time-to-first-audio is what makes a voice reply feel fast. This snaps the first
chunk out on a clause boundary (or a soft word-boundary cap) instead of waiting for
a full sentence — without splitting decimals ('twelve point four') mid-number.
"""
import routers.voice_tts as v


def test_flag_default_on_and_togglable(monkeypatch):
    monkeypatch.delenv("ZOE_VOICE_FAST_FIRST_AUDIO", raising=False)
    assert v._fast_first_audio_enabled() is True   # default on
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("ZOE_VOICE_FAST_FIRST_AUDIO", off)
        assert v._fast_first_audio_enabled() is False, off
    for on in ("1", "true", "YES"):
        monkeypatch.setenv("ZOE_VOICE_FAST_FIRST_AUDIO", on)
        assert v._fast_first_audio_enabled() is True, on


def test_breaks_on_first_clause_comma():
    unit, rest = v._extract_first_unit("It's twelve degrees and clear, with a light breeze.")
    assert unit == "It's twelve degrees and clear,"
    assert rest.strip() == "with a light breeze."


def test_short_sentence_emits_whole():
    unit, rest = v._extract_first_unit("Sure, it's sunny.")
    assert unit == "Sure, it's sunny."
    assert rest.strip() == ""


def test_waits_until_min_chars():
    # Too short to synth a stub — hold for more tokens.
    assert v._extract_first_unit("Hi") == (None, "Hi")


def test_decimal_not_split():
    # The '.' in 12.4 is followed by a digit, not whitespace → must not break there.
    unit, rest = v._extract_first_unit("It's 12.4 degrees")
    assert unit is None  # no clause break yet, under the soft cap
    assert rest == "It's 12.4 degrees"


def test_soft_cap_flushes_long_clause_at_word_boundary():
    # No punctuation but the opening clause is long → flush at a word boundary so
    # audio doesn't stall.
    long_open = "The weather in Geraldton this morning is looking"
    unit, rest = v._extract_first_unit(long_open)
    assert unit and len(unit) <= v._FIRST_UNIT_SOFT_CAP
    assert unit.split()[0] == "The" and not unit.endswith(" ")
    assert (unit + rest).replace("  ", " ").strip() == long_open
