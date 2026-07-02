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

# A line that is ENTIRELY a wake-word variant (so the whole line is dropped).
_WAKE_LINE_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay|a|hey,)?[\s,]*"
    r"(?:zoe|zoey|zo|joey|joe|zoie|sewey|so|josie|zoee)"
    r"[\s,.!?]*$",
    re.IGNORECASE,
)
# A leading wake prefix on a line that ALSO carries the command (strip the prefix
# only). Requires a following word (?=\S) so a bare name that IS the command stays.
_WAKE_PREFIX_RE = re.compile(
    r"^\s*(?:hey|hi|ok|okay)?[\s,]*"
    # Inline strip uses ONLY unambiguous non-word wake variants. Real names/words
    # (joe, joey, josie, so) are deliberately EXCLUDED here — as an inline prefix
    # they would corrupt a real command ("Joe wants the weather", "so, add milk").
    # Those still get caught when they are a whole wake-only line (_WAKE_LINE_RE);
    # only the risky inline strip is conservative.
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


def _strip_wake_word(lines: list) -> str:
    """Given Moonshine's per-line transcript texts (in order), drop the leading
    wake word and return the command transcript.

    Conservative by construction: it only removes whole leading wake-only lines or
    an inline leading wake prefix, and never returns empty when there was content
    (a clip that is *only* the wake word is left as-is for the caller to handle)."""
    kept = [t for t in ((s or "").strip() for s in lines) if t]
    if not kept:
        return ""
    # 1. Drop leading lines that are nothing but a wake word.
    while len(kept) > 1 and _WAKE_LINE_RE.match(kept[0]):
        kept = kept[1:]
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
