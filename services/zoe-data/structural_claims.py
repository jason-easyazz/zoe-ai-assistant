"""Structural, language-independent memory floors: the CLAIM ROW (ZOE_STRUCTURAL_CLAIMS).

The leak (docs/research/structural-floors-2026-10-09.md): ``memory_authority`` decided "does the owner's own sentence
entail this stored fact?" by English stemming and word lists, so every review round found one more English phrasing
and no other language was ever served (the lexical stack scores 0.98 on the phrasings it was tuned on, 0.59 on new
English, 0.50 - never fires - on es/fr/de/zh/ja). The cure is to separate READING from AUTHORITY:

* READING a sentence in any language is the extractor's job (a 4B that is multilingual by training). It now emits,
  beside every fact, a CLAIM ROW: ``subj`` ``pred`` ``obj`` ``pol`` (affirm | negate | ended) ``mod`` (asserted | hedged |
  hypothetical | question | reported) ``tense`` (current | past | future) and ``quote`` - the owner's own words. Polarity,
  modality and tense are decided ONCE, here, and stored; nothing re-derives them from prose at a later comparison.
* AUTHORITY is decided on structure that does not care about the language: the quote is a substring of the owner's
  turn (NFKC + casefold + whitespace/accent fold: the own-words wall), the value is in the quote and in the fact (edit
  distance, not stemming), the modality is asserted, the subject is the speaker or one of their relatives, the speaker
  was not rejected, no negation in the quote is left unaccounted for.
* The per-language word lists (``lexicons.py``) are PRE-FILTERS: they may VETO a promotion ("the quote carries a
  hedge the claim row calls asserted") and flag it for the off-path verifier. They never authorise one.

Modes (``ZOE_STRUCTURAL_CLAIMS``, per-call env read): ``off`` (nothing asked of the extractor, nothing logged),
``shadow`` (DEFAULT: the extractor is asked for the claim row, the structural decision is computed and logged beside the
lexical one, the lexical decision stays authoritative), ``enforce`` (the structural decision is authoritative wherever a
valid claim row exists; a fact without one keeps the lexical decision).

This module is pure (stdlib + ``lexicons``): no store, no network, never raises into a caller.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import lexicons as lex

logger = logging.getLogger(__name__)

ENV = "ZOE_STRUCTURAL_CLAIMS"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"


def mode() -> str:
    """``off`` | ``shadow`` (default) | ``enforce``. Only the word ``enforce`` enforces: a typo never acts."""
    raw = os.environ.get("ZOE_STRUCTURAL_CLAIMS")
    if raw is None:
        return SHADOW
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", "disabled"):
        return OFF
    if v == "enforce":
        return ENFORCE
    return SHADOW


def enforcing() -> bool:
    return mode() == ENFORCE


def active() -> bool:
    """Is the claim row asked of the extractor and the decision computed (``shadow`` or ``enforce``)?"""
    return mode() != OFF


# -- the closed vocabularies ------------------------------------------------------------------------------

POLARITIES = ("affirm", "negate", "ended")
MODALITIES = ("asserted", "hedged", "hypothetical", "question", "reported")
TENSES = ("current", "past", "future")
PREDICATES = (
    "residence", "employer", "occupation", "birthday", "age", "name", "pet_name", "allergy", "health",
    "activity", "membership", "plan", "preference", "kin", "other",
)
#: one-valued attributes: a new value on the same subject REPLACES the old one (``memory_supersede.SLOT_ATTRIBUTES``;
#: a second job is not a replaced one, so occupation / employer retire only by an explicit retraction)
SLOT_PREDICATES = frozenset({"residence", "birthday", "age"})
_POL_ALIASES = {"affirmative": "affirm", "positive": "affirm", "true": "affirm", "negative": "negate", "negated": "negate",
                "denied": "negate", "end": "ended", "stopped": "ended", "past": "ended"}
_MOD_ALIASES = {"assert": "asserted", "fact": "asserted", "plain": "asserted", "hedge": "hedged", "uncertain": "hedged",
                "hypothetical": "hypothetical", "wish": "hypothetical", "conditional": "hypothetical",
                "reported_speech": "reported", "report": "reported", "hearsay": "reported", "ask": "question",
                "interrogative": "question"}
_TENSE_ALIASES = {"present": "current", "now": "current", "previous": "past", "former": "past", "later": "future",
                  "planned": "future", "upcoming": "future"}

# -- normalisation ----------------------------------------------------------------------------------------

_INVISIBLE = re.compile(r"[​‌‍⁠﻿]")
_EDGE_PUNCT = " \t\r\n.,;:!?。！？、，¿¡\"'“”‘’-–—"


def norm(text: str) -> str:
    """NFKC + casefold + invisible chars dropped + accents folded + typographic quotes unified + whitespace folded.
    The comparison form for every structural check; never stored, never shown."""
    s = unicodedata.normalize("NFKC", text or "")
    s = _INVISIBLE.sub("", s).replace("’", "'").replace("‘", "'")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", s.casefold())      # "4th" is "4"
    return re.sub(r"\s+", " ", s).strip()


def _edge_strip(text: str) -> str:
    return (text or "").strip(_EDGE_PUNCT)


def _is_cjk_char(ch: str) -> bool:
    return "぀" <= ch <= "ヿ" or "㐀" <= ch <= "鿿" or "ｦ" <= ch <= "ﾟ"


def quote_basis(quote: str, owner_turn: str) -> str:
    """How the quote sits in the owner's turn: ``raw`` (a substring of the turn as typed / transcribed), ``normalised`` (a
    substring only after the household numeric-date normaliser rewrote it - the model reads the normalised turn), or
    ``""`` (not the owner's words: the claim is dropped). A quote shorter than 3 characters (2 for CJK) is no quote."""
    q = norm(_edge_strip(quote))
    if len(q) < (2 if any(_is_cjk_char(c) for c in q) else 3):
        return ""
    t = norm(owner_turn)
    if q in t:
        return "raw"
    try:
        from date_locale import normalize_numeric_dates

        if q in norm(normalize_numeric_dates(owner_turn or "")):
            return "normalised"
    except Exception:  # noqa: BLE001 - no date normaliser = the raw check only
        pass
    return ""


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, early-out above ``cap``."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _tolerance(n: int) -> int:
    """The forgetting ledger's rule (open-problems 2026-10-06): none for 1-4 letters, 1 for 5-6, 2 for 7+."""
    return 0 if n <= 4 else 1 if n <= 6 else 2


def _tokens(s: str) -> list[str]:
    return re.findall(r"[^\W_]+", s)


def _token_match(a: str, b: str) -> bool:
    """Two tokens are the same word: equal, within the edit-distance tolerance, or one inflection of the other (a long
    shared stem: "smoking" / "smokes"). Any token holding a digit must be equal - a number is never fuzzy."""
    if a == b:
        return True
    if any(c.isdigit() for c in a + b):
        return False
    tol = _tolerance(len(a))                      # ``a`` is the value's token: the tolerance is its length's
    if tol and edit_distance(a, b, tol) <= tol:
        return True
    n = min(len(a), len(b))
    if n >= 6:
        pre = 0
        for x, y in zip(a, b):
            if x != y:
                break
            pre += 1
        return pre >= 4 and pre >= 0.6 * n
    return False


def value_in(value: str, text: str) -> bool:
    """Is ``value`` in ``text`` - by substring, or token by token within the tolerance? Language-neutral (a CJK value
    is a substring test; a spelled-out STT variant ("Marisal" for "Marisol") is within tolerance)."""
    v, t = norm(_edge_strip(value)), norm(text)
    if not v or not t:
        return False
    if v in t:
        return True
    if any(_is_cjk_char(c) for c in v):
        return False
    t_toks = _tokens(t)
    v_toks = _tokens(v)
    if not v_toks:
        return False
    for vt in v_toks:
        if len(vt) <= 2 and vt.isalpha():
            continue                  # "of" / "de" / "in": a function word carries no value
        if vt in t_toks or (len(vt) >= 4 and vt in t):
            continue
        if not any(_token_match(vt, tt) for tt in t_toks):
            return False
    return True


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+", norm(text)))


# -- the claim row ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    subj: str       # "user" | "rel:<kin code>" | "person:<Name>" | "other"
    pred: str       # one of PREDICATES, or "kin:<code>"
    obj: str
    pol: str        # affirm | negate | ended
    mod: str        # asserted | hedged | hypothetical | question | reported
    tense: str      # current | past | future
    quote: str
    lang: str = ""  # BCP-47 primary subtag, "" = unknown (detected from the quote)

    def to_dict(self) -> dict[str, str]:
        return {"subj": self.subj, "pred": self.pred, "obj": self.obj, "pol": self.pol, "mod": self.mod,
                "tense": self.tense, "quote": self.quote, "lang": self.lang}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def language(self) -> str:
        return self.lang or lex.detect(self.quote)


def _enum(value: Any, allowed: Sequence[str], aliases: Mapping[str, str]) -> str:
    v = str(value or "").strip().lower().replace("-", "_")
    v = aliases.get(v, v)
    return v if v in allowed else ""


def normalise_subject(raw: Any) -> str:
    s = str(raw or "").strip()
    low = s.lower()
    if low in ("user", "the user", "speaker", "self", "me", "i", "owner", "usuario", "utilisateur", "nutzer", "用户", "ユーザー"):
        return "user"
    if low.startswith("rel:") or low.startswith("relative:") or low.startswith("kin:"):
        code = lex.kin_code(low.split(":", 1)[1].strip()) or low.split(":", 1)[1].strip().replace(" ", "_")
        return "rel:" + code if code else "other"
    if low.startswith("person:") or low.startswith("name:"):
        name = s.split(":", 1)[1].strip()
        return "person:" + name if name else "other"
    code = lex.kin_code(low)
    if code:
        return "rel:" + code
    return "person:" + s if s and s[:1].isupper() else "other"


def normalise_pred(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace(" ", "_")
    if s.startswith("kin:"):
        code = lex.kin_code(s.split(":", 1)[1]) or s.split(":", 1)[1]
        return "kin:" + code
    return s if s in PREDICATES else "other"


def parse_claim(raw: Any) -> tuple[Optional[Claim], str]:
    """``(Claim, "")`` for a well-formed claim object, else ``(None, reason)``. Tolerant of case, a few aliases and
    extra keys; strict about the closed vocabularies and about a quote being present. Never raises."""
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, Mapping):
            return None, "not_an_object"
        pol = _enum(raw.get("pol", raw.get("polarity")), POLARITIES, _POL_ALIASES)
        mod = _enum(raw.get("mod", raw.get("modality")), MODALITIES, _MOD_ALIASES)
        tense = _enum(raw.get("tense"), TENSES, _TENSE_ALIASES)
        quote = _edge_strip(str(raw.get("quote") or ""))
        if not pol:
            return None, "bad_polarity"
        if not mod:
            return None, "bad_modality"
        if not tense:
            return None, "bad_tense"
        if not quote:
            return None, "no_quote"
        lang = str(raw.get("lang") or "").strip().lower().split("-")[0]
        return Claim(normalise_subject(raw.get("subj", raw.get("subject"))), normalise_pred(raw.get("pred", raw.get("predicate"))),
                     str(raw.get("obj", raw.get("object")) or "").strip(), pol, mod, tense, quote,
                     lang if re.fullmatch(r"[a-z]{2,3}", lang) else ""), ""
    except Exception:  # noqa: BLE001 - a malformed row is "no claim", never an error
        return None, "unparseable"


def claim_from_metadata(meta: Optional[Mapping[str, Any]]) -> Optional[Claim]:
    """The claim row stored on a memory row (``metadata["claim"]``, JSON), or None."""
    if not meta:
        return None
    raw = meta.get("claim")
    if not raw:
        return None
    c, _ = parse_claim(raw)
    return c


# -- the polarity of a stretch of text, from the lexicon (a PRE-FILTER: it vetoes, it never authorises) -------

HOLD, NOT_HOLD = "hold", "not_hold"


@dataclass(frozen=True)
class TextPolarity:
    cls: Optional[str]                 # HOLD | NOT_HOLD | None (no lexicon = unknown)
    flags: tuple[str, ...] = ()        # ambiguity labels: unaccounted_negation, hedge_negation
    positive: bool = False             # evidence, not absence: a marker was found (HOLD is positive only for a DENIED end)


def _bound(text: str, a: int, b: int, lang: str) -> bool:
    """Do text positions ``a``..``b`` sit in one clause (no break character, no clause word between)?"""
    lo, hi = (a, b) if a <= b else (b, a)
    seg = text[lo:hi]
    if any(ch in seg for ch in lex._BREAKS):
        return False
    cw = lex.words(lang, "clause_words")
    if cw and not lex.is_cjk(lang):
        return not re.search(rf"(?<!\w)(?:{lex.alt(cw)})(?!\w)", seg, re.IGNORECASE)
    return True


def text_polarity(text: str, lang: str, obj: str = "", siblings: Iterable[Claim] = ()) -> TextPolarity:
    """Does the text state its content as HOLDING, or as not holding (negated / ended)? Uses the language's negation and
    end-state markers: ``I stopped X`` / ``no longer X`` / ``I do not X`` -> NOT_HOLD; ``I did not stop X`` -> HOLD (the
    end was denied); ``X, not Y`` -> HOLD for X when ``Y`` is accounted for by a sibling negate claim, and flagged
    ``unaccounted_negation`` when nothing accounts for it. No lexicon for ``lang`` -> unknown (cls None)."""
    if not lex.load(lang) or not text:
        return TextPolarity(None)
    s = unicodedata.normalize("NFKC", text)
    negs = lex.negation_spans(s, lang)
    ends = lex.ended_spans(s, lang)
    direction = lex.load(lang).get("negation_scope") or "after"
    covered = [(a, b) for a, b, k in ends if k in ("phrase", "aux_neg")]
    free_negs = [n for n in negs if not any(a <= n[0] and n[1] <= b for a, b in covered)]
    flags: list[str] = []
    if ends:
        denied = any(k == "aux_neg" for _, _, k in ends)
        for a, b, k in ends:
            if k != "verb":
                continue
            for n in free_negs:
                if direction in ("after", "both") and n[1] <= a and _bound(s, n[1], a, lang):
                    denied = True
                if direction in ("before", "both") and n[0] >= b and _bound(s, b, n[0], lang):
                    denied = True
        return TextPolarity(HOLD if denied else NOT_HOLD, (), True)
    sib_objs = [c.obj for c in siblings if c.pol in ("negate", "ended") and c.obj]
    exempt = {w.casefold() for w in lex.words(lang, "contrast_exempt")}
    temporal = {w.casefold() for w in lex.words(lang, "temporal")}
    anaphora = {w.casefold() for w in lex.words(lang, "anaphora")}
    not_hold = False
    for n in free_negs:
        scope = lex.negation_scope(s, n, lang)
        sl = scope.casefold()
        toks = _tokens(sl)
        if obj and value_in(obj, scope):
            not_hold = True
        elif toks and toks[0] in exempt:
            not_hold = True                  # "not sure about it": the negation is of the claim itself, as a hedge
            flags.append("hedge_negation")
        elif _has_word(sl, toks, anaphora, lang) or _has_word(sl, toks, temporal, lang):
            not_hold = True
        elif any(value_in(so, scope) for so in sib_objs):
            continue           # a contrast the extractor accounted for with a sibling claim
        elif not _tokens(sl):
            continue           # a bare marker with an empty scope: nothing to attach it to
        else:
            flags.append("unaccounted_negation")
    return TextPolarity(NOT_HOLD if not_hold else HOLD, tuple(dict.fromkeys(flags)), not_hold)


def _has_word(scope_cf: str, toks: list[str], words: set[str], lang: str) -> bool:
    if not words:
        return False
    if lex.is_cjk(lang):
        return any(w in scope_cf for w in words)
    return any(t in words for t in toks) or any((" " in w) and w in scope_cf for w in words)


# -- the structural decision ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """The structural verdict on ONE (fact, claim row, owner turn). ``anchored``: the owner's own words support the fact (the
    ``supports`` tier). ``promoted``: they ENTAIL it plainly, so it carries ``user_stated`` power (``entailing_span``)."""

    anchored: bool
    promoted: bool
    reasons: tuple[str, ...] = ()      # why anchoring failed, else why promotion failed ("" = it did not)
    ambiguous: tuple[str, ...] = ()    # lexicon pre-filter flags: a case for the off-path verifier
    basis: str = ""                    # quote_basis: raw | normalised | ""
    lang: str = ""

    @property
    def label(self) -> str:
        return "promote" if self.promoted else "anchor" if self.anchored else "hold"


NO_CLAIM = Decision(False, False, ("no_claim",))


def pick_claim_for_fact(fact: str, claims: Sequence[Claim]) -> tuple[Optional[Claim], tuple[Claim, ...]]:
    """When one owner sentence yielded several claims (``Bendigo, not Ballarat``), the claim a fact stands on is the one
    whose value the fact carries; the rest are its siblings. Production attaches each claim to its own fact, this is
    the same pairing for a fixture or a test."""
    best, best_n = None, -1
    for c in claims:
        n = (2 if c.obj and value_in(c.obj, fact) else 0) + (1 if c.pol == "affirm" else 0)
        if (not c.obj or value_in(c.obj, fact)) and n > best_n:
            best, best_n = c, n
    if best is None:
        return None, tuple(claims)
    return best, tuple(c for c in claims if c is not best)


def _claim_class(c: Claim) -> str:
    return HOLD if c.pol == "affirm" else NOT_HOLD


def decide(fact: str, claim: Optional[Claim], owner_turn: str, *, siblings: Iterable[Claim] = (),
           speaker_verified: Optional[bool] = None) -> Decision:
    """The structural decision. Language-independent checks first (they authorise); lexicon checks last (they only veto).
    Pure, never raises - an internal error is "hold"."""
    try:
        return _decide(fact, claim, owner_turn, tuple(siblings), speaker_verified)
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.debug("structural decide failed: %s", type(exc).__name__)
        return Decision(False, False, ("error",))


def _decide(fact: str, claim: Optional[Claim], owner_turn: str, siblings: tuple[Claim, ...],
            speaker_verified: Optional[bool]) -> Decision:
    if claim is None:
        return NO_CLAIM
    ctx = _Ctx(fact, claim, owner_turn, siblings, speaker_verified)
    block: list[str] = []         # the owner's words do not support the fact at all
    hold: list[str] = []          # supported, but not plainly: no promotion
    amb: list[str] = []
    for check in _BLOCK_CHECKS:
        check(ctx, block, amb)
    anchored = not block
    for check in _HOLD_CHECKS:
        check(ctx, hold, amb)
    promoted = anchored and not hold
    reasons = tuple(block) if not anchored else tuple(hold)
    return Decision(anchored, promoted, reasons, tuple(dict.fromkeys(amb)), ctx.basis, ctx.qlang)


@dataclass
class _Ctx:
    """The inputs of one structural decision, with the language facts every check needs computed once."""

    fact: str
    claim: Claim
    owner_turn: str
    siblings: tuple
    speaker_verified: Optional[bool]
    qlang: str = ""
    flang: str = ""
    basis: str = ""
    q_pol: TextPolarity = field(default_factory=lambda: TextPolarity(None))

    def __post_init__(self) -> None:
        self.qlang = self.claim.language
        self.flang, ev = lex.detect_scored(self.fact)
        if ev == 0:
            self.flang = self.qlang       # a fact too short to tell: assume the language of the owner's words
        self.basis = quote_basis(self.claim.quote, self.owner_turn)
        self.q_pol = text_polarity(self.claim.quote, self.qlang, self.claim.obj, self.siblings)


# -- the checks. Each appends to ``out`` (block: the owner's words do not support the fact; hold: not plain enough to
# promote) and ``amb`` (a case for the off-path verifier). The language-independent ones AUTHORISE (they gate anchoring);
# the lexicon ones only VETO. Separate functions so the red-when-removed controls can take exactly one out.

def _check_quote(c: _Ctx, out: list, amb: list) -> None:
    if not c.basis:
        out.append("quote_not_in_turn")                       # the own-words wall: the quote is a substring of the turn


def _check_value(c: _Ctx, out: list, amb: list) -> None:
    claim = c.claim
    if not claim.obj:
        out.append("no_value")
        return
    if not value_in(claim.obj, claim.quote):
        out.append("value_not_in_quote")
    if not value_in(claim.obj, c.fact):
        out.append("value_not_in_fact")
    if not numbers_in(c.fact) <= numbers_in(claim.quote):
        out.append("fact_number_not_in_quote")                # "15 March 1980" is not supported by "my birthday is in March"


def _check_modality(c: _Ctx, out: list, amb: list) -> None:
    if c.claim.mod not in ("asserted", "hedged"):
        out.append("modality:" + c.claim.mod)


def _check_subject(c: _Ctx, out: list, amb: list) -> None:
    claim = c.claim
    if _subject_consistent(claim, c.fact, c.flang) is False:
        out.append("subject_mismatch")
    if claim.subj == "user":
        if lex.any_kin_in(claim.quote, c.qlang):
            out.append("relative_in_quote")                   # "my sister moved to Hobart" is not the speaker's own fact
            amb.append("relative_in_quote")
        if lex.load(c.qlang).get("pronoun_required") and not _first_person(claim.quote, c.qlang):
            out.append("no_first_person")                     # "Dana moved to Hobart" read as the speaker's
            amb.append("no_first_person")


def _check_polarity(c: _Ctx, out: list, amb: list) -> None:
    claim = c.claim
    want = _claim_class(claim)
    fact_pol = text_polarity(c.fact, c.flang, claim.obj)
    if fact_pol.cls is None:
        if claim.pol != "affirm":
            out.append("polarity_uncorroborated")             # a denial / an end whose wording nobody can read: fail closed
            amb.append("polarity_uncorroborated")
    elif want == HOLD and fact_pol.cls == NOT_HOLD:
        out.append("fact_polarity_mismatch")
    elif want == NOT_HOLD and fact_pol.cls != NOT_HOLD:
        out.append("fact_polarity_mismatch")                  # the claim says "ended / negated"; the wording says nothing of the sort
    if c.q_pol.cls is not None and c.q_pol.positive and c.q_pol.cls != want:
        out.append("quote_polarity_mismatch")


def _check_tense_supported(c: _Ctx, out: list, amb: list) -> None:
    if c.claim.tense == "past" and c.claim.pol != "ended" and not _fact_marks_past(c.fact, c.flang):
        out.append("tense_mismatch")


def _hold_plain(c: _Ctx, out: list, amb: list) -> None:
    """Promotion needs the owner to have said it PLAINLY, in the first person, about themselves or their own relative."""
    claim = c.claim
    if claim.mod != "asserted":
        out.append("not_asserted")
    if not (claim.subj == "user" or claim.subj.startswith("rel:")):
        out.append("subject:" + claim.subj.split(":")[0])
    if c.basis != "raw":
        out.append("quote_not_raw")
    if c.speaker_verified is False:
        out.append("speaker_not_verified")


def _hold_tense(c: _Ctx, out: list, amb: list) -> None:
    claim = c.claim
    if claim.tense == "future" or (claim.tense == "past" and claim.pol != "ended"):
        out.append("tense:" + claim.tense)


def _hold_prefilters(c: _Ctx, out: list, amb: list) -> None:
    """The lexicon pre-filters: a hedge / wish / question / past marker in the quote the claim row calls plain, and any
    negation left unaccounted for. A veto and a flag for the verifier - never an authorisation."""
    mk = lex.markers(c.claim.quote, c.qlang)
    if mk["hedge"] or mk["hypothetical"] or mk["question"]:
        out.append("prefilter:modality")
        if c.claim.mod == "asserted":
            amb.append("modality_marker_vs_asserted")
    if mk["past"] and c.claim.tense == "current":
        out.append("prefilter:past")
        amb.append("past_marker_vs_current")
    for f in c.q_pol.flags:
        out.append("prefilter:" + f)
        amb.append(f)


_BLOCK_CHECKS = [_check_quote, _check_value, _check_modality, _check_subject, _check_polarity, _check_tense_supported]
_HOLD_CHECKS = [_hold_plain, _hold_tense, _hold_prefilters]


def _first_person(quote: str, lang: str) -> bool:
    fp = {w.casefold() for w in lex.words(lang, "first_person")}
    return any(t in fp for t in _tokens(norm(quote)))


def _fact_marks_past(fact: str, flang: str) -> bool:
    return bool(lex.markers(fact, flang)["past"]) if lex.load(flang) else True   # unknown wording: do not block


def _subject_consistent(claim: Claim, fact: str, flang: str) -> Optional[bool]:
    """Does the fact's wording agree with the claim's subject? None = the language has no data to say."""
    if claim.subj.startswith("rel:"):
        return lex.kin_in(fact, claim.subj.split(":", 1)[1], flang)
    if claim.subj == "user":
        kin = lex.any_kin_in(fact, flang)
        return None if kin is None else (not kin)
    # a named third party / unknown subject: a fact that speaks about THE USER is not about them
    about_user = lex.mentions_user(fact, flang)
    return None if about_user is None else (not about_user)


# -- retirement by key (the owner's retraction / correction pairs rows by id, not by words) ---------------------

def value_match(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (value_in(a, b) or value_in(b, a))


def retires(new: Claim, old: Claim) -> str:
    """The reason a NEW owner-asserted claim retires an OLDER stored claim by KEY, or "". Key = (subject, predicate,
    value): a negate / ended claim retires the older affirm of the same value (an empty value retires the slot); an
    affirm on a one-valued predicate retires an older affirm of a DIFFERENT value. Words are never compared."""
    if new.mod != "asserted" or new.tense == "future" or old.pol != "affirm":
        return ""
    if new.subj != old.subj or new.pred == "other" or new.pred != old.pred:
        return ""
    if new.pol in ("negate", "ended"):
        if not new.obj or value_match(new.obj, old.obj):
            return "retract:" + new.pred
        return ""
    if new.pol == "affirm" and new.pred in SLOT_PREDICATES and new.tense == "current":
        if new.obj and old.obj and not value_match(new.obj, old.obj):
            return "slot:" + new.pred
    return ""


# -- the shadow ledger ------------------------------------------------------------------------------------

STATS: Counter = Counter()


def record(floor: str, lane: str, lang: str, lexical: str, structural: str, *, reasons: Iterable[str] = (),
           ambiguous: Iterable[str] = (), applied: bool = False) -> None:
    """ONE INFO line per comparison, labels only (never the fact or the turn), and an in-process counter. ``agree`` is
    whether the two decisions are the same label."""
    try:
        STATS[(floor, lang or "-", lexical, structural)] += 1
        logger.info("STRUCTURAL_FLOOR floor=%s lane=%s mode=%s lang=%s lexical=%s structural=%s agree=%d applied=%d "
                    "reasons=%s ambiguous=%s", floor, lane, mode(), lang or "-", lexical, structural,
                    int(lexical == structural), int(applied), ",".join(reasons) or "-", ",".join(ambiguous) or "-")
    except Exception:  # noqa: BLE001 - a log line must never fail a write
        pass


def lexical_label(resolved_promoted: bool, resolved_anchored: bool) -> str:
    return "promote" if resolved_promoted else "anchor" if resolved_anchored else "hold"
