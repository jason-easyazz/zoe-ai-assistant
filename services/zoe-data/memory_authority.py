"""memory_authority — who SAID a fact decides who may change it.

Why this exists (live 2026-10-05, generalised from the identity incident): the nightly
digest read a day transcript holding a speech-to-text fragment that named a third person,
asserted a fact about the owner from it, and its contradiction pass called
``MemoryService.review(edit)`` — which SUPERSEDED a row the owner had stated himself and
carried that row's ``source``/``session_id`` forward, so the new row looked like a regex
write. Model inference outranked the user's own words, and the provenance lied about it.
PR #1866 walled off ONE attribute (the user's name); this module is the class
(docs/research/memory-fidelity-audit-2026-10-05.md §2, §6 P1.1; docs/knowledge/memory-authority.md).

The rule (enforced at ONE choke point, ``MemoryService``: ``ingest`` / ``review`` /
``supersede_by`` / ``archive_duplicate``; this module is the pure half — no I/O, no store):

* Every row carries a PROVENANCE CLASS (``authority_class``) from an ALLOW-LIST, ranked:

      operator 6 > user_confirmed 5 > user_stated 4 > user_stated_derived 3
              > user_unverified 2 > model_from_turn 1 > model_from_transcript 0

  plus ``authority`` (the owner's three-way view: user_stated | user_confirmed | inferred),
  ``authority_basis``, ``origin`` (the writer), ``turn_ref`` and ``model`` (LLM writers).
  An UNKNOWN writer is rank 0 (fail-closed). Rows written before this existed derive a class
  on read (``legacy_class``); ``scripts/maintenance/memory_authority_backfill.py`` reports
  and, on request, stamps the same answer.
* A write may supersede / archive / contradict an existing row only if its class is a DIRECT
  user class (rank >= 4: typed or spoken by the person, a later statement of theirs always wins;
  a model's paraphrase of them, ``user_stated_derived``, never overrides a direct statement) or
  its power is >= the row's rank. Anything else is DEMOTED to a ``disputed`` CANDIDATE row linked by
  ``contradicts_id`` (never recalled, askable later) and logs
  ``AUTHORITY_BLOCKED writer=<name> kind=<attr>`` (labels only — never text).
* A supersede records the NEW writer's provenance. It never inherits the old row's
  ``source`` / ``session_id`` / ``user_turn_id`` / (for a model writer) excerpt.
* The user approving a candidate IS the user stating it (``user_confirmed``), and retires the
  row it disputed. #1866's identity wall is the special case "the user's own name is never
  writable by an automatic writer at all".

How a model-assisted writer EARNS ``user_stated`` (``resolve_write``): it read the user's turn
and the user's OWN text supports the fact (``supports``) — subject, value and attribute in one
user sentence, and the user speaking in the first person for a fact about themself. Assistant
turns never count; neither does a third person's name that merely appears in a transcript.

Your own change of mind (ZMB C1 / C5): a PER-TURN writer (``turn_digest`` / ``voice_turn_digest``) whose
anchor is the user's own turn and whose fact that turn ENTAILS - one verbatim sentence of it holds the
subject, value, attribute, polarity and tense (``supporting_span`` / ``supports``) - from a speaker the lane did
not reject (``speaker_verified`` is not False), about the speaker, is the user speaking: it is stamped
``user_stated_derived`` / basis ``verbatim_user_span`` (the honest provenance) and carries ``user_stated``
POWER and standing (``Resolved.promoted``; ``row_class`` reads it back as ``user_stated``), so "I moved to Hobart"
updates "User lives in Perth" and "I don't see Dana anymore" retires "User's dentist is Dana" instead of waiting as
a ``disputed`` candidate. The paraphrase-drift concern is answered by the entailment check, not by rank: a
paraphrase the turn does not entail (a different value, a hypothetical, a relative's fact, a negation the turn
lacks) stays ``user_stated_derived`` rank 3 / ``model_from_turn`` and is held back exactly as before. The nightly
(whole-day transcript) writers are NOT promoted: a transcript has no single turn to quote.

An unverified speaker's self-fact (``user_unverified``) is never the owner's fact: ``MemoryService.ingest``
stores it ``pending`` (a candidate the owner confirms), not ``approved`` (``is_self_assertion``).

``ZOE_MEMORY_AUTHORITY`` = ``enforce`` (default) | ``shadow`` (log
``AUTHORITY_WOULD_BLOCK``, change nothing) | ``off``. Provenance is stamped in every mode.
Default argued in docs/knowledge/memory-authority.md: the wall only ever parks a MODEL's
overwrite of something the user said as a candidate (lossless, reversible); a writer that has
the user's turn earns ``user_stated`` through ``supports``, so what the user said is never
refused.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORY_AUTHORITY"

# ── provenance classes (rank = power over existing rows) ─────────────────────
OPERATOR = "operator"
USER_CONFIRMED = "user_confirmed"
USER_STATED = "user_stated"            # the person's own words, or a deterministic extractor over them
USER_STATED_DERIVED = "user_stated_derived"  # a MODEL's paraphrase that the user's own turn supports
USER_UNVERIFIED = "user_unverified"   # voice turn attributed only by panel binding (P1.3, follow-up)
MODEL_FROM_TURN = "model_from_turn"
MODEL_FROM_TRANSCRIPT = "model_from_transcript"
RANK = {
    OPERATOR: 6, USER_CONFIRMED: 5, USER_STATED: 4, USER_STATED_DERIVED: 3, USER_UNVERIFIED: 2,
    MODEL_FROM_TURN: 1, MODEL_FROM_TRANSCRIPT: 0,
}
CLASSES = tuple(RANK)
#: a class at or above this is the PERSON speaking DIRECTLY: only these always override. A
#: model's paraphrase of the user (rank 3) beats model classes but never a direct statement -
#: both would otherwise be "user_stated" and the NEWER one would win, so a mis-paraphrase of an
#: older sentence in a day transcript could overwrite what the person said later.
USER_RANK = RANK[USER_STATED]
DERIVED_RANK = RANK[USER_STATED_DERIVED]

# The owner's three-way view, derived from the class.
INFERRED = "inferred"
AUTHORITIES = (USER_STATED, USER_CONFIRMED, INFERRED)
PROTECTED = frozenset({USER_STATED, USER_CONFIRMED})


def authority_of(cls: str) -> str:
    if cls in (OPERATOR, USER_CONFIRMED):
        return USER_CONFIRMED
    if cls in (USER_STATED, USER_STATED_DERIVED):
        return USER_STATED
    return INFERRED


# Provenance keys the edit path must NEVER carry forward from the row it supersedes.
PROVENANCE_KEYS = frozenset({
    "authority", "authority_class", "authority_basis", "origin", "turn_ref", "model",
    "contradicts_id", "authority_blocked", "duplicate_of",
})


def mode() -> str:
    """``enforce`` (default) | ``shadow`` | ``off``. Per-call env read."""
    raw = os.environ.get("ZOE_MEMORY_AUTHORITY")
    if raw is None:
        return "enforce"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    if v == "shadow":
        return "shadow"
    return "enforce"


def enabled() -> bool:
    """Is the wall ACTING (``enforce``)?"""
    return mode() == "enforce"


AFFECT_ENV = "ZOE_AFFECT_CONSENT_GATE"
AFFECT_KEYS = ("affect", "valence", "intensity")


def affect_gate_mode() -> str:
    """Who may have an affective record kept. Per-call env read.

    * ``household`` (DEFAULT, owner decision 2026-10-05): every household member INCLUDING children;
      never a guest; an unknown / failed lookup refuses. No stored consent row is required.
    * ``members``: adult members only (a member flagged a minor is refused), no stored consent.
    * ``optin``: adult members with a stored persona mode (the consent row); fails closed.
    * ``off``: no gate.

    An unrecognised value (a typo) falls to the STRICTEST gate, ``optin``, never to the default."""
    raw = os.environ.get("ZOE_AFFECT_CONSENT_GATE")
    if raw is None:
        return "household"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    if v in ("household", "members"):
        return v
    return "optin"


def is_affective(memory_type: Optional[str], metadata: Optional[Mapping[str, Any]] = None) -> bool:
    """An ``emotional_moment`` row: a RECORD of how someone seems (governance note section 6)."""
    return (memory_type or "") == "emotional_moment"


def carries_affect(metadata: Optional[Mapping[str, Any]]) -> bool:
    """An ordinary fact carrying a feeling in its metadata (``affect`` / ``valence`` /
    ``intensity``, stored ``candidate_``-prefixed)."""
    md = metadata or {}
    return any(md.get(k) or md.get(f"candidate_{k}") for k in AFFECT_KEYS)


def active() -> bool:
    """Is the wall at least watching (``shadow`` or ``enforce``)?"""
    return mode() != "off"


# ── writer allow-list ─────────────────────────────────────────────────────────

def _automatic_sources() -> frozenset[str]:
    try:
        from identity_facts import AUTOMATIC_SOURCES

        return frozenset(AUTOMATIC_SOURCES)
    except Exception:  # noqa: BLE001 — the class must degrade, never break a write
        return frozenset()


OPERATOR_WRITERS = frozenset({
    "operator", "operator-cleanup", "identity_audit", "admin", "system",
    "samantha_live_cleanup",
})
#: The review UI IS the person: approving / editing a row there is "yes, that's right".
USER_CONFIRMED_WRITERS = frozenset({"review_ui", "user_confirmed"})
#: The person's own words, typed or dictated, or a deterministic extractor over their turn
#: (assistant text is never mined — tests/test_memory_extractor_purity.py).
USER_STATED_WRITERS = frozenset({
    "voice_fact", "proposal", "manual", "explicit_teach", "conversation_correction", "chat", "chat_regex",
    "chat_regex_fallback", "voice_regex", "conversation", "voice", "skybridge_action",
})
DETERMINISTIC_USER_WRITERS = frozenset({"chat_regex", "chat_regex_fallback", "voice_regex", "conversation", "voice"})
USER_STATED_PREFIXES = ("note_", "journal_")   # note_<action>, journal_<action> (the UI)
#: the people-UI mirror rows ("person_created" / "person_updated"): exact names, NOT a prefix -
#: "person_extractor_llm" is a model.
USER_STATED_WRITERS_EXTRA = frozenset({"person_created", "person_updated", "person_deleted"})
#: Writers fed by the panel / voice lane: their turns can be mis-attributed to the bound member.
VOICE_LANE_WRITERS = frozenset({"voice_fact", "voice_regex", "voice", "voice_turn_digest"})
#: Model writers that READ one user turn: user_stated only when that turn supports the fact.
MODEL_FROM_TURN_WRITERS = frozenset({
    "turn_digest", "voice_turn_digest", "person_extractor_llm", "brain_tool", "mcp",
    "zoe_agent", "decay_sweep",
    # the one "User pasted an email" note a pasted turn leaves (own_words): never anchored, so never user_stated
    "pasted_content",
})
#: Per-turn model writers whose anchor is ONE user turn: when that turn entails the fact (and the speaker is not
#: rejected) the write carries ``user_stated`` power (``VERBATIM_BASIS``) - the owner's own change of mind, stated plainly.
SPAN_WRITERS = frozenset({"turn_digest", "voice_turn_digest"})
VERBATIM_BASIS = "verbatim_user_span"
#: The teach lane: the text (or the user turn it was dictated from) IS the owner's own words.
TEACH_WRITERS = frozenset({"voice_fact", "review_ui", "explicit_teach"})
#: Writers whose evidence (``source_excerpt``) is, by contract, a USER turn: a row from one of them says which turn.
USER_TURN_WRITERS = DETERMINISTIC_USER_WRITERS | TEACH_WRITERS
#: Model writers that read a whole day's transcript: same, against the user turns of it.
TRANSCRIPT_WRITERS = frozenset({"digest", "idle_consolidation"})
#: Automatic writers that never read a user turn (beyond the two sets above).
INFERRED_ONLY_AUTOMATIC = frozenset({
    "consolidation", "synthesis", "music_digest", "ambient", "implicit_supersede",
    "profile-analysis", "hindsight_retain_candidate", "decay_sweep", "mcp",
})
#: Writers that never have user text behind them (and every UNKNOWN writer): rank 0.
LLM_WRITERS = frozenset({
    "turn_digest", "voice_turn_digest", "digest", "idle_consolidation", "consolidation",
    "synthesis", "music_digest", "profile-analysis", "person_extractor_llm",
})


def writer_class(writer: str, *, user_id: str = "") -> str:
    """The class a writer has BEFORE any anchor is considered (unanchored model writers sit
    at their floor; unknown writers at rank 0)."""
    w = (writer or "").strip()
    if w in OPERATOR_WRITERS:
        return OPERATOR
    if w in USER_CONFIRMED_WRITERS or (user_id and w == user_id):
        return USER_CONFIRMED
    if w in USER_STATED_WRITERS or w in USER_STATED_WRITERS_EXTRA or w.startswith(USER_STATED_PREFIXES):
        return USER_STATED
    if w in MODEL_FROM_TURN_WRITERS:
        return MODEL_FROM_TURN
    return MODEL_FROM_TRANSCRIPT


def writer_is_inferred(writer: str, *, user_id: str = "") -> bool:
    """True when this writer, unanchored, is below the user classes (the actors the wall can
    refuse). ``writer == user_id`` is the account acting on its own rows."""
    return RANK[writer_class(writer, user_id=user_id)] < USER_RANK


def is_known_writer(writer: str) -> bool:
    """On the allow-list (a lane / writer name, as opposed to an account id or a stray
    string): only these become a row's ``source``."""
    w = (writer or "").strip()
    return (w in OPERATOR_WRITERS or w in USER_CONFIRMED_WRITERS or w in USER_STATED_WRITERS
            or w in USER_STATED_WRITERS_EXTRA or w.startswith(USER_STATED_PREFIXES) or w in MODEL_FROM_TURN_WRITERS
            or w in TRANSCRIPT_WRITERS or w in LLM_WRITERS or w in _automatic_sources())


def model_for(writer: str) -> str:
    if (writer or "").strip() not in LLM_WRITERS:
        return ""
    return os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")


@dataclass(frozen=True)
class Resolved:
    cls: str
    basis: str
    #: a per-turn model writer whose anchor turn ENTAILS the fact (``VERBATIM_BASIS``): the class stays the honest
    #: ``user_stated_derived``, the power and standing are ``user_stated``'s
    promoted: bool = False

    @property
    def authority(self) -> str:
        return authority_of(self.cls)

    @property
    def rank(self) -> int:
        """STANDING: how well the row it becomes is protected from later writes."""
        return USER_RANK if self.promoted else RANK[self.cls]

    @property
    def power(self) -> int:
        """POWER over existing rows (= ``rank``: a model's paraphrase of the user never
        overrides a direct statement, however fresh the turn it paraphrases) - UNLESS the user's own turn
        entails it verbatim (``promoted``): then the user speaking, at ``user_stated`` power."""
        return USER_RANK if self.promoted else RANK[self.cls]


def resolve_write(writer: str, text: str, *, anchor_text: Optional[str] = None,
                  claimed: Optional[str] = None, user_id: str = "",
                  prompt_text: Optional[str] = None,
                  speaker_verified: Optional[bool] = None) -> Resolved:
    """The class a NEW row gets, from who is writing it and what the user's own turn says.
    ``claimed`` can only DOWNGRADE (``inferred``), or confirm (``user_confirmed``) for a
    user-class writer — a model writer cannot claim authority; only the anchor gives it."""
    w = (writer or "").strip()
    base = writer_class(w, user_id=user_id)
    if claimed == INFERRED:
        return Resolved(MODEL_FROM_TRANSCRIPT, "claimed_inferred")
    if RANK[base] >= USER_RANK:
        # P1.3 hook: a VOICE-lane self-fact whose speaker the speaker-id did not confirm is
        # `user_unverified` (a guest, or another member, may be talking to a panel bound to
        # the owner). ``None`` = the lane does not report a verdict yet (today: the voice
        # daemon does not) -> unchanged. Only a self-fact; only the voice-lane writers.
        if (speaker_verified is False and w in VOICE_LANE_WRITERS
                and _USER_SUBJECT_RE.match(text or "")):
            return Resolved(USER_UNVERIFIED, "speaker_not_verified")
        if claimed == USER_CONFIRMED and base == USER_STATED:
            return Resolved(USER_CONFIRMED, "user_confirmed")
        return Resolved(base, "deterministic_user_turn" if w in DETERMINISTIC_USER_WRITERS
                        else "explicit_source")
    if w in MODEL_FROM_TURN_WRITERS or w in TRANSCRIPT_WRITERS:
        if (anchor_text or prompt_text) and supports(text, anchor_text or "", prompt_text):
            if speaker_verified is False and w in VOICE_LANE_WRITERS:
                return Resolved(USER_UNVERIFIED, "speaker_not_verified")
            if (w in SPAN_WRITERS and anchor_text and is_self_assertion(text)
                    and entailing_span(text, anchor_text) is not None):
                # the owner's own words, one verbatim sentence, ENTAIL the fact: their change of mind
                # (C1 / C5) is the user speaking, not a model's guess at them
                return Resolved(USER_STATED_DERIVED, VERBATIM_BASIS, promoted=True)
            return Resolved(USER_STATED_DERIVED, "anchored_user_turn")
        if w in TRANSCRIPT_WRITERS and anchor_text and observation_gate_mode() != "off":
            # a nightly paraphrase that joins two things the owner put in ONE sentence ("X accepted the offer from Y" from
            # "X got the offer from Y!"): the owner's words carry it, the wording is the model's (``costated_span``)
            got = costated_span(text, anchor_text)
            if got:
                return Resolved(USER_STATED_DERIVED, COSTATED_BASIS)
        return Resolved(base, "unanchored" if anchor_text else "no_user_evidence")
    return Resolved(MODEL_FROM_TRANSCRIPT, "automatic_writer")


def row_power(meta: Mapping[str, Any], text: str = "") -> int:
    """Power over existing rows of the write that produced this row (= its rank)."""
    return RANK[row_class(meta, text)]


def may_override(power: int, target_cls: str) -> bool:
    """May a write of this POWER supersede / archive / contradict a row of ``target_cls``?
    A direct user class always may (a later statement of the person's wins); below that,
    power >= the row's rank."""
    if target_cls == OPERATOR:
        # an operator's deliberate cleanup is not undone by a spoken sentence a panel may have
        # mis-attributed (P1.3): it takes a confirming action (the review UI) or an operator
        return power >= RANK[USER_CONFIRMED]
    return power >= USER_RANK or power >= RANK.get(target_cls, 0)


def provenance(writer: str, res: Resolved, *, turn_ref: Optional[str] = None,
               model: Optional[str] = None) -> dict[str, Any]:
    """The flat metadata a row carries about WHO wrote it (chroma-safe scalars)."""
    out: dict[str, Any] = {
        "authority": res.authority,
        "authority_class": res.cls,
        "authority_basis": res.basis,
        "origin": (writer or "")[:64],
    }
    if turn_ref:
        out["turn_ref"] = str(turn_ref)[:128]
    m = model or model_for(writer)
    if m:
        out["model"] = str(m)[:96]
    return out


# ── reading a row's class (stored, else derived) ──────────────────────────────

def row_class(meta: Mapping[str, Any], text: str = "") -> str:
    """The class a stored row has: the stamped ``authority_class``, else a stamped
    ``authority`` mapped to its floor, else ``legacy_class``."""
    meta = meta or {}
    c = str(meta.get("authority_class") or "")
    if c in RANK:
        if c == USER_STATED_DERIVED and str(meta.get("authority_basis") or "") == VERBATIM_BASIS:
            return USER_STATED   # the user's own turn entailed it verbatim: standing (and power) of the user's words
        return c
    a = str(meta.get("authority") or "")
    if a in AUTHORITIES:
        return {USER_STATED: USER_STATED, USER_CONFIRMED: USER_CONFIRMED,
                INFERRED: MODEL_FROM_TRANSCRIPT}[a]
    return legacy_class_basis(meta, text)[0]


def row_authority(meta: Mapping[str, Any], text: str = "") -> str:
    return authority_of(row_class(meta, text))


def row_rank(meta: Mapping[str, Any], text: str = "") -> int:
    return RANK[row_class(meta, text)]


def legacy_class_basis(meta: Mapping[str, Any], text: str = "") -> tuple[str, str]:
    """``(class, basis)`` for a row written before provenance existed (the backfill rule).

    * reviewed by a MODEL (digest / consolidation / turn_digest / ...): inferred — the text
      is that writer's whatever ``source`` was carried forward (the 2026-10-05 incident row);
    * reviewed by a person: ``user_confirmed``;
    * source on the user allow-list (voice_fact, chat_regex, review_ui, notes ...): the
      class of that source;
    * source a model writer that READ the user's turn: ``user_stated`` only if the stored
      ``source_excerpt`` (the user's utterance) supports the text, else its floor;
    * any other source (unknown = rank 0): ``model_from_transcript``.
    """
    reviewed_by = str(meta.get("reviewed_by") or "").strip()
    source = str(meta.get("source") or meta.get("added_by") or "").strip()
    uid = str(meta.get("user_id") or meta.get("wing") or "")
    src_cls = writer_class(source, user_id=uid)
    if reviewed_by and _is_model_actor(reviewed_by) and reviewed_by != uid:
        return (MODEL_FROM_TURN if reviewed_by in MODEL_FROM_TURN_WRITERS
                else MODEL_FROM_TRANSCRIPT), "legacy_reviewed_by_model"
    if RANK[src_cls] >= USER_RANK:
        return src_cls, "legacy_user_path"
    if source in MODEL_FROM_TURN_WRITERS or source in TRANSCRIPT_WRITERS:
        excerpt = str(meta.get("source_excerpt") or meta.get("candidate_source_excerpt") or "")
        if excerpt and text and supports(text, excerpt):
            return USER_STATED_DERIVED, "legacy_anchored_excerpt"
        if reviewed_by:
            return USER_CONFIRMED, "legacy_reviewed_by_person"
        return src_cls, "legacy_unanchored"
    if reviewed_by:
        return USER_CONFIRMED, "legacy_reviewed_by_person"
    return MODEL_FROM_TRANSCRIPT, "legacy_automatic_source"


def _is_model_actor(name: str) -> bool:
    """A reviewer that is a MODEL / batch pass, as opposed to a person. Only for reading
    LEGACY ``reviewed_by`` values: an account id or any other label that names no known
    automatic writer is a person (a live WRITE from an unknown label is still rank 0)."""
    n = (name or "").strip()
    return (n in MODEL_FROM_TURN_WRITERS or n in TRANSCRIPT_WRITERS or n in LLM_WRITERS
            or n in INFERRED_ONLY_AUTOMATIC or n in _automatic_sources())


def legacy_class(meta: Mapping[str, Any], text: str = "") -> str:
    return legacy_class_basis(meta, text)[0]


def legacy_authority_basis(meta: Mapping[str, Any], text: str = "") -> tuple[str, str]:
    cls, basis = legacy_class_basis(meta, text)
    return authority_of(cls), basis


def is_protected(meta: Mapping[str, Any], text: str = "") -> bool:
    return row_authority(meta, text) in PROTECTED


# ── anchoring: does the USER'S OWN turn support this fact? ────────────────────

_FIRST_PERSON = frozenset({"i", "im", "i'm", "ive", "i've", "id", "i'd", "ill", "i'll", "my",
                           "me", "mine", "myself", "we", "were", "we're", "our", "ours", "us",
                           "weve", "we've"})
_USER_SUBJECT_RE = re.compile(r"^\s*(?:the\s+)?(?:user|speaker|i|my)\b", re.IGNORECASE)

# Words that carry no claim (articles, copulas, the frame of a negation / change cue).
_STOP = frozenset("""
the and but for with from into onto this that these those there here their them they his her its
user users speaker have has had was were been being are not never longer anymore any more
dropped stopped quit cancelled canceled gave given used really very just also still currently now then than year years
who whom what when where while which about some one ones got get gets going doing does did
""".split())
_DIGIT_ORD = re.compile(r"^(\d+)(?:st|nd|rd|th)$")

# Attribute CUE classes: a fact that names an attribute ("name", where they live, work, age,
# birthday, a liking, an allergy) is supported only by a user sentence that names it too —
# this is what separates "my name is X" from a transcript that merely MENTIONS X.
_CUE_WORDS = {
    "name": "name names named call called calling",
    "home": "live lives lived living move moved moving reside resides resided based settle "
            "settled home house from stay stays relocate relocated",
    "work": "work works worked working job jobs employ employed employer career occupation "
            "profession company business hired start started join joined",
    "age": "old age aged turn turned",
    "birth": "birthday born bday birth dob",
    "like": "like likes liked love loves loved enjoy enjoys prefer prefers preferred favourite "
            "favorite fan adore adores into",
    "allergy": "allergic allergy allergies intolerant intolerance",
}


#: nouns that END in s but are not plurals: "Good news: I switched to new glasses" is not the word "new"
_NO_PLURAL = frozenset({"news", "series", "species", "lens"})


def _stem(tok: str) -> str:
    t = tok.lower().strip("'’")
    if t.endswith(("'s", "’s")):
        t = t[:-2]
    m = _DIGIT_ORD.match(t)
    if m:
        return m.group(1)
    if t in _NO_PLURAL:
        return t
    undouble = False
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
        undouble = True
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
        undouble = True
    elif len(t) > 4 and t.endswith("es"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    if undouble and len(t) > 3 and t[-1] == t[-2] and t[-1] in "bgmnprt":
        t = t[:-1]   # "getting" -> "get", "dropped" -> "drop", "planned" -> "plan" (the inflection doubled it)
    return t


_CUE_OF: dict[str, str] = {_stem(w): cls for cls, words in _CUE_WORDS.items() for w in words.split()}


# First-person present / change-of-state shapes that name the attribute without its noun:
# "I'm 41 now" (age), "I'm in Perth now" (home), "I'm at Acme now" (work), "I'm Alex" (name).
_SHAPE_CUES = (
    ("age", re.compile(r"\bI(?:['’]m| am)\s+\d{1,3}\b|\bturn(?:ed|ing|s)?\s+\d{1,3}\b", re.IGNORECASE)),
    ("home", re.compile(r"\bI(?:['’]m| am)\s+(?:now\s+)?in\s+\w+(?:\s+\w+){0,3}?\s+now\b"
                        r"|\bI(?:['’]m| am)\s+now\s+in\s+\w+", re.IGNORECASE)),
    ("work", re.compile(r"\bI(?:['’]m| am)\s+(?:now\s+)?at\s+\w+(?:\s+\w+){0,3}?\s+now\b"
                        r"|\bI(?:['’]m| am)\s+now\s+at\s+\w+", re.IGNORECASE)),
    ("name", re.compile(r"\b(?:I(?:['’]m| am)|this is|it(?:['’]s| is))\s+[A-Z][a-z]+")),
)


def _window_cues(win: str, stems: set[str]) -> set[str]:
    out = {_CUE_OF[s] for s in stems if s in _CUE_OF}
    out |= {cls for cls, rx in _SHAPE_CUES if rx.search(win)}
    return out


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


_ATTACHED_NOT_RE = re.compile(r"^(?P<head>.+?)-+(?P<neg>not)$", re.IGNORECASE)


def _words(text: str) -> list[str]:
    out: list[str] = []
    for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", _fold(text)):
        # "Bendigo-not Ballarat": an attached hyphen would fuse the value with the contrast word
        m = _ATTACHED_NOT_RE.match(w)
        out += [m.group("head"), m.group("neg")] if m else [w]
    return out


def _sentences(text: str) -> list[str]:
    parts = [p for p in re.split(r"(?<=[.!?])\s+|\n+", text or "") if p and p.strip()]
    return parts or ([text] if (text or "").strip() else [])


def _normalise_dates(text: str) -> str:
    try:
        from date_locale import normalize_numeric_dates

        return normalize_numeric_dates(text)
    except Exception:  # noqa: BLE001
        return text


#: A bare base-form action verb in a fact ("plans to stop treatment", "will cancel the booking", "a drop in
#: price") is the CLAIM: the user's sentence must say that verb (in any form). The 60%-of-other-words
#: tolerance would otherwise let "I plan to continue treatment" support its opposite (review of #1913;
#: true on main before this PR too).
_ACTION_FAMILIES = {
    "drop": re.compile(r"\bdrop(?:s|ped|ping)?\b", re.IGNORECASE),
    "stop": re.compile(r"\bstop(?:s|ped|ping)?\b", re.IGNORECASE),
    "cancel": re.compile(r"\bcancel(?:s|led|ed|ling|ing)?\b", re.IGNORECASE),
    "quit": re.compile(r"\bquit(?:s|ting)?\b", re.IGNORECASE),
    "leave": re.compile(r"\b(?:leave|leaves|left|leaving)\b", re.IGNORECASE),
}


def _required_actions(fact: str) -> list["re.Pattern[str]"]:
    plain = _ENDED_BASE_RE.sub(" ", fact or "")   # "did not drop X": a cue, not a claim word
    return [_ACTION_FAMILIES[w.lower()] for w in _words(plain) if w.lower() in _ACTION_FAMILIES]


def _fact_parts(fact: str) -> tuple[bool, set[str], set[str], set[str]]:
    """``(about_user, value_tokens, cue_classes, other_tokens)`` of a stored fact."""
    about_user = bool(_USER_SUBJECT_RE.match(fact or ""))
    value: set[str] = set()
    cues: set[str] = set()
    other: set[str] = set()
    # "did not drop X": the end-state phrase is a CUE (polarity + ended, enforced in the statement check),
    # not a content word the user has to repeat - the bare verbs stay content ("plans to stop treatment").
    for i, w in enumerate(_words(_ENDED_BASE_RE.sub(" ", fact or ""))):
        low = w.lower()
        base = re.sub(r"['’]s$", "", low)
        if low in _STOP or base in _STOP or low in _FIRST_PERSON:
            continue
        s = _stem(low)
        if not s:
            continue
        if s in _CUE_OF:
            cues.add(_CUE_OF[s])
            continue
        if any(ch.isdigit() for ch in w) or (w[:1].isupper() and i > 0 and low not in {"user", "users"}):
            value.add(s)
        elif len(s) > 2:
            other.add(s)
    if other:
        # "User's dog is named Teddy": the dog + the name are the claim, and "my dog Teddy"
        # states both. Only a fact whose attribute IS the name ("User's name is X") needs
        # the user to say "name" / "called".
        cues.discard("name")
    return about_user, value, cues, other


_RELATION = (r"(?:wife|husband|partner|girlfriend|boyfriend|fianc\w*|spouse|son|daughter|kids?|child|"
             r"children|brother|sister|mum|mom|mother|dad|father|grandma|grandmother|grandpa|"
             r"grandfather|aunt|uncle|cousin|niece|nephew|friend|mate|boss|colleague|coworker|"
             r"neighbou?r|parents?|sibling|in-laws?|family|baby|girls|boys|ex)")
#: "my sister", "my wife's", "our best friend": a possessive of ANOTHER PERSON
_MY_RELATION_RE = re.compile(rf"\b(?:my|our)\s+(?:[a-z]+\s+){{0,2}}?({_RELATION})s?(?:['’]s)?\b", re.IGNORECASE)
_NEG_RE = re.compile(r"\b(?:not|never|no|none|nobody|nothing|neither|nor)\b|n['’]t\b|\bany ?more\b",
                     re.IGNORECASE)
_USED_TO_RE = re.compile(r"\bused to\b|\bformerly\b|\bpreviously\b|\bwas living\b", re.IGNORECASE)
#: a stated END of a state ("no longer" / "any more" are also negations above)
#: the BASE form after a negated auxiliary is the same end-state verb: the digest words a denial "User did not
#: drop X", the owner says "I haven't dropped X" (review of #1913)
_ENDED_BASE = r"\b(?:did|do|does|will|would|can|could)(?:\s+not|n['\u2019]t)\s+(?:drop|quit|stop|cancel|give\s+up|leave)\b"
_ENDED_BASE_RE = re.compile(_ENDED_BASE, re.IGNORECASE)
_ENDED_RE = re.compile(r"\b(?:stopped|dropped|quit|cancell?ed|gave up|given up|ended|left|no longer)\b|\bany ?more\b|"
                       + _ENDED_BASE, re.IGNORECASE)
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:wish|if|maybe|perhaps|might|hope|hoping|someday|supposedly|apparently|imagine|pretend|"
    r"would|could)\b", re.IGNORECASE)
_QUESTION_START_RE = re.compile(r"^\s*(?:do|does|did|am|are|is|can|could|will|would|should)\s+"
                                r"(?:i|we|you|my)\b", re.IGNORECASE)


_LEAD_INTERJECTION_RE = re.compile(r"^\s*(?:(?:no|nope|nah|yes|yeah|yep|actually|well|oh|sorry|um|uh|hi|hey)[,.!\s]+)+",
                                   re.IGNORECASE)


# "my mum lives in Bendigo, NOT BALLARAT" - a CONTRAST names the value being corrected away; it is
# not a denial of the fact beside it. Counting that "not" as a polarity mismatch made the owner's own
# explicit correction unsupported (class model_from_turn), so the stale row it corrects OUTRANKED it:
# the new value was parked as a dispute, the old one stayed approved and was served as current
# (day-sim "how's my mum" 2026-10-07: "...getting good care in Ballarat"). Only a clause that
# names something the fact does not is a contrast; "not in Perth" beside "User lives in Perth" is
# a denial and stays one.
# The delimiter is a comma / semicolon or a dash, spaced OR attached ("Bendigo\u2014not Ballarat",
# "Bendigo - not Ballarat", "Bendigo-not Ballarat"; ``_words`` splits an attached "-not" off its value).
# A bare "but not" / "and not" is a delimiter too: speech-to-text carries no commas ("lives in Bendigo but
# not Ballarat", "a nurse and not a doctor").
_CONTRAST_RE = re.compile(r"(?:(?:,|;|\s*[-\u2013\u2014]+)\s*(?:and\s+|but\s+)?|\s+(?:and|but)\s+)"
                          r"not\s+(?P<neg>[^,;.!?]{1,40}?)\s*(?=[,;.!?]|$)", re.IGNORECASE)
_NOT_A_CONTRAST = frozenset({"sure", "really", "yet", "quite", "very", "just", "too", "even", "much", "anymore",
                             "any", "always", "often", "now", "going", "been", "true", "right", "well", "good",
                             "great", "bad", "happy", "ok", "okay", "if", "when", "that", "this", "so"})


_ARTICLES = frozenset({"a", "an", "the", "in", "at", "on", "to", "of"})
#: a TIME qualifier is not a corrected-away value: "but not at the moment" denies the fact for now
_TEMPORAL = frozenset({"moment", "now", "today", "tonight", "currently", "present", "lately", "recently", "days",
                       "week", "weeks", "month", "months", "year", "years", "weekend", "yesterday", "tomorrow",
                       "ever", "then", "atm", "time", "times", "longer", "anymore", "morning", "afternoon",
                       "evening", "night", "while", "meantime", "mean", "moment's"})
#: a clause that points BACK at the fact ("not there", "not in it", "not that place") stems to nothing
#: the fact says, but it is a denial OF the fact - never a corrected-away value (review of #1913).
_ANAPHORA = frozenset({"there", "here", "it", "its", "that", "this", "those", "these", "them", "they", "him",
                       "her", "she", "he", "so", "same", "such"})


def _without_contrast(win: str, fact: str) -> str:
    """``win`` with each ", not <other value>" correction clause removed for the POLARITY check
    only. A clause whose words appear in the fact (or that is a hedge, "not sure") is kept."""
    fact_stems = {_stem(w) for w in _words(fact or "") if w.lower() not in _STOP | _ARTICLES}

    def keep_or_drop(m: "re.Match[str]") -> str:
        neg = _words(m.group("neg"))
        if not neg or len(neg) > 4 or neg[0].lower() in _NOT_A_CONTRAST:
            return m.group(0)
        if any(w.lower() in _TEMPORAL for w in neg):
            return m.group(0)   # "but not at the moment": a denial for now, not a corrected-away value
        if any(w.lower() in _ANAPHORA for w in neg):
            return m.group(0)   # "not there" / "not in it": a denial of the fact, not a corrected-away value
        if {_stem(w) for w in neg if w.lower() not in _STOP | _ARTICLES} & fact_stems:
            return m.group(0)
        return ""
    return _CONTRAST_RE.sub(keep_or_drop, win)


_ENDED_VERB_RE = re.compile(r"\b(?:stopped|dropped|quit|cancell?ed|gave up|given up|ended|left)\b|" + _ENDED_BASE,
                            re.IGNORECASE)


_CLAUSE_BREAK_RE = re.compile(r"[,;.!?]|\b(?:so|but|and|because|then|though|although|while)\b", re.IGNORECASE)


def _negated(text: str) -> bool:
    """Effective polarity of the STATE: "I've dropped / quit / stopped / cancelled X" is the owner's word
    that X is over - the polarity of the fact "User no longer does X" (day-sim race swap,
    AUTHORITY_BLOCKED writer=turn_digest action=supersede). An end-state VERB makes a plain sentence
    negative; a negation BOUND to that verb ("I haven't dropped X", "did not drop X": same clause, before
    it) says the end did not happen, so the denial of an end never matches the end itself. A negation
    elsewhere ("I did not enjoy X, so I dropped it") is about something else (review of #1913). The
    tense/ended cue below still has to agree, so "I live in X" never supports "User quit living in X"."""
    m = _ENDED_VERB_RE.search(text)
    if not m:
        return bool(_NEG_RE.search(text))
    if _ENDED_BASE_RE.match(text, m.start()):
        return False   # "did not drop": the negation is part of the verb phrase
    clause = _CLAUSE_BREAK_RE.split(text[:m.start()])[-1]
    return not _NEG_RE.search(clause)


def _window_is_a_statement_about_the_user(win: str, fact: str) -> bool:
    """The user's OWN, affirmative, first-person statement - not a question, a wish, a
    negation the fact does not share, or a sentence about someone else's relative.
    (Review of #1868: "my sister lives in Perth" must not support "User lives in Perth".)"""
    win = _LEAD_INTERJECTION_RE.sub("", win)
    if "?" in win or _QUESTION_START_RE.match(win):
        return False
    if _negated(_without_contrast(win, fact)) != _negated(fact or ""):
        return False
    if _HYPOTHETICAL_RE.search(win):
        return False
    # POLARITY / TENSE / CHANGE-OF-STATE must AGREE between the user's words and the fact (no
    # entailment across them): "I live in X" does not support "User no longer lives in X", and
    # "I used to live in X" does not support "User lives in X" (nor the reverse). The cue words
    # stay in _STOP for token coverage (paraphrase tolerance); this is where they are enforced.
    for cue in (_USED_TO_RE, _ENDED_RE):
        if bool(cue.search(win)) != bool(cue.search(fact or "")):
            return False
    fact_l = (fact or "").lower()
    for m in _MY_RELATION_RE.finditer(win):
        rel = m.group(1).lower()
        try:
            from memory_quality import _role_variants

            names = {v.lower() for v in _role_variants(rel)} | {rel}
        except Exception:  # noqa: BLE001
            names = {rel}
        if not any(re.search(rf"\b{re.escape(n)}s?\b", fact_l) for n in names):
            return False  # the sentence is about the user's relative; the fact does not say so
    return True


def supports(fact: str, user_text: str, prompt_text: Optional[str] = None) -> bool:
    """Does the user's OWN turn text support ``fact``?

    ``user_text`` is user turns only (the caller must never pass assistant text). True when
    one sentence (or two adjacent ones of the same turn) holds ALL of the fact's value
    tokens (names, numbers, places), the attribute CUE the fact names (name / where they
    live / work / age / birthday / a liking / an allergy), most of its other words, and —
    for a fact about the user — the user speaking in the first person. A third person's
    name merely appearing in a transcript supports nothing: "uh Casey is coming over, I
    think" does not support "User's name is Casey" (no name cue).

    ``prompt_text`` is the ASSISTANT question the user is answering ("Where do you live
    now?"). It is never evidence by itself - it only lets a SHORT elliptical answer ("no,
    Perth now", "it's Alex") be read in context: the answer must carry every value token of
    the fact, and the question must name the same attribute the fact does.
    """
    if not fact or not user_text:
        return False
    if next(_support_windows(fact, user_text), None) is not None:
        return True
    about_user, value, cues, other = _fact_parts(fact)
    if not (value or cues or other):
        return False
    return _answers_a_question(fact, value, cues, user_text, prompt_text)


def _support_windows(fact: str, user_text: str):
    """Every sentence (or two adjacent sentences of one turn) of ``user_text`` that supports ``fact`` - the
    DIRECT path of ``supports`` (no assistant question in play). The windows are cut from the date-normalised
    text; ``supporting_span`` / ``entailing_span`` return the verbatim form of one."""
    if not fact or not user_text:
        return
    about_user, value, cues, other = _fact_parts(fact)
    if not (value or cues or other):
        return
    actions = _required_actions(fact)
    # One TURN per line (the digests join user turns with newlines). A window is a sentence,
    # or two adjacent sentences of the SAME turn - never across turns: "my dog is Teddy"
    # on one turn plus "Rex is coming over" on the next is not "my dog is Rex".
    windows: list[str] = []
    for line in _normalise_dates(user_text).split("\n"):
        sents = _sentences(line)
        windows += sents + [f"{sents[i]} {sents[i + 1]}" for i in range(len(sents) - 1)]
    for win in windows:
        ws = _words(win)
        stems = {_stem(w) for w in ws}
        if value and not value <= stems:
            continue
        if cues and not cues <= _window_cues(win, stems):
            continue
        if other and len(other & stems) / len(other) < 0.6:
            continue
        if about_user and not any(w.lower() in _FIRST_PERSON for w in ws):
            continue
        if actions and not all(rx.search(win) for rx in actions):
            continue
        if not _window_is_a_statement_about_the_user(win, fact):
            continue
        yield win


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def supporting_span(fact: str, user_text: str) -> Optional[str]:
    """The user's own VERBATIM words that entail ``fact``: the sentence (or two adjacent sentences of one
    turn) ``supports`` accepts, only if it appears, whitespace-folded, in ``user_text`` exactly as the user
    wrote it - else None. The direct path only: an elliptical answer to an assistant question
    (``prompt_text``) is never a span, and a window the date normaliser rewrote is not verbatim."""
    return _verbatim(_support_windows(fact, user_text), user_text)


def _verbatim(windows, user_text: str) -> Optional[str]:
    squashed = _squash(user_text)
    for win in windows:
        w = _squash(win)
        if w and w in squashed:
            return w
    return None


#: a hedge, a report or a reported speaker is not a PLAIN statement ("a ferry company came up, I think", "Dana
#: said she lives in Perth", "apparently I moved"). supports() tolerates these (it only decides whether a fact
#: is the DERIVED one of the user); the power to overrule what they said before does not.
_PLAIN_FIRST_PERSON = frozenset("i im ive id ill my me mine myself we weve our ours us".split())
_HEDGE_VERBS = ("think", "guess", "suppose", "reckon", "believe", "figure", "assume", "wonder")
_HEDGE_WORDS = (
    "probably", "possibly", "apparently", "supposedly", "presumably", "sort of", "kind of", "came up",
    "comes up", "coming up", "heard", "rumour", "rumor", "someone", "somebody", "they say", "people say",
    "he says", "she says", "said", "told me", "tells me", "according to")
_PIPE = chr(124)
_HEDGE_RE = re.compile(
    r"\b(?:i" + _PIPE + r"we)\s+(?:" + _PIPE.join(_HEDGE_VERBS) + r")\b" + _PIPE
    + r"\b(?:" + _PIPE.join(re.escape(w) for w in _HEDGE_WORDS) + r")s?\b", re.IGNORECASE)


#: A CLOSED list of discourse labels. Anything else before a colon ("Dana's birthday: I organized a party on May 5.")
#: is a TOPIC - it says who the sentence is about - and is never stripped (review of #1916).
_LEAD_IN_LABEL_RE = re.compile(
    r"^\s*(?:(?:good|bad|great|big|sad|exciting|quick)\s+news|(?:quick\s+)?update|change\s+of\s+plans?|correction|fyi|"
    r"heads[\s-]?up|by\s+the\s+way|btw|also|oh\s+and)\s*:\s+(?=.*\b(?:I|we|my|our)\b)", re.IGNORECASE)


def _plainly_first_person(win: str, fact: str) -> bool:
    """The window is the speaker PLAINLY stating the fact about themself: no hedge or report in it, and the
    first-person word comes BEFORE the first word of the claim (its attribute cue, value or other content) -
    "I moved to Hobart", "I do not see Dana any more", "my dentist is Priya now"; not "a ferry company came up
    I think" (the "I" trails the claim) nor "Dana is my dentist" (a third person leads)."""
    if _HEDGE_RE.search(win):
        return False
    # a lead-in label ("Good news: I ...", "Change of plan: I ...") is not part of the claim
    lead = _LEAD_IN_LABEL_RE.match(win)
    if lead:
        win = win[lead.end():]
    toks = [w.lower().replace("’", "").replace(chr(39), "") for w in _words(win)]
    fp = next((i for i, w in enumerate(toks) if w in _PLAIN_FIRST_PERSON), None)
    if fp is None:
        return False
    _, value, cues, other = _fact_parts(fact)
    claim = value | other
    for i, w in enumerate(_words(win)):
        st = _stem(w)
        if st in claim or (cues and _CUE_OF.get(st) in cues):
            return fp < i
    return True


def entailing_span(fact: str, user_text: str) -> Optional[str]:
    """The user's own VERBATIM sentence that PLAINLY entails ``fact`` (a fact about the speaker), else None:
    ``supporting_span`` plus ``_plainly_first_person``. This is the test a per-turn model writer must pass to
    carry ``user_stated`` power (``VERBATIM_BASIS``) - the owner's own change of mind, stated plainly."""
    if not is_self_assertion(fact):
        return None
    return _verbatim((w for w in _support_windows(fact, user_text) if _plainly_first_person(w, fact)), user_text)


def is_self_assertion(text: str) -> bool:
    """Is this stored fact stated ABOUT the speaker ("User lives in ...", "User no longer sees ...",
    "my ...", "I ...")? The only facts a panel that did not verify the speaker may not state for the owner."""
    return bool(_USER_SUBJECT_RE.match(text or ""))


def turn_evidence(writer: str, res: "Resolved", text: str, *, user_id: str,
                  anchor_text: Optional[str], source_excerpt: Optional[str],
                  user_turn_id: Optional[str], teach: bool = True) -> tuple[Optional[str], Optional[str]]:
    """``(source_excerpt, user_turn_id)`` a row written from a user turn must carry (ZMB A3). What the
    CALLER gave wins; the gaps are filled here, at the one write boundary, from evidence that is the user's
    by construction:

    * the TEACH lane (``voice_fact`` / ``review_ui`` / ``explicit_teach``): the user's own turn
      (``anchor_text``) if the caller has it, else the taught text, which IS their words (``teach=False``
      for an EDIT: a correction made in the review UI is not a chat turn, so it invents no turn id);
    * a model writer whose anchor the user's turn supports (``user_stated_derived`` / ``user_unverified``):
      the verbatim sentence that supports it (``supporting_span``) - nothing when none does, because a row
      no user sentence supports has no user turn to point at.

    The turn id, when not given, is a stable content id of that evidence (never random: a replay of the
    same turn gives the same id). Rows of any other lane are left exactly as the caller built them.
    """
    w = (writer or "").strip()
    excerpt = (source_excerpt or "").strip() or None
    if excerpt is None:
        if w in TEACH_WRITERS:
            if teach:
                excerpt = (anchor_text or text or "").strip() or None
        elif res.cls in (USER_STATED_DERIVED, USER_UNVERIFIED) and anchor_text:
            excerpt = supporting_span(text, anchor_text)
    turn_id = (user_turn_id or "").strip() or None
    if turn_id is None and excerpt and (w in USER_TURN_WRITERS or res.cls in (USER_STATED_DERIVED, USER_UNVERIFIED)):
        basis = text if w in TEACH_WRITERS and not anchor_text else excerpt
        prefix = "fact-" if w in TEACH_WRITERS else "ut-"
        turn_id = prefix + hashlib.sha1(f"{user_id}|{_squash(basis).lower()}".encode("utf-8")).hexdigest()[:16]
    return excerpt, turn_id


#: a fact about one of the user's RELATIVES: "User's sister lives in Perth." / "My son is 12."
_FACT_RELATION_RE = re.compile(
    rf"^\s*(?:the\s+)?(?:user['’]s|my)\s+(?:[a-z]+\s+){{0,2}}?(?P<rel>{_RELATION})\b", re.IGNORECASE)
#: a relation (or any third-party possessive) in a QUESTION: "your sister", "Alice's brother"
_PROMPT_THIRD_PARTY_RE = re.compile(
    rf"\b(?:my|your|our|his|her|their|[a-z]+['’]s)\s+(?:[a-z]+\s+){{0,2}}?{_RELATION}s?\b", re.IGNORECASE)
_YOU_RE = re.compile(r"\byou(?:r|rs|rself)?\b|\byou['’](?:re|ve|d|ll)\b", re.IGNORECASE)
_NOT_A_NAME = frozenset({"user", "users", "the", "my", "i", "we", "our", "his", "her", "their", "a", "an"})


def _prompt_is_self_directed(prompt_text: str) -> bool:
    """The question is put to the user about THEMSELF: it says you / your / yourself and names
    no third party's possessive ("your sister", "Alice's brother")."""
    return bool(_YOU_RE.search(prompt_text)) and not _PROMPT_THIRD_PARTY_RE.search(prompt_text)


def _prompt_subject_matches(fact: str, prompt_text: str) -> bool:
    """Is the SUBJECT of the question the SUBJECT of the fact? (Review of #1868, Codex P1:
    "Where does your sister live?" -> "Perth" must not make "User lives in Perth." the user's.)

      * a fact about the user (or their pet / name / age): the question must be self-directed;
      * a fact about the user's relative ("User's sister ..."): the question must name that
        relation (or a synonym) - and so is not self-directed;
      * a fact about a named third person ("Alice works at Acme."): the question must name them;
      * anything else: no match (fail closed - the fact is then NOT user_stated_derived).
    """
    m = _FACT_RELATION_RE.match(fact or "")
    if m:
        rel = m.group("rel").lower()
        try:
            from memory_quality import _role_variants

            names = {v.lower() for v in _role_variants(rel)} | {rel}
        except Exception:  # noqa: BLE001
            names = {rel}
        return any(re.search(rf"\b{re.escape(n)}s?\b", prompt_text, re.IGNORECASE) for n in names)
    if _USER_SUBJECT_RE.match(fact or ""):
        return _prompt_is_self_directed(prompt_text)
    m = re.match(r"\s*([A-Z][\w'’\-]*)", _fold(fact or ""))
    if m and m.group(1).lower() not in _NOT_A_NAME:
        return re.search(rf"\b{re.escape(m.group(1))}(?:['’]s)?\b", _fold(prompt_text), re.IGNORECASE) is not None
    return False


def _answers_a_question(fact: str, value: set[str], cues: set[str], user_text: str,
                        prompt_text: Optional[str]) -> bool:
    """A short elliptical user answer to an assistant question about the fact's attribute."""
    if not prompt_text or "?" not in prompt_text or not value:
        return False
    p_stems = {_stem(w) for w in _words(prompt_text)}
    p_cues = {_CUE_OF[s] for s in p_stems if s in _CUE_OF}
    p_cues |= {"name"} if re.search(r"\bname\b", prompt_text, re.IGNORECASE) else set()
    if not cues or not cues <= p_cues:
        return False
    if not _prompt_subject_matches(fact, prompt_text):
        return False
    for line in _normalise_dates(user_text).split("\n"):
        ans = _LEAD_INTERJECTION_RE.sub("", line).strip()
        if not ans or "?" in ans or len(ans.split()) > 8 or _HYPOTHETICAL_RE.search(ans):
            continue
        if bool(_NEG_RE.search(ans)) != bool(_NEG_RE.search(fact or "")):
            continue
        if value <= {_stem(w) for w in _words(ans)}:
            return True
    return False


# ── same subject + same attribute, different value ───────────────────────────

# `works at/for X` is the voice extractor's own template (memory_extractor): the slash must read as the
# preposition, or an unconfirmed panel voice's "I work at Acme" sat beside the owner's job instead of
# being held back as a candidate (found by ZMB cell A6.panel_unverified.work).
_WORK_RE = re.compile(r"\bworks?\s+(?:at|for|as|in)(?:/(?:at|for))?:?\s+(?P<v>[\w'’\- ]{2,40}?)(?:[.,;!?]|$)", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(?P<v>\d{1,3})\s+years?\s+old\b|\baged?\s+(?P<w>\d{1,3})\b", re.IGNORECASE)


def kind_of(text: str) -> str:
    """A CLOSED-vocabulary label for what a fact is about (safe to log: never text)."""
    t = (text or "").lower()
    try:
        from identity_facts import asserted_user_name

        if asserted_user_name(text):
            return "name"
    except Exception:  # noqa: BLE001
        pass
    for kind, rx in (
        ("pet", r"\b(?:dog|cat|puppy|kitten|pet|rabbit|bird)\b"),
        ("relationship", r"\b(?:wife|husband|partner|spouse|girlfriend|boyfriend|son|daughter|"
                         r"brother|sister|mum|mom|mother|dad|father|friend|boss)\b"),
        ("birthday", r"\bbirthday\b|\bborn\b|\bdob\b"),
        ("age", r"\byears? old\b|\baged?\s+\d"),
        ("home", r"\blives?\b|\bliving\b|\bmoved\b|\bbased\b|\bhome\b"),
        ("work", r"\bworks?\b|\bjob\b|\bemploy|\bcareer\b|\boccupation\b"),
        ("health", r"\ballerg|\bintoleran|\bdiagnos|\bmedicat"),
        ("preference", r"\blikes?\b|\bloves?\b|\bprefers?\b|\bfavou?rite\b|\benjoys?\b"),
        ("name", r"\bname\b|\bcalled\b|\bnamed\b"),
    ):
        if re.search(rx, t):
            return kind
    return "other"


# Distilled third-person shapes ("User prefers tea."): a verb that takes ONE object at a time.
# Likes / loves / enjoys / takes / owns are deliberately absent - a person has several.
_EXCL_VERB_RE = re.compile(
    r"^\s*(?:the\s+)?user\s+(?P<verb>prefers|drives|studies|attends|supports)\s+(?P<obj>.+?)\s*[.!?]*\s*$",
    re.IGNORECASE)
#: values that exclude each other ("vegetarian" / "vegan"; "single" / "married")
_EXCL_GROUPS = (
    frozenset({"vegetarian", "vegan", "pescatarian", "pescetarian", "omnivore", "carnivore"}),
    frozenset({"single", "married", "divorced", "widowed", "engaged", "separated"}),
)
_OBJ_STOP = frozenset({"a", "an", "the", "to", "of", "my", "their", "his", "her"})


def _obj_tokens(obj: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", (obj or "").lower()) if t not in _OBJ_STOP)


def _verb_object(text: str) -> tuple[str, frozenset[str]]:
    m = _EXCL_VERB_RE.match(text or "")
    return (m.group("verb").lower(), _obj_tokens(m.group("obj"))) if m else ("", frozenset())


def _slot_value(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text or "")
    if not m:
        return ""
    v = next((g for g in m.groups() if g), "")
    return re.sub(r"\s+", " ", v).strip().lower()


# The people-graph writers store a person's date as a COMPACT row, "Alice: 15 March" (no verb, no
# attribute noun - ``memory_quality._attribute_key`` cannot read it). Two compact rows for the SAME
# name whose values are bare dates and name different days contradict each other.
_COMPACT_RE = re.compile(r"^\s*(?P<name>[A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*){0,2})\s*:\s*(?P<value>[^:\n]{1,40}?)\s*[.!?]*\s*$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
_DATE_FILLER = frozenset({"of", "th", "st", "nd", "rd", "the", "on", "in", "around", "about"})


def _bare_date(value: str) -> Optional[tuple[int, int]]:
    """``(month, day)`` when ``value`` is ONLY a calendar date ("March 15", "15th of March 1990",
    "15/03"), else None - a compact row that says anything else is not a date row."""
    v = _normalise_dates(value or "")
    month = day = None
    for tok in re.findall(r"[A-Za-z]+|\d+", _fold(v)):
        low = tok.lower()
        if tok.isdigit():
            if len(tok) <= 2 and day is None and 1 <= int(tok) <= 31:
                day = int(tok)
            elif len(tok) == 4:
                continue  # a year: the day is what identifies the date
            else:
                return None
        elif low in _DATE_FILLER:
            continue
        else:
            hit = next((n for m, n in _MONTHS.items() if low == m or (len(low) == 3 and m.startswith(low))), None)
            if hit is None or month is not None:
                return None
            month = hit
    return (month, day) if month and day else None


def _compact_dates_differ(new_text: str, old_text: str) -> bool:
    a, b = _COMPACT_RE.match(new_text or ""), _COMPACT_RE.match(old_text or "")
    if not (a and b) or _fold(a.group("name")).lower() != _fold(b.group("name")).lower():
        return False
    da, db = _bare_date(a.group("value")), _bare_date(b.group("value"))
    return bool(da and db and da != db)


def conflict_kind(new_text: str, old_text: str) -> Optional[str]:
    """If ``new_text`` contradicts ``old_text`` (same subject, same attribute, a DIFFERENT
    value — or a stated END of it), the closed-vocabulary kind of the attribute, else None.
    Built on the shared reconciler's attribute matcher (``memory_quality``) and the
    implicit-supersede subject / home / change-cue matchers (``memory_supersede``) so
    'same attribute' means one thing across the store."""
    try:
        from memory_quality import _attribute_key, _attrs_match, _same_value
        from memory_supersede import exclusive_conflict, is_tombstone, same_subject, same_topic
    except Exception:  # noqa: BLE001 — a matcher outage must not break a write
        return None
    if not new_text or not old_text or not same_subject(new_text, old_text):
        return None
    if _compact_dates_differ(new_text, old_text):
        return "birthday"
    if exclusive_conflict(new_text, old_text):
        return "home"
    ka, kb = _attribute_key(new_text), _attribute_key(old_text)
    if ka and kb and _attrs_match(ka, kb) and not _same_value(new_text, old_text, ka):
        return kind_of(old_text)
    for rx in (_WORK_RE, _AGE_RE):
        vn, vo = _slot_value(rx, new_text), _slot_value(rx, old_text)
        if vn and vo and vn != vo:
            return kind_of(old_text)
    if is_tombstone(new_text) and same_topic(new_text, old_text):
        return kind_of(old_text)
    # "User prefers tea." vs "User prefers coffee." - same verb, a different single object
    (vn, on), (vo, oo) = _verb_object(new_text), _verb_object(old_text)
    if vn and vn == vo and on and oo and not (on <= oo or oo <= on):
        return kind_of(old_text)
    # mutually exclusive states ("User is vegetarian." / "User is vegan.")
    wn, wo = set(re.findall(r"[a-z]+", new_text.lower())), set(re.findall(r"[a-z]+", old_text.lower()))
    for group in _EXCL_GROUPS:
        gn, go = wn & group, wo & group
        if gn and go and gn != go:
            return kind_of(old_text)
    return None


def find_conflict(new_text: str, rows: list[Any], writer_power: int, *,
                  exclude_id: str = "") -> Optional[tuple[Any, str]]:
    """The first APPROVED row in ``rows`` that ``new_text`` contradicts and that OUTRANKS a
    writer of ``writer_power`` (a direct user class never needs this: it may override), with
    the attribute kind, else None. ``rows`` are MemoryRef-likes (``id``, ``text``, ``metadata``)."""
    if writer_power >= RANK[OPERATOR]:
        return None
    # EVERY row is examined - never a recency prefix (Codex P2 on #1868: with >2,000 approved
    # memories an older user-stated fact fell outside the newest-first slice and became
    # invisible to the wall). The scan is cheap per row (status / class gates, then the
    # subject comparison) and the service runs it on the executor, not the event loop.
    for r in rows:
        meta = getattr(r, "metadata", None) or {}
        if getattr(r, "id", "") == exclude_id or str(meta.get("status") or "") != "approved":
            continue
        text = getattr(r, "text", "") or ""
        if may_override(writer_power, row_class(meta, text)):
            continue
        kind = conflict_kind(new_text, text)
        if kind:
            return r, kind
    return None


#: How the recall packet labels a row a voice the speaker gate did NOT confirm said (it is
#: stored, never the owner's own statement): the brain must not say "you told me".
UNVERIFIED_RECALL_LABEL = "(someone at the panel said this; speaker not confirmed)"


def is_unverified(meta: Optional[Mapping[str, Any]]) -> bool:
    """Was this row stamped ``user_unverified`` at write time? Reads the STAMP only (a row written
    before provenance existed derives a class on read and is never unverified)."""
    return str((meta or {}).get("authority_class") or "") == USER_UNVERIFIED


def is_candidate(ref: Any) -> bool:
    """Is this MemoryRef a held-back authority CANDIDATE (a disputed write)?"""
    md = getattr(ref, "metadata", None) or {}
    return bool(md.get("authority_blocked")) and str(md.get("status") or "") in ("disputed", "pending")


def log_blocked(writer: str, kind: str, *, user_id: str = "", action: str = "") -> None:
    """The ONE log line for a refused write: labels only - never the fact text. In ``shadow``
    mode the line is ``AUTHORITY_WOULD_BLOCK`` and nothing else changes."""
    tag = "AUTHORITY_BLOCKED" if enabled() else "AUTHORITY_WOULD_BLOCK"
    logger.info("%s writer=%s kind=%s%s%s", tag, writer, kind or "other",
                f" action={action}" if action else "", f" user={user_id}" if user_id else "")


# ── the observation gate: a model's NIGHTLY reading of the user is stored only when the user's words carry it ──────
#
# Why (ZMB reflection axis, K1 / K5, measured on main 2026-10-07): the nightly digest asked a model for "facts" from the
# day's transcript and stored every one as an APPROVED row of class ``model_from_transcript``. Three kinds of it were
# false and served: a link nobody stated ("Dagny is Jarvis's husband"; 5 of 10 observations in the lab), a hedged
# restatement of what the owner had said plainly ("Jarvis probably lives in Pellham": the owner's certainty turned into
# a guess), and a "you told me ..." the owner never said (an inference presented as the owner's own words). One class:
# the row's CLAIM was never checked against the owner's words. The gate, in the nightly digest and as pure helpers here
# (``supports`` / ``costated_span`` / ``cited_support``):
#
#   supported     the owner's words (a verbatim span via ``supports``, a single sentence that names every entity the
#                 claim names, or an approved user-class row the item CITES via ``source_memory_ids``) carry it: stored
#                 as before, and a co-stated paraphrase is stamped ``user_stated_derived`` (the honest class: a model's
#                 paraphrase that the owner's turn supports)
#   restatement   supported AND hedged: the owner said it plainly, the model made it a guess: dropped, the owner's row stands
#   unsupported   anything else: a ``pending`` candidate (never served), reject-ledger reason ``guard_observation_unsupported``;
#                 "you told me ..." from a model is removed (a supported claim is stored plain; an unsupported one is
#                 reworded as an inference, "Possibly ...")
#
# ``ZOE_DIGEST_OBSERVATION_GATE`` = enforce (default) | shadow (log what WOULD be held, change nothing) | off.

OBSERVATION_GATE_ENV = "ZOE_DIGEST_OBSERVATION_GATE"
#: a nightly writer's stored basis when a sentence of the owner's names everything the claim names
COSTATED_BASIS = "co_stated_user_turn"
CITED_BASIS = "cited_user_rows"


def observation_gate_mode() -> str:
    """``enforce`` (default) | ``shadow`` | ``off``. Per-call env read."""
    raw = os.environ.get(OBSERVATION_GATE_ENV)
    if raw is None:
        return "enforce"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    if v == "shadow":
        return "shadow"
    return "enforce"


#: "You told me ...", "You said ...", "As you mentioned, ...": the model claims the OWNER said it
_ATTRIBUTION_RE = re.compile(
    r"^\s*(?:as\s+)?you(?:['’]ve|\s+have|\s+did|\s+had)?\s+(?:told|said|mentioned|shared|let me know|noted|explained)\b"
    r"\s*(?:me|us)?\s*(?:that\b|,|:)?\s*", re.IGNORECASE)
#: a model's guess about a thing the owner may have said plainly
_GUESS_RE = re.compile(
    r"\b(?:probably|possibly|perhaps|maybe|likely|seems?(?:\s+to)?|appears?(?:\s+to)?|apparently|presumably|supposedly|"
    r"might|may\s+be|could\s+be|i\s+(?:think|guess|suspect)|looks\s+like|sounds\s+like)\b[,]?\s*", re.IGNORECASE)
_ENTITY_STOP = frozenset({"user", "users", "you", "your"})


def split_attribution(text: str) -> tuple[bool, str]:
    """``(attributed, claim)``: does the text open by saying the OWNER said it, and the claim without that opening."""
    t = (text or "").strip()
    m = _ATTRIBUTION_RE.match(t)
    if not m:
        return False, t
    rest = t[m.end():].strip()
    return True, (rest[:1].upper() + rest[1:]) if rest else rest


def is_hedged(text: str) -> bool:
    return bool(_GUESS_RE.search(text or "")) or bool(_HEDGE_RE.search(text or ""))


def strip_hedge(text: str) -> str:
    """The claim with its guess words removed ("Jarvis probably lives in Pellham" -> "Jarvis lives in Pellham"):
    what the support test reads, so a guess about a plainly stated fact is recognised as that fact."""
    out = _GUESS_RE.sub("", text or "")
    out = re.sub(r"\bto be\b\s+(?=\w+ing\b)", "", out)          # "seems to be starting at X" -> "starting at X"
    return re.sub(r"\s{2,}", " ", out).strip()


def claim_entities(text: str) -> list[str]:
    """The named people / places / organisations a statement names (capitalised runs; the owner and the
    pronouns are not entities). Pure."""
    try:
        from memory_gate import person_candidate_names
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for name in person_candidate_names(text or ""):
        parts = [p for p in name.split() if p.casefold() not in _ENTITY_STOP]
        if parts and " ".join(parts) not in out:
            out.append(" ".join(parts))
    return out


def _names_in(sentence: str, name: str) -> bool:
    low = " " + re.sub(r"[^a-z0-9' ]+", " ", _fold(sentence).lower()) + " "
    low = re.sub(r"['’]s\b", "", low)
    n = re.sub(r"[^a-z0-9' ]+", " ", _fold(name).lower()).strip()
    return bool(n) and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low) is not None


def _relation_words(text: str) -> set[str]:
    return {m.group(0).lower() for m in re.finditer(rf"\b{_RELATION}\b", text or "", re.IGNORECASE)}


def _polarity_tense_agree(win: str, claim: str) -> bool:
    if bool(_NEG_RE.search(win)) != bool(_NEG_RE.search(claim or "")):
        return False
    for cue in (_USED_TO_RE, _ENDED_RE):
        if bool(cue.search(win)) != bool(cue.search(claim or "")):
            return False
    return True


def costated_span(claim: str, user_text: str) -> Optional[str]:
    """The ONE sentence of the owner's own words that names EVERY entity the claim names (two or more), or None.
    A claim that links two named things is as true as the owner's having put them in one sentence: that is what a
    link is. It is NOT enough alone for a role ("X is Y's husband": the sentence must carry the relation word too),
    nor across a polarity / tense change ("quit" against "starts"). One entity or none: never (``supports`` decides)."""
    ents = claim_entities(claim)
    if len(ents) < 2 or not user_text:
        return None
    want_rel = _relation_words(claim)
    for line in _normalise_dates(str(user_text)).split("\n"):
        for sent in _sentences(line):
            if "?" in sent or _QUESTION_START_RE.match(sent) or _HYPOTHETICAL_RE.search(sent):
                continue
            if not all(_names_in(sent, e) for e in ents):
                continue
            if want_rel and not want_rel <= _relation_words(sent):
                continue
            if not _polarity_tense_agree(sent, claim):
                continue
            return _squash(sent)
    return None


def observation_support(claim: str, user_text: str) -> Optional[tuple[str, str]]:
    """``(basis, span)`` when the owner's words carry the claim, else None: ``supports`` first (entailment of the
    attribute, value and speaker), then a single sentence that names everything the claim names (``costated_span``)."""
    if not claim or not user_text:
        return None
    if supports(claim, user_text):
        return "anchored_user_turn", (supporting_span(claim, user_text) or "")
    span = costated_span(claim, user_text)
    return (COSTATED_BASIS, span) if span else None


def cited_support(claim: str, cited_texts: "list[str]") -> Optional[tuple[str, str]]:
    """Support from the approved user-class rows an observation CITES (``source_memory_ids``): the same two tests
    over those rows' texts (each row is one statement; they are joined one per line, never across rows)."""
    texts = [str(t).strip() for t in cited_texts or () if str(t or "").strip()]
    if not texts:
        return None
    got = observation_support(claim, "\n".join(texts))
    return (CITED_BASIS, got[1]) if got else None


@dataclass(frozen=True)
class ObservationVerdict:
    kind: str                     # supported | restatement | unsupported
    text: str                     # what to store (the attribution removed / an inference reworded)
    basis: str = ""               # anchored_user_turn | co_stated_user_turn | cited_user_rows
    reasons: tuple = ()           # labels only: attributed, hedged, no_support, quote_not_verbatim
    span: str = ""                # the owner's words that carry it (never logged)
    anchor: str = ""              # the user-class text to pass as ``anchor_text`` when it is stored


_FUNCTION_LEADS = frozenset("the a an he she they it his her their its my our this that these those there".split())


def _decapitalise(claim: str) -> str:
    """Lower-case the first letter of a claim that opens with a function word ("The knee ..." -> "possibly the knee ...");
    a name keeps its capital ("Possibly Brynja moved ...")."""
    first = (claim.split(None, 1) or [""])[0]
    return claim[:1].lower() + claim[1:] if first.casefold() in _FUNCTION_LEADS else claim


def check_observation(fact: str, user_text: Optional[str], *, cited_texts: "list[str]" = ()) -> ObservationVerdict:
    """Judge ONE model-written statement against the owner's words. ``user_text`` is the owner's turns only (the
    nightly transcript, or the verbatim quote the model gave), ``None`` when the model's quote was NOT verbatim (a
    hallucinated span is no evidence); ``cited_texts`` are the texts of the approved user-class rows it cites."""
    attributed, claim = split_attribution(fact)
    hedged = is_hedged(claim)
    core = strip_hedge(claim) if hedged else claim
    reasons: list[str] = (["attributed"] if attributed else []) + (["hedged"] if hedged else [])
    got = observation_support(core, user_text) if user_text else None
    if got is None:
        got = cited_support(core, list(cited_texts))
    if got is None:
        reasons.append("quote_not_verbatim" if user_text is None else "no_support")
        # an inference stays an inference: never "you told me"
        text = claim if (hedged or not attributed) else "Possibly " + _decapitalise(claim)
        return ObservationVerdict("unsupported", text, reasons=tuple(reasons))
    basis, span = got
    anchor = ("\n".join(str(t).strip() for t in cited_texts if str(t or "").strip())
              if basis == CITED_BASIS else str(user_text or ""))
    if hedged:
        return ObservationVerdict("restatement", core, basis, tuple(reasons), span, anchor)
    return ObservationVerdict("supported", claim, basis, tuple(reasons), span, anchor)
