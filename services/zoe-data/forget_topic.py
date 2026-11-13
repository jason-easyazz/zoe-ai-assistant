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


async def _owned_rows(svc: Any, user_id: str, groups: list) -> list:
    """Every row of the owner's (approved / pending / disputed / superseded) holding the whole topic. RAISES on a store
    failure: a forget must never be confirmed from a read that did not happen."""
    out, seen = [], set()
    for status in ("approved", "pending", "disputed", "superseded"):
        offset = 0
        while True:
            page = await svc.list_by_status(user_id=user_id, status=status, limit=1000, offset=offset)
            if not page:
                break
            for r in page:
                md = getattr(r, "metadata", {}) or {}
                if getattr(r, "id", "") in seen or (md.get("user_id") or md.get("wing")) != user_id:
                    continue
                if row_matches(groups, getattr(r, "text", "") or "") or row_matches(groups, str(md.get("retire_quote") or "")):
                    seen.add(r.id)
                    out.append(r)
            if len(page) < 1000:
                break
            offset += len(page)
    return out


async def _erase_words(user_id: str, groups: list) -> tuple:
    """The owner's words about the topic: the in-process recent turns (retracted, so a capture in flight never lands) and the
    exact-words index entries (deleted), plus the saved transcript turn of each (flagged off the record). RAISES when the index
    could not be read or written (a forget that could not erase the words is not confirmed)."""
    import exact_words
    import turn_forget

    retracted = 0
    for turn in turn_forget.turns_matching(user_id, lambda text: row_matches(groups, text)):
        turn_forget.retract(user_id, turn)
        # the saved transcript turn too, whether or not the exact-words index holds it: the nightly readers (digest, idle
        # consolidation) re-mine chat_messages long after the in-process wall has expired (RAISES: not confirmed if not flagged)
        await exact_words.forget_transcript(user_id, turn.text)
        turn_forget.mark_forgotten(user_id, turn)
        retracted += 1
    erased = 0
    group = min(groups, key=len)                    # the narrowest group: "heart", not the eight spellings of "mum"
    backend = exact_words.get_backend()
    hits: dict = {}
    for needle in sorted(group):
        for tid, text in await backend.rows_matching(user_id, needle):
            if row_matches(groups, text or ""):
                hits[tid] = text
    if hits:
        erased = int(await backend.delete(user_id, list(hits)) or 0)
        for text in hits.values():
            try:
                await exact_words.forget_transcript(user_id, text)
            except Exception as exc:  # noqa: BLE001 - the index row is gone; the transcript flag is the second line
                logger.warning("forget_topic: transcript flag failed (%s)", type(exc).__name__)
    return retracted, erased


async def _withdraw_offers(user_id: str, groups: list, forgotten_texts: list) -> int:
    """The pending offers about the topic go with it (QA round 9: "forget that I told you about my sister" left "add Bea (your
    sister) as a contact?" pending, and it was offered twice more): an offer holding the topic words, or one whose proposed name
    was said in a forgotten turn / row. RAISES when the offers could not be read or deleted: the forget is then NOT confirmed
    (``store_down``, the same honest outcome as a failed notes erase) and the member's repeat runs it again."""
    import pending_suggestions

    said = {w for t in forgotten_texts for w in re.findall(r"[a-z]+", (t or "").lower())}

    def matches(text: str) -> bool:
        names = {w.lower() for n in re.findall(r'"name":\s*"([^"]+)"', text or "") for w in n.split()}
        return row_matches(groups, text) or bool(names and names <= said)

    gone = await pending_suggestions.withdraw_matching(user_id, matches)
    if gone:
        logger.info("forget_topic: user=%s offers_withdrawn=%d", user_id, gone)
    return gone


async def forget(svc: Any, user_id: str, topic: str) -> Result:
    """Forget the topic. Never raises: a failure is a ``Result`` that says what was and was not done."""
    res = Result()
    groups = topic_groups(topic)
    if not groups:
        return res
    try:
        rows = await _owned_rows(svc, user_id, groups)
    except Exception as exc:  # noqa: BLE001
        logger.info("forget_topic: store unavailable (%s)", type(exc).__name__)
        res.store_down = True
        return res
    res.found = len(rows)
    if len(rows) > MAX_ROWS:
        res.too_many = True            # nothing is touched - rows AND words: ask which part
        return res
    import turn_forget

    said = [t.text for t in turn_forget.turns_matching(user_id, lambda text: row_matches(groups, text))]
    # the pending offers FIRST: a failure leaves the forget not confirmed with nothing half-done, so the member's repeat sees the
    # same turns and rows (and so the same names) again
    try:
        await _withdraw_offers(user_id, groups, said + [getattr(r, "text", "") or "" for r in rows])
    except Exception as exc:  # noqa: BLE001 - not confirmed: an offer about the topic may still be pending
        logger.warning("forget_topic: pending offers not withdrawn (%s) - not confirmed", type(exc).__name__)
        try:        # "I won't bring it up meanwhile": the offer reads (pending_suggestions) withhold what a tombstone names
            import memory_tombstones

            for word in min(groups, key=len):
                memory_tombstones.add(user_id, word)       # in-process and short (the durable answer is the honest reply)
        except Exception:  # noqa: BLE001
            pass
        res.store_down = True
        return res
    try:
        # the words before the rows, like every forget: the in-flight capture of those turns cannot land
        res.turns_retracted, res.words_erased = await _erase_words(user_id, groups)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forget_topic: words erase failed (%s)", type(exc).__name__)
        res.store_down = True
        return res
    try:     # the member's short notes about past conversations that are about the topic (conversation_notes; raises on a store error)
        import conversation_notes

        await conversation_notes.erase_topic(user_id, lambda text: row_matches(groups, text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("forget_topic: conversation notes erase failed (%s)", type(exc).__name__)
        res.store_down = True
        return res
    archived: list = []
    for r in rows:
        try:
            ref = await svc.review(r.id, decision="archive", actor=user_id, note="forget_topic")
        except Exception as exc:  # noqa: BLE001
            logger.warning("forget_topic: archive failed (%s)", type(exc).__name__)
            ref = None
        if ref is None:
            res.failed += 1            # never erased on top of a failed archive: it stays hidden-or-recalled, and is SAID
        else:
            archived.append(r)
    if archived and hasattr(svc, "erase_rows"):
        try:
            from memory_service import physical_erase_enabled

            if physical_erase_enabled():
                await svc.erase_rows(user_id, [r.id for r in archived], actor=user_id, reason="forgotten by request (topic)")
        except Exception as exc:  # noqa: BLE001 - archived rows are hidden from every read either way
            logger.warning("forget_topic: physical erase failed (%s) - rows stay archived", type(exc).__name__)
    res.forgotten = len(archived)
    if archived:
        res.preview = _preview(archived[0].text)
    logger.info("forget_topic: user=%s found=%d forgotten=%d failed=%d retracted=%d words=%d",
                user_id, res.found, res.forgotten, res.failed, res.turns_retracted, res.words_erased)
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

