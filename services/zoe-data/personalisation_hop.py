"""personalisation_hop - a durable fact about the owner must shape generic advice (Samantha bar S9a / S9b).

The miss (day-sim 2026-10-03, "no-card baselines all FAIL"): the user told Zoe on day 1 that they work night
shifts and sleep during the day, and that they walk the dog at 6am. Days later "Any tips for sleeping better?"
got generic night-time sleep hygiene and "What should I wear tomorrow? It's meant to be really cold." got "fine
without a jacket". Nothing put the fact in front of the 4B brain: neither question is a recall question
(``zoe_flue_client._recall_question_shape``), so no packet is fetched, and the user-model card that is meant to
carry such facts is not served to synthetic users (``user_portrait.user_model_enabled``) and is only as fresh as
the last nightly rebuild. A hop is needed between the two facts: the QUESTION (sleep / what to wear) and the FACT
(night shift / 6am dog walk) share no words, so even a semantic search for the question does not find it.

What this does, on a GENERIC ADVICE request only (``advice_topics``: "any tips for ...", "what should I wear ...",
"what shall I cook ...", "ideas for ..."), flag ``ZOE_PERSONALISATION_HOP`` (default ON):

1. classify the request into a few topics (sleep, clothing/weather, food, activity/health, travel, kids, routine);
2. read the owner's DURABLE facts - approved rows the owner stated (``memory_authority`` user classes), no age cut:
   they do not decay (``ZOE_RECALL_DURABLE_NO_DECAY``, #1911), and a fact from last month is as true as one from
   yesterday - never moods or recorded changes, never a row about somebody else, never a fact the owner has since
   dropped ("no longer works nights");
3. keep the (at most ``MAX_FACTS`` = 2) whose words are a known constraint for that topic (a night shift for sleep,
   a 6am walk or a commute for what to wear, a diet or an allergy for what to cook, a child's age for what to do);
4. surface them with ONE rule line - "Shape the answer by these facts ..." - as a short block in the recall packet
   (``routers.memories.memory_for_prompt``) and on the Flue seam (``zoe_flue_client._hop_context_block``).

WHERE the facts ride on the Flue wire is its own flag (``ZOE_PERSONALISATION_HOP_PLACEMENT`` = ``block`` | ``suffix`` |
``preamble``, ``placement()``). The live acceptance of 2026-10-09 (S9b FAIL with ``hop_in_packet`` true, P2.b 8/20) looked like
"the 4B ignores the hop"; it was not (the A/B below: the block already moves a hop that REACHES the brain 84/90). The two
causes were upstream of the brain: (1) S9b was never a brain turn - the weather expert answered "What should I wear tomorrow?"
from the forecast (``fast_tiers`` now defers a request this module owns, ``_hop_owns_turn``), and (2) the request-shape
regex recognised only 2 of the 5 phrasings of the P family's diet ask ("Any dinner ideas?", "What's a good dinner for
tonight?", "I can't decide what to make for tea." were not hop turns: 2 x 4 asks = the 8/20). The placements stay as options:
``block`` is the delimited block after the words (the default - measured best), ``suffix`` is ONE parenthetical line after
the words ("(you know this about me: ...)"), ``preamble`` is the delimited block BEFORE the words.
``scripts/perf/hop_placement_ab.py`` is the measurement (docs/knowledge/samantha-bar.md has the table).

A question that is not a request for advice, or whose topic matches no durable fact, adds NOTHING (the bytes are
what they were). Bounded: <= 2 facts, each <= ``FACT_CHARS`` chars, one read of the owner's rows under a hard
``TIMEOUT_S`` (a slow store costs the block, never the turn). Counts only are logged (``PERSONALISATION_HOP``),
never the facts. Only the owner's own rows are read (family-visible rows of other members are not the owner's).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_PERSONALISATION_HOP"
MAX_FACTS = 2
FACT_CHARS = 180
TIMEOUT_S = 1.2
HEADING = "## Shape the answer by"
RULE = ("Shape the answer by these facts the user told you: fit the advice to them instead of giving generic advice; "
        "do not recite them or mention this note.")


PLACEMENT_ENV = "ZOE_PERSONALISATION_HOP_PLACEMENT"
PLACEMENTS = ("block", "suffix", "preamble")
DEFAULT_PLACEMENT = "block"
#: the suffix line opens with this and ends with ")": ONE line, so the sidecar can elide it from older turns
#: (``labs/flue-zoe-brain-2x/src/context-blocks.ts`` ``HOP_NOTE_PREFIX``; pinned equal by a test)
SUFFIX_OPEN = "(you know this about me: "


def placement() -> str:
    """Where the facts ride on the Flue wire: ``block`` | ``suffix`` | ``preamble``; per-call read, an unknown value
    (a typo) is the default - the hop must degrade to the shipped placement, never to none."""
    from typed_env import env_str

    # the flag name and its default are LITERALS here: tools/audit/flag_inventory.py reads them off the call site
    v = env_str("ZOE_PERSONALISATION_HOP_PLACEMENT", "block").lower()
    return v if v in PLACEMENTS else DEFAULT_PLACEMENT


def enabled() -> bool:
    """``ZOE_PERSONALISATION_HOP`` - default ON, per-call read; ``0|false|no|off`` (or set-but-empty) = off."""
    from typed_env import env_bool

    return env_bool("ZOE_PERSONALISATION_HOP", True)


# ── is it a request for generic advice? (pure) ───────────────────────────────────────────────

_ADVICE_RE = re.compile(
    r"\b(?:"
    r"any\s+(?:(?:good|great|nice|easy|quick|healthy|new|other)\s+)?(?:\w+\s+)?(?:tips?|advice|suggestions?|ideas?|recommendations?|thoughts|pointers|hints?)"
    r"|(?:some|a\s+few|got\s+any|have\s+you\s+got\s+any)\s+(?:tips?|advice|suggestions?|ideas?|recommendations?)"
    r"|(?:tips?|advice|ideas?|suggestions?|recommendations?)\s+(?:for|on|about|to)\b"
    r"|what\s+(?:should|shall|can|could|would)\s+(?:i|we)\s+(?:wear|eat|cook|make|have|do|pack|bring|get|buy|try|take|drink|order|plan|pick)"
    r"|what\s+to\s+(?:wear|eat|cook|make|have|do|pack|bring|get|buy|try|take|drink|order|plan)"
    r"|(?:can|could|would|will)\s+you\s+(?:recommend|suggest)"
    r"|(?:recommend|suggest)\s+(?:me\s+)?(?:a|an|some|something|anything)"
    r"|how\s+(?:can|do|should|could)\s+(?:i|we)\s+(?:sleep|rest|relax|unwind|stay|keep|get\s+(?:better|more|enough|some|good)|"
    r"improve|boost|fall\s+asleep|wake|feel\s+(?:better|more)|be\s+more|avoid|prepare|plan|pack|dress)"
    r"|what(?:['’]s|\s+is)\s+(?:a\s+)?(?:good|best|better|the\s+best)\s+(?:way|thing|food|meal|time|idea)"
    r"|what(?:['’]?s|\s+is)\s+(?:an?\s+)?(?:good|best|better|nice|decent|easy|quick|healthy|light)\s+\w+(?:\s+\w+)?\s+(?:for|to)\b"
    r"|(?:help|advise)\s+me\s+(?:to\s+)?(?:plan|choose|decide|pick|sleep|dress|pack|cook)"
    r"|should\s+(?:i|we)\s+(?:wear|bring|take|pack|eat|cook|have|drink|go|stay|do)\b"
    r"|(?:i\s+need|i\s+want|looking\s+for)\s+(?:some\s+)?(?:tips?|advice|ideas?|suggestions?|a\s+recommendation)"
    r")",
    re.IGNORECASE,
)
#: a question ABOUT the owner's own stored facts is a recall question, not a request for advice
_RECALL_SHAPE_RE = re.compile(
    r"\b(?:what['’]?s\s+my|what\s+is\s+my|what\s+did\s+i|when\s+did\s+i|do\s+you\s+remember|what\s+do\s+you\s+know"
    r"\s+about\s+me|who\s+is\s+my|who['’]?s\s+my)\b", re.IGNORECASE)

#: topic -> (words of the REQUEST, words of a FACT that are a known constraint for it, what the advice turns on)
_TOPICS: dict[str, tuple[re.Pattern, re.Pattern]] = {
    "sleep": (
        re.compile(r"\b(?:sleep(?:ing)?|slept|insomnia|nap(?:s|ping)?|tired|exhaust\w*|rest(?:ing)?|wind(?:ing)?\s+down|"
                   r"bed\s?time|wak(?:e|ing)\s+up|fatigue|energy|drowsy|sleepy)\b", re.I),
        re.compile(r"\b(?:night[- ]?shifts?|nightshift|graveyard|(?:works?|working)\s+nights|"
                   r"sleep(?:s|ing)?\s+(?:during|in)\s+the\s+day|day[- ]?time\s+sleep|shift\s+work\w*|rotating\s+roster|"
                   r"on[- ]call|newborn|baby|toddler|infant|insomnia|snor\w+|sleep\s+apn\w+|cpap|"
                   r"(?:wake|get|up)s?\s+(?:up\s+)?(?:at|by|before)\s+\d|jet\s?lag|early\s+starts?)\b", re.I),
    ),
    "clothing": (
        re.compile(r"\b(?:wear(?:ing)?|clothes|clothing|outfit|dress(?:ed|ing)?|jacket|coat|jumper|sweater|layers?|"
                   r"umbrella|raincoat|boots|shoes|gloves|beanie|scarf|warm|cold|freezing|chilly|hot|rain(?:y|ing)?|"
                   r"snow|weather|sunscreen|sun\s?hat)\b", re.I),
        re.compile(r"\b(?:(?:dog[- ]?)?walk\w*|strolls?|jog(?:s|ging)?|running|cycl\w+|bike|biking|commut\w+|rides?\s+to|"
                   r"outdoors?|outside|garden\w*|hik\w+|surf\w*|swim\w*|early\s+morning|dawn|before\s+(?:work|sunrise|dawn)|"
                   r"school\s+run|bus\s+stop|construction|eczema|sensitive\s+skin|arthritis|raynaud\w*)\b", re.I),
    ),
    "food": (
        re.compile(r"\b(?:cook(?:ing)?|dinner|lunch|breakfast|meals?|recipes?|eat(?:ing)?|food|snacks?|dessert|restaurants?|"
                   r"takeaway|menu|bak(?:e|ing)|groceries|picnic|barbecue|bbq|drinks?|cocktails?|wine|tea|supper|brunch)\b", re.I),
        re.compile(r"\b(?:vegetarian|vegan|pescatarian|gluten|coeliac|celiac|dairy|lactose|nuts?|peanuts?|shellfish|"
                   r"allerg\w+|intoleran\w+|halal|kosher|diabet\w+|keto|low[- ]carb|fodmap|(?:don['’]?t|doesn['’]?t|can['’]?t)\s+eat|"
                   r"no\s+meat|picky|fussy|fish)\b", re.I),
    ),
    "activity": (
        re.compile(r"\b(?:exercis\w+|work\s?out|gym|run(?:ning)?|jog\w*|stretch\w*|fitness|training|train|lift(?:ing)?|"
                   r"yoga|pilates|sore|pain|ache|headache|diet|weight|healthy)\b", re.I),
        re.compile(r"\b(?:injur\w+|knee|back\s+(?:pain|problems?)|shoulder|hip\s+(?:replacement|surgery)|arthrit\w+|asthma|"
                   r"heart|blood\s+pressure|diabet\w+|pregnan\w+|marathon|half[- ]marathon|training\s+for|migraines?|chronic|"
                   r"surgery|physio\w*|night[- ]?shifts?)\b", re.I),
    ),
    "travel": (
        re.compile(r"\b(?:travel(?:l?ing)?|trip|holiday|vacation|weekend\s+away|getaway|itinerary|pack(?:ing)?|flight|"
                   r"fly(?:ing)?|road\s+trip|camping|day\s+out|things\s+to\s+do|visit(?:ing)?|activities)\b", re.I),
        re.compile(r"\b(?:kids?|children|toddler|baby|\d{1,2}[- ]years?[- ]old|aged\s+\d|dogs?|pets?|allerg\w+|mobility|"
                   r"wheelchair|afraid\s+of|fear\s+of\s+flying|vegetarian|vegan|budget|motion\s+sick\w*|seasick)\b", re.I),
    ),
    "kids": (
        re.compile(r"\b(?:gifts?|presents?|toys?|activit(?:y|ies)\s+for|entertain\w*|party|playdate|books?\s+for|"
                   r"games?\s+for|screen\s+time|bedtime\s+stor\w+)\b", re.I),
        re.compile(r"\b(?:kids?|son|daughter|children|toddler|baby|\d{1,2}[- ]years?[- ]old|aged\s+\d|years?\s+old|"
                   r"nephew|niece)\b", re.I),
    ),
    "routine": (
        re.compile(r"\b(?:focus|productiv\w+|procrastinat\w+|study(?:ing)?|concentrat\w+|routines?|schedule|planner|"
                   r"habits?|morning|evening)\b", re.I),
        re.compile(r"\b(?:night[- ]?shifts?|shifts?|work(?:s|ing)?\s+from\s+home|remote|freelanc\w+|student|exams?|study|"
                   r"adhd|dyslex\w+|kids?|toddler|baby|commut\w+|early\s+starts?|\d{1,2}\s?(?:am|a\.m\.)\b)", re.I),
    ),
}
#: what the advice turns on, for the log and the tests (never sent to the model)
LENS = {"sleep": "sleep and daily rhythm", "clothing": "weather and their usual outings", "food": "diet and allergies",
        "activity": "fitness and health", "travel": "who is travelling", "kids": "the children's ages",
        "routine": "their working pattern"}


#: the CONTENT of a routine, wording-independent: WHEN it happens (a time of day, a daily rhythm) and a GOING-OUT verb. A fact that
#: says both ("walks the dog along the river every morning at 6am", "cycles to the depot at 5:30", "User: dog walk, dawn") is a
#: constraint on what to wear / the weather whichever sentence shape the store kept (an NL row, a structural row, a role row)
_WHEN_RE = re.compile(
    r"\b(?:every\s+(?:single\s+)?(?:morning|evening|day|dawn|night|weekday)|each\s+(?:morning|evening|day)|daily|"
    r"(?:at|by|around|before|from)\s+\d{1,2}(?::\d{2})?\s?(?:am|a\.m\.)?|\d{1,2}(?::\d{2})?\s?(?:am|a\.m\.)\b|"
    r"dawn|sunrise|sun-?up|first\s+thing|early\s+(?:morning|mornings|start)|at\s+first\s+light)\b", re.I)
_GOING_OUT_RE = re.compile(
    r"\b(?:walk\w*|strolls?|jog\w*|running|runs?\s+(?:\d|to\b|along|around|the\b|laps)|cycl\w+|bik(?:e|es|ing)|rides?|riding|commut\w+|drives?\s+to|catch(?:es)?\s+the|"
    r"head(?:s|ing)?\s+out|goes?\s+out|takes?\s+(?:the|their|his|her|my|our)\s+\w+\s+out|"
    r"(?:along|by|down\s+to|at|around)\s+the\s+(?:river|beach|park|oval|jetty|foreshore|trail|paddock|bush|creek|lake|ocean|shore)|"
    r"surf\w*|swim\w*|paddl\w+|kayak\w*|hik\w+|fishing|garden\w*|"
    r"train(?:s|ing)?\s+(?:outside|outdoors)|plays?\s+(?:football|soccer|cricket|golf|tennis)|dog[- ]?walk\w*|"
    r"school\s+run|paper\s+round|farm\w*|site|outdoors?|outside)\b", re.I)
_OUTDOOR_TOPICS = frozenset({"clothing"})


def fact_hits(topic: str, text: str) -> set[str]:
    """The distinct constraint cues of ``text`` for ``topic``: the topic's word list PLUS, for the weather / what-to-wear lens, the
    shape of a routine that takes the owner outdoors at a fixed time (a time cue AND a going-out verb) - so relevance follows what
    the fact SAYS, not whether it happens to name "walk"."""
    hits = {m.group(0).lower() for m in _TOPICS[topic][1].finditer(text or "")}
    if topic in _OUTDOOR_TOPICS and _WHEN_RE.search(text or "") and _GOING_OUT_RE.search(text or ""):
        hits.add("<outdoor-routine>")
    return hits


def is_advice_request(message: str) -> bool:
    """Is this a request for generic advice or a recommendation (and not a question about the owner's stored facts)?"""
    m = message or ""
    return bool(_ADVICE_RE.search(m)) and not _RECALL_SHAPE_RE.search(m)


def advice_topics(message: str) -> tuple[str, ...]:
    """The topics of a generic-advice request, in a fixed order; () when it is not one. Pure."""
    if not is_advice_request(message):
        return ()
    return tuple(t for t, (ask, _fact) in _TOPICS.items() if ask.search(message))


# ── which stored facts qualify? (pure) ───────────────────────────────────────────────────────

#: the owner no longer does / is this
_DROPPED_RE = re.compile(
    r"\b(?:no\s+longer|used\s+to|stopped|quit|gave\s+up|given\s+up|not\s+any\s?more|don['’]?t\s+\w+\s+any\s?more|"
    r"left\s+(?:the|my)|retired|ex-|former(?:ly)?)\b", re.IGNORECASE)
#: a fact about somebody ELSE: a relative / friend led sentence ("User's mum works nights", "my sister ...")
_OTHER_ADULT = (r"(?:mum|mom|mother|dad|father|wife|husband|partner|girlfriend|boyfriend|sister|brother|friend|boss|"
                r"colleague|coworker|co-worker|neighbou?r|grandma|grandmother|grandpa|grandfather|aunt|uncle|cousin|"
                r"flatmate|roommate|landlord|teacher|doctor|dentist|mate)")
_ABOUT_OTHER_RE = re.compile(
    rf"^\s*(?:the\s+)?(?:user['’]s|my|their|our)\s+(?:[a-z]+\s+){{0,2}}?{_OTHER_ADULT}\b", re.IGNORECASE)
_NAME_LED_RE = re.compile(
    r"^\s*(?!User\b|I\b|I['’]|My\b|We\b|Our\b|Every\b|Each\b|The\b)[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?\s+"
    r"(?:is|are|was|works?|lives?|has|had|likes?|loves?|hates?|goes|gets|walks?|takes?|sleeps?)\b")


def fact_qualifies(text: str) -> bool:
    """A row's text that may be shown as a fact about the OWNER (not dropped, not about another adult)."""
    t = (text or "").strip()
    if not t or _DROPPED_RE.search(t) or _ABOUT_OTHER_RE.match(t) or _NAME_LED_RE.match(t):
        return False
    return True


@dataclass(frozen=True)
class HopFact:
    id: str
    text: str
    topic: str
    score: int


def _one_line(text: str) -> str:
    """A fact as one line with no paren that could close or reopen the suffix early."""
    t = re.sub(r"\s+", " ", text or "").strip().rstrip(".;")
    return t.replace("(", "[").replace(")", "]")


@dataclass(frozen=True)
class Hop:
    """The facts a generic-advice request should be shaped by, and the one rule that says so."""

    topics: tuple[str, ...]
    facts: tuple[HopFact, ...]

    def __bool__(self) -> bool:
        return bool(self.facts)

    def section(self) -> str:
        """The packet section: heading, <= 2 cited bullets, ONE rule line."""
        if not self.facts:
            return ""
        lines = [HEADING]
        for f in self.facts:
            lines.append(f"- {f.text} [mem:{str(f.id)[:8]}]")
        lines.append(RULE)
        return "\n".join(lines)

    def suffix_line(self) -> str:
        """The ``suffix`` placement: ONE parenthetical line - no heading, no rule line, no ids - that reads like the
        user adding what they know about themselves to their own question. '' when there are no facts."""
        if not self.facts:
            return ""
        return SUFFIX_OPEN + "; ".join(_one_line(f.text) for f in self.facts) + ")"


def select(message: str, rows: list) -> Hop:
    """Pure: the <= ``MAX_FACTS`` durable owner facts among ``rows`` (``MemoryRef``-likes: ``.id`` ``.text``) whose
    words are a known constraint for the request's topics. Scored by the number of distinct constraint matches
    (more specific first), then newest. ``rows`` must already be the owner's durable rows."""
    topics = advice_topics(message)
    if not topics:
        return Hop((), ())
    scored: list[tuple[int, float, HopFact]] = []
    for r in rows:
        text = (getattr(r, "text", "") or "").strip()
        if not fact_qualifies(text):
            continue
        best: Optional[tuple[int, str]] = None
        for t in topics:
            hits = fact_hits(t, text)
            if hits and (best is None or len(hits) > best[0]):
                best = (len(hits), t)
        if best is None:
            continue
        try:
            ts = float((getattr(r, "metadata", None) or {}).get("added_ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        shown = text if len(text) <= FACT_CHARS else text[: FACT_CHARS - 1].rstrip() + "…"
        scored.append((best[0], ts, HopFact(str(r.id), shown, best[1], best[0])))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    seen: set[str] = set()
    facts: list[HopFact] = []
    for _s, _ts, f in scored:
        key = re.sub(r"\W+", " ", f.text.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        facts.append(f)
        if len(facts) >= MAX_FACTS:
            break
    return Hop(topics, tuple(facts))


# ── the read ─────────────────────────────────────────────────────────────────────────────────

async def _restrained(user_id: str, message: str, hop: Hop) -> Hop:
    """The hop's facts minus the ones restraint would withhold (``ZOE_RESTRAINT=enforce``): a sensitive fact is not put in front of the
    brain for a voice the speaker gate did not confirm, and a muted topic stays muted. The advice request itself is the pull (the
    owner's own diet / allergy is the point), so only those two walls apply. Never raises."""
    try:
        import restraint

        if restraint.mode() == "off":
            return hop
        keep = await restraint.filter_extra(user_id, message, [f.text for f in hop.facts], pull=True)
        return Hop(hop.topics, tuple(f for f, k in zip(hop.facts, keep) if k))
    except Exception as exc:  # noqa: BLE001
        logger.debug("personalisation_hop: restraint skipped (%s)", type(exc).__name__)
        return hop


async def build(user_id: str, message: str, *, svc: Any = None, session_id: Optional[str] = None) -> Hop:
    """The hop for this turn (``session_id``: the conversation it is for - the relevance gate counts turns per (user, session); None = this task's own): flag on, a real owner, an advice request, one bounded read of the owner's durable rows.
    An empty ``Hop`` otherwise. NEVER raises; a slow or failing store is simply no hop."""
    try:
        if not enabled() or not (user_id or "").strip() or not is_advice_request(message):
            return Hop((), ())
        topics = advice_topics(message)
        if not topics:
            return Hop((), ())
        from memory_service import get_memory_service, is_guest_memory_user

        if is_guest_memory_user(user_id):
            return Hop((), ())
        svc = svc or get_memory_service()
        rows = await asyncio.wait_for(svc.load_durable_for_hop(user_id), timeout=TIMEOUT_S)
        hop = select(message, rows)
        # the relevance gate (ZOE_RECALL_GATE, recall_gate.py): this word-list match is ONE signal of several; shadow returns ``hop``
        # unchanged (and logs what the gate would add or drop), enforce returns the gate's selection. Fail-open to ``hop``.
        import recall_gate

        hop = await recall_gate.hop_surface(user_id, message, rows, hop, session_id)
        if hop.facts:
            hop = await _restrained(user_id, message, hop)
        logger.info("PERSONALISATION_HOP user=%s topics=%s rows=%d facts=%d", user_id, ",".join(topics), len(rows),
                    len(hop.facts))
        return hop
    except Exception as exc:  # noqa: BLE001 - asyncio.TimeoutError included; the hop must never break a turn
        logger.debug("personalisation_hop skipped: %s", type(exc).__name__)
        return Hop((), ())
