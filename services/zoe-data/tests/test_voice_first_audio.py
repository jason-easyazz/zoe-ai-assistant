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


def test_short_sentence_is_not_split_mid_clause():
    # A short reply must play as ONE natural utterance — splitting it at the first
    # comma gives Kokoro sentence-final prosody + padding mid-sentence, which sounds
    # broken. Under the clause-min, so it is held whole until the sentence closes.
    assert v._extract_first_unit("It's twelve degrees and clear, with a light breeze.") == (
        None, "It's twelve degrees and clear, with a light breeze.")


def test_complete_short_sentence_emits_whole():
    unit, rest = v._extract_first_unit("Sure, it's sunny. ")
    assert unit == "Sure, it's sunny."
    assert rest.strip() == ""


def test_time_is_never_split_inside_the_number():
    # The colon in 8:05 is followed by a digit, and mid-stream the ':' can sit at the
    # buffer end — neither may trigger a split ("The time is 8:" <pause> "05…").
    assert v._extract_first_unit("The current time is 8:") == (None, "The current time is 8:")
    assert v._extract_first_unit("The current time is 8:05 in the morning.") == (
        None, "The current time is 8:05 in the morning.")  # no trailing space → held for the end flush
    unit, rest = v._extract_first_unit("The current time is 8:05 in the morning. ")
    assert unit == "The current time is 8:05 in the morning."


def test_waits_until_min_chars():
    # Too short to synth a stub — hold for more tokens.
    assert v._extract_first_unit("Hi") == (None, "Hi")


def test_decimal_not_split():
    # The '.' in 12.4 is followed by a digit, not whitespace → must not break there.
    unit, rest = v._extract_first_unit("It's 12.4 degrees")
    assert unit is None  # no clause break yet, under the soft cap
    assert rest == "It's 12.4 degrees"


def test_long_opening_still_clause_breaks_for_first_audio():
    # A genuine paragraph (no sentence end for a long time) must still start fast —
    # clause-break once past the clause-min so first-audio doesn't stall.
    long_open = ("Honey is one of the very few foods that never spoils, and archaeologists "
                 "have even found edible pots of it. ")
    unit, rest = v._extract_first_unit(long_open)
    assert unit == "Honey is one of the very few foods that never spoils,"
    assert rest.strip().startswith("and archaeologists")


def test_soft_cap_flushes_long_unpunctuated_opening_at_word_boundary():
    # No punctuation at all but the opening is very long → flush at a word boundary
    # so audio doesn't stall behind a run-on clause.
    long_open = "The weather across the whole midwest region this particular morning is looking really"
    unit, rest = v._extract_first_unit(long_open)
    assert unit and len(unit) <= v._FIRST_UNIT_SOFT_CAP
    assert unit.split()[0] == "The" and not unit.endswith(" ")
    assert (unit + rest).replace("  ", " ").strip() == long_open
