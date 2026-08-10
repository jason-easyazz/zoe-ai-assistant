"""Wake-word stripping for Moonshine per-line transcripts (the #854 wake-bleed fix).

Pure mechanics extracted verbatim from routers/voice_tts.py: the wake-word
line/prefix/greeting-homophone regexes and _strip_wake_word(). The transcribe
path that calls this stays in routers/voice_tts.py.
"""
import re


# ── Wake-word bleed fix ──────────────────────────────────────────────────────
# The panel sends the WHOLE captured utterance — wake word ("Hey Zoe") + pre-roll
# included — with no wake-offset metadata. Transcribing the leading wake word
# corrupts the command ("Hey Zoe, what time is it" -> "Hey Zoe Tom is it"). We fix
# this server-side using Moonshine's OWN line segmentation: it naturally emits the
# wake phrase as its own line ("Hey Zoe.") separate from the command line, so we
# drop leading wake-only lines and, if the wake word landed inline on the command
# line, strip just that leading prefix. This NEVER cuts the raw audio, so command
# words can't be clipped — the guard is the replay corpus (tests/replay_samples.py).

# A line that is ENTIRELY a wake-word variant is dropped — but ONLY on the right
# terms, split across two matchers so a bare weak homophone that is really the
# user's first word ("So, add milk to my list") is never silently deleted:
#   * _STRONG_WAKE_LINE_RE (defined below) — a DISTINCTIVE zoe-family name, so a
#     bare greeting-less wake-only line strips only on it ("Zoe.", "Hey zoe").
#   * _GREETING_WEAK_WAKE_LINE_RE (below) — weak homophones (so/a/zo/joe/joey/
#     josie) strip as a whole line ONLY when a REQUIRED greeting proves it is a
#     real wake ("hey so", "ok zo"), mirroring _WAKE_GREETING_NAME_RE inline.
# A leading wake prefix on a line that ALSO carries the command (strip the prefix
# only). Requires a following word (?=\S) so a bare name that IS the command stays.
_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay)?[\s,]*"
    # Inline strip uses ONLY unambiguous non-word wake variants. Real names/words
    # (joe, joey, josie, so) are deliberately EXCLUDED here — as an inline prefix
    # they would corrupt a real command ("Joe wants the weather", "so, add milk").
    # As a whole wake-only line they are caught ONLY with a leading greeting
    # (_GREETING_WEAK_WAKE_LINE_RE); a bare one is the user's word and is kept.
    r"(?:zoe|zoey|zoie|zoee|zo|sewey)"
    r"[\s,.!?-]+(?=\S)",
    re.IGNORECASE,
)
# Ambiguous homophones (joe/joey/josie — real names) are treated as an inline wake
# bleed ONLY when preceded by a REQUIRED greeting ("hey joey, show me my lists" ->
# strip). A bare homophone with no greeting ("Joe wants the weather") has no match
# and is left intact, so a real command subject is never cut.
_WAKE_GREETING_NAME_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay)[\s,]+"
    r"(?:joe|joey|josie)"
    r"[\s,.!?-]+(?=\S)",
    re.IGNORECASE,
)

# ── Pre-roll bleed: the wake phrase is rarely a single clean leading line ────
# The panel prepends ~1.6s of PRE-ROLL audio to every wake-triggered capture
# (`PREROLL_CHUNKS=20` x 1280 @ 16k in scripts/setup/zoe_voice_daemon.py). That
# window deliberately reaches back to BEFORE "Hey" — at 12 chunks it opened
# mid-phrase and ate the command onset (#1326) — so the wake word AND whatever
# the room was doing beforehand are both inside the clip. Two consequences, both
# measured on the operator's corpus, defeat the leading-wake-only-line drop:
#
#   (a) SPLIT — Moonshine breaks the wake phrase across a line boundary:
#       ["Hey", "Zoe. Show me my contacts."]. "Hey" is not a wake-only line, so
#       the drop never fires and the whole wake phrase reaches the brain.
#   (b) FILLER — the pre-roll catches a breath/cough/"yeah" that Moonshine emits
#       as its own line BEFORE the wake line: ["Yeah.", "Hey Zoe.", "Show me my
#       calendar."]. The wake line is no longer leading, so the drop never fires.
#
# Both are fixed by SELECTING lines, never by cutting audio: per the note on
# _prepare_audio_for_moonshine, trimming samples regressed as many clips as it
# fixed, so the audio still reaches Moonshine untouched.
_BARE_GREETING_RE = re.compile(r"^\s*(?:hey|hi|ok|okay)\s*[,.!?…]*\s*$", re.IGNORECASE)
# The continuation of a SPLIT wake phrase: the line must START with an
# unambiguous wake variant. The ambiguous real-name homophones (joe/joey/josie)
# are excluded here for the same reason they are excluded from _WAKE_PREFIX_RE —
# "Hey" / "Joe wants the weather" must not lose its subject.
_WAKE_NAME_START_RE = re.compile(r"^\s*(?:zoe|zoey|zoie|zoee|zo|sewey)\b", re.IGNORECASE)
# Pre-roll junk that Moonshine renders as a short standalone line. Whole-line
# match only (the `$` anchor), so a real command that merely STARTS with one of
# these words ("So, add milk to my list") can never be treated as filler.
_FILLER_LINE_RE = re.compile(
    r"^\s*(?:yeah|yep|yes|no|um+|uh+|ah+|oh+|mm+|hmm+|huh|er+|so|well|right)"
    r"\s*[,.!?…]*\s*$",
    re.IGNORECASE,
)
# How far past a filler line we will look for the real wake line. Bounded so a
# genuine multi-line command can never be consumed by a runaway scan.
_MAX_FILLER_LOOKAHEAD = 2
# A bare, greeting-less wake-only line strips only on a DISTINCTIVE zoe-family
# name — a weak homophone ("so", "zo", "a") on its own is the user's real first
# word, never a wake. This is ALSO the confident matcher the filler lookahead
# demands: skipping a filler reaches PAST it, so a stray "so" must not authorise
# eating the line before it ("Well" / "so" / "what now").
_STRONG_WAKE_LINE_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay)?[\s,]*"
    r"(?:zoe|zoey|zoie|zoee|sewey)"
    r"[\s,.!?]*$",
    re.IGNORECASE,
)
# Weak homophones strip as a whole wake-only line ONLY when a REQUIRED greeting
# proves the line is a real wake ("hey so", "ok zo") — mirroring the inline
# greeting+name rule (_WAKE_GREETING_NAME_RE). A bare weak line ("So", "Joe",
# "A") matches neither this nor _STRONG_WAKE_LINE_RE and is kept intact.
_GREETING_WEAK_WAKE_LINE_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay)[\s,]+"
    r"(?:so|a|zo|joe|joey|josie)"
    r"[\s,.!?]*$",
    re.IGNORECASE,
)


def _wake_line_at(kept: list, i: int, strong: bool = False) -> bool:
    """True when line ``i`` is a wake word — either wholly (a distinctive
    zoe-family name, or a greeting-prefixed weak homophone) or as the bare
    greeting half of a SPLIT wake phrase whose name half opens the next line.

    ``strong=True`` requires a distinctive zoe-family name, for the filler
    lookahead where a weak homophone must not authorise a skip. A bare weak
    homophone ("So", "Joe") matches nothing here — it is the user's own word."""
    if _STRONG_WAKE_LINE_RE.match(kept[i]):
        return True
    if not strong and _GREETING_WEAK_WAKE_LINE_RE.match(kept[i]):
        return True
    # The split form is inherently strong: a bare greeting AND a wake name.
    return (
        i + 1 < len(kept)
        and _BARE_GREETING_RE.match(kept[i])
        and bool(_WAKE_NAME_START_RE.match(kept[i + 1]))
    )


def _strip_wake_word(lines: list) -> str:
    """Given Moonshine's per-line transcript texts (in order), drop the leading
    wake word and return the command transcript.

    Conservative by construction: it only removes leading wake-only lines (plus
    the pre-roll filler that can precede them) or an inline leading wake prefix,
    and never returns empty when there was content (a clip that is *only* the
    wake word is left as-is for the caller to handle)."""
    kept = [t for t in ((s or "").strip() for s in lines) if t]
    if not kept:
        return ""
    # 1. Drop the leading wake word, tolerating the pre-roll bleed above.
    #    NOTHING is dropped until a wake line is actually CONFIRMED: `cut` only
    #    ever advances past a line _wake_line_at() vouched for, so a filler that
    #    turns out not to precede a wake word leaves the transcript untouched.
    #    The scan stops before the last line, so the command line always survives.
    cut = 0
    i = 0
    while i < len(kept) - 1:
        if _wake_line_at(kept, i):
            cut = i + 1
            i += 1
            continue
        # A filler line is SKIPPED (never itself dropped) only while a real wake
        # line is still within reach — that is what marks it as pre-roll junk
        # rather than the start of the command.
        if _FILLER_LINE_RE.match(kept[i]) and any(
            _wake_line_at(kept, j, strong=True)
            for j in range(i + 1, min(i + 1 + _MAX_FILLER_LOOKAHEAD, len(kept) - 1))
        ):
            i += 1
            continue
        break
    kept = kept[cut:]
    # 2. If the wake word is inline on the (now) first line, strip just the prefix:
    #    (a) greeting + ambiguous homophone ("hey joey, ..."), then (b) an
    #    unambiguous wake variant ("zoe, ..." / "zo ..."). A bare homophone
    #    ("Joe wants ...") has no greeting, so it stays intact.
    head = _WAKE_GREETING_NAME_RE.sub("", kept[0], count=1)
    head = _WAKE_PREFIX_RE.sub("", head, count=1).strip()
    if head:
        # Re-capitalise so the command doesn't start lowercase after the cut.
        kept[0] = head[:1].upper() + head[1:]
    return " ".join(kept).strip()
