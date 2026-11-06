"""The write-claim guard: a reply that says it SAVED something is spoken only if something was saved.

Live 2026-10-12 (the trust problem): Zoe told the owner "I've added a work meeting to your calendar for tomorrow at 9 a.m."
There was no calendar row and not one tool call in the turn - the 4B brain narrated a write it never made. Another reply,
"I've added i don't know where you got that from…", sat over a list form nobody had confirmed. A spoken "done" that is not
true is worse than "I couldn't" - the owner stops trusting the whole assistant.

The rule, enforced at the ONE place every brain turn ends (``brain_dispatch.brain_streaming`` / ``brain_oneshot`` - voice,
chat, Telegram, LiveKit): a sentence that CLAIMS a write is let through only when ``write_receipts`` holds a receipt of that
kind for this user since the turn began (a list item / calendar event / reminder / note / timer that really landed - recorded
by the writers themselves, so a brain tool call that reached ``/api/system/intent-dispatch`` and succeeded counts, and one that
failed does not). Otherwise the sentence is replaced with an honest line and ``WRITE_CLAIM_BLOCKED`` is logged.

What counts as a claim is DATA, per language (``lexicons_data/write_claims.json``): claim verbs, target nouns, openers,
blockers (negation / irrealis / future). A new language or phrasing is a new row there, never a new regex here. A language
with no row simply claims nothing. Questions, offers ("want me to add it?"), refusals ("I couldn't add it") and promises
("I'll add it") are never claims.

Units: a reply is judged in UNITS - a sentence, or (once a sentence has run ``LEAD_TOKENS`` words) the part of it up to its next
comma / semicolon / colon / dash. A claim verb must sit within the first ``LEAD_TOKENS`` words of its unit, so streaming can
decide, from the first ``LEAD_TOKENS`` words alone, whether a unit can EVER become a claim: it holds a unit back until then (or
until the unit ends) and releases everything else at once. That bound is what makes streaming and one-shot output identical at
every delta cut - however the text is cut, the same units are judged on the same words. A claim verb counts in the active
("I've added …") and the completed passive ("… was added", "… has been added"); "is scheduled for Friday" describes the calendar
and is not a claim (only "is added" is, ``state_claim`` in the lexicon). Flag ``ZOE_WRITE_CLAIM_GUARD`` = ``enforce`` (default) | ``shadow`` (log only) |
``off``. The replay harness (``replay_isolation``) is never guarded - its writes are deliberately not committed.

In-progress claims (live 2026-11-04: typed "add balloons, blue streamers and goldfish crackers to the shopping list" was answered
"Adding balloons, blue streamers, and goldfish crackers to your shopping list now." and nothing was written): "Adding X to your
list now", "I'm setting a reminder", "I'll put that on your calendar right away" say the write is happening THIS turn. The brain
may say that just before its tool call, so such a unit is judged at the END of the turn (``guard_stream`` holds it until a
receipt lands or the turn ends). When the fast path DEFERRED a write to the brain (``write_receipts.take_deferred_write``, the
chat lane's ``EXPERT_DEFER_WRITE``), the request already named the target, so an in-progress claim with none of its own ("Adding
those now.") claims that kind.

QA 2026-11-05: STANDING promises with no immediacy word ("I'll remind you to call the plumber at 2:30"), REMOVALS ("I've taken that
off your calendar" - a removal receipt kind, never the create's), memory / forget claims and whole-phrase claims ("got that
noted") joined the lexicon. A REPLACED claim is noted for its session (``session_id``; ``claim_correction``,
``ZOE_WRITE_CLAIM_CORRECTION``) so the brain's next turn learns the write did not happen - its own history still holds the claim.

Stdlib only; never raises into a turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator, Optional

import write_receipts

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).with_name("lexicons_data") / "write_claims.json"
ENV_FLAG = "ZOE_WRITE_CLAIM_GUARD"
# "did you add it?" is answered from what happened EARLIER: a recap may stand on a write from this recent a past.
RECAP_WINDOW_S = 1800.0
_SENTINEL_PREFIXES = ("__TOOL__:", "__THINKING__:")
# A claim verb must sit within this many words of the start of its unit (see the module docstring). 12 words is ~65 characters:
# the point at which the voice path's own first-unit clause break (``voice_tts._FIRST_UNIT_CLAUSE_MIN``) starts to apply anyway.
LEAD_TOKENS = 12


def mode() -> str:
    raw = (os.environ.get(ENV_FLAG) or "enforce").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return "off"
    return "shadow" if raw == "shadow" else "enforce"


# ── text folding / tokenising ──────────────────────────────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    if text.isascii():          # the common case on the hot path: NFKC / NFD / accent-stripping are the identity on ASCII
        return text.casefold()
    s =unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'").casefold()
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def tokenize(text: str) -> list[str]:
    return [t.strip("'") for t in re.findall(r"[a-z0-9']+", _fold(text)) if t.strip("'")]


def _phrase(text: str) -> str:
    return " ".join(tokenize(text))


# ── the lexicon ────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Lang:
    code: str
    subjects: frozenset
    second_person: frozenset
    openers: frozenset
    adverbs: frozenset
    verbs: dict           # token -> (kind, needs_target, state_claim, progressive, removal, particles)
    targets: dict         # kind -> tuple of phrases
    other_targets: tuple
    blockers: tuple
    recap_cues: tuple
    honest: str
    passive_done: frozenset = frozenset()     # "has been added": a perfect passive - a claim even with no target noun
    passive_past: frozenset = frozenset()     # "was added": a past passive - a claim when it names a target ("The restaurant was booked" does not)
    passive_state: frozenset = frozenset()    # "is added": a present-tense passive - a claim only for a ``state_claim`` verb
    clause_starts: frozenset = frozenset()    # words that open a new clause after a comma ("it", "that", …)
    joiners: tuple = ()                       # clause joiners as token tuples, longest first ("but", "so", "because")
    conditionals: frozenset = frozenset()     # "if" / "unless": a TRAILING one opens its own (blocked) clause, it never reaches back
    temporals: tuple = ()                     # "after" / "when" / "once" / "before" as token tuples, longest first: a TRAILING one likewise
    temporal_firsts: frozenset = frozenset()  # the first word of each, for the per-word fast check in ``_temporal_at``
    temporal_clause_starts: tuple = ()        # what follows a temporal word when it opens a CLAUSE ("after YOU asked"), not a time phrase ("after lunch")
    discourse: frozenset = frozenset()        # "okay" / "so" / "well": skipped before judging whether a tagged unit opens a question
    tag_questions: tuple = ()                 # a trailing "okay" / "right" / "vale": a TAG on a statement, not a question about the write
    question_openers: frozenset = frozenset() # words that open a genuine question ("did", "have", "should"): "Have I added it, okay?"
    ellipsis_objects: frozenset = frozenset() # what may follow a sentence-opening claim verb in a completion ellipsis ("Added THE milk")
    modifiers: frozenset = frozenset()        # ordinary adverbs that may sit before a claim verb ("definitely", "again")
    modifier_suffixes: tuple = ()             # a word ending in one is an adverb too ("-ly", "-mente"), once a subject stands before it
    # IN-PROGRESS claims, judged at the END of the turn (``Claim.in_progress``): "I'm adding …" / "Adding … now" (a ``progressive``
    # claim verb, whose extra subjects are these) and "I'll add that to your list right now" (a promise verb: counts ONLY after a
    # promise subject AND with an immediacy word in its clause - a bare "I'll add it" stays a promise, "Shall I add it?" an offer).
    progressive_subjects: frozenset = frozenset()
    promise_verbs: dict = None                # base form -> (kind, needs_target)
    promise_subjects: frozenset = frozenset()
    immediacy: tuple = ()
    implied_cues: tuple = ()                  # "now" / "for you": an untargeted in-progress claim stands for a DEFERRED write's kind
    progressive_aux: frozenset = frozenset()  # "am" / "are" between a subject and a progressive verb ("I am adding")
    promise_blockers: frozenset = frozenset() # blockers that are a promise's own auxiliary ("will" in "I will add … now")
    # ASSURANCE claims: "I can certainly set that reminder for you" answers a write request as if it were being done. A promise
    # verb led by an assurance subject ("I can") counts as an IN-PROGRESS claim only with an assurance word in its unit
    # ("certainly", "absolutely") AND either a write the fast path deferred to this turn or an immediacy word - so "I can add it"
    # and "Can you do reminders?" -> "I can certainly set reminders for you!" stay offers / descriptions.
    assurance_subjects: frozenset = frozenset()
    assurance_words: tuple = ()
    # STANDING promises (QA 2026-11-05: "I'll remind you to call the plumber at 2:30 pm tomorrow." with no tool call): a promise
    # subject + a promise verb with no immediacy word is an in-progress claim too, unless a deferral word ("later") or a trailing
    # temporal / conditional clause is in its clause, or the user's own turn opened with a describe-question ("what …").
    standing_promises: bool = False
    promise_deferrals: tuple = ()
    standing_skip_openers: tuple = ()
    # Whole-phrase claims ("got that noted", "consider it forgotten"): (token tuple, kind) - "any" = any receipt.
    claim_phrases: tuple = ()
    phrase_firsts: frozenset = frozenset()    # their first words: a unit holding one early may become a claim (streaming hold)
    # A REMOVAL verb ("removed", "taken … off") claims the removal kind of its target ("list" -> "list_removed").
    removal_kinds: dict = None
    honest_by_kind: dict = None               # receipt kind -> the honest line for a claim of that kind

    def is_modifier(self, tok: str, *, by_suffix: bool = False) -> bool:
        if tok in self.adverbs or tok in self.modifiers:
            return True
        return by_suffix and any(len(tok) >= len(suf) + 3 and tok.endswith(suf) for suf in self.modifier_suffixes)

    @property
    def joiner_firsts(self) -> frozenset:
        return frozenset(j[0] for j in self.joiners)

    @property
    def hold_words(self) -> frozenset:
        return self.subjects | self.openers | self.adverbs | frozenset(self.verbs)


@lru_cache(maxsize=1)
def _langs() -> tuple:
    try:
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing / broken data file means NO claims are recognised, never a crash
        logger.warning("write_claim_guard: lexicon unreadable at %s - guard inactive", DATA_PATH)
        return ()
    out = []
    for code, lex in raw.items():
        if code.startswith("_") or not isinstance(lex, dict) or not lex.get("enabled"):
            continue

        def words(key: str) -> frozenset:
            return frozenset(w for w in (_phrase(x) for x in lex.get(key) or []) if w)

        def phrases(key: str) -> tuple:
            return tuple(p for p in (_phrase(x) for x in lex.get(key) or []) if p)

        def particles(spec: dict) -> frozenset:
            return frozenset(w for w in (_phrase(x) for x in (spec.get("particles") or [])) if w)

        verbs = {}
        for verb, spec in (lex.get("claim_verbs") or {}).items():
            tok, spec = _phrase(verb), spec or {}
            if tok:
                verbs[tok] = (str(spec.get("kind") or "any"), bool(spec.get("needs_target")),
                              bool(spec.get("state_claim")), spec.get("form") == "progressive",
                              spec.get("action") == "remove", particles(spec))
        promise_verbs = {}
        for verb, spec in (lex.get("promise_verbs") or {}).items():
            tok, spec = _phrase(verb), spec or {}
            if tok:
                promise_verbs[tok] = (str(spec.get("kind") or "any"), bool(spec.get("needs_target")),
                                      spec.get("action") == "remove", particles(spec))
        claim_phrases = tuple(sorted(
            ((tuple(p.split()), str((spec or {}).get("kind") or "any"))
             for p, spec in ((_phrase(k), v) for k, v in (lex.get("claim_phrases") or {}).items()) if p),
            key=lambda x: len(x[0]), reverse=True))
        targets = {kind: phrases_ for kind, phrases_ in ((k, tuple(p for p in (_phrase(x) for x in v) if p))
                                                          for k, v in (lex.get("targets") or {}).items())}
        temporals = tuple(sorted((tuple(p.split()) for p in phrases("clause_temporals")), key=len, reverse=True))
        out.append(_Lang(
            code=code, subjects=words("subjects"), second_person=words("second_person"), openers=words("openers"),
            adverbs=words("adverbs"), verbs=verbs, targets=targets, other_targets=phrases("other_targets"),
            blockers=phrases("blockers"), recap_cues=phrases("recap_cues"), honest=str(lex.get("honest") or "").strip(),
            passive_done=words("passive_done"), passive_past=words("passive_past"), passive_state=words("passive_state"), clause_starts=words("clause_starts"),
            tag_questions=phrases("tag_questions"), question_openers=words("question_openers"),
            ellipsis_objects=words("ellipsis_objects"),
            modifiers=words("modifiers"), modifier_suffixes=tuple(sorted(phrases("modifier_suffixes"), key=len, reverse=True)),
            joiners=tuple(sorted((tuple(p.split()) for p in phrases("clause_joiners")), key=len, reverse=True)),
            conditionals=words("clause_conditionals"),
            temporals=temporals, temporal_firsts=frozenset(t[0] for t in temporals),
            temporal_clause_starts=tuple(sorted((tuple(p.split()) for p in phrases("temporal_clause_starts")), key=len, reverse=True)),
            discourse=words("discourse_openers"),
            progressive_subjects=words("progressive_subjects"), promise_verbs=promise_verbs,
            promise_subjects=words("promise_subjects"), immediacy=phrases("immediacy"),
            implied_cues=phrases("implied_cues"), progressive_aux=words("progressive_aux"),
            promise_blockers=words("promise_blockers"),
            assurance_subjects=words("assurance_subjects"), assurance_words=phrases("assurance_words"),
            standing_promises=bool(lex.get("standing_promises")), promise_deferrals=phrases("promise_deferrals"),
            standing_skip_openers=tuple(tuple(p.split()) for p in phrases("standing_skip_openers")),
            claim_phrases=claim_phrases, phrase_firsts=frozenset(p[0] for p, _ in claim_phrases),
            removal_kinds={str(k): str(v) for k, v in (lex.get("removal_kinds") or {}).items()},
            honest_by_kind={str(k): str(v).strip() for k, v in (lex.get("honest_by_kind") or {}).items() if str(v).strip()},
        ))
    return tuple(out)


def _has_phrase(padded: str, phrase: str) -> bool:
    return f" {phrase} " in padded


@dataclass(frozen=True)
class Claim:
    kinds: tuple          # known target kinds the sentence names; () = a generic "I've added it"
    lang: str
    honest: str
    # Every claim in the unit says the write is happening NOW ("Adding milk to your list now", "I'll put it on your calendar right
    # away"): the brain may say that just BEFORE its tool call, so it is judged at the end of the turn, not when it is spoken.
    in_progress: bool = False


# What may stand between a statement and its tag: , ; : and the dashes (em, en, and a SPACED hyphen or double hyphen -
# "to-do" is one word and never a separator).
_TAG_SEP_RE = re.compile(r"[,;:\u2014\u2013]|(?<=\s)-{1,2}(?=\s)")


def _declarative_part(text: str) -> Optional[str]:
    """The statement a ``?``-ending unit really makes, or None when it is a genuine question. A TAG ("…, okay?", "… - right?",
    "… \u2014 okay?", "…: okay?", es "…, ¿vale?") questions only the tag: the clause before it is still an affirmative claim, so it is
    returned for judging. A question that opens with an interrogative word ("Have I added it, okay?") or "¿" is genuine and exempt."""
    if text.lstrip().startswith("¿"):
        return None
    body = text.rstrip().rstrip("?!. ").rstrip()
    seps = list(_TAG_SEP_RE.finditer(body))
    if not seps or seps[-1].start() <= 0:
        return None
    cut = seps[-1]
    tag, rest = _phrase(body[cut.end():]), body[:cut.start()]
    if "\u00bf" in rest:
        return None                 # a question opened inside the unit ("Bueno, ¿he añadido …")
    langs = _langs()
    discourse = frozenset().union(*(lang.discourse for lang in langs))
    first = next((t for t in tokenize(rest) if t not in discourse), "")      # "Okay, have I added it — right?" is a question
    if any(first in lang.question_openers for lang in langs):
        return None
    return rest if any(tag in lang.tag_questions for lang in langs) else None


def _completion_ellipsis(rest: list, has_target: bool, lang: "_Lang") -> bool:
    """A claim verb that OPENS its clause with nothing before it is Zoe's completion ("Added the milk.", "Added it.", "Added milk
    to the shopping list.") only when what follows reads as the thing written: nothing, a determiner / pronoun, or a target noun with
    no copula after it. "Scheduled maintenance starts at 2 PM" and "Added sugar is unhealthy" use the participle as an adjective."""
    if not lang.ellipsis_objects:
        return True                 # a language whose verbs conjugate ("añadí") has no bare-participle ambiguity
    if not rest or rest[0] in lang.ellipsis_objects:
        return True
    copulas = lang.passive_state | lang.passive_past | lang.passive_done
    return has_target and not any(t in copulas for t in rest)


#: Words naming a household / media action Zoe can take that records no tracked write receipt (smart home, media): a generic completion
#: phrase beside one of them is not a claim to have written to the store.
_ACTION_DEVICE_WORDS = frozenset({"light", "lights", "lamp", "lamps", "fan", "fans", "heater", "heaters", "thermostat", "tv", "speaker",
                                  "speakers", "music", "volume", "door", "doors", "blind", "blinds", "plug", "plugs", "switch", "aircon"})


def find_claim(sentence: str, *, implied_kind: Optional[str] = None, standing: bool = True) -> Optional[Claim]:
    """The write this sentence CLAIMS to have made, or None. Pure. Every claim clause of the unit counts: the Claim names the
    kinds of ALL of them, so a receipt for one cannot vouch for another.

    ``implied_kind``: this turn is a write the fast path DEFERRED to the brain (``write_receipts.take_deferred_write``), so the
    request already named its target - an in-progress claim with no target noun of its own ("Adding those now.") claims THAT kind.
    ``standing``: a promise with no immediacy word ("I'll remind you at 2:30") counts as an in-progress claim (``standing_allowed``
    turns it off for a turn whose user asked a describe-question, "what reminders have I got?")."""
    text = (sentence or "").strip()
    if not text:
        return None
    if text.endswith("?"):
        text = _declarative_part(text)
        if text is None:
            return None
    if not tokenize(text):
        return None
    unit_padded = " " + " ".join(tokenize(text)) + " "
    for lang in _langs():
        assured = any(_has_phrase(unit_padded, w) for w in lang.assurance_words)
        # Blockers (negation, irrealis, future) and target nouns are judged in the CLAUSE of the claim verb only: "I've added
        # milk to your list, but I'm not sure about eggs" and "… as you wanted" still claim the milk (review, #2004).
        found: list = []            # (kinds, in_progress) for each claiming clause
        conditional = any(t in lang.conditionals for t in tokenize(text))
        # A standing promise needs the WHOLE unit free of what makes it a plan: a deferral ("later", "as soon as you say") or a
        # leading temporal clause ("After I've added milk, I'll set a reminder").
        unit_toks = tokenize(text)
        while unit_toks and unit_toks[0] in lang.discourse:
            unit_toks = unit_toks[1:]
        standing_unit = bool(standing and lang.standing_promises
                             and not any(_has_phrase(unit_padded, w) for w in lang.promise_deferrals)
                             and not (unit_toks and unit_toks[0] in lang.temporal_firsts))
        prev_claimed = False        # did the clause just before this one claim a write?
        for offset, toks, temporal_tail, head in _clauses(text, lang):
            if head and prev_claimed:
                # A temporal tail that FOLLOWS a claim ("… after I've scheduled the physio") reports a second, completed write: its
                # leading temporal word only orders the two, so it is not a blocker here and the tail's claim needs its own receipt.
                # After a promise or denial the word stays ("I'll tell you after I've added it" is no claim).
                toks, offset = toks[head:], offset + head
            prev_claimed = False
            padded = " " + " ".join(toks) + " "
            blocked = {b for b in lang.blockers if _has_phrase(padded, b)}
            # "I will add that to your list right now": the future word that blocks every other claim is the promise's own
            # auxiliary, so such a clause may still hold a PROMISE claim (and only that).
            promise_only = bool(blocked) and blocked <= lang.promise_blockers
            if blocked and not promise_only:
                continue
            phrase_kind = None if promise_only else _phrase_claim(toks, offset, lang)
            if phrase_kind == "any" and (any(_has_phrase(unit_padded, o) for o in lang.other_targets)
                                         or any(w in _ACTION_DEVICE_WORDS for w in tokenize(text))):
                phrase_kind = None      # "Consider it done - the lights are off.": a callable action with no tracked receipt, not a write claim
            if phrase_kind is not None:
                # "…, got that noted." / "Consider it forgotten.": a whole phrase that says the write is done.
                found.append(((() if phrase_kind == "any" else (phrase_kind,)), False))
                prev_claimed = True
                if phrase_kind != "any":
                    continue
                # a GENERIC phrase ("got that noted") adds no kind of its own: the clause's specific claim ("I've added milk to your
                # list, got that noted") still needs ITS receipt, so the verb scan below runs too instead of being skipped
            for i, tok in enumerate(toks):
                if offset + i >= LEAD_TOKENS:
                    break               # streaming cannot see past here before it must release the unit (module docstring)
                promise = (lang.promise_verbs or {}).get(tok)
                if promise is not None and not conditional:
                    p_kind, p_needs, p_removal, p_particles = promise
                    now_ = _promised_now(toks, i, padded, lang)
                    sure = bool(assured and _assured_now(toks, i, padded, lang, implied_kind))
                    # A STANDING promise ("I'll remind you to call the plumber at 2:30"): no immediacy word, but nothing that
                    # defers it ("later", a trailing "once you confirm") either. Never with an "if" anywhere in the unit.
                    standing_ = bool(standing_unit and not now_ and not sure and not temporal_tail
                                     and _promise_led(toks, i, lang))
                    if (now_ or sure or standing_) and _particle_after(toks, i, p_particles):
                        # "I'll add that to your list right now": a write promised for THIS turn. Never with an "if" anywhere in
                        # the unit ("I'll add it now if you like" is an offer).
                        # A verb that names its OWN kind (remind / remember / forget) keeps it: the nouns of its CONTENT ("I'll remind you to
                        # add milk to your shopping list at 3pm", "I'll remember your appointment is on Friday") do not turn the
                        # promise into a list / calendar write that needs a different receipt.
                        kinds = (p_kind,) if (p_kind != "any" and not p_needs) else _named_kinds(padded, lang)
                        if not kinds:
                            if any(_has_phrase(padded, o) for o in lang.other_targets):
                                continue
                            if p_kind != "any" and not p_needs:
                                kinds = (p_kind,)       # the verb names its own kind ("I'll remind you", "I'll forget that")
                            elif implied_kind:
                                kinds = (implied_kind,)
                            elif p_needs:
                                continue
                            else:
                                kinds = ()
                        found.append((_action_kinds(kinds, p_removal, lang), True))
                        prev_claimed = True
                        break
                spec = lang.verbs.get(tok)
                if spec is None or promise_only:
                    continue
                verb_kind, needs_target, state_claim, progressive, removal, parts = spec
                pre = toks[max(0, i - 4):i]
                aux = "" if progressive else _passive_aux(pre, lang)       # "I was adding …" is no passive
                passive = aux in lang.passive_done or aux in lang.passive_past or (state_claim and aux in lang.passive_state)
                if temporal_tail and aux in lang.passive_state:
                    continue            # "Milk is added once you tap Done": a pending form described, not a write reported
                if not passive and any(p in lang.second_person for p in pre):
                    continue            # "you added it" - the user did it ("The milk you mentioned has been added": you is not the writer)
                extra = lang.progressive_subjects if progressive else frozenset()
                aux_ok = lang.progressive_aux if progressive else frozenset()
                active = (all(p in lang.hold_words or p in lang.modifiers or p in extra for p in pre)
                          or _subject_then_modifiers(toks, i, lang, extra, aux_ok))
                if not (active or passive):
                    continue
                if not _particle_after(toks, i, parts):
                    continue            # "I've taken note of it" is no removal; "I've taken it OFF your calendar" is
                cued = any(_has_phrase(padded, c) for c in lang.implied_cues)
                if progressive and not cued and not _has_subject_before(toks, i, lang, extra, aux_ok):
                    # A bare "Adding milk to your shopping list by tapping +" / "Setting a timer helps you focus" is an instruction or a
                    # description; only "now" / "for you" (or a first-person subject: "I'm adding") makes it Zoe's own write.
                    continue
                kinds = (verb_kind,) if (verb_kind == "forget" and not needs_target) else _named_kinds(padded, lang)
                # (a completed FORGET keeps its explicit kind: "I've forgotten that memory" is a deletion claim, not a memory write)
                implied = bool(progressive and implied_kind and cued)
                if not pre and not _completion_ellipsis(toks[i + 1:], bool(kinds) or implied, lang):
                    continue
                if not kinds:
                    if any(_has_phrase(padded, o) for o in lang.other_targets):
                        continue
                    if implied:
                        kinds = (implied_kind,)
                    elif needs_target:
                        continue
                    elif not active and aux not in lang.passive_done and verb_kind != "any":
                        continue        # "The restaurant was booked solid": a past passive with no target noun claims no write
                    else:
                        kinds = () if verb_kind == "any" else (verb_kind,)
                found.append((_action_kinds(kinds, removal, lang), progressive))
                prev_claimed = True
                break                   # one claim per clause: its target nouns are already all in ``kinds``
        if found:
            specific = {k for kinds, _ in found for k in kinds}
            ordered = tuple(k for k in lang.targets if k in specific) + tuple(sorted(specific - set(lang.targets)))
            honest = (lang.honest_by_kind or {}).get(ordered[0], lang.honest) if ordered else lang.honest
            # ANY in-progress clause defers the whole unit to the end of the turn (a reminder "I'm setting now" may land after the
            # sentence); judged then, every kind still needs its own receipt.
            return Claim(kinds=ordered, lang=lang.code, honest=honest, in_progress=any(p for _, p in found))
    return None


def _action_kinds(kinds: tuple, removal: bool, lang: "_Lang") -> tuple:
    """The receipt kinds a claim needs: its target kinds, or - for a REMOVAL verb - their removal kinds ("list" -> "list_removed",
    a note / memory -> "forget"; ``removal_kinds`` data)."""
    if not removal:
        return kinds
    out: list = []
    for k in kinds:
        r = (lang.removal_kinds or {}).get(k) or f"{k}_removed"
        if r not in out:
            out.append(r)
    return tuple(out)


def _particle_after(toks: list, i: int, particles: frozenset) -> bool:
    """A verb that needs a particle ("taken … OFF", "forgotten THAT") has one later in its clause; True when it needs none."""
    return not particles or any(t in particles for t in toks[i + 1:])


def _phrase_claim(toks: list, offset: int, lang: "_Lang") -> Optional[str]:
    """The kind of a whole-phrase claim ("got that noted") starting within the unit's first ``LEAD_TOKENS`` words, else None."""
    if not lang.claim_phrases:
        return None
    for k in range(len(toks)):
        if offset + k >= LEAD_TOKENS:
            break
        for ptoks, kind in lang.claim_phrases:
            if tuple(toks[k:k + len(ptoks)]) == ptoks:
                return kind
    return None


def standing_allowed(user_text: str) -> bool:
    """False when the user's own turn opens with a describe-question ("what reminders have I got?", ``standing_skip_openers``):
    a future-tense reply then describes what exists ("I'll remind you at 2:30") rather than promising a write."""
    toks = tokenize(user_text or "")
    langs = _langs()
    discourse = frozenset().union(*(lang.discourse for lang in langs)) if langs else frozenset()
    while toks and toks[0] in discourse:
        toks = toks[1:]
    return not any(toks[:len(p)] == list(p) for lang in langs for p in lang.standing_skip_openers)


def _named_kinds(padded: str, lang: "_Lang") -> tuple:
    """The known target kinds a clause names ("… to your shopping list" -> ("list",))."""
    return tuple(kind for kind, phrases_ in lang.targets.items() if any(_has_phrase(padded, p) for p in phrases_))


def _promised_now(toks: list, i: int, padded: str, lang: "_Lang") -> bool:
    """The promise verb at ``toks[i]`` is led by a promise subject ("I'll", "we'll") through nothing but openers and modifiers, and
    its clause says NOW ("now", "right away"): "I'll add that to your list right now" - not "I'll add it later", "I can add it"."""
    if not any(_has_phrase(padded, w) for w in lang.immediacy):
        return False
    return _promise_led(toks, i, lang)


def _promise_led(toks: list, i: int, lang: "_Lang") -> bool:
    """The promise verb at ``toks[i]`` is led by a promise subject ("I'll", "we'll", "let me") through nothing but openers and
    modifiers."""
    if not lang.promise_subjects:
        return False
    for j in range(i - 1, max(-1, i - 1 - _SUBJECT_REACH), -1):
        tok = toks[j]
        if tok in lang.promise_subjects or (j > 0 and f"{toks[j - 1]} {tok}" in lang.promise_subjects):
            return True
        if not (tok in lang.openers or lang.is_modifier(tok, by_suffix=True)):
            return False
    return False


def _assured_now(toks: list, i: int, padded: str, lang: "_Lang", implied_kind: Optional[str]) -> bool:
    """The promise verb at ``toks[i]`` is led by an assurance subject ("I can") through nothing but openers and modifiers, in a turn
    whose write was deferred to the brain or with an immediacy word in its clause (the caller has already found an assurance word in
    the unit): "I can certainly set that reminder for you" said in answer to "remind me …" - QA 2026-11-05, where the tool then
    failed and the reply read as done before its apology."""
    if not lang.assurance_subjects:
        return False
    if not implied_kind and not any(_has_phrase(padded, w) for w in lang.immediacy):
        return False
    for j in range(i - 1, max(-1, i - 1 - _SUBJECT_REACH), -1):
        tok = toks[j]
        if j > 0 and f"{toks[j - 1]} {tok}" in lang.assurance_subjects:
            return True
        if not (tok in lang.openers or lang.is_modifier(tok, by_suffix=True)):
            return False
    return False


def _passive_aux(pre: list, lang: "_Lang") -> str:
    """The passive auxiliary just before a claim verb, looking back over adverbs ("was just added", "has been successfully
    added"); "" when there is none. A subject of any length may stand before it ("The appointment on Friday was added")."""
    for tok in reversed(pre):
        if tok in lang.passive_done or tok in lang.passive_past or tok in lang.passive_state:
            return tok
        if not lang.is_modifier(tok, by_suffix=True):
            return ""
    return ""


_SUBJECT_REACH = 6      # how far back from the claim verb a first-person subject may stand ("I have now definitely gone ahead and added")


def _has_subject_before(toks: list, i: int, lang: "_Lang", extra_subjects: frozenset, aux: frozenset) -> bool:
    """A first-person subject leads the verb at ``toks[i]`` ("I'm adding", "I am now adding")."""
    return _subject_then_modifiers(toks, i, lang, extra_subjects, aux)


def _subject_then_modifiers(toks: list, i: int, lang: "_Lang", extra_subjects: frozenset = frozenset(),
                            aux: frozenset = frozenset()) -> bool:
    """True when the claim verb at ``toks[i]`` is led by a first-person subject through nothing but openers, claim words and
    modifiers - listed ones or any adverb by suffix ("I've definitely added", "we have quietly set"). The subject is what makes a
    suffix word safe to trust: "Emily added the milk" has none and stays someone else's write; "I think Mum added" is cut by
    "think", which is neither a hold word nor a modifier."""
    for j in range(i - 1, max(-1, i - 1 - _SUBJECT_REACH), -1):
        tok = toks[j]
        if tok in lang.subjects or tok in extra_subjects:
            return True
        if tok in aux and j > 0 and toks[j - 1] in lang.subjects:
            return True                 # "I am adding", "we are setting": the progressive's own auxiliary after its subject
        if not (tok in lang.hold_words or lang.is_modifier(tok, by_suffix=True)):
            return False
    return False


_BREAK_RE = re.compile(r"[a-z0-9']+|[,;:—–]")


def _clauses(text: str, lang: "_Lang") -> list:
    """``(offset, tokens, temporal_tail, head)`` for each clause of ``text`` - ``offset`` is how many words precede the clause in
    the unit; ``temporal_tail`` is True when a trailing temporal clause ("… after you asked me to") was split off right after it;
    ``head`` is how many leading words of THIS clause are the temporal phrase that split it off (0 for any other clause). The
    word tokens split into clauses: at ``; : -`` always, at a comma that is followed by a new subject
    (``clause_starts`` / subjects / second person) or a joiner, and at every joiner word (``clause_joiners`` - "but", "so",
    "as", "because"… per language DATA; the joiner itself belongs to neither clause). A comma inside a list of items does
    not split ("I've added milk, eggs and bread to your list")."""
    items = [t.strip("'") if t[0] == "'" or t[-1] == "'" else t for t in _BREAK_RE.findall(_fold(text))]
    items = [t for t in items if t]
    starts = lang.clause_starts | lang.subjects | lang.second_person
    clauses: list = []
    cur: list = []
    seen = 0            # words consumed so far (joiners included) = the offset of the clause being built
    cur_off = 0
    cur_head = 0        # words of the clause being built that are the temporal phrase which split it off
    i = 0

    cur_soft = False    # a prepositional time phrase ("after lunch") was passed over inside the clause being built

    def close(temporal_tail: bool = False) -> None:
        nonlocal cur, cur_off, cur_head, cur_soft
        if cur:
            clauses.append((cur_off, cur, temporal_tail or cur_soft, cur_head))
        cur = []
        cur_off = seen
        cur_head = 0
        cur_soft = False

    while i < len(items):
        tok = items[i]
        if tok in ";:—–":
            close()
        elif tok == ",":
            j = next((k for k in range(i + 1, len(items)) if items[k] not in ",;:—–"), len(items))
            nxt = items[j] if j < len(items) else ""
            if nxt in starts or _comma_joiner(items, j, lang, starts):
                close()
        else:
            if tok in lang.conditionals and cur:
                # "I've added milk to your list, if that's okay": the hedge is a clause of its own and blocks only itself. A LEADING
                # "if" stays in its clause (and blocks it), so "If it's added, great" is still not a claim (review, #2004).
                close()
            temporal = _temporal_at(items, i, lang)
            if temporal and cur and _word_follows(items, i + temporal, lang):
                if _clause_starts_at(items, i + temporal, lang):
                    # "I've added milk to your list, after you asked me to": the trailing temporal clause is a clause of its own
                    # (its own leading "after" blocks only IT), so it never disables the completed claim before it. A LEADING
                    # "When I've added it, I'll tell you" stays one clause and stays blocked.
                    close(temporal_tail=True)
                    cur_head = temporal
                else:
                    # "I've put the dentist after lunch in your calendar": a time PHRASE, not a clause. It is neither a split nor a
                    # blocker, but each of its words keeps its place as a placeholder so word positions (the 12-word bound
                    # streaming depends on) do not shift.
                    cur_soft = True
                    cur.extend(["~"] * temporal)
                    seen += temporal
                    i += temporal
                    continue
            joiner = next((len(j) for j in lang.joiners if items[i:i + len(j)] == list(j)), 0)
            if joiner:
                close()
                seen += joiner
                cur_off = seen
                i += joiner
                continue
            cur.append(tok)
            seen += 1
        i += 1
    close()
    return clauses


def _comma_joiner(items: list, j: int, lang: "_Lang", starts: frozenset) -> bool:
    """A comma followed by a joiner opens a new clause - "…, but I'm not sure about eggs", "…, and then …", "…, and I've set a
    reminder". An Oxford-comma "and" before the LAST ITEM of a list ("balloons, streamers, and crackers to your list") is only the
    first word of a longer joiner ("and then"), with no new subject after it: the items stay in the clause of their verb."""
    if j >= len(items) or items[j] not in lang.joiner_firsts:
        return False
    if any(items[j:j + len(jn)] == list(jn) for jn in lang.joiners):
        return True
    return j + 1 < len(items) and items[j + 1] in starts


def _temporal_at(items: list, i: int, lang: "_Lang") -> int:
    """Length in words of the temporal phrase ("after", "una vez") starting at ``items[i]``, else 0."""
    if items[i] not in lang.temporal_firsts:      # the hot path: almost every word of a reply stops here
        return 0
    return next((len(t) for t in lang.temporals if items[i:i + len(t)] == list(t)), 0)


def _word_follows(items: list, i: int, lang: "_Lang") -> bool:
    """True when a real word follows at ``items[i]`` that is not itself temporal: "after YOU asked" / "after LUNCH" lead on, but
    "once before" / "once." / "before," are adverbs and leave the sentence as it was."""
    return i < len(items) and items[i] not in ",;:\u2014\u2013" and not _temporal_at(items, i, lang)


def _clause_starts_at(items: list, i: int, lang: "_Lang") -> bool:
    """True when ``items[i:]`` opens a clause (a subject: "you", "I've", es "de que" / "lo"), not a time phrase ("lunch")."""
    return any(items[i:i + len(t)] == list(t) for t in lang.temporal_clause_starts)


def is_recap_question(user_text: str) -> bool:
    """The user is asking about something done EARLIER ("did you add milk?"): a claim may stand on a recent past write."""
    if not (user_text or "").strip():
        return False
    padded = " " + " ".join(tokenize(user_text)) + " "
    return any(_has_phrase(padded, c) for lang in _langs() for c in lang.recap_cues)


# ── the verdict ────────────────────────────────────────────────────────────────────────────────────────────────────────

def supported(claim: Claim, user_id: Optional[str], started: float, *, user_text: str = "") -> bool:
    """True when a real write backs the claim: a receipt of every kind it names since ``started`` (a recap question reaches
    back ``RECAP_WINDOW_S``). A receipt is the ONLY evidence - not a tool's name: the ``phase=start`` sentinel arrives before the
    tool has finished, and a tool that failed leaves no receipt (review, #2004). A receipt made inside ANOTHER concurrent turn
    of the same user does not count (``write_receipts``)."""
    if is_recap_question(user_text):
        # "did you add milk?" asks about EARLIER turns: any of the user's recent writes, whichever turn made them.
        since = float(min(started, write_receipts.now() - RECAP_WINDOW_S))      # a plain float: no turn filter
        turn = None
    else:
        since, turn = started, getattr(started, "turn", None)
    if not claim.kinds:
        return write_receipts.has(user_id, since, turn=turn)
    return all(write_receipts.has(user_id, since, kind, turn=turn) for kind in claim.kinds)


def _log_blocked(claim: Claim, user_id: Optional[str], lane: str, action: str) -> None:
    # Counts and kinds only: the sentence is household conversation and stays out of the log.
    logger.warning("WRITE_CLAIM_BLOCKED action=%s lane=%s user=%s lang=%s kinds=%s reason=no_receipt",
                   action, lane, (user_id or "guest"), claim.lang, ",".join(claim.kinds) or "any")


# ── sentence splitting ─────────────────────────────────────────────────────────────────────────────────────────────────

_ABBREV_TAIL_RE = re.compile(r"(?:\b[a-z]\.[a-z]\.|\b(?:mr|mrs|ms|dr|st|vs|etc|no)\.)$", re.IGNORECASE)


_MERIDIEM_TAIL_RE = re.compile(r"\b[ap]\.m\.$", re.IGNORECASE)
_TERMINATOR_RE = re.compile(r"[.!?…]+[\"')\]]*\s|\n|[,;:—–]\s")
_TAIL_PUNCT_RE = re.compile(r"[.!?…,;:—–]+[\"')\]]*$")


def _sentence_end(buf: str, start: int = 0, unit_start: Optional[int] = None) -> Optional[int]:
    """Index just past the first COMPLETE unit at or after ``start`` in ``buf`` (terminator + the whitespace after it), else
    None. A unit ends at a sentence terminator, or at a comma / semicolon / colon / dash once the unit has run ``LEAD_TOKENS``
    words (so a long chatty run-up to a claim is cut where the claim's own clause begins). ``unit_start`` is where the unit
    began when that is before ``start`` (the streaming guard has already released the first part of it)."""
    for m in _TERMINATOR_RE.finditer(buf, start):
        end = m.end()
        if m.group()[0] in ",;:—–":
            if len(tokenize(buf[start if unit_start is None else unit_start:m.start()])) < LEAD_TOKENS:
                continue
            return end
        if _ABBREV_TAIL_RE.search(buf[:m.start() + 1]):
            # "at 9 a.m. tomorrow" goes on; "at 9 a.m. Anything else?" ends there (a capital starts the next sentence).
            ahead = buf[end:end + 1]
            if not (_MERIDIEM_TAIL_RE.search(buf[:m.start() + 1]) and ahead.isalpha() and ahead.isupper()):
                continue
        return end
    return None


def split_sentences(text: str) -> list[str]:
    """``text`` cut into the units the guard judges (see ``_sentence_end``); concatenated they give ``text`` back."""
    out, rest = [], text or ""
    while True:
        end = _sentence_end(rest)
        if end is None:
            break
        out.append(rest[:end])
        rest = rest[end:]
    if rest:
        out.append(rest)
    return out


def _could_become_claim(unit_so_far: str) -> bool:
    """Hold an unfinished unit until it is KNOWN it can never be a claim: it has fewer than ``LEAD_TOKENS`` complete words, or a
    claim verb sits among its first ``LEAD_TOKENS``, or it ends in punctuation (the boundary may be completing). The last word
    may be half-arrived ("I'v", "añ") and so is not counted."""
    toks = tokenize(unit_so_far)
    if unit_so_far and (unit_so_far[-1].isalnum() or unit_so_far[-1] in "'’"):      # any script: "añ" is half of "añadido"
        if toks:
            toks.pop()
    if len(toks) < LEAD_TOKENS or _TAIL_PUNCT_RE.search(unit_so_far):
        return True
    head = toks[:LEAD_TOKENS]
    return any(t in lang.verbs or t in (lang.promise_verbs or {}) or t in lang.phrase_firsts
               for lang in _langs() for t in head)


# ── the guard ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _replacement(sentence: str, claim: Claim) -> str:
    """The honest line in the claiming sentence's place, keeping the whitespace around it (a sentence cut across deltas can
    carry the boundary space as its LEADING whitespace)."""
    lead = sentence[:len(sentence) - len(sentence.lstrip())]
    trail = sentence[len(sentence.rstrip()):]
    return lead + claim.honest + trail


def _note_correction(session_id: str, claim: Claim) -> None:
    """A claim was REPLACED: the brain's own session history still holds the claim it made, so its next turn is told the truth
    (``claim_correction``, ``ZOE_WRITE_CLAIM_CORRECTION``). Never raises."""
    if not session_id:
        return
    try:
        import claim_correction

        claim_correction.note(session_id, claim.lang, claim.kinds)
    except Exception:  # noqa: BLE001 - bookkeeping must never break a turn
        logger.debug("write_claim_guard: correction note failed", exc_info=True)


def guard_text(reply: str, user_id: Optional[str], started: float, *, user_text: str = "", lane: str = "brain",
               implied_kind: Optional[str] = None, session_id: str = "") -> str:
    """The reply with every unsupported write claim replaced by the honest line (once). Unchanged when off / no claims. The
    reply is whole, so the turn is over: an in-progress claim is judged like any other. ``session_id``: the brain session whose
    next turn is told about a replaced claim (``claim_correction``)."""
    m = mode()
    if m == "off" or not (reply or "").strip():
        return reply
    try:
        out, replaced = [], False
        standing = standing_allowed(user_text)
        for sentence in split_sentences(reply):
            claim = find_claim(sentence, implied_kind=implied_kind, standing=standing)
            if claim is None or supported(claim, user_id, started, user_text=user_text):
                out.append(sentence)
                continue
            _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
            if m == "shadow":
                out.append(sentence)
            elif not replaced and claim.honest:
                replaced = True
                _note_correction(session_id, claim)
                out.append(_replacement(sentence, claim))
        return "".join(out) if out else reply
    except Exception as exc:  # noqa: BLE001 - never break a turn over bookkeeping
        logger.warning("write_claim_guard.guard_text failed (reply passed through): %s", exc)
        return reply


async def guard_stream(inner: AsyncIterator[str], user_id: Optional[str], started: float, *, user_text: str = "",
                       lane: str = "brain", implied_kind: Optional[str] = None, session_id: str = "") -> AsyncIterator[str]:
    """Pass ``inner`` through, replacing an unsupported write-claim sentence. Sentinels pass untouched; text is held back only
    while a unit could still become a claim (``_could_become_claim``). The output equals ``guard_text`` of the whole reply however
    the deltas are cut.

    An IN-PROGRESS claim ("Adding milk to your list now.") is often spoken just BEFORE the tool call that makes it true, so it is
    not judged when it ends: it - and everything after it - is held until a receipt backs it (re-checked on every delta and
    sentinel, so a tool that lands releases it at once) or the turn ends, when it is judged like ``guard_text`` judges it."""
    m = mode()
    if m == "off":
        async for delta in inner:
            yield delta
        return
    held: list = []     # the deltas not yet released, as they arrived (an unchanged sentence goes out in the SAME pieces)
    head = ""           # the part of the CURRENT unit already released (it holds >= LEAD_TOKENS claim-free words)
    replaced = False
    unit_clear = False  # the current unit is already known claim-free (its first LEAD_TOKENS words held no claim verb)
    cleared = 0         # leading characters of ``held`` already judged clean (released once a whole piece is clean)
    pending = None      # (claim, end): an unsupported in-progress claim unit ending at ``held`` offset ``end``, awaiting a receipt
    finishing = False   # the turn is over: nothing is pending any more
    standing = standing_allowed(user_text)

    def block(sentence: str, claim: Claim) -> Optional[str]:
        """An unsupported claim: None = release the sentence untouched (shadow); else the text to release instead."""
        nonlocal replaced
        _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
        if m == "shadow":
            return None
        if replaced or not claim.honest:
            return ""                      # a second claim in the same reply is dropped (guard_text does the same)
        replaced = True
        _note_correction(session_id, claim)
        return _replacement(sentence, claim)

    def judge(sentence: str):
        """None = release the sentence untouched; a string = release that instead (may be empty); a Claim = an in-progress
        claim to hold until a receipt or the end of the turn."""
        nonlocal head, unit_clear
        whole = head + sentence
        head = ""
        unit_clear = False
        claim = find_claim(whole, implied_kind=implied_kind, standing=standing)
        if claim is None or supported(claim, user_id, started, user_text=user_text):
            return None
        if claim.in_progress and not finishing:
            return claim
        return block(sentence, claim)

    def advance() -> list:
        """Judge every unit that is complete, and return the pieces that may go out now."""
        nonlocal head, unit_clear, cleared, pending
        out: list = []
        if pending is not None:
            claim, end = pending
            if supported(claim, user_id, started, user_text=user_text):
                pending, cleared = None, end
            elif finishing:
                pending = None
                buf = "".join(held)
                verdict = block(buf[cleared:end], claim)
                if verdict is None:
                    cleared = end
                else:
                    text = buf[:cleared] + verdict
                    held[:] = [buf[end:]] if buf[end:] else []
                    cleared = 0
                    if text:
                        out.append(text)
            else:
                return out                  # still waiting for the write it promised
        while True:
            buf = "".join(held)
            # The released head of the unit counts too: where a unit ends (its word count) is then the same however the text
            # is cut.
            end = _sentence_end(head + buf, len(head) + cleared, 0 if head else cleared)
            if end is None:
                break
            end -= len(head)
            verdict = judge(buf[cleared:end])
            if verdict is None:
                cleared = end            # clean: stays in its ORIGINAL pieces
                continue
            if isinstance(verdict, Claim):
                pending = (verdict, end)
                break
            # An unsupported claim: release what was already clean, then the honest line in the sentence's place.
            text = buf[:cleared] + verdict
            held[:] = [buf[end:]] if buf[end:] else []
            cleared = 0
            if text:
                out.append(text)
        while held and len(held[0]) <= cleared:     # whole clean pieces go out exactly as they arrived
            piece = held.pop(0)
            cleared -= len(piece)
            if pending is not None:
                pending = (pending[0], pending[1] - len(piece))      # its end is an offset into ``held``
            out.append(piece)
        if pending is not None or finishing:
            return out
        tail = "".join(held)[cleared:]
        if tail and not (_TAIL_PUNCT_RE.search(tail) or (not unit_clear and _could_become_claim(head + tail))):
            # Known claim-free: the unit's first LEAD_TOKENS words hold no claim verb, so nothing later in it can be a
            # claim. (A tail that ends in punctuation is held: the boundary's whitespace may be one delta away.)
            unit_clear = True
            head += tail
            out.extend(held)
            held.clear()
            cleared = 0
        return out

    def flush() -> list:
        """The turn is over: settle a pending claim, then judge what is still held as the final (possibly unfinished) unit."""
        nonlocal finishing, cleared
        finishing = True
        pieces = advance()
        buf = "".join(held)
        if not buf:
            return pieces
        verdict = judge(buf[cleared:]) if cleared < len(buf) else None
        pieces.extend(list(held) if verdict is None else [buf[:cleared] + verdict])
        held.clear()
        cleared = 0
        return pieces

    try:
        async for delta in inner:
            if delta.startswith(_SENTINEL_PREFIXES):
                yield delta
                if pending is not None:         # a tool may just have landed the write the held claim promised
                    for piece in advance():
                        yield piece
                continue
            held.append(delta)
            for piece in advance():
                yield piece
        for piece in flush():
            yield piece
    except Exception:
        # The lane failed mid-stream (a failover / transport error): what was held back is real, already-generated text -
        # release it (judged like the end of a stream) before the error propagates, as the unguarded stream would have.
        for piece in flush():
            yield piece
        raise
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass
