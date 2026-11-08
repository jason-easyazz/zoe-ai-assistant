"""A write needs an ask - flag ``ZOE_WRITE_INTENT_GATE=off|shadow|on`` (default ``on``).

THE MISS (QA 2026-11-08): "ok so thursday week i have <a learner test> and netball on saturday morning ... help me think through the
logistics" made the brain call ``add_calendar_event`` THREE times. The dates did not resolve, so nothing was written; with resolvable
dates three events would have existed. Nobody said add, put, book or schedule. The same round: "add bolts to the list" (no list named)
put a hardware item on the household SHOPPING list.

Both are the same class - the 4B reaches for a write tool because a write tool is in reach - and the same fix: the tools' only door is
``POST /api/system/intent-dispatch``, which sees the intent and the slots but not what the person said. ``zoe_flue_client`` notes each
brain turn's message here (like ``memory_retire.note_turn``) and ``routers.system.intent_dispatch`` asks :func:`check` before it
fulfils a ``calendar_create`` or a ``list_add``. A refusal is a 422 whose detail the sidecar's tool already relays to the model
("nothing was saved: <reason>"), so the model tells the person plainly and carries on in words.

* ``calendar_create`` needs an explicit write verb in the turn (add / put / book / schedule / pencil in / diarise / "create an event"), a short
  affirmation of Zoe's own offer ("yes please", "go ahead"), or a short follow-up inside a conversation that already asked for a write
  ("7pm" after "add dinner on friday"). A noun is not a verb: "my schedule", "the book", "a put" never count.
* ``list_add`` onto the household SHOPPING list, when the turn named no list, needs an item that does not look like a hardware, work or
  personal-admin thing (:data:`NON_GROCERY_RX`). Otherwise Zoe asks which list. "add milk to the list" still goes straight to shopping.

No noted turn (a channel that never noted one, a stale note) => allowed: the gate only ever REFUSES on evidence. The words are data in
this module and English-only on purpose: a turn in another language is not gated (nothing is refused it cannot read).

Stdlib only. Never raises. ``shadow`` logs ``WRITE_INTENT_GATE mode=shadow ... would_refuse=1`` and refuses nothing; ``off`` reads nothing.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_WRITE_INTENT_GATE"
_TURN_TTL_S = 300.0
_FOLLOWUP_TTL_S = 180.0
_FOLLOWUP_MAX_WORDS = 8
_MAX = 256
#: user id -> (message, noted at)
_turns: "dict[str, tuple[str, float]]" = {}
#: user id -> when a turn last asked for a calendar write (a short answer to Zoe's follow-up question rides on it)
_asked: "dict[str, float]" = {}
#: user id -> when Zoe's own last reply offered to put something on the calendar ("Want me to add those?")
_offered: "dict[str, float]" = {}
_OFFER_RX = re.compile(
    r"\b(?:want|like)\s+me\s+to\s+(?:add|put|book|schedule|pencil)\b|\b(?:shall|should|can|could)\s+i\s+(?:add|put|book|schedule)\b|"
    r"\bi\s+(?:can|could)\s+(?:add|put|book|schedule)\s+(?:it|that|those|them|these)\b", re.IGNORECASE)


def mode() -> str:
    """``on`` (default; unset or unknown) | ``shadow`` | ``off``. Read per call."""
    raw = env_str("ZOE_WRITE_INTENT_GATE").lower()   # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw == "shadow":
        return "shadow"
    return "on"


# -- the words (data) --------------------------------------------------------------------------------------

_WRITE_VERBS = (
    r"add(?!\s+up\b)|put(?!\s+together\b)|book|schedule|pencil|diarise|diarize|lock\s+in|block\s+out|"
    r"(?:create|set\s+up|make)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:calendar\s+)?(?:event|appointment|meeting)"
)
_WRITE_VERB_RX = re.compile(r"\b(?:" + _WRITE_VERBS + r")\b", re.IGNORECASE)
#: a word before the verb that makes it a NOUN ("my schedule", "the book", "a put")
_DETERMINERS = frozenset({"my", "the", "our", "your", "his", "her", "their", "this", "that", "a", "an", "its", "whose", "no", "any"})
#: "schedule" followed by one of these is the noun again ("the schedule is crazy", "schedule for next week")
_NOUN_FOLLOWERS = frozenset({"for", "is", "are", "was", "looks", "has", "of"})
#: "schedule this week / next month" is the noun again, but "schedule this appointment for Friday" is the verb: after this/next, only a
#: period word makes it a noun
_PERIOD_WORDS = frozenset({"week", "weeks", "month", "months", "weekend", "year", "fortnight", "morning", "afternoon", "evening",
                           "arvo", "time", "term", "season", "quarter", "semester"})
#: common English function words: a turn of three or more words holding none of them is not English (a Spanish "Añade una cita
#: al calendario mañana a las tres"), and the gate - English-only by design - lets it through rather than read its silence as "no ask"
_EN_WORDS = frozenset(
    "the an to of and in on at for with my i you we it is are was be do does did can could would should will shall please this that "
    "these those your our their his her what when where who how why not no yes ok okay just so if or but about from by up out into then "
    "also some any all there here have has had need want let lets give get make think going tomorrow today tonight next week".split())


def _probably_english(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    if len(words) < 3:
        return True
    return any(w in _EN_WORDS for w in words)
_AFFIRM_RX = re.compile(
    r"^\W*(?:(?:ok|okay|and|so|well)[\s,]+)*(?:yes|yeah|yep|yup|sure|please|do\s+it|go\s+ahead|go\s+for\s+it|go\s+on|"
    r"sounds\s+good|that'?s\s+(?:right|good|fine|perfect)|perfect|great|correct|absolutely|definitely|of\s+course|"
    r"yes\s+please|all\s+of\s+(?:them|those)|both|the\s+lot)\b",
    re.IGNORECASE,
)


_NEGATORS = frozenset({"dont", "don't", "not", "never", "no", "without", "stop", "wont", "won't", "cant", "can't", "cannot",
                      "shouldnt", "shouldn't", "mustnt", "mustn't", "neednt", "needn't", "avoid", "skip"})


def _negated_before(text: str, pos: int) -> bool:
    """A negator within the three words before the verb, in the same clause ("don't add", "no need to put", "please do not book")."""
    clause = re.split(r"[.;!?]|,\s|\b(?:but|and|then)\b", text[:pos])[-1]
    words = [w.strip(",.;:!?'\"").lower() for w in clause.split()][-3:]
    return any(w in _NEGATORS for w in words)


def _verb_hit(text: str) -> bool:
    """An imperative-ish use of a write verb: not preceded by a determiner, and ``schedule`` not followed by a noun-ish word."""
    for m in _WRITE_VERB_RX.finditer(text or ""):
        before = (text[: m.start()].split() or [""])[-1].strip(",.;:!?'\"").lower()
        if before in _DETERMINERS:
            continue
        if _negated_before(text, m.start()):
            continue               # "don't add the appointment to my calendar" is an instruction NOT to write
        word = m.group(0).lower()
        after = (text[m.end():].split() or [""])[0].strip(",.;:!?'\"").lower()
        if word == "schedule" and (not after or after in _NOUN_FOLLOWERS):
            continue
        if word == "schedule" and after in ("this", "next"):
            rest = text[m.end():].split()
            nxt = (rest[1] if len(rest) > 1 else "").strip(",.;:!?'\"").lower()
            if not nxt or nxt in _PERIOD_WORDS:
                continue
        return True
    return False


def asks_to_write_calendar(text: str) -> bool:
    """Did this turn explicitly ask for a calendar write? Pure."""
    return _verb_hit(text)


# a hardware / work / personal-admin thing - something the household does not buy at the supermarket
NON_GROCERY_RX = re.compile(
    r"\b(?:bolts?|screws?|nails?|washers?|anchors?|brackets?|hinges?|cables?|wires?|conduit|fuses?|breakers?|"
    r"insulation|tape|drills?|saws?|hammers?|spanners?|wrenches|wrench|pliers|screwdrivers?|tools?|ladders?|torch(?:es)?|"
    r"chargers?|adapt[eo]rs?|laptops?|usbs?|paint|sandpaper|boots?|shoes?|shirts?|jeans|jackets?|uniforms?|hi-?vis|helmets?|"
    r"invoices?|receipts?|quotes?|paperwork|forms?|passports?|licen[cs]es?|rego|registration|insurance|tax|tyres?|tires?|"
    r"spark\s+plugs?|oil\s+filters?|wiper\s+blades?|mower)\b",
    re.IGNORECASE,
)


def looks_non_grocery(item: str) -> bool:
    """Is the item clearly a hardware / work / personal-admin thing rather than something from the shop? Pure."""
    return bool(NON_GROCERY_RX.search(item or ""))


_GENERIC_LIST_WORDS = frozenset({"the", "my", "our", "a", "this", "that", "your", "to", "on", "in", "of", "and", "or"})
_LIST_TYPE_RX = re.compile(r"\b(?:shopping|grocery|groceries|to-?do|to\s+do|personal|work|bucket|tasks?|chores?)\b", re.IGNORECASE)
_LIST_REF_RX = re.compile(r"\b(\w+)\s+list\b", re.IGNORECASE)
_LIST_ASK_RX = re.compile(r"\b(?:add|put)\b|\blist\b", re.IGNORECASE)


_LIST_TYPES = r"(?:shopping|grocery|groceries|to-?do|to\s+do|personal|work|bucket|tasks?|chores?)"
#: a list TYPE counts only as the destination: "to (the) work", "on my chores", "work list" - never a word inside the item ("work boots")
_LIST_DEST_RX = re.compile(
    r"\b(?:to|on|onto|in|into)\s+(?:(?:the|my|our|your)\s+)?" + _LIST_TYPES + r"\b(?!\s+(?:boots?|shoes?|shirts?|gear|clothes|trousers|pants|"
    r"jacket|gloves?|uniform|laptop|bag|bags)\b)|\b" + _LIST_TYPES + r"\s+(?:list|tasks?)\b", re.IGNORECASE)


def names_a_list(text: str) -> bool:
    """Does the turn name WHICH list ("my work list", "the shopping list", "to personal")? "the list" / "my list" do not, and a
    list-type word inside the ITEM ("add work boots to the list") is not a destination. Pure."""
    t = text or ""
    if _LIST_DEST_RX.search(t):
        return True
    return any(m.group(1).lower() not in _GENERIC_LIST_WORDS for m in _LIST_REF_RX.finditer(t))


def asks_for_a_list_add(text: str) -> bool:
    return bool(_LIST_ASK_RX.search(text or ""))


# -- the turn ----------------------------------------------------------------------------------------------

def note_turn(user_id: str, message: str) -> None:
    """Record this user's current brain turn (called by ``zoe_flue_client`` as the turn starts). Never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid or mode() == "off":
            return
        text = str(message or "")
        now = time.monotonic()
        _turns.pop(uid, None)
        _turns[uid] = (text, now)
        if asks_to_write_calendar(text):
            _asked.pop(uid, None)
            _asked[uid] = now
        elif len(text.split()) > _FOLLOWUP_MAX_WORDS:
            _asked.pop(uid, None)        # a long turn with no write verb is another topic: the write conversation is over
        for d in (_turns, _asked, _offered):
            while len(d) > _MAX:
                d.pop(next(iter(d)))
    except Exception:  # noqa: BLE001
        return


def note_reply(user_id: str, reply: str) -> None:
    """Record whether Zoe's reply OFFERED a calendar write, so a bare "yes" to it is an ask. Called by ``reply_polish``. Never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid or mode() == "off":
            return
        _offered.pop(uid, None)
        if _OFFER_RX.search(str(reply or "")):
            _offered[uid] = time.monotonic()
    except Exception:  # noqa: BLE001
        return


def _noted(user_id: str) -> Optional[str]:
    got = _turns.get((user_id or "").strip())
    if got is None or time.monotonic() - got[1] > _TURN_TTL_S:
        return None
    return got[0]


def reset_state() -> None:
    _turns.clear()
    _asked.clear()
    _offered.clear()


def _calendar_asked(uid: str, text: str) -> bool:
    if asks_to_write_calendar(text):
        return True
    words = text.split()
    if len(words) > _FOLLOWUP_MAX_WORDS:
        return False
    now = time.monotonic()
    offered_at, asked_at = _offered.get(uid), _asked.get(uid)
    if _AFFIRM_RX.match(text) and offered_at is not None and now - offered_at <= _FOLLOWUP_TTL_S:
        return True                  # "yes please" to Zoe's own offer
    return bool(asked_at is not None and now - asked_at <= _FOLLOWUP_TTL_S)   # a short answer inside a write conversation


def which_list_question(item: str) -> str:
    item = " ".join(str(item or "").split())[:60] or "that"
    return f"Which list should I put {item} on - your shopping list or your personal list?"


def check(user_id: str, intent: str, slots: dict) -> Optional[str]:
    """A plain-English reason the write must NOT run, or None (run it). Called by ``/api/system/intent-dispatch`` before fulfilment."""
    try:
        m = mode()
        if m == "off" or intent not in ("calendar_create", "list_add"):
            return None
        uid = (user_id or "").strip()
        text = _noted(uid)
        if text is None or not _probably_english(text):
            return None            # nothing noted, or a turn in a language this gate cannot read: never refused on its silence
        reason: Optional[str] = None
        if intent == "calendar_create":
            if not _calendar_asked(uid, text):
                reason = ("they have not asked for anything to go on the calendar, only to talk it through; carry on in words "
                          "and offer to add it")
        else:
            s = slots or {}
            shopping = str(s.get("list_type") or "").strip().lower() in ("", "shopping") and not (
                str(s.get("list_name") or "").strip() or str(s.get("list_phrase") or "").strip())
            item = str(s.get("item") or "")
            if shopping and asks_for_a_list_add(text) and not names_a_list(text) and looks_non_grocery(item):
                reason = "they did not say which list and this is not a grocery; ask them: " + which_list_question(item)
        if reason is None:
            return None
        logger.info("WRITE_INTENT_GATE mode=%s intent=%s would_refuse=1", m, intent)
        return reason if m == "on" else None
    except Exception:  # noqa: BLE001 - a gate is optional; the write path is not
        logger.warning("WRITE_INTENT_GATE check failed; allowing", exc_info=True)
        return None
