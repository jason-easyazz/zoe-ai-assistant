"""ZOE_MIRROR_GUARD (mirror_guard.py): drop a reflective-listening opener ("It sounds like ...") when something of substance follows.

What is pinned (synthetic phrasings only, ci_safe):
  * the pure ``trim``: a mirror sentence followed by substance goes; a mirror sentence followed by NOTHING or by a generic question
    ("How are you holding up?") is left alone (trimming it would leave something worse); a positive reaction ("sounds like a great
    plan") and a mirror that is not the FIRST sentence are never touched; a mirror sentence that carries its own substance after a
    colon / dash / contrast word keeps that substance;
  * the stream form equals the pure form for every way of cutting a reply into deltas (the A/B scores the pure form);
  * tool / thinking sentinels pass at once and the inner stream is closed; a reply that cannot become a mirror is released on its first
    delta (no first-sound latency), one that might is held for a word or two;
  * the words are DATA: no formula in the module source, a language with no lexicon entry is inert (never guessed from English);
  * modes: ``off`` wraps nothing, ``shadow`` (the default) is byte-identical and logs labels only (never reply text), ``on`` trims -
    through the real seam (``run_flue_brain_streaming``) with wire-1 doubles;
  * NEGATIVE CONTROLS: break the matcher / the substance rule / the closer rule and the tests go red (see the ``mutated`` tests).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging

import pytest

import mirror_guard as mg
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

GOOD = "Late buses are the worst, so ask if they can move it to tomorrow."


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ZOE_MIRROR_GUARD", "ZOE_STRIP_NARRATION", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE", "ZOE_TRIVIA_HEDGE", "ZOE_CLEAN_GOODBYE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    real_lexicon = mg.lexicon
    real_lexicon.cache_clear()
    yield
    monkeypatch.undo()          # a test may have replaced ``mg.lexicon``: put the real cached one back before clearing it
    real_lexicon.cache_clear()


def _run(coro):
    return asyncio.run(coro)


# -- the mode ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    (None, "shadow"), ("", "shadow"), ("shadow", "shadow"), ("banana", "shadow"),
    ("off", "off"), ("0", "off"), ("false", "off"), ("NO", "off"),
    ("on", "on"), ("1", "on"), ("true", "on"), ("enforce", "on"), (" ON ", "on"),
])
def test_mode_reads_the_flag_per_call(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("ZOE_MIRROR_GUARD", raising=False)
    else:
        monkeypatch.setenv("ZOE_MIRROR_GUARD", raw)
    assert mg.mode() == want


# -- the pure trim ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("reply,want", [
    # a mirror sentence followed by substance: the mirror goes, the first letter is capitalised
    ("It sounds like you had a frustrating morning. " + GOOD, GOOD),
    ("That sounds like such an exhausting week. Honestly, the fix is a proper lunch break.", "Honestly, the fix is a proper lunch break."),
    ("Oh wow, it seems like you've got a lot on. Friday's deadline is the real problem; push it.", "Friday's deadline is the real problem; push it."),
    ("I hear you. Pasta freezes fine if you undercook it a minute.", "Pasta freezes fine if you undercook it a minute."),
    ("So you're saying the tap leaks. Which tap is it?", "Which tap is it?"),
    ("That must be so hard. That sounds like a lot to carry. Try writing the list down tonight.", "Try writing the list down tonight."),   # two stacked
    # a mirror sentence that carries its own substance: keep the substance, drop the formula's clause
    ("It sounds like a long day: here is a trick for the stubborn tap.", "Here is a trick for the stubborn tap."),
    ("I hear you - say it slower and pause between the lists.", "Say it slower and pause between the lists."),
    ("It seems like you're tired, but a short walk usually clears the head.", "A short walk usually clears the head."),
])
def test_a_mirror_opener_is_dropped_when_substance_follows(reply, want):
    assert mg.trim(reply) == want
    assert mg.opens_with_mirror(reply)


@pytest.mark.parametrize("reply", [
    # nothing follows / only a generic question follows: trimming would leave something worse, so the reply is left alone
    "It sounds like you had a frustrating morning.",
    "That sounds like such an exhausting experience. How are you holding up?",
    "It sounds like a lot. Do you want to talk about it?",
    "I hear what you're saying. How are you feeling right now?",
    "It sounds like a lot",                              # unterminated
    # not a mirror at all
    GOOD,
    "I'm sorry you've had a rough day. Want a quick idea for dinner?",
    "That sounds hard, but a short walk usually helps.",   # 'sounds hard' is the brief acknowledgement we WANT
    "Tough one. Ask if they can move it to tomorrow.",
    # a positive reaction is not a mirror
    "That sounds like a great plan! Take a coat.",
    "Sounds like a plan. I'll set the timer for ten minutes.",
    "It sounds like fun, and you can pick the music.",
    "That sounds like a really lovely idea. Bring a jumper.",
    # a mirror that is not the FIRST sentence is the author's choice of structure: never touched
    "Octopuses have three hearts. It sounds like you'd enjoy the weird facts, so here is another one about squid.",
    # a very long 'mirror' sentence carries content of its own
    "It sounds like " + "you have been dealing with a great deal of things all at once " * 4 + "so rest. Ask for help tomorrow morning.",
    "",
    "   ",
])
def test_left_alone(reply):
    assert mg.trim(reply) == reply


def test_the_metric_counts_the_opener_whatever_follows():
    assert mg.opens_with_mirror("That sounds like such an exhausting experience. How are you holding up?")
    assert mg.opens_with_mirror("It sounds like you had a long day.")
    assert not mg.opens_with_mirror(GOOD)
    assert not mg.opens_with_mirror("That sounds like a great plan!")
    assert not mg.opens_with_mirror("")
    assert not mg.opens_with_mirror("Octopuses have three hearts. It sounds like you'd enjoy that.")


def test_a_specific_question_after_the_mirror_is_substance_but_a_generic_one_is_not():
    assert mg.trim("It sounds like a big day. What time is your flight tomorrow?") == "What time is your flight tomorrow?"
    assert mg.trim("It sounds like a big day. How are you feeling?") == "It sounds like a big day. How are you feeling?"
    # 'how are you feeling' + a specific object is a specific question
    assert mg.trim("It sounds like a big day. How are you feeling about the interview at nine?") == "How are you feeling about the interview at nine?"


def test_spanish_has_its_own_lexicon_and_german_is_inert():
    assert mg.trim("Parece que estás cansado. Mañana descansa temprano.", "es") == "Mañana descansa temprano."
    assert mg.trim("Suena a que fue un día largo. Pide que lo muevan a mañana.", "es") == "Pide que lo muevan a mañana."
    assert mg.trim("Parece que estás cansado. ¿Cómo te sientes?", "es") == "Parece que estás cansado. ¿Cómo te sientes?"
    assert mg.trim("Eso suena genial. Llévate un abrigo.", "es") == "Eso suena genial. Llévate un abrigo."
    text = "Es klingt, als hättest du einen langen Tag gehabt. Frag, ob sie es auf morgen verschieben."
    assert mg.lexicon("de") is None and mg.trim(text, "de") == text      # no entry => no guard, never English guessed
    assert not mg.opens_with_mirror(text, "de")


def test_the_words_are_data_not_code():
    """No string CONSTANT in the module (docstrings aside) holds a mirror formula, a filler or a closer: they live in lexicons_data."""
    tree = ast.parse(inspect.getsource(mg))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    consts = [n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    for phrase in ("sounds like", "i hear", "it seems", "must be", "how are you", "parece que", "but", "however"):
        assert not [c for c in consts if phrase in c], phrase


def test_every_lexicon_entry_is_normalised_and_consistent():
    import lexicons

    for lang in lexicons.LANGS:
        entry = (lexicons.load(lang) or {}).get(mg.LEXICON_KEY)
        if not entry:
            continue
        lex = mg.lexicon(lang)
        assert lex is not None and lex.openers and lex.closers and lex.fillers, lang
        for key in ("fillers", "openers", "positive_exempt", "generic_closers", "intensifiers"):
            for item in entry[key]:     # already in the form it is matched in: lower-case, accent-free, no punctuation but inner apostrophes
                assert item and " ".join(mg.norm_words(item)) == item, (lang, key, item)
        # an exemption must not be an opener of its own, and no opener may BE a positive phrase
        assert not set(lex.positive) & set(lex.openers), lang


# -- the stream form --------------------------------------------------------------------------------------
REPLIES = [
    "It sounds like you had a frustrating morning. " + GOOD,
    "That sounds like such an exhausting experience. How are you holding up?",
    "That sounds like a great plan! Take a coat.",
    "Oh wow, it seems like you've got a lot on. Friday's deadline is the real problem; push it.",
    "I hear you - say it slower and pause between the lists.",
    "Sounds like you're saying a few of these words, which is odd. What did I get wrong?",
    "It seems like you're tired, but a short walk usually clears the head.",
    "That must be so hard. That sounds like a lot to carry. Try writing the list down tonight.",
    "Octopuses have three hearts and blue blood, which is why they are so odd.",
    "I'm sorry you've had a rough day. Want a quick idea for dinner?",
    "It sounds like a lot",
    "Hi!",
]


@pytest.mark.parametrize("text", REPLIES)
@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13, 400])
def test_stream_equals_pure(text, size):
    st = mg.MirrorStream(mg.lexicon("en"))
    out = "".join(st.feed(text[i:i + size]) for i in range(0, len(text), size)) + st.finish()
    assert out == mg.trim(text)


@pytest.mark.parametrize("text", REPLIES)
def test_stream_equals_pure_at_every_cut(text):
    for k in range(1, len(text)):
        st = mg.MirrorStream(mg.lexicon("en"))
        assert st.feed(text[:k]) + st.feed(text[k:]) + st.finish() == mg.trim(text), (text, k)


def test_a_reply_that_cannot_be_a_mirror_is_released_on_its_first_delta():
    st = mg.MirrorStream(mg.lexicon("en"))
    assert st.feed("Octo") == "Octo"                   # no opener starts with 'octo'
    assert st.feed("puses have three hearts.") == "puses have three hearts."
    st = mg.MirrorStream(mg.lexicon("en"))
    assert st.feed("Late buses are the worst.") == "Late buses are the worst."


def test_a_reply_that_might_become_a_mirror_is_held_for_a_word_or_two_only():
    st = mg.MirrorStream(mg.lexicon("en"))
    assert st.feed("It") == "" and st.feed(" sounds") == ""                       # still a possible opener
    assert st.feed(" like you had a hard one. ") == ""                              # whole mirror sentence, nothing after it yet
    assert st.feed("Ask if they can move it. ") == "Ask if they can move it. "
    st = mg.MirrorStream(mg.lexicon("en"))
    assert st.feed("It") == "" and st.feed(" was a long one, wasn't it.") == "It was a long one, wasn't it."   # diverged: released whole


def test_shadow_stream_holds_nothing_and_changes_nothing_but_records_the_verdict():
    text = "It sounds like you had a frustrating morning. " + GOOD
    st = mg.MirrorStream(mg.lexicon("en"), shadow=True)
    out = "".join(st.feed(text[i:i + 4]) for i in range(0, len(text), 4)) + st.finish()
    assert out == text
    assert st.decision is not None and st.decision.opener and st.decision.trimmed        # what 'on' WOULD have done


async def _collect(agen):
    return [d async for d in agen]


def test_sentinels_pass_at_once_and_the_inner_stream_is_closed(monkeypatch):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")
    closed = []

    async def src():
        try:
            yield "__THINKING__:hm"
            yield "It sounds like you had a frustrating morning. "
            yield '__TOOL__:{"phase": "start", "id": "1", "name": "recall_memory"}'
            yield GOOD
        finally:
            closed.append(True)

    out = _run(_collect(mg.filter_stream(src(), "my bus was late")))
    assert out[0] == "__THINKING__:hm" and out[1].startswith("__TOOL__:")
    assert "".join(out[2:]) == GOOD and closed == [True]


def test_off_and_unknown_language_pass_the_stream_through_untouched(monkeypatch):
    text = "It sounds like you had a frustrating morning. " + GOOD

    async def src():
        yield text[:20]
        yield text[20:]

    monkeypatch.setenv("ZOE_MIRROR_GUARD", "off")
    assert _run(_collect(mg.filter_stream(src(), "my bus was late"))) == [text[:20], text[20:]]
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")
    german = "Mein Bus hatte heute Morgen wieder einmal Verspätung, und ich habe das Meeting verpasst, weil ich nicht anders konnte."
    assert _run(_collect(mg.filter_stream(src(), german))) == [text[:20], text[20:]]    # a German turn: no lexicon, no guard


def test_the_log_carries_labels_only_never_the_reply(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "shadow")
    secret = "Zebrafinch"

    async def src():
        yield f"It sounds like you had a frustrating {secret} morning. "
        yield GOOD

    with caplog.at_level(logging.INFO, logger="mirror_guard"):
        out = "".join(_run(_collect(mg.filter_stream(src(), "my bus was late"))))
    assert out == f"It sounds like you had a frustrating {secret} morning. {GOOD}"
    assert "MIRROR_GUARD mode=shadow lang=en opener=1 would_trim=1" in caplog.text
    assert secret not in caplog.text and "frustrating" not in caplog.text


# -- the seam, end to end (wire-1 doubles) ----------------------------------------------------------------
class _Resp:
    def __init__(self, text):
        self._t = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": self._t}}


def _client(reply, captured):
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return _Resp(reply)
    return _C


@pytest.fixture
def seam(monkeypatch):
    import httpx

    async def turn(message, reply):
        cap = {}
        monkeypatch.setattr(httpx, "AsyncClient", _client(reply, cap))
        out = [c async for c in zc.run_flue_brain_streaming(message, "s1", "jason")]
        return "".join(out), json.loads(cap["content"])["message"]
    return turn


MIRRORED = "It sounds like you had a frustrating morning. " + GOOD


def test_seam_default_is_shadow_and_byte_identical(seam):
    out, sent = _run(seam("My bus was late and I missed my meeting", MIRRORED))
    assert out == MIRRORED and sent == " zoe-uid:jason\nMy bus was late and I missed my meeting"


def test_seam_off_does_not_even_build_the_wrapper(seam, monkeypatch):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "off")

    def boom(*a, **k):
        raise AssertionError("the guard wrapped the stream with the flag off")

    monkeypatch.setattr(mg, "filter_stream", boom)
    out, _ = _run(seam("My bus was late and I missed my meeting", MIRRORED))
    assert out == MIRRORED


def test_seam_on_trims_and_leaves_the_wire_alone(seam, monkeypatch):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")
    out, sent = _run(seam("My bus was late and I missed my meeting", MIRRORED))
    assert out == GOOD and sent == " zoe-uid:jason\nMy bus was late and I missed my meeting"      # the prompt side is untouched


def test_seam_on_leaves_a_mirror_plus_generic_question_alone(seam, monkeypatch):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")
    reply = "That sounds like such an exhausting experience. How are you holding up?"
    out, _ = _run(seam("My dad is in hospital for a few days", reply))
    assert out == reply


def test_seam_on_a_spanish_turn_uses_the_spanish_words(seam, monkeypatch):
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")
    out, _ = _run(seam("Mi autobús llegó tarde y perdí la reunión de esta mañana", "Parece que fue una mañana difícil. Pide que la muevan a mañana."))
    assert out == "Pide que la muevan a mañana."


# -- negative controls: break the fix => red --------------------------------------------------------------
def test_mutated_matcher_trims_nothing(monkeypatch):
    assert mg.trim(MIRRORED) == GOOD
    monkeypatch.setattr(mg, "is_mirror_sentence", lambda *a, **k: False)
    assert mg.trim(MIRRORED) == MIRRORED


def test_mutated_substance_rule_would_empty_a_lone_mirror(monkeypatch):
    """With the 'generic closer' rule broken, 'mirror + generic question' is trimmed down to the bare generic question - the very result the
    rule exists to prevent. This test is green only while the real rule holds (the mutated one makes the assertion above fail)."""
    reply = "That sounds like such an exhausting experience. How are you holding up?"
    assert mg.trim(reply) == reply
    monkeypatch.setattr(mg, "is_generic_closer", lambda *a, **k: False)
    assert mg.trim(reply) == "How are you holding up?"


def test_mutated_positive_exemption_would_trim_good_news(monkeypatch):
    assert mg.trim("That sounds like a great plan! Take a coat.") == "That sounds like a great plan! Take a coat."
    lex = mg.lexicon("en")
    monkeypatch.setattr(mg, "lexicon", lambda lang: type(lex)(**{**lex.__dict__, "positive": ()}))
    assert mg.trim("That sounds like a great plan! Take a coat.") == "Take a coat."
