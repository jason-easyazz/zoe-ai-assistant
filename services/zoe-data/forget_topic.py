"""A TOPIC forget: "forget what I told you about my mum's heart".

The gap it closes (QA round 4, 2026-11-08). ``memory_forget_entity`` only fires for a NAME ("forget everything about
Dana"); "forget what I told you about my mum's heart" names no one, so no intent matched, the brain (which has no forget
tool) answered "done", the claim guard replaced that with "I didn't manage to save that", and the row stayed in the recall
packet. A bare "forget that" is bound to the previous turn (``turn_forget``) and cannot mean this either.

The contract (forgotten means forever; fails closed):

* The topic is the content words after "about" (possessives and stop words dropped, kin words matched with their
  synonyms: mum = mom = mother). A stored row is the topic's ONLY when it holds EVERY topic word - never a fuzzy "mostly".
* More than ``MAX_ROWS`` matching rows is NOT forgotten: the owner is asked to say which part. Nothing changes.
* Matching rows (approved, pending, disputed, superseded) are archived and then erased for real (``erase_rows``); a row
  whose archive did not land is never erased and is reported as left. The owner's recent turns that say the topic are
  retracted (their capture in flight never lands), their words leave the exact-words index, and the saved transcript turn
  is flagged off the record - the same cascade "forget that" runs for one turn.
* The reply says exactly what happened: how many things went, how many could not be reached, or that nothing matched and
  nothing was changed. It never says "saved" about a forget, and never "forgotten" while a row is still there. A topic said
  in a recent turn but not filed yet (the capture runs minutes behind) IS forgotten: its turn is retracted and the reply says so.

Pure matching (``parse`` / ``topic_groups`` / ``row_matches``) has no I/O; ``forget`` is the only part that touches the store.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: A topic forget touching more rows than this is refused (ask which part): a broad word must never sweep a lot.
MAX_ROWS = 12

_OPENER = (r"(?:(?:ok(?:ay)?|no+|nope|oh+|wait|please|actually|yes|yeah|so|and|um+|hey|zoe)\b[\s,.!?;:-]*)*"
           r"(?:(?:forget|scrap|delete)\s+that\b[\s,.!?;:-]*(?:and\s+)?)?(?:please\s+)?")
_VERB = (r"(?:forget|delete|remove|erase|scrap|"
         r"(?:do\s*n'?t|do\s+not|stop)\s+(?:keep|keeping|remember|remembering|save|saving|store|storing))")
_QUANT = (r"(?:everything|anything|all(?:\s+of\s+it)?"
          r"|what(?:ever)?\s+(?:i(?:'?ve)?|we)\s+(?:just\s+)?(?:told\s+you|said|mentioned)"
          # "forget that I told you about my sister" (QA round 8): with no topic intent it reached the brain, which has no forget
          # tool, and said "I don't have that noted" while the fact was still on its way to the store
          r"|that\s+(?:i(?:'?ve)?|we)\s+(?:just\s+)?(?:told\s+you|said|mentioned)"
          r"|what\s+you\s+know|your\s+memories|the\s+memories"
          # "forget the thing about asha and mushrooms", "forget the bit about my brother" (QA round 7): the same instruction in the
          # words people actually use; the noun is REQUIRED (bare "forget about my day" stays a dismissal)
          r"|(?:all\s+)?(?:the|that|this|those|these)\s+(?:(?:little|small|whole|other|sad|silly)\s+)?"
          r"(?:thing|things|bit|bits|part|parts|stuff|story|stories|detail|details|info|information|note|notes)"
          r"(?:\s+(?:i|we)\s+(?:just\s+)?(?:told\s+you|said|mentioned))?)")
# the quantifier is REQUIRED: "forget about my day" is a dismissal, not an instruction to erase anything
_TOPIC_RE = re.compile(
    r"^[\s,.!?;:-]*" + _OPENER + _VERB + r"\s+" + _QUANT + r"\s+(?:you\s+know\s+)?about\s+"
    r"(?P<topic>[a-z0-9][a-z0-9'\- ]{1,60}?)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# "forget that Edward dislikes fish. I find it was not correct": the thing to forget is the CLAUSE itself. The opener word "that" is
# required (so "forget it" / "forget Dana" stay elsewhere); a first-person clause ("forget that I said ...") is the previous-turn
# retraction's; and the clause must carry at least two content words so a stray "forget that it rained" cannot sweep rows.
_CLAUSE_RE = re.compile(
    r"^[\s,.!?;:-]*" + _OPENER + r"(?:forget|delete|erase|scrap)\s+(?:the\s+fact\s+)?that\s+"
    r"(?P<topic>(?!(?:i|we|it|that|this|what|you|my\s+(?:last|previous))\b)[a-z0-9][a-z0-9'\- ]{5,70}?)\s*(?:[.!?]+.{0,60})?$",
    re.IGNORECASE,
)
MIN_CLAUSE_GROUPS = 2

_STOP = frozenset(
    "my our your his her their the a an of and or to in on at with for about that this these those thing things stuff "
    "i me you we it its is are was were be been am what whatever everything anything all told said mentioned "
    "just really some any being been having".split())     # "about being lonely": the turn said "I've been lonely" (QA round 8)

#: kin words that mean the same person-role: a row saying "mother" is "my mum"
_SYNONYMS: tuple = (
    frozenset({"mum", "mom", "mother", "mam", "mummy", "mommy", "mama", "mamma"}),
    frozenset({"dad", "father", "daddy", "papa", "pop"}),
    frozenset({"kid", "child", "children", "kids"}),
    frozenset({"grandma", "grandmother", "nan", "nana", "gran", "granny", "nanna"}),
    frozenset({"grandpa", "grandfather", "grandad", "granddad", "pop"}),
    frozenset({"wife", "spouse"}),
    frozenset({"husband", "spouse"}),
    frozenset({"doctor", "gp", "doctors"}),
)


def _stem(tok: str) -> str:
    t = tok.lower().strip("'-")
    if t.endswith(("'s", "’s")):
        t = t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def _tokens(text: str) -> list:
    t = (text or "").replace("’", "'").replace("‘", "'").lower()
    return [_stem(w) for w in re.findall(r"[a-z0-9][a-z0-9'-]*", t)]


def parse(text: str) -> Optional[str]:
    """The topic phrase of a topic-forget utterance ("my mum's heart"), else None. Never raises."""
    try:
        t = (text or "").replace("’", "'").replace("‘", "'").strip()
        if not t or len(t.split()) > 24:
            return None
        m = _TOPIC_RE.match(t)
        if m:
            topic = m.group("topic").strip(" -'")
            return topic if topic_groups(topic) else None
        m = _CLAUSE_RE.match(t)
        if m:
            topic = m.group("topic").strip(" -'")
            return topic if len(topic_groups(topic)) >= MIN_CLAUSE_GROUPS else None
        return None
    except Exception:  # noqa: BLE001
        return None


def topic_groups(topic: str) -> list:
    """The topic as a list of word GROUPS (a word and its synonyms): a row matches only when every group is present.
    [] when the topic has no content word (so nothing can match)."""
    groups: list = []
    for w in _tokens(topic):
        if not w or w in _STOP or len(w) < 2:
            continue
        syn = next((s for s in _SYNONYMS if w in s), None)
        group = frozenset({_stem(x) for x in syn}) if syn else frozenset({w})
        if group not in groups:
            groups.append(group)
    return groups


def row_matches(groups: list, text: str) -> bool:
    """Does ``text`` hold every topic word (or a synonym)? An empty topic matches nothing."""
    if not groups:
        return False
    have = set(_tokens(text))
    return all(g & have for g in groups)


@dataclass
class Result:
    found: int = 0
    forgotten: int = 0
    failed: int = 0
    too_many: bool = False
    store_down: bool = False
    turns_retracted: int = 0
    preview: str = ""
    words_erased: int = 0
    notes: list = field(default_factory=list)


def _preview(text: str) -> str:
    p = " ".join((text or "").split())
    return p if len(p) <= 80 else p[:77].rstrip() + "…"


async def _owned_rows(svc: Any, user_id: str, groups: list, *, mids: Any = ()) -> list:
    """Owned topic matches (live + superseded), plus live rows bound to ``mids``. RAISES on a store
    failure: a forget must never be confirmed from a read that did not happen."""
    import turn_forget

    want = {(turn_forget.TURN, mid) for mid in mids}
    out = []
    for r in await turn_forget.live_rows(svc, user_id, extra_statuses=("superseded",)):
        md = getattr(r, "metadata", {}) or {}
        if (md.get("user_id") or md.get("wing")) != user_id:
            continue
        if ((groups and (row_matches(groups, getattr(r, "text", "") or "")
                        or row_matches(groups, str(md.get("retire_quote") or ""))))
                or (md.get("status") in turn_forget.LIVE_FORGET_STATUSES and turn_forget.classify(r) in want)):
            out.append(r)
    return out


async def _index_hits(user_id: str, groups: list) -> dict:
    """``{turn_id: text}``: the member's PERSISTED exact-words entries that hold the whole topic (the index outlives a restart
    and the in-process recent turns). RAISES when the index cannot be read."""
    import exact_words

    backend = exact_words.get_backend()
    hits: dict = {}
    for needle in sorted(min(groups, key=len)):     # the narrowest group: "heart", not the eight spellings of "mum"
        for tid, text in await backend.rows_matching(user_id, needle):
            if row_matches(groups, text or ""):
                hits[tid] = text
    return hits


async def _said_mids(user_id: str, turns: list, hits: dict, rows: Any = ()) -> list:
    """The saved-turn ids of the owner's words about the topic: each retracted recent turn's ids (a turn noted with none: the saved
    turns its words resolve to), each exact-words entry's (a legacy entry's words resolved the same way; a non-turn entry names
    none) AND each matched row's own saved turn (``source_turn``): after a restart, with no recent turn and no indexed entry, the
    row is the only thing that still names the turn (a legacy row resolves its excerpt through the turn-forget helper), and its
    transcript / observations / notes go by that id. RAISES on a store
    failure."""
    import exact_words
    import turn_forget

    out: list = []

    def add(mids) -> None:
        out.extend(m for m in mids if m not in out)
    for t in turns:
        add(t.mids or await exact_words.legacy_transcript_ids(user_id, t.text))
    for tid, text in hits.items():
        kind, mid = turn_forget.classify(tid)              # PER ROW: by its saved turn / its words / a non-turn source: none
        if kind == turn_forget.TURN:
            add([mid])
        elif kind == turn_forget.LEGACY:
            add(await exact_words.legacy_transcript_ids(user_id, text))
    for r in rows or ():
        kind, mid = turn_forget.classify(r)                # a bound row names its saved turn; only a legacy row resolves its excerpt
        if kind == turn_forget.TURN:
            add([mid])
        elif kind == turn_forget.LEGACY:
            add(await turn_forget.resolve_legacy_ids(user_id, ((getattr(r, "metadata", {}) or {}).get("source_excerpt"),)))
    return out


def fold_name_text(text: str) -> str:
    """ONE fold for both sides of a name comparison: JSON / unicode escapes undone, accents stripped, NFKC, casefold, and every
    apostrophe / hyphen variant made the plain one ("Zo\\u00eb" / "Zoë" / "zoe", "O’Neil" / "O'Neil", "Anne‑Marie"). Pure."""
    t = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), str(text or "")).replace('\\"', '"')
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    t = unicodedata.normalize("NFKC", t).casefold()
    return re.sub(r"[\u2010-\u2015\u2212]", "-", re.sub(r"[\u2018\u2019\u02bc`\u00b4]", "'", t))


def name_keys(text: str) -> set:
    """Every way a name in ``text`` can be matched: each whole word ("anne-marie", "o'neil"), its joined form ("annemarie",
    "oneil") and its hyphen / apostrophe parts of two letters or more ("anne", "marie", "neil"), on ``fold_name_text``. Pure."""
    out: set = set()
    for w in re.findall(r"[a-z0-9]+(?:['-][a-z0-9]+)*", fold_name_text(text)):
        out.add(w)
        out.add(re.sub(r"['-]", "", w))
        out.update(p for p in re.split(r"['-]", w) if len(p) >= 2)
    return out


def _offer_matcher(groups: list, forgotten_texts: list):
    """The pending offers about the topic go with it (QA round 9: "forget that I told you about my sister" left "add Bea (your
    sister) as a contact?" pending, and it was offered twice more): an offer holding the topic words, or one whose proposed name
    was said in a forgotten turn / row."""
    said = set().union(*(name_keys(t) for t in forgotten_texts)) if forgotten_texts else set()

    def matches(text: str) -> bool:
        # the proposed name, read from the stored slots as written (JSON-escaped: "Zo\u00eb", "O\"Neil"), compared on ONE fold:
        # ANY whole name or part the forgotten words hold withdraws the offer (over-withdrawing is harmless, a survivor is not)
        names = re.findall(r'"name":\s*"((?:[^"\\]|\\.)*)"', text or "")
        return row_matches(groups, text) or any(name_keys(n) & said for n in names)
    return matches


async def forget(svc: Any, user_id: str, topic: str) -> Result:
    """Forget the topic. Never raises: a failure is a ``Result`` that says what was and was not done."""
    try:
        return await _forget(svc, user_id, topic)
    finally:   # any forget drops the member's pending corrections - AFTER their transcript turns went off the record
        try:   # never raises: a failing clear must not replace the forget's own result or exception
            import correction_marker

            correction_marker.forget_user(user_id, reason="forget_topic")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PENDING_CORRECTION clear_failed reason=forget_topic user=%s err=%s", user_id, type(exc).__name__)


async def _resolve(svc: Any, user_id: str, groups: list, res: Result, seen: dict) -> Any:
    """The topic's whole target set as ids (``forget_record.Plan``), BEFORE anything is erased: the rows holding the topic, the
    recent turns that said it (retracted here: the in-process wall, so a capture in flight never lands) and the exact-words entries
    that hold it, their saved turns, the notes / registry entries / pending offers about it. RAISES on a store failure."""
    import conversation_notes
    import forget_record
    import memory_value_registry
    import night_mind
    import pending_suggestions
    import turn_forget

    rows = await _owned_rows(svc, user_id, groups)
    res.found = len(rows)
    if len(rows) > MAX_ROWS:
        res.too_many = True            # nothing is touched - rows AND words: ask which part
        return None
    turns = turn_forget.turns_matching(user_id, lambda text: row_matches(groups, text))
    for turn in turns:
        turn_forget.retract(user_id, turn)
    # the names come from every place the words still are: the recent turns, the rows, and the PERSISTED exact-words index
    # (after a restart the turn "my sister Bea lives in Hobart" may be nowhere else; QA round 9 review)
    hits = await _index_hits(user_id, groups)
    mids = await _said_mids(user_id, turns, hits, rows)
    # Erasing a saved turn takes every live fact derived from it, regardless of its wording.
    if mids:
        rows = list({r.id: r for r in [*rows, *(await _owned_rows(svc, user_id, [], mids=mids))]}.values())
    res.found = len(rows)
    if len(rows) > MAX_ROWS:
        res.too_many = True
        return None
    said = [t.text for t in turns] + list(hits.values())
    legacy_words = list(dict.fromkeys([*said, *((r.metadata or {}).get("source_excerpt") or ""
                                               for r in rows if turn_forget.classify(r)[0] == turn_forget.LEGACY)]))
    row_texts = [getattr(r, "text", "") or "" for r in rows]
    try:
        import value_correction
    except Exception:  # noqa: BLE001
        value_correction = None
    reg_turns = [value_correction.turn_id_of(user_id, t) for t in said] if value_correction is not None else []
    plan = forget_record.Plan(
        rows=[r.id for r in rows], mids=mids, entries=list(hits),
        obs=await night_mind.legacy_observation_ids(user_id, *legacy_words),
        notes=await conversation_notes.ids_for_topic(user_id, lambda text: row_matches(groups, text)),
        reg=await memory_value_registry.ids_for_names(user_id, lambda name: any(row_matches([g], name) for g in groups)),
        reg_turns=reg_turns, reg_topic=True,
        offers=await pending_suggestions.ids_matching(user_id, _offer_matcher(groups, said + row_texts)),
        n={"found": len(rows), "retracted": len(turns), "words": len(hits)})
    if value_correction is not None:
        # the in-process correction memory (never outlives the process): a correction about any topic word, one SAID in a
        # retracted turn, or one whose subject the turns / rows name. Each clear on its own: one failing never skips another.
        for clear in (lambda: value_correction.forget_matching(user_id, lambda text: any(row_matches([g], text) for g in groups)),
                      lambda: value_correction.forget_turns(user_id, reg_turns),
                      lambda: value_correction.forget_in_texts(user_id, said + row_texts)):
            try:
                clear()
            except Exception:  # noqa: BLE001
                pass
    seen["preview"] = _preview(rows[0].text) if rows else ""
    if not (plan.rows or plan.mids or plan.entries or plan.obs or plan.notes or plan.reg or plan.offers or reg_turns):
        return None
    return plan


async def _forget(svc: Any, user_id: str, topic: str) -> Result:
    import forget_record
    import turn_forget

    res = Result()
    groups = topic_groups(topic)
    if not groups:
        return res
    seen: dict = {}
    key = forget_record.words_key(user_id, "topic", " | ".join(sorted(" ".join(sorted(g)) for g in groups)))
    try:     # an EARLIER forget's unfinished steps (the entity cascade's markers, OTHER open forget records): finish them first. This
        import memory_forget_cascade      # topic's own open record is finished below by this request (and answered for)

        await memory_forget_cascade.retry_pending(user_id, skip_records=(forget_record.storage_key(user_id, key),))
    except Exception:  # noqa: BLE001 - never raises; a failure leaves them for the next healthy pass
        pass
    out = await forget_record.forget(user_id, code="topic", key=key, svc=svc, resolve=lambda: _resolve(svc, user_id, groups, res, seen))
    if out.nothing or res.too_many:
        return res
    if not out.complete:
        try:    # "I won't bring it up meanwhile": the offer reads (pending_suggestions) withhold what a tombstone names
            import memory_tombstones

            for word in min(groups, key=len):
                memory_tombstones.add(user_id, word)           # in-process and short (the durable answer is the honest reply)
        except Exception:  # noqa: BLE001
            pass
    if not out.started or out.plan is None:
        logger.info("forget_topic: store unavailable (%s)", type(out.error).__name__ if out.error else "-")
        res.store_down = True
        return res
    n = out.plan.n
    res.found, res.turns_retracted, res.words_erased = n.get("found", res.found), n.get("retracted", 0), n.get("words", 0)
    if not out.complete:
        if "reject" in out.failed:
            res.failed = res.found          # never erased on top of a failed archive: it stays hidden-or-recalled, and is SAID
        else:
            res.store_down = True
        logger.warning("forget_topic: not finished (%s) - the record stays open", ",".join(out.failed))
        return res
    for turn in turn_forget.turns_matching(user_id, lambda text: row_matches(groups, text)):
        turn_forget.mark_forgotten(user_id, turn)
    res.forgotten = len(out.plan.rows)
    res.preview = seen.get("preview", "")
    logger.info("forget_topic: user=%s found=%d forgotten=%d retracted=%d words=%d",
                user_id, res.found, res.forgotten, res.turns_retracted, res.words_erased)
    return res


def reply(res: Result, topic: str) -> str:
    """The honest sentence for a ``Result``."""
    if res.store_down:
        return ("I couldn't reach my notes just now, so I can't say that's forgotten. "
                "I won't bring it up meanwhile - please tell me again in a moment.")
    if res.too_many:
        return (f"That touches {res.found} things I've saved, so I haven't forgotten any of it. "
                "Tell me which part (a few more words) and I'll forget just that.")
    if not res.found:
        if res.turns_retracted or res.words_erased:
            # it was said but not filed yet (the capture runs minutes behind): it is forgotten, never "nothing saved" (QA round 8)
            return "Done — I've forgotten what you told me about that, so it won't be kept. Nothing else was changed."
        return "I couldn't find anything saved about that, so nothing was changed."
    if res.failed and not res.forgotten:
        return ("I found it but couldn't remove it just now, so it's still saved. "
                "Say it again in a minute and I'll finish.")
    if res.failed:
        return (f"I forgot {res.forgotten} of the {res.found} things, but couldn't remove {res.failed} just now, "
                "so that part is still saved. Say it again in a minute and I'll finish.")
    more = res.forgotten - 1
    tail = f" (and {more} more)" if more > 0 else ""
    if not res.preview:
        return f"Done — I've forgotten what you told me about that{tail}."
    return f"Done — I forgot: \"{res.preview}\"{tail}."


async def execute(user_id: str, topic: str) -> str:
    """The ``memory_forget_entity`` intent with a ``topic`` slot. Guests have no memory store: fail closed."""
    try:
        from memory_service import get_memory_service, is_guest_memory_user

        if is_guest_memory_user(user_id):
            return "I don't keep memories in guest sessions, so there's nothing for me to forget."
        svc = get_memory_service()
    except Exception as exc:  # noqa: BLE001
        logger.info("forget_topic: service unavailable (%s)", type(exc).__name__)
        return "I couldn't reach the memory store right now, so nothing was changed."
    return reply(await forget(svc, user_id, topic), topic)

